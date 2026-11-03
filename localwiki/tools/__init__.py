"""Unified tool layer (design §12)."""

from localwiki.tools.mcp_client import MCPClient, MCPServerConfig
from localwiki.tools.registry import (
    NATIVE_TOOL_COUNT,
    NATIVE_TOOL_NAMES,
    ToolRegistry,
    ToolSpec,
    build_default_registry,
)
from localwiki.tools.router import ToolRouter, ToolSelectResult

__all__ = [
    "MCPClient",
    "MCPServerConfig",
    "NATIVE_TOOL_COUNT",
    "NATIVE_TOOL_NAMES",
    "ToolRegistry",
    "ToolRouter",
    "ToolSelectResult",
    "ToolSpec",
    "build_default_registry",
]
