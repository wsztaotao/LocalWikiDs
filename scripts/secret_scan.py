"""Scan workspace / backup text for likely secrets (LW-38 / §21.5)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PATTERNS = [
    (r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", "api_key"),
    (r"(?i)password\s*[:=]\s*\S+", "password"),
    (r"(?i)secret\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}", "secret"),
    (r"-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----", "private_key"),
    (r"(?i)bearer\s+[A-Za-z0-9\-_\.]{20,}", "bearer_token"),
    (r"sk-[A-Za-z0-9]{20,}", "openai_like_key"),
]

SKIP_DIRS = {".git", ".venv", ".venv-work", "node_modules", "__pycache__", ".kw"}


def scan_path(path: Path) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if path.is_file():
        files = [path]
    else:
        files = [
            p
            for p in path.rglob("*")
            if p.is_file()
            and not any(part in SKIP_DIRS for part in p.parts)
            and p.suffix.lower() in {".py", ".js", ".json", ".md", ".txt", ".yml", ".yaml", ".env", ".toml"}
        ]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat, kind in PATTERNS:
            for m in re.finditer(pat, text):
                hits.append(
                    {
                        "file": str(f),
                        "kind": kind,
                        "snippet": m.group(0)[:80],
                    }
                )
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secret scan for LocalWiki workspaces/backups")
    parser.add_argument("paths", nargs="+", help="Workspace and/or backup paths")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    all_hits: list[dict[str, str]] = []
    for p in args.paths:
        all_hits.extend(scan_path(Path(p)))
    if args.json:
        import json

        print(json.dumps({"hits": all_hits, "count": len(all_hits)}, ensure_ascii=False, indent=2))
    else:
        print(f"secret_scan: {len(all_hits)} hit(s)")
        for h in all_hits[:50]:
            print(f"  [{h['kind']}] {h['file']}: {h['snippet']}")
    # Exit 1 if hits found in scanned trees (CI-friendly); allow empty
    return 1 if all_hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
