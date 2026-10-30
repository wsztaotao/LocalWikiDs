"""Tests for behavior_log (LW-17 / §7.4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.behavior_log import (  # noqa: E402
    BEHAVIOR_CATEGORIES,
    BehaviorLog,
    BehaviorSettings,
)


def test_categories_match_design() -> None:
    assert set(BEHAVIOR_CATEGORIES) == {
        "review",
        "edit",
        "approval",
        "model",
        "time",
        "task",
        "tool",
        "answer",
    }


def test_writes_metadata_only_no_body(tmp_path: Path) -> None:
    blog = BehaviorLog(tmp_path)
    ev = blog.record(
        "review",
        "proposal_edited",
        subject_type="Topic",
        metrics={"len_before": 1820, "len_after": 1105, "sections_removed": 1},
        ctx={"task_type": "technical_design"},
        # Attempt to sneak body — must be rejected / stripped
        body="这是不应该写入的正文内容 SECRET",
        content="also forbidden",
    )
    assert ev is not None
    shard = list((tmp_path / "raw" / "behavior").glob("*.jsonl"))
    assert len(shard) == 1
    line = shard[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    data = json.loads(line)
    assert "body" not in data
    assert "content" not in data
    assert "SECRET" not in line
    assert "不应该" not in line
    assert data["cat"] == "review"
    assert data["metrics"]["len_before"] == 1820
    assert "ev_uid" in data
    assert "ts" in data


def test_category_disabled_stops_write(tmp_path: Path) -> None:
    blog = BehaviorLog(tmp_path)
    blog.update_settings(BehaviorSettings(enabled={"review": False}))
    ev = blog.record(
        "review",
        "proposal_accepted",
        metrics={"count": 1},
    )
    assert ev is None
    shards = list((tmp_path / "raw" / "behavior").glob("*.jsonl"))
    assert shards == [] or all(
        not p.read_text(encoding="utf-8").strip() for p in shards
    )
    # Other categories still write
    ev2 = blog.record("model", "route_switched", metrics={"from": "a", "to": "b"})
    assert ev2 is not None


def test_purge_optional(tmp_path: Path) -> None:
    blog = BehaviorLog(tmp_path)
    blog.record("tool", "tool_used", metrics={"name": "shell", "ok": 1})
    assert blog.stats()["tool"] >= 1
    n = blog.purge(categories=["tool"])
    assert n >= 1
    assert blog.stats().get("tool", 0) == 0
