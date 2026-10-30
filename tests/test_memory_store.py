"""Tests for memory store + page format + legacy migrate (LW-13 / §8.1–8.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.memory.legacy import migrate_legacy_memories  # noqa: E402
from localwiki.memory.store import (  # noqa: E402
    MEMORY_KINDS,
    LONG_TERM_KINDS,
    MemoryRecord,
    MemoryStore,
    parse_memory_page,
    render_memory_page,
)


def test_five_kinds_defined() -> None:
    assert set(MEMORY_KINDS) == {
        "episodic",
        "semantic",
        "procedural",
        "preference",
        "relational",
    }
    assert set(LONG_TERM_KINDS) == {
        "semantic",
        "procedural",
        "preference",
        "relational",
    }


def test_render_parse_roundtrip() -> None:
    rec = MemoryRecord(
        uid="mem_01JTEST0001",
        kind="preference",
        title="报告偏好用表格对比方案",
        description="写方案比较类内容时，用户偏好表格而非长段落。",
        statement="方案比较类内容优先使用表格。",
        subject="user",
        origin="explicit_user",
        confirmation_state="user_confirmed",
        risk_tier="T0",
        importance=0.6,
        classification="private",
        body="完整表述与证据摘录。",
        evidence_refs=["msg:m_01J", "ev:ev_01J"],
        applicability={"task_types": ["report"], "contexts": ["work"]},
    )
    raw = render_memory_page(rec)
    assert raw.startswith("---\n")
    assert "type: Memory" in raw
    assert "kind: preference" in raw
    parsed = parse_memory_page(raw)
    assert parsed.uid == rec.uid
    assert parsed.kind == "preference"
    assert parsed.statement == rec.statement
    assert parsed.confirmation_state == "user_confirmed"
    assert parsed.risk_tier == "T0"
    assert parsed.importance == 0.6
    assert parsed.classification == "private"
    assert "表格" in parsed.statement
    assert "证据" in parsed.body or "摘录" in parsed.body


def test_store_read_write_version(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    rec = MemoryRecord(
        uid="mem_rw_001",
        kind="semantic",
        title="负责后端",
        statement="用户负责 A 项目的后端架构",
        origin="observed",
        confirmation_state="user_confirmed",
        risk_tier="T1",
        body="细节说明",
    )
    path = store.save(rec)
    assert path.is_file()
    assert "wiki/memories/semantic" in str(path).replace("\\", "/")

    loaded = store.get("mem_rw_001")
    assert loaded is not None
    assert loaded.statement == rec.statement
    assert loaded.content_version == 1

    rec2 = loaded.with_updates(statement="用户负责 A 与 B 项目的后端架构", body="更新")
    store.save(rec2)
    loaded2 = store.get("mem_rw_001")
    assert loaded2 is not None
    assert loaded2.content_version == 2
    assert "B 项目" in loaded2.statement

    versions = store.list_versions("mem_rw_001")
    assert len(versions) == 2
    assert versions[0].content_version == 1
    assert "B 项目" not in versions[0].statement


def test_episodic_jsonl(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    ep = MemoryRecord(
        uid="ep_001",
        kind="episodic",
        title="压测讨论",
        statement="10 月 9 日与李四讨论了压测方案，决定推迟上线",
        origin="observed",
        confirmation_state="proposed",
        risk_tier="T1",
        body="",
    )
    store.save(ep)
    listed = store.list(kind="episodic")
    assert len(listed) == 1
    assert listed[0].uid == "ep_001"
    # Episodes live under .kw/memory/episodes, not wiki/memories/episodic
    assert (tmp_path / ".kw" / "memory" / "episodes" / "episodes.jsonl").is_file()
    assert not (tmp_path / "wiki" / "memories" / "episodic").exists()


def test_legacy_migrate_flat_json(tmp_path: Path) -> None:
    legacy_path = tmp_path / ".kw" / "memory" / "legacy_memories.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            [
                {
                    "id": "old_1",
                    "text": "我喜欢简洁回答",
                    "type": "preference",
                    "confirmed": True,
                },
                {
                    "id": "old_2",
                    "text": "负责支付模块",
                    "category": "fact",
                    "confirmed": False,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = migrate_legacy_memories(tmp_path)
    assert report.migrated >= 2
    store = MemoryStore(tmp_path)
    prefs = store.list(kind="preference")
    assert any("简洁" in r.statement for r in prefs)
    # Idempotent
    report2 = migrate_legacy_memories(tmp_path)
    assert report2.migrated == 0
    assert report2.skipped >= 2
