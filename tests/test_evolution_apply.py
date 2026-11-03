"""LW-45 / AT-EVO-07/08: gate matrix, versions, monitor rollback."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.apply import (  # noqa: E402
    GATE_MATRIX,
    EvolutionApplier,
    gate_action,
)
from localwiki.evolution.proposals import ImprovementProposal  # noqa: E402


def _prop(
    *,
    risk: str = "V1",
    file: str = "schema/TOOLS.md",
    anchor: str = "docx",
    uid: str = "imp_gate_1",
    diff: str = "+ - always set column widths",
) -> ImprovementProposal:
    return ImprovementProposal(
        proposal_uid=uid,
        kind="tool_note",
        target={"file": file, "anchor": anchor},
        diff=diff,
        rationale="test",
        evidence=["LRN-1"],
        risk_tier=risk,
        status="passed",
        pattern_key="tools.docx.table_width",
    )


def test_gate_matrix_cells() -> None:
    for (level, risk), expected in GATE_MATRIX.items():
        assert gate_action(level, risk, eval_passed=True) == expected
    # Freeze always reject
    assert gate_action("E1", "V0", freeze_hits=[{"x": 1}]) == "reject"
    assert gate_action("E2", "V1", freeze_hits=[{"x": 1}]) == "reject"
    # E0 rejects apply
    assert gate_action("E0", "V0") == "reject"


def test_e1_requires_confirmation(tmp_path: Path) -> None:
    app = EvolutionApplier(tmp_path)
    app.set_level("E1")
    ver = app.apply(_prop(), eval_passed=True, user_confirmed=False)
    assert ver is None
    ver2 = app.apply_with_snapshots(_prop(uid="imp_e1_ok"), eval_passed=True, user_confirmed=True)
    assert ver2 is not None
    assert ver2.auto_applied is False
    assert (tmp_path / "schema" / "TOOLS.md").exists()


def test_e2_auto_applies_v0_v1_not_v2(tmp_path: Path) -> None:
    app = EvolutionApplier(tmp_path)
    app.set_level("E2")
    v1 = app.apply_with_snapshots(_prop(uid="imp_v1", risk="V1"), eval_passed=True)
    assert v1 is not None and v1.auto_applied is True
    v0 = app.apply_with_snapshots(
        _prop(uid="imp_v0", risk="V0", file="schema/params_notes.md", anchor="rrf"),
        eval_passed=True,
    )
    assert v0 is not None and v0.auto_applied is True
    v2 = app.apply(
        _prop(uid="imp_v2", risk="V2", file="schema/AGENTS.md", anchor="learned"),
        eval_passed=True,
        user_confirmed=False,
    )
    assert v2 is None  # awaiting user


def test_freeze_proposal_rejected(tmp_path: Path) -> None:
    app = EvolutionApplier(tmp_path)
    app.set_level("E2")
    bad = _prop(
        uid="imp_freeze",
        file="localwiki/agent/policy.py",
        diff="+ auto approve R3",
    )
    bad.kind = "approval_matrix_change"
    bad.rationale = "放宽审批矩阵"
    assert app.apply(bad, eval_passed=True) is None


def test_monitor_regression_auto_rollback(tmp_path: Path) -> None:
    app = EvolutionApplier(tmp_path)
    app.set_level("E2")
    tools = tmp_path / "schema" / "TOOLS.md"
    tools.parent.mkdir(parents=True)
    tools.write_text("# TOOLS\n\noriginal\n", encoding="utf-8")
    ver = app.apply_with_snapshots(
        _prop(uid="imp_rb"),
        eval_passed=True,
        metrics_baseline={"task_success": 1.0, "tool_error_rate": 0.0},
    )
    assert ver is not None
    assert "column widths" in tools.read_text(encoding="utf-8") or "widths" in tools.read_text(
        encoding="utf-8"
    )
    # Induce regression
    rb = app.observe_task(metrics={"task_success": 0.7, "tool_error_rate": 0.2}, related=True)
    assert rb is not None
    assert rb.rolled_back_from == ver.version
    text = tools.read_text(encoding="utf-8")
    assert "original" in text
    # Same improvement should be blocked if re-applied
    again = app.apply_with_snapshots(_prop(uid="imp_rb"), eval_passed=True)
    assert again is None


def test_oscillation_freeze(tmp_path: Path) -> None:
    app = EvolutionApplier(tmp_path)
    app.set_level("E2")
    for i in range(3):
        app.apply_with_snapshots(
            _prop(uid=f"imp_osc_{i}", anchor="same"),
            eval_passed=True,
            user_confirmed=False,
        )
    assert app.registry.is_target_frozen("schema/TOOLS.md#same")
    blocked = app.apply(_prop(uid="imp_osc_4", anchor="same"), eval_passed=True)
    assert blocked is None


def test_versions_recorded(tmp_path: Path) -> None:
    app = EvolutionApplier(tmp_path)
    app.set_level("E2")
    ver = app.apply_with_snapshots(_prop(uid="imp_ver"), eval_passed=True)
    assert ver is not None
    versions = app.registry.list_versions()
    assert any(v["version"] == ver.version for v in versions)
    assert versions[-1]["changes"][0]["before_hash"]
