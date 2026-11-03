"""Privacy gate + fallback tests (LW-11 / §14.4 / §17.3) — AT-RT-03/04/06."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.model_routing import (  # noqa: E402
    ModelRef,
    ModelRoutesConfig,
    ProviderConfig,
    RouteConfig,
    RetryableModelError,
    UsageRecord,
    higher_content_class,
    privacy_allows,
    resolve_model_call,
)


def _cfg_local_then_online() -> ModelRoutesConfig:
    return ModelRoutesConfig(
        version=2,
        providers={
            "ollama_local": ProviderConfig(
                kind="ollama", location="local", base_url="http://127.0.0.1:11434"
            ),
            "openai_main": ProviderConfig(
                kind="openai_compatible",
                location="online",
                base_url="https://api.example.com/v1",
                api_key="secret:openai_main",
            ),
        },
        routes={
            "memory_extract": RouteConfig(
                name="memory_extract",
                primary=ModelRef(provider="ollama_local", model="local-m"),
                fallback=[ModelRef(provider="openai_main", model="gpt-x")],
                privacy="prefer_local",
            ),
            "chat": RouteConfig(
                name="chat",
                primary=ModelRef(provider="openai_main", model="gpt-x"),
                fallback=[],
                privacy="content",
            ),
        },
    )


def test_matrix_sensitive_blocks_online_by_default() -> None:
    ok, reason = privacy_allows(
        "sensitive",
        "online",
        mode="online",
        route="chat",
        route_privacy="content",
        online_authorized=True,
        sensitive_authorized=False,
    )
    assert ok is False
    assert "sensitive" in reason


def test_matrix_private_needs_authorization_for_online() -> None:
    ok, _ = privacy_allows(
        "private",
        "online",
        mode="local_first",
        route="memory_extract",
        route_privacy="prefer_local",
        online_authorized=False,
    )
    assert ok is False
    ok2, _ = privacy_allows(
        "private",
        "online",
        mode="local_first",
        route="memory_extract",
        route_privacy="prefer_local",
        online_authorized=True,
    )
    assert ok2 is True


def test_local_always_allowed() -> None:
    for cls in ("public", "normal", "private", "sensitive"):
        ok, _ = privacy_allows(
            cls,  # type: ignore[arg-type]
            "local",
            mode="offline",
            route="chat",
            route_privacy="content",
        )
        assert ok is True


def test_at_rt_03_fallback_cannot_bypass_privacy() -> None:
    """Local fails; online fallback present; private without auth → E_PRIVACY_BLOCKED."""
    cfg = _cfg_local_then_online()
    calls: list[str] = []

    def call_fn(ref: ModelRef, provider: ProviderConfig) -> dict[str, Any]:
        calls.append(f"{ref.provider}/{ref.model}")
        if provider.location == "local":
            raise RetryableModelError("local down")
        return {"text": "should_not_reach"}

    result = resolve_model_call(
        cfg,
        "memory_extract",
        content_class="private",
        mode="local_first",
        online_authorized=False,
        call_fn=call_fn,
    )
    assert result.ok is False
    assert result.error_code == "E_PRIVACY_BLOCKED"
    assert result.response is None
    assert calls == ["ollama_local/local-m"]  # online never invoked
    assert any(s.reason.startswith("privacy:") for s in result.skipped)
    assert "private" in (result.error_message or "").lower() or "private" in str(
        result.skipped
    )


def test_at_rt_03_authorized_allows_online_fallback() -> None:
    cfg = _cfg_local_then_online()
    calls: list[str] = []

    def call_fn(ref: ModelRef, provider: ProviderConfig) -> dict[str, Any]:
        calls.append(ref.provider)
        if provider.location == "local":
            raise RetryableModelError("local down")
        return {"text": "ok", "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    result = resolve_model_call(
        cfg,
        "memory_extract",
        content_class="private",
        mode="local_first",
        online_authorized=True,
        call_fn=call_fn,
    )
    assert result.ok is True
    assert result.used_fallback is True
    assert result.model_ref and result.model_ref.provider == "openai_main"
    assert calls == ["ollama_local", "openai_main"]
    assert result.usage.kind == "reported"
    assert result.usage.prompt_tokens == 3


def test_at_rt_04_offline_skips_all_online() -> None:
    cfg = _cfg_local_then_online()
    calls: list[str] = []

    def call_fn(ref: ModelRef, provider: ProviderConfig) -> dict[str, Any]:
        calls.append(ref.provider)
        return {"text": "hi"}

    # Only online primary (chat)
    result = resolve_model_call(
        cfg,
        "chat",
        content_class="public",
        mode="offline",
        call_fn=call_fn,
    )
    assert result.ok is False
    assert result.error_code in ("E_PRIVACY_BLOCKED", "E_OFFLINE", "E_MODEL_UNAVAILABLE")
    assert calls == []
    assert any(s.reason == "offline_mode" for s in result.skipped)


def test_at_rt_06_unknown_usage_not_zero() -> None:
    cfg = _cfg_local_then_online()

    def call_fn(ref: ModelRef, provider: ProviderConfig) -> dict[str, Any]:
        return {"text": "hi"}  # no usage field

    result = resolve_model_call(
        cfg,
        "memory_extract",
        content_class="normal",
        mode="local_first",
        call_fn=call_fn,
    )
    assert result.ok is True
    assert result.usage.kind == "unknown"
    assert result.usage.prompt_tokens is None
    assert result.usage.completion_tokens is None
    # Must not coerce unknown → 0
    assert result.usage.as_log_dict()["prompt_tokens"] is None
    assert result.usage.as_log_dict()["kind"] == "unknown"


def test_higher_content_class_inherits_max() -> None:
    assert higher_content_class("public", "private") == "private"
    assert higher_content_class("sensitive", "normal") == "sensitive"
    assert higher_content_class() == "public"


def test_public_only_route_blocks_private_online() -> None:
    ok, reason = privacy_allows(
        "private",
        "online",
        mode="online",
        route="skill_search_rerank",
        route_privacy="public_only",
        online_authorized=True,
    )
    assert ok is False
    assert "public_only" in reason


def test_usage_record_unknown_default() -> None:
    u = UsageRecord.unknown()
    assert u.kind == "unknown"
    d = u.as_log_dict()
    assert d["prompt_tokens"] is None
    assert d["completion_tokens"] is None
