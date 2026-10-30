"""Expose installed skills/harness catalog for OpenCode (R3.13)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

HARNESS_EXPOSE_DIR = ".harness-expose"


def _read_skill_description(skill_dir: Path) -> str:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return skill_dir.name
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return skill_dir.name
    # YAML frontmatter description
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            fm = text[3:end]
            m = re.search(r"(?m)^description:\s*[\"']?(.+?)[\"']?\s*$", fm)
            if m:
                return m.group(1).strip()
    # First non-empty non-heading line
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s == "---":
            continue
        return s[:200]
    return skill_dir.name


def list_enabled_skill_catalog(workspace: Path | str) -> list[dict[str, Any]]:
    """Enabled skills + harness entries for OC/AGENTS injection."""
    from localwiki.skills_hub.harness import list_installed

    root = Path(workspace)
    info = list_installed(root)
    out: list[dict[str, Any]] = []
    for s in info.get("skills") or []:
        name = str(s.get("name") or "")
        if not name or name == HARNESS_EXPOSE_DIR:
            continue
        if s.get("enabled") is False:
            continue
        p = Path(str(s.get("path") or root / ".kw" / "skills" / name))
        if not p.is_dir():
            continue
        out.append(
            {
                "name": name,
                "kind": "skill",
                "description": _read_skill_description(p),
                "path": str(p),
            }
        )
    for h in info.get("harness") or info.get("harnesses") or []:
        name = str(h.get("name") or "")
        if not name:
            continue
        if h.get("enabled") is False:
            continue
        p = Path(str(h.get("path") or root / ".kw" / "harness" / name))
        out.append(
            {
                "name": name,
                "kind": "harness",
                "description": f"Harness pack at .kw/harness/{name}",
                "path": str(p),
            }
        )
    return out


def expose_harness_into_skills(workspace: Path | str) -> dict[str, Any]:
    """Write thin SKILL.md wrappers under .kw/skills/.harness-expose/<name>/."""
    root = Path(workspace)
    skills = root / ".kw" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    expose_root = skills / HARNESS_EXPOSE_DIR
    expose_root.mkdir(parents=True, exist_ok=True)

    from localwiki.skills_hub.harness import harness_root

    hroot = harness_root(root)
    live: set[str] = set()
    written: list[str] = []
    if hroot.is_dir():
        for p in sorted(hroot.iterdir()):
            if not p.is_dir():
                continue
            name = p.name
            live.add(name)
            dest = expose_root / name
            dest.mkdir(parents=True, exist_ok=True)
            skill_md = dest / "SKILL.md"
            body = (
                "---\n"
                f"name: harness-{name}\n"
                f'description: "LocalWiki harness pack {name} (use files under .kw/harness/{name})"\n'
                "---\n\n"
                f"# Harness: {name}\n\n"
                f"This pack lives at `.kw/harness/{name}`.\n"
                "When the task needs this harness, read its manifest/README and follow its scripts.\n"
            )
            skill_md.write_text(body, encoding="utf-8")
            written.append(name)

    removed: list[str] = []
    for child in list(expose_root.iterdir()):
        if child.is_dir() and child.name not in live:
            # remove stale expose
            for f in child.rglob("*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
            try:
                child.rmdir()
            except OSError:
                pass
            removed.append(child.name)

    return {"ok": True, "exposed": written, "removed": removed, "path": str(expose_root)}


def _is_docs_only_entry(name: str, desc: str) -> bool:
    low = f"{name} {desc}".lower()
    return any(
        k in low
        for k in (
            "byteplus",
            "volcengine",
            "doubao speech",
            "seed speech",
            "production guidance",
        )
    )


def _is_compact_primary(entry: dict[str, Any]) -> bool:
    """R3.23 Q3=A: office-* / harness only on the main AGENTS list."""
    kind = entry.get("kind") or "skill"
    name = str(entry.get("name") or "")
    if kind == "harness":
        return True
    if name.startswith("office-") or name.startswith("office_"):
        return True
    return False


def format_catalog_md(
    entries: list[dict[str, Any]],
    *,
    limit: int = 40,
    compact: bool = True,
) -> str:
    lines = ["## Installed skills (use when relevant)", ""]
    lines.append(
        "When a task matches an entry below, follow that skill/harness. "
        "Do not claim the environment has no skills."
    )
    lines.append("")
    primary: list[dict[str, Any]] = []
    footnoted = 0
    for e in entries:
        name = str(e.get("name") or "")
        desc = (e.get("description") or "").replace("\n", " ").strip()
        if compact:
            if _is_docs_only_entry(name, desc) or not _is_compact_primary(e):
                footnoted += 1
                continue
        elif _is_docs_only_entry(name, desc):
            desc = desc + "（文档-only，本机导出请用 office-tts / 工具 export_tts）"
        primary.append({**e, "description": desc})

    n = 0
    for e in primary:
        if n >= limit:
            lines.append(f"- …and {len(primary) - limit} more (see `.kw/skills`)")
            break
        kind = e.get("kind") or "skill"
        name = e.get("name") or ""
        desc = (e.get("description") or "").replace("\n", " ").strip()
        prefix = "(harness) " if kind == "harness" else ""
        lines.append(f"- {prefix}{name}: {desc}")
        n += 1
    if n == 0:
        lines.append("- (none installed yet — use Settings → Skill to install)")
    if compact and footnoted:
        lines.append("")
        lines.append(
            f"Other installed packages ({footnoted}) including docs-only cloud TTS "
            "and third-party skills live under `.kw/skills` — prefer office-* / "
            "export_docx|pdf|tts for local delivery."
        )
    lines.append("")
    return "\n".join(lines)
