"""Model-backed knowledge compile (R3.9 + R3.28 knowledge-ingest Skill)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from localwiki.agent.routed_model import ModelNotConfiguredError, build_routed_model
from localwiki.extractors import slugify
from localwiki.llmwiki.index_log import CATEGORY_DIRS
from localwiki.llmwiki.knowledge_ingest_skill import (
    SKILL_NAME,
    build_compile_messages,
    enrich_page_body,
    parse_ingest_payload,
)

MAX_CHARS = 80_000


def _assistant_text(resp: Any) -> str:
    if isinstance(resp, dict):
        choices = resp.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            return str(msg.get("content") or "").strip()
        if "content" in resp:
            return str(resp.get("content") or "").strip()
    return str(resp or "").strip()


def _page_markdown(
    title: str,
    page_type: str,
    summary: str,
    body: str,
    *,
    compile_mode: str = "model",
) -> str:
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"{page_type}\"\n"
        f"title: \"{title}\"\ncompile_mode: {compile_mode}\n"
        f"skill: {SKILL_NAME}\n---\n\n"
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
    """Call knowledge_compile model with K7 skill; return ops + meta for ChangeSet."""
    root = Path(root).resolve()
    truncated = False
    body = text or ""
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS]
        truncated = True

    if model is None:
        model = build_routed_model(root, core_route="knowledge_compile")
        try:
            model.timeout = max(float(getattr(model, "timeout", 120) or 120), 180.0)
        except Exception:  # noqa: BLE001
            pass

    messages = build_compile_messages(
        root=root,
        text=body,
        meta=meta,
        source_uid=source_uid,
        truncated=truncated,
    )
    try:
        resp = model.generate(messages, tools=None, extras={})
    except ModelNotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ModelNotConfiguredError(str(exc), code="E_MODEL") from exc

    raw = _assistant_text(resp)
    if not raw:
        raise ModelNotConfiguredError(
            "knowledge_compile returned empty content", code="E_MODEL"
        )
    payload = parse_ingest_payload(raw)
    pages = list(payload.get("pages") or [])
    if not pages:
        raise ModelNotConfiguredError(
            "knowledge_compile produced no pages", code="E_MODEL"
        )

    summary = str(payload.get("summary") or pages[0].get("summary") or "")
    key_points = list(payload.get("key_points") or [])
    quotes = list(payload.get("quotes") or [])
    related = list(payload.get("related") or [])
    main_content = str(payload.get("main_content") or "")

    from localwiki.core import file_sha256

    ops: list[dict[str, Any]] = []
    src_page = f"references/{source_uid}.md"
    kp_md = "\n".join(f"- {k}" for k in key_points) or "- （无）"
    quotes_md = "\n\n".join(
        f"> {q.get('text', '')}\n\n— {q.get('note') or ''}".rstrip("— ")
        for q in quotes
    ) or "（无）"
    related_md = "\n".join(
        (
            f"- [[{r['wikilink']}|{r['title']}]]（{r.get('relation') or '相关'}）"
            if r.get("wikilink")
            else f"- {r.get('title')}（{r.get('relation') or '相关'}）"
        )
        for r in related
    ) or "- （编译后由闸门补充关联）"
    src_body = (
        f"# {meta.get('title')}\n\n"
        f"- 来源文件：`{meta.get('filename')}`\n"
        f"- 格式：{meta.get('format')}\n"
        f"- sha256：`{meta.get('sha256')}`\n"
        f"- 资产：`{meta.get('path')}`\n"
        f"- compile_mode：model\n"
        f"- skill：`{SKILL_NAME}`\n"
        f"- model：`{getattr(model, 'model', '')}`\n\n"
        f"## 摘要\n\n{summary or pages[0]['summary']}\n\n"
        f"## 主要内容\n\n{main_content or summary}\n\n"
        f"## 核心观点\n\n{kp_md}\n\n"
        f"## 引用\n\n{quotes_md}\n\n"
        f"## 相关\n\n{related_md}\n"
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
        page_body = enrich_page_body(
            str(p.get("body") or ""),
            key_points=key_points,
            quotes=quotes,
            related=related,
        )
        content = _page_markdown(
            p["title"], p["page_type"], p["summary"], page_body, compile_mode="model"
        )
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
            "parse": payload.get("parse"),
            "skill_contract": payload.get("skill_contract"),
            "skill_ok": bool(payload.get("ok")),
            "truncated": truncated,
            "page_count": len(pages),
            "abstract": summary[:500],
            "main_content": main_content[:800],
            "key_points": key_points,
            "quotes": quotes,
            "related": related,
            "missing": payload.get("missing") or [],
        },
        "skill": SKILL_NAME,
        "route": "knowledge_compile",
        "model": getattr(model, "model", None),
        "provider": getattr(model, "provider_name", None),
        "payload": payload,
    }
