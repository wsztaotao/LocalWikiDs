"""Tests for localwiki.llmwiki.query (LW-06)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.llmwiki.query import (  # noqa: E402
    MemoryHit,
    StyleDirective,
    WikiPageSnippet,
    pin_snapshot,
    query_wiki,
    validate_citations,
)


def _pages() -> list[WikiPageSnippet]:
    return [
        WikiPageSnippet(
            page_uid="concepts/python-route",
            title="Python 路线",
            path="concepts/python-route.md",
            summary="项目采用 Python 实现桌面知识系统。",
            body="LocalWiki 采用 Python 路线，而不是 Tauri。证据来自架构决策。",
            page_type="Concept",
            category="concepts",
        ),
        WikiPageSnippet(
            page_uid="concepts/tauri-route",
            title="Tauri 路线",
            path="concepts/tauri-route.md",
            summary="早期方案考虑过 Tauri 2 + Rust。",
            body="早期设计文档描述了 Tauri 方案，后被放弃。",
            page_type="Concept",
            category="concepts",
        ),
        WikiPageSnippet(
            page_uid="topics/stack",
            title="技术栈对比",
            path="topics/stack.md",
            summary="Python 与 Tauri 的对比。",
            body="综合对比：Python 交付更快；Tauri 更接近原生。",
            page_type="Topic",
            category="topics",
            aliases=["技术对比"],
        ),
    ]


def test_citations_map_to_read_materials() -> None:
    snap = pin_snapshot(_pages(), snapshot_id="snap_test")
    result = query_wiki("对比 Python 与 Tauri 路线", snap)
    assert result.snapshot_id == "snap_test"
    assert result.citations
    assert result.context.read_materials
    issues = validate_citations(result)
    assert issues == []
    read = result.read_material_uids()
    for c in result.citations:
        assert c.material_uid in read


def test_writeback_synthesis_proposal() -> None:
    snap = pin_snapshot(_pages())
    result = query_wiki("请综合对比 Python 与 Tauri", snap)
    assert result.writeback is not None
    assert result.writeback.status == "pending"
    assert result.writeback.page_type == "Topic"
    assert result.writeback.content_kind == "synthesis"
    assert result.writeback.citation_ids


def test_writeback_decision_proposal() -> None:
    snap = pin_snapshot(_pages())
    result = query_wiki("我决定采用 Python 路线", snap)
    assert result.writeback is not None
    assert result.writeback.page_type == "Decision"
    assert result.writeback.content_kind == "decision"
    assert result.writeback.status == "pending"


def test_no_writeback_when_no_new_info() -> None:
    snap = pin_snapshot(
        [
            WikiPageSnippet(
                page_uid="concepts/alpha",
                title="Alpha",
                path="concepts/alpha.md",
                summary="Alpha 是一个概念。",
                body="Alpha 是一个概念。",
            )
        ]
    )
    result = query_wiki("什么是 Alpha", snap)
    assert result.writeback is not None
    assert result.writeback.status == "skipped"
    assert result.writeback.reason in ("no_new_information", "no_citations")


def test_memory_and_style_in_context() -> None:
    snap = pin_snapshot(
        _pages(),
        memories=[MemoryHit("mem1", "用户偏好表格对比", "preference")],
        style_directives=[StyleDirective("brevity", "回答保持简洁")],
    )
    result = query_wiki("对比技术路线", snap)
    kinds = {m.kind for m in result.context.read_materials}
    assert "wiki_page" in kinds
    assert "memory" in kinds or "style" in kinds
    assert validate_citations(result) == []


def test_query_does_not_write_files(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "concepts").mkdir()
    page = wiki / "concepts" / "x.md"
    page.write_text("type: Concept\ntitle: X\n\nbody\n", encoding="utf-8")
    before = page.read_bytes()
    from localwiki.llmwiki.query import load_pages_from_wiki_dir

    pages = load_pages_from_wiki_dir(wiki)
    snap = pin_snapshot(pages)
    query_wiki("什么是 X", snap)
    assert page.read_bytes() == before
