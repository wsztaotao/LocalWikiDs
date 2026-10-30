"""ToolRegistry and ToolSpec (design §12.1).

Registers v0.11's 46 native tools with stable names/risks. Handlers are
scaffolded stubs (no v0.11 source); contract tests lock the catalog.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

# Prompt-injection-ish patterns in untrusted descriptions
_INJECTION_PAT = re.compile(
    r"(ignore\s+previous|system\s*prompt|你是|忽略以上|do\s+not\s+follow)",
    re.I,
)

MAX_DESCRIPTION_CHARS = 500

# Exact v0.11 agent tool list (46) — names must stay stable
_NATIVE_DEFS: list[tuple[str, str, str, dict[str, Any]]] = [
    # name, category, risk, extra
    ("memory_search", "knowledge_memory", "R0", {}),
    ("persona_read", "knowledge_memory", "R0", {}),
    ("memory_propose", "knowledge_memory", "R0", {"proposal": True}),
    ("oauth_list", "third_party", "R1", {}),
    ("oauth_search", "third_party", "R1", {}),
    ("oauth_read_mail", "third_party", "R1", {"egress": "network"}),
    ("cloud_session_start", "cloud", "R2", {}),
    ("cloud_session_status", "cloud", "R2", {}),
    ("cloud_session_stop", "cloud", "R2", {}),
    ("file_extract", "workspace", "R0", {}),
    ("workspace_replace", "workspace", "R2", {}),
    ("xlsx_create", "artifact", "R2", {}),
    ("artifact_inspect", "workspace", "R0", {}),
    ("docx_create", "artifact", "R2", {}),
    ("pptx_create", "artifact", "R2", {}),
    ("pdf_create", "artifact", "R2", {}),
    ("diagram_create", "artifact", "R2", {}),
    ("image_generate", "artifact", "R2", {"egress": "network"}),
    ("web_search", "network", "R1", {"egress": "network"}),
    ("web_fetch", "network", "R1", {"egress": "network"}),
    ("win_command", "shell", "R3", {}),
    ("plan_set", "plan", "R0", {}),
    ("plan_update", "plan", "R0", {}),
    ("knowledge_search", "knowledge_memory", "R0", {}),
    ("knowledge_read", "knowledge_memory", "R0", {}),
    ("workspace_list", "workspace", "R0", {}),
    ("workspace_read", "workspace", "R0", {}),
    ("workspace_write", "workspace", "R2", {}),
    ("browser_open", "browser", "R1", {"egress": "network"}),
    ("browser_inspect", "browser", "R1", {"egress": "network"}),
    ("browser_click", "browser", "R2", {"egress": "network"}),
    ("browser_fill", "browser", "R2", {"egress": "network"}),
    ("browser_screenshot", "browser", "R2", {"egress": "network"}),
    ("computer_screenshot", "cloud", "R3", {}),
    ("computer_click", "cloud", "R3", {}),
    ("computer_type", "cloud", "R3", {}),
    ("computer_hotkey", "cloud", "R3", {}),
    ("computer_scroll", "cloud", "R3", {}),
    ("computer_open_url", "cloud", "R3", {"egress": "network"}),
    ("computer_launch", "cloud", "R3", {}),
    ("computer_clipboard", "cloud", "R3", {}),
    ("integration_list", "third_party", "R1", {}),
    ("integration_call", "third_party", "R1", {"egress": "network"}),
    ("integration_write", "third_party", "R3", {"egress": "network"}),
    ("mcp_list", "mcp", "R0", {}),
    ("mcp_call", "mcp", "R1", {}),
]

NATIVE_TOOL_NAMES: tuple[str, ...] = tuple(n for n, *_ in _NATIVE_DEFS)
NATIVE_TOOL_COUNT = len(NATIVE_TOOL_NAMES)
assert NATIVE_TOOL_COUNT == 46, NATIVE_TOOL_COUNT

# v0.12 aliases (not counted in the 46)
_ALIASES: dict[str, str] = {
    "shell_exec": "win_command",
}

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolHandler(Protocol):
    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class ToolSpec:
    name: str
    provider: str = "native"
    provider_ref: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    risk: str = "R0"
    risk_rules: list[Any] = field(default_factory=list)
    privacy: dict[str, Any] = field(default_factory=dict)
    timeout_s: int = 60
    max_output_chars: int = 8000
    requires: list[str] = field(default_factory=list)
    audit: bool = True
    enabled: bool = True
    category: str = "general"
    aliases: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "provider_ref": self.provider_ref,
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
            "risk": self.risk,
            "risk_rules": list(self.risk_rules),
            "privacy": dict(self.privacy),
            "timeout_s": self.timeout_s,
            "max_output_chars": self.max_output_chars,
            "requires": list(self.requires),
            "audit": self.audit,
            "enabled": self.enabled,
            "category": self.category,
            "aliases": list(self.aliases),
        }


def sanitize_description(text: str, *, max_chars: int = MAX_DESCRIPTION_CHARS) -> str:
    t = (text or "").strip()
    if _INJECTION_PAT.search(t):
        t = "[description redacted: injection-like content]"
    if len(t) > max_chars:
        t = t[: max_chars - 1] + "…"
    return t


def _default_schema(name: str) -> dict[str, Any]:
    # Minimal object schema; specific adapters refine later
    props: dict[str, Any] = {}
    required: list[str] = []
    if name in ("web_search", "knowledge_search", "memory_search", "oauth_search"):
        props["query"] = {"type": "string"}
        required = ["query"]
    elif name in ("knowledge_read", "workspace_read", "persona_read"):
        props["ref"] = {"type": "string"}
        required = ["ref"]
    elif name in ("web_fetch", "browser_open", "computer_open_url"):
        props["url"] = {"type": "string"}
        required = ["url"]
    elif name in ("win_command", "shell_exec"):
        props["command"] = {"type": "string"}
        required = ["command"]
    elif name == "mcp_call":
        props["server"] = {"type": "string"}
        props["tool"] = {"type": "string"}
        props["arguments"] = {"type": "object"}
        required = ["server", "tool"]
    elif name.startswith("plan_"):
        props["steps"] = {"type": "array"}
    return {"type": "object", "properties": props, "required": required}


def _stub_handler(name: str) -> Handler:
    def _run(arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "tool": name,
            "stub": True,
            "message": f"native tool {name} scaffold (no v0.11 implementation)",
            "arguments": dict(arguments or {}),
        }

    return _run


def _make_native_spec(name: str, category: str, risk: str, extra: dict[str, Any]) -> ToolSpec:
    egress = extra.get("egress")
    privacy: dict[str, Any] = {
        "egress": egress or "none",
        "sends_user_content": bool(egress),
    }
    aliases = [a for a, target in _ALIASES.items() if target == name]
    return ToolSpec(
        name=name,
        provider="native",
        description=sanitize_description(f"Native tool: {name} ({category})"),
        input_schema=_default_schema(name),
        risk=risk,
        privacy=privacy,
        category=category,
        aliases=aliases,
        meta={"proposal": bool(extra.get("proposal")), "v011": True},
    )


@dataclass
class ToolCallResult:
    name: str
    ok: bool
    output: dict[str, Any]
    risk: str
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Handler] = {}
        self._alias_to_name: dict[str, str] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: Handler | None = None,
        *,
        trust_description: bool = False,
    ) -> None:
        desc = spec.description
        if not trust_description and spec.provider != "native":
            desc = sanitize_description(desc)
            # Never trust peer-reported risk for external tools — keep caller risk
        spec = deepcopy(spec)
        spec.description = desc
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler or _stub_handler(spec.name)
        for alias in spec.aliases:
            self._alias_to_name[alias] = spec.name

    def resolve_name(self, name: str) -> str:
        if name in self._specs:
            return name
        return self._alias_to_name.get(name, name)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(self.resolve_name(name))

    def list_tools(self, *, enabled_only: bool = False) -> list[ToolSpec]:
        out = list(self._specs.values())
        if enabled_only:
            out = [t for t in out if t.enabled]
        return sorted(out, key=lambda t: t.name)

    def names(self, *, enabled_only: bool = False) -> list[str]:
        return [t.name for t in self.list_tools(enabled_only=enabled_only)]

    def set_enabled(self, name: str, enabled: bool) -> None:
        spec = self.get(name)
        if spec:
            spec.enabled = enabled

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolCallResult:
        resolved = self.resolve_name(name)
        spec = self._specs.get(resolved)
        if spec is None:
            return ToolCallResult(
                name=name,
                ok=False,
                output={"error": "tool_not_found"},
                risk="R0",
            )
        if not spec.enabled:
            return ToolCallResult(
                name=resolved,
                ok=False,
                output={"error": "tool_disabled"},
                risk=spec.risk,
            )
        handler = self._handlers[resolved]
        raw = handler(dict(arguments or {}))
        text = str(raw)
        truncated = False
        if len(text) > spec.max_output_chars:
            truncated = True
            raw = {
                "ok": raw.get("ok", True) if isinstance(raw, dict) else True,
                "truncated": True,
                "preview": text[: spec.max_output_chars],
            }
        return ToolCallResult(
            name=resolved,
            ok=bool(raw.get("ok", True)) if isinstance(raw, dict) else True,
            output=raw if isinstance(raw, dict) else {"result": raw},
            risk=spec.risk,
            truncated=truncated,
        )


def build_default_registry(workspace: Any | None = None) -> ToolRegistry:
    """Register all 46 native tools + tool_search meta + shell_exec alias.

    If ``workspace`` is set, attach Personal Core real handlers for
    workspace/knowledge tools (R3).
    """
    from pathlib import Path

    reg = ToolRegistry()
    for name, category, risk, extra in _NATIVE_DEFS:
        reg.register(_make_native_spec(name, category, risk, extra))

    # Meta tool for router (§11.2 / §12.1 related)
    reg.register(
        ToolSpec(
            name="tool_search",
            provider="native",
            description="Search registered tools by keyword",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            risk="R0",
            category="meta",
            meta={"meta": True},
        ),
        handler=lambda args: {
            "ok": True,
            "matches": [
                t.name
                for t in reg.list_tools(enabled_only=True)
                if str(args.get("query") or "").lower() in t.name.lower()
                or str(args.get("query") or "").lower() in t.description.lower()
            ][:20],
        },
    )

    # Explicit alias registration map
    for alias, target in _ALIASES.items():
        spec = reg.get(target)
        if spec and alias not in spec.aliases:
            spec.aliases.append(alias)
        reg._alias_to_name[alias] = target

    if workspace is not None:
        from localwiki.tools.native_workspace import attach_workspace_handlers

        attach_workspace_handlers(reg, Path(workspace))

    return reg
