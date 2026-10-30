"""Tests for persona style signals (LW-21 / §10.4, §10.7)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.persona.signals import (  # noqa: E402
    FORBIDDEN_TOPICS,
    WORK_STYLE_DIMENSIONS,
    extract_from_text,
    extract_style_signals,
    hits_forbidden,
)


def test_dimensions_cover_design() -> None:
    assert len(WORK_STYLE_DIMENSIONS) == 12
    assert "information_density" in WORK_STYLE_DIMENSIONS
    assert "third_party_personality" in FORBIDDEN_TOPICS


def test_self_text_produces_signals() -> None:
    report = extract_from_text(
        "我喜欢简短的回答，请用表格对比方案。",
        speaker_role="self",
        material_role="user_authored",
        evidence_ref="msg:m1",
    )
    assert report.kept()
    dims = {s.dimension for s in report.kept()}
    assert "information_density" in dims
    assert "structure_preference" in dims
    assert any(s.source == "direct_statement" for s in report.kept())


def test_third_party_produces_no_signals() -> None:
    report = extract_from_text(
        "他喜欢简短的回答，请用表格。同事总是直接说结论。",
        speaker_role="other",
        material_role="reference_only",
        evidence_ref="msg:other1",
    )
    assert report.kept() == []
    assert any("skipped_non_self" in i for i in report.issues)


def test_unknown_and_ai_produce_no_signals() -> None:
    for role in ("unknown", "ai", "system"):
        r = extract_from_text(
            "我喜欢简洁要点式表达。",
            speaker_role=role,
            evidence_ref=f"msg:{role}",
        )
        assert r.kept() == [], role


def test_user_authored_material_without_speaker_ok() -> None:
    report = extract_from_text(
        "文档应保持简洁，多用列表。",
        speaker_role="unknown",
        material_role="user_authored",
        evidence_ref="src:doc1",
    )
    assert report.kept()


def test_forbidden_topic_discarded() -> None:
    assert hits_forbidden("讨论宗教信仰问题") == "religion"
    report = extract_from_text(
        "我的宗教信仰影响写作风格，我喜欢简洁。",
        speaker_role="self",
        evidence_ref="msg:forbid",
    )
    # Whole blob hit forbidden — no kept signals
    assert report.kept() == []
    assert report.discarded


def test_third_party_personality_forbidden_in_self_text() -> None:
    # Self text evaluating someone else's personality — discard
    report = extract_from_text(
        "他性格内向，所以方案写得很保守。我喜欢简洁。",
        speaker_role="self",
        evidence_ref="msg:tp",
    )
    # May discard whole or skip forbidden excerpts; must not keep third-party personality signal
    for s in report.kept():
        assert "性格" not in s.excerpt or hits_forbidden(s.excerpt) is None


def test_unified_extract_skips_other_in_batch() -> None:
    report = extract_style_signals(
        texts=[
            {
                "text": "请详细展开背景。",
                "speaker_role": "other",
                "evidence_ref": "m_other",
            },
            {
                "text": "请简短点，直接说结论。",
                "speaker_role": "self",
                "evidence_ref": "m_self",
            },
        ]
    )
    assert report.kept()
    assert all(s.speaker_role == "self" for s in report.kept())
    assert any("skipped_non_self" in i for i in report.issues)


def test_behavior_and_trace_self_only() -> None:
    report = extract_style_signals(
        behavior_events=[
            {
                "ev_uid": "ev1",
                "cat": "review",
                "kind": "proposal_edited",
                "metrics": {"len_before": 2000, "len_after": 1000},
            }
        ],
        agent_sessions=[
            [
                {
                    "event": "user_steer",
                    "session_uid": "s1",
                    "payload": {"steer": "步骤细一点"},
                },
                {
                    "event": "assistant_message",
                    "speaker_role": "ai",
                    "payload": {"text": "我喜欢冗长铺垫"},
                },
            ]
        ],
    )
    assert any(s.source == "behavior" for s in report.kept())
    assert any(s.source == "agent_trace" for s in report.kept())
    # AI message must not contribute
    assert not any("冗长铺垫" in s.excerpt for s in report.kept())
