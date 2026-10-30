"""Installed Skill registry (``.kw/skills/registry.json``, §13.6)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def tree_sha256(root: Path) -> str:
    """Content hash of all files under root (deterministic)."""
    h = hashlib.sha256()
    if not root.is_dir():
        return h.hexdigest()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


@dataclass
class SkillRecord:
    name: str
    kind: str
    source: dict[str, Any]
    tree_sha256: str
    installed_at: str
    scan: dict[str, Any] = field(default_factory=dict)
    granted: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    pinned: bool = True
    disabled_reason: str | None = None
    install_path: str | None = None
    version_or_commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillRecord":
        return cls(
            name=str(data["name"]),
            kind=str(data.get("kind") or "agent_skill"),
            source=dict(data.get("source") or {}),
            tree_sha256=str(data.get("tree_sha256") or ""),
            installed_at=str(data.get("installed_at") or ""),
            scan=dict(data.get("scan") or {}),
            granted=dict(data.get("granted") or {}),
            enabled=bool(data.get("enabled", True)),
            pinned=bool(data.get("pinned", True)),
            disabled_reason=data.get("disabled_reason"),
            install_path=data.get("install_path"),
            version_or_commit=data.get("version_or_commit"),
        )


class SkillRegistry:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / ".kw" / "skills" / "registry.json"
        self.skills_root = self.workspace / ".kw" / "skills"
        self._skills: dict[str, SkillRecord] = {}
        self.load()

    def load(self) -> None:
        self._skills = {}
        if not self.path.is_file():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for item in data.get("skills") or []:
            rec = SkillRecord.from_dict(item)
            self._skills[rec.name] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"skills": [s.to_dict() for s in sorted(self._skills.values(), key=lambda x: x.name)]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def upsert(self, record: SkillRecord) -> None:
        self._skills[record.name] = record
        self.save()

    def get(self, name: str) -> SkillRecord | None:
        return self._skills.get(name)

    def list(self, *, enabled_only: bool = False) -> list[SkillRecord]:
        out = list(self._skills.values())
        if enabled_only:
            out = [s for s in out if s.enabled]
        return sorted(out, key=lambda s: s.name)

    def remove(self, name: str) -> SkillRecord | None:
        rec = self._skills.pop(name, None)
        if rec:
            self.save()
        return rec

    def verify_integrity(self) -> list[SkillRecord]:
        """Disable skills whose tree hash no longer matches (tamper detection)."""
        tampered: list[SkillRecord] = []
        for rec in list(self._skills.values()):
            if not rec.install_path:
                continue
            root = Path(rec.install_path)
            if not root.is_dir():
                root = self.skills_root / f"{rec.name}@{rec.version_or_commit or 'unknown'}"
            if not root.is_dir():
                rec.enabled = False
                rec.disabled_reason = "missing_install_dir"
                tampered.append(rec)
                continue
            current = tree_sha256(root)
            if rec.tree_sha256 and current != rec.tree_sha256:
                rec.enabled = False
                rec.disabled_reason = "tampered:tree_sha256_mismatch"
                tampered.append(rec)
        if tampered:
            self.save()
        return tampered


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
