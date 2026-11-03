"""Tests for nightly consolidate + forget cascade (LW-16 / §8.6 / §8.8)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.memory.consolidate import (  # noqa: E402
    DependencyIndex,
    FtsIndex,
    consolidate_nightly,
    forget_memory,
)
from localwiki.memory.embed import EmbeddingIndex, hash_embed  # noqa: E402
from localwiki.memory.store import MemoryRecord, MemoryStore  # noqa: E402


def test_consolidate_episode_to_candidate_not_auto_confirm(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    now = datetime.now(timezone.utc)
    for i in range(3):
        store.save(
            MemoryRecord(
                uid=f"ep_{i}",
                kind="episodic",
                statement=f"讨论了压测方案第{i}次，决定推迟上线",
                origin="observed",
                confirmation_state="proposed",
                created_at=(now - timedelta(days=1)).isoformat(),
            )
        )
    report = consolidate_nightly(store, now=now)
    assert report.episode_candidates >= 1
    # Must not auto-confirm inferred
    assert all(c.confirmation_state == "proposed" for c in report.proposals)
    assert report.auto_confirmed == 0


def test_decay_and_expire(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    now = datetime.now(timezone.utc)
    store.save(
        MemoryRecord(
            uid="mem_dormant",
            kind="semantic",
            statement="很久没提的推断事实",
            origin="inferred",
            confirmation_state="proposed",
            importance=0.1,
            last_accessed_at=(now - timedelta(days=200)).isoformat(),
            created_at=(now - timedelta(days=200)).isoformat(),
        )
    )
    store.save(
        MemoryRecord(
            uid="mem_exp",
            kind="semantic",
            statement="已过期条目",
            confirmation_state="user_confirmed",
            valid_until=(now - timedelta(days=1)).isoformat(),
        )
    )
    report = consolidate_nightly(store, now=now)
    assert "mem_dormant" in report.dormant_uids
    assert "mem_exp" in report.expired_uids
    d = store.get("mem_dormant")
    assert d is not None and d.lifecycle == "dormant"
    e = store.get("mem_exp")
    assert e is not None and e.lifecycle == "expired"


def test_forget_cascades_fts_vectors_deps(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    rec = MemoryRecord(
        uid="mem_del",
        kind="preference",
        statement="要删除的偏好",
        confirmation_state="user_confirmed",
    )
    store.save(rec)
    # Version history
    store.save(rec.with_updates(statement="要删除的偏好 v2"))

    fts = FtsIndex(tmp_path)
    fts.upsert(rec.uid, rec.statement)
    emb = EmbeddingIndex()
    emb.upsert(rec.uid, hash_embed(rec.statement))
    deps = DependencyIndex(tmp_path)
    deps.link("exp_1", "mem_del", kind="experience")
    deps.link("style_1", "mem_del", kind="style")

    result = forget_memory(
        store,
        "mem_del",
        mode="delete",
        fts=fts,
        embeddings=emb,
        dependencies=deps,
    )
    assert result.ok
    assert store.get("mem_del") is None
    assert rec.uid not in fts.uids()
    assert rec.uid not in emb.vectors
    assert deps.get("exp_1")["status"] == "needs_review"
    assert deps.get("style_1")["status"] == "needs_review"


def test_purge_clears_history(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    store.save(
        MemoryRecord(
            uid="mem_purge",
            kind="semantic",
            statement="彻底删除",
            confirmation_state="user_confirmed",
        )
    )
    store.save(
        MemoryRecord(
            uid="mem_purge",
            kind="semantic",
            statement="彻底删除 v2",
            confirmation_state="user_confirmed",
            content_version=1,
        )
    )
    assert store.list_versions("mem_purge")
    result = forget_memory(store, "mem_purge", mode="purge", confirm_purge=True)
    assert result.ok
    assert store.get("mem_purge") is None
    assert store.list_versions("mem_purge") == []
    assert result.audit_placeholder is not None
