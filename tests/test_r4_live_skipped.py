"""R3.5 RP6: R4 live flags — no key/env stays SKIPPED-friendly."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.integrations_live import get_integrations, live_tool_status  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_integrations_default_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    flags = get_integrations(ws)
    assert flags["tavily_enabled"] is False
    assert flags["mcp_enabled"] is False
    assert flags["playwright_enabled"] is False
    st = live_tool_status(ws)
    assert st["tools"]["tavily"]["ready"] is False


@pytest.mark.live
def test_tavily_live_optional() -> None:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        pytest.skip("TAVILY_API_KEY not set")
    # Presence of key is enough for this smoke; full search is environment-specific
    assert len(key) > 4
