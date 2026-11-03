"""Install curated office skills into workspace .kw/skills (R3.11)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from localwiki.skills_hub.office_catalog import catalog_version, list_catalog


def bundled_root() -> Path:
    return Path(__file__).resolve().parents[1] / "skills_bundled"


def skills_dir(workspace: Path | str) -> Path:
    return Path(workspace).resolve() / ".kw" / "skills"


def skills_empty(workspace: Path | str) -> bool:
    d = skills_dir(workspace)
    if not d.is_dir():
        return True
    return not any(p.is_dir() for p in d.iterdir())


def _copy_bundled(workspace: Path, item_id: str, rel: str, *, force: bool) -> dict[str, Any]:
    src = bundled_root() / rel
    if not src.is_dir() or not (src / "SKILL.md").is_file():
        return {"id": item_id, "ok": False, "message": f"bundled missing: {rel}"}
    dest = skills_dir(workspace) / item_id
    if dest.exists() and not force:
        if (dest / "SKILL.md").is_file():
            return {"id": item_id, "ok": True, "skipped": True, "message": "already installed"}
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return {"id": item_id, "ok": True, "skipped": False, "message": "installed"}


def bootstrap_office_skills(
    workspace: Path | str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Idempotent install of bundled office skills. Never raises for missing optional deps."""
    root = Path(workspace).resolve()
    skills_dir(root).mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    for item in list_catalog():
        iid = str(item.get("id") or "")
        kind = str(item.get("kind") or "")
        if kind != "bundled":
            failed.append({"id": iid, "message": f"unsupported kind {kind}"})
            continue
        rel = str(item.get("path") or "")
        try:
            res = _copy_bundled(root, iid, rel, force=force)
        except OSError as exc:
            failed.append({"id": iid, "message": str(exc)})
            continue
        if not res.get("ok"):
            failed.append({"id": iid, "message": str(res.get("message") or "failed")})
        elif res.get("skipped"):
            skipped.append(iid)
        else:
            installed.append(iid)
    return {
        "ok": True,
        "catalog_version": catalog_version(),
        "installed": installed,
        "skipped": skipped,
        "failed": failed,
        "skills_path": str(skills_dir(root)),
    }
