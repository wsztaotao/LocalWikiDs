"""Personal Core helpers: 6-route view + context pack for thin Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# UI / settings presets (R3.6) — providers written as secret: refs only
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "ollama_local": {
        "kind": "ollama",
        "location": "local",
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
    },
    "openai": {
        "kind": "openai",
        "location": "online",
        "base_url": "https://api.openai.com/v1",
        "api_key": "secret:openai",
    },
    "anthropic": {
        "kind": "anthropic",
        "location": "online",
        "base_url": "",
        "api_key": "secret:anthropic",
    },
    "deepseek": {
        "kind": "openai_compatible",
        "location": "online",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "secret:deepseek",
    },
    "qwen": {
        "kind": "openai_compatible",
        "location": "online",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "secret:qwen",
    },
}

# Canonical Personal Core routes (S0 spec §2)
CORE_ROUTES: tuple[str, ...] = (
    "chat",
    "agent",
    "knowledge_compile",
    "memory",
    "experience_persona",
    "embedding",
)

# Map core route → underlying model_routing route name(s)
CORE_TO_MODEL_ROUTE: dict[str, str] = {
    "chat": "chat",
    "agent": "agent",
    "knowledge_compile": "knowledge_compile",
    "memory": "memory_consolidate",
    "experience_persona": "experience",
    "embedding": "embedding",
}

# Map legacy / fine-grained names → core route
ROUTE_TO_CORE: dict[str, str] = {
    "chat": "chat",
    "agent": "agent",
    "knowledge_compile": "knowledge_compile",
    "wiki_lint": "knowledge_compile",
    "wiki_compile": "knowledge_compile",
    "wiki_query": "knowledge_compile",
    "memory": "memory",
    "memory_extract": "memory",
    "memory_consolidate": "memory",
    "experience": "experience_persona",
    "persona": "experience_persona",
    "experience_persona": "experience_persona",
    "embedding": "embedding",
    "vision": "chat",
    "skill_search_rerank": "agent",
    "nightly": "memory",
    "reflection": "experience_persona",
}


def core_route_for(name: str) -> str:
    return ROUTE_TO_CORE.get(name, "chat")


def list_core_routes() -> list[str]:
    return list(CORE_ROUTES)


def build_agent_context(
    workspace: Path | str,
    *,
    surface: str = "agent",
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Pack confirmed-ish personal context for Agent (files + candidates sidecar).

    Best-effort: missing modules/files yield empty sections (offline safe).
    """
    root = Path(workspace).resolve()
    sections: list[str] = []
    meta: dict[str, Any] = {"workspace": str(root), "surface": surface}

    # AGENTS.md / schema tools
    for rel in ("schema/AGENTS.md", "AGENTS.md", "schema/tools.md"):
        p = root / rel
        if p.is_file():
            text = p.read_text(encoding="utf-8")[:2000]
            sections.append(f"## Rules ({rel})\n{text}")
            break

    # Style directives if present
    style_path = root / ".kw" / "persona" / "style_directives.json"
    if style_path.is_file():
        try:
            data = json.loads(style_path.read_text(encoding="utf-8"))
            sections.append(
                "## StyleDirectives\n" + json.dumps(data, ensure_ascii=False)[:1500]
            )
            meta["style"] = True
        except json.JSONDecodeError:
            pass
    else:
        # language + work style hint file
        lang = root / "wiki" / "profiles" / "style" / "language.md"
        if lang.is_file():
            sections.append("## Language style\n" + lang.read_text(encoding="utf-8")[:1200])
            meta["language_style"] = True

    # Experience snippets
    exp_dir = root / "wiki" / "experiences"
    if exp_dir.is_dir():
        bits: list[str] = []
        for p in sorted(exp_dir.glob("*.md"))[:5]:
            bits.append(p.read_text(encoding="utf-8")[:400])
        if bits:
            sections.append("## Experience\n" + "\n---\n".join(bits))
            meta["experience_files"] = len(bits)

    # Memory index pages
    mem_dir = root / "wiki" / "memories"
    if mem_dir.is_dir():
        bits = []
        for p in sorted(mem_dir.glob("*.md"))[:5]:
            bits.append(p.read_text(encoding="utf-8")[:400])
        if bits:
            sections.append("## Memory\n" + "\n---\n".join(bits))
            meta["memory_files"] = len(bits)

    # Try StyleDirectives compiler if flags exist
    try:
        from localwiki.persona.directives import StyleFeatureFlags

        flags = StyleFeatureFlags()
        # OCEAN off by design for personal core default
        meta["ocean_default"] = False
        meta["style_surface_enabled"] = flags.is_enabled(surface)
    except Exception:  # noqa: BLE001
        pass

    blob = "\n\n".join(sections)
    if len(blob) > max_chars:
        blob = blob[: max_chars - 20] + "\n…[truncated]"
    return {"ok": True, "text": blob, "meta": meta, "core_routes": list_core_routes()}


