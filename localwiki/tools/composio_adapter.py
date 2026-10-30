"""Composio adapter for SDK 0.25 (design §12.5).

Uses ``composio.Composio`` (not legacy ComposioToolSet). Offline disabled.
Risk from tool slug verbs; write calls get external_write receipts.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from localwiki.tools.registry import ToolRegistry, ToolSpec, sanitize_description

R1_VERBS = frozenset(
    {"GET", "LIST", "SEARCH", "FETCH", "FIND", "READ", "DESCRIBE", "SHOW", "VIEW", "STATUS"}
)
R3_VERBS = frozenset(
    {
        "CREATE",
        "UPDATE",
        "DELETE",
        "SEND",
        "POST",
        "ADD",
        "REMOVE",
        "PUT",
        "PATCH",
        "WRITE",
        "UPLOAD",
        "INVITE",
        "REVOKE",
        "CANCEL",
        "EXECUTE",
        "RUN",
        "PUBLISH",
        "MERGE",
        "CLOSE",
        "OPEN",
        "COMMENT",
        "REPLY",
    }
)

_SLUG_SPLIT = re.compile(r"[_\-\s]+")


class ComposioError(RuntimeError):
    pass


class ComposioSdk(Protocol):
    toolkits: Any
    tools: Any
    connected_accounts: Any


SdkFactory = Callable[[str], Any]
SecretGetter = Callable[[str], str | None]


def classify_tool_risk(slug: str, *, override: str | None = None) -> str:
    """§12.5: read-ish verbs → R1; write-ish → R3; unknown → R3."""
    if override in ("R0", "R1", "R2", "R3"):
        return override
    parts = [p.upper() for p in _SLUG_SPLIT.split(slug) if p]
    # Prefer later verb tokens (GITHUB_CREATE_ISSUE → CREATE)
    verbs = [p for p in parts if p in R1_VERBS or p in R3_VERBS]
    if not verbs:
        return "R3"
    # If any R3 verb present → R3 (CREATE beats GET in same slug)
    if any(v in R3_VERBS for v in verbs):
        return "R3"
    return "R1"


def check_composio_privacy(
    *,
    content_class: str = "normal",
    offline: bool = False,
    allow_private_params: bool = False,
) -> tuple[bool, str | None]:
    if offline:
        return False, "offline_mode"
    cls = (content_class or "normal").lower()
    if cls == "sensitive":
        return False, "privacy:composio_forbidden:sensitive"
    if cls == "private" and not allow_private_params:
        return False, "privacy:composio_needs_auth:private"
    return True, None


def _wrap_untrusted(payload: Any) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"<untrusted>\n{body}\n</untrusted>"


def _request_hash(slug: str, arguments: dict[str, Any]) -> str:
    blob = json.dumps({"slug": slug, "arguments": arguments}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ToolConnection:
    """Metadata only — never stores OAuth tokens (Composio-hosted)."""

    toolkit: str
    user_id: str
    status: str = "connected"  # pending | connected | revoked
    connected_account_id: str | None = None
    redirect_url: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExternalWriteReceipt:
    """Idempotent write receipt (§12.5 / external_writes)."""

    receipt_id: str
    slug: str
    idempotency_key: str
    request_hash: str
    resource_id: str | None = None
    status: str = "unknown"  # ok | failed | unknown
    created_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FakeAuthorizeRequest:
    redirect_url: str
    toolkit: str
    user_id: str
    _connected: bool = False

    def wait_for_connection(self, timeout: float = 1.0) -> dict[str, Any]:
        self._connected = True
        return {"status": "ACTIVE", "id": f"conn_{self.toolkit}"}


class FakeComposioSdk:
    """Recorded-response stand-in for composio.Composio (unit tests)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.toolkits = _FakeToolkits(self)
        self.tools = _FakeTools(self)
        self.connected_accounts = _FakeAccounts()
        self._connected: set[str] = set()


