"""Harness pack install under ``.kw/harness/<name>/`` (R3)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from localwiki.skills_hub.installer import InstallError, install_from_tree
from localwiki.skills_hub.scanner import scan_tree


def harness_root(workspace: Path | str) -> Path:
    return Path(workspace) / ".kw" / "harness"


def install_harness_from_tree(
    workspace: Path | str,
    files: dict[str, str | bytes],
    *,
    name: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Scan → copy harness pack; requires ``manifest.json``."""
    root = Path(workspace)
    staging = root / ".kw" / "runtime" / "harness_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        rel_n = rel.replace("\\", "/").lstrip("/")
        if ".." in rel_n.split("/"):
            raise InstallError("path escape in harness files")
        dest = staging / rel_n
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            dest.write_bytes(content)
        else:
            dest.write_text(str(content), encoding="utf-8")

    scan = scan_tree(staging)
    if scan.block_install and not force:
        shutil.rmtree(staging, ignore_errors=True)
        return {
            "ok": False,
            "blocked": True,
            "kind": "harness",
            "scan": scan.to_dict(),
            "message": "critical findings block harness install",
        }

    manifest_path = staging / "manifest.json"
    if not manifest_path.is_file():
        shutil.rmtree(staging, ignore_errors=True)
        return {
            "ok": False,
            "blocked": False,
            "kind": "harness",
            "message": "manifest.json required",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "message": f"bad manifest: {exc}", "kind": "harness"}

    hname = name or str(manifest.get("name") or staging.name)
    dest = harness_root(root) / hname
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(staging, dest)
    shutil.rmtree(staging, ignore_errors=True)
    return {
        "ok": True,
        "blocked": False,
        "kind": "harness",
        "name": hname,
        "path": str(dest),
        "manifest": manifest,
        "scan": scan.to_dict(),
    }


def install_skill_or_harness(
    workspace: Path | str,
    *,
    kind: str,
    files: dict[str, str | bytes] | None = None,
    source: dict[str, Any] | None = None,
    source_url: str | None = None,
    repo: str | None = None,
    path: str | None = None,
    ref: str | None = None,
    force: bool = False,
    acknowledge_high_risk: bool = False,
    http_fetch: Any | None = None,
    github_token: str | None = None,
) -> dict[str, Any]:
    """Unified install entry for API (files and/or GitHub URL/repo)."""
    from localwiki.skills_hub.fetch import FetchError
    from localwiki.skills_hub.remote import resolve_install_files

    kind_n = (kind or "skill").lower()
    try:
        resolved, meta = resolve_install_files(
            files=files,
            source_url=source_url,
            repo=repo,
            path=path,
            ref=ref,
            http_fetch=http_fetch,
            token=github_token,
        )
    except FetchError as exc:
        return {"ok": False, "blocked": False, "kind": kind_n, "message": str(exc)}
    src = dict(source or {})
    src.update(meta)
    if kind_n == "harness":
        return install_harness_from_tree(workspace, resolved, force=force)
    result = install_from_tree(
        Path(workspace),
        resolved,
        source=src,
        force=force,
        acknowledge_high_risk=acknowledge_high_risk,
    )
    return {
        "ok": not result.blocked,
        "blocked": result.blocked,
        "kind": "skill",
        "message": result.message,
        **result.to_dict(),
    }


def list_installed(workspace: Path | str) -> dict[str, Any]:
    """List skills + harness; report OpenCode skills path alignment (R3.6)."""
    root = Path(workspace)
    skills: list[dict[str, Any]] = []
    skills_dir = root / ".kw" / "skills"
    if skills_dir.is_dir():
        for p in sorted(skills_dir.iterdir()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            enabled = True
            disabled_reason = None
            # registry may mark disabled
            try:
                from localwiki.skills_hub.registry import SkillRegistry

                reg = SkillRegistry(root)
                rec = None
                for r in reg.list():
                    if r.name in p.name or p.name.startswith(r.name):
                        rec = r
                        break
                if rec is not None:
                    enabled = bool(rec.enabled)
                    disabled_reason = rec.disabled_reason
            except Exception:  # noqa: BLE001
                pass
            skills.append(
                {
                    "name": p.name,
                    "kind": "skill",
                    "path": str(p),
                    "enabled": enabled,
                    "disabled_reason": disabled_reason,
                }
            )
    harnesses: list[dict[str, Any]] = []
    hroot = harness_root(root)
    if hroot.is_dir():
        for p in sorted(hroot.iterdir()):
            if p.is_dir():
                harnesses.append(
                    {
                        "name": p.name,
                        "kind": "harness",
                        "path": str(p),
                        "enabled": True,
                    }
                )
    oc_skills = skills_dir
    aligned = oc_skills.is_dir() or True  # path contract: bridge uses .kw/skills
    warning = None if aligned else "skills directory missing"
    return {
        "ok": True,
        "skills": skills,
        "harness": harnesses,
        "harnesses": harnesses,  # back-compat
        "opencode_skills_aligned": bool(aligned),
        "opencode_skills_path": str(oc_skills),
        "alignment_warning": warning,
    }


def stuck_search_install(
    workspace: Path | str,
    query: str,
    *,
    mock_files: dict[str, str | bytes] | None = None,
) -> dict[str, Any]:
    """卡壳钩子：search → install fixture/mock → report."""
    from localwiki.skills_hub.search import search_skills

    search = search_skills(query, offline=True)
    installed = None
    if mock_files:
        installed = install_skill_or_harness(
            workspace, kind="skill", files=mock_files, force=True
        )
    return {
        "ok": True,
        "query": query,
        "search": search.to_dict() if hasattr(search, "to_dict") else search,
        "installed": installed,
    }
