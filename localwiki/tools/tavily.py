"""Tavily search adapter (design §12.3, AT-TOOL-04/05).

Default path: ``tavily-python`` SDK. Missing package/key → fallback handlers.
Results are always untrusted. Budget + privacy (§17.3) enforced before egress.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

DEFAULT_BUDGET = 20
DEFAULT_MAX_RESULTS = 8
DEFAULT_CRAWL_LIMIT = 10

# §17.3 — Tavily query column
_TAVILY_MATRIX: dict[str, str] = {
    "public": "allow",
    "normal": "allow",
    "private": "auth",  # needs explicit allow_private_in_query
    "sensitive": "deny",
}


class TavilyError(RuntimeError):
    pass


class TavilySdkClient(Protocol):
    def search(self, query: str, **kwargs: Any) -> Any: ...
    def extract(self, urls: list[str] | str, **kwargs: Any) -> Any: ...
    def crawl(self, url: str, **kwargs: Any) -> Any: ...
    def map(self, url: str, **kwargs: Any) -> Any: ...


SdkFactory = Callable[[str], TavilySdkClient]
FallbackHandler = Callable[[dict[str, Any]], dict[str, Any]]
SecretGetter = Callable[[str], str | None]


def _wrap_untrusted(payload: Any) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"<untrusted>\n{body}\n</untrusted>"


def check_tavily_privacy(
    *,
    content_class: str = "normal",
    offline: bool = False,
    allow_private_in_query: bool = False,
    allow_sensitive_in_query: bool = False,
) -> tuple[bool, str | None]:
    """Return (ok, reason). Sensitive never allowed for Tavily (§17.3)."""
    if offline:
        return False, "offline_mode"
    cls = (content_class or "normal").lower()
    rule = _TAVILY_MATRIX.get(cls, "deny")
    if rule == "allow":
        return True, None
    if rule == "deny":
        return False, f"privacy:tavily_forbidden:{cls}"
    # auth required for private
    if cls == "private" and allow_private_in_query:
        return True, None
    if cls == "sensitive" and allow_sensitive_in_query:
        # Still forbidden by matrix — do not widen defaults
        return False, "privacy:tavily_forbidden:sensitive"
    return False, f"privacy:tavily_needs_auth:{cls}"


@dataclass
class UsageEvent:
    kind: str
    tool: str
    task_uid: str | None
    ok: bool
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tool": self.tool,
            "task_uid": self.task_uid,
            "ok": self.ok,
            "meta": dict(self.meta),
        }


@dataclass
class TavilyAdapter:
    """Per-task Tavily facade with budget, privacy, and fallback."""

    api_key: str | None = None
    offline: bool = False
    budget: int = DEFAULT_BUDGET
    task_uid: str | None = None
    content_class: str = "normal"
    allow_private_in_query: bool = False
    sdk_factory: SdkFactory | None = None
    fallback_search: FallbackHandler | None = None
    fallback_fetch: FallbackHandler | None = None
    _calls: int = 0
    _usage: list[UsageEvent] = field(default_factory=list)
    _client: Any = None
    _status_hint: str | None = None

    def status(self) -> dict[str, Any]:
        installed = self._sdk_available()
        configured = bool(self.api_key)
        available = installed and configured and not self.offline
        hint = self._status_hint
        if self.offline:
            hint = "offline_mode"
        elif not installed:
            hint = "E_DEP_MISSING:tavily-python"
        elif not configured:
            hint = "not_configured:secret:tavily"
        return {
            "installed": installed,
            "configured": configured,
            "offline": self.offline,
            "available": available,
            "budget": self.budget,
            "used": self._calls,
            "remaining": max(0, self.budget - self._calls),
            "hint": hint,
            "provider": "tavily" if available else "fallback",
        }

    def _sdk_available(self) -> bool:
        if self.sdk_factory is not None:
            return True
        try:
            import tavily  # noqa: F401

            return True
        except ImportError:
            return False

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise TavilyError("not_configured")
        if self.sdk_factory:
            self._client = self.sdk_factory(self.api_key)
        else:
            try:
                from tavily import TavilyClient
            except ImportError as exc:
                raise TavilyError("E_DEP_MISSING:tavily-python") from exc
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    def usage_events(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._usage]

    def _record(self, tool: str, ok: bool, **meta: Any) -> None:
        self._usage.append(
            UsageEvent(
                kind="tavily",
                tool=tool,
                task_uid=self.task_uid,
                ok=ok,
                meta=meta,
            )
        )

    def _privacy_gate(self) -> dict[str, Any] | None:
        ok, reason = check_tavily_privacy(
            content_class=self.content_class,
            offline=self.offline,
            allow_private_in_query=self.allow_private_in_query,
        )
        if ok:
            return None
        return {"ok": False, "error": reason, "provider": "tavily", "untrusted": False}

    def _budget_gate(self) -> dict[str, Any] | None:
        if self._calls >= self.budget:
            return {
                "ok": False,
                "error": "budget_exceeded",
                "budget": self.budget,
                "used": self._calls,
                "provider": "tavily",
            }
        return None

    def _consume(self) -> None:
        self._calls += 1

    def search(
        self,
        query: str,
        *,
        max_results: int = DEFAULT_MAX_RESULTS,
        search_depth: str = "basic",
        **kwargs: Any,
    ) -> dict[str, Any]:
        blocked = self._privacy_gate()
        if blocked:
            self._record("web_search", False, reason=blocked.get("error"))
            return blocked

        if not self.status()["available"]:
            return self._fallback_search(query, max_results=max_results)

        budget = self._budget_gate()
        if budget:
            self._record("web_search", False, reason="budget_exceeded")
            return budget

        try:
            client = self._get_client()
            raw = client.search(
                query,
                search_depth=search_depth,
                max_results=max_results,
                include_answer=False,
                include_raw_content=False,
                topic=kwargs.get("topic", "general"),
                include_domains=kwargs.get("include_domains"),
                exclude_domains=kwargs.get("exclude_domains"),
            )
            self._consume()
            self._record("web_search", True, max_results=max_results)
            return {
                "ok": True,
                "provider": "tavily",
                "untrusted": True,
                "query": query,
                "results": raw,
                "wrapped": _wrap_untrusted(raw),
            }
        except Exception as exc:
            self._record("web_search", False, reason=str(exc))
            # On SDK failure, try fallback
            fb = self._fallback_search(query, max_results=max_results)
            fb["tavily_error"] = str(exc)
            return fb

    def extract(self, urls: list[str] | str, **kwargs: Any) -> dict[str, Any]:
        blocked = self._privacy_gate()
        if blocked:
            self._record("web_fetch", False, reason=blocked.get("error"))
            return blocked

        url_list = [urls] if isinstance(urls, str) else list(urls)
        if not self.status()["available"]:
            return self._fallback_fetch(url_list)

        budget = self._budget_gate()
        if budget:
            self._record("web_fetch", False, reason="budget_exceeded")
            return budget

        try:
            client = self._get_client()
            raw = client.extract(urls=url_list, **kwargs)
            self._consume()
            self._record("web_fetch", True, urls=len(url_list))
            return {
                "ok": True,
                "provider": "tavily",
                "untrusted": True,
                "urls": url_list,
                "results": raw,
                "wrapped": _wrap_untrusted(raw),
            }
        except Exception as exc:
            self._record("web_fetch", False, reason=str(exc))
            fb = self._fallback_fetch(url_list)
            fb["tavily_error"] = str(exc)
            return fb

    def crawl(
        self,
        url: str,
        *,
        limit: int = DEFAULT_CRAWL_LIMIT,
        use_map: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        blocked = self._privacy_gate()
        if blocked:
            self._record("web_crawl", False, reason=blocked.get("error"))
            return blocked

        if not self.status()["available"]:
            return {
                "ok": False,
                "error": self.status().get("hint") or "not_available",
                "provider": "fallback",
                "hint": "web_crawl requires Tavily",
            }

        budget = self._budget_gate()
        if budget:
            self._record("web_crawl", False, reason="budget_exceeded")
            return budget

        limit = max(1, min(int(limit), DEFAULT_CRAWL_LIMIT))
        try:
            client = self._get_client()
            if use_map:
                raw = client.map(url, limit=limit, **kwargs)
            else:
                raw = client.crawl(url, limit=limit, **kwargs)
            self._consume()
            self._record("web_crawl", True, limit=limit, use_map=use_map)
            return {
                "ok": True,
                "provider": "tavily",
                "untrusted": True,
                "url": url,
                "limit": limit,
                "results": raw,
                "wrapped": _wrap_untrusted(raw),
            }
        except Exception as exc:
            self._record("web_crawl", False, reason=str(exc))
            return {"ok": False, "error": str(exc), "provider": "tavily"}

    def _fallback_search(self, query: str, *, max_results: int) -> dict[str, Any]:
        hint = self.status().get("hint") or "fallback"
        self._status_hint = hint
        if self.fallback_search:
            out = self.fallback_search({"query": query, "max_results": max_results})
            out = dict(out)
            out.setdefault("ok", True)
            out["provider"] = "fallback"
            out["hint"] = hint
            out["untrusted"] = True
            if "wrapped" not in out:
                out["wrapped"] = _wrap_untrusted(out.get("results", out))
            self._record("web_search", True, fallback=True, hint=hint)
            return out
        self._record("web_search", False, fallback=True, hint=hint)
        return {
            "ok": False,
            "provider": "fallback",
            "hint": hint,
            "error": "tavily_unavailable_no_fallback_impl",
            "message": "Tavily not configured; legacy web_search stub",
        }

    def _fallback_fetch(self, urls: list[str]) -> dict[str, Any]:
        hint = self.status().get("hint") or "fallback"
        if self.fallback_fetch:
            out = self.fallback_fetch({"urls": urls})
            out = dict(out)
            out.setdefault("ok", True)
            out["provider"] = "fallback"
            out["hint"] = hint
            out["untrusted"] = True
            if "wrapped" not in out:
                out["wrapped"] = _wrap_untrusted(out.get("results", out))
            self._record("web_fetch", True, fallback=True, hint=hint)
            return out
        self._record("web_fetch", False, fallback=True, hint=hint)
        return {
            "ok": False,
            "provider": "fallback",
            "hint": hint,
            "error": "tavily_unavailable_no_fallback_impl",
            "urls": urls,
        }


def default_legacy_search(arguments: dict[str, Any]) -> dict[str, Any]:
    """Scaffold legacy web_search (no v0.11 HTTP crawler in this repo)."""
    return {
        "ok": True,
        "stub": True,
        "results": [],
        "query": arguments.get("query"),
        "message": "legacy web_search scaffold (no network)",
    }


def default_legacy_fetch(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "stub": True,
        "results": [],
        "urls": arguments.get("urls") or arguments.get("url"),
        "message": "legacy web_fetch scaffold (no network)",
    }


def build_tavily_adapter(
    *,
    secret_getter: SecretGetter | None = None,
    secret_name: str = "tavily",
    api_key: str | None = None,
    offline: bool = False,
    budget: int = DEFAULT_BUDGET,
    task_uid: str | None = None,
    content_class: str = "normal",
    allow_private_in_query: bool = False,
    sdk_factory: SdkFactory | None = None,
    fallback_search: FallbackHandler | None = None,
    fallback_fetch: FallbackHandler | None = None,
) -> TavilyAdapter:
    key = api_key
    if key is None and secret_getter is not None:
        key = secret_getter(secret_name)
    return TavilyAdapter(
        api_key=key,
        offline=offline,
        budget=budget,
        task_uid=task_uid,
        content_class=content_class,
        allow_private_in_query=allow_private_in_query,
        sdk_factory=sdk_factory,
        fallback_search=fallback_search or default_legacy_search,
        fallback_fetch=fallback_fetch or default_legacy_fetch,
    )


def bind_tavily_tools(registry: Any, adapter: TavilyAdapter) -> None:
    """Replace web_search / web_fetch handlers; register web_crawl."""
    from localwiki.tools.registry import ToolSpec

    def _search(args: dict[str, Any]) -> dict[str, Any]:
        return adapter.search(
            str(args.get("query") or ""),
            max_results=int(args.get("max_results") or DEFAULT_MAX_RESULTS),
        )

    def _fetch(args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url")
        urls = args.get("urls")
        if urls:
            return adapter.extract(list(urls))
        return adapter.extract(str(url or ""))

    def _crawl(args: dict[str, Any]) -> dict[str, Any]:
        return adapter.crawl(
            str(args.get("url") or ""),
            limit=int(args.get("limit") or DEFAULT_CRAWL_LIMIT),
            use_map=bool(args.get("use_map")),
        )

    for name, handler in (("web_search", _search), ("web_fetch", _fetch)):
        spec = registry.get(name)
        if spec:
            registry.register(spec, handler=handler)

    if registry.get("web_crawl") is None:
        registry.register(
            ToolSpec(
                name="web_crawl",
                provider="tavily",
                description="Crawl/map pages via Tavily (page limit enforced)",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "limit": {"type": "integer"},
                        "use_map": {"type": "boolean"},
                    },
                    "required": ["url"],
                },
                risk="R1",
                privacy={"egress": "network", "sends_user_content": True},
                category="network",
                meta={"tavily": True},
            ),
            handler=_crawl,
        )
    else:
        registry.register(registry.get("web_crawl"), handler=_crawl)
