"""StyleDirectives compilation and per-feature application (§10.6).

Hard constraints:
- Style affects expression / work manner only — never facts or conclusions.
- Style cannot relax security approvals.
- Current user instruction > project constraints > StyleDirectives.
- Each surface (chat / agent / artifact) can be disabled independently.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

from localwiki.persona.traits import PersonaTraitCandidate

ApplySurface = Literal["chat", "agent", "artifact", "wiki_compile", "scheduling"]

SURFACES: tuple[str, ...] = (
    "chat",
    "agent",
    "artifact",
    "wiki_compile",
    "scheduling",
)


def _uid(prefix: str = "sd") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _sha(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class StyleFeatureFlags:
    """Per-surface enable flags; global_enabled gates everything."""

    global_enabled: bool = True
    chat: bool = True
    agent: bool = True
    artifact: bool = True
    wiki_compile: bool = True
    scheduling: bool = True

    def is_enabled(self, surface: str) -> bool:
        if not self.global_enabled:
            return False
        return bool(getattr(self, surface, False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_enabled": self.global_enabled,
            **{s: bool(getattr(self, s)) for s in SURFACES},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StyleFeatureFlags:
        if not data:
            return cls()
        return cls(
            global_enabled=bool(data.get("global_enabled", True)),
            **{s: bool(data.get(s, True)) for s in SURFACES},
        )


@dataclass
class StyleDirectives:
    """Compiled style object consumed by features (§10.6 JSON)."""

    sd_uid: str
    context: str = "work"
    compiled_from: list[str] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    artifact: dict[str, Any] = field(default_factory=dict)
    scheduling: dict[str, Any] = field(default_factory=dict)
    prompt_text: str = ""
    version_sha: str = ""
    flags: StyleFeatureFlags = field(default_factory=StyleFeatureFlags)
    # Hard constraint markers (never omitted)
    cannot_relax_approvals: bool = True
    cannot_alter_facts: bool = True
    priority_note: str = "当前用户指令 > 项目约束 > StyleDirectives"

    def __post_init__(self) -> None:
        if not self.version_sha:
            self.version_sha = _sha(self.to_dict_body())

    def to_dict_body(self) -> dict[str, Any]:
        return {
            "sd_uid": self.sd_uid,
            "context": self.context,
            "compiled_from": list(self.compiled_from),
            "response": dict(self.response),
            "agent": dict(self.agent),
            "artifact": dict(self.artifact),
            "scheduling": dict(self.scheduling),
            "prompt_text": self.prompt_text,
        }

    def to_dict(self) -> dict[str, Any]:
        d = self.to_dict_body()
        d["version_sha"] = self.version_sha
        d["flags"] = self.flags.to_dict()
        d["cannot_relax_approvals"] = self.cannot_relax_approvals
        d["cannot_alter_facts"] = self.cannot_alter_facts
        d["priority_note"] = self.priority_note
        return d


def _mid(value_range: Sequence[float] | tuple[float, float]) -> float:
    if not value_range:
        return 0.0
    return (float(value_range[0]) + float(value_range[-1])) / 2.0


def _pick(mid: float, low: str, high: str, neutral: str = "neutral") -> str:
    if mid >= 0.5:
        return high
    if mid <= -0.5:
        return low
    return neutral


def compile_style_directives(
    traits: Sequence[PersonaTraitCandidate | dict[str, Any]],
    *,
    context: str = "work",
    preference_memories: Sequence[dict[str, Any]] | None = None,
    writing_experience_summaries: Sequence[str] | None = None,
    flags: StyleFeatureFlags | None = None,
) -> StyleDirectives:
    """Compile confirmed/active traits (+ optional prefs) into StyleDirectives.

    Callers should pass only user_confirmed + active traits for production use.
    Unconfirmed traits may be compiled for preview but must not be marked active
    by this function — confirmation is external.
    """
    flags = flags or StyleFeatureFlags()
    dim_vals: dict[str, float] = {}
    compiled_from: list[str] = []

    for t in traits:
        if isinstance(t, PersonaTraitCandidate):
            if t.context and t.context != context:
                continue
            dim_vals[t.dimension] = _mid(t.value_range)
            compiled_from.append(t.uid)
        elif isinstance(t, dict):
            persona = t.get("persona") or t
            ctx = persona.get("context", t.get("context", "work"))
            if ctx != context:
                continue
            dim = str(persona.get("dimension") or "")
            vr = persona.get("value_range") or [0, 0]
            dim_vals[dim] = _mid(vr)
            compiled_from.append(str(persona.get("uid") or t.get("uid") or dim))

    for mem in preference_memories or []:
        uid = str(mem.get("uid") or "")
        if uid:
            compiled_from.append(uid)

    density = dim_vals.get("information_density", 0.0)
    expression = dim_vals.get("expression_style", 0.0)
    structure = dim_vals.get("structure_preference", 0.0)
    decision_speed = dim_vals.get("decision_speed", 0.0)
    autonomy = dim_vals.get("autonomy_preference", 0.0)
    plan_g = dim_vals.get("plan_granularity", 0.0)
    learn = dim_vals.get("learning_style", 0.0)
    perspective = dim_vals.get("perspective", 0.0)
    quality = dim_vals.get("quality_orientation", 0.0)
    rhythm = dim_vals.get("work_rhythm", 0.0)

    response = {
        "length": _pick(density, "detailed", "concise", "balanced"),
        "lead_with": _pick(decision_speed, "analysis", "conclusion", "balanced"),
        "tone": _pick(expression, "soft", "direct", "neutral"),
        "format": _pick(structure, "paragraphs", "tables_and_lists", "mixed"),
        "explain_order": _pick(learn, "example_first", "principle_first", "mixed"),
    }
    agent = {
        "plan_granularity": _pick(plan_g, "coarse", "fine", "balanced"),
        "checkin_frequency": _pick(
            autonomy, "each_step", "milestones_only", "default"
        ),
        "report_style": _pick(density, "verbose", "brief", "balanced"),
        # Explicit: autonomy never widens approval mode
        "approval_mode_locked": True,
    }
    artifact = {
        "structure": _pick(perspective, "overview_first", "detail_first", "summary_first"),
        "density": _pick(density, "low", "high", "medium"),
        "terminology": "keep_user_terms",
        "polish": _pick(quality, "ship_fast", "polish", "balanced"),
    }
    scheduling = {
        "preferred_hours": (
            ["20:00-23:00"] if rhythm >= 0.5 else ["09:00-12:00", "14:00-18:00"]
        ),
        "reminder_lead_time": "1d",
    }

    prompt_parts = [
        f"回答{_pick(density, '详尽展开', '先给结论再给要点', '适度详略')}",
        f"语气{_pick(expression, '委婉', '直接', '中性')}",
        f"版式偏好{_pick(structure, '段落叙述', '表格与列表', '混合')}",
    ]
    if writing_experience_summaries:
        prompt_parts.append(
            "写作经验：" + "；".join(writing_experience_summaries[:3])
        )
    prompt_parts.append("不得改变事实与结论；不得放宽安全审批。")

    sd = StyleDirectives(
        sd_uid=_uid("sd"),
        context=context,
        compiled_from=list(dict.fromkeys(compiled_from)),
        response=response,
        agent=agent,
        artifact=artifact,
        scheduling=scheduling,
        prompt_text="；".join(prompt_parts),
        flags=flags,
    )
    sd.version_sha = _sha(sd.to_dict_body())
    return sd


@dataclass
class SurfaceApplyResult:
    surface: str
    applied: bool
    disabled_reason: str | None
    fragment: dict[str, Any]
    prompt_addon: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StyleDirectiveService:
    """Feature-facing API: compile once, apply per surface with kill switches."""

    def __init__(
        self,
        directives: StyleDirectives | None = None,
        flags: StyleFeatureFlags | None = None,
    ) -> None:
        self.directives = directives
        self.flags = flags or (directives.flags if directives else StyleFeatureFlags())

    def set_flags(self, flags: StyleFeatureFlags) -> None:
        self.flags = flags
        if self.directives:
            self.directives.flags = flags

    def disable(self, surface: str | None = None) -> None:
        """Disable one surface, or all when surface is None (global)."""
        if surface is None:
            self.flags.global_enabled = False
        elif hasattr(self.flags, surface):
            setattr(self.flags, surface, False)
        if self.directives:
            self.directives.flags = self.flags

    def enable(self, surface: str | None = None) -> None:
        if surface is None:
            self.flags.global_enabled = True
        elif hasattr(self.flags, surface):
            setattr(self.flags, surface, True)
        if self.directives:
            self.directives.flags = self.flags

    def apply(
        self,
        surface: ApplySurface | str,
        *,
        user_instruction: str | None = None,
        project_constraints: Sequence[str] | None = None,
        approval_mode: str | None = None,
    ) -> SurfaceApplyResult:
        """Return style fragment for a surface, or empty if disabled.

        ``user_instruction`` / ``project_constraints`` are echoed for priority
        awareness; style never overrides them. ``approval_mode`` is passed
        through unchanged (style cannot relax it).
        """
        if self.directives is None:
            return SurfaceApplyResult(
                surface=surface,
                applied=False,
                disabled_reason="no_directives",
                fragment={},
                prompt_addon="",
            )
        if not self.flags.is_enabled(surface):
            reason = (
                "global_disabled"
                if not self.flags.global_enabled
                else f"surface_disabled:{surface}"
            )
            return SurfaceApplyResult(
                surface=surface,
                applied=False,
                disabled_reason=reason,
                fragment={},
                prompt_addon="",
            )

        sd = self.directives
        if surface == "chat":
            fragment = dict(sd.response)
            addon = sd.prompt_text
        elif surface == "agent":
            fragment = dict(sd.agent)
            addon = (
                f"计划粒度={sd.agent.get('plan_granularity')}；"
                f"汇报={sd.agent.get('report_style')}"
            )
        elif surface in ("artifact", "wiki_compile"):
            fragment = dict(sd.artifact)
            if surface == "wiki_compile":
                fragment = {
                    "density": sd.artifact.get("density"),
                    "terminology": sd.artifact.get("terminology"),
                }
            addon = f"成果结构={sd.artifact.get('structure')}；密度={sd.artifact.get('density')}"
        elif surface == "scheduling":
            fragment = dict(sd.scheduling)
            addon = ""
        else:
            fragment = {}
            addon = ""

        # Hard constraints always attached
        fragment["cannot_relax_approvals"] = True
        fragment["cannot_alter_facts"] = True
        if approval_mode is not None:
            fragment["approval_mode"] = approval_mode  # unchanged pass-through
        if user_instruction:
            fragment["user_instruction_priority"] = True
        if project_constraints:
            fragment["project_constraints"] = list(project_constraints)

        return SurfaceApplyResult(
            surface=surface,
            applied=True,
            disabled_reason=None,
            fragment=fragment,
            prompt_addon=addon,
        )
