"""Workspace layout helpers (R1 spec §3)."""

from __future__ import annotations

import json
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any

# Package root: localwiki/ → repo root
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_TEMPLATES = _REPO_ROOT / "schema_templates"
_WEB_DIR = _REPO_ROOT / "web"


def workspace_paths(root: Path) -> dict[str, Path]:
    root = root.resolve()
    kw = root / ".kw"
    return {
        "root": root,
        "schema": root / "schema",
        "wiki": root / "wiki",
        "kw": kw,
        "config": kw / "config.json",
        "history": kw / "history",
        "objects": kw / "history" / "objects",
        "commits": kw / "history" / "commits",
        "head": kw / "history" / "HEAD",
        "changesets": kw / "changesets",
        "transactions": kw / "transactions",
        "cache": kw / "cache",
        "index_db": kw / "cache" / "index.sqlite",
        "lock": kw / "runtime" / "workspace.lock",
        "runtime": kw / "runtime",
    }


def ensure_workspace(root: Path | str) -> dict[str, Path]:
    """Create minimal workspace dirs; do not overwrite existing user files."""
    root = Path(root).resolve()
    paths = workspace_paths(root)
    for key in (
        "schema",
        "wiki",
        "kw",
        "history",
        "objects",
        "commits",
        "changesets",
        "transactions",
        "cache",
        "runtime",
    ):
        paths[key].mkdir(parents=True, exist_ok=True)

    index_md = paths["wiki"] / "index.md"
    if not index_md.exists():
        index_md.write_text(
            "---\nokf_version: \"0.2\"\n---\n\n# 知识目录\n\n- 尚无页面。\n",
            encoding="utf-8",
        )
    log_md = paths["wiki"] / "log.md"
    if not log_md.exists():
        log_md.write_text("# 知识更新日志\n\n", encoding="utf-8")

    if _SCHEMA_TEMPLATES.is_dir():
        for src in _SCHEMA_TEMPLATES.iterdir():
            if src.is_file():
                dest = paths["schema"] / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)

    cfg = load_config(paths["config"])
    if not cfg.get("workspace_uid"):
        cfg["workspace_uid"] = f"ws_{uuid.uuid4().hex[:12]}"
    if not cfg.get("api_token"):
        cfg["api_token"] = secrets.token_urlsafe(24)
    if "port" not in cfg:
        cfg["port"] = 8765
    save_config(paths["config"], cfg)

    if not paths["head"].exists():
        paths["head"].write_text("snap_000000\n", encoding="utf-8")

    return paths


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def save_config(config_path: Path, cfg: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_head(root: Path | str) -> str:
    paths = workspace_paths(Path(root))
    if not paths["head"].exists():
        return "snap_000000"
    return paths["head"].read_text(encoding="utf-8").strip() or "snap_000000"


def web_static_dir() -> Path:
    return _WEB_DIR
