"""Install / update / uninstall Skills (§13.3, §13.7)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from localwiki.skills_hub.fetch import FetchResult, materialize_tree
from localwiki.skills_hub.permissions import PermissionManifest, build_manifest_from_scan
from localwiki.skills_hub.registry import SkillRecord, SkillRegistry, tree_sha256, utc_now_iso
from localwiki.skills_hub.scanner import ScanReport, scan_tree


class InstallError(RuntimeError):
    pass


@dataclass
class InstallResult:
    record: SkillRecord
    scan: ScanReport
    blocked: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "scan": self.scan.to_dict(),
            "blocked": self.blocked,
            "message": self.message,
        }


def _parse_skill_meta(skill_dir: Path) -> dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    name = skill_dir.name
    kind = "agent_skill"
    description = ""
    if skill_md.is_file():
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                fm = text[3:end]
                for line in fm.splitlines():
                    if line.strip().startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("\"'")
                    if line.strip().startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip("\"'")
        kind = "agent_skill"
    elif (skill_dir / "package.json").is_file() or (skill_dir / "server.py").is_file():
        kind = "mcp_server"
        pkg = skill_dir / "package.json"
        if pkg.is_file():
            try:
                name = json.loads(pkg.read_text(encoding="utf-8")).get("name") or name
            except json.JSONDecodeError:
                pass
    return {"name": name, "kind": kind, "description": description}


def install_from_tree(
    workspace: Path,
    files: dict[str, str | bytes],
    *,
    source: dict[str, Any] | None = None,
    commit: str = "local",
    force: bool = False,
    confirm_critical_name: str | None = None,
    acknowledge_high_risk: bool = False,
    registry: SkillRegistry | None = None,
) -> InstallResult:
    """Scan → permission → copy to ``.kw/skills/<name>@<sha>/`` read-only best-effort."""
    reg = registry or SkillRegistry(workspace)
    staging = workspace / ".kw" / "runtime" / "skill_staging" / f"tmp-{commit}"
    if staging.exists():
        shutil.rmtree(staging)
    fetch: FetchResult = materialize_tree(files, staging)
    scan = scan_tree(staging)

    if scan.block_install:
        meta = _parse_skill_meta(staging)
        if confirm_critical_name != meta["name"] and not force:
            shutil.rmtree(staging, ignore_errors=True)
            rec = SkillRecord(
                name=str(meta["name"]),
                kind=str(meta["kind"]),
                source=source or {"type": "local"},
                tree_sha256="",
                installed_at=utc_now_iso(),
                scan={"max_level": scan.max_level, "blocked": True},
                enabled=False,
                disabled_reason="scan_critical_blocked",
            )
            return InstallResult(rec, scan, True, "critical findings block install")

    if scan.max_level == "high" and not acknowledge_high_risk and not force:
        shutil.rmtree(staging, ignore_errors=True)
        meta = _parse_skill_meta(staging)
        rec = SkillRecord(
            name=str(meta["name"]),
            kind=str(meta["kind"]),
            source=source or {"type": "local"},
            tree_sha256="",
            installed_at=utc_now_iso(),
            scan={"max_level": scan.max_level, "needs_ack": True},
            enabled=False,
            disabled_reason="high_risk_needs_ack",
        )
        return InstallResult(rec, scan, True, "high risk requires acknowledge_high_risk")

    meta = _parse_skill_meta(staging)
    name = str(meta["name"])
    digest = tree_sha256(staging)
    pin = (source or {}).get("commit") or commit
    dest = reg.skills_root / f"{name}@{pin}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, dest)
    shutil.rmtree(staging, ignore_errors=True)

    # Best-effort read-only on files
    for p in dest.rglob("*"):
        if p.is_file():
            try:
                p.chmod(0o444)
            except OSError:
                pass

    manifest = build_manifest_from_scan(scan)
    record = SkillRecord(
        name=name,
        kind=str(meta["kind"]),
        source=source
        or {
            "type": "local",
            "commit": pin,
        },
        tree_sha256=digest,
        installed_at=utc_now_iso(),
        scan={
            "max_level": scan.max_level,
            "categories": scan.categories_hit,
            "finding_count": len(scan.findings),
        },
        granted=manifest.to_dict(),
        enabled=True,
        pinned=True,
        install_path=str(dest),
        version_or_commit=str(pin),
    )
    reg.upsert(record)
    return InstallResult(record, scan, False, "installed")


def uninstall_skill(workspace: Path, name: str, *, registry: SkillRegistry | None = None) -> bool:
    reg = registry or SkillRegistry(workspace)
    rec = reg.get(name)
    if not rec:
        return False
    if rec.install_path:
        path = Path(rec.install_path)
        if path.is_dir():
            # chmod writable then remove
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        p.chmod(0o644)
                    except OSError:
                        pass
            shutil.rmtree(path, ignore_errors=True)
    reg.remove(name)
    return True


def update_skill_from_tree(
    workspace: Path,
    name: str,
    files: dict[str, str | bytes],
    *,
    new_commit: str,
    acknowledge_high_risk: bool = False,
    confirm_critical_name: str | None = None,
    registry: SkillRegistry | None = None,
) -> InstallResult:
    """Replace install with rescan; never auto-update without this call."""
    reg = registry or SkillRegistry(workspace)
    old = reg.get(name)
    source = dict(old.source) if old else {"type": "local"}
    source["commit"] = new_commit
    if old:
        uninstall_skill(workspace, name, registry=reg)
    return install_from_tree(
        workspace,
        files,
        source=source,
        commit=new_commit,
        acknowledge_high_risk=acknowledge_high_risk,
        confirm_critical_name=confirm_critical_name,
        registry=reg,
    )
