"""Pattern aggregation and promotion gating (design §23.7 / LW-42 / AT-EVO-04).

Promotion three-conditions (within 30 days):
  Recurrence-Count ≥ 3, Distinct-Tasks ≥ 2
Direct promote: user explicit remember / ERR priority=critical
Excluded from counts: classification=self_assessment
Blocked alone: untrusted_origin (cannot promote without trusted evidence)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Sequence

from localwiki.evolution.learnings import LearningStore

PromotionReason = Literal[
    "three_conditions",
    "user_explicit",
    "critical_err",
]

# §23.7 learning type → promotion target
TARGET_BY_SIGNAL: dict[str, dict[str, str]] = {
    "tool_trap": {"file": "schema/TOOLS.md", "kind": "tool_note"},
    "integration": {"file": "schema/TOOLS.md", "kind": "tool_note"},
    "agent_process": {"file": "schema/AGENTS.md", "kind": "agents_rule", "anchor": "learned_rules"},
    "wiki_compile": {"file": "schema/", "kind": "schema_revision"},
    "wiki_lint": {"file": "schema/", "kind": "schema_revision"},
    "prompt": {"file": "prompts/", "kind": "prompt_variant"},
    "model_capability": {"file": "config/model_routes.json", "kind": "routing_suggestion"},
    "retrieval": {"file": "config/retrieval.json", "kind": "retrieval_params"},
    "skill_extract": {"file": ".kw/skills/local/", "kind": "skill_extract_suggestion"},
    "user_conclusion": {"file": "candidates/", "kind": "user_candidate_handoff"},
    "feature": {"file": "developer_backlog/", "kind": "dev_backlog"},
}

_USER_EXPLICIT_RE = re.compile(
    r"(以后都这样|记住这个做法|记住这个|以后都按|always do (it )?this way|"
    r"remember (this|to)|from now on always)",
    re.I,
)

_SELF_ASSESSMENT = frozenset({"self_assessment", "model_self_assessment"})
_UNTRUSTED = frozenset({"untrusted_origin", "untrusted"})


def _parse_dt(value: str | None) -> datetime | None:
    if not value or value in ("(none)",):
        return None
    text = value.strip()
    # Allow date-only
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text = text + "T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_self_assessment(record: dict[str, Any] | Any) -> bool:
    if isinstance(record, dict):
        cls = str(record.get("classification") or "")
        evidence = str(record.get("evidence") or "")
        source = str(record.get("source") or "")
    else:
        cls = str(getattr(record, "classification", "") or "")
        evidence = ",".join(getattr(record, "evidence", []) or [])
        source = str(getattr(record, "source", "") or "")
    if cls in _SELF_ASSESSMENT:
        return True
    if source == "self_assessment":
        return True
    # Evidence only self_assessment → treat as model self-eval
    parts = {p.strip() for p in evidence.replace(",", " ").split() if p.strip() and p.strip() != "(none)"}
    if parts and parts <= {"self_assessment", "model_self_eval"}:
        return True
    return False


def is_untrusted(record: dict[str, Any]) -> bool:
    cls = str(record.get("classification") or "")
    return cls in _UNTRUSTED


def short_preventive_rule(summary: str, suggested_action: str = "") -> str:
    """§23.7: short preventive rule (what / when), not a long incident report."""
    base = (suggested_action or summary or "").strip()
    # Drop narrative fluff
    base = re.sub(r"(?m)^(任务|task_|第\s*\d+\s*步|详情|事故).*$", "", base)
    base = re.sub(r"\s+", " ", base).strip(" .-")
    if not base:
        base = "避免重复已知失败模式"
    # Prefer imperative / when-clause style
    if len(base) > 120:
        base = base[:117] + "…"
    if not base.startswith(("-", "•")):
        base = f"- {base}"
    return base


def resolve_promotion_target(
    *,
    kind: str,
    category: str,
    area: str,
    summary: str = "",
    pattern_key: str = "",
) -> dict[str, str]:
    """Map learning signal → promotion target file/kind (§23.7 table)."""
    text = f"{category} {area} {summary} {pattern_key}".lower()
    if kind == "FEAT" or "feature" in category.lower():
        return dict(TARGET_BY_SIGNAL["feature"])
    if area == "tools" or "docx" in text or "tool" in text or "trap" in text:
        tgt = dict(TARGET_BY_SIGNAL["tool_trap"])
        # Anchor from pattern_key tools.docx.table_width → docx / tool name
        parts = [p for p in pattern_key.split(".") if p and p not in ("tools", "failure", "gotcha")]
        if parts:
            tgt["anchor"] = parts[0] if len(parts) == 1 else parts[1] if parts[0] in ("tools",) else parts[0]
            if pattern_key.startswith("tools.") and len(pattern_key.split(".")) >= 2:
                tgt["anchor"] = pattern_key.split(".")[1]
        return tgt
    if area == "agent" or "agents" in text or category in ("best_practice", "correction"):
        if "prompt" in text or "提示词" in summary:
            return dict(TARGET_BY_SIGNAL["prompt"])
        return dict(TARGET_BY_SIGNAL["agent_process"])
    if area == "wiki" or "lint" in text or "compile" in text:
        return dict(TARGET_BY_SIGNAL["wiki_lint"] if "lint" in text else TARGET_BY_SIGNAL["wiki_compile"])
    if area == "routing" or "model" in text or "能力" in summary:
        return dict(TARGET_BY_SIGNAL["model_capability"])
    if area in ("memory", "experience") or "检索" in summary or "recall" in text:
        return dict(TARGET_BY_SIGNAL["retrieval"])
    if "skill" in text or area == "skills":
        return dict(TARGET_BY_SIGNAL["skill_extract"])
    if area == "style" or "用户" in summary:
        return dict(TARGET_BY_SIGNAL["user_conclusion"])
    if area == "integration":
        return dict(TARGET_BY_SIGNAL["integration"])
    return dict(TARGET_BY_SIGNAL["agent_process"])


@dataclass
class PatternAggregate:
    pattern_key: str
    records: list[dict[str, Any]] = field(default_factory=list)
    # Countable (excludes self_assessment)
    countable_recurrence: int = 0
    distinct_tasks: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    has_trusted_evidence: bool = False
    only_untrusted: bool = True
    critical_err: bool = False
    user_explicit: bool = False
    sample_summary: str = ""
    sample_suggested: str = ""
    sample_kind: str = "LRN"
    sample_category: str = ""
    sample_area: str = "agent"


@dataclass
class PromotionCandidate:
    pattern_key: str
    reason: PromotionReason
    target: dict[str, str]
    preventive_rule: str
    record_ids: list[str]
    proposal_uid: str
    countable_recurrence: int
    distinct_tasks: int
    window_days: int = 30

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_key": self.pattern_key,
            "reason": self.reason,
            "target": dict(self.target),
            "preventive_rule": self.preventive_rule,
            "record_ids": list(self.record_ids),
            "proposal_uid": self.proposal_uid,
            "countable_recurrence": self.countable_recurrence,
            "distinct_tasks": self.distinct_tasks,
            "window_days": self.window_days,
        }


@dataclass
class PromotionSkip:
    pattern_key: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"pattern_key": self.pattern_key, "reason": self.reason}


def _related_tasks(record: dict[str, Any]) -> set[str]:
    related = record.get("related") or ""
    if isinstance(related, list):
        parts = related
    else:
        parts = [p.strip() for p in str(related).split(",") if p.strip() and p.strip() != "(none)"]
    out: set[str] = set()
    for p in parts:
        if p.startswith("task_") or p.startswith("task"):
            out.add(p)
        elif re.match(r"^[0-9a-zA-Z_-]{4,}$", p) and not p.startswith("ERR") and not p.startswith("LRN"):
            out.add(p)
    return out


def _int_field(record: dict[str, Any], key: str, default: int = 1) -> int:
    try:
        return int(record.get(key) or default)
    except (TypeError, ValueError):
        return default


def build_aggregates(records: Sequence[dict[str, Any]]) -> dict[str, PatternAggregate]:
    """Group by Pattern-Key; self_assessment excluded from countable recurrence."""
    by_key: dict[str, PatternAggregate] = {}
    for rec in records:
        pk = str(rec.get("pattern_key") or "").strip()
        if not pk or pk == "(none)":
            continue
        agg = by_key.get(pk)
        if agg is None:
            agg = PatternAggregate(pattern_key=pk)
            by_key[pk] = agg
        agg.records.append(rec)
        if not agg.sample_summary:
            agg.sample_summary = str(rec.get("summary") or "")
            agg.sample_suggested = str(rec.get("suggested_action") or "")
            agg.sample_kind = str(rec.get("kind") or "LRN")
            agg.sample_category = str(rec.get("category") or "")
            agg.sample_area = str(rec.get("area") or "agent")

        fs = _parse_dt(str(rec.get("first_seen") or ""))
        ls = _parse_dt(str(rec.get("last_seen") or rec.get("first_seen") or ""))
        # Logged field may stand in when first/last missing from parse
        if ls is None:
            ls = _parse_dt(str(rec.get("logged") or ""))

        if is_self_assessment(rec):
            # Still track presence but do NOT add to countable_recurrence / distinct_tasks
            continue

        # Prefer stored counters on the canonical record; sum carefully across duplicates
        # For Pattern-Key dedup store usually has one row with bumped counts.
        bump = _int_field(rec, "recurrence_count", 1)
        # When multiple markdown rows share a key (legacy), take max then reconcile below
        agg.countable_recurrence = max(agg.countable_recurrence, bump)
        agg.distinct_tasks |= _related_tasks(rec)
        stored_dt = _int_field(rec, "distinct_tasks", 0)
        if stored_dt > len(agg.distinct_tasks):
            # Stored Distinct-Tasks may exceed parsed Related list — trust stored floor
            # Fill with synthetic placeholders so len matches for eligibility checks
            while len(agg.distinct_tasks) < stored_dt:
                agg.distinct_tasks.add(f"_stored_task_{len(agg.distinct_tasks)+1}")

        if fs and (agg.first_seen is None or fs < agg.first_seen):
            agg.first_seen = fs
        if ls and (agg.last_seen is None or ls > agg.last_seen):
            agg.last_seen = ls

        if not is_untrusted(rec):
            agg.has_trusted_evidence = True
            agg.only_untrusted = False
        # else keep only_untrusted if never saw trusted

        if str(rec.get("kind")) == "ERR" and str(rec.get("priority") or "").lower() == "critical":
            agg.critical_err = True

        blob = " ".join(
            [
                str(rec.get("summary") or ""),
                str(rec.get("details") or ""),
                str(rec.get("suggested_action") or ""),
            ]
        )
        if _USER_EXPLICIT_RE.search(blob):
            agg.user_explicit = True

    # Reconcile only_untrusted when no countable records
    for agg in by_key.values():
        countable = [r for r in agg.records if not is_self_assessment(r)]
        if not countable:
            agg.countable_recurrence = 0
            agg.distinct_tasks = set()
            agg.only_untrusted = True
            agg.has_trusted_evidence = False
            continue
        if all(is_untrusted(r) for r in countable):
            agg.only_untrusted = True
            agg.has_trusted_evidence = False
        else:
            agg.only_untrusted = False
            agg.has_trusted_evidence = True
    return by_key


def evaluate_aggregate(
    agg: PatternAggregate,
    *,
    now: datetime | None = None,
    window_days: int = 30,
) -> PromotionCandidate | PromotionSkip:
    """Apply §23.7 / AT-EVO-04 gates."""
    now = now or _now()
    pk = agg.pattern_key

    if not agg.records:
        return PromotionSkip(pk, "empty")

    if agg.countable_recurrence <= 0 and not agg.critical_err and not agg.user_explicit:
        return PromotionSkip(pk, "self_assessment_only")

    if agg.only_untrusted and not agg.has_trusted_evidence:
        # untrusted alone cannot promote (even if counts look high)
        if not agg.user_explicit:  # user explicit is trusted signal
            return PromotionSkip(pk, "untrusted_origin_only")

    # Window: all countable activity within last window_days
    window_ok = True
    if agg.first_seen and agg.last_seen:
        if (agg.last_seen - agg.first_seen) > timedelta(days=window_days):
            window_ok = False
        if (now - agg.last_seen) > timedelta(days=window_days):
            window_ok = False
    elif agg.last_seen and (now - agg.last_seen) > timedelta(days=window_days):
        window_ok = False

    reason: PromotionReason | None = None
    if agg.critical_err:
        reason = "critical_err"
    elif agg.user_explicit:
        reason = "user_explicit"
    elif (
        window_ok
        and agg.countable_recurrence >= 3
        and len(agg.distinct_tasks) >= 2
    ):
        reason = "three_conditions"
    elif not window_ok and agg.countable_recurrence >= 3 and len(agg.distinct_tasks) >= 2:
        return PromotionSkip(pk, "outside_30_day_window")
    else:
        return PromotionSkip(
            pk,
            f"below_threshold:rec={agg.countable_recurrence},tasks={len(agg.distinct_tasks)}",
        )

    target = resolve_promotion_target(
        kind=agg.sample_kind,
        category=agg.sample_category,
        area=agg.sample_area,
        summary=agg.sample_summary,
        pattern_key=pk,
    )
    rule = short_preventive_rule(agg.sample_summary, agg.sample_suggested)
    ids = [str(r.get("record_id") or "") for r in agg.records if r.get("record_id")]
    return PromotionCandidate(
        pattern_key=pk,
        reason=reason,
        target=target,
        preventive_rule=rule,
        record_ids=[i for i in ids if i],
        proposal_uid=f"imp_{uuid.uuid4().hex[:12]}",
        countable_recurrence=agg.countable_recurrence,
        distinct_tasks=len(agg.distinct_tasks),
        window_days=window_days,
    )


class PatternAggregator:
    """Scan LearningStore, decide promotions, mark records promoted."""

    def __init__(self, store: LearningStore) -> None:
        self.store = store

    def scan(
        self,
        *,
        now: datetime | None = None,
        window_days: int = 30,
    ) -> tuple[list[PromotionCandidate], list[PromotionSkip]]:
        records = self.store.list_records()
        aggs = build_aggregates(records)
        promoted: list[PromotionCandidate] = []
        skipped: list[PromotionSkip] = []
        for agg in aggs.values():
            result = evaluate_aggregate(agg, now=now, window_days=window_days)
            if isinstance(result, PromotionCandidate):
                promoted.append(result)
            else:
                skipped.append(result)
        return promoted, skipped

    def promote_eligible(
        self,
        *,
        now: datetime | None = None,
        window_days: int = 30,
        mark: bool = True,
    ) -> list[PromotionCandidate]:
        candidates, _ = self.scan(now=now, window_days=window_days)
        if mark:
            for c in candidates:
                self.mark_promoted(c)
        return candidates

    def mark_promoted(self, candidate: PromotionCandidate) -> None:
        """Set Status=promoted; record promotion target + proposal id in metadata lines."""
        for rid in candidate.record_ids:
            self.store.update_record_fields(
                rid,
                {
                    "Status": "promoted",
                    "Promotion-Target": candidate.target.get("file", ""),
                    "Promotion-Kind": candidate.target.get("kind", ""),
                    "Proposal-Id": candidate.proposal_uid,
                    "Preventive-Rule": candidate.preventive_rule,
                },
            )


def observe_and_maybe_promote(
    store: LearningStore,
    *,
    now: datetime | None = None,
) -> list[PromotionCandidate]:
    """Convenience: scan store and mark promotions (LW-42 entry)."""
    return PatternAggregator(store).promote_eligible(now=now, mark=True)