def _provider_key_status(
    workspace: Path,
    *,
    kind: str,
    api_key_ref: str,
) -> dict[str, Any]:
    """Whether a usable API key exists (ref alone is not enough)."""
    if kind == "ollama":
        return {
            "key_required": False,
            "key_configured": False,
            "key_ref": "",
            "key_label": "本地无需 Key",
        }
    ref = (api_key_ref or "").strip()
    if not ref.startswith("secret:"):
        return {
            "key_required": True,
            "key_configured": False,
            "key_ref": "",
            "key_label": "未配置 Key",
        }
    name = ref[len("secret:") :]
    present = False
    try:
        from localwiki.secrets import open_secret_store

        val = open_secret_store(workspace).get(name)
        present = bool(val)
    except Exception:  # noqa: BLE001
        present = False
    return {
        "key_required": True,
        "key_configured": present,
        "key_ref": ref if present else ref,  # still show intended ref
        "key_label": "已配置 Key" if present else "未配置 Key",
    }


def get_core_model_settings(workspace: Path | str) -> dict[str, Any]:
    """6-route view over ``.kw/model-routes.json`` (no raw secrets)."""
    from localwiki.model_routing import load_workspace_routes

    root = Path(workspace)
    cfg = load_workspace_routes(root)
    routes_out: dict[str, Any] = {}
    for core in CORE_ROUTES:
        mr = CORE_TO_MODEL_ROUTE[core]
        rc = cfg.routes.get(mr) or cfg.routes.get("chat")
        if rc is None:
            routes_out[core] = {
                "model_route": mr,
                "primary": {"provider": "", "model": ""},
                "fallback": [],
                "privacy": "content",
            }
            continue
        routes_out[core] = {
            "model_route": mr,
            "primary": rc.primary.to_dict(),
            "fallback": [f.to_dict() for f in rc.fallback],
            "privacy": rc.privacy,
        }
    providers: dict[str, Any] = {}
    for k, v in cfg.providers.items():
        ref = (
            v.api_key
            if str(v.api_key).startswith("secret:") or not v.api_key
            else "secret:REDACTED"
        )
        status = _provider_key_status(root, kind=v.kind or "", api_key_ref=str(ref or ""))
        providers[k] = {
            **v.to_dict(),
            # ensure api_key is only secret: ref or empty
            "api_key": ref if status["key_configured"] else (ref if ref.startswith("secret:") else ""),
            "key_configured": status["key_configured"],
            "key_required": status["key_required"],
            "key_label": status["key_label"],
        }
        # Do not claim a key exists when keyring has no value — clear display ref for UI
        if status["key_required"] and not status["key_configured"]:
            providers[k]["api_key"] = ""
            providers[k]["api_key_pending"] = ref if ref.startswith("secret:") else ""
    return {
        "ok": True,
        "core_routes": list(CORE_ROUTES),
        "routes": routes_out,
        "providers": providers,
        "version": cfg.version,
    }


