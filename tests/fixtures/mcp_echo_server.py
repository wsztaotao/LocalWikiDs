"""Minimal stdio MCP server for LW-26 integration tests.

Run: python tests/fixtures/mcp_echo_server.py
Exposes tool ``echo`` that returns the input text.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any


async def main() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    server = Server("localwiki-echo")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="echo",
                description="Echo text back",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != "echo":
            raise ValueError(f"unknown tool: {name}")
        text = str(arguments.get("text", ""))
        return [TextContent(type="text", text=text)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
