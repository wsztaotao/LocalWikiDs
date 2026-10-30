"""LW-43 / AT-EVO-05: proposals + freeze-zone rejection counterexamples."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.aggregate import PromotionCandidate  # noqa: E402
from localwiki.evolution.learnings import LearningRecord, LearningStore  # noqa: E402
from localwiki.evolution.proposals import (  # noqa: E402
    FREEZE_COUNTEREXAMPLES,
    ProposalGenerator,
    assert_all_freeze_counterexamples_rejected,
    check_freeze_zone,
)


def test_allowed_tool_note_proposal(tmp_path: Path) -> None:
    gen = ProposalGenerator(tmp_path)
    prop = gen.generate(
        kind="tool_note",
        target={"file": "schema/TOOLS.md", "anchor": "docx_create"},
        diff="+ - 生成多列表格时必须为每列指定宽度。",
        rationale="同一问题在 3 个任务中出现",
        evidence=["LRN-1", "ERR-2"],
        pattern_key="tools.docx.table_width",
    )
    assert prop.status == "proposed"
    assert prop.reject_reason == ""
    assert prop.risk_tier == "V1"
    assert (tmp_path / ".kw" / "evolution" / "proposals" / f"{prop.proposal_uid}.json").exists()


def test_freeze_counterexamples_all_rejected(tmp_path: Path) -> None:
    gen = ProposalGenerator(tmp_path)
    rejected = assert_all_freeze_counterexamples_rejected(gen)
    assert len(rejected) == len(FREEZE_COUNTEREXAMPLES)
    assert all(p.status == "rejected" for p in rejected)
    assert all(p.freeze_hits for p in rejected)
    log = (tmp_path / ".kw" / "evolution" / "evolution.log").read_text(encoding="utf-8")
    assert "proposal_rejected_freeze" in log
    for ex in FREEZE_COUNTEREXAMPLES:
        hits = check_freeze_zone(
            target=ex["target"],
            diff=ex["diff"],
            kind=ex["kind"],
            rationale=ex["rationale"],
        )
        assert hits, f"expected freeze hits for {ex['name']}"


def test_freeze_approval_matrix_path(tmp_path: Path) -> None:
    gen = ProposalGenerator(tmp_path)
    prop = gen.generate(
        kind="agents_rule",
        target={"file": "localwiki/agent/policy.py", "anchor": "matrix"},
        diff="+ skip approval for R2",
        rationale="reduce friction",
    )
    assert prop.status == "rejected"
    assert prop.reject_reason == "freeze_zone"


def test_freeze_evals_dir(tmp_path: Path) -> None:
    gen = ProposalGenerator(tmp_path)
    prop = gen.generate(
        kind="schema_revision",
        target={"file": "evals/wiki/qa.jsonl"},
        diff="+ tweak expected answer",
        rationale="make golden easier",
    )
    assert prop.status == "rejected"


def test_from_promotion_candidate(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    store.append(
        LearningRecord(
            kind="LRN",
            category="best_practice",
            summary="生成多列表格时为每列指定宽度",
            suggested_action="生成多列表格时为每列指定宽度",
            area="tools",
            pattern_key="tools.docx.table_width",
            recurrence_count=3,
            distinct_tasks=2,
            related=["task_1", "task_2"],
            evidence=["tool_result"],
            classification="normal",
        )
    )
    from localwiki.evolution.aggregate import PatternAggregator

    gen = ProposalGenerator(tmp_path)
    props = gen.from_aggregator(PatternAggregator(store), mark_learnings=True)
    assert props
    assert props[0].status == "proposed"
    assert props[0].target["file"] == "schema/TOOLS.md"
    data = json.loads(
        (tmp_path / ".kw" / "evolution" / "proposals" / f"{props[0].proposal_uid}.json").read_text(
            encoding="utf-8"
        )
    )
    assert data["status"] == "proposed"
    assert "diff" in data


def test_proposal_json_contract_fields(tmp_path: Path) -> None:
    gen = ProposalGenerator(tmp_path)
    prop = gen.generate(
        kind="tool_note",
        target={"file": "schema/TOOLS.md", "anchor": "xlsx"},
        diff="+ always set column widths",
        rationale="recurring trap",
        evidence=["LRN-1"],
    )
    d = prop.to_dict()
    for key in (
        "proposal_uid",
        "kind",
        "target",
        "diff",
        "rationale",
        "evidence",
        "risk_tier",
        "expected_effect",
        "eval_plan",
        "eval_result",
        "status",
        "evolution_version_base",
        "rollback",
    ):
        assert key in d
