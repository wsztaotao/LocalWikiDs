"""R4 live integrations switches — optional; never block personal core."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from localwiki.workspace import load_config, workspace_paths


DEFAULT_INTEGRATIONS: dict[str, bool] = {
    "tavily_enabled": False,
    "mcp_enabled": False,
    "playwright_enabled": False,
}


def get_integrations(workspace: Path | str) -> dict[str, Any]:
    root = Path(workspace).resolve()
    cfg = load_config(workspace_paths(root)["config"])
    raw = cfg.get("integrations") if isinstance(cfg.get("integrations"), dict) else {}
    out = dict(DEFAULT_INTEGRATIONS)
    for k in DEFAULT_INTEGRATIONS:
        if k in raw:
            out[k] = bool(raw[k])
    return out


def live_tool_status(workspace: Path | str) -> dict[str, Any]:
    """Honest readiness for Tavily / MCP / Playwright (no network calls)."""
    flags = get_integrations(workspace)
    status: dict[str, Any] = {"integrations": flags, "tools": {}}

    # Tavily
    tavily_ready = False
    tavily_reason = "disabled"
    if flags["tavily_enabled"]:
        try:
            import tavily  # noqa: F401

            tavily_ready = True
            tavily_reason = "sdk_import_ok"
        except Exception as exc:  # noqa: BLE001
            tavily_reason = f"sdk_missing:{exc}"
    status["tools"]["tavily"] = {"ready": tavily_ready, "reason": tavily_reason}

    # MCP
    mcp_ready = False
    mcp_reason = "disabled"
    if flags["mcp_enabled"]:
        try:
            from localwiki.tools import mcp_client  # noqa: F401

            mcp_ready = True
            mcp_reason = "module_ok"
        except Exception as exc:  # noqa: BLE001
            mcp_reason = f"import:{exc}"
    status["tools"]["mcp"] = {"ready": mcp_ready, "reason": mcp_reason}

    # Playwright
    pw_ready = False
    pw_reason = "disabled"
    if flags["playwright_enabled"]:
        try:
            import playwright  # noqa: F401

            pw_ready = True
            pw_reason = "sdk_import_ok"
        except Exception as exc:  # noqa: BLE001
            pw_reason = f"sdk_missing:{exc}"
    status["tools"]["playwright"] = {"ready": pw_ready, "reason": pw_reason}

    return status
