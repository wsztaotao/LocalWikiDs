"""Tests for Composio adapter (LW-29 / §12.5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.tools.composio_adapter import (  # noqa: E402
    ComposioAdapter,
    ComposioError,
    FakeComposioSdk,
    classify_tool_risk,
    check_composio_privacy,
)
from localwiki.tools.registry import build_default_registry  # noqa: E402


def test_risk_verbs() -> None:
    assert classify_tool_risk("GITHUB_LIST_REPOS") == "R1"
    assert classify_tool_risk("GITHUB_GET_REPO") == "R1"
    assert classify_tool_risk("GITHUB_SEARCH_ISSUES") == "R1"
    assert classify_tool_risk("GITHUB_CREATE_AN_ISSUE") == "R3"
    assert classify_tool_risk("GMAIL_SEND_EMAIL") == "R3"
    assert classify_tool_risk("UNKNOWN_WIDGET") == "R3"
    assert classify_tool_risk("GITHUB_LIST_REPOS", override="R0") == "R0"


def test_privacy_and_offline() -> None:
    assert check_composio_privacy(content_class="normal")[0]
    assert not check_composio_privacy(content_class="private")[0]
    assert check_composio_privacy(
        content_class="private", allow_private_params=True
    )[0]
    assert not check_composio_privacy(content_class="sensitive")[0]
    assert check_composio_privacy(offline=True) == (False, "offline_mode")


def test_authorize_register_execute_receipt(tmp_path: Path) -> None:
    adapter = ComposioAdapter(
        api_key="k",
        user_id="u1",
        sdk_factory=lambda k: FakeComposioSdk(k),
    )
    conn = adapter.authorize("github")
    assert conn.status == "connected"
    assert conn.redirect_url and "github" in conn.redirect_url
    assert "token" not in json.dumps(conn.to_dict()).lower()

    reg = build_default_registry()
    names = adapter.register_connected_tools(reg, "github")
    assert "composio.GITHUB_LIST_REPOS" in names
    assert "composio.GITHUB_CREATE_AN_ISSUE" in names
    assert reg.get("composio.GITHUB_LIST_REPOS").risk == "R1"
    assert reg.get("composio.GITHUB_CREATE_AN_ISSUE").risk == "R3"

    read = adapter.execute("GITHUB_LIST_REPOS", {})
    assert read["ok"]
    assert read["risk"] == "R1"
    assert read.get("receipt") is None

    write = adapter.execute(
        "GITHUB_CREATE_AN_ISSUE",
        {
            "owner": "o",
            "repo": "r",
            "title": "t",
            "idempotency_key": "idem-1",
        },
    )
    assert write["ok"]
    assert write["risk"] == "R3"
    assert write["receipt"]["status"] == "ok"
    assert write["receipt"]["resource_id"] == "res_123"
    assert write["receipt"]["request_hash"]

    # Dedup
    again = adapter.execute(
        "GITHUB_CREATE_AN_ISSUE",
        {"owner": "o", "repo": "r", "title": "t", "idempotency_key": "idem-1"},
    )
    assert again.get("deduped") is True

    path = tmp_path / "connections.json"
    adapter.save_connections(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["connections"][0]["toolkit"] == "github"


def test_offline_blocks_execute() -> None:
    adapter = ComposioAdapter(
        api_key="k",
        offline=True,
        sdk_factory=lambda k: FakeComposioSdk(k),
    )
    out = adapter.execute("GITHUB_LIST_REPOS", {})
    assert out["ok"] is False
    assert out["error"] == "offline_mode"


def test_private_params_blocked() -> None:
    adapter = ComposioAdapter(
        api_key="k",
        content_class="private",
        allow_private_params=False,
        sdk_factory=lambda k: FakeComposioSdk(k),
    )
    out = adapter.execute("GITHUB_LIST_REPOS", {})
    assert out["ok"] is False
    assert "privacy" in out["error"]


def test_bind_integration_tools() -> None:
    adapter = ComposioAdapter(
        api_key="k",
        sdk_factory=lambda k: FakeComposioSdk(k),
    )
    adapter.authorize("github")
    reg = build_default_registry()
    adapter.register_connected_tools(reg, "github")
    adapter.bind_integration_aliases(reg)
    listed = reg.execute("integration_list", {})
    assert listed.ok
    # write via integration_call rejected
    bad = reg.execute(
        "integration_call",
        {"slug": "GITHUB_CREATE_AN_ISSUE", "arguments": {"title": "x"}},
    )
    assert bad.output["ok"] is False
    good = reg.execute(
        "integration_write",
        {
            "slug": "GITHUB_CREATE_AN_ISSUE",
            "arguments": {"owner": "o", "repo": "r", "title": "x"},
            "idempotency_key": "k2",
        },
    )
    assert good.output["ok"] is True
    assert good.output["receipt"]


def test_not_configured() -> None:
    adapter = ComposioAdapter(api_key=None)
    st = adapter.status()
    assert st["available"] is False
    assert "not_configured" in (st["hint"] or "")
    with pytest.raises(ComposioError):
        adapter.authorize("github")


@pytest.mark.live
def test_live_composio_skipped_without_key() -> None:
    import os

    key = os.environ.get("COMPOSIO_API_KEY") or os.environ.get("LOCALWIKI_COMPOSIO_KEY")
    if not key:
        pytest.skip("SKIPPED/@live: no Composio API key")
    try:
        from composio import Composio
    except ImportError:
        site = ROOT / ".venv-work" / "Lib" / "site-packages"
        if site.is_dir():
            sys.path.insert(0, str(site))
        from composio import Composio

    client = Composio(api_key=key)
    assert client is not None
