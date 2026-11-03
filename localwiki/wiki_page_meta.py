"""Wiki page title rewrite + delete guards (R3.30)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from localwiki.evolution.proposals import check_freeze_zone
from localwiki.wiki_gate import is_protected_content

PROTECTED_PATHS = frozenset({"index.md", "log.md"})

_H1 = re.compile(r"(?m)^#\s+(.+)$")


def normalize_wiki_path(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("/")


def delete_block_reason(root: Path | str, path: str, *, content: str | None = None) -> str | None:
    """Return human reason if delete forbidden; else None."""
    rel = normalize_wiki_path(path)
    if not rel or ".." in rel.split("/"):
        return "invalid path"
    if rel in PROTECTED_PATHS:
        return f"protected path: {rel}"
    hits = check_freeze_zone(extra_paths=[rel], target={"path": rel})
    if hits:
        return "freeze_zone: " + "; ".join(h.detail for h in hits[:3])
    text = content
    if text is None:
        fp = Path(root).resolve() / "wiki" / rel
        if fp.is_file():
            text = fp.read_text(encoding="utf-8", errors="replace")
    if is_protected_content(text):
        return "protected content (secret/locked)"
    return None


def set_title_in_content(raw: str, title: str) -> str:
    """Set frontmatter title; sync first H1 if present. Path unchanged."""
    title = (title or "").strip()
    if not title:
        raise ValueError("title required")
    # escape for YAML double-quoted
    safe = title.replace("\\", "\\\\").replace('"', '\\"')
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", raw or "", re.S)
    if not m:
        body = raw or ""
        # optional sync leading H1
        if _H1.search(body):
            body = _H1.sub(f"# {title}", body, count=1)
        else:
            body = f"# {title}\n\n{body.lstrip()}"
        return f'---\nokf_version: "0.2"\ntitle: "{safe}"\n---\n\n{body.lstrip()}'
    yaml_text = m.group(1)
    body = raw[m.end() :]
    lines = yaml_text.splitlines()
    out_lines: list[str] = []
    replaced = False
    for line in lines:
        if re.match(r"^title\s*:", line):
            out_lines.append(f'title: "{safe}"')
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.insert(0, f'title: "{safe}"')
    if _H1.search(body):
        body = _H1.sub(f"# {title}", body, count=1)
    return "---\n" + "\n".join(out_lines) + "\n---\n" + body


def page_list_enrich(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from localwiki.wiki_categories import dir_category

    out: list[dict[str, Any]] = []
    for p in pages:
        row = dict(p)
        row["dir_category"] = dir_category(str(p.get("path") or ""))
        out.append(row)
    return out
