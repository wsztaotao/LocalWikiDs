"""R3.8 workbench presets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.model_routing import (  # noqa: E402
    ModelRef,
    ProviderConfig,
    RouteConfig,
    load_workspace_routes,
    save_workspace_routes,
)
from localwiki.workbench_presets import load_presets, save_presets, select_preset  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_select_preset_rewrites_agent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    cfg = load_workspace_routes(ws)
    cfg.providers["deepseek"] = ProviderConfig(
        kind="openai_compatible",
        location="online",
        base_url="https://api.deepseek.com/v1",
        api_key="secret:ds",
    )
    cfg.routes["agent"] = RouteConfig(
        name="agent",
        primary=ModelRef(provider="ollama_local", model="llama"),
        fallback=[],
        privacy="content",
    )
    save_workspace_routes(ws, cfg)
    save_presets(
        ws,
        {"deepseek": {"label": "DeepSeek", "provider": "deepseek", "model": "deepseek-chat"}},
    )
    out = select_preset(ws, "deepseek")
    assert out["preset_id"] == "deepseek"
    cfg2 = load_workspace_routes(ws)
    assert cfg2.routes["agent"].primary.provider == "deepseek"
    assert cfg2.routes["agent"].primary.model == "deepseek-chat"
    assert "deepseek" in load_presets(ws)


def test_select_preset_autofills_empty_model(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    cfg = load_workspace_routes(ws)
    cfg.providers["qwen"] = ProviderConfig(
        kind="openai_compatible",
        location="online",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="",
    )
    save_workspace_routes(ws, cfg)
    save_presets(ws, {"qwen": {"label": "千问", "provider": "qwen", "model": ""}})
    out = select_preset(ws, "qwen")
    assert out["preset"]["model"]  # suggestions fill qwen-plus etc.
    cfg2 = load_workspace_routes(ws)
    assert cfg2.routes["agent"].primary.model == out["preset"]["model"]
