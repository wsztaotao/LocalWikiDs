"""Tests for localwiki.llmwiki.ingest (LW-05)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.llmwiki.ingest import (  # noqa: E402
    ExistingPage,
    SourceDocument,
    clear_ingest_cache,
    ingest_source,
)


SAMPLE_SELF = """
关于 LocalWiki 项目。

我决定采用 Python 路线而不是 Tauri。

以后回答请简洁一点，方案比较优先用表格，不要长段落。

发布前必须先跑全量回归，再打包安装程序。
"""


def test_ingest_produces_four_queues() -> None:
    clear_ingest_cache()
    source = SourceDocument(
        text=SAMPLE_SELF,
        title="LocalWiki",
        speaker_role="self",
    )
    result = ingest_source(source)
    assert result.reused is False
    assert result.schema_version == "0.12.0"
    assert len(result.knowledge.plan.operations) >= 1
    assert result.knowledge.changeset_status == "draft"
    assert len(result.memory_candidates) >= 1
    assert len(result.experience_candidates) >= 1
    assert len(result.style_evidence) >= 1
    # Prefer update when matching existing page
    existing = [
        ExistingPage(
            page_uid="wiki/concepts/localwiki",
            title="LocalWiki",
            page_type="Concept",
            aliases=["LocalWiki 项目"],
            content_hash="abc",
            claims=["LocalWiki 是知识系统"],
        )
    ]
    clear_ingest_cache()
    result2 = ingest_source(source, existing_pages=existing)
    ops = result2.knowledge.plan.operations
    assert any(o.op in ("revise", "conflict") for o in ops)


def test_unknown_attribution_no_user_experience() -> None:
    clear_ingest_cache()
    source = SourceDocument(
        text="发布前必须先跑全量回归。\n\n步骤一：测试。\n步骤二：打包。",
        speaker_role="unknown",
    )
    result = ingest_source(source)
    assert result.experience_candidates == []
    assert result.memory_candidates == []
    assert result.style_evidence == []
    assert len(result.knowledge.plan.operations) >= 1


def test_other_attribution_no_style_or_user_memory() -> None:
    clear_ingest_cache()
    source = SourceDocument(
        text="第三方说：以后回答请简洁一点，喜欢用表格。",
        speaker_role="other",
    )
    result = ingest_source(source)
    assert result.memory_candidates == []
    assert result.style_evidence == []
    assert result.experience_candidates == []


def test_compile_signature_reuse() -> None:
    clear_ingest_cache()
    source = SourceDocument(text=SAMPLE_SELF, title="LocalWiki", speaker_role="self")
    a = ingest_source(source)
    b = ingest_source(source)
    assert a.compile_signature == b.compile_signature
    assert b.reused is True


def test_conflict_retained_both() -> None:
    clear_ingest_cache()
    existing = [
        ExistingPage(
            page_uid="wiki/concepts/route",
            title="技术路线",
            page_type="Concept",
            content_hash="h1",
            claims=["系统采用 Tauri 路线"],
        )
    ]
    source = SourceDocument(
        text="关于技术路线。\n\n并非采用 Tauri，我决定采用 Python 路线。",
        title="技术路线",
        speaker_role="self",
    )
    result = ingest_source(source, existing_pages=existing)
    assert result.knowledge.plan.conflicts
    assert all(c.resolution == "retain_both" for c in result.knowledge.plan.conflicts)


def test_ingest_does_not_write_wiki(tmp_path: Path) -> None:
    clear_ingest_cache()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    before = list(wiki.rglob("*"))
    source = SourceDocument(text=SAMPLE_SELF, speaker_role="self")
    ingest_source(source, workspace=tmp_path)
    after = list(wiki.rglob("*"))
    assert before == after
