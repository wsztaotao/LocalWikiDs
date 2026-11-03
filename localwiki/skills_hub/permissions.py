"""Skill permission manifests (§13.5)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from localwiki.skills_hub.scanner import ScanReport


@dataclass
class PermissionManifest:
    fs_read: list[str] = field(default_factory=lambda: ["{skill_dir}", "{task_dir}"])
    fs_write: list[str] = field(default_factory=lambda: ["{task_dir}"])
    network: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    inferred_from_scan: list[str] = field(default_factory=list)
    user_grants: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionManifest":
        return cls(
            fs_read=list(data.get("fs_read") or ["{skill_dir}", "{task_dir}"]),
            fs_write=list(data.get("fs_write") or ["{task_dir}"]),
            network=list(data.get("network") or []),
            commands=list(data.get("commands") or []),
            secrets=list(data.get("secrets") or []),
            inferred_from_scan=list(data.get("inferred_from_scan") or []),
            user_grants=dict(data.get("user_grants") or {}),
        )


def build_manifest_from_scan(
    report: ScanReport,
    *,
    declared: dict[str, Any] | None = None,
) -> PermissionManifest:
    """Union of declared permissions and scan-inferred needs."""
    declared = declared or {}
    m = PermissionManifest(
        fs_read=list(declared.get("fs_read") or ["{skill_dir}", "{task_dir}"]),
        fs_write=list(declared.get("fs_write") or ["{task_dir}"]),
        network=list(declared.get("network") or []),
        commands=list(declared.get("commands") or []),
        secrets=list(declared.get("secrets") or []),
    )
    inferred: list[str] = []
    cats = set(report.categories_hit)
    if "network" in cats and not m.network:
        m.network = ["*"]  # needs per-run approval on Windows
        inferred.append("network")
    if "process" in cats and "python" not in m.commands:
        m.commands = list(dict.fromkeys([*m.commands, "python", "node"]))
        inferred.append("process→commands")
    if "credentials" in cats:
        inferred.append("credentials→deny_by_default")
    if "path_escape" in cats:
        inferred.append("path_escape→tighten_fs")
    m.inferred_from_scan = inferred
    return m


def filter_secrets(
    granted: PermissionManifest,
    available_secret_names: list[str],
) -> dict[str, str]:
    """Only inject secrets named in the grant list (values resolved by caller)."""
    allowed = set(granted.secrets)
    return {n: f"secret:{n}" for n in available_secret_names if n in allowed}


def assert_write_path_allowed(
    path: Path,
    *,
    task_dir: Path,
    manifest: PermissionManifest,
) -> None:
    """Raise if write target is outside granted write roots (task_dir only by default)."""
    target = path.resolve()
    allowed_roots: list[Path] = []
    for entry in manifest.fs_write:
        if entry == "{task_dir}":
            allowed_roots.append(task_dir.resolve())
        elif not entry.startswith("{"):
            allowed_roots.append(Path(entry).resolve())
    if not any(str(target).startswith(str(root)) for root in allowed_roots):
        raise PermissionError(f"write_denied:{target}")


def load_skill_declared_permissions(skill_dir: Path) -> dict[str, Any]:
    """Parse optional permissions from SKILL.md frontmatter or plugin yaml keys."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        plugin = skill_dir / "localwiki-plugin.yaml"
        if plugin.is_file():
            text = plugin.read_text(encoding="utf-8", errors="replace")
            # Minimal YAML-ish: look for permissions block via json sidecar if present
            side = skill_dir / "permissions.json"
            if side.is_file():
                return json.loads(side.read_text(encoding="utf-8"))
            return {}
        return {}
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            fm = text[3:end]
            # Very small frontmatter parse for permissions: json block
            if "permissions:" in fm:
                side = skill_dir / "permissions.json"
                if side.is_file():
                    return json.loads(side.read_text(encoding="utf-8"))
    side = skill_dir / "permissions.json"
    if side.is_file():
        return json.loads(side.read_text(encoding="utf-8"))
    return {}
