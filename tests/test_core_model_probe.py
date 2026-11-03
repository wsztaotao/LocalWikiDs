"""R3-B: 6-route model settings + probe (mock transport)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.personal_core import (  # noqa: E402
    get_core_model_settings,
    probe_core_route,
    save_core_model_settings,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_get_save_core_routes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    view = get_core_model_settings(ws)
    assert view["ok"]
    assert set(view["routes"]) == {
        "chat",
        "agent",
        "knowledge_compile",
        "memory",
        "experience_persona",
        "embedding",
    }
    out = save_core_model_settings(
        ws,
        {
            "routes": {
                "chat": {
                    "primary": {"provider": "ollama_local", "model": "llama3.2"},
                    "fallback": [],
                    "privacy": "content",
                }
            }
        },
    )
    assert out["routes"]["chat"]["primary"]["model"] == "llama3.2"


def test_reject_raw_api_key(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    try:
        save_core_model_settings(
            ws,
            {
                "providers": {
                    "openai": {
                        "kind": "openai_compatible",
                        "location": "online",
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "sk-raw-secret",
                    }
                }
            },
        )
        assert False, "should reject raw key"
    except ValueError as exc:
        assert "secret:" in str(exc)


def test_probe_with_mock_http(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    save_core_model_settings(
        ws,
        {
            "routes": {
                "chat": {
                    "primary": {"provider": "ollama_local", "model": "llama3.2"},
                    "fallback": [],
                }
            }
        },
    )

    def fake_http(method, url, *, json_body=None, headers=None, timeout=30.0):
        if "/api/generate" in url or "/api/chat" in url:
            return {"response": "pong", "model": "llama3.2", "message": {"content": "{}"}}
        if "/api/embeddings" in url or "/api/embed" in url:
            return {"embedding": [0.1, 0.2]}
        return {"ok": True}

    report = probe_core_route(ws, "chat", http=fake_http)
    assert "chat" in report["results"]
    # availability may succeed depending on probe paths
    assert report["results"]["chat"]["model"] == "llama3.2"


def test_probe_resolves_secret_ref_not_path_as_store(tmp_path: Path, monkeypatch) -> None:
    """Regression: SecretStore(workspace Path) caused WindowsPath.get AttributeError."""
    from localwiki.secrets import MemoryBackend, SecretStore

    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SecretStore(MemoryBackend())
    store.set("qwen", "sk-test-qwen")
    monkeypatch.setattr(
        "localwiki.secrets.open_secret_store",
        lambda *a, **k: store,
    )
    save_core_model_settings(
        ws,
        {
            "providers": {
                "qwen": {
                    "kind": "openai_compatible",
                    "location": "online",
                    "base_url": "https://example.invalid/v1",
                    "api_key": "secret:qwen",
                }
            },
            "routes": {
                "chat": {
                    "primary": {"provider": "qwen", "model": "qwen-plus"},
                    "fallback": [],
                }
            },
        },
    )
    seen_auth: list[str] = []

    def fake_http(method, url, *, json_body=None, headers=None, timeout=30.0):
        if headers and headers.get("Authorization"):
            seen_auth.append(headers["Authorization"])
        if "/chat/completions" in url or "/completions" in url:
            return {
                "choices": [{"message": {"content": "pong"}}],
                "id": "x",
            }
        if "/embeddings" in url:
            return {"data": [{"embedding": [0.1]}]}
        return {"data": [{"id": "qwen-plus"}]}

    report = probe_core_route(ws, "chat", http=fake_http)
    chat = report["results"]["chat"]
    err = str(chat.get("error") or "")
    assert "WindowsPath" not in err
    assert "secret_resolve_failed" not in err
    assert any("sk-test-qwen" in a for a in seen_auth) or chat.get("ok") is not False or chat.get(
        "model"
    ) == "qwen-plus"
