"""R3-C: nightly personal layer + FTS."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.gateway import set_pause  # noqa: E402
from localwiki.runtime.nightly import run_nightly, run_personal_layer  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_personal_layer_d9_auto_accept(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    out = run_personal_layer(ws)
    assert out["ok"]
    assert (ws / "wiki" / "memories" / "_nightly.md").is_file()
    assert (ws / "wiki" / "experiences" / "_nightly.md").is_file()
    assert (ws / "wiki" / "profiles" / "style" / "language.md").is_file()


def test_nightly_full_with_pause(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    set_pause(ws, True)
    report = run_nightly(ws)
    assert report["personal"]["ok"]
    assert report["evolution"]["paused"] is True
    assert report["fts"]["ok"] is True
