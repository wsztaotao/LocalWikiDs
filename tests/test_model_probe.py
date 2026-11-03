"""Tests for capability probes (LW-10 / §14.3) — recorded HTTP, no live calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.model_routing import (  # noqa: E402
    PROBE_NAMES,
    ProviderConfig,
    default_v2_config,
    probe_model,
    probe_ollama_model,
    probe_openai_compatible,
    route_capability_warnings,
)


def _ollama_http(method: str, url: str, **kwargs: Any) -> Any:
    body = kwargs.get("json_body")
    if url.endswith("/api/generate") and body and body.get("format"):
        return {"response": json.dumps({"ok": True})}
    if url.endswith("/api/generate"):
        return {"response": "pong", "model": body.get("model")}
    if url.endswith("/api/chat"):
        if body and body.get("tools"):
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "ping", "arguments": "{}"}}
                    ],
                }
            }
        if body and body.get("messages") and body["messages"][0].get("images"):
            return {"message": {"role": "assistant", "content": "a pixel"}}
        return {"message": {"role": "assistant", "content": "hi"}}
    if url.endswith("/api/embed"):
        return {"embeddings": [[0.1] * 8]}
    if url.endswith("/api/show"):
        return {
            "model_info": {"llama.context_length": 8192},
            "details": {},
        }
    raise AssertionError(f"unexpected ollama url {url}")


def _openai_http(method: str, url: str, **kwargs: Any) -> Any:
    body = kwargs.get("json_body") or {}
    if url.endswith("/chat/completions"):
        if body.get("tools"):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "type": "function",
                                    "function": {"name": "ping", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }
        if body.get("response_format"):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}
        content = body.get("messages", [{}])[0].get("content")
        if isinstance(content, list):
            return {"choices": [{"message": {"content": "image seen"}}]}
        return {"id": "cmpl", "choices": [{"message": {"content": "pong"}}]}
    if url.endswith("/embeddings"):
        return {"data": [{"embedding": [0.2] * 4}]}
    if url.endswith("/models"):
        return {"data": [{"id": "gpt-test", "context_length": 128000}]}
    raise AssertionError(f"unexpected openai url {url}")


def test_ollama_six_probes() -> None:
    report = probe_ollama_model(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        http=_ollama_http,
        num_ctx=4096,
    )
    assert set(PROBE_NAMES) <= set(report.results)
    for name in PROBE_NAMES:
        assert report.results[name].ok, (name, report.results[name])
    assert report.results["embedding_dim"].detail["dimension"] == 8
    assert report.results["actual_context"].detail["effective_context"] == 4096


def test_openai_compatible_six_probes() -> None:
    report = probe_openai_compatible(
        base_url="https://api.example.com/v1",
        model="gpt-test",
        api_key="secret:openai_main",
        http=_openai_http,
    )
    for name in PROBE_NAMES:
        assert report.results[name].ok, (name, report.results[name])
    assert report.results["embedding_dim"].detail["dimension"] == 4


def test_probe_model_dispatch() -> None:
    ollama = ProviderConfig(
        kind="ollama", location="local", base_url="http://127.0.0.1:11434"
    )
    report = probe_model(ollama, "m", http=_ollama_http, num_ctx=2048)
    assert report.kind == "ollama"
    assert all(report.results[n].ok for n in PROBE_NAMES)

    oai = ProviderConfig(
        kind="openai_compatible",
        location="online",
        base_url="https://api.example.com/v1",
        api_key="secret:k",
    )
    report2 = probe_model(oai, "gpt-test", http=_openai_http)
    assert report2.kind == "openai_compatible"
    assert all(report2.results[n].ok for n in PROBE_NAMES)


def test_route_capability_warnings() -> None:
    cfg = default_v2_config()
    caps = {
        "ollama_local/": {"streaming", "structured_output", "embedding"},
    }
    warnings = route_capability_warnings(cfg, caps)
    assert any("agent" in w and "tool_calling" in w for w in warnings)
    assert "agent_json_fallback_protocol" in warnings
