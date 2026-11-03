"""R3-C: evolution pause / freeze / auto-apply."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.gateway import (  # noqa: E402
    is_paused,
    run_auto_evolution,
    set_pause,
    status,
)
from localwiki.evolution.proposals import ImprovementProposal, ProposalStore  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_pause_flag(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    assert is_paused(ws) is False
    set_pause(ws, True)
    assert is_paused(ws) is True
    st = status(ws)
    assert st["pause"] is True


def test_freeze_rejects(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = ProposalStore(ws)
    prop = ImprovementProposal(
        proposal_uid="imp_freeze_test",
        kind="rule",
        target={"file": "localwiki/core.py", "anchor": "x"},
        diff="+ never",
        rationale="touch freeze",
        risk_tier="V0",
        status="proposed",
    )
    store.save(prop)
    out = run_auto_evolution(ws)
    assert any(r.get("reason") == "freeze_zone" for r in out["rejected"])


def test_auto_apply_v0(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    set_pause(ws, False)
    store = ProposalStore(ws)
    # Target a non-freeze wiki schema note
    target_file = "schema/TOOLS.md"
    (ws / "schema").mkdir(parents=True, exist_ok=True)
    (ws / target_file).write_text("# Tools\n", encoding="utf-8")
    prop = ImprovementProposal(
        proposal_uid="imp_auto_v0",
        kind="rule",
        target={"file": target_file, "anchor": "tools"},
        diff="+ Prefer concise answers",
        rationale="style tip",
        risk_tier="V0",
        status="proposed",
    )
    store.save(prop)
    out = run_auto_evolution(ws)
    assert out["paused"] is False
    # Either applied or rejected for non-freeze reasons — must not freeze
    assert not any(r.get("reason") == "freeze_zone" for r in out["rejected"])
