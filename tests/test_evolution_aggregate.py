"""LW-42 / AT-EVO-04: Pattern-Key aggregation and promotion thresholds."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.aggregate import (  # noqa: E402
    PatternAggregator,
    build_aggregates,
    evaluate_aggregate,
    is_self_assessment,
    short_preventive_rule,
)
from localwiki.evolution.learnings import LearningRecord, LearningStore  # noqa: E402


def _iso(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat(timespec="seconds")


def _seed(
    store: LearningStore,
    *,
    pattern_key: str,
    recurrence: int,
    distinct_tasks: int,
    classification: str = "normal",
    kind: str = "LRN",
    priority: str = "medium",
    summary: str = "docx_create 多列表格必须设列宽",
    related: list[str] | None = None,
    days_ago_first: int = 5,
    days_ago_last: int = 0,
    area: str = "tools",
    category: str = "best_practice",
) -> LearningRecord:
    tasks = related or [f"task_{i}" for i in range(1, distinct_tasks + 1)]
    rec = LearningRecord(
        kind=kind,  # type: ignore[arg-type]
        category=category,
        summary=summary,
        details="seeded for promotion test",
        suggested_action="生成多列表格时为每列指定宽度",
        area=area,
        priority=priority,
        pattern_key=pattern_key,
        recurrence_count=recurrence,
        distinct_tasks=distinct_tasks,
        related=tasks,
        evidence=["tool_result", "user_edit"]
        if classification == "normal"
        else [classification],
        classification=classification,
        first_seen=_iso(days_ago_first),
        last_seen=_iso(days_ago_last),
        source="test",
    )
    # Bypass Pattern-Key bump so seeded counters stick: write directly once
    if store.find_by_pattern_key(pattern_key):
        # overwrite via append bump path — for tests use unique keys
        pass
    return store.append(rec)


def test_pattern_key_dedup_on_append(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    a = LearningRecord(
        kind="ERR",
        category="error",
        summary="patch failed",
        area="tools",
        pattern_key="tools.apply_patch.failure",
        related=["task_a"],
        evidence=["tool_result"],
    )
    b = LearningRecord(
        kind="ERR",
        category="error",
        summary="patch failed again",
        area="tools",
        pattern_key="tools.apply_patch.failure",
        related=["task_b"],
        evidence=["tool_result"],
    )
    store.append(a)
    store.append(b)
    entries = store.list_records("ERR")
    assert len(entries) == 1
    assert int(entries[0]["recurrence_count"]) == 2
    assert int(entries[0]["distinct_tasks"]) >= 2


def test_three_conditions_promote(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="tools.docx.table_width",
        recurrence=3,
        distinct_tasks=2,
    )
    cands, skips = PatternAggregator(store).scan()
    assert any(c.pattern_key == "tools.docx.table_width" for c in cands)
    cand = next(c for c in cands if c.pattern_key == "tools.docx.table_width")
    assert cand.reason == "three_conditions"
    assert cand.countable_recurrence >= 3
    assert cand.distinct_tasks >= 2
    assert cand.target["file"] == "schema/TOOLS.md"
    assert cand.preventive_rule.startswith("-")


def test_below_threshold_no_promote(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="tools.docx.narrow",
        recurrence=2,
        distinct_tasks=2,
    )
    cands, skips = PatternAggregator(store).scan()
    assert not any(c.pattern_key == "tools.docx.narrow" for c in cands)
    assert any(s.pattern_key == "tools.docx.narrow" for s in skips)


def test_self_assessment_not_counted(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    # First countable
    store.append(
        LearningRecord(
            kind="LRN",
            category="best_practice",
            summary="use column widths",
            area="tools",
            pattern_key="tools.docx.selfmix",
            related=["task_1"],
            evidence=["tool_result"],
            classification="normal",
            recurrence_count=1,
            distinct_tasks=1,
            first_seen=_iso(2),
            last_seen=_iso(2),
        )
    )
    # Self-assessment bumps must NOT increase countable recurrence
    store.append(
        LearningRecord(
            kind="LRN",
            category="best_practice",
            summary="I think widths matter",
            area="tools",
            pattern_key="tools.docx.selfmix",
            related=["task_2"],
            evidence=["self_assessment"],
            classification="self_assessment",
            first_seen=_iso(1),
            last_seen=_iso(1),
        )
    )
    store.append(
        LearningRecord(
            kind="LRN",
            category="best_practice",
            summary="model believes again",
            area="tools",
            pattern_key="tools.docx.selfmix",
            related=["task_3"],
            evidence=["self_assessment"],
            classification="self_assessment",
            first_seen=_iso(0),
            last_seen=_iso(0),
        )
    )
    entries = store.list_records("LRN")
    keyed = [e for e in entries if e["pattern_key"] == "tools.docx.selfmix"]
    assert len(keyed) == 1
    # Only the first countable write counts in store recurrence
    assert int(keyed[0]["recurrence_count"]) == 1
    assert is_self_assessment({"classification": "self_assessment", "evidence": "self_assessment"})

    cands, skips = PatternAggregator(store).scan()
    assert not any(c.pattern_key == "tools.docx.selfmix" for c in cands)
    skip = next(s for s in skips if s.pattern_key == "tools.docx.selfmix")
    assert "below_threshold" in skip.reason or skip.reason == "self_assessment_only"


def test_untrusted_alone_cannot_promote(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="tools.web.inject",
        recurrence=5,
        distinct_tasks=3,
        classification="untrusted_origin",
        summary="from scraped page advice",
    )
    cands, skips = PatternAggregator(store).scan()
    assert not any(c.pattern_key == "tools.web.inject" for c in cands)
    assert any(s.reason == "untrusted_origin_only" for s in skips)


def test_critical_err_direct_promote(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="tools.shell.critical",
        recurrence=1,
        distinct_tasks=1,
        kind="ERR",
        priority="critical",
        category="error",
        summary="destructive failure",
    )
    cands, _ = PatternAggregator(store).scan()
    assert any(c.reason == "critical_err" for c in cands)


def test_user_explicit_direct_promote(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="agent.style.explicit",
        recurrence=1,
        distinct_tasks=1,
        area="agent",
        summary="以后都这样：先读 AGENTS.md 再改文件",
    )
    cands, _ = PatternAggregator(store).scan()
    assert any(c.reason == "user_explicit" for c in cands)


def test_outside_30_day_window(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="tools.old.pattern",
        recurrence=3,
        distinct_tasks=2,
        days_ago_first=60,
        days_ago_last=45,
    )
    cands, skips = PatternAggregator(store).scan()
    assert not any(c.pattern_key == "tools.old.pattern" for c in cands)
    assert any(s.reason == "outside_30_day_window" for s in skips)


def test_mark_promoted_updates_status(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    _seed(
        store,
        pattern_key="tools.docx.promote_mark",
        recurrence=3,
        distinct_tasks=2,
    )
    cands = PatternAggregator(store).promote_eligible(mark=True)
    assert cands
    entries = store.list_records()
    hit = next(e for e in entries if e["pattern_key"] == "tools.docx.promote_mark")
    assert hit["status"] == "promoted"
    assert hit.get("proposal_id")


def test_short_preventive_rule() -> None:
    rule = short_preventive_rule(
        "长篇事故：任务 task_01 第 4 步失败然后用户手动改了",
        "生成多列表格时必须为每列指定宽度",
    )
    assert rule.startswith("-")
    assert "task_01" not in rule or len(rule) < 130
