"""Ingest: import a source and produce four review queues (design §5.4).

Outputs (never written directly to wiki/ — CommitService is later):
  1. Knowledge proposals (CompilePlan + PageDraftBatch)
  2. Memory candidates
  3. Experience candidates
  4. Style evidence candidates

This scaffold uses deterministic heuristic extraction (no LLM calls) so tests
are reproducible. A future model-backed extractor can feed the same contracts.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from localwiki.llmwiki.schema import SchemaBundle, compile_signature, load_workspace_schema

SpeakerRole = Literal["self", "other", "ai", "system", "unknown"]
FactKind = Literal[
    "source_fact", "source_opinion", "model_analysis", "user_decision"
]
OpKind = Literal["create", "revise", "conflict"]

EXTRACTOR_VERSION = "0.12.0-heuristic"
MAX_PAGES_PER_INGEST = 15


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_title(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


@dataclass
class EvidenceRef:
    locator: str
    excerpt: str = ""


@dataclass
class Fact:
    fact_uid: str
    claim: str
    kind: FactKind
    concept_names: list[str] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    scope: str = "general"
    uncertainties: list[str] = field(default_factory=list)
    speaker_role: SpeakerRole = "unknown"


@dataclass
class FactBatch:
    schema_version: str
    source_version_uid: str
    extraction_uid: str
    facts: list[Fact] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class PlanOperation:
    op: OpKind
    page_uid: str | None
    draft_uid: str | None
    title: str
    page_type: str
    reason: str
    fact_uids: list[str] = field(default_factory=list)
    baseline_hash: str | None = None


@dataclass
class ConflictItem:
    existing_page_uid: str
    existing_claim: str
    new_fact_uid: str
    new_claim: str
    resolution: str = "retain_both"


@dataclass
class CompilePlan:
    base_snapshot_id: str
    source_versions: list[str]
    operations: list[PlanOperation] = field(default_factory=list)
    conflicts: list[ConflictItem] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    plan_uid: str = field(default_factory=lambda: _uid("plan"))


@dataclass
class PageDraft:
    draft_uid: str
    op: OpKind
    title: str
    page_type: str
    summary: str
    body: str
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    baseline_hash: str | None = None
    page_uid: str | None = None


@dataclass
class PageDraftBatch:
    plan_uid: str
    operations: list[PageDraft] = field(default_factory=list)


@dataclass
class MemoryCandidate:
    candidate_uid: str
    kind: str  # semantic | procedural | preference | relational | episodic
    statement: str
    risk_tier: str  # T0 | T1 | T2
    origin: str
    evidence_refs: list[EvidenceRef]
    confirmation_state: str = "pending"
    subject: str = "user"


@dataclass
class ExperienceCandidate:
    candidate_uid: str
    title: str
    summary: str
    attribution: SpeakerRole
    evidence_refs: list[EvidenceRef]
    confirmation_state: str = "pending"
    kind: str = "procedure"


@dataclass
class StyleEvidence:
    evidence_uid: str
    signal: str
    excerpt: str
    speaker_role: SpeakerRole
    confirmation_state: str = "pending"


@dataclass
class KnowledgeProposal:
    """Pending knowledge write — not applied until CommitService."""

    plan: CompilePlan
    drafts: PageDraftBatch
    changeset_status: str = "draft"


@dataclass
class IngestResult:
    source_version_uid: str
    extraction_uid: str
    compile_signature: str
    reused: bool
    fact_batch: FactBatch
    knowledge: KnowledgeProposal
    memory_candidates: list[MemoryCandidate]
    experience_candidates: list[ExperienceCandidate]
    style_evidence: list[StyleEvidence]
    attribution: SpeakerRole
    schema_version: str
    issues: list[str] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "source_version_uid": self.source_version_uid,
            "compile_signature": self.compile_signature,
            "reused": self.reused,
            "knowledge_ops": len(self.knowledge.plan.operations),
            "conflicts": len(self.knowledge.plan.conflicts),
            "memory_candidates": len(self.memory_candidates),
            "experience_candidates": len(self.experience_candidates),
            "style_evidence": len(self.style_evidence),
            "attribution": self.attribution,
            "schema_version": self.schema_version,
            "issues": list(self.issues),
        }


@dataclass
class ExistingPage:
    page_uid: str
    title: str
    page_type: str
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    content_hash: str = ""
    claims: list[str] = field(default_factory=list)


@dataclass
class SourceDocument:
    """Normalized input for ingest (extracted text already available)."""

    text: str
    title: str = ""
    source_version_uid: str = ""
    speaker_role: SpeakerRole = "unknown"
    source_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_version_uid:
            self.source_version_uid = _uid("src")
        if self.source_hash is None:
            self.source_hash = _sha256_text(self.text)


# --- Heuristic extractors (deterministic; no LLM) ---

_DECISION_PAT = re.compile(
    r"(决定|决议|确认采用|我决定|we (?:decided|will)|decision[:：])",
    re.I,
)
_PREFERENCE_PAT = re.compile(
    r"(喜欢|偏好|以后|不要|请用|prefer|please use|from now on)",
    re.I,
)
_PROCEDURE_PAT = re.compile(
    r"(步骤|流程|先.+再|必须先|发布前|checklist|procedure|always)",
    re.I,
)
_STYLE_PAT = re.compile(
    r"(简洁|详细|表格|长段落|语气|concise|brief|table|tone)",
    re.I,
)
_CONFLICT_NEG = re.compile(r"(并非|不是|否定|相反|contradict|not true)", re.I)


def split_paragraphs(text: str) -> list[tuple[str, str]]:
    """Return (locator, paragraph) pairs."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not parts:
        line = text.strip()
        return [("p0", line)] if line else []
    return [(f"p{i}", p) for i, p in enumerate(parts)]


