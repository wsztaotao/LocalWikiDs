"""Wiki ingest gate: dedup + related bidirectional wikilinks (R3.22)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from localwiki.core import PageOp, file_sha256
from localwiki.llmwiki.lint import parse_front_matter

E_DEDUP_BLOCK = "E_DEDUP_BLOCK"

BODY_DUP = 0.85
BODY_AMB_LO = 0.70
RELATED_LO = 0.40
RELATED_TOP_K = 3
MAX_PAGE_TOUCH = 15

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)
_WIKILINK_PATH_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")

TOPIC_PREFIXES = (
    "topics/",
    "concepts/",
    "entities/",
    "decisions/",
    "questions/",
    "memories/",
)
NAV_NAMES = {"index.md", "log.md"}

SemanticJudge = Callable[[dict[str, Any]], dict[str, Any]]


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _token_set(text: str) -> set[str]:
    text = (text or "").lower()
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


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def is_nav(path: str) -> bool:
    p = path.replace("\\", "/").lstrip("/")
    name = Path(p).name
    return name in NAV_NAMES or p.endswith("/index.md")


def is_source_page(path: str) -> bool:
    p = path.replace("\\", "/").lstrip("/")
    return p.startswith("references/") and not p.startswith("references/assets/")


def is_topic_like(path: str, *, content: str | None = None) -> bool:
    p = path.replace("\\", "/").lstrip("/")
    if is_nav(p) or is_source_page(p) or p.startswith("references/assets/"):
        return False
    if content:
        meta, _ = parse_front_matter(content)
        if meta.get("redirect_to"):
            return False
    return any(p.startswith(pref) for pref in TOPIC_PREFIXES)


def is_protected_content(content: str | None) -> bool:
    if not content:
        return False
    meta, _ = parse_front_matter(content)
    if meta.get("secret") or meta.get("locked") is True:
        return True
    # privacy flag
    priv = meta.get("privacy") or meta.get("kw")
    if isinstance(priv, dict) and priv.get("secret"):
        return True
    if "secret:" in (content or "")[:400] and re.search(
        r"(?m)^secret:\s*", content or ""
    ):
        return True
    return False


def extract_title_body(content: str | None, *, fallback_path: str = "") -> tuple[str, str, list[str]]:
    raw = content or ""
    meta, body = parse_front_matter(raw)
    title = str(meta.get("title") or "").strip()
    if not title:
        m = _TITLE_RE.search(body) or _TITLE_RE.search(raw)
        title = (m.group(1).strip() if m else "") or Path(fallback_path).stem
    aliases: list[str] = []
    raw_aliases = meta.get("aliases") or []
    if isinstance(raw_aliases, str):
        aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]
    elif isinstance(raw_aliases, list):
        aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
    return title, body, aliases


@dataclass
class MatchTarget:
    path: str
    title: str
    score: float


@dataclass
class MatchResult:
    kind: str  # duplicate | related | none | ambiguous
    confidence: str  # high | low
    tier: str | None
    targets: list[MatchTarget] = field(default_factory=list)
    reason: str = ""


@dataclass
class GateAction:
    action: str
    path: str
    target: str | None = None
    tier: str | None = None
    confidence: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": self.path,
            "target": self.target,
            "tier": self.tier,
            "confidence": self.confidence,
            "detail": self.detail,
        }


def _list_existing_pages(root: Path) -> list[dict[str, Any]]:
    wiki = root / "wiki"
    out: list[dict[str, Any]] = []
    if not wiki.is_dir():
        return out
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki).as_posix()
        if is_nav(rel):
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title, body, aliases = extract_title_body(raw, fallback_path=rel)
        meta, _ = parse_front_matter(raw)
        out.append(
            {
                "path": rel,
                "title": title,
                "body": body,
                "aliases": aliases,
                "raw": raw,
                "protected": is_protected_content(raw),
                "redirect_to": meta.get("redirect_to"),
            }
        )
    return out


def match_candidate(
    root: Path | str,
    *,
    title: str,
    path: str,
    body: str,
    aliases: Sequence[str] | None = None,
    existing: list[dict[str, Any]] | None = None,
    semantic_judge: SemanticJudge | None = None,
) -> MatchResult:
    """Three-tier match per R3.22 spec §4.2."""
    root = Path(root).resolve()
    pages = existing if existing is not None else _list_existing_pages(root)
    path_n = path.replace("\\", "/").lstrip("/")
    cand_title = normalize_title(title)
    cand_aliases = {normalize_title(a) for a in (aliases or []) if a}
    cand_keys = {cand_title} | cand_aliases
    cand_keys.discard("")
    cand_body_tokens = _token_set((body or "")[:4000])
    cand_mix_tokens = _token_set(f"{title}\n{(body or '')[:4000]}")

    # Tier 1: title / alias
    title_hits: list[MatchTarget] = []
    for pg in pages:
        if pg["path"] == path_n:
            continue
        if pg.get("redirect_to") or not is_topic_like(pg["path"], content=pg.get("raw")):
            continue
        keys = {normalize_title(pg["title"])} | {
            normalize_title(a) for a in (pg.get("aliases") or [])
        }
        keys.discard("")
        if cand_keys & keys:
            title_hits.append(MatchTarget(pg["path"], pg["title"], 1.0))
    if title_hits:
        # Prefer topic-like targets
        title_hits.sort(
            key=lambda t: (0 if is_topic_like(t.path) else 1, -t.score, t.path)
        )
        return MatchResult(
            kind="duplicate",
            confidence="high",
            tier="title",
            targets=title_hits[:5],
            reason="normalized title/alias match",
        )

    # Tier 2: body similarity (topic-like only); score by body-first, mix as tie-break
    if not is_topic_like(path_n) and not any(
        is_topic_like(p["path"], content=p.get("raw")) for p in pages
    ):
        return MatchResult(kind="none", confidence="high", tier=None, reason="no topic pages")

    scored: list[MatchTarget] = []
    for pg in pages:
        if pg["path"] == path_n:
            continue
        if pg.get("redirect_to") or not is_topic_like(pg["path"], content=pg.get("raw")):
            continue
        pg_body = (pg.get("body") or "")[:4000]
        body_score = jaccard(cand_body_tokens, _token_set(pg_body))
        mix_score = jaccard(
            cand_mix_tokens, _token_set(f"{pg['title']}\n{pg_body}")
        )
        score = max(body_score, mix_score)
        if score >= RELATED_LO:
            scored.append(MatchTarget(pg["path"], pg["title"], score))
    scored.sort(key=lambda t: (-t.score, t.path))

    if not scored:
        return MatchResult(kind="none", confidence="high", tier="body", reason="no similar pages")

    best = scored[0]
    if best.score >= BODY_DUP:
        return MatchResult(
            kind="duplicate",
            confidence="high",
            tier="body",
            targets=scored[:5],
            reason=f"body jaccard={best.score:.3f}",
        )
    if best.score >= BODY_AMB_LO:
        # Tier 3: optional model
        if semantic_judge is not None:
            try:
                verdict = semantic_judge(
                    {
                        "title": title,
                        "path": path_n,
                        "body": body[:3000],
                        "candidates": [
                            {"path": t.path, "title": t.title, "score": t.score}
                            for t in scored[:5]
                        ],
                    }
                )
            except Exception:  # noqa: BLE001
                verdict = {}
            v = str((verdict or {}).get("verdict") or "").lower()
            canon = (verdict or {}).get("canonical_path") or best.path
            if v == "duplicate":
                return MatchResult(
                    kind="duplicate",
                    confidence="high",
                    tier="model",
                    targets=[MatchTarget(str(canon), best.title, best.score)] + scored[1:5],
                    reason="model verdict=duplicate",
                )
            if v == "related":
                links = (verdict or {}).get("link_paths") or [best.path]
                targets = [
                    MatchTarget(str(p), next((s.title for s in scored if s.path == p), p), 0.5)
                    for p in links
                ]
                return MatchResult(
                    kind="related",
                    confidence="high",
                    tier="model",
                    targets=targets or scored[:RELATED_TOP_K],
                    reason="model verdict=related",
                )
            if v == "distinct":
                return MatchResult(
                    kind="none",
                    confidence="high",
                    tier="model",
                    targets=[],
                    reason="model verdict=distinct",
                )
        return MatchResult(
            kind="ambiguous",
            confidence="low",
            tier="body",
            targets=scored[:5],
            reason=f"ambiguous jaccard={best.score:.3f}",
        )
    # related band
    return MatchResult(
        kind="related",
        confidence="high",
        tier="body",
        targets=scored[:RELATED_TOP_K],
        reason=f"related jaccard={best.score:.3f}",
    )


def ensure_related_section(content: str, link_md: str) -> str:
    """Append a wikilink under ## 相关 if missing (idempotent)."""
    link_md = (link_md or "").strip()
    if not link_md:
        return content
    # Extract target path from [[path|title]] or [[path]]
    m = _WIKILINK_PATH_RE.search(link_md)
    target_key = (m.group(1).strip() if m else link_md).replace("\\", "/")
    if target_key and target_key in content:
        # already linked (path substring); still check exact wiki link
        for raw in _WIKILINK_PATH_RE.findall(content):
            if raw.strip().replace("\\", "/") == target_key:
                return content

    section_header = "## 相关"
    line = f"- {link_md}" if not link_md.startswith("-") else link_md
    if section_header in content:
        # insert before end of file after section
        parts = content.split(section_header, 1)
        head, rest = parts[0], parts[1]
        # rest starts with optional newline then section body
        return head + section_header + "\n" + line + "\n" + rest.lstrip("\n")
    # append section
    base = content.rstrip() + "\n\n"
    return f"{base}{section_header}\n\n{line}\n"