def save_core_model_settings(
    workspace: Path | str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Update core routes / providers; reject raw API keys (secret: only)."""
    from localwiki.model_routing import (
        ModelRef,
        ProviderConfig,
        RouteConfig,
        load_workspace_routes,
        save_workspace_routes,
    )

    root = Path(workspace)
    cfg = load_workspace_routes(root)

    for name, pdata in (payload.get("providers") or {}).items():
        if not isinstance(pdata, dict):
            continue
        key = str(pdata.get("api_key") or "")
        # UI may send plaintext once → store via SecretStore, keep only secret: ref in config
        key_value = pdata.get("api_key_value") or pdata.get("api_key_plain")
        if key_value:
            from localwiki.secrets import open_secret_store

            secret_name = str(pdata.get("secret_name") or name).strip() or str(name)
            try:
                key = open_secret_store(root).set(secret_name, str(key_value))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"provider {name}: cannot store secret: {exc}") from exc
        elif key and not key.startswith("secret:") and key != "secret:REDACTED":
            raise ValueError(
                f"provider {name}: api_key must be secret:name, or send api_key_value to store"
            )
        if key == "secret:REDACTED":
            existing = cfg.providers.get(name)
            key = existing.api_key if existing else ""
        existing_kind = (
            cfg.providers.get(name).kind if name in cfg.providers else "ollama"
        )
        kind = str(pdata.get("kind") or existing_kind)
        location = str(pdata.get("location") or ("local" if kind == "ollama" else "online"))
        cfg.providers[str(name)] = ProviderConfig(
            kind=kind,
            location=location,  # type: ignore[arg-type]
            base_url=str(pdata.get("base_url") or ""),
            api_key=key,
        )

    for core, rdata in (payload.get("routes") or {}).items():
        if core not in CORE_ROUTES or not isinstance(rdata, dict):
            continue
        mr = CORE_TO_MODEL_ROUTE[core]
        primary = ModelRef.from_dict(rdata.get("primary")) or ModelRef(provider="", model="")
        fallback = []
        for item in rdata.get("fallback") or []:
            ref = ModelRef.from_dict(item)
            if ref:
                fallback.append(ref)
        privacy = str(rdata.get("privacy") or "content")
        cfg.routes[mr] = RouteConfig(
            name=mr, primary=primary, fallback=fallback, privacy=privacy
        )
        # Keep memory_extract aligned with memory core route
        if core == "memory":
            cfg.routes["memory_extract"] = RouteConfig(
                name="memory_extract",
                primary=ModelRef(
                    provider=primary.provider,
                    model=primary.model,
                    options=dict(primary.options),
                ),
                fallback=list(fallback),
                privacy=privacy,
            )
        if core == "experience_persona":
            cfg.routes["persona"] = RouteConfig(
                name="persona",
                primary=ModelRef(
                    provider=primary.provider,
                    model=primary.model,
                    options=dict(primary.options),
                ),
                fallback=list(fallback),
                privacy=privacy,
            )

    save_workspace_routes(root, cfg)
    return get_core_model_settings(root)


def sync_core_model_settings(
    workspace: Path | str,
    *,
    source_route: str = "agent",
) -> dict[str, Any]:
    """Copy primary/fallback/privacy from one core route onto all CORE_ROUTES (U3)."""
    from localwiki.model_routing import ModelRef, RouteConfig, load_workspace_routes, save_workspace_routes

    if source_route not in CORE_ROUTES:
        raise ValueError(f"unknown source_route: {source_route}")
    root = Path(workspace)
    cfg = load_workspace_routes(root)
    src_mr = CORE_TO_MODEL_ROUTE[source_route]
    src = cfg.routes.get(src_mr) or cfg.routes.get("chat")
    if src is None or not (src.primary.model or "").strip():
        raise ValueError(f"source route '{source_route}' is not configured")
    for core in CORE_ROUTES:
        mr = CORE_TO_MODEL_ROUTE[core]
        cfg.routes[mr] = RouteConfig(
            name=mr,
            primary=ModelRef(
                provider=src.primary.provider,
                model=src.primary.model,
                options=dict(src.primary.options),
            ),
            fallback=list(src.fallback),
            privacy=src.privacy,
        )
    save_workspace_routes(root, cfg)
    return get_core_model_settings(root)


def loop_enabled(workspace: Path | str) -> bool:
    """Whether local AgentLoop may be selected (default False — U5/Q2A)."""
    from localwiki.workspace import load_config, workspace_paths

    root = Path(workspace).resolve()
    cfg = load_config(workspace_paths(root)["config"])
    autonomy = cfg.get("autonomy") if isinstance(cfg.get("autonomy"), dict) else {}
    if "local_agent_loop_enabled" in autonomy:
        return bool(autonomy.get("local_agent_loop_enabled"))
    router = cfg.get("agent_router") if isinstance(cfg.get("agent_router"), dict) else {}
    return bool(router.get("allow_local_loop"))


def list_provider_presets() -> dict[str, Any]:
    return {"ok": True, "presets": dict(PROVIDER_PRESETS)}


# Suggested chat/embed models when remote listing is unavailable
MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini", "text-embedding-3-small"],
    "anthropic": [
        "claude-sonnet-4-5",
        "claude-opus-4",
        "claude-haiku-4-5",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "qwen": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen-long", "qwen-vl-plus"],
    "openai_compatible": ["gpt-4o-mini", "deepseek-chat", "qwen-plus"],
}


def list_provider_models(
    workspace: Path | str,
    provider_id: str,
    *,
    http: Any | None = None,
) -> dict[str, Any]:
    """List selectable models for a configured provider (Ollama tags or /v1/models)."""
    from localwiki.model_routing import _default_http, load_workspace_routes

    root = Path(workspace).resolve()
    pid = (provider_id or "").strip()
    if not pid:
        return {
            "ok": True,
            "provider": pid,
            "kind": "",
            "models": [],
            "source": "none",
            "error": "provider required",
        }

    cfg = load_workspace_routes(root)
    prov = cfg.providers.get(pid)
    if prov is None:
        # Fall back to preset suggestions only
        names = list(MODEL_SUGGESTIONS.get(pid) or [])
        return {
            "ok": True,
            "provider": pid,
            "kind": "",
            "models": [{"name": n} for n in names],
            "source": "suggestions" if names else "none",
            "error": "unknown_provider",
        }

    kind = prov.kind or ""
    if kind == "ollama":
        tags = list_ollama_tags(root, base_url=prov.base_url or None, http=http)
        return {
            "ok": True,
            "provider": pid,
            "kind": kind,
            "models": list(tags.get("models") or []),
            "source": "ollama",
            "error": tags.get("error"),
            "base_url": tags.get("base_url"),
        }

    suggested = list(
        MODEL_SUGGESTIONS.get(pid)
        or MODEL_SUGGESTIONS.get(kind)
        or []
    )
    remote: list[dict[str, Any]] = []
    error: str | None = None
    base = (prov.base_url or "").rstrip("/")
    if kind in ("openai", "openai_compatible") and base:
        transport = http or _default_http
        headers: dict[str, str] = {}
        api_key = prov.api_key or ""
        if str(api_key).startswith("secret:"):
            try:
                from localwiki.secrets import open_secret_store

                api_key = open_secret_store(root).resolve(api_key) or ""
            except Exception as exc:  # noqa: BLE001
                error = f"secret_resolve_failed:{exc}"
                api_key = ""
        if api_key and not str(api_key).startswith("secret:"):
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            # Short timeout — UI waits on this; fall back to suggestions on failure
            kwargs: dict[str, Any] = {"json_body": None, "headers": headers or None}
            try:
                data = transport("GET", f"{base}/models", timeout=5.0, **kwargs)
            except TypeError:
                data = transport("GET", f"{base}/models", **kwargs)
            items = []
            if isinstance(data, dict):
                items = data.get("data") or data.get("models") or []
            for m in items:
                if isinstance(m, dict):
                    name = str(m.get("id") or m.get("name") or m.get("model") or "")
                else:
                    name = str(m or "")
                if name:
                    remote.append({"name": name})
        except Exception as exc:  # noqa: BLE001
            error = f"unreachable:{exc}"

    # Prefer remote ids; append missing suggestions
    seen: set[str] = set()
    models: list[dict[str, Any]] = []
    for m in remote:
        n = m["name"]
        if n in seen:
            continue
        seen.add(n)
        models.append(m)
    for n in suggested:
        if n in seen:
            continue
        seen.add(n)
        models.append({"name": n})

    source = "remote" if remote else ("suggestions" if models else "none")
    return {
        "ok": True,
        "provider": pid,
        "kind": kind,
        "models": models,
        "source": source,
        "error": error,
        "base_url": base or None,
    }


def list_ollama_tags(
    workspace: Path | str,
    *,
    base_url: str | None = None,
    http: Any | None = None,
) -> dict[str, Any]:
    """List local Ollama models via ``GET {base}/api/tags`` (R3.6)."""
    from localwiki.model_routing import _default_http, load_workspace_routes

    root = Path(workspace)
    url = (base_url or "").rstrip("/")
    if not url:
        cfg = load_workspace_routes(root)
        for prov in cfg.providers.values():
            if prov.kind == "ollama":
                url = (prov.base_url or "").rstrip("/")
                break
        if not url:
            url = "http://127.0.0.1:11434"
    transport = http or _default_http
    try:
        data = transport("GET", f"{url}/api/tags", json_body=None, headers=None)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": True,
            "base_url": url,
            "models": [],
            "error": f"unreachable:{exc}",
        }
    models: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for m in data.get("models") or []:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or m.get("model") or "")
            if not name:
                continue
            models.append(
                {
                    "name": name,
                    "size": m.get("size") or 0,
                    "digest": m.get("digest"),
                    "modified_at": m.get("modified_at"),
                }
            )
    return {"ok": True, "base_url": url, "models": models, "error": None}


def patch_autonomy_settings(
    workspace: Path | str,
    *,
    nightly_local_hour: int | None = None,
    pause_evolution: bool | None = None,
    local_agent_loop_enabled: bool | None = None,
) -> dict[str, Any]:
    """Merge autonomy fields into ``.kw/config.json``."""
    from localwiki.workspace import load_config, save_config, workspace_paths

    root = Path(workspace).resolve()
    paths = workspace_paths(root)
    cfg = load_config(paths["config"])
    autonomy = cfg.get("autonomy") if isinstance(cfg.get("autonomy"), dict) else {}
    autonomy = dict(autonomy)
    if nightly_local_hour is not None:
        hour = int(nightly_local_hour)
        if hour < 0 or hour > 23:
            raise ValueError("nightly_local_hour must be 0..23")
        autonomy["nightly_local_hour"] = hour
        autonomy.setdefault("nightly_enabled", True)
    if pause_evolution is not None:
        autonomy["pause_evolution"] = bool(pause_evolution)
    if local_agent_loop_enabled is not None:
        autonomy["local_agent_loop_enabled"] = bool(local_agent_loop_enabled)
        router = cfg.get("agent_router") if isinstance(cfg.get("agent_router"), dict) else {}
        router = dict(router)
        router["allow_local_loop"] = bool(local_agent_loop_enabled)
        router.setdefault("default", "opencode")
        cfg["agent_router"] = router
    cfg["autonomy"] = autonomy
    save_config(paths["config"], cfg)
    return {
        "ok": True,
        "autonomy": autonomy,
        "local_agent_loop_enabled": loop_enabled(root),
    }


def probe_core_route(
    workspace: Path | str,
    route: str | None = None,
    *,
    http: Any | None = None,
) -> dict[str, Any]:
    """Probe one or all core routes via model_routing.probe_model."""
    from localwiki.model_routing import load_workspace_routes, probe_model

    root = Path(workspace)
    cfg = load_workspace_routes(root)
    targets = [route] if route else list(CORE_ROUTES)
    results: dict[str, Any] = {}
    for core in targets:
        if core not in CORE_ROUTES:
            results[str(core)] = {"ok": False, "error": "unknown_core_route"}
            continue
        mr = CORE_TO_MODEL_ROUTE[core]
        rc = cfg.routes.get(mr)
        if rc is None or not rc.primary.model:
            results[core] = {"ok": False, "error": "model_not_configured", "model_route": mr}
            continue
        prov = cfg.providers.get(rc.primary.provider)
        if prov is None:
            results[core] = {"ok": False, "error": "provider_missing", "provider": rc.primary.provider}
            continue
        api_key = prov.api_key or ""
        if str(api_key).startswith("secret:"):
            try:
                from localwiki.secrets import open_secret_store

                api_key = open_secret_store(root).resolve(api_key)
            except Exception as exc:  # noqa: BLE001
                results[core] = {
                    "ok": False,
                    "error": f"secret_resolve_failed:{exc}",
                    "provider": rc.primary.provider,
                }
                continue
        report = probe_model(prov, rc.primary.model, http=http, api_key=api_key)
        avail = report.results.get("availability")
        results[core] = {
            "ok": bool(avail and avail.ok),
            "model_route": mr,
            "provider": report.provider,
            "model": report.model,
            "kind": report.kind,
            "probes": {
                k: {"ok": v.ok, "error": v.error, "detail": v.detail}
                for k, v in report.results.items()
            },
            "warnings": list(report.warnings),
        }
    all_ok = all(bool(v.get("ok")) for v in results.values()) if results else False
    return {"ok": all_ok, "results": results}
