"""R3.8 patch: real key_configured + agent→chat model fallback."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.routed_model import build_routed_model  # noqa: E402
from localwiki.model_routing import (  # noqa: E402
    ModelRef,
    ProviderConfig,
    RouteConfig,
    load_workspace_routes,
    save_workspace_routes,
)
from localwiki.personal_core import get_core_model_settings  # noqa: E402
from localwiki.secrets import MemoryBackend, SecretStore  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_key_configured_false_when_secret_missing(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SecretStore(MemoryBackend())
    monkeypatch.setattr(
        "localwiki.secrets.open_secret_store",
        lambda *a, **k: store,
    )
    cfg = load_workspace_routes(ws)
    cfg.providers["openai"] = ProviderConfig(
        kind="openai",
        location="online",
        base_url="https://api.openai.com/v1",
        api_key="secret:openai",
    )
    cfg.providers["anthropic"] = ProviderConfig(
        kind="anthropic",
        location="online",
        base_url="",
        api_key="secret:anthropic",
    )
    save_workspace_routes(ws, cfg)
    settings = get_core_model_settings(ws)
    assert settings["providers"]["openai"]["key_configured"] is False
    assert settings["providers"]["openai"]["key_label"] == "未配置 Key"
    assert settings["providers"]["anthropic"]["key_configured"] is False


def test_key_configured_true_when_secret_present(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SecretStore(MemoryBackend())
    store.set("deepseek", "sk-test-value")
    monkeypatch.setattr(
        "localwiki.secrets.open_secret_store",
        lambda *a, **k: store,
    )
    cfg = load_workspace_routes(ws)
    cfg.providers["deepseek"] = ProviderConfig(
        kind="openai_compatible",
        location="online",
        base_url="https://api.deepseek.com/v1",
        api_key="secret:deepseek",
    )
    save_workspace_routes(ws, cfg)
    settings = get_core_model_settings(ws)
    assert settings["providers"]["deepseek"]["key_configured"] is True
    assert settings["providers"]["deepseek"]["api_key"] == "secret:deepseek"


def test_agent_falls_back_to_chat_model(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    cfg = load_workspace_routes(ws)
    cfg.providers["ollama_local"] = ProviderConfig(
        kind="ollama",
        location="local",
        base_url="http://127.0.0.1:11434",
        api_key="",
    )
    cfg.routes["chat"] = RouteConfig(
        name="chat",
        primary=ModelRef(provider="ollama_local", model="llama3.2:latest"),
        fallback=[],
        privacy="content",
    )
    cfg.routes["agent"] = RouteConfig(
        name="agent",
        primary=ModelRef(provider="ollama_local", model=""),
        fallback=[],
        privacy="content",
    )
    save_workspace_routes(ws, cfg)
    m = build_routed_model(ws, core_route="agent")
    assert m.model == "llama3.2:latest"


def test_resolve_secret_via_open_store(tmp_path: Path, monkeypatch) -> None:
    from localwiki.agent.routed_model import _resolve_api_key
    from localwiki.secrets import MemoryBackend, SecretStore

    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SecretStore(MemoryBackend())
    store.set("qwen", "sk-live")
    monkeypatch.setattr(
        "localwiki.secrets.open_secret_store",
        lambda *a, **k: store,
    )
    assert _resolve_api_key(ws, "secret:qwen") == "sk-live"
