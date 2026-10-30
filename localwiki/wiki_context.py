"""Retrieve LocalWiki pages for agent prompt injection (R3.10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from localwiki.tools.native_workspace import fts_search

DEFAULT_LIMIT = 5
DEFAULT_MAX_CHARS = 6000
PER_PAGE_CHARS = 1500


def retrieve_wiki_context(
    workspace: Path | str,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """FTS top-k + truncated page bodies for prompt injection."""
    root = Path(workspace).resolve()
    hits_raw = fts_search(root, query, limit=limit)
    hits: list[dict[str, Any]] = []
    chunks: list[str] = []
    used = 0
    wiki = root / "wiki"
    for h in hits_raw:
        path = str(h.get("path") or "")
        if not path:
            continue
        title = str(h.get("title") or Path(path).stem)
        snippet = str(h.get("snippet") or "")
        body = ""
        fp = wiki / path
        if fp.is_file():
            try:
                body = fp.read_text(encoding="utf-8")
            except OSError:
                body = ""
        if len(body) > PER_PAGE_CHARS:
            body = body[:PER_PAGE_CHARS] + "…"
        piece = f"### {path} · {title}\n{body or snippet}\n"
        if used + len(piece) > max_chars and chunks:
            break
        if used + len(piece) > max_chars:
            remain = max_chars - used
            if remain < 80:
                break
            piece = piece[:remain] + "…"
        chunks.append(piece)
        used += len(piece)
        hits.append({"path": path, "title": title, "snippet": snippet})

    hit = bool(hits)
    context_text = ""
    if hit:
        context_text = (
            "【本地知识库摘录】\n"
            "优先依据下列摘录回答；摘录不足时可补充通识，但勿谎称读过未提供的全文。\n\n"
            + "\n".join(chunks)
        )
    return {
        "hits": hits,
        "context_text": context_text,
        "hit": hit,
        "paths": [h["path"] for h in hits],
    }


def build_prompt_with_wiki(user_or_compact: str, wiki: dict[str, Any]) -> str:
    user = (user_or_compact or "").strip()
    ctx = (wiki.get("context_text") or "").strip()
    if not ctx:
        # R3.15: do not force a "wiki miss" opening line (pollutes creative/export tasks).
        return f"【用户问题】\n{user}"
    return f"{ctx}\n\n【用户问题】\n{user}"