def extract_facts(
    source: SourceDocument,
    *,
    schema_version: str,
) -> FactBatch:
    """Deterministic fact extraction from paragraphs."""
    extraction_uid = _uid("ext")
    facts: list[Fact] = []
    issues: list[str] = []
    role = source.speaker_role

    for locator, para in split_paragraphs(source.text):
        if len(para) < 8:
            continue
        kind: FactKind = "source_fact"
        if _DECISION_PAT.search(para) and role in ("self", "unknown"):
            kind = "user_decision"
        elif _PREFERENCE_PAT.search(para) or _STYLE_PAT.search(para):
            kind = "source_opinion" if role != "self" else "source_fact"

        # Concept names: Capitalized tokens / Chinese noun-ish chunks (simple).
        concepts = _guess_concepts(para, source.title)
        facts.append(
            Fact(
                fact_uid=_uid("fact"),
                claim=para[:500],
                kind=kind,
                concept_names=concepts,
                evidence_refs=[EvidenceRef(locator=locator, excerpt=para[:200])],
                scope="general",
                uncertainties=[],
                speaker_role=role,
            )
        )

    if not facts and source.text.strip():
        facts.append(
            Fact(
                fact_uid=_uid("fact"),
                claim=source.text.strip()[:500],
                kind="source_fact",
                concept_names=_guess_concepts(source.text, source.title),
                evidence_refs=[EvidenceRef(locator="p0", excerpt=source.text[:200])],
                speaker_role=role,
            )
        )
    if not facts:
        issues.append("empty_source")

    return FactBatch(
        schema_version=schema_version,
        source_version_uid=source.source_version_uid,
        extraction_uid=extraction_uid,
        facts=facts,
        issues=issues,
    )


def _guess_concepts(text: str, title: str = "") -> list[str]:
    names: list[str] = []
    if title.strip():
        names.append(title.strip())
    # English-ish Title Case words
    for m in re.finditer(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)\b", text):
        names.append(m.group(1))
    # Simple 「」 or 《》 titles
    for m in re.finditer(r"[「《]([^」》]{2,40})[」》]", text):
        names.append(m.group(1))
    # Dedup preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = _norm_title(n)
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out[:8]


