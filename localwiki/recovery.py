"""Backup / verify / restore-to-new-dir (R2). Secrets excluded."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from localwiki.diagnostics import doctor
from localwiki.workspace import ensure_workspace

# Paths relative to workspace root that must never enter backups
SECRET_GLOBS = (
    ".kw/secrets.bin",
    ".kw/config.json",  # may contain api_token — strip token on backup via rewrite
)


class RecoveryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _iter_backup_files(root: Path) -> list[Path]:
    root = root.resolve()
    out: list[Path] = []
    for rel in ("wiki", "schema", ".kw/history", ".kw/changesets", ".kw/transactions", ".kw/cache"):
        base = root / rel
        if not base.exists():
            continue
        if base.is_file():
            out.append(base)
            continue
        for p in base.rglob("*"):
            if p.is_file():
                out.append(p)
    return out


def backup_workspace(
    root: Path | str,
    dest: Path | str,
    *,
    as_zip: bool = True,
) -> dict[str, Any]:
    """Backup to a path **outside** the workspace. Never includes secrets.bin; config token redacted."""
    root = Path(root).resolve()
    dest = Path(dest).resolve()
    if not root.is_dir():
        raise RecoveryError("E_NOT_FOUND", f"workspace missing: {root}")
    try:
        dest.relative_to(root)
        raise RecoveryError("E_INVALID_INPUT", "backup destination must be outside workspace")
    except ValueError:
        pass  # not under root — good
    except RecoveryError:
        raise

    stamp = _utc_stamp()
    if as_zip:
        if dest.suffix.lower() != ".zip":
            dest = dest / f"localwiki-backup-{stamp}.zip" if dest.is_dir() or not dest.suffix else dest
            if dest.suffix.lower() != ".zip":
                dest = Path(str(dest) + ".zip")
        dest.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "created_at": stamp,
            "source_workspace": str(root),
            "files": [],
            "secrets_excluded": True,
        }
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in _iter_backup_files(root):
                rel = path.relative_to(root).as_posix()
                if rel.endswith("secrets.bin"):
                    continue
                if rel == ".kw/config.json":
                    cfg = json.loads(path.read_text(encoding="utf-8"))
                    cfg = {**cfg, "api_token": None, "token_redacted": True}
                    zf.writestr(rel, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
                else:
                    zf.write(path, rel)
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
                if rel == ".kw/config.json":
                    digest = "redacted"
                manifest["files"].append({"path": rel, "sha256": digest})
            zf.writestr(
                "backup-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
        return {"ok": True, "path": str(dest), "kind": "zip", "manifest": manifest}

    # Directory copy
    if dest.exists():
        raise RecoveryError("E_INVALID_INPUT", f"dest exists: {dest}")
    dest.mkdir(parents=True)
    for path in _iter_backup_files(root):
        rel = path.relative_to(root)
        if rel.as_posix().endswith("secrets.bin"):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.as_posix() == ".kw/config.json":
            cfg = json.loads(path.read_text(encoding="utf-8"))
            cfg = {**cfg, "api_token": None, "token_redacted": True}
            target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            shutil.copy2(path, target)
    manifest = {"created_at": stamp, "source_workspace": str(root), "secrets_excluded": True}
    (dest / "backup-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"ok": True, "path": str(dest), "kind": "dir", "manifest": manifest}


def verify_backup(path: Path | str) -> dict[str, Any]:
    path = Path(path).resolve()
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "backup-manifest.json" not in names:
                return {"ok": False, "error": "missing manifest"}
            if any(n.endswith("secrets.bin") for n in names):
                return {"ok": False, "error": "secrets present in backup"}
            man = json.loads(zf.read("backup-manifest.json"))
            return {"ok": True, "kind": "zip", "files": len(names), "manifest": man}
    if path.is_dir():
        man_path = path / "backup-manifest.json"
        if not man_path.exists():
            return {"ok": False, "error": "missing manifest"}
        if (path / ".kw" / "secrets.bin").exists():
            return {"ok": False, "error": "secrets present in backup"}
        man = json.loads(man_path.read_text(encoding="utf-8"))
        return {"ok": True, "kind": "dir", "manifest": man}
    return {"ok": False, "error": "not a backup zip or directory"}


def restore_backup(
    backup: Path | str,
    new_workspace: Path | str,
) -> dict[str, Any]:
    """Restore only into a **non-existent** new directory, then doctor."""
    backup = Path(backup).resolve()
    dest = Path(new_workspace).resolve()
    if dest.exists():
        raise RecoveryError("E_INVALID_INPUT", "restore target must not exist")
    v = verify_backup(backup)
    if not v.get("ok"):
        raise RecoveryError("E_INVALID_INPUT", str(v.get("error")))

    dest.mkdir(parents=True)
    if backup.is_file() and backup.suffix.lower() == ".zip":
        with zipfile.ZipFile(backup, "r") as zf:
            zf.extractall(dest)
    else:
        for p in backup.rglob("*"):
            if p.is_file():
                rel = p.relative_to(backup)
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)

    ensure_workspace(dest)  # refill token etc. without wiping wiki
    report = doctor(dest, ensure=False)
    return {"ok": True, "workspace": str(dest), "doctor": report}
