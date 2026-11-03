"""MCP Client: stdio + Streamable HTTP (design §12.2, AT-TOOL-03).

Lifecycle: first call starts process → initialize → tools/list → register;
idle timeout closes; crash restarts at most twice. Versions must be pinned
(no ``@latest``). ``secret:<name>`` env values resolve from SecretStore only
at process start and are never written to disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from localwiki.tools.registry import ToolRegistry, ToolSpec, sanitize_description

DEFAULT_IDLE_TIMEOUT_S = 600.0
MAX_RESTARTS = 2
CONFIG_REL = Path(".kw") / "runtime" / "mcp" / "servers.json"
LATEST_PAT = re.compile(r"@latest\b", re.I)
SECRET_ENV_PAT = re.compile(r"^secret:([A-Za-z0-9_.\-]+)$")


class MCPError(RuntimeError):
    pass


class MCPDependencyMissing(MCPError):
    """Raised when the optional ``mcp`` package is not installed."""


@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    autostart: bool = False
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S
    max_restarts: int = MAX_RESTARTS
    transport: str = "stdio"  # stdio | http

    def validate(self) -> None:
        blob = " ".join([self.command or "", *self.args, self.url or ""])
        if LATEST_PAT.search(blob):
            raise MCPError(
                f"MCP server {self.name!r}: pinned versions required; @latest forbidden"
            )
        if self.transport == "stdio" and not self.command:
            raise MCPError(f"MCP server {self.name!r}: stdio requires command")
        if self.transport == "http" and not self.url:
            raise MCPError(f"MCP server {self.name!r}: http requires url")


@dataclass
class DiscoveredTool:
    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    @property
    def qualified_name(self) -> str:
        return f"mcp.{self.server}.{self.name}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["qualified_name"] = self.qualified_name
        return d


@dataclass
class MCPCallResult:
    ok: bool
    server: str
    tool: str
    content: Any
    untrusted: bool = True
    error: str | None = None
    restarted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "server": self.server,
            "tool": self.tool,
            "content": self.content,
            "untrusted": self.untrusted,
            "error": self.error,
            "restarted": self.restarted,
            "wrapped": _wrap_untrusted(self.content) if self.ok else None,
        }


class MCPSession(Protocol):
    """Minimal session surface used by MCPClient (real SDK or fake)."""

    def initialize(self) -> None: ...
    def list_tools(self) -> list[DiscoveredTool]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...
    def close(self) -> None: ...


SessionFactory = Callable[["MCPServerConfig", dict[str, str]], MCPSession]
SecretResolver = Callable[[str], str]
Clock = Callable[[], float]


def _wrap_untrusted(payload: Any) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"<untrusted>\n{body}\n</untrusted>"


def load_servers_config(path: Path) -> dict[str, MCPServerConfig]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MCPError("servers.json must be an object")
    out: dict[str, MCPServerConfig] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise MCPError(f"invalid config for {name!r}")
        transport = str(cfg.get("transport") or ("http" if cfg.get("url") else "stdio"))
        sc = MCPServerConfig(
            name=name,
            command=cfg.get("command"),
            args=list(cfg.get("args") or []),
            env={str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()},
            url=cfg.get("url"),
            autostart=bool(cfg.get("autostart", False)),
            idle_timeout_s=float(cfg.get("idle_timeout_s", DEFAULT_IDLE_TIMEOUT_S)),
            max_restarts=int(cfg.get("max_restarts", MAX_RESTARTS)),
            transport=transport,
        )
        sc.validate()
        out[name] = sc
    return out


def write_default_servers_config(path: Path, *, force: bool = False) -> Path:
    """Write a template servers.json with pinned versions (no secrets)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return path
    template = {
        "playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@0.0.83",
                "--isolated",
                "--output-dir",
                "{task_dir}/browser",
            ],
            "env": {},
            "autostart": False,
            "idle_timeout_s": 600,
        },
        "tavily": {
            "command": "npx",
            "args": ["-y", "tavily-mcp@0.2.22"],
            "env": {"TAVILY_API_KEY": "secret:tavily"},
            "autostart": False,
            "idle_timeout_s": 600,
        },
    }
    path.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def resolve_env(
    env_cfg: dict[str, str],
    *,
    resolver: SecretResolver | None = None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build subprocess env; resolve ``secret:`` refs; never persist values."""
    out = dict(base_env if base_env is not None else os.environ)
    for key, val in env_cfg.items():
        m = SECRET_ENV_PAT.match(val.strip())
        if m:
            if resolver is None:
                raise MCPError(f"secret ref {val!r} for {key} but no SecretStore")
            out[key] = resolver(val)
        else:
            out[key] = val
    return out


@dataclass
class _ConnState:
    config: MCPServerConfig
    session: MCPSession | None = None
    tools: list[DiscoveredTool] = field(default_factory=list)
    last_used: float = 0.0
    restarts: int = 0
    connected: bool = False


class FakeMCPSession:
    """Recorded-response session for unit tests (no subprocess)."""

    def __init__(
        self,
        tools: list[DiscoveredTool] | None = None,
        call_map: dict[str, Any] | None = None,
        *,
        fail_init: bool = False,
        fail_calls: int = 0,
        crash_after_calls: int | None = None,
        server: str = "fake",
    ) -> None:
        self.server = server
        self._tools = tools or [
            DiscoveredTool(
                server=server,
                name="echo",
                description="echo args",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ]
        self._call_map = dict(call_map or {})
        self._fail_init = fail_init
        self._fail_calls_left = fail_calls
        self._crash_after = crash_after_calls
        self._call_count = 0
        self.closed = False
        self.initialized = False

    def initialize(self) -> None:
        if self._fail_init:
            raise MCPError("fake init failure")
        self.initialized = True

    def list_tools(self) -> list[DiscoveredTool]:
        if not self.initialized:
            raise MCPError("not initialized")
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.closed:
            raise MCPError("session closed")
        if not self.initialized:
            raise MCPError("not initialized")
        if self._fail_calls_left > 0:
            self._fail_calls_left -= 1
            raise MCPError("transient call failure")
        self._call_count += 1
        if self._crash_after is not None and self._call_count > self._crash_after:
            raise MCPError("simulated crash")
        if name in self._call_map:
            return self._call_map[name]
        return {"echo": arguments}

    def close(self) -> None:
        self.closed = True
        self.initialized = False


class SdkMCPSession:
    """Thin sync wrapper around official ``mcp`` ClientSession."""

    def __init__(self, config: MCPServerConfig, env: dict[str, str]) -> None:
        self._config = config
        self._env = env
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cm: Any = None
        self._session: Any = None
        self._server = config.name

    def initialize(self) -> None:
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._async_start())
        except Exception:
            self.close()
            raise

    async def _async_start(self) -> None:
        try:
            from mcp import ClientSession
        except ImportError as exc:
            raise MCPDependencyMissing("E_DEP_MISSING: mcp") from exc

        if self._config.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self._config.command or "",
                args=list(self._config.args),
                env=self._env,
            )
            self._cm = stdio_client(params)
            read, write = await self._cm.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
        elif self._config.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            self._cm = streamablehttp_client(self._config.url or "")
            read, write, _ = await self._cm.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
        else:
            raise MCPError(f"unsupported transport: {self._config.transport}")

    def list_tools(self) -> list[DiscoveredTool]:
        assert self._loop and self._session
        result = self._loop.run_until_complete(self._session.list_tools())
        tools: list[DiscoveredTool] = []
        for t in result.tools:
            schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
            if hasattr(schema, "model_dump"):
                schema = schema.model_dump()
            tools.append(
                DiscoveredTool(
                    server=self._server,
                    name=t.name,
                    description=sanitize_description(getattr(t, "description", "") or ""),
                    input_schema=dict(schema) if isinstance(schema, dict) else {
                        "type": "object",
                        "properties": {},
                    },
                )
            )
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        assert self._loop and self._session
        result = self._loop.run_until_complete(
            self._session.call_tool(name, arguments=arguments)
        )
        # Prefer structured content; fall back to text parts
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent
        parts: list[Any] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))
        if len(parts) == 1:
            return parts[0]
        return parts

    def close(self) -> None:
        if self._loop is None:
            return
        try:

            async def _shutdown() -> None:
                if self._session is not None:
                    await self._session.__aexit__(None, None, None)
                if self._cm is not None:
                    await self._cm.__aexit__(None, None, None)

            self._loop.run_until_complete(_shutdown())
        except Exception:
            pass
        finally:
            self._loop.close()
            self._loop = None
            self._session = None
            self._cm = None


def default_session_factory(config: MCPServerConfig, env: dict[str, str]) -> MCPSession:
    return SdkMCPSession(config, env)


class MCPClient:
    """Manages configured MCP servers and optional ToolRegistry registration."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        secret_resolver: SecretResolver | None = None,
        registry: ToolRegistry | None = None,
        session_factory: SessionFactory | None = None,
        clock: Clock | None = None,
        default_risk: str = "R1",
    ) -> None:
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self.secret_resolver = secret_resolver
        self.registry = registry
        self.session_factory = session_factory or default_session_factory
        self.clock = clock or time.monotonic
        self.default_risk = default_risk
        self._servers: dict[str, MCPServerConfig] = {}
        self._conns: dict[str, _ConnState] = {}

    @property
    def config_path(self) -> Path:
        return self.workspace / CONFIG_REL

    def load_config(self, path: Path | None = None) -> dict[str, MCPServerConfig]:
        p = path or self.config_path
        self._servers = load_servers_config(p)
        return dict(self._servers)

    def set_server(self, config: MCPServerConfig) -> None:
        config.validate()
        self._servers[config.name] = config

    def server_names(self) -> list[str]:
        return sorted(self._servers)

    def ensure_connected(self, name: str) -> list[DiscoveredTool]:
        cfg = self._servers.get(name)
        if cfg is None:
            raise MCPError(f"unknown MCP server: {name}")
        st = self._conns.get(name)
        if st and st.connected and st.session is not None:
            st.last_used = self.clock()
            return list(st.tools)
        return self._start(name, cfg, restart_count=st.restarts if st else 0)

    def _start(
        self, name: str, cfg: MCPServerConfig, *, restart_count: int
    ) -> list[DiscoveredTool]:
        env = resolve_env(cfg.env, resolver=self.secret_resolver)
        session = self.session_factory(cfg, env)
        try:
            session.initialize()
            tools = session.list_tools()
            for t in tools:
                t.server = name
        except Exception as exc:
            try:
                session.close()
            except Exception:
                pass
            raise MCPError(f"failed to start MCP server {name}: {exc}") from exc

        self._conns[name] = _ConnState(
            config=cfg,
            session=session,
            tools=tools,
            last_used=self.clock(),
            restarts=restart_count,
            connected=True,
        )
        self._register_tools(name, tools)
        return list(tools)

    def _register_tools(self, server: str, tools: list[DiscoveredTool]) -> None:
        if self.registry is None:
            return
        for t in tools:
            # Drop previous registration for same qualified name
            qn = t.qualified_name

            def _make_handler(srv: str, tool_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
                def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
                    result = self.call_tool(srv, tool_name, arguments)
                    return result.to_dict()

                return _handler

            self.registry.register(
                ToolSpec(
                    name=qn,
                    provider="mcp",
                    provider_ref=server,
                    description=sanitize_description(t.description or f"MCP tool {qn}"),
                    input_schema=dict(t.input_schema),
                    risk=self.default_risk,
                    privacy={"egress": "network", "sends_user_content": True},
                    category="mcp",
                    meta={"mcp_server": server, "mcp_tool": t.name},
                ),
                handler=_make_handler(server, t.name),
                trust_description=False,
            )

    def list_tools(self, name: str | None = None) -> list[DiscoveredTool]:
        if name:
            return list(self.ensure_connected(name))
        out: list[DiscoveredTool] = []
        for n in self.server_names():
            st = self._conns.get(n)
            if st and st.connected:
                out.extend(st.tools)
        return out

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPCallResult:
        args = dict(arguments or {})
        restarted = False
        last_err: Exception | None = None
        for attempt in range(MAX_RESTARTS + 1):
            try:
                tools = self.ensure_connected(server)
                st = self._conns[server]
                assert st.session is not None
                # Validate tool exists after discovery
                known = {t.name for t in tools}
                if tool not in known and attempt == 0:
                    # Still allow call — some servers omit dynamic tools
                    pass
                raw = st.session.call_tool(tool, args)
                st.last_used = self.clock()
                return MCPCallResult(
                    ok=True,
                    server=server,
                    tool=tool,
                    content=raw,
                    untrusted=True,
                    restarted=restarted,
                )
            except Exception as exc:
                last_err = exc
                st = self._conns.get(server)
                max_r = st.config.max_restarts if st else MAX_RESTARTS
                if attempt >= max_r:
                    break
                # Crash / disconnect → close and restart
                self._force_close(server, keep_restart_count=True)
                if server in self._conns:
                    self._conns[server].restarts += 1
                else:
                    cfg = self._servers[server]
                    self._conns[server] = _ConnState(config=cfg, restarts=1)
                restarted = True
        return MCPCallResult(
            ok=False,
            server=server,
            tool=tool,
            content=None,
            error=str(last_err) if last_err else "call failed",
            restarted=restarted,
        )

    def close(self, name: str) -> None:
        self._force_close(name, keep_restart_count=False)

    def _force_close(self, name: str, *, keep_restart_count: bool) -> None:
        st = self._conns.get(name)
        if not st:
            return
        if st.session is not None:
            try:
                st.session.close()
            except Exception:
                pass
        restarts = st.restarts if keep_restart_count else 0
        self._conns[name] = _ConnState(
            config=st.config,
            session=None,
            tools=[],
            last_used=0.0,
            restarts=restarts,
            connected=False,
        )
        # Disable mcp.* tools for this server in registry (keep native mcp_list/call)
        if self.registry is not None:
            for spec in list(self.registry.list_tools()):
                if spec.provider == "mcp" and spec.provider_ref == name:
                    self.registry.set_enabled(spec.name, False)

    def reap_idle(self) -> list[str]:
        """Close connections idle past their timeout. Returns closed server names."""
        now = self.clock()
        closed: list[str] = []
        for name, st in list(self._conns.items()):
            if not st.connected or st.session is None:
                continue
            idle = now - st.last_used
            if idle >= st.config.idle_timeout_s:
                self.close(name)
                closed.append(name)
        return closed

    def is_connected(self, name: str) -> bool:
        st = self._conns.get(name)
        return bool(st and st.connected and st.session is not None)

    def restart_count(self, name: str) -> int:
        st = self._conns.get(name)
        return st.restarts if st else 0

    def shutdown(self) -> None:
        for name in list(self._conns):
            self.close(name)

    def bind_native_mcp_tools(self, registry: ToolRegistry | None = None) -> None:
        """Wire ``mcp_list`` / ``mcp_call`` native stubs to this client."""
        reg = registry or self.registry
        if reg is None:
            raise MCPError("no registry")
        self.registry = reg

        def _list(arguments: dict[str, Any]) -> dict[str, Any]:
            server = arguments.get("server")
            if server:
                tools = self.ensure_connected(str(server))
            else:
                # Discover configured servers lazily only if already connected
                tools = self.list_tools()
            return {
                "ok": True,
                "tools": [t.to_dict() for t in tools],
                "servers": self.server_names(),
            }

        def _call(arguments: dict[str, Any]) -> dict[str, Any]:
            server = str(arguments.get("server") or "")
            tool = str(arguments.get("tool") or "")
            args = arguments.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            return self.call_tool(server, tool, args).to_dict()

        for name, handler in (("mcp_list", _list), ("mcp_call", _call)):
            spec = reg.get(name)
            if spec is None:
                continue
            reg.register(spec, handler=handler)
