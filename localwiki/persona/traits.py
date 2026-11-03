"""Aggregate style signals into Persona Trait candidates (§10.2, §10.4, §10.5).

Evidence gate (defaults, configurable):
- direct_statement → candidate immediately (grade direct_statement)
- ≥2 source kinds AND ≥6 independent evidence, same direction → candidate
- otherwise → observing only (no candidate)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from localwiki.persona.signals import WORK_STYLE_DIMENSIONS, StyleSignal

DEFAULT_MIN_EVIDENCE = 6
DEFAULT_MIN_SOURCE_KINDS = 2
# Directions with opposite signs beyond this gap count as inconsistent
DIRECTION_CONSISTENCY_EPS = 0.25


def _uid(prefix: str = "trait") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class EvidenceGateConfig:
    min_evidence: int = DEFAULT_MIN_EVIDENCE
    min_source_kinds: int = DEFAULT_MIN_SOURCE_KINDS
    consistency_eps: float = DIRECTION_CONSISTENCY_EPS


@dataclass
class PersonaTraitCandidate:
    """Proposed Persona Trait page payload (§10.5) — not auto-activated."""

    uid: str
    dimension: str
    context: str
    title: str
    description: str
    value_range: tuple[float, float]
    evidence_summary: dict[str, int]
    evidence_refs: list[str]
    confirmation_state: str = "unconfirmed"
    lifecycle: str = "proposed"
    framework: str = "work_style"
    evidence_grade: str = "single_case"
    user_note: str = ""
    representative_excerpts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["value_range"] = list(self.value_range)
        return d

    def to_front_matter(self) -> dict[str, Any]:
        return {
            "type": "Persona Trait",
            "title": self.title,
            "description": self.description,
            "status": "draft",
            "kw": {
                "schema_version": 3,
                "uid": self.uid,
                "security": {"classification": "private", "scopes": ["personal"]},
                "persona": {
                    "framework": self.framework,
                    "dimension": self.dimension,
                    "context": self.context,
                    "value_range": list(self.value_range),
                    "evidence_summary": dict(self.evidence_summary),
                    "evidence_refs": list(self.evidence_refs),
                    "confirmation_state": self.confirmation_state,
                    "user_note": self.user_note,
                    "lifecycle": self.lifecycle,
                },
            },
        }


@dataclass
class ObservingBucket:
    dimension: str
    context: str
    signals: list[StyleSignal]
    reason: str
    value_range: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "context": self.context,
            "signal_count": len(self.signals),
            "reason": self.reason,
            "value_range": list(self.value_range) if self.value_range else None,
            "evidence_refs": [s.evidence_ref for s in self.signals],
        }


@dataclass
class TraitAggregateReport:
    candidates: list[PersonaTraitCandidate] = field(default_factory=list)
    observing: list[ObservingBucket] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "observing": [o.to_dict() for o in self.observing],
            "issues": list(self.issues),
        }


def _source_bucket(source: str) -> str:
    if source == "direct_statement":
        return "statements"
    if source == "text":
        return "documents"
    if source == "behavior":
        return "behavior_events"
    if source == "agent_trace":
        return "agent_sessions"
    return "other"


def _value_range(directions: Sequence[float], n: int) -> tuple[float, float]:
    """Wider interval when few / dispersed evidence (§10.4)."""
    if not directions:
        return (0.0, 0.0)
    mean = sum(directions) / len(directions)
    # sample spread
    var = sum((d - mean) ** 2 for d in directions) / max(1, len(directions))
    spread = var ** 0.5
    # evidence scarcity widens
    scarcity = max(0.3, 1.5 / max(1, n) ** 0.5)
    half = max(0.2, spread + scarcity * 0.5)
    lo = max(-2.0, mean - half)
    hi = min(2.0, mean + half)
    if lo > hi:
        lo, hi = hi, lo
    return (round(lo, 2), round(hi, 2))


def _directions_consistent(
    directions: Sequence[float],
    eps: float,
) -> bool:
    if not directions:
        return False
    # All non-near-zero should share a sign (or be near zero)
    signs = []
    for d in directions:
        if abs(d) <= eps:
            continue
        signs.append(1 if d > 0 else -1)
    if not signs:
        return False  # all neutral — not enough directional evidence
    return len(set(signs)) == 1


def _title_for(dimension: str, value_range: tuple[float, float]) -> str:
    mid = (value_range[0] + value_range[1]) / 2
    labels = {
        "information_density": ("详尽完整", "简洁要点"),
        "expression_style": ("委婉含蓄", "直接明确"),
        "structure_preference": ("叙述段落", "列表表格"),
        "decision_basis": ("经验直觉", "数据证据"),
        "decision_speed": ("充分论证再定", "快速决定"),
        "risk_preference": ("保守稳妥", "敢于尝试"),
        "perspective": ("全局概览", "细节落实"),
        "autonomy_preference": ("希望多确认", "希望少打扰"),
        "plan_granularity": ("粗粒度里程碑", "细粒度步骤"),
        "quality_orientation": ("够用即可", "精益求精"),
        "learning_style": ("先看示例", "先讲原理"),
        "work_rhythm": ("均匀推进", "截止前集中"),
    }
    neg, pos = labels.get(dimension, ("负向", "正向"))
    tip = pos if mid >= 0 else neg
    return f"{dimension}偏好：{tip}"


def aggregate_traits(
    signals: Sequence[StyleSignal],
    *,
    config: EvidenceGateConfig | None = None,
) -> TraitAggregateReport:
    cfg = config or EvidenceGateConfig()
    report = TraitAggregateReport()
    kept = [s for s in signals if not s.discarded]
    if not kept:
        report.issues.append("no_signals")
        return report

    groups: dict[tuple[str, str], list[StyleSignal]] = defaultdict(list)
    for s in kept:
        if s.dimension not in WORK_STYLE_DIMENSIONS:
            report.issues.append(f"skip_unknown_dimension:{s.dimension}")
            continue
        groups[(s.dimension, s.context or "work")].append(s)

    for (dimension, context), group in sorted(groups.items()):
        directions = [s.direction for s in group]
        refs = list(dict.fromkeys(s.evidence_ref for s in group))
        source_kinds = {s.source for s in group}
        summary = {
            "statements": 0,
            "documents": 0,
            "behavior_events": 0,
            "agent_sessions": 0,
        }
        for s in group:
            bucket = _source_bucket(s.source)
            if bucket in summary:
                summary[bucket] += 1

        vr = _value_range(directions, len(group))
        excerpts = [s.excerpt for s in group if s.excerpt][:5]

        # Gate 1: any direct_statement → candidate
        directs = [s for s in group if s.source == "direct_statement"]
        if directs:
            # Use all group for range but grade as direct
            cand = PersonaTraitCandidate(
                uid=_uid(f"trait_{dimension}"),
                dimension=dimension,
                context=context,
                title=_title_for(dimension, vr),
                description=(
                    f"在{context}场景中，由用户明确声明等证据支持的工作风格倾向。"
                ),
                value_range=vr,
                evidence_summary=summary,
                evidence_refs=refs,
                evidence_grade="direct_statement",
                representative_excerpts=excerpts,
            )
            report.candidates.append(cand)
            continue

        consistent = _directions_consistent(directions, cfg.consistency_eps)
        n = len(group)
        n_sources = len(source_kinds)

        if (
            n >= cfg.min_evidence
            and n_sources >= cfg.min_source_kinds
            and consistent
        ):
            grade = (
                "multiple_independent_cases"
                if n >= cfg.min_evidence
                else "single_case"
            )
            cand = PersonaTraitCandidate(
                uid=_uid(f"trait_{dimension}"),
                dimension=dimension,
                context=context,
                title=_title_for(dimension, vr),
                description=(
                    f"在{context}场景中，由 {n} 条、{n_sources} 类来源一致证据聚合的倾向。"
                ),
                value_range=vr,
                evidence_summary=summary,
                evidence_refs=refs,
                evidence_grade=grade,
                representative_excerpts=excerpts,
            )
            report.candidates.append(cand)
            continue

        # Below threshold → observing only
        if not consistent:
            reason = "inconsistent_direction"
        elif n_sources < cfg.min_source_kinds:
            reason = (
                f"single_source_or_insufficient_kinds:"
                f"{n_sources}<{cfg.min_source_kinds}"
            )
        else:
            reason = f"insufficient_evidence:{n}<{cfg.min_evidence}"
        report.observing.append(
            ObservingBucket(
                dimension=dimension,
                context=context,
                signals=list(group),
                reason=reason,
                value_range=vr,
            )
        )

    return report