def _wikilink(path: str, title: str) -> str:
    p = path.replace("\\", "/").lstrip("/")
    t = (title or Path(p).stem).strip()
    return f"[[{p}|{t}]]"


def _op_key(op: PageOp) -> str:
    return op.path.replace("\\", "/").lstrip("/")


def _merge_ops_by_path(ops: list[PageOp]) -> list[PageOp]:
    """Last write wins per path; preserve relative order of first appearance."""
    order: list[str] = []
    by_path: dict[str, PageOp] = {}
    for op in ops:
        k = _op_key(op)
        if k not in by_path:
            order.append(k)
        by_path[k] = op
    return [by_path[k] for k in order]


def _content_map_from_ops(
    root: Path, ops: Sequence[PageOp]
) -> dict[str, str]:
    """Effective content after applying ops in order (create/update only)."""
    wiki = root / "wiki"
    state: dict[str, str] = {}
    for op in ops:
        k = _op_key(op)
        if op.op == "delete":
            state.pop(k, None)
            continue
        if op.content is not None:
            state[k] = op.content
        elif k not in state:
            p = wiki / k
            if p.exists():
                state[k] = p.read_text(encoding="utf-8", errors="replace")
    return state


def _read_page(root: Path, path: str, overlay: dict[str, str]) -> str | None:
    k = path.replace("\\", "/").lstrip("/")
    if k in overlay:
        return overlay[k]
    p = root / "wiki" / k
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return None