def match_existing_pages(
    facts: list[Fact],
    existing: list[ExistingPage],
) -> dict[str, ExistingPage | None]:
    """Map fact_uid → best existing page (lexical title/alias match) or None."""
    index: dict[str, ExistingPage] = {}
    for page in existing:
        index[_norm_title(page.title)] = page
        for alias in page.aliases:
            index[_norm_title(alias)] = page

    mapping: dict[str, ExistingPage | None] = {}
    for fact in facts:
        hit: ExistingPage | None = None
        for name in fact.concept_names:
            page = index.get(_norm_title(name))
            if page is not None:
                hit = page
                break
        if hit is None:
            # claim start match
            claim_key = _norm_title(fact.claim[:40])
            for key, page in index.items():
                if key and (key in claim_key or claim_key in key):
                    hit = page
                    break
        mapping[fact.fact_uid] = hit
    return mapping


def build_compile_plan(
    fact_batch: FactBatch,
    existing: list[ExistingPage],
    *,
    base_snapshot_id: str = "snap_empty",
    max_pages: int = MAX_PAGES_PER_INGEST,
) -> tuple[CompilePlan, PageDraftBatch]:
    matches = match_existing_pages(fact_batch.facts, existing)
    ops: list[PlanOperation] = []
    drafts: list[PageDraft] = []
    conflicts: list[ConflictItem] = []
    issues: list[str] = []

    # Group by matched page or new concept title
    groups: dict[str, list[Fact]] = {}
    for fact in fact_batch.facts:
        page = matches.get(fact.fact_uid)
        if page is not None:
            key = f"page:{page.page_uid}"
        else:
            title = fact.concept_names[0] if fact.concept_names else fact.claim[:40]
            key = f"new:{_norm_title(title)}"
        groups.setdefault(key, []).append(fact)

    if len(groups) > max_pages:
        issues.append(
            f"page_limit_exceeded:{len(groups)}>{max_pages}; split into batches"
        )
        # Keep first max_pages groups; rest noted
        keys = list(groups.keys())[:max_pages]
        groups = {k: groups[k] for k in keys}

    for key, group_facts in groups.items():
        primary = group_facts[0]
        page = matches.get(primary.fact_uid)
        title = (
            page.title
            if page
            else (primary.concept_names[0] if primary.concept_names else primary.claim[:40])
        )
        page_type = _infer_page_type(primary)
        fact_uids = [f.fact_uid for f in group_facts]
        body = "\n\n".join(f.claim for f in group_facts)
        evidence = [e for f in group_facts for e in f.evidence_refs]

        if page is not None:
            # Conflict if new claim negates existing claim text
            for fact in group_facts:
                for old in page.claims:
                    if _CONFLICT_NEG.search(fact.claim) or (
                        old and _claims_conflict(old, fact.claim)
                    ):
                        conflicts.append(
                            ConflictItem(
                                existing_page_uid=page.page_uid,
                                existing_claim=old,
                                new_fact_uid=fact.fact_uid,
                                new_claim=fact.claim,
                            )
                        )
            op_kind: OpKind = "conflict" if conflicts else "revise"
            draft_uid = _uid("draft")
            ops.append(
                PlanOperation(
                    op=op_kind if op_kind == "conflict" else "revise",
                    page_uid=page.page_uid,
                    draft_uid=draft_uid,
                    title=title,
                    page_type=page.page_type or page_type,
                    reason="update_existing_preferred",
                    fact_uids=fact_uids,
                    baseline_hash=page.content_hash or None,
                )
            )
            drafts.append(
                PageDraft(
                    draft_uid=draft_uid,
                    op=ops[-1].op,
                    title=title,
                    page_type=page.page_type or page_type,
                    summary=primary.claim[:120],
                    body=body,
                    evidence_refs=evidence,
                    baseline_hash=page.content_hash or None,
                    page_uid=page.page_uid,
                )
            )
        else:
            draft_uid = _uid("draft")
            ops.append(
                PlanOperation(
                    op="create",
                    page_uid=None,
                    draft_uid=draft_uid,
                    title=title,
                    page_type=page_type,
                    reason="no_existing_match",
                    fact_uids=fact_uids,
                )
            )
            drafts.append(
                PageDraft(
                    draft_uid=draft_uid,
                    op="create",
                    title=title,
                    page_type=page_type,
                    summary=primary.claim[:120],
                    body=body,
                    evidence_refs=evidence,
                )
            )

    plan = CompilePlan(
        base_snapshot_id=base_snapshot_id,
        source_versions=[fact_batch.source_version_uid],
        operations=ops,
        conflicts=conflicts,
        issues=issues + list(fact_batch.issues),
    )
    batch = PageDraftBatch(plan_uid=plan.plan_uid, operations=drafts)
    return plan, batch


