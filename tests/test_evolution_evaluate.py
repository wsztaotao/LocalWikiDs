"""LW-44 / AT-EVO-06: replay without external writes; baseline invisible to proposals."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.evaluate import (  # noqa: E402
    EVAL_DOMAINS,
    ROLE_JUDGE,
    ROLE_PROPOSAL,
    BaselineRunner,
    BaselineVisibilityGuard,
    OfflineEvaluator,
    RecordedToolCall,
    ReplayHarness,
    Trajectory,
    ensure_min_suite_sizes,
    is_external_write_tool,
)


EVALS = ROOT / "evals"


def test_each_domain_has_at_least_20_tasks() -> None:
    counts = ensure_min_suite_sizes(EVALS, minimum=20)
    assert set(counts) == set(EVAL_DOMAINS)
    assert all(n >= 20 for n in counts.values())


def test_baseline_invisible_to_proposal_model() -> None:
    guard = BaselineVisibilityGuard(EVALS)
    with pytest.raises(PermissionError, match="invisible to proposal"):
        guard.load_suite("wiki_qa", role=ROLE_PROPOSAL)
    with pytest.raises(PermissionError):
        guard.read_text(EVALS / "wiki_qa" / "tasks.jsonl", role=ROLE_PROPOSAL)
    # Judge / replay roles may load
    rows = guard.load_suite("wiki_qa", role=ROLE_JUDGE)
    assert len(rows) >= 20


def test_replay_never_executes_external_writes(tmp_path: Path) -> None:
    traj = Trajectory(
        task_uid="task_replay_1",
        goal="fix docx table width",
        messages=[{"role": "assistant", "content": "created table without widths"}],
        tool_calls=[
            RecordedToolCall(
                name="docx_create",
                arguments={"cols": 3},
                result={"ok": False, "error": "misaligned"},
                is_external_write=True,
            ),
            RecordedToolCall(
                name="composio.gmail.send",
                arguments={"to": "a@b.c"},
                result={"ok": True, "recorded": True},
                is_external_write=True,
            ),
        ],
        final_output="created table without widths",
        pattern_keys=["tools.docx.table_width"],
        score=0.3,
    )
    harness = ReplayHarness()
    # Even if caller asks to execute tools, writes_executed stays 0
    result = harness.replay(
        traj,
        new_rules="- 生成多列表格时必须为每列指定宽度",
        execute_tools=True,
    )
    assert result.external_writes_attempted >= 2
    assert result.external_writes_executed == 0
    assert result.ok is True
    assert result.improved is True


def test_offline_evaluator_pass_and_regression(tmp_path: Path) -> None:
    traj = Trajectory(
        task_uid="task_ev",
        goal="docx widths",
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
        pattern_keys=["tools.docx.table_width"],
    )
    ev = OfflineEvaluator(tmp_path, evals_root=EVALS)
    report = ev.evaluate_proposal(
        {
            "proposal_uid": "imp_test",
            "diff": "+ - 生成多列表格时必须为每列指定宽度",
        },
        [traj],
    )
    assert report.passed
    assert all(d.n_tasks >= 20 for d in report.domains)
    assert all(d.regression <= 0.01 for d in report.domains)

    # Inject degrading candidate → fail on regression
    def good(t: dict) -> str:
        return str(t.get("canonical_answer") or t.get("expected_contains") or "ok")

    def bad(t: dict) -> str:
        return "WRONG"

    report2 = ev.evaluate_proposal(
        {"proposal_uid": "imp_bad", "diff": "+ noop"},
        [traj],
        baseline_fn=good,
        candidate_fn=bad,
    )
    assert report2.passed is False
    assert report2.reason == "baseline_regression"


def test_is_external_write_tool_helpers() -> None:
    assert is_external_write_tool("docx_create")
    assert is_external_write_tool("composio.slack.post")
    assert not is_external_write_tool("wiki_query")
    assert not is_external_write_tool("learning_search")


def test_baseline_runner_oracle_scores(tmp_path: Path) -> None:
    runner = BaselineRunner(EVALS)
    for domain in EVAL_DOMAINS:
        score = runner.run_domain(domain, role=ROLE_JUDGE)
        assert score.n_tasks >= 20
        assert score.baseline_mean == 1.0
