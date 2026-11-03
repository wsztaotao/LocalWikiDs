"""Tests for persona trait aggregation (LW-22 / §10.4–10.5)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.persona.signals import StyleSignal  # noqa: E402
from localwiki.persona.traits import (  # noqa: E402
    EvidenceGateConfig,
    aggregate_traits,
)


def _sig(
    dim: str = "information_density",
    direction: float = 1.0,
    source: str = "text",
    ref: str = "r1",
    excerpt: str = "简短",
) -> StyleSignal:
    return StyleSignal(
        signal_uid=f"sig_{ref}",
        dimension=dim,
        direction=direction,
        source=source,
        evidence_ref=ref,
        excerpt=excerpt,
        speaker_role="self",
    )


def test_direct_statement_generates_candidate() -> None:
    report = aggregate_traits(
        [_sig(source="direct_statement", ref="stmt1", excerpt="我喜欢简短的回答")]
    )
    assert len(report.candidates) == 1
    assert report.candidates[0].evidence_grade == "direct_statement"
    assert report.candidates[0].confirmation_state == "unconfirmed"
    assert report.candidates[0].lifecycle == "proposed"
    assert report.observing == []


def test_insufficient_evidence_no_candidate() -> None:
    # 5 text-only same direction — below 6 and single source kind
    sigs = [_sig(ref=f"t{i}", direction=1.0, source="text") for i in range(5)]
    report = aggregate_traits(sigs)
    assert report.candidates == []
    assert report.observing
    assert "insufficient" in report.observing[0].reason or "single_source" in report.observing[0].reason


def test_single_source_even_with_many_no_candidate() -> None:
    sigs = [_sig(ref=f"t{i}", direction=1.2, source="text") for i in range(8)]
    report = aggregate_traits(sigs)
    assert report.candidates == []
    assert any("source" in o.reason for o in report.observing)


def test_two_sources_six_consistent_generates_candidate() -> None:
    sigs = [_sig(ref=f"t{i}", direction=1.0, source="text") for i in range(3)]
    sigs += [
        _sig(ref=f"b{i}", direction=1.1, source="behavior") for i in range(3)
    ]
    report = aggregate_traits(sigs)
    assert len(report.candidates) == 1
    c = report.candidates[0]
    assert c.evidence_grade == "multiple_independent_cases"
    assert c.value_range[0] <= c.value_range[1]
    assert c.evidence_summary["documents"] == 3
    assert c.evidence_summary["behavior_events"] == 3
    fm = c.to_front_matter()
    assert fm["type"] == "Persona Trait"
    assert fm["kw"]["persona"]["dimension"] == "information_density"
    assert fm["kw"]["security"]["classification"] == "private"


def test_inconsistent_direction_observing_only() -> None:
    sigs = [
        _sig(ref=f"t{i}", direction=1.5, source="text") for i in range(3)
    ] + [
        _sig(ref=f"b{i}", direction=-1.5, source="behavior") for i in range(3)
    ]
    report = aggregate_traits(sigs)
    assert report.candidates == []
    assert report.observing
    assert report.observing[0].reason == "inconsistent_direction"


def test_configurable_thresholds() -> None:
    sigs = [
        _sig(ref="t1", source="text", direction=1.0),
        _sig(ref="b1", source="behavior", direction=1.0),
    ]
    # Default: need 6 → observing
    assert aggregate_traits(sigs).candidates == []
    # Lowered gate
    report = aggregate_traits(
        sigs, config=EvidenceGateConfig(min_evidence=2, min_source_kinds=2)
    )
    assert len(report.candidates) == 1
