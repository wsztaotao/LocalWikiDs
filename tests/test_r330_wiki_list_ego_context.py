"""R3.30: page delete/title, ego graph, list dir_category."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService, file_sha256  # noqa: E402
from localwiki.wiki_graph import build_wiki_graph  # noqa: E402
from localwiki.wiki_page_meta import (  # noqa: E402
    delete_block_reason,
    set_title_in_content,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def _page(title: str, body: str) -> str:
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"Topic\"\n"
        f"title: \"{title}\"\n---\n\n# {title}\n\n{body}\n"
    )


def test_delete_block_index_and_secret(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    assert delete_block_reason(ws, "index.md")
    assert delete_block_reason(ws, "log.md")
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    (wiki / "sec.md").write_text(
        "---\nokf_version: \"0.2\"\ntitle: \"S\"\nsecret: true\n---\n\n# S\n",
        encoding="utf-8",
    )
    assert delete_block_reason(ws, "topics/sec.md")
    (wiki / "ok.md").write_text(_page("可删", "body"), encoding="utf-8")
    assert delete_block_reason(ws, "topics/ok.md") is None


def test_delete_and_title_via_commit(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    path = "topics/t.md"
    (wiki / "t.md").write_text(_page("原名", "见 [[topics/u.md]]"), encoding="utf-8")
    (wiki / "u.md").write_text(_page("邻", "回 [[topics/t.md]]"), encoding="utf-8")
    svc = CommitService(ws)
    pages = svc.list_pages()
    assert any(p["path"] == path and p.get("dir_category") == "topics" for p in pages)

    raw = (ws / "wiki" / path).read_text(encoding="utf-8")
    new_c = set_title_in_content(raw, "新标题")
    assert 'title: "新标题"' in new_c
    cs = svc.create_changeset(
        purpose="title",
        ops=[
            {
                "op": "update",
                "path": path,
                "content": new_c,
                "base_sha256": file_sha256(ws / "wiki" / path),
            }
        ],
        state="ready",
    )
    cs = svc.accept(cs.change_uid)
    assert cs.state == "accepted"
    assert "新标题" in (ws / "wiki" / path).read_text(encoding="utf-8")

    cs2 = svc.create_changeset(
        purpose="del",
        ops=[
            {
                "op": "delete",
                "path": path,
                "base_sha256": file_sha256(ws / "wiki" / path),
            }
        ],
        state="ready",
    )
    cs2 = svc.accept(cs2.change_uid)
    assert cs2.state == "accepted"
    assert not (ws / "wiki" / path).exists()


def test_ego_one_hop(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    (wiki / "a.md").write_text(_page("A", "[[topics/b.md]]"), encoding="utf-8")
    (wiki / "b.md").write_text(_page("B", "[[topics/a.md]]"), encoding="utf-8")
    (wiki / "c.md").write_text(_page("C", "孤岛"), encoding="utf-8")
    g = build_wiki_graph(ws, view="ego", focus="topics/a.md", weak=True)
    assert g["view"] == "ego"
    paths = {n["path"] for n in g["nodes"]}
    assert paths == {"topics/a.md", "topics/b.md"}
    assert "topics/c.md" not in paths
    assert all(e.get("kind") != "weak" for e in g["edges"])
    assert any(n.get("focus") for n in g["nodes"] if n["path"] == "topics/a.md")


def test_html_has_wiki_list_controls() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="page-type-filter"' in html
    assert 'id="wiki-ego-cy"' in html
    assert 'id="graph-ctx-menu"' in html
    assert 'id="graph-title-input"' in html
