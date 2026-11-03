"""ExperiencePack contract and selection (design §9.6–9.7).

Unconfirmed candidates never enter the execution pack (``selected``).
They may appear only in ``candidates_shown_not_used`` for UI reference.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

ConfirmationState = Literal[
    "unconfirmed",
    "user_confirmed",
    "revoked",
    "pending",
]
Lifecycle = Literal[
    "proposed",
    "active",
    "needs_review",
    "deprecated",
    "disabled",
]

EVIDENCE_GRADE_WEIGHT: dict[str, float] = {
    "direct_statement": 1.0,
    "single_case": 0.55,
    "multiple_independent_cases": 0.85,
    "transfer_verified": 1.0,
}

PRIORITY_NOTE = (
    "当前用户要求 > 项目约束 > 已确认经验 > 推断候选 > 模型通用做法"
)

# §8.7 default experience share for agent context (tokens when context=15k)
DEFAULT_BUDGET_TOKENS = 1800


def _uid(prefix: str = "xp") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^\w]+", (text or "").lower()) if len(t) >= 2}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class ExperienceItem:
    """One confirmed (or candidate) experience record available for selection."""

    uid: str
    kind: str  # rule | procedure | counterexample | rubric | template
    title: str = ""
    summary: str = ""
    version_sha: str = ""
    confirmation_state: ConfirmationState | str = "unconfirmed"
    lifecycle: Lifecycle | str = "proposed"
    evidence_grade: str = "single_case"
    task_types: list[str] = field(default_factory=list)
    project_scopes: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    privacy_classification: str = "private"
    preferred_tools: list[str] = field(default_factory=list)
    body: str = ""
    embedding: list[float] | None = None

    def __post_init__(self) -> None:
        if not self.version_sha:
            self.version_sha = _sha(
                f"{self.uid}|{self.kind}|{self.title}|{self.summary}|{self.body}"
            )

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation_state == "user_confirmed"

    @property
    def is_executable(self) -> bool:
        return (
            self.is_confirmed
            and self.lifecycle == "active"
            and self.confirmation_state != "revoked"
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelectedEntry:
    uid: str
    version_sha: str
    kind: str
    why: str
    grade: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RejectedEntry:
    uid: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperiencePack:
    """Execution pack contract (§9.6 JSON)."""

    pack_uid: str
    task_uid: str
    selected: list[SelectedEntry] = field(default_factory=list)
    rejected: list[RejectedEntry] = field(default_factory=list)
    candidates_shown_not_used: list[str] = field(default_factory=list)
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    priority_note: str = PRIORITY_NOTE
    task_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_uid": self.pack_uid,
            "task_uid": self.task_uid,
            "selected": [s.to_dict() for s in self.selected],
            "rejected": [r.to_dict() for r in self.rejected],
            "candidates_shown_not_used": list(self.candidates_shown_not_used),
            "budget_tokens": self.budget_tokens,
            "priority_note": self.priority_note,
            "task_type": self.task_type,
        }

    def selected_uids(self) -> list[str]:
        return [s.uid for s in self.selected]


@dataclass
class TaskSpec:
    """Task specification (§9.7). Privilege fields are caller-owned."""

    task_uid: str
    goal: str
    task_type: str
    inputs: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    must_pass_checks: list[str] = field(default_factory=list)
    approval_mode: str = "workspace-auto"
    experience_pack_uid: str | None = None
    style_directives_uid: str | None = None
    tool_scope: list[str] = field(default_factory=list)
    privacy: dict[str, Any] = field(
        default_factory=lambda: {
            "allow_online_model": True,
            "allowed_classifications": ["normal", "private"],
        }
    )
    project_scope: str | None = None
    schema_version: int = 3
    query_text: str = ""  # for relevance; defaults to goal

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_uid": self.task_uid,
            "goal": self.goal,
            "task_type": self.task_type,
            "inputs": list(self.inputs),
            "constraints": list(self.constraints),
            "deliverables": list(self.deliverables),
            "must_pass_checks": list(self.must_pass_checks),
            "approval_mode": self.approval_mode,
            "experience_pack_uid": self.experience_pack_uid,
            "style_directives_uid": self.style_directives_uid,
            "tool_scope": list(self.tool_scope),
            "privacy": dict(self.privacy),
            "project_scope": self.project_scope,
        }


def _estimate_tokens(item: ExperienceItem) -> int:
    text = f"{item.title} {item.summary} {item.body}"
    # rough CJK+latin heuristic
    return max(40, len(text) // 2)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _hard_filter_reason(
    item: ExperienceItem,
    task: TaskSpec,
) -> str | None:
    """Return rejection reason, or None if passes hard filters."""
    if item.lifecycle in ("disabled", "deprecated"):
        return f"lifecycle={item.lifecycle}"
    if item.confirmation_state == "revoked":
        return "confirmation_state=revoked"
    if not item.is_confirmed:
        # Not a reject into execution — handled as reference-only
        return None
    if item.lifecycle != "active":
        return f"lifecycle={item.lifecycle} (confirmed but not active)"
    if item.task_types and task.task_type not in item.task_types:
        return f"task_type mismatch: need one of {item.task_types}, got {task.task_type}"
    if item.project_scopes and task.project_scope:
        if task.project_scope not in item.project_scopes:
            return (
                f"project_scope mismatch: need one of {item.project_scopes}, "
                f"got {task.project_scope}"
            )
    allowed = set((task.privacy or {}).get("allowed_classifications") or [])
    if allowed and item.privacy_classification not in allowed:
        return (
            f"privacy={item.privacy_classification} not in "
            f"allowed_classifications={sorted(allowed)}"
        )
    return None


def _exclusion_hit(item: ExperienceItem, task: TaskSpec) -> str | None:
    blob = " ".join(
        [
            task.goal,
            " ".join(task.constraints),
            " ".join(str(d) for d in task.deliverables),
            task.query_text or "",
        ]
    ).lower()
    for ex in item.exclusions:
        if not ex:
            continue
        if str(ex).lower() in blob:
            return f"exclusions 包含：{ex}"
    return None


def select_experience_pack(
    items: Sequence[ExperienceItem],
    task: TaskSpec,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    query_embedding: Sequence[float] | None = None,
) -> ExperiencePack:
    """Build ExperiencePack: hard filter → exclusions → relevance → grade → budget.

    Unconfirmed items never appear in ``selected``; they go to
    ``candidates_shown_not_used`` when relevant enough to show.
    """
    pack = ExperiencePack(
        pack_uid=_uid("xp"),
        task_uid=task.task_uid,
        budget_tokens=budget_tokens,
        task_type=task.task_type,
    )
    query = task.query_text or task.goal
    q_tokens = _tokens(query)

    scored: list[tuple[float, ExperienceItem, str]] = []
    for item in items:
        if not item.is_confirmed:
            # Reference only — never execute
            rel = _overlap(q_tokens, _tokens(f"{item.title} {item.summary} {item.body}"))
            if item.task_types and task.task_type in item.task_types:
                rel = max(rel, 0.35)
            if rel >= 0.15 or (item.task_types and task.task_type in item.task_types):
                pack.candidates_shown_not_used.append(item.uid)
            continue

        reason = _hard_filter_reason(item, task)
        if reason:
            pack.rejected.append(RejectedEntry(uid=item.uid, why=reason))
            continue

        excl = _exclusion_hit(item, task)
        if excl:
            pack.rejected.append(RejectedEntry(uid=item.uid, why=excl))
            continue

        lexical = _overlap(q_tokens, _tokens(f"{item.title} {item.summary} {item.body}"))
        vector = 0.0
        if query_embedding is not None and item.embedding:
            vector = _cosine(query_embedding, item.embedding)
        grade_w = EVIDENCE_GRADE_WEIGHT.get(item.evidence_grade, 0.5)
        # task_type exact match boost
        type_boost = 0.25 if (not item.task_types or task.task_type in item.task_types) else 0.0
        score = 0.45 * lexical + 0.35 * vector + 0.20 * grade_w + type_boost
        why = (
            f"task_type={task.task_type}"
            + (f" 且匹配「{item.title or item.uid}」" if item.title else "")
            + f"；grade={item.evidence_grade}；score={score:.2f}"
        )
        scored.append((score, item, why))

    scored.sort(key=lambda x: x[0], reverse=True)

    used = 0
    for score, item, why in scored:
        cost = _estimate_tokens(item)
        if used + cost > budget_tokens and pack.selected:
            pack.rejected.append(
                RejectedEntry(
                    uid=item.uid,
                    why=f"budget_tokens exceeded (need {cost}, left {budget_tokens - used})",
                )
            )
            continue
        if used + cost > budget_tokens and not pack.selected:
            # Always allow at least one if it alone fits poorly — still reject if huge
            if cost > budget_tokens:
                pack.rejected.append(
                    RejectedEntry(
                        uid=item.uid,
                        why=f"single item tokens {cost} > budget {budget_tokens}",
                    )
                )
                continue
        pack.selected.append(
            SelectedEntry(
                uid=item.uid,
                version_sha=item.version_sha,
                kind=item.kind,
                why=why,
                grade=item.evidence_grade,
            )
        )
        used += cost

    # Deduplicate reference list
    pack.candidates_shown_not_used = list(dict.fromkeys(pack.candidates_shown_not_used))
    return pack


def render_pack_block(pack: ExperiencePack, items_by_uid: dict[str, ExperienceItem]) -> str:
    """Format selected experiences for context injection (audit-friendly UIDs)."""
    lines = ["<experience_pack>", f"<!-- pack={pack.pack_uid} task={pack.task_uid} -->"]
    for sel in pack.selected:
        item = items_by_uid.get(sel.uid)
        text = (item.summary if item else "") or sel.why
        lines.append(
            f"- [{sel.uid}] ({sel.kind}, {sel.grade}) {text}"
        )
    lines.append("</experience_pack>")
    return "\n".join(lines)
