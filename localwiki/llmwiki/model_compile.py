"""Model-backed knowledge compile (R3.9 — knowledge_compile route)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from localwiki.agent.routed_model import ModelNotConfiguredError, build_routed_model
from localwiki.extractors import slugify
from localwiki.llmwiki.index_log import CATEGORY_DIRS

MAX_CHARS = 80_000

_COMPILE_SYSTEM = """You are LocalWiki knowledge_compile.
Given a source document, produce:
1) a concise Chinese (or source-language) abstract/summary
2) a deep reading note covering the full provided text (structure, key claims, quotes)

Return ONLY JSON:
{
  "summary": "…",
  "pages": [
    {"title": "…", "page_type": "Topic", "summary": "…", "body": "markdown body"}
  ]
}
At least one page in pages. No markdown fences around the JSON."""


def _assistant_text(resp: Any) -> str:
    if isinstance(resp, dict):
        choices = resp.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            return str(msg.get("content") or "").strip()
        if "content" in resp:
            return str(resp.get("content") or "").strip()
    return str(resp or "").strip()


def _parse_pages(raw: str) -> tuple[str, list[dict[str, Any]], str]:
    """Return summary, pages, parse_mode (json|loose)."""
    text = (raw or "").strip()
    if not text:
        return "", [], "loose"
    # strip fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            summary = str(data.get("summary") or "").strip()
            pages = data.get("pages") or []
            if isinstance(pages, list) and pages:
                out = []
                for p in pages:
                    if not isinstance(p, dict):
                        continue
                    title = str(p.get("title") or "Untitled").strip() or "Untitled"
                    out.append(
                        {
                            "title": title,
                            "page_type": str(p.get("page_type") or "Topic"),
                            "summary": str(p.get("summary") or summary)[:500],
                            "body": str(p.get("body") or "").strip() or summary,
                        }
                    )
                if out:
                    return summary or out[0]["summary"], out, "json"
    except json.JSONDecodeError:
        pass
    # loose: whole reply as one topic page
    title = "模型精读笔记"
    first = text.splitlines()[0].strip("# ").strip() if text else title
    if 2 < len(first) < 80:
        title = first
    return text[:400], [
        {
            "title": title,
            "page_type": "Topic",
            "summary": text[:200],
            "body": text,
        }
    ], "loose"


def _page_markdown(title: str, page_type: str, summary: str, body: str) -> str:
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"{page_type}\"\n"
        f"title: \"{title}\"\ncompile_mode: model\n---\n\n"
        f"# {title}\n\n> {summary}\n\n{body}\n"
    )


def _draft_path(title: str, page_type: str) -> str:
    folder = CATEGORY_DIRS.get(page_type, "topics")
    return f"{folder}/{slugify(title)}.md"


def model_compile_to_ops(
    root: Path | str,
    *,
    text: str,
    meta: dict[str, Any],
    source_uid: str,
    model: Any | None = None,
) -> dict[str, Any]:
    """Call knowledge_compile model; return ops + meta for ChangeSet."""
    root = Path(root).resolve()
    truncated = False
    body = text or ""
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS]
        truncated = True

    if model is None:
        model = build_routed_model(root, core_route="knowledge_compile")
        # Longer timeout for full-text deep read
        try:
            model.timeout = max(float(getattr(model, "timeout", 120) or 120), 180.0)
        except Exception:  # noqa: BLE001
            pass

    user = (
        f"Source title: {meta.get('title') or source_uid}\n"
        f"Filename: {meta.get('filename')}\n"
        f"Format: {meta.get('format')}\n"
        f"Truncated: {truncated}\n\n"
        f"--- FULL TEXT ---\n{body}\n"
    )
    try:
        resp = model.generate(
            [
                {"role": "system", "content": _COMPILE_SYSTEM},
                {"role": "user", "content": user},
            ],
            tools=None,
            extras={},
        )
    except ModelNotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ModelNotConfiguredError(str(exc), code="E_MODEL") from exc

    raw = _assistant_text(resp)
    if not raw:
        raise ModelNotConfiguredError(
            "knowledge_compile returned empty content", code="E_MODEL"
        )
    summary, pages, parse_mode = _parse_pages(raw)
    if not pages:
        raise ModelNotConfiguredError(
            "knowledge_compile produced no pages", code="E_MODEL"
        )

    from localwiki.core import file_sha256

    ops: list[dict[str, Any]] = []
    src_page = f"references/{source_uid}.md"
    src_body = (
        f"# {meta.get('title')}\n\n"
        f"- 来源文件：`{meta.get('filename')}`\n"
        f"- 格式：{meta.get('format')}\n"
        f"- sha256：`{meta.get('sha256')}`\n"
        f"- 资产：`{meta.get('path')}`\n"
        f"- compile_mode：model\n"
        f"- model：`{getattr(model, 'model', '')}`\n\n"
        f"## 模型摘要\n\n{summary or pages[0]['summary']}\n"
    )
    wiki_src = root / "wiki" / src_page
    if wiki_src.exists():
        ops.append(
            {
                "op": "update",
                "path": src_page,
                "content": src_body,
                "base_sha256": file_sha256(wiki_src),
            }
        )
    else:
        ops.append({"op": "create", "path": src_page, "content": src_body})

    for p in pages:
        path = _draft_path(p["title"], p["page_type"])
        content = _page_markdown(p["title"], p["page_type"], p["summary"], p["body"])
        target = root / "wiki" / path
        if target.exists():
            ops.append(
                {
                    "op": "update",
                    "path": path,
                    "content": content,
                    "base_sha256": file_sha256(target),
                }
            )
        else:
            ops.append({"op": "create", "path": path, "content": content})

    return {
        "ops": ops,
        "summary": {
            "compile_mode": "model",
            "parse": parse_mode,
            "truncated": truncated,
            "page_count": len(pages),
            "abstract": (summary or "")[:500],
        },
        "route": "knowledge_compile",
        "model": getattr(model, "model", None),
        "provider": getattr(model, "provider_name", None),
    }
