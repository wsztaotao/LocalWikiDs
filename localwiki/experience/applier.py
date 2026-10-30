"""ExperienceApplier — inject ExperiencePack into product surfaces (§9.6).

Application points: agent planning, self-check, writing, chat, wiki compile,
tool/skill ordering, nightly suggestions. Privilege fields on TaskSpec are
never expanded by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from localwiki.experience.pack import (
    DEFAULT_BUDGET_TOKENS,
    ExperienceItem,
    ExperiencePack,
    TaskSpec,
    render_pack_block,
    select_experience_pack,
)

ApplyPoint = Literal[
    "agent_plan",
    "agent_self_check",
    "writing",
    "chat",
    "wiki_compile",
    "tool_select",
    "nightly",
]

# §8.7 experience share of context — used to derive token budgets from context size
APPLY_BUDGET_SHARE: dict[ApplyPoint, float] = {
    "agent_plan": 0.12,
    "agent_self_check": 0.12,
    "writing": 0.08,
    "chat": 0.05,
    "wiki_compile": 0.03,
    "tool_select": 0.05,
    "nightly": 0.08,
}

# Which kinds each surface prefers
APPLY_KINDS: dict[ApplyPoint, frozenset[str]] = {
    "agent_plan": frozenset({"procedure", "rule", "counterexample"}),
    "agent_self_check": frozenset({"rubric"}),
    "writing": frozenset({"rule", "rubric"}),
    "chat": frozenset({"rule", "counterexample"}),
    "wiki_compile": frozenset({"rule", "procedure"}),
    "tool_select": frozenset({"procedure", "rule"}),
    "nightly": frozenset({"procedure"}),
}


@dataclass
class ApplyResult:
    apply_point: ApplyPoint | str
    pack: ExperiencePack
    injection_text: str
    preferred_tools: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "apply_point": self.apply_point,
            "pack": self.pack.to_dict(),
            "injection_text": self.injection_text,
            "preferred_tools": list(self.preferred_tools),
            "notes": list(self.notes),
        }


class ExperienceApplier:
    """Select and format experiences for a TaskSpec / apply point."""

    def __init__(
        self,
        catalog: Sequence[ExperienceItem] | None = None,
        *,
        context_tokens: int = 15000,
    ) -> None:
        self.catalog: list[ExperienceItem] = list(catalog or [])
        self.context_tokens = context_tokens
        self._by_uid = {i.uid: i for i in self.catalog}

    def register(self, item: ExperienceItem) -> None:
        self.catalog.append(item)
        self._by_uid[item.uid] = item

    def budget_for(self, apply_point: ApplyPoint | str) -> int:
        share = APPLY_BUDGET_SHARE.get(apply_point, 0.05)  # type: ignore[arg-type]
        return max(200, int(self.context_tokens * share))

    def build_pack(
        self,
        task: TaskSpec,
        *,
        apply_point: ApplyPoint | str = "agent_plan",
        budget_tokens: int | None = None,
        query_embedding: Sequence[float] | None = None,
    ) -> ExperiencePack:
        kinds = APPLY_KINDS.get(apply_point)  # type: ignore[arg-type]
        pool = [
            i
            for i in self.catalog
            if kinds is None or i.kind in kinds or not i.is_confirmed
        ]
        # Always include unconfirmed of any kind for reference list filtering
        # (select_experience_pack handles them)
        seen = {i.uid for i in pool}
        for i in self.catalog:
            if i.uid not in seen and not i.is_confirmed:
                pool.append(i)
        budget = budget_tokens if budget_tokens is not None else self.budget_for(apply_point)
        if budget_tokens is None and apply_point == "agent_plan":
            budget = max(budget, DEFAULT_BUDGET_TOKENS)
        return select_experience_pack(
            pool,
            task,
            budget_tokens=budget,
            query_embedding=query_embedding,
        )

    def apply(
        self,
        task: TaskSpec,
        *,
        apply_point: ApplyPoint | str = "agent_plan",
        budget_tokens: int | None = None,
        query_embedding: Sequence[float] | None = None,
    ) -> ApplyResult:
        pack = self.build_pack(
            task,
            apply_point=apply_point,
            budget_tokens=budget_tokens,
            query_embedding=query_embedding,
        )
        # Attach pack uid onto a copy-friendly note for TaskSpec consumers
        notes = [
            "unconfirmed_excluded_from_selected",
            f"priority={pack.priority_note}",
        ]
        preferred: list[str] = []
        if apply_point in ("tool_select", "agent_plan"):
            for sel in pack.selected:
                item = self._by_uid.get(sel.uid)
                if item and item.preferred_tools:
                    preferred.extend(item.preferred_tools)
            preferred = list(dict.fromkeys(preferred))
            if preferred:
                notes.append(f"preferred_tools={preferred}")

        if apply_point == "nightly":
            notes.append("nightly_suggestions_require_user_confirmation")

        # Prevent copying old project facts: strip numeric/project params reminder
        notes.append("parameters_must_come_from_current_task_inputs")

        text = render_pack_block(pack, self._by_uid)
        return ApplyResult(
            apply_point=apply_point,
            pack=pack,
            injection_text=text,
            preferred_tools=preferred,
            notes=notes,
        )

    def attach_to_task_spec(
        self,
        task: TaskSpec,
        *,
        apply_point: ApplyPoint | str = "agent_plan",
    ) -> tuple[TaskSpec, ApplyResult]:
        """Return TaskSpec with experience_pack_uid set; does not widen privileges."""
        result = self.apply(task, apply_point=apply_point)
        # Shallow copy fields onto new TaskSpec
        updated = TaskSpec(
            task_uid=task.task_uid,
            goal=task.goal,
            task_type=task.task_type,
            inputs=list(task.inputs),
            constraints=list(task.constraints),
            deliverables=list(task.deliverables),
            must_pass_checks=list(task.must_pass_checks),
            approval_mode=task.approval_mode,
            experience_pack_uid=result.pack.pack_uid,
            style_directives_uid=task.style_directives_uid,
            tool_scope=list(task.tool_scope),
            privacy=dict(task.privacy),
            project_scope=task.project_scope,
            schema_version=task.schema_version,
            query_text=task.query_text,
        )
        return updated, result