def _make_update(root: Path, path: str, content: str, overlay: dict[str, str]) -> PageOp:
    k = path.replace("\\", "/").lstrip("/")
    target = root / "wiki" / k
    if target.exists() or k in overlay:
        base = file_sha256(target) if target.exists() else None
        # If only in overlay (new in batch), still create
        if not target.exists():
            return PageOp(op="create", path=k, content=content)
        return PageOp(op="update", path=k, content=content, base_sha256=base)
    return PageOp(op="create", path=k, content=content)


def transform_ops(
    root: Path | str,
    ops: Sequence[PageOp] | Sequence[dict[str, Any]],
    *,
    semantic_judge: SemanticJudge | None = None,
) -> tuple[list[PageOp], dict[str, Any]]:
    """Rewrite wiki ops: drop duplicate topic creates; inject bidirectional links."""
    root = Path(root).resolve()
    parsed = [o if isinstance(o, PageOp) else PageOp.from_dict(o) for o in ops]
    actions: list[GateAction] = []
    needs_review = False
    existing = _list_existing_pages(root)
    existing_by_path = {p["path"]: p for p in existing}

    # Track topic creates already kept in this batch (first wins)
    kept_topic_titles: dict[str, str] = {}  # norm title -> path
    out: list[PageOp] = []
    pending_links: list[tuple[str, str, str, str]] = []
    # (from_path, to_path, from_title, to_title)

    source_paths_in_batch = [
        _op_key(o)
        for o in parsed
        if o.op in ("create", "update") and is_source_page(_op_key(o))
    ]

    touch_budget = MAX_PAGE_TOUCH

    for op in parsed:
        path = _op_key(op)
        if op.op == "delete" or is_nav(path) or path.startswith("references/assets/"):
            out.append(op)
            continue
        if op.op not in ("create", "update") or op.content is None:
            out.append(op)
            continue

        if op.op == "update" or not is_topic_like(path):
            out.append(op)
            # Still allow related linking for new source pages
            if op.op == "create" and is_source_page(path):
                title, body, aliases = extract_title_body(op.content, fallback_path=path)
                # match related topics to link source ↔ topics
                mr = match_candidate(
                    root,
                    title=title,
                    path=path,
                    body=body,
                    aliases=aliases,
                    existing=existing,
                    semantic_judge=semantic_judge,
                )
                if mr.kind in ("related", "duplicate") and mr.targets:
                    for t in mr.targets[:RELATED_TOP_K]:
                        pending_links.append((path, t.path, title, t.title))
                        pending_links.append((t.path, path, t.title, title))
                        actions.append(
                            GateAction(
                                action="inject_link",
                                path=path,
                                target=t.path,
                                tier=mr.tier,
                                confidence=mr.confidence,
                                detail="source_to_topic",
                            )
                        )
            continue

        # create + topic-like
        title, body, aliases = extract_title_body(op.content, fallback_path=path)
        norm = normalize_title(title)

        # Batch-local title dedup
        if norm and norm in kept_topic_titles:
            canon = kept_topic_titles[norm]
            actions.append(
                GateAction(
                    action="drop_create",
                    path=path,
                    target=canon,
                    tier="title",
                    confidence="high",
                    detail="batch_title_dup",
                )
            )
            for sp in source_paths_in_batch:
                pending_links.append((sp, canon, Path(sp).stem, title))
                pending_links.append((canon, sp, title, Path(sp).stem))
            continue

        mr = match_candidate(
            root,
            title=title,
            path=path,
            body=body,
            aliases=aliases,
            existing=existing,
            semantic_judge=semantic_judge,
        )

        if mr.kind == "duplicate" and mr.confidence == "high" and mr.targets:
            canon = mr.targets[0].path
            actions.append(
                GateAction(
                    action="drop_create",
                    path=path,
                    target=canon,
                    tier=mr.tier,
                    confidence="high",
                    detail=mr.reason,
                )
            )
            for sp in source_paths_in_batch:
                pending_links.append((sp, canon, Path(sp).stem, mr.targets[0].title))
                pending_links.append((canon, sp, mr.targets[0].title, Path(sp).stem))
            # Also link dropped topic intent to canon via sources only (Q4)
            continue

        if mr.kind == "ambiguous" or (mr.kind == "duplicate" and mr.confidence == "low"):
            needs_review = True
            out.append(op)
            if norm:
                kept_topic_titles[norm] = path
            actions.append(
                GateAction(
                    action="propose",
                    path=path,
                    target=mr.targets[0].path if mr.targets else None,
                    tier=mr.tier,
                    confidence="low",
                    detail=mr.reason,
                )
            )
            continue

        # keep create
        out.append(op)
        if norm:
            kept_topic_titles[norm] = path
        # Add to existing overlay for subsequent matches
        existing.append(
            {
                "path": path,
                "title": title,
                "body": body,
                "aliases": list(aliases),
                "raw": op.content,
                "protected": is_protected_content(op.content),
            }
        )
        existing_by_path[path] = existing[-1]

        if mr.kind == "related" and mr.targets:
            for t in mr.targets[:RELATED_TOP_K]:
                pending_links.append((path, t.path, title, t.title))
                pending_links.append((t.path, path, t.title, title))
                actions.append(
                    GateAction(
                        action="inject_link",
                        path=path,
                        target=t.path,
                        tier=mr.tier,
                        confidence=mr.confidence,
                        detail=mr.reason,
                    )
                )

    # Apply bidirectional links
    overlay = _content_map_from_ops(root, out)
    link_ops: list[PageOp] = []
    seen_pair: set[tuple[str, str]] = set()
    for from_p, to_p, from_title, to_title in pending_links:
        if from_p == to_p:
            continue
        key = (from_p, to_p)
        if key in seen_pair:
            continue
        seen_pair.add(key)
        if touch_budget <= 0:
            break
        raw = _read_page(root, from_p, overlay)
        if raw is None:
            continue
        if is_protected_content(raw) and from_p not in overlay:
            # Don't silently edit protected on-disk pages that aren't in this batch
            actions.append(
                GateAction(
                    action="propose",
                    path=from_p,
                    target=to_p,
                    confidence="low",
                    detail="protected_skip_silent_link",
                )
            )
            needs_review = True
            continue
        # If target is protected and we're editing the other side only — OK for from_p
        new_c = ensure_related_section(raw, _wikilink(to_p, to_title))
        if new_c == raw:
            continue
        op = _make_update(root, from_p, new_c, overlay)
        link_ops.append(op)
        overlay[from_p] = new_c
        touch_budget -= 1

    merged = _merge_ops_by_path(out + link_ops)
    gate_meta = {
        "transformed": bool(actions) or merged != parsed,
        "needs_review": needs_review,
        "actions": [a.to_dict() for a in actions],
    }
    return merged, gate_meta


def verify_ops(
    root: Path | str,
    ops: Sequence[PageOp] | Sequence[dict[str, Any]],
    *,
    semantic_judge: SemanticJudge | None = None,
) -> None:
    """Raise CommitError-compatible exception if high-confidence duplicate create remains."""
    from localwiki.core import CommitError

    root = Path(root).resolve()
    parsed = [o if isinstance(o, PageOp) else PageOp.from_dict(o) for o in ops]
    existing = _list_existing_pages(root)
    for op in parsed:
        if op.op != "create" or op.content is None:
            continue
        path = _op_key(op)
        if not is_topic_like(path):
            continue
        title, body, aliases = extract_title_body(op.content, fallback_path=path)
        mr = match_candidate(
            root,
            title=title,
            path=path,
            body=body,
            aliases=aliases,
            existing=existing,
            semantic_judge=semantic_judge,
        )
        if mr.kind == "duplicate" and mr.confidence == "high":
            raise CommitError(
                E_DEDUP_BLOCK,
                f"duplicate topic create blocked: {path} ~ {mr.targets[0].path}",
            )
