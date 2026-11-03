"""In-task self-healing (design §23.5 / LW-40).

Diagnose → repair → verify, at most 2 retries. HEAL is written only when
verification evidence exists. Healing must not escalate risk (e.g. R2→R3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from localwiki.agent.policy import PolicyEngine
from localwiki.agent.protocol import ToolCall
from localwiki.evolution.learnings import LearningRecord, LearningStore
from localwiki.tools.registry import ToolRegistry, ToolSpec

MAX_HEAL_RETRIES = 2

Executor = Callable[[str, dict[str, Any]], dict[str, Any]]
RepairFn = Callable[[ToolCall, dict[str, Any], int], ToolCall | None]


@dataclass
class HealAttempt:
    attempt: int
    call: dict[str, Any]
    result: dict[str, Any]
    verified: bool
    risk: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "call": dict(self.call),
            "result": dict(self.result),
            "verified": self.verified,
            "risk": self.risk,
        }


@dataclass
class HealResult:
    ok: bool
    healed: bool
    attempts: list[HealAttempt] = field(default_factory=list)
    heal_record: LearningRecord | None = None
    blocked_reason: str | None = None
    final_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "healed": self.healed,
            "attempts": [a.to_dict() for a in self.attempts],
            "heal_record": self.heal_record.to_dict() if self.heal_record else None,
            "blocked_reason": self.blocked_reason,
            "final_result": self.final_result,
        }


def is_failure(result: dict[str, Any]) -> bool:
    if result.get("ok") is False:
        return True
    out = result.get("output") if isinstance(result.get("output"), dict) else result
    if isinstance(out, dict):
        if out.get("ok") is False or out.get("error") or out.get("timed_out"):
            return True
        if out.get("exit_code") not in (None, 0):
            return True
    return False


def has_verification_evidence(result: dict[str, Any]) -> bool:
    """Accept only concrete success signals — not model self-praise."""
    if is_failure(result):
        return False
    out = result.get("output") if isinstance(result.get("output"), dict) else result
    if not isinstance(out, dict):
        return bool(result.get("ok"))
    if out.get("exit_code") == 0:
        return True
    if out.get("ok") is True and not out.get("error"):
        return True
    if out.get("verified") is True:
        return True
    if out.get("file_exists") is True or out.get("bytes", 0) > 0:
        return True
    if result.get("ok") is True and "error" not in out:
        return True
    return False


def default_repair(
    call: ToolCall,
    result: dict[str, Any],
    attempt: int,
) -> ToolCall | None:
    """Deterministic micro-repairs (no model). Returns None if no idea."""
    name = call.name
    args = dict(call.arguments or {})
    err = str(result)

    # Whitespace / quoting tweak for shell
    if name in ("win_command", "shell_exec") and "command" in args:
        cmd = str(args["command"])
        if attempt == 1 and cmd.endswith(" "):
            args["command"] = cmd.strip()
            return ToolCall(id=f"{call.id}_r{attempt}", name=name, arguments=args)
        if attempt == 1 and "not recognized" in err.lower():
            # try where.exe prefix — still same risk class
            args["command"] = f"where {cmd.split()[0]}" if cmd.split() else cmd
            return ToolCall(id=f"{call.id}_r{attempt}", name=name, arguments=args)
        if attempt >= 1:
            args["command"] = cmd.strip()
            return ToolCall(id=f"{call.id}_r{attempt}", name=name, arguments=args)

    # context_mismatch → signal re-read (same tool, mark retry)
    if "context_mismatch" in err and name in ("apply_patch", "workspace_replace"):
        args["_heal_retry"] = attempt
        return ToolCall(id=f"{call.id}_r{attempt}", name=name, arguments=args)

    # Generic: retry identical args once (still counts toward budget)
    if attempt <= MAX_HEAL_RETRIES:
        args["_heal_retry"] = attempt
        return ToolCall(id=f"{call.id}_r{attempt}", name=name, arguments=args)
    return None


class SelfHealer:
    """Run diagnose→repair→verify without escalating risk."""

    def __init__(
        self,
        registry: ToolRegistry,
        store: LearningStore,
        *,
        policy: PolicyEngine | None = None,
        repair_fn: RepairFn | None = None,
        max_retries: int = MAX_HEAL_RETRIES,
    ) -> None:
        self.registry = registry
        self.store = store
        self.policy = policy or PolicyEngine()
        self.repair_fn = repair_fn or default_repair
        self.max_retries = max_retries

    def _risk(self, call: ToolCall) -> str:
        spec = self.registry.get(call.name)
        return self.policy.classify(call, spec)

    def _risk_rank(self, risk: str) -> int:
        order = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
        return order.get(risk, 3)

    def heal(
        self,
        call: ToolCall,
        failed_result: dict[str, Any],
        *,
        task_uid: str = "",
        execute: Executor | None = None,
    ) -> HealResult:
        if not is_failure(failed_result):
            return HealResult(
                ok=True,
                healed=False,
                final_result=failed_result,
                blocked_reason="not_a_failure",
            )

        base_risk = self._risk(call)
        attempts: list[HealAttempt] = [
            HealAttempt(
                attempt=0,
                call=call.to_dict(),
                result=failed_result,
                verified=False,
                risk=base_risk,
            )
        ]
        def _exec(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if execute is not None:
                return execute(name, arguments)
            tr = self.registry.execute(name, arguments)
            return {
                "ok": tr.ok,
                "tool": tr.name,
                "risk": tr.risk,
                "output": tr.output,
                "truncated": tr.truncated,
            }

        current = call
        last = failed_result
        for i in range(1, self.max_retries + 1):
            repaired = self.repair_fn(current, last, i)
            if repaired is None:
                break
            new_risk = self._risk(repaired)
            if self._risk_rank(new_risk) > self._risk_rank(base_risk):
                return HealResult(
                    ok=False,
                    healed=False,
                    attempts=attempts,
                    blocked_reason="risk_escalation_forbidden",
                    final_result=last,
                )
            # Also forbid changing to a higher-risk tool name
            orig_spec = self.registry.get(call.name)
            new_spec = self.registry.get(repaired.name)
            if orig_spec and new_spec:
                if self._risk_rank(new_spec.risk) > self._risk_rank(orig_spec.risk):
                    return HealResult(
                        ok=False,
                        healed=False,
                        attempts=attempts,
                        blocked_reason="risk_escalation_forbidden",
                        final_result=last,
                    )

            last = _exec(repaired.name, repaired.arguments)
            verified = has_verification_evidence(last)
            attempts.append(
                HealAttempt(
                    attempt=i,
                    call=repaired.to_dict(),
                    result=last,
                    verified=verified,
                    risk=new_risk,
                )
            )
            current = repaired
            if verified:
                heal_rec = self._write_heal(
                    call,
                    failed_result,
                    repaired,
                    last,
                    attempts=attempts,
                    task_uid=task_uid,
                    base_risk=base_risk,
                )
                return HealResult(
                    ok=True,
                    healed=True,
                    attempts=attempts,
                    heal_record=heal_rec,
                    final_result=last,
                )

        return HealResult(
            ok=False,
            healed=False,
            attempts=attempts,
            blocked_reason="max_retries_exhausted",
            final_result=last,
        )

    def _write_heal(
        self,
        original: ToolCall,
        failure: dict[str, Any],
        repair: ToolCall,
        success: dict[str, Any],
        *,
        attempts: Sequence[HealAttempt],
        task_uid: str,
        base_risk: str,
    ) -> LearningRecord:
        # Only called when verification evidence exists
        assert has_verification_evidence(success)
        rec = LearningRecord(
            kind="HEAL",
            category="self_healing",
            summary=f"Healed failure of {original.name}",
            details=(
                f"fault={failure}\n"
                f"root_cause=deterministic_retry\n"
                f"repair={repair.to_dict()}\n"
                f"verification={success}\n"
                f"attempts={len(attempts)}\n"
                f"risk_unchanged={base_risk}"
            ),
            suggested_action=f"Prefer repaired args pattern for {original.name}",
            area="agent",
            pattern_key=f"heal.{original.name}",
            related=[task_uid] if task_uid else [],
            evidence=["tool_result", "verification"],
            tags=["self-healing", original.name],
            source="self_healing",
        )
        return self.store.append(rec)
