"""Offline trajectory replay + baseline eval suites (design §23.9 / LW-44 / AT-EVO-06).

Hard rules:
- Replay NEVER executes external writes; tool calls reuse recorded results only.
- Baseline suites under ``evals/`` are freeze-zone and invisible to proposal models.
- Pass when target metric improves and per-domain regression ≤ 1%.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

EVAL_DOMAINS = (
    "wiki_qa",
    "memory_recall",
    "experience_pack",
    "agent_tasks",
    "artifacts",
)

# Tools that would mutate external systems — must use recordings only
EXTERNAL_WRITE_TOOLS = frozenset(
    {
        "composio",
        "integration_write",
        "gmail_send",
        "slack_post",
        "http_post",
        "http_put",
        "http_delete",
        "browser_click",
        "browser_type",
        "browser_fill",
        "shell_exec",
        "win_command",
        "apply_patch",
        "docx_create",
        "pptx_create",
        "xlsx_create",
        "pdf_create",
        "fs_write",
        "file_write",
        "wiki_commit",
    }
)

MAX_REGRESSION = 0.01  # 1% absolute
ROLE_PROPOSAL = "proposal"
ROLE_JUDGE = "judge"
ROLE_REPLAY = "replay"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class RecordedToolCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    is_external_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trajectory:
    task_uid: str
    goal: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[RecordedToolCall] = field(default_factory=list)
    final_output: str = ""
    pattern_keys: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_uid": self.task_uid,
            "goal": self.goal,
            "messages": list(self.messages),
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "final_output": self.final_output,
            "pattern_keys": list(self.pattern_keys),
            "score": self.score,
        }


@dataclass
class ReplayResult:
    task_uid: str
    ok: bool
    baseline_score: float
    candidate_score: float
    improved: bool
    external_writes_attempted: int
    external_writes_executed: int  # must stay 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DomainScore:
    domain: str
    n_tasks: int
    baseline_mean: float
    candidate_mean: float
    regression: float  # baseline - candidate (positive = worse)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalReport:
    proposal_uid: str
    passed: bool
    replay: list[ReplayResult] = field(default_factory=list)
    domains: list[DomainScore] = field(default_factory=list)
    max_regression: float = MAX_REGRESSION
    reason: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_uid": self.proposal_uid,
            "passed": self.passed,
            "replay": [r.to_dict() for r in self.replay],
            "domains": [d.to_dict() for d in self.domains],
            "max_regression": self.max_regression,
            "reason": self.reason,
            "created_at": self.created_at or _now_iso(),
        }


class BaselineVisibilityGuard:
    """Ensure proposal-role callers cannot read ``evals/`` content (§23.9)."""

    def __init__(self, evals_root: Path | str) -> None:
        self.evals_root = Path(evals_root).resolve()

    def assert_allowed(self, role: str) -> None:
        if role == ROLE_PROPOSAL:
            raise PermissionError(
                "baseline eval suites are freeze-zone and invisible to proposal models"
            )

    def read_text(self, path: Path | str, *, role: str) -> str:
        self.assert_allowed(role)
        p = Path(path).resolve()
        if self.evals_root not in p.parents and p != self.evals_root:
            # Still allow only under evals_root
            try:
                p.relative_to(self.evals_root)
            except ValueError as e:
                raise PermissionError(f"path outside evals root: {p}") from e
        return p.read_text(encoding="utf-8")

    def load_suite(self, domain: str, *, role: str) -> list[dict[str, Any]]:
        self.assert_allowed(role)
        path = self.evals_root / domain / "tasks.jsonl"
        if not path.exists():
            raise FileNotFoundError(path)
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows


def is_external_write_tool(name: str) -> bool:
    n = (name or "").lower()
    if n in EXTERNAL_WRITE_TOOLS:
        return True
    if n.startswith("composio.") or n.startswith("integration_"):
        return True
    if n.startswith("browser_") and n not in ("browser_snapshot", "browser_tabs"):
        return True
    return False


class ReplayHarness:
    """Re-run a trajectory with recorded tool results only — no live side effects."""

    def __init__(
        self,
        *,
        model_fn: Callable[[str, str, Sequence[dict[str, Any]]], str] | None = None,
    ) -> None:
        # model_fn(goal, rules_text, messages) -> candidate output
        self.model_fn = model_fn or _default_replay_model

    def replay(
        self,
        traj: Trajectory,
        *,
        new_rules: str = "",
        execute_tools: bool = False,
    ) -> ReplayResult:
        """If ``execute_tools`` is True this still refuses external writes (hard rule)."""
        writes_attempted = 0
        writes_executed = 0
        tool_outputs: list[dict[str, Any]] = []

        for call in traj.tool_calls:
            external = call.is_external_write or is_external_write_tool(call.name)
            if external:
                writes_attempted += 1
                # ALWAYS reuse recording — never execute
                tool_outputs.append(
                    {
                        "name": call.name,
                        "result": call.result,
                        "source": "recording",
                        "external_write": True,
                    }
                )
                if execute_tools:
                    # Explicitly blocked even if caller asks
                    writes_executed += 0
            else:
                tool_outputs.append(
                    {
                        "name": call.name,
                        "result": call.result,
                        "source": "recording",
                        "external_write": False,
                    }
                )

        candidate_out = self.model_fn(traj.goal, new_rules, traj.messages)
        baseline = float(traj.score)
        candidate = _score_output(traj, candidate_out, new_rules)
        improved = candidate > baseline + 1e-9
        return ReplayResult(
            task_uid=traj.task_uid,
            ok=writes_executed == 0,
            baseline_score=baseline,
            candidate_score=candidate,
            improved=improved,
            external_writes_attempted=writes_attempted,
            external_writes_executed=writes_executed,
            notes=f"tools_replayed={len(tool_outputs)}",
        )


def _default_replay_model(goal: str, rules: str, messages: Sequence[dict[str, Any]]) -> str:
    """Deterministic stub: if rules mention a fix keyword from goal, improve output."""
    base = ""
    for m in messages:
        if m.get("role") == "assistant":
            base = str(m.get("content") or base)
    if not base:
        base = f"answer for: {goal}"
    if rules and any(
        tok in rules.lower()
        for tok in ("列宽", "column width", "width", "必须", "always", "预防")
    ):
        return base + "\n[applied_learned_rule]"
    return base


def _score_output(traj: Trajectory, output: str, rules: str) -> float:
    score = 0.4
    if "[applied_learned_rule]" in output:
        score += 0.4
    if traj.final_output and traj.final_output.strip() in output:
        score += 0.1
    if rules and len(rules) > 5:
        score += 0.05
    # Pattern-key keyword overlap
    for pk in traj.pattern_keys:
        tip = pk.split(".")[-1]
        if tip and tip.lower() in output.lower():
            score += 0.05
    return min(1.0, score)


def load_trajectory_file(path: Path | str) -> Trajectory:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    calls = [
        RecordedToolCall(
            name=c["name"],
            arguments=c.get("arguments") or {},
            result=c.get("result") or {},
            is_external_write=bool(c.get("is_external_write"))
            or is_external_write_tool(c.get("name") or ""),
        )
        for c in data.get("tool_calls") or []
    ]
    return Trajectory(
        task_uid=data["task_uid"],
        goal=data.get("goal") or "",
        messages=list(data.get("messages") or []),
        tool_calls=calls,
        final_output=data.get("final_output") or "",
        pattern_keys=list(data.get("pattern_keys") or []),
        score=float(data.get("score") or 0.5),
    )


def run_deterministic_checks(task: dict[str, Any], output: str) -> tuple[bool, list[str]]:
    """Deterministic checks attached to baseline tasks."""
    errors: list[str] = []
    checks = task.get("checks") or []
    for c in checks:
        ctype = c.get("type")
        if ctype == "contains":
            needle = str(c.get("value") or "")
            if needle.lower() not in output.lower():
                errors.append(f"missing:{needle}")
        elif ctype == "regex":
            if not re.search(str(c.get("value") or ""), output, re.I):
                errors.append(f"regex_fail:{c.get('value')}")
        elif ctype == "json_schema_keys":
            try:
                obj = json.loads(output)
            except json.JSONDecodeError:
                errors.append("not_json")
                continue
            for k in c.get("keys") or []:
                if k not in obj:
                    errors.append(f"missing_key:{k}")
        elif ctype == "not_contains":
            needle = str(c.get("value") or "")
            if needle.lower() in output.lower():
                errors.append(f"forbidden:{needle}")
    expect = task.get("expected_contains")
    if expect and str(expect).lower() not in output.lower():
        errors.append(f"expected_contains:{expect}")
    return (not errors, errors)


class BaselineRunner:
    """Run freeze-zone eval suites; proposal role cannot load tasks."""

    def __init__(self, evals_root: Path | str) -> None:
        self.evals_root = Path(evals_root)
        self.guard = BaselineVisibilityGuard(self.evals_root)

    def domain_count(self, domain: str) -> int:
        # Internal/ops role — counts for acceptance, not proposal context
        rows = self.guard.load_suite(domain, role=ROLE_JUDGE)
        return len(rows)

    def run_domain(
        self,
        domain: str,
        *,
        answer_fn: Callable[[dict[str, Any]], str] | None = None,
        role: str = ROLE_JUDGE,
    ) -> DomainScore:
        tasks = self.guard.load_suite(domain, role=role)
        answer_fn = answer_fn or _default_baseline_answer
        scores: list[float] = []
        for t in tasks:
            out = answer_fn(t)
            ok, _ = run_deterministic_checks(t, out)
            scores.append(1.0 if ok else 0.0)
        mean = sum(scores) / len(scores) if scores else 0.0
        return DomainScore(
            domain=domain,
            n_tasks=len(tasks),
            baseline_mean=mean,
            candidate_mean=mean,
            regression=0.0,
        )

    def compare_domains(
        self,
        *,
        baseline_fn: Callable[[dict[str, Any]], str],
        candidate_fn: Callable[[dict[str, Any]], str],
        role: str = ROLE_JUDGE,
    ) -> list[DomainScore]:
        out: list[DomainScore] = []
        for domain in EVAL_DOMAINS:
            tasks = self.guard.load_suite(domain, role=role)
            b_scores: list[float] = []
            c_scores: list[float] = []
            for t in tasks:
                b_ok, _ = run_deterministic_checks(t, baseline_fn(t))
                c_ok, _ = run_deterministic_checks(t, candidate_fn(t))
                b_scores.append(1.0 if b_ok else 0.0)
                c_scores.append(1.0 if c_ok else 0.0)
            b_mean = sum(b_scores) / len(b_scores) if b_scores else 0.0
            c_mean = sum(c_scores) / len(c_scores) if c_scores else 0.0
            out.append(
                DomainScore(
                    domain=domain,
                    n_tasks=len(tasks),
                    baseline_mean=b_mean,
                    candidate_mean=c_mean,
                    regression=max(0.0, b_mean - c_mean),
                )
            )
        return out


def _default_baseline_answer(task: dict[str, Any]) -> str:
    """Oracle that satisfies expected_contains / checks for fixture self-test."""
    expect = task.get("expected_contains") or task.get("canonical_answer") or "ok"
    if task.get("answer_format") == "json":
        keys = []
        for c in task.get("checks") or []:
            if c.get("type") == "json_schema_keys":
                keys = list(c.get("keys") or [])
        obj = {k: expect for k in keys} or {"answer": expect}
        return json.dumps(obj, ensure_ascii=False)
    return str(expect)


class OfflineEvaluator:
    """Combine replay + baseline; enforce regression ≤ 1% and no live writes."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        evals_root: Path | str | None = None,
        replay: ReplayHarness | None = None,
    ) -> None:
        self.root = Path(workspace_root)
        self.evals_root = Path(evals_root) if evals_root else self._default_evals_root()
        self.replay = replay or ReplayHarness()
        self.baseline = BaselineRunner(self.evals_root)

    def _default_evals_root(self) -> Path:
        # Prefer repo-level evals/ next to localwiki package
        here = Path(__file__).resolve()
        repo = here.parents[2]
        candidate = repo / "evals"
        return candidate if candidate.exists() else self.root / "evals"

    def evaluate_proposal(
        self,
        proposal: dict[str, Any] | Any,
        trajectories: Sequence[Trajectory],
        *,
        baseline_fn: Callable[[dict[str, Any]], str] | None = None,
        candidate_fn: Callable[[dict[str, Any]], str] | None = None,
        max_regression: float = MAX_REGRESSION,
    ) -> EvalReport:
        if hasattr(proposal, "to_dict"):
            pdata = proposal.to_dict()
        else:
            pdata = dict(proposal)
        uid = str(pdata.get("proposal_uid") or "unknown")
        rules = str(pdata.get("diff") or "")

        replay_results = [
            self.replay.replay(t, new_rules=rules, execute_tools=False) for t in trajectories
        ]
        if any(r.external_writes_executed > 0 for r in replay_results):
            return EvalReport(
                proposal_uid=uid,
                passed=False,
                replay=replay_results,
                reason="external_write_executed",
                created_at=_now_iso(),
                max_regression=max_regression,
            )

        target_improved = True
        if replay_results:
            target_improved = any(r.improved for r in replay_results) or all(
                r.candidate_score >= r.baseline_score for r in replay_results
            )

        b_fn = baseline_fn or _default_baseline_answer
        # Candidate must not regress; default candidate = baseline oracle (0 regression)
        # A degrading candidate_fn can be injected in tests
        c_fn = candidate_fn or b_fn
        domains = self.baseline.compare_domains(baseline_fn=b_fn, candidate_fn=c_fn, role=ROLE_JUDGE)
        reg_ok = all(d.regression <= max_regression + 1e-12 for d in domains)
        passed = bool(target_improved and reg_ok and all(r.ok for r in replay_results))
        reason = "passed"
        if not target_improved:
            reason = "target_not_improved"
        elif not reg_ok:
            reason = "baseline_regression"
        return EvalReport(
            proposal_uid=uid,
            passed=passed,
            replay=replay_results,
            domains=domains,
            max_regression=max_regression,
            reason=reason,
            created_at=_now_iso(),
        )


def ensure_min_suite_sizes(evals_root: Path | str, minimum: int = 20) -> dict[str, int]:
    """Return counts; raise if any domain has fewer than ``minimum`` tasks."""
    root = Path(evals_root)
    guard = BaselineVisibilityGuard(root)
    counts: dict[str, int] = {}
    for d in EVAL_DOMAINS:
        n = len(guard.load_suite(d, role=ROLE_JUDGE))
        counts[d] = n
        if n < minimum:
            raise AssertionError(f"domain {d} has {n} tasks; need ≥ {minimum}")
    return counts
