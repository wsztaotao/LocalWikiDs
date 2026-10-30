"""Tests for experience feedback loop (LW-20 / §9.8)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.experience.feedback import (  # noqa: E402
    WorkFeedback,
    compute_edit_metrics,
    process_feedback,
)
from localwiki.experience.pack import (  # noqa: E402
    ExperiencePack,
    SelectedEntry,
)


def _pack(*uids: str) -> ExperiencePack:
    return ExperiencePack(
        pack_uid="xp_test",
        task_uid="task_fb",
        selected=[
            SelectedEntry(
                uid=u,
                version_sha=f"sha_{u}",
                kind="rule",
                why="matched",
                grade="multiple_independent_cases",
            )
            for u in uids
        ],
    )


def test_revision_generates_change_observation() -> None:
    before = "第一章\n概述很长很长的段落。\n第二章\n细节。"
    after = "第一章\n概述（精简）。\n第二章\n细节与验收。"
    fb = WorkFeedback(
        feedback_uid="wfb_1",
        task_uid="task_fb",
        artifact_version="v1",
        rating=3,
        delivered_text=before,
        revised_text=after,
    )
    result = process_feedback(fb, pack=_pack("exp_a"))
    assert result.observation is not None
    assert result.observation.motive_inferred is False
    assert result.observation.edit_ratio is not None
    assert result.observation.edit_ratio > 0
    assert result.observation.metrics["len_before"] == len(before)
    assert result.observation.metrics["len_after"] == len(after)
    assert "hunk_count" in result.observation.metrics
    assert result.observation.task_uid == "task_fb"


def test_accept_small_edit_transfer_not_proof_for_all() -> None:
    before = "hello world document content here"
    after = "hello world document content here!"  # tiny change
    metrics = compute_edit_metrics(before, after)
    assert metrics["edit_ratio"] <= 0.15
    fb = WorkFeedback(
        feedback_uid="wfb_2",
        task_uid="task_fb",
        artifact_version="v1",
        rating=5,
        accepted=True,
        delivered_text=before,
        revised_text=after,
    )
    result = process_feedback(fb, pack=_pack("exp_a", "exp_b"))
    assert len(result.transfers) == 2
    assert all(t.note == "one_success_not_proof_for_all" for t in result.transfers)
    xfers = [c for c in result.candidates if c.kind == "transfer_evidence"]
    assert len(xfers) == 2
    assert all(c.metadata["one_success_not_proof_for_all"] for c in xfers)


def test_heavy_edit_revision_and_counterexample_on_error() -> None:
    before = "A" * 500
    after = "B" * 500  # fully different → heavy
    fb = WorkFeedback(
        feedback_uid="wfb_3",
        task_uid="task_fb",
        artifact_version="v1",
        rating=1,
        text="结构不对，缺少验收条件",
        category="error",
        delivered_text=before,
        revised_text=after,
    )
    result = process_feedback(fb, pack=_pack("exp_delivery_rule"))
    assert result.observation is not None
    assert result.observation.edit_ratio is not None
    assert result.observation.edit_ratio >= 0.35
    assert any(c.signal == "heavy_edit" for c in result.candidates)
    assert any(c.kind == "counterexample" for c in result.candidates)
    assert "exp_delivery_rule" in result.linked_experience_uids


def test_preference_text_yields_memory_candidate() -> None:
    fb = WorkFeedback(
        feedback_uid="wfb_4",
        task_uid="task_fb",
        artifact_version="v1",
        text="希望技术设计保持精简，少用长段落",
        category="preference",
        delivered_text="doc",
        revised_text=None,
    )
    result = process_feedback(fb, pack=_pack("exp_a"))
    assert result.memory_candidates
    assert result.memory_candidates[0]["kind"] == "preference"
    assert result.memory_candidates[0]["confirmation_state"] == "proposed"
    assert result.memory_candidates[0]["risk_tier"] == "T1"


def test_compute_edit_metrics_identical() -> None:
    m = compute_edit_metrics("same", "same")
    assert m["edit_ratio"] == 0.0
    assert m["lines_added"] == 0
