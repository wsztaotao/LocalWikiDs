"""Native workspace/knowledge handlers (R3): list/read/write/FTS search."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from localwiki.core import CommitService, CommitError, file_sha256
from localwiki.workspace import ensure_workspace

# D9 allowlist for auto_accept
AUTO_ACCEPT_PREFIXES = (
    "memories/",
    "experiences/",
    "profiles/style/",
)


class WorkspaceToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_rel(root: Path, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise WorkspaceToolError("E_INVALID_INPUT", f"bad path: {rel}")
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceToolError("E_INVALID_INPUT", "path escape") from exc
    return target


def fts_db_path(workspace: Path) -> Path:
    paths = ensure_workspace(workspace)
    return paths["cache"] / "wiki_fts.sqlite"


def rebuild_wiki_fts(workspace: Path | str) -> dict[str, Any]:
    root = Path(workspace).resolve()
    ensure_workspace(root)
    wiki = root / "wiki"
    db = fts_db_path(root)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TABLE IF EXISTS wiki_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE wiki_fts USING fts5(path, title, body, tokenize='unicode61')"
        )
        n = 0
        if wiki.is_dir():
            for p in wiki.rglob("*.md"):
                rel = p.relative_to(wiki).as_posix()
                text = p.read_text(encoding="utf-8", errors="replace")
                title = _title_of(text, rel)
                conn.execute(
                    "INSERT INTO wiki_fts(path, title, body) VALUES(?,?,?)",
                    (rel, title, text),
                )
                n += 1
        conn.commit()
        return {"ok": True, "indexed": n, "db": str(db)}
    finally:
        conn.close()


def _like_terms(safe: str, *, max_terms: int = 24) -> list[str]:
    """Build LIKE needles for sentence-style queries.

    Prefer left-anchored CJK prefixes and particle-split phrases so
    「逻辑哲学论的核心观点是什么」 still matches title 「逻辑哲学论」,
    without flooding with every sliding window (which hits session extracts
    first) or ultra-short ASCII tokens (false positives like ``no``).
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        t = (t or "").strip()
        if not t or t in seen:
            return
        if re.fullmatch(r"[A-Za-z0-9_\-]+", t):
            if len(t) < 5:
                return
        elif re.search(r"[\u4e00-\u9fff]", t):
            # min 3 CJK chars — length-2 is too noisy (核心/观点/研究…)
            if len(re.findall(r"[\u4e00-\u9fff]", t)) < 3 and len(t) < 3:
                return
            if len(t) < 3:
                return
        elif len(t) < 4:
            return
        seen.add(t)
        out.append(t)

    def _add_cjk_prefixes(run: str) -> None:
        _add(run)
        if len(run) < 3:
            return
        for n in range(min(8, len(run)), 2, -1):
            _add(run[:n])

    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", safe))
    _add(safe)
    if not has_cjk:
        # ASCII-only: whole string + long tokens only (avoid ``topic`` false hits)
        for tok in re.split(r"\s+", safe):
            _add(tok)
        return out[:max_terms]

    for tok in re.split(r"\s+", safe):
        _add(tok)

    # Split on common Chinese function words / question particles
    for piece in re.split(r"[的是了吗呢吧啊嘛如何怎么什么请问]+", safe):
        for run in re.findall(r"[\u4e00-\u9fff]+", piece or ""):
            _add_cjk_prefixes(run)

    for run in re.findall(r"[\u4e00-\u9fff]+", safe):
        _add_cjk_prefixes(run)

    return out[:max_terms]


def _escape_like(s: str) -> str:
    """Escape ``\\``, ``%``, ``_`` for SQLite LIKE (ESCAPE '\\')."""
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _path_rank(path: str) -> int:
    """Lower is better: topics/references before memories/logs."""
    p = (path or "").replace("\\", "/")
    if p.startswith("topics/"):
        return 0
    if p.startswith("references/"):
        return 1
    if p in ("index.md",):
        return 2
    if p.startswith("memories/") or p.startswith("experiences/"):
        return 8
    if p in ("log.md",) or p.startswith("profiles/"):
        return 9
    return 5


