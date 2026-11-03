"""Resolve safe paths under workspace/.kw/demo for /demo/* (R3.23)."""

from __future__ import annotations

import mimetypes
from pathlib import Path


def demo_root(workspace: Path | str) -> Path:
    return Path(workspace).resolve() / ".kw" / "demo"


def resolve_demo_file(workspace: Path | str, url_path: str) -> Path | None:
    """Map ``/demo/...`` or relative rel to a file under ``.kw/demo``.

    Returns None on missing file or path escape.
    """
    root = demo_root(workspace)
    raw = (url_path or "").strip()
    if raw.startswith("/demo/"):
        rel = raw[len("/demo/") :]
    elif raw == "/demo" or raw == "/demo/":
        rel = ""
    else:
        rel = raw.lstrip("/")
    rel = rel.replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        return None
    if not rel or rel.endswith("/"):
        candidate = root / rel / "index.html" if rel else root / "index.html"
    else:
        candidate = root / rel
        if candidate.is_dir():
            candidate = candidate / "index.html"
    try:
        target = candidate.resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    if not target.is_file():
        return None
    return target


def guess_demo_ctype(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
