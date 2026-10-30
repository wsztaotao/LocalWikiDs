"""Query and writeback proposals (design §5.5).

Citations must map only to materials actually read in this query.
Writeback produces proposals — never writes wiki/ directly.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ContentKind = Literal["synthesis", "decision", "hypothesis", "none"]
AnswerPartKind = Literal["fact", "analysis", "unconfirmed"]


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class WikiPageSnippet:
    page_uid: str
    title: str
    path: str
    summary: str
    page_type: str = "Concept"
    body: str = ""
    aliases: list[str] = field(default_factory=list)
    category: str = ""


@dataclass
class MemoryHit:
    memory_uid: str
    statement: str
    kind: str = "semantic"


@dataclass
class ExperienceHit:
    experience_uid: str
    title: str
    summary: str


@dataclass
class StyleDirective:
    key: str
    instruction: str


@dataclass
class ReadMaterial:
    """A material actually loaded into the query context."""

    material_uid: str
    kind: str  # wiki_page | memory | experience | style | evidence
    title: str
    content: str
    path: str = ""


@dataclass
class Citation:
    citation_id: str
    material_uid: str
    excerpt: str
    claim_span: str = ""


@dataclass
class AnswerPart:
    kind: AnswerPartKind
    text: str
    citation_ids: list[str] = field(default_factory=list)


@dataclass
class WritebackProposal:
    proposal_uid: str
    page_type: str
    title: str
    content_kind: ContentKind
    summary: str
    body: str
    citation_ids: list[str]
    reason: str
    status: str = "pending"  # pending | skipped


@dataclass
class QueryContext:
    snapshot_id: str
    question: str
    read_materials: list[ReadMaterial]
    budget_tokens_used: int
    budget_tokens_limit: int


@dataclass
class QueryResult:
    snapshot_id: str
    question: str
    answer_parts: list[AnswerPart]
    citations: list[Citation]
    writeback: WritebackProposal | None
    context: QueryContext
    issues: list[str] = field(default_factory=list)

    def citation_material_uids(self) -> set[str]:
        return {c.material_uid for c in self.citations}

    def read_material_uids(self) -> set[str]:
        return {m.material_uid for m in self.context.read_materials}


@dataclass
class WikiSnapshot:
    """Pinned wiki view for one query."""

    snapshot_id: str
    pages: list[WikiPageSnippet]
    memories: list[MemoryHit] = field(default_factory=list)
    experiences: list[ExperienceHit] = field(default_factory=list)
    style_directives: list[StyleDirective] = field(default_factory=list)
    index_categories: dict[str, list[str]] = field(default_factory=dict)


DEFAULT_BUDGET = {
    "system": 800,
    "tools": 400,
    "dialogue": 1200,
    "knowledge": 3000,
    "memory": 800,
    "experience": 600,
    "style": 300,
    "output_reserve": 1000,
    "safety_margin": 200,
}


def estimate_tokens(text: str) -> int:
    # Rough CJK-aware estimate: ~1.5 chars per token for mixed text.
    if not text:
        return 0
    return max(1, int(len(text) / 1.5))


def pin_snapshot(
    pages: list[WikiPageSnippet],
    *,
    memories: list[MemoryHit] | None = None,
    experiences: list[ExperienceHit] | None = None,
    style_directives: list[StyleDirective] | None = None,
    snapshot_id: str | None = None,
) -> WikiSnapshot:
    sid = snapshot_id or _uid("snap")
    categories: dict[str, list[str]] = {}
    for p in pages:
        cat = p.category or p.page_type or "misc"
        categories.setdefault(cat, []).append(p.page_uid)
    return WikiSnapshot(
        snapshot_id=sid,
        pages=list(pages),
        memories=list(memories or []),
        experiences=list(experiences or []),
        style_directives=list(style_directives or []),
        index_categories=categories,
    )


def _score_page(question: str, page: WikiPageSnippet) -> float:
    q = _norm(question)
    score = 0.0
    title = _norm(page.title)
    if title and title in q:
        score += 5.0
    if q and any(tok and tok in title for tok in q.split() if len(tok) > 1):
        score += 2.0
    blob = _norm(f"{page.summary} {page.body}")
    for tok in set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", q)):
        if tok in blob:
            score += 1.0
        if tok in title:
            score += 1.5
    for alias in page.aliases:
        if _norm(alias) in q:
            score += 3.0
    return score


def retrieve_candidates(
    snapshot: WikiSnapshot,
    question: str,
    *,
    limit: int = 10,
) -> list[WikiPageSnippet]:
    ranked = sorted(
        snapshot.pages,
        key=lambda p: _score_page(question, p),
        reverse=True,
    )
    scored = [p for p in ranked if _score_page(question, p) > 0]
    if not scored:
        # Fall back to first pages for empty index behavior — still "read".
        return ranked[: min(3, limit)]
    return scored[:limit]


def assemble_context(
    snapshot: WikiSnapshot,
    question: str,
    *,
    budget: dict[str, int] | None = None,
) -> tuple[list[ReadMaterial], int, int]:
    """Assemble read materials within token budget. Returns (materials, used, limit)."""
    b = dict(DEFAULT_BUDGET)
    if budget:
        b.update(budget)
    limit = sum(b.values())
    used = b["system"] + b["tools"] + b["dialogue"] + b["output_reserve"] + b["safety_margin"]
    materials: list[ReadMaterial] = []

    def _try_add(mat: ReadMaterial, bucket: str) -> bool:
        nonlocal used
        cost = estimate_tokens(mat.content)
        # Soft bucket: allow if remaining total budget permits.
        remaining = limit - used
        if cost > remaining:
            return False
        materials.append(mat)
        used += cost
        return True

    for page in retrieve_candidates(snapshot, question):
        content = page.body or page.summary or page.title
        _try_add(
            ReadMaterial(
                material_uid=page.page_uid,
                kind="wiki_page",
                title=page.title,
                content=content,
                path=page.path or page.page_uid,
            ),
            "knowledge",
        )

    for mem in snapshot.memories:
        if any(tok in _norm(mem.statement) for tok in _norm(question).split() if len(tok) > 1) or True:
            # Include all provided memories for MVP packing; budget still caps.
            _try_add(
                ReadMaterial(
                    material_uid=mem.memory_uid,
                    kind="memory",
                    title=mem.kind,
                    content=mem.statement,
                ),
                "memory",
            )

    for exp in snapshot.experiences:
        _try_add(
            ReadMaterial(
                material_uid=exp.experience_uid,
                kind="experience",
                title=exp.title,
                content=exp.summary,
            ),
            "experience",
        )

    for sty in snapshot.style_directives:
        _try_add(
            ReadMaterial(
                material_uid=f"style:{sty.key}",
                kind="style",
                title=sty.key,
                content=sty.instruction,
            ),
            "style",
        )

    return materials, used, limit


_DECISION_RE = re.compile(r"(我决定|决定采用|确认|we decided|I decide)", re.I)
_COMPARE_RE = re.compile(r"(对比|比较|综合|vs\.?|compared|versus)", re.I)
_HYPOTHESIS_RE = re.compile(r"(可能|也许|推测|maybe|perhaps|hypothesis)", re.I)


def _make_citation(mat: ReadMaterial, claim: str) -> Citation:
    excerpt = mat.content[:180]
    return Citation(
        citation_id=_uid("cite"),
        material_uid=mat.material_uid,
        excerpt=excerpt,
        claim_span=claim[:120],
    )


def generate_answer(
    question: str,
    materials: list[ReadMaterial],
) -> tuple[list[AnswerPart], list[Citation]]:
    """Deterministic answer builder; citations only from ``materials``."""
    citations: list[Citation] = []
    parts: list[AnswerPart] = []
    wiki_mats = [m for m in materials if m.kind == "wiki_page"]
    if not wiki_mats and not materials:
        parts.append(
            AnswerPart(
                kind="unconfirmed",
                text="当前快照中没有可读材料，无法给出有依据的回答。",
                citation_ids=[],
            )
        )
        return parts, citations

    # Fact parts from wiki pages
    cite_ids: list[str] = []
    for mat in wiki_mats[:5]:
        c = _make_citation(mat, mat.content[:80])
        citations.append(c)
        cite_ids.append(c.citation_id)
        parts.append(
            AnswerPart(
                kind="fact",
                text=f"根据《{mat.title}》：{mat.content[:200]}",
                citation_ids=[c.citation_id],
            )
        )

    # Analysis if multiple sources
    if len(wiki_mats) >= 2:
        joined = "；".join(m.title for m in wiki_mats[:3])
        # Cite all used pages
        ids = []
        for mat in wiki_mats[:3]:
            # Reuse existing citation for same material if present
            existing = next((c for c in citations if c.material_uid == mat.material_uid), None)
            if existing:
                ids.append(existing.citation_id)
            else:
                c = _make_citation(mat, mat.title)
                citations.append(c)
                ids.append(c.citation_id)
        parts.append(
            AnswerPart(
                kind="analysis",
                text=f"综合 {joined} 的内容，可形成跨资料比较结论。",
                citation_ids=ids,
            )
        )

    # Memory / style as unconfirmed soft context (still cited if used)
    for mat in materials:
        if mat.kind == "memory":
            c = _make_citation(mat, mat.content[:80])
            citations.append(c)
            parts.append(
                AnswerPart(
                    kind="unconfirmed",
                    text=f"相关记忆：{mat.content[:160]}",
                    citation_ids=[c.citation_id],
                )
            )
            break

    return parts, citations


def propose_writeback(
    question: str,
    answer_parts: list[AnswerPart],
    citations: list[Citation],
    materials: list[ReadMaterial],
) -> WritebackProposal | None:
    """Decide whether to propose saving the answer as a wiki page."""
    text_blob = "\n".join(p.text for p in answer_parts)
    cite_ids = [c.citation_id for c in citations]

    # Model answer cannot self-certify: need at least one non-answer material citation.
    if not citations:
        return WritebackProposal(
            proposal_uid=_uid("wb"),
            page_type="Question",
            title=question[:80] or "未命名问题",
            content_kind="hypothesis",
            summary="无引用，不写回为正式结论",
            body=text_blob,
            citation_ids=[],
            reason="no_citations",
            status="skipped",
        )

    read_uids = {m.material_uid for m in materials}
    for c in citations:
        if c.material_uid not in read_uids:
            raise ValueError(
                f"citation material_uid {c.material_uid!r} not in read materials"
            )

    if _DECISION_RE.search(question) or _DECISION_RE.search(text_blob):
        return WritebackProposal(
            proposal_uid=_uid("wb"),
            page_type="Decision",
            title=f"决定：{question[:60]}",
            content_kind="decision",
            summary=text_blob[:160],
            body=text_blob,
            citation_ids=cite_ids,
            reason="user_decision_detected",
            status="pending",
        )

    wiki_count = sum(1 for m in materials if m.kind == "wiki_page")
    if wiki_count >= 2 and (
        _COMPARE_RE.search(question)
        or _COMPARE_RE.search(text_blob)
        or any(p.kind == "analysis" for p in answer_parts)
    ):
        return WritebackProposal(
            proposal_uid=_uid("wb"),
            page_type="Topic",
            title=f"综合：{question[:60]}",
            content_kind="synthesis",
            summary=text_blob[:160],
            body=text_blob,
            citation_ids=cite_ids,
            reason="cross_source_synthesis",
            status="pending",
        )

    if _HYPOTHESIS_RE.search(text_blob) and wiki_count < 1:
        return WritebackProposal(
            proposal_uid=_uid("wb"),
            page_type="Question",
            title=question[:80],
            content_kind="hypothesis",
            summary=text_blob[:160],
            body=text_blob,
            citation_ids=cite_ids,
            reason="unverified_hypothesis",
            status="pending",
        )

    # Duplicate / no new info: skip writeback
    return WritebackProposal(
        proposal_uid=_uid("wb"),
        page_type="Topic",
        title=question[:80],
        content_kind="none",
        summary="",
        body="",
        citation_ids=cite_ids,
        reason="no_new_information",
        status="skipped",
    )


def validate_citations(result: QueryResult) -> list[str]:
    """Return issues if any citation points outside read materials."""
    read = result.read_material_uids()
    issues: list[str] = []
    for c in result.citations:
        if c.material_uid not in read:
            issues.append(f"orphan_citation:{c.citation_id}->{c.material_uid}")
    for part in result.answer_parts:
        for cid in part.citation_ids:
            if not any(c.citation_id == cid for c in result.citations):
                issues.append(f"missing_citation_obj:{cid}")
    return issues


def query_wiki(
    question: str,
    snapshot: WikiSnapshot,
    *,
    budget: dict[str, int] | None = None,
) -> QueryResult:
    """Run query against a pinned snapshot; optionally propose writeback."""
    materials, used, limit = assemble_context(snapshot, question, budget=budget)
    parts, citations = generate_answer(question, materials)
    writeback = propose_writeback(question, parts, citations, materials)
    ctx = QueryContext(
        snapshot_id=snapshot.snapshot_id,
        question=question,
        read_materials=materials,
        budget_tokens_used=used,
        budget_tokens_limit=limit,
    )
    result = QueryResult(
        snapshot_id=snapshot.snapshot_id,
        question=question,
        answer_parts=parts,
        citations=citations,
        writeback=writeback,
        context=ctx,
    )
    result.issues = validate_citations(result)
    return result


def load_pages_from_wiki_dir(wiki_dir: Path) -> list[WikiPageSnippet]:
    """Minimal loader: read *.md under wiki/ (except index/log) as snippets."""
    wiki_dir = Path(wiki_dir)
    pages: list[WikiPageSnippet] = []
    if not wiki_dir.is_dir():
        return pages
    for path in sorted(wiki_dir.rglob("*.md")):
        if path.name in ("index.md", "log.md"):
            continue
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(wiki_dir)).replace("\\", "/")
        title = path.stem
        m = re.search(r"^title:\s*(.+)$", text, re.M)
        if m:
            title = m.group(1).strip().strip("\"'")
        page_type = "Concept"
        tm = re.search(r"^type:\s*(.+)$", text, re.M)
        if tm:
            page_type = tm.group(1).strip()
        pages.append(
            WikiPageSnippet(
                page_uid=rel[:-3] if rel.endswith(".md") else rel,
                title=title,
                path=rel,
                summary=text[:200],
                page_type=page_type,
                body=text,
                category=path.parent.name,
            )
        )
    return pages


def query_result_as_dict(result: QueryResult) -> dict[str, Any]:
    return asdict(result)
