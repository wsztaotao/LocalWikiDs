"""R1: CommitService / ChangeSet unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitError, CommitService, E_CONFLICT  # noqa: E402
from localwiki.workspace import ensure_workspace, read_head  # noqa: E402


def test_commit_create_advances_head(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    head0 = read_head(ws)
    svc = CommitService(ws)
    cs = svc.create_changeset(
        "add hello",
        [{"op": "create", "path": "concepts/hello.md", "content": "# Hello\n\nworld\n"}],
    )
    assert cs.state == "ready"
    out = svc.accept(cs.change_uid)
    assert out.state == "accepted"
    assert out.commit_id
    head1 = read_head(ws)
    assert head1 != head0
    page = ws / "wiki" / "concepts" / "hello.md"
    assert page.exists()
    assert "world" in page.read_text(encoding="utf-8")
    assert any(p["path"] == "concepts/hello.md" for p in svc.list_pages())


def test_commit_conflict_base_sha(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    svc = CommitService(ws)
    cs1 = svc.create_changeset(
        "create",
        [{"op": "create", "path": "a.md", "content": "# A\n"}],
    )
    svc.accept(cs1.change_uid)
    # Stale base_sha on update
    cs2 = svc.create_changeset(
        "bad update",
        [
            {
                "op": "update",
                "path": "a.md",
                "content": "# A2\n",
                "base_sha256": "0" * 64,
            }
        ],
    )
    with pytest.raises(CommitError) as ei:
        svc.accept(cs2.change_uid)
    assert ei.value.code == E_CONFLICT
    failed = svc.get_changeset(cs2.change_uid)
    assert failed.state == "conflicted"


def test_recover_and_head_stable(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    svc = CommitService(ws)
    cs = svc.create_changeset(
        "page",
        [{"op": "create", "path": "p.md", "content": "# P\n"}],
    )
    svc.accept(cs.change_uid)
    head = read_head(ws)
    notes = svc.recover_on_startup()
    assert isinstance(notes, list)
    assert read_head(ws) == head
    # New service instance sees same HEAD
    svc2 = CommitService(ws)
    assert read_head(ws) == head
    content = svc2.read_page("p.md")
    assert content["content"].startswith("# P")