def fts_search(workspace: Path | str, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    root = Path(workspace).resolve()
    db = fts_db_path(root)
    if not db.exists():
        rebuild_wiki_fts(root)
    q = (query or "").strip()
    if not q:
        return []
    # Escape FTS5 special chars roughly: quote phrases
    safe = re.sub(r'[^\w\u4e00-\u9fff\s]+', " ", q, flags=re.UNICODE).strip()
    if not safe:
        return []
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", safe))
    match_parts = [t for t in safe.split() if t]
    if has_cjk:
        terms = " OR ".join(
            f'"{t}"' if " " in t else t
            for t in match_parts
            if len(t) >= 2
        )
    else:
        # Latin: require ALL long tokens (AND) so noise words alone cannot hit
        long_parts = [t for t in match_parts if len(t) >= 4]
        terms = " AND ".join(long_parts) if long_parts else ""
    conn = sqlite3.connect(str(db))
    try:
        rows: list = []
        try:
            if terms:
                rows = conn.execute(
                    "SELECT path, title, snippet(wiki_fts, 2, '[', ']', '…', 24) "
                    "FROM wiki_fts WHERE wiki_fts MATCH ? LIMIT ?",
                    (terms, int(limit)),
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        # Chinese / short queries often yield empty MATCH; fall back to LIKE
        if not rows:
            by_path: dict[str, tuple] = {}
            for needle in _like_terms(safe):
                like = f"%{_escape_like(needle)}%"
                # Do not LIKE against path for plain tokens — ``topic`` would
                # false-hit directory ``topics/``.
                if "/" in needle or "." in needle:
                    found = conn.execute(
                        "SELECT path, title, substr(body,1,120) FROM wiki_fts "
                        "WHERE body LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
                        "OR path LIKE ? ESCAPE '\\' LIMIT ?",
                        (like, like, like, int(limit) * 3),
                    ).fetchall()
                else:
                    found = conn.execute(
                        "SELECT path, title, substr(body,1,120) FROM wiki_fts "
                        "WHERE body LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
                        "LIMIT ?",
                        (like, like, int(limit) * 3),
                    ).fetchall()
                for r in found:
                    path = r[0]
                    if path not in by_path:
                        by_path[path] = r
            ranked = sorted(by_path.values(), key=lambda r: (_path_rank(r[0]), r[0]))
            rows = ranked[: int(limit)]
        return [
            {"path": r[0], "title": r[1], "snippet": r[2], "score": 1.0}
            for r in rows
        ]
    finally:
        conn.close()


def _title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(fallback).stem


def make_handlers(workspace: Path | str) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    root = Path(workspace).resolve()
    ensure_workspace(root)
    wiki = (root / "wiki").resolve()

    def workspace_list(args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path") or ".")
        try:
            base = _safe_rel(root, rel) if rel not in (".", "") else root
        except WorkspaceToolError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        if not base.is_dir():
            return {"ok": False, "error": {"code": "E_NOT_FOUND", "message": rel}}
        items = []
        for p in sorted(base.iterdir()):
            try:
                r = p.relative_to(root).as_posix()
            except ValueError:
                continue
            items.append({"path": r, "type": "dir" if p.is_dir() else "file"})
        return {"ok": True, "items": items}

    def workspace_read(args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path") or "")
        try:
            target = _safe_rel(root, rel)
        except WorkspaceToolError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        if not target.is_file():
            return {"ok": False, "error": {"code": "E_NOT_FOUND", "message": rel}}
        data = target.read_bytes()
        if len(data) > 2_000_000:
            return {"ok": False, "error": {"code": "E_INVALID_INPUT", "message": "file too large"}}
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": {"code": "E_INVALID_INPUT", "message": "not utf-8 text"}}
        return {"ok": True, "path": rel.replace("\\", "/"), "content": text}

    def knowledge_read(args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path") or "")
        try:
            page = CommitService(root).read_page(rel)
            return {"ok": True, **page}
        except CommitError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}

    def knowledge_search(args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query") or args.get("q") or "")
        hits = fts_search(root, q, limit=int(args.get("limit") or 20))
        return {"ok": True, "query": q, "hits": hits}

    def workspace_write(args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path") or "").replace("\\", "/").lstrip("/")
        content = args.get("content")
        if content is None:
            return {"ok": False, "error": {"code": "E_INVALID_INPUT", "message": "content required"}}
        content = str(content)
        # Must be under wiki/
        if not rel.startswith("wiki/") and ".." not in rel:
            # allow path relative to wiki
            wiki_rel = rel
        elif rel.startswith("wiki/"):
            wiki_rel = rel[len("wiki/") :]
        else:
            return {"ok": False, "error": {"code": "E_INVALID_INPUT", "message": "path must be under wiki/"}}
        try:
            _safe_rel(wiki, wiki_rel)
        except WorkspaceToolError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}

        svc = CommitService(root)
        target = wiki / wiki_rel
        if target.exists():
            op = {
                "op": "update",
                "path": wiki_rel,
                "content": content,
                "base_sha256": file_sha256(target),
            }
        else:
            op = {"op": "create", "path": wiki_rel, "content": content}

        auto = bool(args.get("auto_accept"))
        if auto and not any(wiki_rel.startswith(p) for p in AUTO_ACCEPT_PREFIXES):
            auto = False

        try:
            cs = svc.create_changeset(
                purpose=str(args.get("purpose") or f"workspace_write {wiki_rel}"),
                ops=[op],
                state="ready",
            )
            if auto:
                cs = svc.accept(cs.change_uid)
                try:
                    rebuild_wiki_fts(root)
                except Exception:  # noqa: BLE001
                    pass
            return {
                "ok": True,
                "change_uid": cs.change_uid,
                "state": cs.state,
                "path": wiki_rel,
                "auto_accepted": auto and cs.state == "accepted",
            }
        except CommitError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}

    return {
        "workspace_list": workspace_list,
        "workspace_read": workspace_read,
        "knowledge_read": knowledge_read,
        "knowledge_search": knowledge_search,
        "workspace_write": workspace_write,
    }


def attach_workspace_handlers(registry: Any, workspace: Path | str) -> None:
    """Replace stub handlers for Personal Core workspace tools."""
    handlers = make_handlers(workspace)
    for name, fn in handlers.items():
        if name in getattr(registry, "_handlers", {}):
            registry._handlers[name] = fn
        elif hasattr(registry, "get") and registry.get(name):
            registry._handlers[name] = fn
