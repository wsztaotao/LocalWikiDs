"""Tests for Playwright-MCP adapter (LW-28 / §12.4 / AT-TOOL-06)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.tools.playwright import (  # noqa: E402
    PLAYWRIGHT_MCP_VERSION,
    PlaywrightAdapter,
    base_risk_for_tool,
    check_domain,
    extract_host,
    looks_like_submit,
    resolve_risk,
)
from localwiki.tools.registry import build_default_registry  # noqa: E402


def test_risk_mapping_table() -> None:
    assert base_risk_for_tool("browser_navigate") == "R1"
    assert base_risk_for_tool("browser_snapshot") == "R1"
    assert base_risk_for_tool("browser_screenshot") == "R1"
    assert base_risk_for_tool("browser_click") == "R2"
    assert base_risk_for_tool("browser_fill") == "R2"
    assert base_risk_for_tool("browser_evaluate") == "R3"
    assert base_risk_for_tool("browser_file_upload") == "R3"


def test_submit_keyword_escalates_to_r3() -> None:
    assert looks_like_submit("提交订单")
    assert looks_like_submit("Pay now")
    assert looks_like_submit("Delete account")
    assert not looks_like_submit("Next page")
    assert resolve_risk("browser_click", {"element": "Submit payment"}) == "R3"
    assert resolve_risk("browser_click", {"element": "Learn more"}) == "R2"
    assert resolve_risk("browser_click", {"element": "Go", "url": "https://x.com/checkout"}) == "R3"


def test_domain_allowlist_blocks_and_approval() -> None:
    d = check_domain("https://evil.example/x", ["allowed.com"])
    assert d.allowed is False
    assert d.needs_approval is True
    assert d.host == "evil.example"

    ok = check_domain("https://docs.allowed.com/a", ["allowed.com"])
    assert ok.allowed is True

    assert extract_host("https://Foo.Bar/") == "foo.bar"

    adapter = PlaywrightAdapter(
        allowlist=["allowed.com"],
        call_fn=lambda s, t, a: {"ok": True, "content": "snap"},
    )
    blocked = adapter.call("browser_open", {"url": "https://other.com"})
    assert blocked["ok"] is False
    assert blocked["needs_approval"] is True

    adapter.approve_host("other.com")
    allowed = adapter.call("browser_open", {"url": "https://other.com/path"})
    assert allowed["ok"] is True
    assert allowed["risk"] == "R1"
    assert allowed["mapped_tool"] == "browser_navigate"
    assert "<untrusted>" in allowed["wrapped"]


def test_offline_local_only() -> None:
    remote = check_domain(
        "https://example.com", ["example.com"], offline_local_only=True
    )
    assert remote.allowed is False
    assert remote.reason == "offline_blocks_remote"
    local = check_domain(
        "http://127.0.0.1:8080", ["example.com"], offline_local_only=True
    )
    assert local.allowed is True


def test_server_config_pinned_no_latest() -> None:
    adapter = PlaywrightAdapter(allowlist=["example.com"], task_dir=Path("task1"))
    cfg = adapter.ensure_server_config()
    assert f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}" in cfg.args
    assert "--isolated" in cfg.args
    assert "@latest" not in " ".join(cfg.args)
    assert any("allowed-origins" in a or a.startswith("https://") for a in cfg.args)


def test_bind_browser_aliases() -> None:
    calls: list[tuple] = []

    def call_fn(server: str, tool: str, args: dict) -> dict:
        calls.append((server, tool, args))
        return {"ok": True, "content": {"snapshot": "ok"}}

    adapter = PlaywrightAdapter(
        allowlist=["example.com"],
        call_fn=call_fn,
    )
    reg = build_default_registry()
    adapter.bind_browser_aliases(reg)
    r = reg.execute("browser_open", {"url": "https://example.com"})
    assert r.ok
    assert calls[0][1] == "browser_navigate"
    # Click submit escalates — still executes but risk R3 in output
    r2 = reg.execute(
        "browser_click",
        {"element": "提交", "url": "https://example.com/form"},
    )
    assert r2.ok
    assert r2.output["risk"] == "R3"
