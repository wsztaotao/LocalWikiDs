"""Workbench named presets: local / deepseek / qwen (R3.8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PRESETS: dict[str, dict[str, str]] = {
    "local": {"label": "本地", "provider": "ollama_local", "model": ""},
    "deepseek": {"label": "DeepSeek", "provider": "deepseek", "model": ""},
    "qwen": {"label": "千问", "provider": "qwen", "model": ""},
}


def _config_path(workspace: Path) -> Path:
    return Path(workspace).resolve() / ".kw" / "config.json"


def load_presets(workspace: Path | str) -> dict[str, dict[str, str]]:
    root = Path(workspace).resolve()
    path = _config_path(root)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    raw = data.get("workbench_presets") or {}
    out: dict[str, dict[str, str]] = {}
    for key, default in DEFAULT_PRESETS.items():
        cur = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(cur, dict):
            cur = {}
        out[key] = {
            "label": str(cur.get("label") or default["label"]),
            "provider": str(cur.get("provider") or default["provider"]),
            "model": str(cur.get("model") or default["model"]),
        }
    return out


def save_presets(workspace: Path | str, presets: dict[str, Any]) -> dict[str, dict[str, str]]:
    root = Path(workspace).resolve()
    path = _config_path(root)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    merged = load_presets(root)
    for key in DEFAULT_PRESETS:
        if key not in presets or not isinstance(presets[key], dict):
            continue
        p = presets[key]
        merged[key] = {
            "label": str(p.get("label") or merged[key]["label"]),
            "provider": str(p.get("provider") or merged[key]["provider"]),
            "model": str(p.get("model") or ""),
        }
    data["workbench_presets"] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def _default_model_for_provider(workspace: Path, provider_id: str) -> str:
    """Pick first listed model when preset.model is empty."""
    from localwiki.personal_core import list_provider_models

    out = list_provider_models(workspace, provider_id)
    for m in out.get("models") or []:
        if isinstance(m, dict):
            name = str(m.get("name") or "").strip()
        else:
            name = str(m or "").strip()
        if name:
            return name
    return ""


def select_preset(workspace: Path | str, preset_id: str) -> dict[str, Any]:
    """Apply preset to agent route and persist."""
    from localwiki.model_routing import ModelRef, RouteConfig, load_workspace_routes, save_workspace_routes
    from localwiki.personal_core import CORE_TO_MODEL_ROUTE

    root = Path(workspace).resolve()
    pid = (preset_id or "").strip()
    presets = load_presets(root)
    if pid not in presets:
        raise ValueError(f"unknown preset_id: {preset_id}")
    preset = dict(presets[pid])
    provider = preset["provider"]
    model = (preset.get("model") or "").strip()
    cfg = load_workspace_routes(root)
    if provider and provider not in cfg.providers:
        raise ValueError(f"provider not configured: {provider}")
    if not model and provider:
        model = _default_model_for_provider(root, provider)
        preset["model"] = model
    if not model:
        raise ValueError(
            f"preset '{pid}' has no model; pick a model in Settings → 厂商与模型 → 工作台三预设"
        )
    mr = CORE_TO_MODEL_ROUTE["agent"]
    existing = cfg.routes.get(mr)
    fallback = list(existing.fallback) if existing else []
    privacy = existing.privacy if existing else "content"
    cfg.routes[mr] = RouteConfig(
        name=mr,
        primary=ModelRef(provider=provider, model=model),
        fallback=fallback,
        privacy=privacy,
    )
    save_workspace_routes(root, cfg)
    # remember last selected
    path = _config_path(root)
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data["workbench_selected_preset"] = pid
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"preset_id": pid, "preset": preset, "agent_route": mr}