def _infer_page_type(fact: Fact) -> str:
    if fact.kind == "user_decision":
        return "Decision"
    if fact.kind == "model_analysis":
        return "Topic"
    if fact.concept_names and len(fact.concept_names) == 1:
        return "Concept"
    return "Topic"


def _claims_conflict(old: str, new: str) -> bool:
    if not old or not new:
        return False
    # Very light heuristic: both mention same topic word but one has 不是/并非
    return bool(_CONFLICT_NEG.search(new)) and any(
        tok in new for tok in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{4,}", old)[:5]
    )


def derive_memory_candidates(
    fact_batch: FactBatch,
    attribution: SpeakerRole,
) -> list[MemoryCandidate]:
    if attribution not in ("self",):
        # Only self-authored materials produce user memory candidates.
        return []
    out: list[MemoryCandidate] = []
    for fact in fact_batch.facts:
        if fact.speaker_role not in ("self", "unknown"):
            continue
        kind = "semantic"
        tier = "T1"
        origin = "observed"
        if _PREFERENCE_PAT.search(fact.claim) or _STYLE_PAT.search(fact.claim):
            kind = "preference"
            tier = "T0"
            origin = "explicit_user"
        elif _PROCEDURE_PAT.search(fact.claim):
            kind = "procedural"
            tier = "T1"
            origin = "observed"
        elif fact.kind == "user_decision":
            kind = "semantic"
            tier = "T1"
            origin = "explicit_user"
        out.append(
            MemoryCandidate(
                candidate_uid=_uid("memc"),
                kind=kind,
                statement=fact.claim[:300],
                risk_tier=tier,
                origin=origin,
                evidence_refs=list(fact.evidence_refs),
            )
        )
    return out


def derive_experience_candidates(
    fact_batch: FactBatch,
    attribution: SpeakerRole,
) -> list[ExperienceCandidate]:
    out: list[ExperienceCandidate] = []
    if attribution == "unknown":
        # AT-EXP-01 style: unknown attribution must not yield user-habit experience.
        return out
    for fact in fact_batch.facts:
        if not _PROCEDURE_PAT.search(fact.claim) and fact.kind != "user_decision":
            continue
        if attribution == "other":
            # Third-party material: knowledge only — skip user experience.
            continue
        if attribution == "ai" and fact.speaker_role != "self":
            continue
        out.append(
            ExperienceCandidate(
                candidate_uid=_uid("expc"),
                title=(fact.concept_names[0] if fact.concept_names else "procedure")[:80],
                summary=fact.claim[:300],
                attribution=attribution,
                evidence_refs=list(fact.evidence_refs),
                kind="procedure" if _PROCEDURE_PAT.search(fact.claim) else "decision_rule",
            )
        )
    return out


def derive_style_evidence(
    fact_batch: FactBatch,
    attribution: SpeakerRole,
) -> list[StyleEvidence]:
    if attribution != "self":
        return []
    out: list[StyleEvidence] = []
    for fact in fact_batch.facts:
        if fact.speaker_role not in ("self", "unknown"):
            continue
        if not _STYLE_PAT.search(fact.claim) and not _PREFERENCE_PAT.search(fact.claim):
            continue
        m = _STYLE_PAT.search(fact.claim) or _PREFERENCE_PAT.search(fact.claim)
        signal = m.group(0) if m else "preference"
        out.append(
            StyleEvidence(
                evidence_uid=_uid("stye"),
                signal=signal,
                excerpt=fact.claim[:200],
                speaker_role="self",
            )
        )
    return out


