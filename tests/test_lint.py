"""Tests for localwiki.llmwiki.lint (LW-07) — one sample per §5.6 check class."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.llmwiki.lint import (  # noqa: E402
    WikiPage,
    run_lint,
)
from localwiki.llmwiki.schema import load_default_schema  # noqa: E402


def _run(pages: list[WikiPage], **kwargs):
    return run_lint(pages, schema=load_default_schema(), **kwargs)


def test_all_nine_checks_have_findings() -> None:
    """Fixture wiki engineered so each of the 9 checks fires at least once."""
    pages = [
        WikiPage(
            page_uid="concepts/alpha",
            path="concepts/alpha.md",
            title="Alpha",
            page_type="Concept",
            body="## 定义\nAlpha。\n\n提到 BetaEngine 与 BetaEngine。\n\n[坏链](concepts/missing.md)\n\n[^1]\n",
            raw="type: Concept\ntitle: Alpha\n",
            aliases=["Alpha"],
        ),
        WikiPage(
            page_uid="concepts/beta-dup",
            path="concepts/beta-dup.md",
            title="Alpha",  # duplicate title
            page_type="Concept",
            body="## 定义\n重复标题页。\n冲突：与旧结论矛盾。\n依赖失效。\n",
            raw="type: Concept\ntitle: Alpha\nconflicts: [c1]\n",
            marked_conflicts=["c1"],
        ),
        WikiPage(
            page_uid="concepts/orphan",
            path="concepts/orphan.md",
            title="Orphan",
            page_type="",  # format: missing type
            body="孤立内容，无入链。",
            raw="title: Orphan\n",
        ),
        WikiPage(
            page_uid="questions/open-q",
            path="questions/open-q.md",
            title="如何扩展插件",
            page_type="Question",
            body="## 问题\n如何扩展？\n\n未解决\n",
            raw="type: Question\ntitle: 如何扩展插件\n",
        ),
        WikiPage(
            page_uid="concepts/stale",
            path="concepts/stale.md",
            title="Stale Topic",
            page_type="Topic",
            body="## 综合\nx\n## 一致点\ny\n## 分歧\nz\n",
            raw="type: Topic\nkw:\n  stale_after: '2020-01-01'\n",
            stale_after="2020-01-01",
        ),
        WikiPage(
            page_uid="templates/exp",
            path="templates/exp.md",
            title="模板",
            page_type="Topic",
            body="## 综合\na\n## 一致点\nb\n## 分歧\nc\n已停用经验仍被引用。\n记忆与结论冲突。\n",
            raw="type: Topic\nexperience:disabled\nmemory_conflict: true\n",
            disabled_experience_refs=["exp_old"],
        ),
    ]
    # Wire a link from alpha to beta so beta not orphan; orphan stays orphan.
    pages[0].body += "\n[到重复](concepts/beta-dup.md)\n"
    from localwiki.llmwiki.lint import _wire_links

    _wire_links(pages)

    report = _run(pages, today=date(2026, 10, 12))
    assert report.auto_modified_pages == []
    by = report.by_check
    required = [
        "format",
        "broken_links",
        "orphan_pages",
        "missing_concepts",
        "contradictions",
        "stale_content",
        "duplicates",
        "source_gaps",
        "memory_experience_consistency",
    ]
    for name in required:
        assert name in by and by[name], f"expected findings for {name}, got {by.get(name)}"


def test_format_missing_type() -> None:
    pages = [
        WikiPage("a", "a.md", "A", "", "body", raw="title: A\n"),
    ]
    findings = [f for f in _run(pages).findings if f.check == "format"]
    assert any("type" in f.message for f in findings)


def test_broken_links_and_footnote() -> None:
    pages = [
        WikiPage(
            "a",
            "a.md",
            "A",
            "Concept",
            "## 定义\n[x](nope.md)\n[^9]\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n",
        )
    ]
    findings = [f for f in _run(pages).findings if f.check == "broken_links"]
    assert any("broken link" in f.message for f in findings)
    assert any("footnote" in f.message for f in findings)


def test_orphan_pages() -> None:
    pages = [
        WikiPage("a", "a.md", "A", "Concept", "## 定义\nx\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n"),
        WikiPage("b", "b.md", "B", "Concept", "## 定义\ny\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n"),
    ]
    findings = [f for f in _run(pages).findings if f.check == "orphan_pages"]
    assert len(findings) >= 2


def test_missing_concepts() -> None:
    pages = [
        WikiPage(
            "a",
            "a.md",
            "A",
            "Concept",
            "## 定义\nWidgetCore and WidgetCore again.\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n",
        )
    ]
    findings = [f for f in _run(pages).findings if f.check == "missing_concepts"]
    assert any("WidgetCore" in f.message for f in findings)


def test_contradictions() -> None:
    pages = [
        WikiPage(
            "a",
            "a.md",
            "A",
            "Concept",
            "## 定义\n冲突标记\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n",
            marked_conflicts=["c1"],
        )
    ]
    findings = [f for f in _run(pages).findings if f.check == "contradictions"]
    assert findings


def test_stale_content() -> None:
    pages = [
        WikiPage(
            "a",
            "a.md",
            "A",
            "Topic",
            "## 综合\na\n## 一致点\nb\n## 分歧\nc\n",
            stale_after="2020-01-01",
        )
    ]
    findings = [
        f
        for f in _run(pages, today=date(2026, 1, 1)).findings
        if f.check == "stale_content"
    ]
    assert findings


def test_duplicates() -> None:
    pages = [
        WikiPage("a", "a.md", "Same", "Concept", "## 定义\nx\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n"),
        WikiPage("b", "b.md", "Other", "Concept", "## 定义\ny\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n", aliases=["Same"]),
    ]
    findings = [f for f in _run(pages).findings if f.check == "duplicates"]
    assert findings


def test_source_gaps() -> None:
    pages = [
        WikiPage(
            "q",
            "q.md",
            "Open",
            "Question",
            "## 问题\n?\n## 背景\nb\n未解决\n",
        )
    ]
    findings = [f for f in _run(pages).findings if f.check == "source_gaps"]
    assert findings


def test_memory_experience_consistency() -> None:
    pages = [
        WikiPage(
            "t",
            "t.md",
            "T",
            "Topic",
            "## 综合\na\n## 一致点\nb\n## 分歧\nc\n已停用经验\n",
            raw="experience:disabled\n",
            disabled_experience_refs=["exp_old"],
        )
    ]
    # put ref in body
    pages[0].body += "\nexp_old\n"
    findings = [
        f for f in _run(pages).findings if f.check == "memory_experience_consistency"
    ]
    assert findings


def test_lint_never_auto_modifies(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "concepts"
    wiki.mkdir(parents=True)
    page = wiki / "x.md"
    page.write_text(
        "---\ntype: Concept\ntitle: X\n---\n## 定义\n[bad](nope.md)\n## 证据\ne\n## 关联\nr\n## 适用条件\nc\n## 未解决问题\nq\n",
        encoding="utf-8",
    )
    before = page.read_bytes()
    from localwiki.llmwiki.lint import lint_workspace

    report = lint_workspace(tmp_path)
    assert page.read_bytes() == before
    assert report.auto_modified_pages == []


def test_web_lint_js_exists() -> None:
    path = ROOT / "web" / "lint.js"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "LocalWikiLint" in text
    assert "broken_links" in text
