"""Workspace export files under .kw/exports (R3.11 / R3.15)."""

from __future__ import annotations

import re
import secrets
import time
from pathlib import Path
from typing import Any

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_STEM_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def exports_dir(workspace: Path | str) -> Path:
    d = Path(workspace).resolve() / ".kw" / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_export_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw or "/" in raw.replace("\\", "/") or "\\" in raw or ".." in raw:
        raise ValueError(f"invalid export name: {name!r}")
    base = Path(raw).name.strip()
    if not base or base != raw or not _SAFE_NAME.match(base):
        raise ValueError(f"invalid export name: {name!r}")
    return base


def unique_export_name(stem: str = "export", ext: str = ".docx") -> str:
    """Return a collision-resistant export file name (R3.15)."""
    e = str(ext or "").strip()
    if not e.startswith("."):
        e = "." + e if e else ".docx"
    e = e.lower()
    if e not in (".docx", ".xlsx", ".mp3", ".wav", ".pdf", ".pptx"):
        e = ".docx"
    base = _STEM_SAFE.sub("_", str(stem or "export")).strip("_-") or "export"
    base = base[:48]
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return safe_export_name(f"{base}_{stamp}_{secrets.token_hex(3)}{e}")


def resolve_export(workspace: Path | str, name: str) -> Path:
    root = exports_dir(workspace)
    safe = safe_export_name(name)
    target = (root / safe).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("path escape") from exc
    return target


def save_bytes(workspace: Path | str, name: str, data: bytes) -> str:
    """Write bytes; return safe file name."""
    path = resolve_export(workspace, name)
    path.write_bytes(data)
    return path.name


def list_exports(workspace: Path | str) -> list[dict[str, Any]]:
    root = exports_dir(workspace)
    items: list[dict[str, Any]] = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if not _SAFE_NAME.match(p.name):
            continue
        st = p.stat()
        items.append(
            {
                "name": p.name,
                "bytes": int(st.st_size),
                "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            }
        )
    return items


def _strip_md_noise(line: str) -> str:
    s = (line or "").strip()
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return s.strip()


def materialize_docx_from_text(
    workspace: Path | str,
    name: str,
    text: str,
    *,
    force: bool = False,
) -> str:
    """Create a .docx under exports from assistant/user text. Returns final file name."""
    from localwiki.office_io import write_docx

    raw = (name or "export").strip()
    raw = raw.replace("\\", "/").split("/")[-1]
    if not raw.lower().endswith(".docx"):
        raw = raw + ".docx"
    # sanitize to safe_export_name rules
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-") or "export.docx"
    if not safe.lower().endswith(".docx"):
        safe = safe + ".docx"
    safe = safe_export_name(safe)

    path = resolve_export(workspace, safe)
    if path.is_file() and not force:
        return safe

    title = ""
    paragraphs: list[str] = []
    bullets: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if "/api/exports/" in s or s.startswith("http://") or s.startswith("https://"):
            continue
        if s.startswith("由于当前环境") or "不支持直接生成" in s:
            continue
        if s.startswith("- ") or s.startswith("* ") or s.startswith("• "):
            bullets.append(_strip_md_noise(s[2:] if s[1] == " " else s[1:]))
            continue
        cleaned = _strip_md_noise(s)
        if not cleaned:
            continue
        if not title and len(cleaned) < 80:
            title = cleaned
        else:
            paragraphs.append(cleaned)
    if not title:
        title = Path(safe).stem.replace("_", " ")
    if not paragraphs and not bullets:
        paragraphs = [_strip_md_noise(text)[:4000] or title]

    data = write_docx(title=title, paragraphs=paragraphs, bullet_points=bullets)
    return save_bytes(workspace, safe, data)


def resolve_export_existing(workspace: Path | str, name: str) -> Path | None:
    """Resolve export file; try adding .docx if bare name missing."""
    try:
        p = resolve_export(workspace, name)
        if p.is_file():
            return p
    except ValueError:
        p = None
    if "." not in Path(str(name)).name:
        try:
            p2 = resolve_export(workspace, str(name) + ".docx")
            if p2.is_file():
                return p2
        except ValueError:
            pass
    return None
