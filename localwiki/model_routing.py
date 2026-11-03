"""Model routing v2: 13 feature routes + migration + probe + privacy gate.

LW-09: config / migrate. LW-10: capability probe. LW-11: privacy gate + fallback (§14.4 / §17.3).
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

Location = Literal["local", "online"]
ContentClass = Literal["public", "normal", "private", "sensitive"]
PrivacyMode = Literal["offline", "local_first", "online"]
UsageKind = Literal["reported", "estimated", "unknown"]

CONTENT_CLASS_RANK: dict[str, int] = {
    "public": 0,
    "normal": 1,
    "private": 2,
    "sensitive": 3,
}

# Canonical 13 routes from §14.1
ROUTE_NAMES: tuple[str, ...] = (
    "chat",
    "agent",
    "knowledge_compile",
    "wiki_lint",
    "memory_extract",
    "memory_consolidate",
    "experience",
    "persona",
    "embedding",
    "vision",
    "skill_search_rerank",
    "nightly",
    "reflection",
)

# v0.11 6-way task routes → v0.12 targets
V11_ROUTE_MAP: dict[str, tuple[str, ...]] = {
    "chat": ("chat",),
    "agent": ("agent",),
    "knowledge": ("knowledge_compile", "wiki_lint"),
    "experience": ("experience",),
    "nightly": ("memory_consolidate", "nightly"),
    "persona": ("memory_extract", "persona"),
}

# Aliases accepted when loading (migrate script historically used these names)
ROUTE_ALIASES: dict[str, str] = {
    "wiki_compile": "knowledge_compile",
    "wiki_query": "knowledge_compile",  # query shares compile family until dedicated route
}

DEFAULT_PRIVACY: dict[str, str] = {
    "chat": "content",
    "agent": "task",
    "knowledge_compile": "material",
    "wiki_lint": "material",
    "memory_extract": "prefer_local",
    "memory_consolidate": "prefer_local",
    "experience": "prefer_local",
    "persona": "prefer_local",
    "embedding": "prefer_local",
    "vision": "content",
    "skill_search_rerank": "public_only",
    "nightly": "task",
    "reflection": "prefer_local",
}

REQUIRED_CAPABILITIES: dict[str, list[str]] = {
    "chat": ["streaming"],
    "agent": ["tool_calling"],
    "knowledge_compile": ["structured_output"],
    "wiki_lint": ["structured_output"],
    "memory_extract": ["structured_output"],
    "memory_consolidate": ["structured_output"],
    "experience": ["structured_output"],
    "persona": ["structured_output"],
    "embedding": ["embedding"],
    "vision": ["vision"],
    "skill_search_rerank": [],
    "nightly": ["structured_output"],
    "reflection": ["structured_output"],
}


@dataclass
class ModelRef:
    provider: str
    model: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"provider": self.provider, "model": self.model}
        if self.options:
            d["options"] = dict(self.options)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ModelRef | None:
        if not data or not isinstance(data, dict):
            return None
        return cls(
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            options=dict(data.get("options") or {}),
        )


@dataclass
class ProviderConfig:
    kind: str
    location: Location
    base_url: str = ""
    api_key: str = ""  # secret:name reference only — never a raw secret

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "location": self.location}
        if self.base_url:
            d["base_url"] = self.base_url
        if self.api_key:
            d["api_key"] = self.api_key
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderConfig:
        loc = str(data.get("location") or "online")
        if loc not in ("local", "online"):
            loc = "online"
        # base_url heuristic: non-loopback Ollama → online
        base = str(data.get("base_url") or "")
        kind = str(data.get("kind") or "")
        if kind == "ollama" and base and not _is_loopback(base):
            loc = "online"
        return cls(
            kind=kind,
            location=loc,  # type: ignore[arg-type]
            base_url=base,
            api_key=str(data.get("api_key") or ""),
        )


def _is_loopback(url: str) -> bool:
    u = url.lower()
    return any(
        h in u
        for h in (
            "127.0.0.1",
            "localhost",
            "[::1]",
            "0.0.0.0",
        )
    )


@dataclass
class RouteConfig:
    name: str
    primary: ModelRef
    fallback: list[ModelRef] = field(default_factory=list)
    privacy: str = "content"

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_dict(),
            "fallback": [f.to_dict() for f in self.fallback],
            "privacy": self.privacy,
        }


@dataclass
class ModelRoutesConfig:
    version: int
    providers: dict[str, ProviderConfig]
    routes: dict[str, RouteConfig]
    budgets: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "providers": {k: v.to_dict() for k, v in self.providers.items()},
            "routes": {k: v.to_dict() for k, v in self.routes.items()},
            "budgets": deepcopy(self.budgets),
        }

    def ensure_all_routes(self) -> list[str]:
        """Fill missing routes by falling back to chat; return warning messages."""
        warnings: list[str] = []
        chat = self.routes.get("chat")
        if chat is None:
            chat = RouteConfig(
                name="chat",
                primary=ModelRef(provider="ollama_local", model=""),
                privacy=DEFAULT_PRIVACY["chat"],
            )
            self.routes["chat"] = chat
            warnings.append("created_default_chat_route")
        for name in ROUTE_NAMES:
            if name not in self.routes:
                self.routes[name] = RouteConfig(
                    name=name,
                    primary=ModelRef(
                        provider=chat.primary.provider,
                        model=chat.primary.model,
                        options=dict(chat.primary.options),
                    ),
                    fallback=[
                        ModelRef(provider=f.provider, model=f.model, options=dict(f.options))
                        for f in chat.fallback
                    ],
                    privacy=DEFAULT_PRIVACY.get(name, "content"),
                )
                warnings.append(f"route_fallback_to_chat:{name}")
        self.warnings.extend(warnings)
        return warnings


def default_v2_config() -> ModelRoutesConfig:
    providers = {
        "ollama_local": ProviderConfig(
            kind="ollama",
            location="local",
            base_url="http://127.0.0.1:11434",
        )
    }
    routes: dict[str, RouteConfig] = {}
    for name in ROUTE_NAMES:
        model = "bge-m3" if name == "embedding" else ""
        routes[name] = RouteConfig(
            name=name,
            primary=ModelRef(provider="ollama_local", model=model),
            fallback=[],
            privacy=DEFAULT_PRIVACY[name],
        )
    budgets = {
        "agent": {"max_tokens_per_task": 400000},
        "nightly": {"max_tokens_per_day": 300000},
        "evolution": {"max_tokens_per_day": 200000},
    }
    return ModelRoutesConfig(version=2, providers=providers, routes=routes, budgets=budgets)


def _parse_route_block(name: str, data: dict[str, Any]) -> RouteConfig:
    primary = ModelRef.from_dict(data.get("primary")) or ModelRef(provider="", model="")
    fallback_raw = data.get("fallback") or []
    fallback: list[ModelRef] = []
    if isinstance(fallback_raw, list):
        for item in fallback_raw:
            ref = ModelRef.from_dict(item)
            if ref:
                fallback.append(ref)
    privacy = str(data.get("privacy") or DEFAULT_PRIVACY.get(name, "content"))
    return RouteConfig(name=name, primary=primary, fallback=fallback, privacy=privacy)


def load_routes_dict(data: dict[str, Any]) -> ModelRoutesConfig:
    """Load v2 config dict; migrate if version < 2 or v0.11 shape."""
    version = int(data.get("version") or 1)
    if version < 2 or _looks_like_v11(data):
        return migrate_v11_to_v2(data)

    providers = {
        k: ProviderConfig.from_dict(v)
        for k, v in (data.get("providers") or {}).items()
        if isinstance(v, dict)
    }
    routes: dict[str, RouteConfig] = {}
    raw_routes = data.get("routes") or {}
    for raw_name, block in raw_routes.items():
        if not isinstance(block, dict):
            continue
        name = ROUTE_ALIASES.get(str(raw_name), str(raw_name))
        routes[name] = _parse_route_block(name, block)

    cfg = ModelRoutesConfig(
        version=2,
        providers=providers or default_v2_config().providers,
        routes=routes,
        budgets=dict(data.get("budgets") or default_v2_config().budgets),
    )
    cfg.ensure_all_routes()
    return cfg


def _looks_like_v11(data: dict[str, Any]) -> bool:
    routes = data.get("routes") or data.get("task_routes") or {}
    if not isinstance(routes, dict):
        return False
    keys = set(routes)
    v11 = {"chat", "agent", "knowledge", "experience", "nightly", "persona"}
    # Exactly the old 6, or has knowledge without knowledge_compile
    if keys == v11:
        return True
    if "knowledge" in keys and "knowledge_compile" not in keys and "wiki_compile" not in keys:
        return True
    return False


def migrate_v11_to_v2(data: dict[str, Any]) -> ModelRoutesConfig:
    """Map v0.11 6 task routes into 13 v0.12 feature routes (AT-RT-01)."""
    base = default_v2_config()
    raw_routes = data.get("routes") or data.get("task_routes") or {}
    raw_providers = data.get("providers") or {}

    if isinstance(raw_providers, dict) and raw_providers:
        base.providers = {
            k: ProviderConfig.from_dict(v)
            for k, v in raw_providers.items()
            if isinstance(v, dict)
        }

    # Normalize a v11 route entry into ModelRef primary + fallback
    def _extract(block: Any) -> tuple[ModelRef, list[ModelRef]]:
        if isinstance(block, str):
            return ModelRef(provider="ollama_local", model=block), []
        if not isinstance(block, dict):
            return ModelRef(provider="ollama_local", model=""), []
        primary = ModelRef.from_dict(block.get("primary"))
        if primary is None:
            # v11 sometimes: {"provider", "model"}
            if "provider" in block or "model" in block:
                primary = ModelRef(
                    provider=str(block.get("provider") or "ollama_local"),
                    model=str(block.get("model") or ""),
                )
            else:
                primary = ModelRef(provider="ollama_local", model="")
        fb: list[ModelRef] = []
        for item in block.get("fallback") or []:
            ref = ModelRef.from_dict(item) if isinstance(item, dict) else None
            if ref:
                fb.append(ref)
        return primary, fb

    for v11_name, targets in V11_ROUTE_MAP.items():
        if v11_name not in raw_routes:
            continue
        primary, fallback = _extract(raw_routes[v11_name])
        for target in targets:
            base.routes[target] = RouteConfig(
                name=target,
                primary=ModelRef(
                    provider=primary.provider,
                    model=primary.model,
                    options=dict(primary.options),
                ),
                fallback=[
                    ModelRef(provider=f.provider, model=f.model, options=dict(f.options))
                    for f in fallback
                ],
                privacy=DEFAULT_PRIVACY[target],
            )

    # New routes stay at defaults unless chat was set — copy chat for skill/vision/reflection soft default
    chat = base.routes.get("chat")
    if chat and chat.primary.model:
        for name in ("vision", "skill_search_rerank", "reflection"):
            if name in base.routes and not base.routes[name].primary.model:
                base.routes[name] = RouteConfig(
                    name=name,
                    primary=ModelRef(
                        provider=chat.primary.provider,
                        model=chat.primary.model,
                        options=dict(chat.primary.options),
                    ),
                    fallback=list(chat.fallback),
                    privacy=DEFAULT_PRIVACY[name],
                )

    if isinstance(data.get("budgets"), dict):
        base.budgets.update(data["budgets"])

    base.version = 2
    base.warnings.append("migrated_from_v11")
    base.ensure_all_routes()
    return base


def load_workspace_routes(workspace: Path) -> ModelRoutesConfig:
    path = Path(workspace) / ".kw" / "model-routes.json"
    if not path.is_file():
        cfg = default_v2_config()
        save_workspace_routes(workspace, cfg)
        return cfg
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = load_routes_dict(data)
    if cfg.warnings and "migrated_from_v11" in cfg.warnings:
        save_workspace_routes(workspace, cfg)
    return cfg


def save_workspace_routes(workspace: Path, cfg: ModelRoutesConfig) -> Path:
    path = Path(workspace) / ".kw" / "model-routes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never write raw secrets — only secret: refs already in provider.api_key
    text = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def route_names() -> list[str]:
    return list(ROUTE_NAMES)


# --- Capability probe (LW-10 / §14.3) ---

PROBE_NAMES: tuple[str, ...] = (
    "availability",
    "tool_calling",
    "structured_output",
    "vision",
    "embedding_dim",
    "actual_context",
)


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ModelProbeReport:
    provider: str
    model: str
    kind: str
    results: dict[str, ProbeResult]
    warnings: list[str] = field(default_factory=list)

    def all_required_ok(self) -> bool:
        return all(self.results[n].ok for n in PROBE_NAMES if n in self.results)


HttpTransport = Any  # callable(method, url, *, json=None, headers=None) -> dict|list|bytes


def _default_http(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Any:
    import urllib.error
    import urllib.request

    data = None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode("utf-8"))
            return raw
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:200]}") from exc


def probe_ollama_model(
    *,
    base_url: str,
    model: str,
    http: HttpTransport | None = None,
    num_ctx: int | None = None,
) -> ModelProbeReport:
    """Run 6 capability probes against an Ollama endpoint (recorded http in tests)."""
    transport = http or _default_http
    base = base_url.rstrip("/")
    results: dict[str, ProbeResult] = {}

    # 1. availability — minimal generate
    try:
        resp = transport(
            "POST",
            f"{base}/api/generate",
            json_body={"model": model, "prompt": "ping", "stream": False, "options": {"num_predict": 1}},
        )
        ok = isinstance(resp, dict) and ("response" in resp or "model" in resp)
        results["availability"] = ProbeResult("availability", ok, {"raw_keys": list(resp) if isinstance(resp, dict) else []})
    except Exception as exc:
        results["availability"] = ProbeResult("availability", False, error=str(exc))

    # 2. tool_calling — chat with tools
    try:
        resp = transport(
            "POST",
            f"{base}/api/chat",
            json_body={
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": "Call the ping tool."}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "test tool",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            },
        )
        msg = (resp or {}).get("message") if isinstance(resp, dict) else None
        tool_calls = (msg or {}).get("tool_calls") if isinstance(msg, dict) else None
        ok = bool(tool_calls)
        results["tool_calling"] = ProbeResult(
            "tool_calling", ok, {"tool_calls": tool_calls or []}
        )
    except Exception as exc:
        results["tool_calling"] = ProbeResult("tool_calling", False, error=str(exc))

    # 3. structured_output — format json schema
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    try:
        resp = transport(
            "POST",
            f"{base}/api/generate",
            json_body={
                "model": model,
                "prompt": 'Return JSON {"ok": true}',
                "stream": False,
                "format": schema,
            },
        )
        text = (resp or {}).get("response", "") if isinstance(resp, dict) else ""
        parsed = json.loads(text) if isinstance(text, str) and text.strip().startswith("{") else None
        ok = isinstance(parsed, dict) and "ok" in parsed
        results["structured_output"] = ProbeResult(
            "structured_output", ok, {"parsed": parsed}
        )
    except Exception as exc:
        results["structured_output"] = ProbeResult("structured_output", False, error=str(exc))

    # 4. vision — minimal image payload (1x1 png base64 stub)
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    try:
        resp = transport(
            "POST",
            f"{base}/api/chat",
            json_body={
                "model": model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": "describe",
                        "images": [tiny_png_b64],
                    }
                ],
            },
        )
        msg = (resp or {}).get("message") if isinstance(resp, dict) else None
        content = (msg or {}).get("content") if isinstance(msg, dict) else None
        ok = bool(content) or (isinstance(resp, dict) and "error" not in resp)
        results["vision"] = ProbeResult("vision", bool(ok), {"has_content": bool(content)})
    except Exception as exc:
        results["vision"] = ProbeResult("vision", False, error=str(exc))

    # 5. embedding_dim
    try:
        resp = transport(
            "POST",
            f"{base}/api/embed",
            json_body={"model": model, "input": "hello"},
        )
        embeddings = []
        if isinstance(resp, dict):
            embeddings = resp.get("embeddings") or resp.get("embedding") or []
        if embeddings and isinstance(embeddings[0], (int, float)):
            dim = len(embeddings)
        elif embeddings and isinstance(embeddings[0], list):
            dim = len(embeddings[0])
        else:
            dim = 0
        results["embedding_dim"] = ProbeResult(
            "embedding_dim", dim > 0, {"dimension": dim}
        )
    except Exception as exc:
        results["embedding_dim"] = ProbeResult("embedding_dim", False, error=str(exc))

    # 6. actual_context — /api/show + optional num_ctx override (never trust marketing alone)
    try:
        resp = transport("POST", f"{base}/api/show", json_body={"model": model})
        model_info = resp if isinstance(resp, dict) else {}
        # Prefer model_info parameters / context_length if present
        ctx = None
        details = model_info.get("details") or {}
        params = model_info.get("model_info") or model_info.get("parameters") or {}
        if isinstance(params, dict):
            for key in ("llama.context_length", "context_length", "num_ctx"):
                if key in params:
                    ctx = int(params[key])
                    break
        if ctx is None and isinstance(details, dict) and "context_length" in details:
            ctx = int(details["context_length"])
        configured = num_ctx
        effective = configured if configured else ctx
        ok = effective is not None and int(effective) > 0
        results["actual_context"] = ProbeResult(
            "actual_context",
            ok,
            {
                "reported_context": ctx,
                "configured_num_ctx": configured,
                "effective_context": effective,
            },
        )
    except Exception as exc:
        results["actual_context"] = ProbeResult("actual_context", False, error=str(exc))

    return ModelProbeReport(
        provider="ollama",
        model=model,
        kind="ollama",
        results=results,
    )


def probe_openai_compatible(
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    http: HttpTransport | None = None,
    num_ctx: int | None = None,
) -> ModelProbeReport:
    """Six probes for OpenAI-compatible HTTP APIs."""
    transport = http or _default_http
    base = base_url.rstrip("/")
    headers = {}
    if api_key and not api_key.startswith("secret:"):
        headers["Authorization"] = f"Bearer {api_key}"
    elif api_key.startswith("secret:"):
        # LW-10: do not resolve secrets here; tests inject http. Live needs secrets module.
        headers["Authorization"] = "Bearer REDACTED_UNRESOLVED"
    results: dict[str, ProbeResult] = {}

    def _chat(body: dict[str, Any]) -> Any:
        return transport(
            "POST",
            f"{base}/chat/completions",
            json_body=body,
            headers=headers,
        )

    # 1. availability
    try:
        resp = _chat(
            {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
        )
        ok = isinstance(resp, dict) and bool(resp.get("choices"))
        results["availability"] = ProbeResult("availability", ok, {"id": (resp or {}).get("id")})
    except Exception as exc:
        results["availability"] = ProbeResult("availability", False, error=str(exc))

    # 2. tool_calling
    try:
        resp = _chat(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Call ping."}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "test",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            }
        )
        choice = ((resp or {}).get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        results["tool_calling"] = ProbeResult("tool_calling", bool(tool_calls), {"count": len(tool_calls)})
    except Exception as exc:
        results["tool_calling"] = ProbeResult("tool_calling", False, error=str(exc))

    # 3. structured_output
    try:
        resp = _chat(
            {
                "model": model,
                "messages": [{"role": "user", "content": 'Return {"ok": true}'}],
                "response_format": {"type": "json_object"},
            }
        )
        choice = ((resp or {}).get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content")) or ""
        parsed = json.loads(content) if content.strip().startswith("{") else None
        ok = isinstance(parsed, dict) and "ok" in parsed
        results["structured_output"] = ProbeResult("structured_output", ok, {"parsed": parsed})
    except Exception as exc:
        results["structured_output"] = ProbeResult("structured_output", False, error=str(exc))

    # 4. vision
    try:
        resp = _chat(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 16,
            }
        )
        choice = ((resp or {}).get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content")) or ""
        results["vision"] = ProbeResult("vision", bool(content), {"has_content": bool(content)})
    except Exception as exc:
        results["vision"] = ProbeResult("vision", False, error=str(exc))

    # 5. embedding_dim
    try:
        resp = transport(
            "POST",
            f"{base}/embeddings",
            json_body={"model": model, "input": "hello"},
            headers=headers,
        )
        data = (resp or {}).get("data") if isinstance(resp, dict) else None
        dim = 0
        if isinstance(data, list) and data:
            emb = data[0].get("embedding") or []
            dim = len(emb)
        results["embedding_dim"] = ProbeResult("embedding_dim", dim > 0, {"dimension": dim})
    except Exception as exc:
        results["embedding_dim"] = ProbeResult("embedding_dim", False, error=str(exc))

    # 6. actual_context — use configured num_ctx; optional /models metadata
    try:
        meta = transport("GET", f"{base}/models", headers=headers)
        reported = None
        if isinstance(meta, dict):
            for m in meta.get("data") or []:
                if m.get("id") == model and "context_length" in m:
                    reported = int(m["context_length"])
        effective = num_ctx if num_ctx else reported
        ok = effective is not None and int(effective) > 0
        results["actual_context"] = ProbeResult(
            "actual_context",
            ok,
            {
                "reported_context": reported,
                "configured_num_ctx": num_ctx,
                "effective_context": effective,
                "note": "do_not_trust_marketing_alone",
            },
        )
    except Exception as exc:
        # Still OK if user configured num_ctx
        if num_ctx:
            results["actual_context"] = ProbeResult(
                "actual_context",
                True,
                {"configured_num_ctx": num_ctx, "effective_context": num_ctx},
            )
        else:
            results["actual_context"] = ProbeResult("actual_context", False, error=str(exc))

    return ModelProbeReport(
        provider="openai_compatible",
        model=model,
        kind="openai_compatible",
        results=results,
    )


def probe_anthropic_native(
    *,
    model: str,
    api_key: str,
    http: HttpTransport | None = None,
    base_url: str = "https://api.anthropic.com",
) -> ModelProbeReport:
    """Minimal native Anthropic Messages API probe (R3.6)."""
    transport = http or _default_http
    base = (base_url or "https://api.anthropic.com").rstrip("/")
    results: dict[str, ProbeResult] = {}
    headers = {
        "x-api-key": api_key or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # availability
    try:
        if not api_key:
            raise RuntimeError("missing_api_key")
        resp = transport(
            "POST",
            f"{base}/v1/messages",
            json_body={
                "model": model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=headers,
        )
        content = ""
        if isinstance(resp, dict):
            blocks = resp.get("content") or []
            if blocks and isinstance(blocks[0], dict):
                content = str(blocks[0].get("text") or "")
        results["availability"] = ProbeResult(
            "availability", True, {"has_content": bool(content)}
        )
    except Exception as exc:  # noqa: BLE001
        results["availability"] = ProbeResult("availability", False, error=str(exc))

    for name in PROBE_NAMES:
        if name == "availability":
            continue
        # Native probe focuses on availability; other caps marked not_applicable
        results[name] = ProbeResult(
            name,
            False,
            error="not_probed_native_use_compat_for_full_suite",
        )
    return ModelProbeReport(
        provider="anthropic",
        model=model,
        kind="anthropic",
        results=results,
        warnings=["anthropic_native_availability_only"],
    )


def probe_model(
    provider: ProviderConfig,
    model: str,
    *,
    http: HttpTransport | None = None,
    num_ctx: int | None = None,
    api_key: str = "",
) -> ModelProbeReport:
    """Dispatch probe by provider kind."""
    key = api_key or provider.api_key
    if key.startswith("secret:"):
        # Probe layer does not resolve secrets; callers should pass resolved key.
        # Keep secret: as missing for native probe honesty.
        if provider.kind == "anthropic" and not provider.base_url:
            return ModelProbeReport(
                provider=provider.kind,
                model=model,
                kind=provider.kind,
                results={
                    n: ProbeResult(n, False, error="secret_unresolved_pass_api_key")
                    for n in PROBE_NAMES
                },
                warnings=["resolve_secret_before_probe"],
            )
    if provider.kind == "ollama":
        return probe_ollama_model(
            base_url=provider.base_url or "http://127.0.0.1:11434",
            model=model,
            http=http,
            num_ctx=num_ctx,
        )
    if provider.kind == "anthropic":
        bu = (provider.base_url or "").rstrip("/")
        # Custom base_url that is not Anthropic host → OpenAI-compatible Claude gateway
        if bu and "api.anthropic.com" not in bu:
            return probe_openai_compatible(
                base_url=bu,
                model=model,
                api_key=key,
                http=http,
                num_ctx=num_ctx,
            )
        return probe_anthropic_native(
            model=model,
            api_key=key if not str(key).startswith("secret:") else "",
            http=http,
            base_url=bu or "https://api.anthropic.com",
        )
    if provider.kind in ("openai_compatible", "openai"):
        return probe_openai_compatible(
            base_url=provider.base_url,
            model=model,
            api_key=key,
            http=http,
            num_ctx=num_ctx,
        )
    return ModelProbeReport(
        provider=provider.kind,
        model=model,
        kind=provider.kind,
        results={n: ProbeResult(n, False, error="unknown_provider_kind") for n in PROBE_NAMES},
    )


def route_capability_warnings(
    cfg: ModelRoutesConfig,
    capabilities: dict[str, set[str]],
) -> list[str]:
    """Warn when a route's primary model lacks required capabilities.

    ``capabilities`` maps ``provider/model`` → set of capability names.
    """
    warnings: list[str] = []
    for name, route in cfg.routes.items():
        required = REQUIRED_CAPABILITIES.get(name) or []
        if not required:
            continue
        key = f"{route.primary.provider}/{route.primary.model}"
        have = capabilities.get(key) or set()
        missing = [c for c in required if c not in have]
        if missing:
            warnings.append(f"route:{name}:missing:{','.join(missing)}")
            if name == "agent" and "tool_calling" in missing:
                warnings.append("agent_json_fallback_protocol")
    return warnings


# --- Privacy gate + fallback (LW-11 / §14.4 / §17.3) ---


class RetryableModelError(Exception):
    """Transient model failure — try next candidate (fallback)."""


@dataclass
class SkipRecord:
    provider: str
    model: str
    location: Location
    reason: str


@dataclass
class UsageRecord:
    kind: UsageKind
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @classmethod
    def unknown(cls) -> UsageRecord:
        return cls(kind="unknown", prompt_tokens=None, completion_tokens=None)

    @classmethod
    def from_response(cls, response: Any) -> UsageRecord:
        """Extract usage; unknown must stay None — never coerce to 0 (AT-RT-06)."""
        if not isinstance(response, dict):
            return cls.unknown()
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return cls.unknown()
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if pt is None and ct is None and usage.get("total_tokens") is None:
            return cls.unknown()
        # Explicit None stays None; only set ints when present
        prompt = int(pt) if isinstance(pt, (int, float)) else None
        completion = int(ct) if isinstance(ct, (int, float)) else None
        if prompt is None and completion is None:
            return cls.unknown()
        return cls(kind="reported", prompt_tokens=prompt, completion_tokens=completion)

    def as_log_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class ModelCallResult:
    ok: bool
    route: str
    model_ref: ModelRef | None
    used_fallback: bool
    usage: UsageRecord
    skipped: list[SkipRecord] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    response: Any = None
    request_log: dict[str, Any] = field(default_factory=dict)


def higher_content_class(*classes: ContentClass | str) -> ContentClass:
    """Derived content inherits the highest classification (§17.3)."""
    if not classes:
        return "public"
    best = "public"
    best_rank = -1
    for c in classes:
        rank = CONTENT_CLASS_RANK.get(str(c), 0)
        if rank > best_rank:
            best_rank = rank
            best = str(c)  # type: ignore[assignment]
    if best not in CONTENT_CLASS_RANK:
        return "normal"
    return best  # type: ignore[return-value]


def privacy_allows(
    content_class: ContentClass | str,
    location: Location,
    *,
    mode: PrivacyMode,
    route: str,
    route_privacy: str,
    online_authorized: bool = False,
    sensitive_authorized: bool = False,
) -> tuple[bool, str]:
    """Return (allowed, reason). Local always allowed; online checked per §17.3 matrix.

    Fallback candidates must call this again — never inherit a prior allow.
    """
    cls = str(content_class)
    if cls not in CONTENT_CLASS_RANK:
        cls = "normal"

    if location == "local":
        return True, "local_ok"

    # Online path
    if mode == "offline":
        return False, "offline_mode"

    if route_privacy == "public_only" and cls != "public":
        return False, f"privacy:public_only:blocks:{cls}"

    if cls == "public":
        return True, "public_ok"

    if cls == "normal":
        # Allowed in local_first / online modes (§17.3)
        return True, "normal_ok"

    if cls == "private":
        if online_authorized:
            return True, "private_authorized"
        return False, "privacy:private_requires_authorization"

    if cls == "sensitive":
        # Default forbid online; item-level grant only
        if sensitive_authorized:
            return True, "sensitive_authorized"
        return False, "privacy:sensitive_blocked"

    return False, f"privacy:unknown_class:{cls}"


def _candidates_for_route(cfg: ModelRoutesConfig, route_name: str) -> list[tuple[ModelRef, bool]]:
    """List (ref, is_fallback) for primary + fallbacks."""
    route = cfg.routes.get(route_name)
    if route is None:
        chat = cfg.routes.get("chat")
        if chat is None:
            return []
        route = chat
        route_name = "chat"
    out: list[tuple[ModelRef, bool]] = [(route.primary, False)]
    for fb in route.fallback:
        out.append((fb, True))
    return out


ModelCallFn = Callable[[ModelRef, ProviderConfig], Any]


def resolve_model_call(
    cfg: ModelRoutesConfig,
    route_name: str,
    *,
    content_class: ContentClass | str = "normal",
    mode: PrivacyMode = "local_first",
    online_authorized: bool = False,
    sensitive_authorized: bool = False,
    call_fn: ModelCallFn,
) -> ModelCallResult:
    """Select primary then fallbacks with per-candidate privacy checks (§14.4).

    - Offline skips online models.
    - Fallback cannot bypass privacy (re-check each candidate).
    - Records route, model used, used_fallback, usage kind.
    """
    route = cfg.routes.get(route_name) or cfg.routes.get("chat")
    route_privacy = route.privacy if route else "content"
    skipped: list[SkipRecord] = []
    privacy_skips = 0
    attempted = 0
    last_error: str | None = None

    candidates = _candidates_for_route(cfg, route_name)
    if not candidates:
        return ModelCallResult(
            ok=False,
            route=route_name,
            model_ref=None,
            used_fallback=False,
            usage=UsageRecord.unknown(),
            error_code="E_MODEL_UNAVAILABLE",
            error_message="no models configured for route",
        )

    for ref, is_fallback in candidates:
        provider = cfg.providers.get(ref.provider)
        if provider is None:
            skipped.append(
                SkipRecord(ref.provider, ref.model, "online", "missing_provider")
            )
            continue

        allowed, reason = privacy_allows(
            content_class,
            provider.location,
            mode=mode,
            route=route_name,
            route_privacy=route_privacy,
            online_authorized=online_authorized,
            sensitive_authorized=sensitive_authorized,
        )
        if not allowed:
            skipped.append(
                SkipRecord(ref.provider, ref.model, provider.location, reason)
            )
            if reason.startswith("privacy:") or reason == "offline_mode":
                privacy_skips += 1
            continue

        attempted += 1
        try:
            response = call_fn(ref, provider)
        except RetryableModelError as exc:
            last_error = str(exc)
            skipped.append(
                SkipRecord(
                    ref.provider,
                    ref.model,
                    provider.location,
                    f"retryable:{exc}",
                )
            )
            continue
        except Exception as exc:
            last_error = str(exc)
            skipped.append(
                SkipRecord(
                    ref.provider,
                    ref.model,
                    provider.location,
                    f"error:{exc}",
                )
            )
            continue

        usage = UsageRecord.from_response(response)
        log = {
            "route": route_name,
            "provider": ref.provider,
            "model": ref.model,
            "used_fallback": is_fallback,
            "usage": usage.as_log_dict(),
            "content_class": content_class,
            "mode": mode,
        }
        return ModelCallResult(
            ok=True,
            route=route_name,
            model_ref=ref,
            used_fallback=is_fallback,
            usage=usage,
            skipped=skipped,
            response=response,
            request_log=log,
        )

    # Decide error code: privacy-only blocks → E_PRIVACY_BLOCKED / E_OFFLINE
    offline_only = all(s.reason == "offline_mode" for s in skipped) and skipped
    if offline_only and attempted == 0:
        code = "E_OFFLINE"
        msg = "离线模式下所有在线模型被跳过。"
    elif privacy_skips > 0 and attempted == 0:
        code = "E_PRIVACY_BLOCKED"
        msg = (
            f"该内容为 {content_class}，隐私策略阻止发送到可用模型。"
            f" 跳过：{[s.reason for s in skipped]}"
        )
    elif privacy_skips > 0 and attempted > 0:
        # Local tried and failed; online blocked by privacy — still privacy block (AT-RT-03)
        code = "E_PRIVACY_BLOCKED"
        msg = (
            f"本地模型不可用，且内容为 {content_class} 时后备在线模型未获授权。"
            f" 跳过：{[s.reason for s in skipped]}"
        )
    else:
        code = "E_MODEL_UNAVAILABLE"
        msg = f"所有候选模型均不可用。last_error={last_error}; skipped={[s.reason for s in skipped]}"

    return ModelCallResult(
        ok=False,
        route=route_name,
        model_ref=None,
        used_fallback=False,
        usage=UsageRecord.unknown(),
        skipped=skipped,
        error_code=code,
        error_message=msg,
        request_log={
            "route": route_name,
            "content_class": content_class,
            "mode": mode,
            "skipped": [s.__dict__ for s in skipped],
        },
    )