# Signature → cached result (process-local; enough for unit tests / single run).
_SIGNATURE_CACHE: dict[str, IngestResult] = {}


def clear_ingest_cache() -> None:
    _SIGNATURE_CACHE.clear()


def ingest_source(
    source: SourceDocument,
    *,
    workspace: Path | None = None,
    schema: SchemaBundle | None = None,
    existing_pages: list[ExistingPage] | None = None,
    base_snapshot_id: str = "snap_empty",
    model_route_version: str = "0",
    evolution_version: str = "0",
    use_cache: bool = True,
) -> IngestResult:
    """Run ingest pipeline; returns four candidate queues + knowledge proposal."""
    if schema is None:
        if workspace is not None:
            schema = load_workspace_schema(workspace)
        else:
            from localwiki.llmwiki.schema import load_default_schema

            schema = load_default_schema()

    extract_hash = _sha256_text(source.text)
    source_hash = source.source_hash or extract_hash
    existing = existing_pages or []
    affected = [p.content_hash for p in existing if p.content_hash]
    sig = compile_signature(
        source_hash=source_hash,
        extract_hash=extract_hash,
        extractor_version=EXTRACTOR_VERSION,
        schema=schema,
        model_route_version=model_route_version,
        evolution_version=evolution_version,
        affected_page_hashes=affected,
    )
    if use_cache and sig in _SIGNATURE_CACHE:
        cached = _SIGNATURE_CACHE[sig]
        # Return a shallow copy flagging reuse
        return IngestResult(
            source_version_uid=cached.source_version_uid,
            extraction_uid=cached.extraction_uid,
            compile_signature=sig,
            reused=True,
            fact_batch=cached.fact_batch,
            knowledge=cached.knowledge,
            memory_candidates=list(cached.memory_candidates),
            experience_candidates=list(cached.experience_candidates),
            style_evidence=list(cached.style_evidence),
            attribution=cached.attribution,
            schema_version=cached.schema_version,
            issues=list(cached.issues) + ["reused_compile_signature"],
        )

    attribution = source.speaker_role
    fact_batch = extract_facts(source, schema_version=schema.version)
    plan, drafts = build_compile_plan(
        fact_batch,
        existing,
        base_snapshot_id=base_snapshot_id,
        max_pages=MAX_PAGES_PER_INGEST,
    )
    knowledge = KnowledgeProposal(plan=plan, drafts=drafts, changeset_status="draft")
    memories = derive_memory_candidates(fact_batch, attribution)
    experiences = derive_experience_candidates(fact_batch, attribution)
    styles = derive_style_evidence(fact_batch, attribution)

    result = IngestResult(
        source_version_uid=source.source_version_uid,
        extraction_uid=fact_batch.extraction_uid,
        compile_signature=sig,
        reused=False,
        fact_batch=fact_batch,
        knowledge=knowledge,
        memory_candidates=memories,
        experience_candidates=experiences,
        style_evidence=styles,
        attribution=attribution,
        schema_version=schema.version,
        issues=list(plan.issues),
    )
    if use_cache:
        _SIGNATURE_CACHE[sig] = result
    return result


def ingest_result_as_dict(result: IngestResult) -> dict[str, Any]:
    """JSON-friendly dump for debugging / contracts (no wiki writes)."""
    return {
        "summary": result.to_summary(),
        "fact_batch": asdict(result.fact_batch),
        "knowledge": {
            "changeset_status": result.knowledge.changeset_status,
            "plan": asdict(result.knowledge.plan),
            "drafts": asdict(result.knowledge.drafts),
        },
        "memory_candidates": [asdict(m) for m in result.memory_candidates],
        "experience_candidates": [asdict(e) for e in result.experience_candidates],
        "style_evidence": [asdict(s) for s in result.style_evidence],
    }
