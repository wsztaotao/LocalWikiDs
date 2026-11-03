"""Agent main loop — stop conditions, budgets, tool turns (design §11.3).

Policy / sandbox / compact / session modules arrive in later LW tasks.
This loop accepts injectable hooks so LW-34+ can plug in without rewriting.
Security defaults stay conservative: R3+ requires approval under workspace-auto.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from localwiki.agent.protocol import (
    ModelResponse,
    ProtocolAdapter,
    ProtocolKind,
    ToolCall,
    get_adapter,
)
from localwiki.experience.pack import TaskSpec
from localwiki.tools.registry import ToolRegistry, ToolSpec
from localwiki.tools.router import ToolRouter

DEFAULT_MAX_STEPS = 60
DEFAULT_MAX_TOOL_OUTPUT_CHARS = 8000
DEFAULT_CONTEXT_TOKEN_BUDGET = 32000
COMPACT_RATIO = 0.7

# Conservative approval matrix preview (§11.4 workspace-auto default).
# Full matrix + modes land in LW-34 policy.py — do not widen here.
_AUTO_RISKS_WORKSPACE_AUTO = frozenset({"R0", "R1", "R2"})


class ModelClient(Protocol):
    """Minimal generate interface used by the loop."""

    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any,
        extras: dict[str, Any],
    ) -> Any: ...


class ApprovalGate(Protocol):
    def classify(self, call: ToolCall, spec: ToolSpec | None) -> str: ...

    def needs_approval(self, risk: str, mode: str) -> bool: ...


class HookSink(Protocol):
    def on_tool_result(self, call: ToolCall, result: dict[str, Any]) -> None: ...

    def on_task_end(self, session: dict[str, Any]) -> None: ...

    def on_approval_request(self, request: dict[str, Any]) -> dict[str, Any] | None: ...


Approver = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class LoopConfig:
    max_steps: int = DEFAULT_MAX_STEPS
    max_tool_output_chars: int = DEFAULT_MAX_TOOL_OUTPUT_CHARS
    context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET
    cost_budget: float | None = None
    approval_mode: str = "workspace-auto"
    wrap_untrusted: bool = True


@dataclass
class PlanStep:
    id: str
    title: str
    status: str = "pending"  # pending | in_progress | completed

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "status": self.status}


@dataclass
class ApprovalRequest:
    request_id: str
    tool: str
    arguments: dict[str, Any]
    risk: str
    reason: str
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "risk": self.risk,
            "reason": self.reason,
            "mode": self.mode,
        }


@dataclass
class AgentResult:
    ok: bool
    status: str  # completed | stopped | approval_required | cancelled | error
    content: str = ""
    steps: int = 0
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    plan: list[PlanStep] = field(default_factory=list)
    check_results: list[dict[str, Any]] = field(default_factory=list)
    approval_pending: ApprovalRequest | None = None
    protocol: str = ""
    error: str | None = None
    session: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "content": self.content,
            "steps": self.steps,
            "tool_trace": list(self.tool_trace),
            "plan": [p.to_dict() for p in self.plan],
            "check_results": list(self.check_results),
            "approval_pending": (
                self.approval_pending.to_dict() if self.approval_pending else None
            ),
            "protocol": self.protocol,
            "error": self.error,
            "session": dict(self.session),
        }


class ConservativeApprovalGate:
    """§11.4 subset until policy.py (LW-34). Never auto-approves R3/R4."""

    def classify(self, call: ToolCall, spec: ToolSpec | None) -> str:
        if spec is not None:
            return spec.risk or "R0"
        return "R3"  # unknown tool → high risk

    def needs_approval(self, risk: str, mode: str) -> bool:
        mode = mode or "workspace-auto"
        if mode == "read-only":
            return risk not in ("R0", "R1")
        if mode == "full-access":
            # Still require approval for R4; external write/delete → R3 kept for LW-34
            return risk == "R4"
        # workspace-auto (default)
        return risk not in _AUTO_RISKS_WORKSPACE_AUTO


class NullHooks:
    def on_tool_result(self, call: ToolCall, result: dict[str, Any]) -> None:
        return None

    def on_task_end(self, session: dict[str, Any]) -> None:
        return None

    def on_approval_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        return None


def _estimate_tokens(messages: Sequence[dict[str, Any]]) -> int:
    # Rough heuristic sufficient for compact trigger; real tokenizer later
    total = 0
    for m in messages:
        total += len(str(m.get("content") or "")) // 2 + 8
        for tc in m.get("tool_calls") or []:
            total += len(str(tc)) // 2
    return total


def _truncate_output(output: dict[str, Any], limit: int) -> tuple[dict[str, Any], bool]:
    text = str(output)
    if len(text) <= limit:
        return output, False
    return {
        "ok": output.get("ok", True) if isinstance(output, dict) else True,
        "truncated": True,
        "preview": text[:limit],
        "full_saved": False,
    }, True


def _plan_all_completed(plan: Sequence[PlanStep]) -> bool:
    if not plan:
        return True
    return all(s.status == "completed" for s in plan)


def _run_must_pass_checks(
    checks: Sequence[str],
    content: str,
    tool_trace: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    blob = content.lower()
    tool_blob = " ".join(str(t) for t in tool_trace).lower()
    results: list[dict[str, Any]] = []
    for check in checks:
        c = (check or "").strip()
        if not c:
            continue
        # Simple substring heuristic for scaffold; rubrics arrive via experience later
        passed = c.lower() in blob or c.lower() in tool_blob
        results.append({"check": c, "passed": passed})
    return results


class AgentLoop:
    """Model → tools → observations until final / budget / cancel."""

    def __init__(
        self,
        registry: ToolRegistry,
        model: ModelClient,
        *,
        protocol: ProtocolKind | str = "openai",
        router: ToolRouter | None = None,
        policy: ApprovalGate | None = None,
        hooks: HookSink | None = None,
        approver: Approver | None = None,
        config: LoopConfig | None = None,
        compact_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
        lessons: Sequence[str] | None = None,
    ) -> None:
        self.registry = registry
        self.model = model
        self.adapter = get_adapter(protocol)
        self.router = router or ToolRouter(registry)
        self.policy: ApprovalGate = policy or ConservativeApprovalGate()
        self.hooks: HookSink = hooks or NullHooks()
        self.approver = approver
        self.config = config or LoopConfig()
        self.compact_fn = compact_fn
        self.lessons = list(lessons or [])
        self._cancelled = False

    @property
    def protocol(self) -> str:
        return self.adapter.kind

    def cancel(self) -> None:
        self._cancelled = True

    def run(
        self,
        task: TaskSpec,
        *,
        messages: list[dict[str, Any]] | None = None,
        plan: list[PlanStep] | None = None,
        preferred_tools: Sequence[str] | None = None,
        style_block: str = "",
        experience_block: str = "",
    ) -> AgentResult:
        cfg = self.config
        mode = task.approval_mode or cfg.approval_mode
        session_id = f"agt_{uuid.uuid4().hex[:12]}"
        history: list[dict[str, Any]] = list(messages or [])
        if not history:
            history = self._seed_messages(
                task,
                style_block=style_block,
                experience_block=experience_block,
            )

        plan_steps = list(plan or [])
        tool_trace: list[dict[str, Any]] = []
        last_content = ""
        cost = 0.0
        t0 = time.time()

        for step in range(1, cfg.max_steps + 1):
            if self._cancelled:
                return AgentResult(
                    ok=False,
                    status="cancelled",
                    content=last_content,
                    steps=step - 1,
                    tool_trace=tool_trace,
                    plan=plan_steps,
                    protocol=self.protocol,
                    session=self._session_dict(session_id, task, step - 1, t0),
                )

            if cfg.cost_budget is not None and cost >= cfg.cost_budget:
                return AgentResult(
                    ok=False,
                    status="stopped",
                    content=last_content,
                    steps=step - 1,
                    tool_trace=tool_trace,
                    plan=plan_steps,
                    protocol=self.protocol,
                    error="cost_budget_exceeded",
                    session=self._session_dict(session_id, task, step - 1, t0),
                )

            # Compact trigger (hook for LW-36)
            tokens = _estimate_tokens(history)
            if tokens > int(cfg.context_token_budget * COMPACT_RATIO):
                if self.compact_fn is not None:
                    history = self.compact_fn(history)
                else:
                    # Soft trim: keep system + last 6 messages
                    history = _soft_trim(history)

            selected = self.router.select(
                tool_scope=task.tool_scope or None,
                preferred_tools=preferred_tools,
            )
            specs = [
                s
                for s in self.registry.list_tools(enabled_only=True)
                if s.name in selected.selected
            ]
            extras = self.adapter.request_extras(specs)
            tools_payload = extras.get("tools")

            # Inject JSON fallback system hint if needed
            turn_messages = list(history)
            if extras.get("system_extra"):
                turn_messages = [
                    {"role": "system", "content": extras["system_extra"]},
                    *turn_messages,
                ]

            raw = self.model.generate(
                turn_messages,
                tools=tools_payload,
                extras=extras,
            )
            cost += 0.001  # placeholder metering
            resp = self.adapter.parse_response(raw)
            last_content = resp.content or last_content

            if resp.final and not resp.has_tools:
                if _plan_all_completed(plan_steps):
                    checks = _run_must_pass_checks(
                        task.must_pass_checks, last_content, tool_trace
                    )
                    failed = [c for c in checks if not c["passed"]]
                    delivery = last_content
                    if failed:
                        delivery = (
                            f"{last_content}\n\n[self-check failed: "
                            + ", ".join(c["check"] for c in failed)
                            + "]"
                        )
                    result = AgentResult(
                        ok=not failed,
                        status="completed",
                        content=delivery,
                        steps=step,
                        tool_trace=tool_trace,
                        plan=plan_steps,
                        check_results=checks,
                        protocol=self.protocol,
                        session=self._session_dict(session_id, task, step, t0),
                    )
                    self.hooks.on_task_end(result.session)
                    return result
                # Plan incomplete — nudge and continue
                history.append(self.adapter.format_assistant_message(resp))
                history.append(
                    {
                        "role": "user",
                        "content": (
                            "Plan still has incomplete steps; "
                            "continue or mark them completed."
                        ),
                    }
                )
                continue

            if not resp.tool_calls:
                # Ambiguous non-final empty — treat as completed soft stop
                history.append(self.adapter.format_assistant_message(resp))
                if resp.parse_error:
                    continue
                checks = _run_must_pass_checks(
                    task.must_pass_checks, last_content, tool_trace
                )
                result = AgentResult(
                    ok=True,
                    status="completed",
                    content=last_content,
                    steps=step,
                    tool_trace=tool_trace,
                    plan=plan_steps,
                    check_results=checks,
                    protocol=self.protocol,
                    session=self._session_dict(session_id, task, step, t0),
                )
                self.hooks.on_task_end(result.session)
                return result

            history.append(self.adapter.format_assistant_message(resp))

            for call in resp.tool_calls:
                if not call.name:
                    continue
                spec = self.registry.get(call.name)
                # Prefer PolicyEngine.decide when available (LW-34)
                decide_fn = getattr(self.policy, "decide", None)
                if callable(decide_fn):
                    pol = decide_fn(call, spec, mode)
                    risk = pol.risk
                    if pol.rejected:
                        obs = {
                            "ok": False,
                            "error": "rejected_by_policy",
                            "mode": mode,
                            "risk": risk,
                            "reason": pol.reason,
                        }
                        tool_trace.append(
                            {
                                "step": step,
                                "call": call.to_dict(),
                                "risk": risk,
                                "result": obs,
                                "approved": False,
                            }
                        )
                        history.append(
                            self.adapter.format_tool_result_message(
                                call, obs, wrap_untrusted=cfg.wrap_untrusted
                            )
                        )
                        continue
                    needs_apr = pol.needs_approval
                    apr_reason = pol.reason
                else:
                    risk = self.policy.classify(call, spec)
                    needs_apr = self.policy.needs_approval(risk, mode)
                    apr_reason = f"risk {risk} requires approval under mode={mode}"
                    if mode == "read-only" and risk in ("R2", "R3", "R4"):
                        obs = {
                            "ok": False,
                            "error": "rejected_by_mode",
                            "mode": mode,
                            "risk": risk,
                        }
                        tool_trace.append(
                            {
                                "step": step,
                                "call": call.to_dict(),
                                "risk": risk,
                                "result": obs,
                                "approved": False,
                            }
                        )
                        history.append(
                            self.adapter.format_tool_result_message(
                                call, obs, wrap_untrusted=cfg.wrap_untrusted
                            )
                        )
                        continue

                if needs_apr:
                    req = ApprovalRequest(
                        request_id=_new_id("apr"),
                        tool=call.name,
                        arguments=dict(call.arguments),
                        risk=risk,
                        reason=apr_reason,
                        mode=mode,
                    )
                    decision = None
                    if self.approver is not None:
                        decision = self.approver(req.to_dict())
                    else:
                        decision = self.hooks.on_approval_request(req.to_dict())

                    if not decision or decision.get("decision") not in (
                        "approved",
                        "approve",
                        "modified",
                    ):
                        return AgentResult(
                            ok=False,
                            status="approval_required",
                            content=last_content,
                            steps=step,
                            tool_trace=tool_trace,
                            plan=plan_steps,
                            approval_pending=req,
                            protocol=self.protocol,
                            error="E_APPROVAL_REQUIRED",
                            session=self._session_dict(session_id, task, step, t0),
                        )
                    if decision.get("decision") == "modified" and isinstance(
                        decision.get("arguments"), dict
                    ):
                        call.arguments = dict(decision["arguments"])

                exec_result = self.registry.execute(call.name, call.arguments)
                output = dict(exec_result.output)
                output, truncated = _truncate_output(
                    output, cfg.max_tool_output_chars
                )
                if truncated:
                    output["truncated"] = True

                # Sync plan tools if present
                _maybe_update_plan(plan_steps, call, output)

                obs = {
                    "ok": exec_result.ok,
                    "tool": exec_result.name,
                    "risk": exec_result.risk,
                    "output": output,
                }
                self.hooks.on_tool_result(call, obs)
                tool_trace.append(
                    {
                        "step": step,
                        "call": call.to_dict(),
                        "risk": risk,
                        "result": obs,
                        "approved": True,
                    }
                )
                history.append(
                    self.adapter.format_tool_result_message(
                        call, obs, wrap_untrusted=cfg.wrap_untrusted
                    )
                )

            # checkpoint placeholder (LW-36 session.py)
            _ = {"session_id": session_id, "step": step, "history_len": len(history)}

        return AgentResult(
            ok=False,
            status="stopped",
            content=last_content,
            steps=cfg.max_steps,
            tool_trace=tool_trace,
            plan=plan_steps,
            protocol=self.protocol,
            error="max_steps_exceeded",
            session=self._session_dict(session_id, task, cfg.max_steps, t0),
        )

    def _seed_messages(
        self,
        task: TaskSpec,
        *,
        style_block: str,
        experience_block: str,
    ) -> list[dict[str, Any]]:
        parts = [
            "You are the LocalWiki work agent. Follow system safety rules.",
            f"Goal: {task.goal}",
            f"Task type: {task.task_type}",
            f"Approval mode: {task.approval_mode}",
        ]
        if task.constraints:
            parts.append("Constraints: " + "; ".join(task.constraints))
        if task.must_pass_checks:
            parts.append("Must pass: " + "; ".join(task.must_pass_checks))
        if self.lessons:
            parts.append("Lessons:\n- " + "\n- ".join(self.lessons))
        if experience_block:
            parts.append(experience_block)
        if style_block:
            parts.append(style_block)
        return [
            {"role": "system", "content": "\n".join(parts)},
            {"role": "user", "content": task.goal},
        ]

    def _session_dict(
        self,
        session_id: str,
        task: TaskSpec,
        steps: int,
        t0: float,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "task_uid": task.task_uid,
            "protocol": self.protocol,
            "steps": steps,
            "elapsed_s": round(time.time() - t0, 3),
            "approval_mode": task.approval_mode,
        }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _soft_trim(messages: list[dict[str, Any]], keep_recent: int = 6) -> list[dict[str, Any]]:
    if len(messages) <= keep_recent + 1:
        return messages
    head = messages[:1]
    tail = messages[-keep_recent:]
    summary = {
        "role": "system",
        "content": (
            "[handoff summary placeholder — compact.py LW-36] "
            f"omitted={len(messages) - 1 - keep_recent} earlier messages"
        ),
    }
    return head + [summary] + tail


def _maybe_update_plan(
    plan: list[PlanStep],
    call: ToolCall,
    output: dict[str, Any],
) -> None:
    if call.name == "plan_set":
        steps = call.arguments.get("steps") or output.get("steps") or []
        plan.clear()
        for i, s in enumerate(steps):
            if isinstance(s, dict):
                plan.append(
                    PlanStep(
                        id=str(s.get("id") or f"s{i+1}"),
                        title=str(s.get("title") or s.get("name") or f"step {i+1}"),
                        status=str(s.get("status") or "pending"),
                    )
                )
            else:
                plan.append(PlanStep(id=f"s{i+1}", title=str(s), status="pending"))
    elif call.name == "plan_update":
        sid = str(call.arguments.get("id") or call.arguments.get("step_id") or "")
        status = str(call.arguments.get("status") or "")
        for p in plan:
            if p.id == sid or p.title == sid:
                if status:
                    p.status = status
                break
