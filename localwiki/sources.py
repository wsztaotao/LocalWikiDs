"""Import pipeline: assets on disk + compile to ChangeSet (R2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from localwiki.core import CommitService, file_sha256
from localwiki.extractors import (
    ExtractError,
    extract_bytes,
    slugify,
    write_locators_json,
)
from localwiki.llmwiki.index_log import CATEGORY_DIRS
from localwiki.llmwiki.ingest import (
    ExistingPage,
    SourceDocument,
    ingest_result_as_dict,
    ingest_source,
)
from localwiki.workspace import ensure_workspace, read_head


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sources_index(root: Path) -> Path:
    return ensure_workspace(root)["wiki"] / "references" / "sources.json"


def list_sources(root: Path | str) -> list[dict[str, Any]]:
    path = _sources_index(Path(root))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(data.get("sources") or [])


def _save_sources(root: Path, sources: list[dict[str, Any]]) -> None:
    path = _sources_index(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sources": sources, "updated_at": _utc()}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def import_bytes(
    root: Path | str,
    data: bytes,
    *,
    filename: str,
    title: str | None = None,
) -> dict[str, Any]:
    """Extract, dedupe by sha256, write asset version under wiki/references/assets."""
    root = Path(root).resolve()
    ensure_workspace(root)
    result = extract_bytes(data, filename=filename)
    sources = list_sources(root)
    for s in sources:
        if s.get("sha256") == result.sha256:
            return {**s, "deduped": True}

    src_id = f"src_{result.sha256[:12]}"
    # next version
    base = root / "wiki" / "references" / "assets" / src_id
    base.mkdir(parents=True, exist_ok=True)
    existing_vers = sorted(base.glob("v*"))
    n = len(existing_vers) + 1
    ver = f"v{n:03d}"
    dest = base / ver
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "original.bin").write_bytes(data)
    (dest / "extracted.txt").write_text(result.text, encoding="utf-8")
    write_locators_json(dest / "locators.json", result.locators)
    meta = {
        "source_uid": src_id,
        "version": ver,
        "filename": filename,
        "title": title or result.title,
        "format": result.format,
        "sha256": result.sha256,
        "path": dest.relative_to(root / "wiki").as_posix(),
        "imported_at": _utc(),
        "needs_model": True,
        "meta": result.meta,
    }
    (dest / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sources.append(meta)
    _save_sources(root, sources)
    return {**meta, "deduped": False}


def _draft_path(title: str, page_type: str) -> str:
    folder = CATEGORY_DIRS.get(page_type, "topics")
    return f"{folder}/{slugify(title)}.md"


def _page_markdown(title: str, page_type: str, summary: str, body: str, *, needs_model: bool) -> str:
    flags = "needs_model: true\n" if needs_model else ""
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"{page_type}\"\n"
        f"title: \"{title}\"\n{flags}---\n\n"
        f"# {title}\n\n> {summary}\n\n{body}\n"
    )


def compile_source(
    root: Path | str,
    source_uid: str,
    *,
    version: str | None = None,
    mode: str = "model",
) -> dict[str, Any]:
    """Create a ready ChangeSet: default model compile; explicit heuristic opt-in."""
    root = Path(root).resolve()
    mode_norm = (mode or "model").strip().lower()
    if mode_norm not in ("model", "heuristic"):
        raise ExtractError("E_INVALID_INPUT", f"unsupported compile mode: {mode}")

    sources = list_sources(root)
    meta = next((s for s in sources if s.get("source_uid") == source_uid), None)
    if not meta:
        raise ExtractError("E_NOT_FOUND", f"source not found: {source_uid}")
    ver = version or meta.get("version")
    asset = root / "wiki" / "references" / "assets" / source_uid / str(ver)
    text_path = asset / "extracted.txt"
    if not text_path.exists():
        raise ExtractError("E_NOT_FOUND", f"extracted.txt missing for {source_uid}/{ver}")
    text = text_path.read_text(encoding="utf-8")

    svc = CommitService(root)

    if mode_norm == "model":
        from localwiki.agent.routed_model import ModelNotConfiguredError
        from localwiki.llmwiki.model_compile import model_compile_to_ops

        try:
            compiled = model_compile_to_ops(
                root, text=text, meta=meta, source_uid=source_uid
            )
        except ModelNotConfiguredError as exc:
            raise ExtractError(
                getattr(exc, "code", "E_MODEL_UNAVAILABLE"),
                str(exc),
            ) from exc
        ops = list(compiled["ops"])
        if not ops:
            raise ExtractError("E_INVALID_INPUT", "no ops produced from model compile")
        cs = svc.create_changeset(
            purpose=f"model-ingest {source_uid} {ver}",
            ops=ops,
            state="ready",
        )
        cand_dir = root / ".kw" / "candidates"
        cand_dir.mkdir(parents=True, exist_ok=True)
        (cand_dir / f"{cs.change_uid}.json").write_text(
            json.dumps(
                {
                    "change_uid": cs.change_uid,
                    "source_uid": source_uid,
                    "compile_mode": "model",
                    "model": compiled.get("model"),
                    "summary": compiled.get("summary"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "changeset": cs.to_dict(),
            "summary": compiled.get("summary"),
            "source_uid": source_uid,
            "version": ver,
            "compile_mode": "model",
            "route": compiled.get("route"),
            "model": compiled.get("model"),
            "provider": compiled.get("provider"),
        }

    # --- heuristic (explicit weak path) ---
    existing: list[ExistingPage] = []
    for p in svc.list_pages():
        if p["path"] in ("index.md", "log.md"):
            continue
        content = (root / "wiki" / p["path"]).read_text(encoding="utf-8")
        existing.append(
            ExistingPage(
                page_uid=p["path"],
                title=p.get("title") or Path(p["path"]).stem,
                page_type="Topic",
                content_hash=p.get("sha256") or "",
                summary=content[:200],
                claims=[ln.strip() for ln in content.splitlines() if ln.strip()][:5],
            )
        )

    doc = SourceDocument(
        text=text,
        title=str(meta.get("title") or source_uid),
        source_version_uid=f"{source_uid}_{ver}",
        speaker_role="unknown",
        source_hash=str(meta.get("sha256") or ""),
    )
    result = ingest_source(
        doc,
        workspace=root,
        existing_pages=existing,
        base_snapshot_id=read_head(root),
        use_cache=False,
    )

    ops: list[dict[str, Any]] = []
    src_page = f"references/{source_uid}.md"
    src_body = (
        f"# {meta.get('title')}\n\n"
        f"- 来源文件：`{meta.get('filename')}`\n"
        f"- 格式：{meta.get('format')}\n"
        f"- sha256：`{meta.get('sha256')}`\n"
        f"- 资产：`{meta.get('path')}`\n"
        f"- compile_mode：heuristic\n\n"
        f"## 摘录\n\n{text[:2000]}\n"
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

    for draft in result.knowledge.drafts.operations:
        if draft.op == "conflict":
            continue
        path = (
            draft.page_uid
            if draft.page_uid and draft.page_uid.endswith(".md")
            else _draft_path(draft.title, draft.page_type)
        )
        content = _page_markdown(
            draft.title,
            draft.page_type,
            draft.summary,
            draft.body,
            needs_model=True,
        )
        target = root / "wiki" / path
        if draft.op == "revise" and target.exists():
            ops.append(
                {
                    "op": "update",
                    "path": path,
                    "content": content,
                    "base_sha256": draft.baseline_hash or file_sha256(target),
                }
            )
        else:
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

    if not ops:
        raise ExtractError("E_INVALID_INPUT", "no ops produced from ingest")

    cs = svc.create_changeset(
        purpose=f"heuristic-ingest {source_uid} {ver}",
        ops=ops,
        state="ready",
    )
    cand_dir = root / ".kw" / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    (cand_dir / f"{cs.change_uid}.json").write_text(
        json.dumps(
            {
                "change_uid": cs.change_uid,
                "source_uid": source_uid,
                "compile_mode": "heuristic",
                "ingest": ingest_result_as_dict(result),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "changeset": cs.to_dict(),
        "summary": result.to_summary(),
        "source_uid": source_uid,
        "version": ver,
        "compile_mode": "heuristic",
        "route": None,
        "model": None,
    }
