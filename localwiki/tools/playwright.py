"""Playwright-MCP adapter (design §12.4, AT-TOOL-06).

Risk mapping, domain allowlist + approval gate, submit-keyword escalation.
``browser_*`` aliases route through this adapter; Node missing → fallback stub.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from localwiki.tools.mcp_client import MCPClient, MCPServerConfig

PLAYWRIGHT_MCP_VERSION = "0.0.83"
PLAYWRIGHT_SERVER = "playwright"

# §12.4 risk mapping
RISK_R1 = frozenset(
    {
        "browser_navigate",
        "browser_open",
        "browser_inspect",
        "browser_snapshot",
        "browser_screenshot",
        "browser_take_screenshot",
        "browser_wait_for",
        "browser_tabs",
    }
)
RISK_R2 = frozenset(
    {
        "browser_click",
        "browser_type",
        "browser_fill",
        "browser_fill_form",
        "browser_select",
        "browser_hover",
    }
)
RISK_R3 = frozenset(
    {
        "browser_evaluate",
        "browser_file_upload",
        "browser_handle_dialog",
    }
)

# Alias map: v0.11 native name → Playwright-MCP tool name
BROWSER_ALIASES: dict[str, str] = {
    "browser_open": "browser_navigate",
    "browser_inspect": "browser_snapshot",
    "browser_click": "browser_click",
    "browser_fill": "browser_type",
    "browser_screenshot": "browser_take_screenshot",
}

DEFAULT_SUBMIT_KEYWORDS = (
    "提交",
    "发送",
    "支付",
    "删除",
    "确认",
    "submit",
    "send",
    "pay",
    "delete",
    "confirm",
    "purchase",
    "checkout",
    "登录",
    "login",
    "sign in",
    "sign-in",
)

CallFn = Callable[[str, str, dict[str, Any]], dict[str, Any]]
FallbackFn = Callable[[str, dict[str, Any]], dict[str, Any]]


def _wrap_untrusted(payload: Any) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"<untrusted>\n{body}\n</untrusted>"


def node_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npx") is not None


def extract_host(url: str) -> str | None:
    if not url:
        return None
    raw = url.strip()
    if re.match(r"^(about:|data:|blob:)", raw, re.I):
        return None
    if "://" not in raw and not raw.startswith("file:"):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme in ("file",):
        return "file"
    host = (parsed.hostname or "").lower().rstrip(".")
    return host or None


def host_allowed(host: str | None, allowlist: list[str] | set[str]) -> bool:
    if not allowlist:
        return False
    if host is None:
        return False
    if host == "file" and ("file" in allowlist or "file://" in allowlist):
        return True
    allowed = {a.lower().lstrip(".") for a in allowlist}
    if host in allowed:
        return True
    # subdomain match: example.com allows a.example.com
    for a in allowed:
        if a.startswith("*."):
            suffix = a[2:]
            if host == suffix or host.endswith("." + suffix):
                return True
        elif host.endswith("." + a):
            return True
    return False


def base_risk_for_tool(tool: str) -> str:
    name = tool.lower()
    # Strip mcp.playwright. prefix if present
    if name.startswith("mcp."):
        parts = name.split(".")
        name = parts[-1]
    if name in RISK_R3:
        return "R3"
    if name in RISK_R2:
        return "R2"
    if name in RISK_R1:
        return "R1"
    # Unknown browser-ish → R2 default (not R0)
    if name.startswith("browser_"):
        return "R2"
    return "R1"


def looks_like_submit(
    target: str | None,
    *,
    keywords: tuple[str, ...] = DEFAULT_SUBMIT_KEYWORDS,
    role: str | None = None,
) -> bool:
    text = f"{target or ''} {role or ''}".lower()
    if not text.strip():
        return False
    for kw in keywords:
        if kw.lower() in text:
            return True
    return False


def resolve_risk(
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    keywords: tuple[str, ...] = DEFAULT_SUBMIT_KEYWORDS,
) -> str:
    """Compute effective risk; escalate click/fill on submit-like targets to R3."""
    args = arguments or {}
    risk = base_risk_for_tool(tool)
    mapped = BROWSER_ALIASES.get(tool, tool)
    # Form submit / payment / login related pages
    if mapped in ("browser_click", "browser_type", "browser_fill", "browser_fill_form"):
        target = (
            args.get("element")
            or args.get("ref")
            or args.get("name")
            or args.get("text")
            or args.get("label")
            or ""
        )
        role = args.get("role")
        page_url = str(args.get("url") or args.get("page_url") or "")
        page_hint = page_url.lower()
        if looks_like_submit(str(target), keywords=keywords, role=str(role) if role else None):
            return "R3"
        if any(k in page_hint for k in ("login", "signin", "checkout", "pay", "登录", "支付")):
            if mapped in ("browser_click", "browser_fill_form"):
                return "R3"
    if mapped == "browser_evaluate":
        return "R3"
    return risk


@dataclass
class DomainDecision:
    allowed: bool
    host: str | None
    needs_approval: bool
    reason: str


def check_domain(
    url: str,
    allowlist: list[str],
    *,
    offline_local_only: bool = False,
) -> DomainDecision:
    host = extract_host(url)
    if offline_local_only:
        # §17.4: Playwright offline → only localhost and file://
        local_ok = host in ("localhost", "127.0.0.1", "::1", "file") or (
            host is not None and host.endswith(".localhost")
        )
        if not local_ok:
            return DomainDecision(
                allowed=False,
                host=host,
                needs_approval=False,
                reason="offline_blocks_remote",
            )
        return DomainDecision(True, host, False, "offline_local_ok")

    if host_allowed(host, allowlist):
        return DomainDecision(True, host, False, "allowlist")
    return DomainDecision(
        allowed=False,
        host=host,
        needs_approval=True,
        reason="domain_not_in_allowlist",
    )


@dataclass
class PlaywrightAdapter:
    """Policy + routing layer over MCP Client for Playwright-MCP."""

    mcp: MCPClient | None = None
    allowlist: list[str] = field(default_factory=list)
    approved_hosts: set[str] = field(default_factory=set)
    submit_keywords: tuple[str, ...] = DEFAULT_SUBMIT_KEYWORDS
    offline_local_only: bool = False
    task_dir: Path | None = None
    call_fn: CallFn | None = None  # inject for unit tests
    fallback_fn: FallbackFn | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def status(self) -> dict[str, Any]:
        has_node = node_available()
        return {
            "node": has_node,
            "available": has_node or self.call_fn is not None,
            "hint": None if has_node or self.call_fn else "E_DEP_MISSING:node",
            "allowlist": list(self.allowlist),
            "version": PLAYWRIGHT_MCP_VERSION,
        }

    def ensure_server_config(self) -> MCPServerConfig:
        out_dir = str((self.task_dir or Path(".")) / "browser")
        args = [
            f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}",
            "--isolated",
            "--output-dir",
            out_dir,
        ]
        # Dual restriction: pass allowed origins when list non-empty
        if self.allowlist:
            origins = []
            for h in self.allowlist:
                if h in ("file", "file://"):
                    continue
                if h.startswith("http"):
                    origins.append(h)
                else:
                    origins.append(f"https://{h}")
            if origins:
                args.extend(["--allowed-origins", ";".join(origins)])
        cfg = MCPServerConfig(
            name=PLAYWRIGHT_SERVER,
            command="npx",
            args=args,
            autostart=False,
            idle_timeout_s=600,
        )
        cfg.validate()
        if self.mcp is not None:
            self.mcp.set_server(cfg)
        return cfg

    def approve_host(self, host: str) -> None:
        self.approved_hosts.add(host.lower())

    def evaluate_call(
        self, tool: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return policy decision without executing."""
        args = dict(arguments or {})
        mapped = BROWSER_ALIASES.get(tool, tool)
        risk = resolve_risk(tool, args, keywords=self.submit_keywords)
        url = str(args.get("url") or args.get("page_url") or "")
        domain: DomainDecision | None = None
        if url or mapped in ("browser_navigate", "browser_open"):
            if not url and mapped in ("browser_navigate", "browser_open"):
                return {
                    "ok": False,
                    "error": "url_required",
                    "risk": risk,
                    "mapped_tool": mapped,
                }
            if url:
                domain = check_domain(
                    url,
                    self.allowlist,
                    offline_local_only=self.offline_local_only,
                )
                if not domain.allowed:
                    host = domain.host or ""
                    if domain.needs_approval and host and host in self.approved_hosts:
                        domain = DomainDecision(True, host, False, "user_approved")
                    else:
                        return {
                            "ok": False,
                            "error": domain.reason,
                            "needs_approval": domain.needs_approval,
                            "host": domain.host,
                            "risk": risk,
                            "mapped_tool": mapped,
                        }
        return {
            "ok": True,
            "risk": risk,
            "mapped_tool": mapped,
            "host": domain.host if domain else None,
        }

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        decision = self.evaluate_call(tool, args)
        if not decision.get("ok"):
            return decision

        mapped = str(decision["mapped_tool"])
        risk = str(decision["risk"])
        st = self.status()
        if not st["available"]:
            if self.fallback_fn:
                out = self.fallback_fn(tool, args)
                out = dict(out)
                out["provider"] = "fallback"
                out["risk"] = risk
                out["hint"] = st.get("hint")
                return out
            return {
                "ok": False,
                "error": st.get("hint") or "playwright_unavailable",
                "risk": risk,
            }

        # Normalize args for navigate alias
        if tool == "browser_open" and "url" in args:
            mcp_args = {"url": args["url"]}
        else:
            mcp_args = args

        try:
            if self.call_fn is not None:
                raw = self.call_fn(PLAYWRIGHT_SERVER, mapped, mcp_args)
            elif self.mcp is not None:
                self.ensure_server_config()
                result = self.mcp.call_tool(PLAYWRIGHT_SERVER, mapped, mcp_args)
                raw = result.to_dict()
            else:
                return {"ok": False, "error": "no_mcp_client", "risk": risk}
        except Exception as exc:
            if self.fallback_fn:
                out = self.fallback_fn(tool, args)
                out = dict(out)
                out["provider"] = "fallback"
                out["playwright_error"] = str(exc)
                out["risk"] = risk
                return out
            return {"ok": False, "error": str(exc), "risk": risk}

        evidence = {
            "tool": mapped,
            "risk": risk,
            "host": decision.get("host"),
            "args_keys": sorted(mcp_args.keys()),
        }
        self.evidence.append(evidence)
        content = raw.get("content", raw) if isinstance(raw, dict) else raw
        ok = bool(raw.get("ok", True)) if isinstance(raw, dict) else True
        return {
            "ok": ok,
            "provider": "playwright-mcp",
            "mapped_tool": mapped,
            "risk": risk,
            "untrusted": True,
            "result": content,
            "wrapped": _wrap_untrusted(content),
            "evidence": evidence,
        }

    def bind_browser_aliases(self, registry: Any) -> None:
        for native_name in BROWSER_ALIASES:
            spec = registry.get(native_name)
            if spec is None:
                continue

            def _make(n: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
                def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
                    return self.call(n, arguments)

                return _handler

            # Keep native risk as floor; execute path re-evaluates
            registry.register(spec, handler=_make(native_name))
