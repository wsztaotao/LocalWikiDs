"""R3.8 batch review."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_batch_accept_reject(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    svc = CommitService(ws)
    a = svc.create_changeset(
        purpose="a",
        ops=[{"op": "create", "path": "t_a.md", "content": "a\n"}],
        state="ready",
    )
    b = svc.create_changeset(
        purpose="b",
        ops=[{"op": "create", "path": "t_b.md", "content": "b\n"}],
        state="ready",
    )
    results = []
    for uid, action in ((a.change_uid, "accept"), (b.change_uid, "reject")):
        if action == "accept":
            cs = svc.accept(uid)
        else:
            cs = svc.reject(uid)
        results.append(cs.state)
    assert results == ["accepted", "rejected"]
    assert (ws / "wiki" / "t_a.md").is_file()
    assert not (ws / "wiki" / "t_b.md").exists()
