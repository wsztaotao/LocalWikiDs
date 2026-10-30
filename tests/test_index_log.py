"""Tests for localwiki.llmwiki.index_log (LW-08)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.llmwiki.index_log import (  # noqa: E402
    CommitEvent,
    IndexEntry,
    IndexLogWriter,
    filter_log_events,
    render_log,
    render_root_index,
)


def test_failed_tasks_not_in_log() -> None:
    events = [
        CommitEvent(
            op="创建",
            summary="新建 Alpha",
            page_path="concepts/alpha.md",
            page_title="Alpha",
            status="succeeded",
            committed_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
            task_uid="t1",
        ),
        CommitEvent(
            op="更新",
            summary="失败的更新",
            page_path="concepts/beta.md",
            page_title="Beta",
            status="failed",
            committed_at=datetime(2026, 10, 12, 11, 0, tzinfo=timezone.utc),
            task_uid="t2",
        ),
        CommitEvent(
            op="回写",
            summary="已取消",
            page_path="topics/x.md",
            page_title="X",
            status="cancelled",
            committed_at=datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc),
            task_uid="t3",
        ),
    ]
    entries = filter_log_events(events)
    assert len(entries) == 1
    assert entries[0].task_uid == "t1"
    text = render_log(entries)
    assert "新建 Alpha" in text
    assert "失败的更新" not in text
    assert "已取消" not in text
    assert "**创建**" in text


def test_log_newest_date_first() -> None:
    events = [
        CommitEvent(
            op="创建",
            summary="旧",
            page_path="a.md",
            page_title="A",
            status="succeeded",
            committed_at=datetime(2026, 10, 1, 8, 0),
        ),
        CommitEvent(
            op="更新",
            summary="新",
            page_path="b.md",
            page_title="B",
            status="succeeded",
            committed_at=datetime(2026, 10, 12, 8, 0),
        ),
    ]
    text = render_log(filter_log_events(events))
    assert text.index("2026-10-12") < text.index("2026-10-01")


def test_root_index_okf_and_categories() -> None:
    entries = [
        IndexEntry("concepts/alpha", "Alpha", "Concept", "concepts/alpha.md", "概念 A", "concepts"),
        IndexEntry("topics/t1", "Topic1", "Topic", "topics/t1.md", "主题", "topics"),
    ]
    text = render_root_index(entries)
    assert 'okf_version: "0.2"' in text
    assert "[concepts](concepts/index.md)" in text
    assert "[topics](topics/index.md)" in text
    assert "Alpha" in text


def test_writer_regenerate(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    writer = IndexLogWriter(wiki)
    entries = [
        IndexEntry("concepts/alpha", "Alpha", "Concept", "concepts/alpha.md", "A", "concepts"),
    ]
    events = [
        CommitEvent(
            op="创建",
            summary="新建 Alpha",
            page_path="concepts/alpha.md",
            page_title="Alpha",
            status="succeeded",
            committed_at=datetime(2026, 10, 12, 9, 0),
        ),
        CommitEvent(
            op="更新",
            summary="should not appear",
            page_path="concepts/alpha.md",
            page_title="Alpha",
            status="failed",
            committed_at=datetime(2026, 10, 12, 9, 30),
        ),
    ]
    paths = writer.regenerate(entries, events)
    assert (wiki / "index.md").is_file()
    assert (wiki / "concepts" / "index.md").is_file()
    assert (wiki / "log.md").is_file()
    log = (wiki / "log.md").read_text(encoding="utf-8")
    assert "新建 Alpha" in log
    assert "should not appear" not in log
    assert "log.md" in paths
