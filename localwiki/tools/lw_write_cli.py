"""CLI for OpenCode ``lw_write`` tool — large file write inside workspace (R3.25).

Usage:
  python -m localwiki.tools.lw_write_cli --path rel/path [--append] < content
  python -m localwiki.tools.lw_write_cli --path rel/path --content-file /tmp/x
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _workspace() -> Path:
    env = os.environ.get("LOCALWIKI_WORKSPACE") or ""
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def resolve_safe_path(workspace: Path, rel: str) -> Path:
    """Resolve ``rel`` under workspace; raise ValueError on escape."""
    raw = (rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("~"):
        raise ValueError("path must be relative to workspace")
    parts = Path(raw).parts
    if ".." in parts:
        raise ValueError("path escape (..) forbidden")
    target = (workspace / raw).resolve()
    ws = workspace.resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError("path escapes workspace") from exc
    return target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lw_write_cli")
    p.add_argument("--path", required=True, help="relative path under workspace")
    p.add_argument("--append", action="store_true", help="append instead of overwrite")
    p.add_argument("--content-file", default=None, help="read content from file")
    args = p.parse_args(argv)

    ws = _workspace()
    try:
        target = resolve_safe_path(ws, args.path)
    except ValueError as exc:
        print(f"lw_write error: {exc}", file=sys.stderr)
        return 2

    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()

    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "w"
    with target.open(mode, encoding="utf-8") as f:
        f.write(content)
    nbytes = len(content.encode("utf-8"))
    try:
        rel = str(target.relative_to(ws)).replace("\\", "/")
    except ValueError:
        rel = str(target)
    print(f"ok path={rel} bytes={nbytes} mode={'append' if args.append else 'write'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
