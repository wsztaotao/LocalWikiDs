"""R3-A: native workspace tools."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService  # noqa: E402
from localwiki.tools.native_workspace import make_handlers  # noqa: E402
from localwiki.tools.registry import build_default_registry  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_read_list_and_escape(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    (ws / "note.txt").write_text("hello", encoding="utf-8")
    h = make_handlers(ws)
    listed = h["workspace_list"]({"path": "."})
    assert listed["ok"]
    assert any(i["path"] == "note.txt" for i in listed["items"])
    rd = h["workspace_read"]({"path": "note.txt"})
    assert rd["content"] == "hello"
    bad = h["workspace_read"]({"path": "../outside"})
    assert bad["ok"] is False


def test_write_creates_changeset_not_file(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    h = make_handlers(ws)
    out = h["workspace_write"](
        {"path": "topics/t.md", "content": "# T\n\nbody\n", "purpose": "test"}
    )
    assert out["ok"] is True
    assert out["state"] == "ready"
    assert out.get("auto_accepted") is False
    assert not (ws / "wiki" / "topics" / "t.md").exists()
    # accept manually
    CommitService(ws).accept(out["change_uid"])
    assert (ws / "wiki" / "topics" / "t.md").exists()


def test_registry_attach(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    (ws / "wiki" / "a.md").write_text("# A\nfindme\n", encoding="utf-8")
    reg = build_default_registry(workspace=ws)
    r = reg.execute("workspace_read", {"path": "wiki/index.md"})
    # path is workspace-relative; index under wiki/
    r2 = reg.execute("knowledge_read", {"path": "index.md"})
    assert r2.ok is True
