"""E2E smoke §21.4 scenario 7 (self-evolution loop) — offline, no live models."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.aggregate import PatternAggregator  # noqa: E402
from localwiki.evolution.apply import EvolutionApplier  # noqa: E402
from localwiki.evolution.evaluate import (  # noqa: E402
    OfflineEvaluator,
    RecordedToolCall,
    Trajectory,
)
from localwiki.evolution.learnings import LearningRecord, LearningStore  # noqa: E402
from localwiki.evolution.proposals import ProposalGenerator  # noqa: E402


def test_evolution_closed_loop_smoke(tmp_path: Path) -> None:
    """3× same Pattern-Key → proposal → offline eval → user approve → regress → rollback."""
    store = LearningStore(tmp_path)
    pk = "tools.docx.table_width"
    for i in range(3):
        store.append(
            LearningRecord(
                kind="ERR",
                category="error",
                summary="docx_create 多列表格错位",
                suggested_action="生成多列表格时必须为每列指定宽度",
                area="tools",
                pattern_key=pk,
                related=[f"task_{i+1}"],
                evidence=["tool_result"],
                classification="normal",
                priority="medium",
            )
        )
    # Force counters if bump path coalesced
    entries = [e for e in store.list_records("ERR") if e["pattern_key"] == pk]
    assert entries
    assert int(entries[0]["recurrence_count"]) >= 3

    gen = ProposalGenerator(tmp_path)
    props = gen.from_aggregator(PatternAggregator(store), mark_learnings=True)
    assert props and props[0].status == "proposed"

    traj = Trajectory(
        task_uid="task_4",
        goal="docx table",
        messages=[{"role": "assistant", "content": "bad table"}],
        tool_calls=[
            RecordedToolCall(
                name="docx_create",
                arguments={},
                result={"ok": False},
                is_external_write=True,
            )
        ],
        score=0.3,
        pattern_keys=[pk],
    )
    report = OfflineEvaluator(tmp_path, evals_root=ROOT / "evals").evaluate_proposal(
        props[0], [traj]
    )
    assert report.passed
    assert all(r.external_writes_executed == 0 for r in report.replay)

    app = EvolutionApplier(tmp_path)
    app.set_level("E1")
    (tmp_path / "schema").mkdir(parents=True, exist_ok=True)
    (tmp_path / "schema" / "TOOLS.md").write_text("# TOOLS\n", encoding="utf-8")
    props[0].status = "passed"
    ver = app.apply_with_snapshots(props[0], eval_passed=True, user_confirmed=True)
    assert ver is not None

    # Induce regression under E2 auto path for rollback demo
    app.set_level("E2")
    prop2 = ProposalGenerator(tmp_path).generate(
        kind="tool_note",
        target={"file": "schema/TOOLS.md", "anchor": "docx_create"},
        diff="+ - extra note for rollback demo",
        rationale="demo",
        evidence=["e2e"],
        pattern_key="tools.docx.rollback_demo",
    )
    ver2 = app.apply_with_snapshots(
        prop2,
        eval_passed=True,
        metrics_baseline={"task_success": 1.0, "tool_error_rate": 0.0},
    )
    assert ver2 and ver2.auto_applied
    rb = app.observe_task(metrics={"task_success": 0.5, "tool_error_rate": 0.3})
    assert rb is not None
    assert rb.rolled_back_from == ver2.version
