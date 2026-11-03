"""Post-task reflection and correction routing (design §23.5 / §20.7 / LW-41).

Outputs the §23.5 reflection contract. User-related conclusions enqueue
memory/persona/experience candidates (ch. 8–10); system lessons → learnings.
Self-assessment-only items are flagged and must not count toward promotion.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal, Sequence

from localwiki.evolution.learnings import LearningStore, make_lrn

ROOT_CAUSE_CLASSES = (
    "事实错误",
    "约束遗漏",
    "经验不适用",
    "工具陷阱",
    "提示词问题",
    "模型能力不足",
    "执行问题",
    "格式偏好",
    "外部服务故障",
)

# Preference / habit / personal fact → user side
_USER_SIDE_RE = re.compile(
    r"(偏好|习惯|喜欢|不要|别用|报告|格式|语气|风格|我的|我是|以后都|"
    r"prefer|don't put|no code in|always use)",
    re.I,
)
# Tool / process / knowledge / prompt → system side
_SYSTEM_SIDE_RE = re.compile(
    r"(docx|pptx|xlsx|工具|表格|错位|patch|timeout|mcp|composio|tavily|"
    r"skill|路由|提示词|tool|schema|AGENTS)",
    re.I,
)


@dataclass
class FailedStep:
    step: str
    symptom: str
    root_cause_class: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Lesson:
    statement: str
    category: str
    area: str
    pattern_key: str
    applies_to: list[str] = field(default_factory=list)
    confidence_basis: str = "tool_result"
    self_assessment: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class UserRelatedItem:
    statement: str
    kind: str  # preference | habit | personal_fact
    target: str  # memory | persona | experience
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReflectionReport:
    task_uid: str
    outcome: str
    what_worked: list[str] = field(default_factory=list)
    what_failed: list[FailedStep] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    user_related: list[UserRelatedItem] = field(default_factory=list)
    confidence_basis: str = "tool_result+user_edit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_uid": self.task_uid,
            "outcome": self.outcome,
            "what_worked": list(self.what_worked),
            "what_failed": [f.to_dict() for f in self.what_failed],
            "lessons": [L.to_dict() for L in self.lessons],
            "user_related": [u.to_dict() for u in self.user_related],
            "confidence_basis": self.confidence_basis,
        }

    def validate_contract(self) -> list[str]:
        errors: list[str] = []
        if not self.task_uid:
            errors.append("missing_task_uid")
        if not self.outcome:
            errors.append("missing_outcome")
        for f in self.what_failed:
            if f.root_cause_class not in ROOT_CAUSE_CLASSES:
                errors.append(f"bad_root_cause:{f.root_cause_class}")
            if not f.evidence:
                errors.append(f"missing_evidence:{f.step}")
        for L in self.lessons:
            if L.self_assessment or L.confidence_basis == "self_assessment":
                # allowed but must be marked
                if not L.self_assessment:
                    L.self_assessment = True
            # user prefs must not appear in lessons
            if _USER_SIDE_RE.search(L.statement) and not _SYSTEM_SIDE_RE.search(L.statement):
                errors.append(f"user_pref_in_lessons:{L.statement[:40]}")
        return errors


@dataclass
class CandidateEnqueue:
    """Pointers to ch.8–10 candidate queues (no privileged writes)."""

    target: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "payload": dict(self.payload)}


@dataclass
class ReflectResult:
    report: ReflectionReport
    learning_ids: list[str] = field(default_factory=list)
    candidates: list[CandidateEnqueue] = field(default_factory=list)
    contract_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report.to_dict(),
            "learning_ids": list(self.learning_ids),
            "candidates": [c.to_dict() for c in self.candidates],
            "contract_errors": list(self.contract_errors),
        }


def classify_correction(text: str) -> Literal["user", "system", "both"]:
    u = bool(_USER_SIDE_RE.search(text or ""))
    s = bool(_SYSTEM_SIDE_RE.search(text or ""))
    if u and s:
        return "both"
    if u:
        return "user"
    if s:
        return "system"
    return "both"  # §23.4: 无法判断时两边都记录


def parse_reflection_json(raw: Any) -> ReflectionReport:
    data = raw if isinstance(raw, dict) else json.loads(str(raw))
    failures = [
        FailedStep(
            step=str(f.get("step") or ""),
            symptom=str(f.get("symptom") or ""),
            root_cause_class=str(f.get("root_cause_class") or "执行问题"),
            evidence=list(f.get("evidence") or []),
        )
        for f in data.get("what_failed") or []
        if isinstance(f, dict)
    ]
    lessons = []
    for L in data.get("lessons") or []:
        if not isinstance(L, dict):
            continue
        basis = str(L.get("confidence_basis") or "tool_result")
        lessons.append(
            Lesson(
                statement=str(L.get("statement") or ""),
                category=str(L.get("category") or "best_practice"),
                area=str(L.get("area") or "agent"),
                pattern_key=str(L.get("pattern_key") or ""),
                applies_to=list(L.get("applies_to") or []),
                confidence_basis=basis,
                self_assessment=bool(L.get("self_assessment"))
                or basis == "self_assessment",
            )
        )
    users = [
        UserRelatedItem(
            statement=str(u.get("statement") or ""),
            kind=str(u.get("kind") or "preference"),
            target=str(u.get("target") or "memory"),
            evidence=list(u.get("evidence") or []),
        )
        for u in data.get("user_related") or []
        if isinstance(u, dict)
    ]
    return ReflectionReport(
        task_uid=str(data.get("task_uid") or ""),
        outcome=str(data.get("outcome") or "unknown"),
        what_worked=list(data.get("what_worked") or []),
        what_failed=failures,
        lessons=lessons,
        user_related=users,
        confidence_basis=str(data.get("confidence_basis") or "tool_result"),
    )


def deterministic_reflect(
    *,
    task_uid: str,
    goal: str,
    outcome: str,
    errors: Sequence[dict[str, Any]] | None = None,
    feedback: Sequence[str] | None = None,
    corrections: Sequence[str] | None = None,
) -> ReflectionReport:
    """Heuristic reflector for offline tests (injectable model may replace)."""
    what_failed: list[FailedStep] = []
    lessons: list[Lesson] = []
    user_related: list[UserRelatedItem] = []
    worked: list[str] = []

    for i, err in enumerate(errors or []):
        eid = str(err.get("id") or f"ERR-local-{i}")
        symptom = str(err.get("summary") or err.get("error") or "tool failure")
        area = "tools"
        rcc = "工具陷阱" if _SYSTEM_SIDE_RE.search(symptom) else "执行问题"
        what_failed.append(
            FailedStep(
                step=str(err.get("step") or f"s{i}"),
                symptom=symptom,
                root_cause_class=rcc,
                evidence=[eid],
            )
        )
        if rcc == "工具陷阱":
            lessons.append(
                Lesson(
                    statement=f"When using related tools, avoid: {symptom[:80]}",
                    category="best_practice",
                    area=area,
                    pattern_key=f"tools.gotcha.{i}",
                    applies_to=[str(err.get("tool") or "tool")],
                    confidence_basis="tool_result",
                )
            )

    for text in corrections or []:
        side = classify_correction(text)
        if side in ("user", "both"):
            user_related.append(
                UserRelatedItem(
                    statement=text.strip()[:200],
                    kind="preference",
                    target="memory",
                    evidence=["user_message"],
                )
            )
        if side in ("system", "both"):
            lessons.append(
                Lesson(
                    statement=f"System correction: {text.strip()[:120]}",
                    category="correction",
                    area="tools" if _SYSTEM_SIDE_RE.search(text) else "agent",
                    pattern_key="correction.system",
                    confidence_basis="user_feedback",
                )
            )

    if not what_failed and outcome.startswith("accept"):
        worked.append("task completed without recorded tool failures")

    # Unverified speculative lesson → self_assessment (must not promote)
    if not errors and not corrections:
        lessons.append(
            Lesson(
                statement="Model thinks the plan was fine",
                category="best_practice",
                area="agent",
                pattern_key="self.noop",
                confidence_basis="self_assessment",
                self_assessment=True,
            )
        )

    return ReflectionReport(
        task_uid=task_uid,
        outcome=outcome,
        what_worked=worked,
        what_failed=what_failed,
        lessons=lessons,
        user_related=user_related,
        confidence_basis="tool_result+user_edit" if (errors or corrections) else "self_assessment",
    )


class TaskReflector:
    def __init__(
        self,
        store: LearningStore,
        *,
        model_reflect: Callable[[dict[str, Any]], Any] | None = None,
        enqueue_fn: Callable[[CandidateEnqueue], None] | None = None,
    ) -> None:
        self.store = store
        self.model_reflect = model_reflect
        self.enqueue_fn = enqueue_fn
        self.pending_candidates: list[CandidateEnqueue] = []

    def run(
        self,
        *,
        task_uid: str,
        goal: str = "",
        outcome: str = "completed",
        errors: Sequence[dict[str, Any]] | None = None,
        feedback: Sequence[str] | None = None,
        corrections: Sequence[str] | None = None,
        compact_summary: dict[str, Any] | None = None,
    ) -> ReflectResult:
        if self.model_reflect is not None:
            raw = self.model_reflect(
                {
                    "task_uid": task_uid,
                    "goal": goal,
                    "outcome": outcome,
                    "errors": list(errors or []),
                    "feedback": list(feedback or []),
                    "corrections": list(corrections or []),
                    "compact_summary": compact_summary or {},
                }
            )
            report = parse_reflection_json(raw)
        else:
            report = deterministic_reflect(
                task_uid=task_uid,
                goal=goal,
                outcome=outcome,
                errors=errors,
                feedback=feedback,
                corrections=corrections,
            )

        contract_errors = report.validate_contract()
        learning_ids: list[str] = []
        candidates: list[CandidateEnqueue] = []

        # System lessons → learnings (skip pure self_assessment for promotion counting)
        for L in report.lessons:
            if L.self_assessment or L.confidence_basis == "self_assessment":
                # Still may log but mark so aggregate (LW-42) ignores for counts
                continue
            rec = make_lrn(
                category=L.category,
                summary=L.statement,
                details=f"task_reflection applies_to={L.applies_to}",
                area=L.area if L.area in (
                    "wiki",
                    "memory",
                    "experience",
                    "style",
                    "agent",
                    "tools",
                    "skills",
                    "routing",
                    "ui",
                    "integration",
                )
                else "agent",
                pattern_key=L.pattern_key or f"reflect.{task_uid[:8]}",
                related=[task_uid],
                evidence=["task_reflection", "tool_result"]
                if L.confidence_basis != "user_feedback"
                else ["task_reflection", "user_feedback"],
            )
            rec.source = "task_reflection"
            saved = self.store.append(rec)
            learning_ids.append(saved.record_id)

        # User-related → ch.8–10 candidate queues
        for u in report.user_related:
            cand = CandidateEnqueue(
                target=u.target,
                payload={
                    "kind": u.kind,
                    "statement": u.statement,
                    "evidence": u.evidence,
                    "task_uid": task_uid,
                    "source": "task_reflection",
                },
            )
            candidates.append(cand)
            self.pending_candidates.append(cand)
            if self.enqueue_fn is not None:
                self.enqueue_fn(cand)

        return ReflectResult(
            report=report,
            learning_ids=learning_ids,
            candidates=candidates,
            contract_errors=contract_errors,
        )