class _FakeToolkits:
    def __init__(self, parent: FakeComposioSdk) -> None:
        self._p = parent

    def authorize(self, user_id: str, toolkit: str) -> FakeAuthorizeRequest:
        return FakeAuthorizeRequest(
            redirect_url=f"https://composio.dev/auth/{toolkit}?u={user_id}",
            toolkit=toolkit.lower(),
            user_id=user_id,
        )


class _FakeTools:
    def __init__(self, parent: FakeComposioSdk) -> None:
        self._p = parent
        self.executions: list[dict[str, Any]] = []

    def get(self, user_id: str, toolkits: list[str] | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        tk = [t.upper() for t in (toolkits or ["GITHUB"])]
        out: list[dict[str, Any]] = []
        if "GITHUB" in tk:
            out.extend(
                [
                    {
                        "slug": "GITHUB_LIST_REPOS",
                        "name": "List repos",
                        "description": "List repositories",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                    {
                        "slug": "GITHUB_CREATE_AN_ISSUE",
                        "name": "Create issue",
                        "description": "Create a GitHub issue",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "owner": {"type": "string"},
                                "repo": {"type": "string"},
                                "title": {"type": "string"},
                            },
                            "required": ["owner", "repo", "title"],
                        },
                    },
                ]
            )
        return out

    def execute(
        self, slug: str, user_id: str, arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        self.executions.append({"slug": slug, "user_id": user_id, "arguments": args})
        if "CREATE" in slug.upper() or "SEND" in slug.upper():
            return {"successful": True, "data": {"id": "res_123", "html_url": "https://example/issue/1"}}
        return {"successful": True, "data": {"items": []}}


class _FakeAccounts:
    def list(self, **kwargs: Any) -> list[Any]:
        return []


@dataclass
class ComposioAdapter:
    api_key: str | None = None
    user_id: str = "local-user"
    offline: bool = False
    content_class: str = "normal"
    allow_private_params: bool = False
    sdk_factory: SdkFactory | None = None
    risk_overrides: dict[str, str] = field(default_factory=dict)
    connections: dict[str, ToolConnection] = field(default_factory=dict)
    receipts: list[ExternalWriteReceipt] = field(default_factory=list)
    enabled_tools: set[str] | None = None  # None = all registered
    _client: Any = None
    _registered: dict[str, str] = field(default_factory=dict)  # slug -> risk

    def status(self) -> dict[str, Any]:
        installed = self._sdk_available()
        configured = bool(self.api_key)
        available = installed and configured and not self.offline
        hint = None
        if self.offline:
            hint = "offline_mode"
        elif not configured:
            hint = "not_configured:secret:composio"
            if not installed:
                hint = "not_configured:secret:composio;E_DEP_MISSING:composio"
        elif not installed:
            hint = "E_DEP_MISSING:composio"
        return {
            "installed": installed,
            "configured": configured,
            "offline": self.offline,
            "available": available,
            "hint": hint,
            "connections": [c.to_dict() for c in self.connections.values()],
        }

    def _sdk_available(self) -> bool:
        if self.sdk_factory is not None:
            return True
        try:
            import composio  # noqa: F401

            return True
        except ImportError:
            return False

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ComposioError("not_configured")
        if self.offline:
            raise ComposioError("offline_mode")
        if self.sdk_factory:
            self._client = self.sdk_factory(self.api_key)
        else:
            try:
                from composio import Composio
            except ImportError as exc:
                raise ComposioError("E_DEP_MISSING:composio") from exc
            self._client = Composio(api_key=self.api_key)
        return self._client

    def authorize(self, toolkit: str) -> ToolConnection:
        """Start OAuth for a toolkit; metadata only (no tokens stored)."""
        ok, reason = check_composio_privacy(
            content_class=self.content_class,
            offline=self.offline,
            allow_private_params=True,  # auth itself is allowed when online
        )
        if self.offline:
            raise ComposioError("offline_mode")
        if not self.status()["available"] and self.sdk_factory is None and not self.api_key:
            raise ComposioError(self.status().get("hint") or "unavailable")

        client = self._client_or_raise()
        tk = toolkit.lower()
        req = client.toolkits.authorize(user_id=self.user_id, toolkit=tk)
        redirect = getattr(req, "redirect_url", None) or getattr(req, "redirectUrl", None)
        conn = ToolConnection(
            toolkit=tk,
            user_id=self.user_id,
            status="pending",
            redirect_url=str(redirect) if redirect else None,
        )
        # Optional wait — Fake always succeeds; live may block (@live)
        wait = getattr(req, "wait_for_connection", None)
        if callable(wait):
            try:
                info = wait()
                conn.status = "connected"
                if isinstance(info, dict):
                    conn.connected_account_id = str(info.get("id") or "") or None
            except Exception:
                conn.status = "pending"
        self.connections[tk] = conn
        return conn

    def list_toolkit_tools(self, toolkit: str) -> list[dict[str, Any]]:
        client = self._client_or_raise()
        raw = client.tools.get(user_id=self.user_id, toolkits=[toolkit.upper()])
        tools: list[dict[str, Any]] = []
        for item in raw or []:
            if isinstance(item, dict):
                slug = str(item.get("slug") or item.get("name") or "")
                desc = str(item.get("description") or item.get("name") or slug)
                schema = item.get("input_schema") or item.get("parameters") or {
                    "type": "object",
                    "properties": {},
                }
            else:
                slug = str(getattr(item, "slug", None) or getattr(item, "name", "") or "")
                desc = str(getattr(item, "description", None) or slug)
                schema = getattr(item, "input_schema", None) or {
                    "type": "object",
                    "properties": {},
                }
                if hasattr(schema, "model_dump"):
                    schema = schema.model_dump()
            if not slug:
                continue
            risk = classify_tool_risk(slug, override=self.risk_overrides.get(slug))
            tools.append(
                {
                    "slug": slug,
                    "description": sanitize_description(desc),
                    "input_schema": schema if isinstance(schema, dict) else {
                        "type": "object",
                        "properties": {},
                    },
                    "risk": risk,
                    "toolkit": toolkit.upper(),
                }
            )
        return tools

    def register_connected_tools(self, registry: ToolRegistry, toolkit: str) -> list[str]:
        """Register tools for a connected toolkit as ``composio.<SLUG>``."""
        tk = toolkit.lower()
        conn = self.connections.get(tk)
        if conn is None or conn.status != "connected":
            raise ComposioError(f"toolkit not connected: {toolkit}")

        registered: list[str] = []
        for tool in self.list_toolkit_tools(toolkit):
            slug = tool["slug"]
            if self.enabled_tools is not None and slug not in self.enabled_tools:
                continue
            qn = f"composio.{slug}"
            risk = tool["risk"]
            self._registered[slug] = risk

            def _make(s: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
                def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
                    return self.execute(s, arguments)

                return _handler

            registry.register(
                ToolSpec(
                    name=qn,
                    provider="composio",
                    provider_ref=toolkit.upper(),
                    description=tool["description"],
                    input_schema=tool["input_schema"],
                    risk=risk,
                    privacy={"egress": "network", "sends_user_content": True},
                    category="third_party",
                    meta={"composio_slug": slug, "toolkit": toolkit.upper()},
                ),
                handler=_make(slug),
                trust_description=False,
            )
            registered.append(qn)
        return registered

    def execute(self, slug: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        ok, reason = check_composio_privacy(
            content_class=self.content_class,
            offline=self.offline,
            allow_private_params=self.allow_private_params,
        )
        if not ok:
            return {"ok": False, "error": reason, "slug": slug}

        risk = classify_tool_risk(slug, override=self.risk_overrides.get(slug))
        is_write = risk == "R3"
        idem = str(args.pop("idempotency_key", None) or uuid.uuid4())
        req_hash = _request_hash(slug, args)

        # Dedup successful write by idempotency key
        if is_write:
            for r in self.receipts:
                if r.idempotency_key == idem and r.status == "ok":
                    return {
                        "ok": True,
                        "slug": slug,
                        "risk": risk,
                        "deduped": True,
                        "receipt": r.to_dict(),
                        "result": {"resource_id": r.resource_id},
                    }

        try:
            client = self._client_or_raise()
            raw = client.tools.execute(slug, user_id=self.user_id, arguments=args)
        except Exception as exc:
            receipt = None
            if is_write:
                receipt = ExternalWriteReceipt(
                    receipt_id=str(uuid.uuid4()),
                    slug=slug,
                    idempotency_key=idem,
                    request_hash=req_hash,
                    status="unknown",
                    meta={"error": str(exc)},
                )
                self.receipts.append(receipt)
            return {
                "ok": False,
                "error": str(exc),
                "slug": slug,
                "risk": risk,
                "receipt": receipt.to_dict() if receipt else None,
                "needs_manual_review": is_write,
            }

        resource_id = None
        if isinstance(raw, dict):
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            if isinstance(data, dict):
                resource_id = data.get("id") or data.get("resource_id")

        receipt = None
        if is_write:
            receipt = ExternalWriteReceipt(
                receipt_id=str(uuid.uuid4()),
                slug=slug,
                idempotency_key=idem,
                request_hash=req_hash,
                resource_id=str(resource_id) if resource_id is not None else None,
                status="ok",
            )
            self.receipts.append(receipt)

        return {
            "ok": True,
            "slug": slug,
            "risk": risk,
            "untrusted": True,
            "result": raw,
            "wrapped": _wrap_untrusted(raw),
            "receipt": receipt.to_dict() if receipt else None,
        }

    def save_connections(self, path: Path) -> None:
        """Persist connection metadata (no secrets)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "connections": [c.to_dict() for c in self.connections.values()],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if "token" in text.lower() and "redirect" not in text.lower():
            # Belt-and-suspenders: refuse accidental token dumps
            raise ComposioError("refusing to write payload that looks like tokens")
        path.write_text(text + "\n", encoding="utf-8")

    def bind_integration_aliases(self, registry: ToolRegistry) -> None:
        """Wire integration_list / integration_call / integration_write stubs."""

        def _list(arguments: dict[str, Any]) -> dict[str, Any]:
            return {
                "ok": True,
                "connections": [c.to_dict() for c in self.connections.values()],
                "tools": list(self._registered),
            }

        def _call(arguments: dict[str, Any]) -> dict[str, Any]:
            slug = str(arguments.get("slug") or arguments.get("tool") or "")
            args = arguments.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            # Force read path: reject write slugs via this tool
            risk = classify_tool_risk(slug, override=self.risk_overrides.get(slug))
            if risk == "R3":
                return {
                    "ok": False,
                    "error": "use_integration_write_for_mutating",
                    "slug": slug,
                    "risk": risk,
                }
            return self.execute(slug, args)

        def _write(arguments: dict[str, Any]) -> dict[str, Any]:
            slug = str(arguments.get("slug") or arguments.get("tool") or "")
            args = arguments.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            if "idempotency_key" in arguments:
                args = dict(args)
                args["idempotency_key"] = arguments["idempotency_key"]
            return self.execute(slug, args)

        for name, handler in (
            ("integration_list", _list),
            ("integration_call", _call),
            ("integration_write", _write),
        ):
            spec = registry.get(name)
            if spec:
                registry.register(spec, handler=handler)


def build_composio_adapter(
    *,
    api_key: str | None = None,
    secret_getter: SecretGetter | None = None,
    secret_name: str = "composio",
    user_id: str = "local-user",
    offline: bool = False,
    content_class: str = "normal",
    allow_private_params: bool = False,
    sdk_factory: SdkFactory | None = None,
) -> ComposioAdapter:
    key = api_key
    if key is None and secret_getter is not None:
        key = secret_getter(secret_name)
    return ComposioAdapter(
        api_key=key,
        user_id=user_id,
        offline=offline,
        content_class=content_class,
        allow_private_params=allow_private_params,
        sdk_factory=sdk_factory,
    )
