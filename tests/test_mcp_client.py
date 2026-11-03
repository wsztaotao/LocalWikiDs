"""Tests for MCP Client lifecycle (LW-26 / §12.2 / AT-TOOL-03)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.secrets import open_secret_store  # noqa: E402
from localwiki.tools.mcp_client import (  # noqa: E402
    DiscoveredTool,
    FakeMCPSession,
    MCPClient,
    MCPError,
    MCPServerConfig,
    load_servers_config,
    resolve_env,
    write_default_servers_config,
)
from localwiki.tools.registry import build_default_registry  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _fake_factory(
    tools: list[DiscoveredTool] | None = None,
    **kwargs,
):
    sessions: list[FakeMCPSession] = []

    def factory(config: MCPServerConfig, env: dict[str, str]) -> FakeMCPSession:
        s = FakeMCPSession(
            tools=tools
            or [
                DiscoveredTool(
                    server=config.name,
                    name="echo",
                    description="echo",
                    input_schema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                )
            ],
            server=config.name,
            **kwargs,
        )
        sessions.append(s)
        return s

    factory.sessions = sessions  # type: ignore[attr-defined]
    return factory


def test_reject_latest_in_config(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps(
            {
                "bad": {
                    "command": "npx",
                    "args": ["@playwright/mcp@latest"],
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(MCPError, match="@latest"):
        load_servers_config(path)


def test_write_default_config_pinned(tmp_path: Path) -> None:
    path = tmp_path / ".kw" / "runtime" / "mcp" / "servers.json"
    write_default_servers_config(path)
    text = path.read_text(encoding="utf-8")
    assert "@latest" not in text
    assert "@playwright/mcp@0.0.83" in text
    assert "secret:tavily" in text
    cfgs = load_servers_config(path)
    assert "playwright" in cfgs and "tavily" in cfgs


def test_secret_env_resolved_not_persisted(tmp_path: Path) -> None:
    store = open_secret_store(force_memory=True)
    store.set("tavily", "tvly-SECRET-VALUE")
    env = resolve_env(
        {"TAVILY_API_KEY": "secret:tavily", "FOO": "bar"},
        resolver=store.resolve,
        base_env={"PATH": "/bin"},
    )
    assert env["TAVILY_API_KEY"] == "tvly-SECRET-VALUE"
    assert env["FOO"] == "bar"
    # Config file must still only have the ref
    path = tmp_path / "servers.json"
    write_default_servers_config(path)
    assert "tvly-SECRET-VALUE" not in path.read_text(encoding="utf-8")


def test_connect_discover_call_register() -> None:
    clock = _Clock()
    factory = _fake_factory()
    reg = build_default_registry()
    client = MCPClient(
        session_factory=factory,
        registry=reg,
        clock=clock,
    )
    client.set_server(
        MCPServerConfig(name="demo", command="fake", args=[], idle_timeout_s=30)
    )
    tools = client.ensure_connected("demo")
    assert len(tools) == 1
    assert tools[0].qualified_name == "mcp.demo.echo"
    assert client.is_connected("demo")
    assert reg.get("mcp.demo.echo") is not None

    result = client.call_tool("demo", "echo", {"text": "hi"})
    assert result.ok
    assert result.untrusted
    assert "<untrusted>" in (result.to_dict().get("wrapped") or "")
    assert result.content == {"echo": {"text": "hi"}}


def test_idle_timeout_closes() -> None:
    clock = _Clock()
    factory = _fake_factory()
    client = MCPClient(session_factory=factory, clock=clock)
    client.set_server(
        MCPServerConfig(name="demo", command="fake", idle_timeout_s=10)
    )
    client.ensure_connected("demo")
    assert client.is_connected("demo")
    clock.advance(9)
    assert client.reap_idle() == []
    assert client.is_connected("demo")
    clock.advance(2)
    assert client.reap_idle() == ["demo"]
    assert not client.is_connected("demo")
    assert factory.sessions[0].closed


def test_crash_restart_max_two() -> None:
    """Simulated crash on every call → restart twice then fail (AT-TOOL-03)."""
    sessions: list[FakeMCPSession] = []

    def factory(config: MCPServerConfig, env: dict[str, str]) -> FakeMCPSession:
        # Each new session crashes immediately on call
        s = FakeMCPSession(
            server=config.name,
            crash_after_calls=0,  # first call raises
        )
        # Override: always crash
        def boom(name: str, arguments: dict) -> None:
            raise MCPError("simulated crash")

        s.call_tool = boom  # type: ignore[method-assign]
        sessions.append(s)
        return s

    client = MCPClient(session_factory=factory, clock=_Clock())
    client.set_server(
        MCPServerConfig(name="flaky", command="fake", max_restarts=2)
    )
    result = client.call_tool("flaky", "echo", {"text": "x"})
    assert result.ok is False
    assert result.restarted is True
    # initial + 2 restarts = 3 session creations
    assert len(sessions) == 3
    assert client.restart_count("flaky") == 2


def test_crash_then_recover() -> None:
    """First session crashes once; restart succeeds."""
    n = {"i": 0}

    def factory(config: MCPServerConfig, env: dict[str, str]) -> FakeMCPSession:
        n["i"] += 1
        if n["i"] == 1:
            s = FakeMCPSession(server=config.name)

            def boom(name: str, arguments: dict):
                raise MCPError("crash")

            s.call_tool = boom  # type: ignore[method-assign]
            return s
        return FakeMCPSession(
            server=config.name,
            call_map={"echo": {"ok": True, "text": "recovered"}},
        )

    client = MCPClient(session_factory=factory, clock=_Clock())
    client.set_server(MCPServerConfig(name="ok", command="fake"))
    result = client.call_tool("ok", "echo", {"text": "x"})
    assert result.ok
    assert result.restarted
    assert result.content == {"ok": True, "text": "recovered"}


def test_bind_native_mcp_list_call() -> None:
    factory = _fake_factory()
    reg = build_default_registry()
    client = MCPClient(session_factory=factory, registry=reg, clock=_Clock())
    client.set_server(MCPServerConfig(name="demo", command="fake"))
    client.bind_native_mcp_tools()
    listed = reg.execute("mcp_list", {"server": "demo"})
    assert listed.ok
    assert any(t["name"] == "echo" for t in listed.output["tools"])
    called = reg.execute(
        "mcp_call", {"server": "demo", "tool": "echo", "arguments": {"text": "z"}}
    )
    assert called.ok
    assert called.output["ok"] is True


def test_stdio_echo_server_live() -> None:
    """Real stdio round-trip via fixtures/mcp_echo_server.py (needs mcp SDK)."""
    echo = ROOT / "tests" / "fixtures" / "mcp_echo_server.py"
    assert echo.is_file()
    py = ROOT / ".venv-work" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)

    # Client-side SDK must be importable (work venv preferred)
    try:
        import mcp  # noqa: F401
    except ImportError:
        site = ROOT / ".venv-work" / "Lib" / "site-packages"
        if site.is_dir() and str(site) not in sys.path:
            sys.path.insert(0, str(site))
        try:
            import mcp  # noqa: F401
        except ImportError:
            pytest.skip("mcp package not installed (install requirements-work.txt)")

    client = MCPClient(clock=_Clock())
    client.set_server(
        MCPServerConfig(
            name="echo",
            command=str(py),
            args=[str(echo)],
            idle_timeout_s=60,
        )
    )
    try:
        tools = client.ensure_connected("echo")
        names = {t.name for t in tools}
        assert "echo" in names
        result = client.call_tool("echo", "echo", {"text": "ping"})
        assert result.ok
        content = result.content
        if isinstance(content, list):
            assert any("ping" in str(c) for c in content)
        else:
            assert "ping" in str(content)
    finally:
        client.shutdown()
