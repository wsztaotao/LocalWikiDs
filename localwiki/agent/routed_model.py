"""HTTP model client wired from workspace 6-route config (R3.5 — no stub success)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ModelNotConfiguredError(RuntimeError):
    def __init__(self, message: str, *, code: str = "E_MODEL_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _resolve_api_key(workspace: Path, raw: str) -> str:
    if not raw:
        return ""
    if raw.startswith("secret:"):
        try:
            from localwiki.secrets import open_secret_store

            return open_secret_store(workspace).resolve(raw)
        except Exception as exc:  # noqa: BLE001
            raise ModelNotConfiguredError(
                f"cannot resolve secret: {exc}", code="E_SECRET"
            ) from exc
    return raw


def build_routed_model(workspace: Path | str, *, core_route: str = "agent") -> "RoutedHttpModel":
    """Build a real HTTP model from CORE_ROUTES; raise if missing/incomplete."""
    from localwiki.personal_core import CORE_ROUTES, CORE_TO_MODEL_ROUTE
    from localwiki.model_routing import load_workspace_routes

    root = Path(workspace).resolve()
    if core_route not in CORE_ROUTES:
        raise ModelNotConfiguredError(f"unknown core route: {core_route}")
    cfg = load_workspace_routes(root)
    mr = CORE_TO_MODEL_ROUTE[core_route]
    route = cfg.routes.get(mr) or cfg.routes.get("chat")
    # Agent with empty model: fall back to chat (common after preset with blank model)
    if (
        core_route == "agent"
        and (route is None or not (route.primary.model or "").strip())
    ):
        chat = cfg.routes.get("chat")
        if chat is not None and (chat.primary.model or "").strip():
            route = chat
            mr = "chat"
    if route is None or not (route.primary.model or "").strip():
        raise ModelNotConfiguredError(
            f"route '{core_route}' has no model configured; "
            "set 六路路由 · 智能体 or pick a workbench preset with a model",
            code="E_MODEL_UNAVAILABLE",
        )
    provider = cfg.providers.get(route.primary.provider)
    if provider is None:
        raise ModelNotConfiguredError(
            f"provider '{route.primary.provider}' missing for route '{core_route}'",
            code="E_MODEL_UNAVAILABLE",
        )
    base = (provider.base_url or "").rstrip("/")
    if not base:
        if provider.kind == "ollama":
            base = "http://127.0.0.1:11434"
        elif provider.kind == "anthropic":
            base = "https://api.anthropic.com"
        else:
            raise ModelNotConfiguredError(
                f"provider '{route.primary.provider}' missing base_url",
                code="E_MODEL_UNAVAILABLE",
            )
    api_key = _resolve_api_key(root, provider.api_key or "")
    return RoutedHttpModel(
        kind=provider.kind,
        base_url=base,
        model=route.primary.model,
        api_key=api_key,
        provider_name=route.primary.provider,
        route_name=core_route,
    )


def agent_route_env_injection(workspace: Path | str) -> dict[str, str]:
    """Env vars to inject into OpenCode from the agent core route (Q8A)."""
    model = build_routed_model(workspace, core_route="agent")
    env: dict[str, str] = {
        "LOCALWIKI_AGENT_MODEL": model.model,
        "LOCALWIKI_AGENT_PROVIDER": model.provider_name,
        "LOCALWIKI_AGENT_BASE_URL": model.base_url,
    }
    # OpenAI-compatible conventions commonly used by CLI agents
    if model.kind in ("openai_compatible", "openai", "ollama"):
        env["OPENAI_BASE_URL"] = (
            model.base_url
            if model.kind != "ollama"
            else model.base_url.rstrip("/") + "/v1"
        )
        env["OPENAI_API_KEY"] = model.api_key or "ollama"
        env["OPENAI_MODEL"] = model.model
    if model.kind == "anthropic":
        env["ANTHROPIC_API_KEY"] = model.api_key or ""
        env["ANTHROPIC_MODEL"] = model.model
        if model.base_url:
            env["ANTHROPIC_BASE_URL"] = model.base_url
    if model.api_key:
        env["LOCALWIKI_AGENT_API_KEY"] = model.api_key
    return env


class RoutedHttpModel:
    """Minimal OpenAI-compatible / Ollama chat client for AgentLoop."""

    def __init__(
        self,
        *,
        kind: str,
        base_url: str,
        model: str,
        api_key: str = "",
        provider_name: str = "",
        route_name: str = "agent",
        timeout: float = 120.0,
    ) -> None:
        self.kind = kind
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider_name = provider_name
        self.route_name = route_name
        self.timeout = timeout

    def generate(self, messages: list[dict[str, Any]], *, tools: Any, extras: dict[str, Any]) -> Any:
        if self.kind == "anthropic" and "api.anthropic.com" in self.base_url:
            return self._generate_anthropic(messages, tools=tools)

        if self.kind == "ollama" and "/v1" not in self.base_url:
            url = f"{self.base_url}/api/chat"
            body: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": False,
            }
            if tools:
                body["tools"] = tools
            data = self._post(url, body, auth=False)
            msg = (data or {}).get("message") or {}
            return {
                "choices": [
                    {
                        "message": {
                            "role": msg.get("role") or "assistant",
                            "content": msg.get("content") or "",
                            "tool_calls": msg.get("tool_calls"),
                        },
                        "finish_reason": "stop",
                    }
                ]
            }

        base = self.base_url
        if self.kind == "ollama" and not base.endswith("/v1"):
            base = base + "/v1"
        url = f"{base}/chat/completions"
        body = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        return self._post(url, body, auth=True)

    def _generate_anthropic(self, messages: list[dict[str, Any]], *, tools: Any) -> Any:
        # Convert OpenAI-ish messages → Anthropic messages API
        system = ""
        converted: list[dict[str, Any]] = []
        for m in messages:
            role = str(m.get("role") or "user")
            content = m.get("content") or ""
            if role == "system":
                system = str(content) if not system else system + "\n" + str(content)
                continue
            if role == "tool":
                continue
            if role not in ("user", "assistant"):
                role = "user"
            converted.append({"role": role, "content": content})
        if not converted:
            converted = [{"role": "user", "content": "hello"}]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": converted,
        }
        if system:
            body["system"] = system
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        data = self._post(url, body, auth=False, anthropic=True)
        text = ""
        for block in (data or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text") or "")
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": text, "tool_calls": None},
                    "finish_reason": "stop",
                }
            ]
        }

    def _post(
        self,
        url: str,
        body: dict[str, Any],
        *,
        auth: bool,
        anthropic: bool = False,
    ) -> Any:
        headers = {"Content-Type": "application/json"}
        if anthropic:
            headers["x-api-key"] = self.api_key or ""
            headers["anthropic-version"] = "2023-06-01"
        elif auth and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ModelNotConfiguredError(
                f"model HTTP {exc.code}: {detail}", code="E_MODEL_HTTP"
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelNotConfiguredError(
                f"model unreachable: {exc}", code="E_MODEL_HTTP"
            ) from exc
