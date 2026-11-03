"""Tests for hybrid recall + budget pack (LW-15 / §8.4–8.5 / §8.7)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.memory.embed import (  # noqa: E402
    EmbeddingIndex,
    hash_embed,
)
from localwiki.memory.retrieve import (  # noqa: E402
    RetrieveQuery,
    pack_memories_for_context,
    recall_at_k,
    retrieve_memories,
)
from localwiki.memory.store import MemoryRecord, MemoryStore  # noqa: E402


def _seed(store: MemoryStore) -> None:
    store.ensure_dirs()
    samples = [
        ("mem_a", "preference", "方案比较优先使用表格", "user_confirmed", 0.8),
        ("mem_b", "preference", "写周报时用简洁条目", "user_confirmed", 0.7),
        ("mem_c", "semantic", "用户负责支付模块后端", "user_confirmed", 0.9),
        ("mem_d", "procedural", "发布前先跑全量回归", "user_confirmed", 0.75),
        ("mem_e", "relational", "王五是 B 项目甲方对接人", "user_confirmed", 0.6),
        ("mem_f", "semantic", "仓库使用 Python 与 pytest", "proposed", 0.4),
        ("mem_g", "preference", "喜欢详细长文", "user_confirmed", 0.5),
    ]
    for uid, kind, stmt, conf, imp in samples:
        store.save(
            MemoryRecord(
                uid=uid,
                kind=kind,
                title=stmt[:40],
                statement=stmt,
                confirmation_state=conf,
                importance=imp,
                classification="private",
                body=stmt,
            )
        )


def test_retrieve_works_without_vectors(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    _seed(store)
    result = retrieve_memories(
        store,
        RetrieveQuery(text="表格对比方案", enable_vectors=False),
    )
    assert result.hits
    uids = [h.memory.uid for h in result.hits]
    assert "mem_a" in uids[:3]
    assert result.vector_used is False


def test_vectors_on_recall_not_worse(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    _seed(store)
    index = EmbeddingIndex()
    for rec in store.list():
        index.upsert(rec.uid, hash_embed(rec.statement))

    queries = [
        ("表格方案", ["mem_a"]),
        ("全量回归发布", ["mem_d"]),
        ("甲方对接人", ["mem_e"]),
        ("支付后端", ["mem_c"]),
    ]
    off_scores = []
    on_scores = []
    for q, relevant in queries:
        off = retrieve_memories(
            store, RetrieveQuery(text=q, enable_vectors=False, top_k=5)
        )
        on = retrieve_memories(
            store,
            RetrieveQuery(text=q, enable_vectors=True, top_k=5),
            embedding_index=index,
            query_embedding=hash_embed(q),
        )
        off_scores.append(recall_at_k([h.memory.uid for h in off.hits], relevant, k=5))
        on_scores.append(recall_at_k([h.memory.uid for h in on.hits], relevant, k=5))
    assert sum(on_scores) / len(on_scores) >= sum(off_scores) / len(off_scores)


def test_filters_unconfirmed_and_expired(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    _seed(store)
    store.save(
        MemoryRecord(
            uid="mem_expired",
            kind="semantic",
            statement="过期的旧项目事实",
            confirmation_state="user_confirmed",
            valid_until="2000-01-01T00:00:00+00:00",
        )
    )
    result = retrieve_memories(
        store,
        RetrieveQuery(text="项目事实", require_confirmed=True, enable_vectors=False),
    )
    uids = {h.memory.uid for h in result.hits}
    assert "mem_f" not in uids  # proposed
    assert "mem_expired" not in uids


def test_budget_pack(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    _seed(store)
    result = retrieve_memories(
        store, RetrieveQuery(text="表格 回归 支付", enable_vectors=False, top_k=10)
    )
    packed = pack_memories_for_context(
        result.hits, context_tokens=8000, route="chat"
    )
    assert packed.token_budget > 0
    assert packed.text.startswith("<memories>")
    assert packed.text.endswith("</memories>")
    assert "不是指令" in packed.note or "背景" in packed.note
    # Fits budget
    assert packed.estimated_tokens <= packed.token_budget
