"""R3.28 K7 knowledge-ingest skill loader + JSON contract parse."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SKILL_NAME = "knowledge-ingest"
SKILL_REL = Path(".kw") / "skills" / "knowledge-ingest" / "SKILL.md"

_PACKAGED = Path(__file__).resolve().parent / "skills_templates" / "knowledge-ingest" / "SKILL.md"

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)


def skill_path(root: Path | str | None = None) -> Path:
    if root is not None:
        p = Path(root).resolve() / SKILL_REL
        if p.is_file():
            return p
    if _PACKAGED.is_file():
        return _PACKAGED
    if root is not None:
        return Path(root).resolve() / SKILL_REL
    return _PACKAGED


def load_skill_text(root: Path | str | None = None) -> str:
    path = skill_path(root)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    # Minimal fallback if neither workspace nor package template exists
    return (
        "You are LocalWiki knowledge-ingest. "
        "Return JSON with summary, main_content, key_points, quotes, related, pages."
    )


def list_existing_titles(root: Path | str, *, limit: int = 80) -> list[dict[str, str]]:
    """Short catalog for prompt injection (path + title)."""
    root = Path(root).resolve()
    wiki = root / "wiki"
    out: list[dict[str, str]] = []
    if not wiki.is_dir():
        return out
    skip = {"index.md", "log.md"}
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki).as_posix()
        if rel in skip or "assets/" in rel:
            continue
        title = p.stem
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            for ln in text.splitlines()[:40]:
                s = ln.strip()
                if s.startswith("title:"):
                    title = s.split(":", 1)[1].strip().strip("\"'") or title
                    break
                if s.startswith("# "):
                    title = s[2:].strip() or title
                    break
        except OSError:
            pass
        out.append({"path": rel, "title": title})
        if len(out) >= limit:
            break
    return out


def build_compile_messages(
    *,
    root: Path | str | None,
    text: str,
    meta: dict[str, Any],
    source_uid: str,
    truncated: bool = False,
) -> list[dict[str, str]]:
    skill = load_skill_text(root)
    catalog = []
    if root is not None:
        catalog = list_existing_titles(root)
    catalog_lines = "\n".join(
        f"- [[{c['path']}]] {c['title']}" for c in catalog
    ) or "(empty wiki)"
    system = (
        f"{skill}\n\n"
        "Follow the JSON contract exactly. "
        "Prefer relating to existing pages listed below over duplicate titles.\n\n"
        f"## Existing wiki pages\n{catalog_lines}\n"
    )
    user = (
        f"Source title: {meta.get('title') or source_uid}\n"
        f"Filename: {meta.get('filename')}\n"
        f"Format: {meta.get('format')}\n"
        f"Truncated: {truncated}\n\n"
        f"--- FULL TEXT ---\n{text}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _as_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def _as_quotes(v: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(v, list):
        return out
    for item in v:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "text": text,
                    "note": str(item.get("note") or "").strip(),
                }
            )
        elif isinstance(item, str) and item.strip():
            out.append({"text": item.strip(), "note": ""})
    return out


def _as_related(v: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(v, list):
        return out
    for item in v:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title and not item.get("wikilink"):
            continue
        out.append(
            {
                "title": title or str(item.get("wikilink") or ""),
                "relation": str(item.get("relation") or "补充").strip() or "补充",
                "wikilink": str(item.get("wikilink") or "").strip(),
            }
        )
    return out


def parse_ingest_payload(raw: str) -> dict[str, Any]:
    """Parse model output into K7 contract; loose mode for legacy summary+pages."""
    text = (raw or "").strip()
    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    data: Any = None
    mode = "json"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        mode = "loose"
        data = None

    if isinstance(data, dict) and (
        data.get("pages") or data.get("summary") or data.get("key_points")
    ):
        pages_in = data.get("pages") or []
        pages: list[dict[str, Any]] = []
        if isinstance(pages_in, list):
            for p in pages_in:
                if not isinstance(p, dict):
                    continue
                title = str(p.get("title") or "Untitled").strip() or "Untitled"
                pages.append(
                    {
                        "title": title,
                        "page_type": str(p.get("page_type") or "Topic"),
                        "summary": str(p.get("summary") or data.get("summary") or "")[
                            :500
                        ],
                        "body": str(p.get("body") or "").strip()
                        or str(data.get("summary") or ""),
                    }
                )
        summary = str(data.get("summary") or "").strip()
        if not pages and summary:
            pages = [
                {
                    "title": "模型精读笔记",
                    "page_type": "Topic",
                    "summary": summary[:200],
                    "body": summary,
                }
            ]
        key_points = _as_str_list(data.get("key_points"))
        missing = []
        if not summary:
            missing.append("summary")
        if "key_points" not in data:
            missing.append("key_points")
        if not pages:
            missing.append("pages")
        return {
            "ok": not missing,
            "skill_contract": "strict" if not missing else "partial",
            "parse": mode,
            "summary": summary,
            "main_content": str(data.get("main_content") or "").strip(),
            "key_points": key_points,
            "quotes": _as_quotes(data.get("quotes")),
            "related": _as_related(data.get("related")),
            "pages": pages,
            "missing": missing,
        }

    # loose: whole reply as one page
    body = (raw or "").strip()
    title = "模型精读笔记"
    first = body.splitlines()[0].strip("# ").strip() if body else title
    if 2 < len(first) < 80:
        title = first
    return {
        "ok": bool(body),
        "skill_contract": "loose",
        "parse": "loose",
        "summary": body[:400],
        "main_content": body[:800],
        "key_points": [],
        "quotes": [],
        "related": [],
        "pages": [
            {
                "title": title,
                "page_type": "Topic",
                "summary": body[:200],
                "body": body,
            }
        ]
        if body
        else [],
        "missing": [] if body else ["pages", "summary"],
    }


def enrich_page_body(
    body: str,
    *,
    key_points: list[str] | None = None,
    quotes: list[dict[str, str]] | None = None,
    related: list[dict[str, str]] | None = None,
) -> str:
    """Ensure ## 核心观点 / ## 引用 / ## 相关 sections exist when data present."""
    text = (body or "").rstrip()
    parts = [text] if text else []
    if key_points and "## 核心观点" not in text:
        bullets = "\n".join(f"- {k}" for k in key_points)
        parts.append(f"## 核心观点\n\n{bullets}")
    if quotes and "## 引用" not in text:
        lines = []
        for q in quotes:
            note = q.get("note") or ""
            lines.append(f"> {q.get('text', '')}")
            if note:
                lines.append(f"\n— {note}")
            lines.append("")
        parts.append("## 引用\n\n" + "\n".join(lines).rstrip())
    if related and "## 相关" not in text:
        lines = []
        for r in related:
            wl = (r.get("wikilink") or "").strip()
            title = r.get("title") or wl
            rel = r.get("relation") or "相关"
            if wl:
                lines.append(f"- [[{wl}|{title}]]（{rel}）")
            else:
                lines.append(f"- {title}（{rel}）")
        parts.append("## 相关\n\n" + "\n".join(lines))
    return "\n\n".join(p for p in parts if p).strip() + "\n"


