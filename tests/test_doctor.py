"""R1: doctor + workspace lock tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.diagnostics import doctor  # noqa: E402
from localwiki.single_instance import WorkspaceLock  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_doctor_ok_on_fresh_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    report = doctor(ws)
    assert report["ok"] is True
    assert report["snapshot_id"]
    assert report["config"]["has_token"] is True
    ids = {c["id"] for c in report["checks"]}
    assert "head" in ids
    assert "dir_wiki" in ids


def test_lock_mutex_other_pid(tmp_path: Path) -> None:
    """Foreign alive PID must block acquire (same-process is reentrant by design)."""
    ws = tmp_path / "ws"
    paths = ensure_workspace(ws)
    # PID 4 (Windows System) / 1 (init) are almost always alive and not us
    foreign = 4 if os.name == "nt" else 1
    assert foreign != os.getpid()
    paths["lock"].write_text(
        json.dumps({"pid": foreign, "held_at": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    b = WorkspaceLock(paths["lock"])
    assert b.acquire(blocking=False) is False


def test_lock_reentrant_same_process(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    paths = ensure_workspace(ws)
    a = WorkspaceLock(paths["lock"])
    b = WorkspaceLock(paths["lock"])
    assert a.acquire(blocking=False)
    assert b.acquire(blocking=False)  # nested
    b.release()
    assert a.info() is not None  # still held
    a.release()
    assert a.info() is None
