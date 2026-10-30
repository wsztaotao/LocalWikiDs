"""Tests for memory extract / tier / dedup / conflict (LW-14 / §8.3)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.memory.dedup import (  # noqa: E402
    detect_conflicts,
    find_duplicate,
    normalize_statement,
)
from localwiki.memory.extract import (  # noqa: E402
    ExtractedCandidate,
    classify_risk_tier,
    extract_memory_candidates,
    process_candidates,
)
from localwiki.memory.store import MemoryRecord, MemoryStore  # noqa: E402


def test_t0_explicit_low_risk_preference() -> None:
    tier, reason = classify_risk_tier(
        "以后回答短一点",
        kind="preference",
        origin="explicit_user",
    )
    assert tier == "T0"
    assert "explicit" in reason or "preference" in reason


def test_t1_inferred_or_procedural() -> None:
    tier, _ = classify_risk_tier(
        "发布前似乎总是先跑回归",
        kind="procedural",
        origin="inferred",
    )
    assert tier == "T1"


def test_t2_sensitive_blocked() -> None:
    texts = [
        "我的身份证号是110101199001011234",
        "我有抑郁症需要长期服药",
        "我的银行卡余额有五十万",
        "张三的家庭住址是北京市朝阳区某某路",
    ]
    for text in texts:
        tier, reason = classify_risk_tier(text, kind="semantic", origin="explicit_user")
        assert tier == "T2", text
        assert "sensitive" in reason or "third_party" in reason


def test_extract_and_process_blocks_t2(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    texts = [
        "我喜欢用表格对比方案",
        "身份证号是44030119800101123X",
    ]
    result = process_candidates(texts, store=store, source="chat")
    assert any(a.action == "auto_confirm" for a in result.accepted)
    assert any(a.action == "blocked" for a in result.blocked)
    assert all(b.candidate.risk_tier == "T2" for b in result.blocked)
    # T2 never written
    assert not any("440301" in r.statement for r in store.list())


def test_dedup_merges_evidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    existing = MemoryRecord(
        uid="mem_dup1",
        kind="preference",
        statement="方案比较优先使用表格",
        confirmation_state="user_confirmed",
        risk_tier="T0",
        evidence_refs=["msg:old"],
    )
    store.save(existing)
    cand = ExtractedCandidate(
        statement="方案比较优先使用表格。",
        kind="preference",
        origin="explicit_user",
        evidence_refs=["msg:new"],
    )
    dup = find_duplicate(cand, store.list())
    assert dup is not None
    assert dup.uid == "mem_dup1"
    result = process_candidates(
        [cand.statement],
        store=store,
        source="chat",
        evidence_prefix="msg:batch",
    )
    assert any(a.action == "merged" for a in result.accepted)
    loaded = store.get("mem_dup1")
    assert loaded is not None
    assert "msg:old" in loaded.evidence_refs
    assert any(r.startswith("msg:") for r in loaded.evidence_refs)
    assert loaded.access_count >= 1


def test_conflict_does_not_overwrite(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_dirs()
    store.save(
        MemoryRecord(
            uid="mem_c1",
            kind="preference",
            statement="喜欢详细长文回答",
            subject="user",
            confirmation_state="user_confirmed",
            applicability={"contexts": ["work"]},
        )
    )
    cand = ExtractedCandidate(
        statement="喜欢简短回答",
        kind="preference",
        origin="explicit_user",
        subject="user",
        applicability={"contexts": ["work"]},
    )
    conflicts = detect_conflicts(cand, store.list())
    assert len(conflicts) >= 1
    result = process_candidates([cand.statement], store=store, source="chat")
    assert any(a.action == "conflict" for a in result.conflicts)
    # Old memory unchanged
    old = store.get("mem_c1")
    assert old is not None
    assert "详细" in old.statement
    assert old.confirmation_state == "user_confirmed"


def test_normalize_statement() -> None:
    assert normalize_statement("  喜欢表格！！ ") == normalize_statement("喜欢表格")
