"""Memory dedup and conflict detection (design §8.3).

Dedup order: normalized statement equality → optional FTS-like token overlap →
optional vector cosine (when embeddings provided). Default cosine threshold 0.88.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, Sequence

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
_CONFLICT_PAIRS = [
    (re.compile(r"详细|长文|长段落|verbose", re.I), re.compile(r"简短|短一点|简洁|短答", re.I)),
    (re.compile(r"表格", re.I), re.compile(r"长段落|纯文字|散文", re.I)),
]


class HasStatement(Protocol):
    statement: str
    subject: str
    kind: str
    applicability: dict[str, Any]
    uid: str


def normalize_statement(text: str) -> str:
    t = text.strip().lower()
    t = _PUNCT.sub("", t)
    return t


def _token_set(text: str) -> set[str]:
    text = text.lower()
    out: set[str] = set()
    for m in re.finditer(r"[\u4e00-\u9fff]+|[a-zA-Z]{3,}|\d+", text):
        chunk = m.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) == 1:
                out.add(chunk)
            else:
                for i in range(len(chunk) - 1):
                    out.add(chunk[i : i + 2])
        else:
            out.add(chunk)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def find_duplicate(
    candidate: Any,
    existing: Sequence[HasStatement],
    *,
    candidate_embedding: Sequence[float] | None = None,
    existing_embeddings: dict[str, Sequence[float]] | None = None,
    cosine_threshold: float = 0.88,
    token_overlap_threshold: float = 0.85,
) -> HasStatement | None:
    """Return existing memory that duplicates candidate, or None."""
    norm = normalize_statement(getattr(candidate, "statement", ""))
    if not norm:
        return None
    cand_tokens = _token_set(getattr(candidate, "statement", ""))

    for mem in existing:
        if normalize_statement(mem.statement) == norm:
            return mem

    # Lexical near-duplicate (FTS stand-in)
    for mem in existing:
        if _jaccard(cand_tokens, _token_set(mem.statement)) >= token_overlap_threshold:
            # Same kind preferred
            if getattr(candidate, "kind", None) and mem.kind != candidate.kind:
                continue
            return mem

    if candidate_embedding and existing_embeddings:
        for mem in existing:
            emb = existing_embeddings.get(mem.uid)
            if emb is None:
                continue
            if cosine_similarity(candidate_embedding, emb) >= cosine_threshold:
                return mem
    return None


def _applicability_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if not a and not b:
        return True
    for key in ("task_types", "contexts", "projects"):
        av = set(a.get(key) or [])
        bv = set(b.get(key) or [])
        if av and bv and av & bv:
            return True
        if av and bv and not (av & bv):
            return False
    return True


def detect_conflicts(
    candidate: Any,
    existing: Sequence[HasStatement],
) -> list[HasStatement]:
    """Find same-subject / overlapping-scope memories that contradict candidate.

    Does not overwrite — caller presents choices: replace / split / keep both.
    """
    stmt = getattr(candidate, "statement", "")
    subject = getattr(candidate, "subject", "user")
    appl = getattr(candidate, "applicability", {}) or {}
    hits: list[HasStatement] = []

    for mem in existing:
        if mem.subject != subject:
            continue
        if getattr(candidate, "kind", None) and mem.kind != candidate.kind:
            continue
        if not _applicability_overlap(appl, mem.applicability or {}):
            continue
        for pos, neg in _CONFLICT_PAIRS:
            if (pos.search(stmt) and neg.search(mem.statement)) or (
                neg.search(stmt) and pos.search(mem.statement)
            ):
                hits.append(mem)
                break
    return hits
