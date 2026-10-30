"""R3.6: Anthropic native + compatible probe paths."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.model_routing import ProviderConfig, probe_model  # noqa: E402


def test_anthropic_native_probe_mock() -> None:
    calls: list[str] = []

    def http(method: str, url: str, **kwargs):
        calls.append(url)
        assert "messages" in url
        return {"content": [{"type": "text", "text": "pong"}]}

    report = probe_model(
        ProviderConfig(kind="anthropic", location="online", base_url="", api_key="sk-test"),
        "claude-3-5-haiku-latest",
        http=http,
        api_key="sk-test",
    )
    assert report.kind == "anthropic"
    assert report.results["availability"].ok is True
    assert calls and "api.anthropic.com" in calls[0]


def test_anthropic_compatible_gateway() -> None:
    def http(method: str, url: str, **kwargs):
        if url.endswith("/chat/completions") or "/chat/completions" in url:
            return {
                "choices": [
                    {"message": {"content": "hi", "tool_calls": [{"id": "1"}]}}
                ]
            }
        if url.endswith("/embeddings") or "/embeddings" in url:
            return {"data": [{"embedding": [0.1, 0.2]}]}
        if url.endswith("/models") or url.rstrip("/").endswith("/models"):
            return {"data": [{"id": "claude-proxy", "context_length": 8192}]}
        return {}

    report = probe_model(
        ProviderConfig(
            kind="anthropic",
            location="online",
            base_url="https://gateway.example/v1",
            api_key="sk",
        ),
        "claude-proxy",
        http=http,
        api_key="sk",
    )
    # Compatible path uses openai_compatible report kind
    assert report.kind == "openai_compatible"
    assert report.results["availability"].ok is True


def test_anthropic_missing_key_fails_honestly() -> None:
    report = probe_model(
        ProviderConfig(kind="anthropic", location="online", base_url="", api_key=""),
        "claude-3-5-haiku-latest",
        api_key="",
    )
    assert report.results["availability"].ok is False
