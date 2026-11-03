"""Skill runtime: progressive load, MCP registration, integrity (§13.6–13.8)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from localwiki.skills_hub.registry import SkillRecord, SkillRegistry
from localwiki.tools.mcp_client import MCPClient, MCPServerConfig
from localwiki.tools.registry import ToolRegistry, ToolSpec


@dataclass
class SkillPromptEntry:
    name: str
    description: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "kind": self.kind}


def list_prompt_entries(registry: SkillRegistry) -> list[SkillPromptEntry]:
    """Progressive disclosure: name + description only (AT-SKL-07)."""
    out: list[SkillPromptEntry] = []
    for rec in registry.list(enabled_only=True):
        desc = ""
        if rec.install_path:
            skill_md = Path(rec.install_path) / "SKILL.md"
            if skill_md.is_file():
                text = skill_md.read_text(encoding="utf-8", errors="replace")
                if text.startswith("---"):
                    end = text.find("---", 3)
                    fm = text[3:end] if end > 0 else ""
                    for line in fm.splitlines():
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("\"'")
                if not desc:
                    # first non-empty body line
                    body = text.split("---", 2)[-1] if text.startswith("---") else text
                    for line in body.splitlines():
                        if line.strip():
                            desc = line.strip()[:200]
                            break
        out.append(SkillPromptEntry(name=rec.name, description=desc or rec.kind, kind=rec.kind))
    return out


def read_skill_body(registry: SkillRegistry, name: str) -> dict[str, Any]:
    """Full SKILL.md wrapped as untrusted skill instructions."""
    rec = registry.get(name)
    if not rec or not rec.enabled:
        return {"ok": False, "error": "skill_unavailable"}
    path = Path(rec.install_path or "") / "SKILL.md"
    if not path.is_file():
        return {"ok": False, "error": "skill_md_missing"}
    text = path.read_text(encoding="utf-8", errors="replace")
    wrapped = (
        f'<skill_instructions source="{rec.name}" trust="untrusted">\n'
        f"{text}\n"
        f"</skill_instructions>"
    )
    return {
        "ok": True,
        "name": rec.name,
        "wrapped": wrapped,
        "untrusted": True,
        "note": "cannot change approval/risk/privacy",
    }


def startup_integrity_check(workspace: Path, registry: SkillRegistry | None = None) -> list[dict[str, Any]]:
    """On startup: tamper → auto-disable (AT-SKL-06)."""
    reg = registry or SkillRegistry(workspace)
    tampered = reg.verify_integrity()
    return [t.to_dict() for t in tampered]


def register_mcp_skill(
    rec: SkillRecord,
    mcp: MCPClient,
    tool_registry: ToolRegistry | None = None,
) -> list[str]:
    """Wire an installed MCP-server skill into MCP Client + ToolRegistry (AT-SKL-08)."""
    if rec.kind != "mcp_server" or not rec.enabled:
        return []
    root = Path(rec.install_path or "")
    # Prefer package.json bin or python -m
    command = "node"
    args: list[str] = []
    pkg = root / "package.json"
    entry = root / "server.py"
    if entry.is_file():
        command = "python"
        args = [str(entry)]
    elif pkg.is_file():
        command = "npx"
        # pinned local path — no @latest
        args = ["--no-install", str(root)]
    else:
        return []
    cfg = MCPServerConfig(
        name=f"skill_{rec.name}",
        command=command,
        args=args,
        idle_timeout_s=600,
    )
    mcp.set_server(cfg)
    if tool_registry is not None:
        mcp.registry = tool_registry
    tools = mcp.ensure_connected(cfg.name)
    return [t.qualified_name for t in tools]


def bind_skill_agent_tools(
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
    *,
    search_fn: Any | None = None,
) -> None:
    """Register skill_search / skill_read / skill_install_request (§13.8)."""

    def _search(arguments: dict[str, Any]) -> dict[str, Any]:
        q = str(arguments.get("query") or "")
        if search_fn:
            result = search_fn(q)
            return result.to_dict() if hasattr(result, "to_dict") else dict(result)
        # Local installed-only fallback
        matches = [
            e.to_dict()
            for e in list_prompt_entries(skill_registry)
            if q.lower() in e.name.lower() or q.lower() in e.description.lower()
        ]
        return {"ok": True, "results": matches}

    def _read(arguments: dict[str, Any]) -> dict[str, Any]:
        return read_skill_body(skill_registry, str(arguments.get("name") or ""))

    def _install_request(arguments: dict[str, Any]) -> dict[str, Any]:
        # Agents may only request — never install (AT-SKL-09 / R4)
        return {
            "ok": True,
            "queued": True,
            "risk": "R4",
            "request": {
                "name": arguments.get("name"),
                "source": arguments.get("source"),
                "reason": arguments.get("reason"),
            },
            "message": "install requires user confirmation in UI; agent cannot self-install",
        }

    specs = [
        ToolSpec(
            name="skill_search",
            provider="skills_hub",
            description="Search skills by keyword",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            risk="R1",
            category="skills",
        ),
        ToolSpec(
            name="skill_read",
            provider="skills_hub",
            description="Read installed SKILL.md (untrusted wrap)",
            input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            risk="R0",
            category="skills",
        ),
        ToolSpec(
            name="skill_install_request",
            provider="skills_hub",
            description="Queue skill install request for user confirmation",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source": {"type": "object"},
                    "reason": {"type": "string"},
                },
                "required": ["name"],
            },
            risk="R3",  # R4 not in native enum; treat as highest available R3 + meta
            category="skills",
            meta={"design_risk": "R4", "agent_cannot_install": True},
        ),
    ]
    handlers = {
        "skill_search": _search,
        "skill_read": _read,
        "skill_install_request": _install_request,
    }
    for spec in specs:
        tool_registry.register(spec, handler=handlers[spec.name])