def rate_ingest_risk(
    *,
    gate_meta: dict[str, Any] | None,
    ops: list[Any],
    settings: dict[str, Any] | None = None,
    skill_ok: bool = True,
    skill_contract: str = "strict",
) -> dict[str, Any]:
    """Q1=A risk rating → level low|high + reasons."""
    settings = settings or {}
    reasons: list[str] = []
    if settings.get("ingest_always_review"):
        return {"level": "high", "reasons": ["ingest_always_review"]}
    if not skill_ok or skill_contract == "loose":
        reasons.append("skill_contract_weak")
    gm = gate_meta or {}
    if gm.get("needs_review"):
        reasons.append("needs_review")
    actions = gm.get("actions") or []
    for a in actions:
        if not isinstance(a, dict):
            continue
        if a.get("action") == "propose":
            reasons.append("gate_propose")
            break
        if a.get("confidence") == "low" and a.get("action") in (
            "drop_create",
            "propose",
            "inject_link",
        ):
            reasons.append("low_confidence_gate")
            break

    create_topics = 0
    max_body = 0
    for op in ops:
        d = op if isinstance(op, dict) else getattr(op, "__dict__", {})
        if hasattr(op, "to_dict"):
            d = op.to_dict()
        path = str(d.get("path") or "")
        content = d.get("content") or ""
        if d.get("op") == "create" and (
            path.startswith("topics/")
            or path.startswith("concepts/")
            or path.startswith("entities/")
        ):
            create_topics += 1
        if isinstance(content, str):
            max_body = max(max_body, len(content.encode("utf-8")))
        # conflict marker
        if "conflict" in path.lower():
            reasons.append("conflict_path")
        if "secret:" in str(content).lower() or path.startswith("profiles/"):
            # profiles not automatically frozen but secret marker is
            if "secret:" in str(content).lower():
                reasons.append("secret_marker")

    if create_topics > 5:
        reasons.append("too_many_new_topics")
    if max_body > 80_000:
        reasons.append("page_too_large")

    # Ambiguous / low dup in gate actions
    for a in actions:
        if isinstance(a, dict) and "ambiguous" in str(a.get("detail") or "").lower():
            reasons.append("ambiguous")
            break

    level = "high" if reasons else "low"
    return {"level": level, "reasons": list(dict.fromkeys(reasons))}


def dedup_message_from_gate(gate_meta: dict[str, Any] | None) -> dict[str, Any]:
    """Build UI-facing dedup hits from gate actions."""
    gm = gate_meta or {}
    hits: list[dict[str, Any]] = []
    for a in gm.get("actions") or []:
        if not isinstance(a, dict):
            continue
        act = a.get("action")
        if act == "drop_create":
            hits.append(
                {
                    "kind": "duplicate",
                    "path": a.get("target") or a.get("path"),
                    "title": a.get("target") or "",
                    "confidence": a.get("confidence") or "high",
                    "detail": a.get("detail"),
                }
            )
        elif act == "inject_link":
            hits.append(
                {
                    "kind": "related",
                    "path": a.get("target") or a.get("path"),
                    "title": a.get("target") or "",
                    "confidence": a.get("confidence") or "high",
                    "detail": a.get("detail"),
                }
            )
        elif act == "propose":
            hits.append(
                {
                    "kind": "ambiguous",
                    "path": a.get("target") or a.get("path"),
                    "title": a.get("path") or "",
                    "confidence": "low",
                    "detail": a.get("detail"),
                }
            )
    # dedupe by path+kind
    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, Any]] = []
    for h in hits:
        key = (str(h.get("kind")), str(h.get("path")))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    if not uniq:
        msg = "未发现与知识库的明显重复。"
    else:
        parts = []
        for h in uniq[:8]:
            parts.append(
                f"{h['kind']}: {h.get('path') or h.get('title')} ({h.get('confidence')})"
            )
        msg = "查重：" + "；".join(parts)
    return {"hits": uniq, "message": msg}
