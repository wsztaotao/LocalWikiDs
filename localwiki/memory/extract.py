"""Memory candidate extraction and risk tiering (design §8.3).

Heuristic extractor (no LLM) for reproducible tests. Model-backed extract can
feed the same ``ExtractedCandidate`` / ``process_candidates`` contracts later.
T2 sensitive content is blocked from writes by default — never relax.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from localwiki.memory.dedup import detect_conflicts, find_duplicate, normalize_statement
from localwiki.memory.store import MemoryRecord, MemoryStore

RiskTier = Literal["T0", "T1", "T2"]
ProcessAction = Literal["auto_confirm", "queue", "blocked", "merged", "conflict"]

_PREFERENCE = re.compile(
    r"(喜欢|偏好|不要|别用|以后|请|希望).{0,20}(表格|短|简洁|详细|长|列表|代码)",
    re.I,
)
_EXPLICIT_PREF = re.compile(
    r"(以后|请|希望|我想|我要|记得).{0,12}(回答|回复|写|用).{0,12}(短|简洁|表格|长|详细)",
    re.I,
)
_PROCEDURE = re.compile(
    r"(一定|必须|总是|先|再|然后|发布前|提交前).{0,30}(跑|测|检查|回归|review)",
    re.I,
)
_RELATIONAL = re.compile(
    r"(是|担任|负责).{0,8}(对接|甲方|客户|同事|经理|负责人)",
)

# T2 sensitive heuristics — health, religion, politics, sexuality, ethnicity,
# finance details, ID numbers, third-party private info
_SENSITIVE = re.compile(
    r"("
    r"身份证|护照|社保号|银行卡|信用卡|余额|工资|存款|"
    r"抑郁|艾滋|癌症|精神病|残疾|"
    r"宗教|信仰|党员|民主党|共和党|"
    r"性取向|同性恋|异性恋|"
    r"民族成分|户籍地址|"
    r"\b\d{15,18}[\dXx]\b|"  # ID-like
    r"\b\d{16,19}\b"  # card-like
    r")",
    re.I,
)
_THIRD_PARTY_PRIVATE = re.compile(
    r"(他的|她的|别人的|张三|李四).{0,20}(住址|电话|手机|工资|病|隐私)",
)


def _uid(prefix: str = "memc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExtractedCandidate:
    statement: str
    kind: str = "semantic"
    origin: str = "inferred"
    subject: str = "user"
    risk_tier: RiskTier | str = "T1"
    tier_reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    applicability: dict[str, Any] = field(default_factory=dict)
    candidate_uid: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_uid:
            self.candidate_uid = _uid()


@dataclass
class ProcessItem:
    action: ProcessAction
    candidate: ExtractedCandidate
    memory_uid: str | None = None
    detail: str = ""
    conflict_with: str | None = None


@dataclass
class ProcessResult:
    accepted: list[ProcessItem] = field(default_factory=list)
    queued: list[ProcessItem] = field(default_factory=list)
    blocked: list[ProcessItem] = field(default_factory=list)
    conflicts: list[ProcessItem] = field(default_factory=list)

    @property
    def all_items(self) -> list[ProcessItem]:
        return self.accepted + self.queued + self.blocked + self.conflicts


def classify_risk_tier(
    statement: str,
    *,
    kind: str = "semantic",
    origin: str = "inferred",
) -> tuple[RiskTier, str]:
    """Return (tier, reason). T2 always wins over T0/T1."""
    text = statement.strip()
    if _SENSITIVE.search(text):
        return "T2", "sensitive_attribute"
    if _THIRD_PARTY_PRIVATE.search(text):
        return "T2", "third_party_private"

    if kind == "preference" and origin == "explicit_user":
        # Low-risk explicit preference → T0
        if _EXPLICIT_PREF.search(text) or _PREFERENCE.search(text) or len(text) < 40:
            return "T0", "explicit_user_preference"
        return "T0", "explicit_preference_default"

    if origin == "inferred" or kind in ("procedural", "relational", "semantic"):
        return "T1", "inferred_or_behavioral"

    if kind == "episodic":
        return "T1", "episodic_needs_review"

    return "T1", "default_queue"


def extract_memory_candidates(
    texts: list[str],
    *,
    source: str = "chat",
    evidence_prefix: str = "msg",
) -> list[ExtractedCandidate]:
    """Heuristic extract from utterance strings (self-attributed)."""
    out: list[ExtractedCandidate] = []
    for i, raw in enumerate(texts):
        text = raw.strip()
        if not text or len(text) < 4:
            continue
        kind = "semantic"
        origin = "observed"
        if _EXPLICIT_PREF.search(text) or _PREFERENCE.search(text):
            kind = "preference"
            origin = "explicit_user"
        elif _PROCEDURE.search(text):
            kind = "procedural"
            origin = "observed"
        elif _RELATIONAL.search(text):
            kind = "relational"
            origin = "observed"

        tier, reason = classify_risk_tier(text, kind=kind, origin=origin)
        out.append(
            ExtractedCandidate(
                statement=text[:500],
                kind=kind,
                origin=origin,
                risk_tier=tier,
                tier_reason=reason,
                evidence_refs=[f"{evidence_prefix}:{source}:{i}"],
                applicability={"contexts": ["work"]} if kind != "episodic" else {},
            )
        )
    return out


def process_candidates(
    texts: list[str] | list[ExtractedCandidate],
    *,
    store: MemoryStore,
    source: str = "chat",
    evidence_prefix: str = "msg",
) -> ProcessResult:
    """Normalize → dedup → conflict → tier → write/queue/block (§8.3 order).

    Design diagram shows tier after conflict; T2 still blocked even if duplicate.
    """
    if texts and isinstance(texts[0], ExtractedCandidate):
        candidates = list(texts)  # type: ignore[arg-type]
    else:
        candidates = extract_memory_candidates(
            [str(t) for t in texts],  # type: ignore[arg-type]
            source=source,
            evidence_prefix=evidence_prefix,
        )

    result = ProcessResult()
    existing = store.list()

    for cand in candidates:
        # Re-classify to be safe
        tier, reason = classify_risk_tier(
            cand.statement, kind=cand.kind, origin=cand.origin
        )
        cand.risk_tier = tier
        cand.tier_reason = reason

        if tier == "T2":
            result.blocked.append(
                ProcessItem(
                    action="blocked",
                    candidate=cand,
                    detail=f"T2 blocked: {reason}",
                )
            )
            continue

        dup = find_duplicate(cand, existing)
        if dup is not None:
            # Merge evidence; do not create new
            refs = list(dict.fromkeys(list(dup.evidence_refs) + list(cand.evidence_refs)))
            updated = dup.with_updates(
                evidence_refs=refs,
                last_accessed_at=_now(),
                access_count=dup.access_count + 1,
            )
            store.save(updated)
            # refresh existing list entry
            existing = [updated if e.uid == dup.uid else e for e in existing]
            result.accepted.append(
                ProcessItem(
                    action="merged",
                    candidate=cand,
                    memory_uid=dup.uid,
                    detail="duplicate_merged",
                )
            )
            continue

        conflicts = detect_conflicts(cand, existing)
        if conflicts:
            result.conflicts.append(
                ProcessItem(
                    action="conflict",
                    candidate=cand,
                    conflict_with=conflicts[0].uid,
                    detail="conflict_needs_user_choice",
                )
            )
            continue

        if tier == "T0":
            rec = MemoryRecord(
                uid=_uid("mem"),
                kind=cand.kind,
                title=cand.statement[:80],
                statement=cand.statement,
                subject=cand.subject,
                origin=cand.origin,
                confirmation_state="auto_confirmed",
                risk_tier="T0",
                classification="private",
                evidence_refs=list(cand.evidence_refs),
                applicability=dict(cand.applicability),
                body=cand.statement,
            )
            store.save(rec)
            existing.append(rec)
            result.accepted.append(
                ProcessItem(
                    action="auto_confirm",
                    candidate=cand,
                    memory_uid=rec.uid,
                    detail="T0 auto_confirmed_revocable",
                )
            )
        else:
            # T1 → pending queue (not written as confirmed)
            result.queued.append(
                ProcessItem(
                    action="queue",
                    candidate=cand,
                    detail="T1 pending_confirmation",
                )
            )

    return result
