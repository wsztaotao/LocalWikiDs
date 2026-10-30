"""Function-calling protocol adapters (design §11.2 / AT-AGT-09).

Supports:
- OpenAI-compatible ``tools`` / ``tool_calls``
- Anthropic ``tools`` / ``tool_use`` content blocks
- Ollama ``tools`` / ``tool_calls`` (chat API)
- Strict JSON fallback when the model has no native tool calling
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from localwiki.tools.registry import ToolSpec

ProtocolKind = Literal["openai", "anthropic", "ollama", "json_fallback"]

PROTOCOL_KINDS: tuple[ProtocolKind, ...] = (
    "openai",
    "anthropic",
    "ollama",
    "json_fallback",
)

JSON_FALLBACK_SYSTEM = (
    "You must respond with a single JSON object only. "
    "To call tools: "
    '{"final":false,"tool_calls":[{"name":"<tool>","arguments":{...}}]}. '
    "To finish: "
    '{"final":true,"content":"<delivery text>"}. '
    "No markdown fences, no extra prose."
)

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


def _new_id(prefix: str = "call") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class ToolCall:
    """Normalized tool invocation (provider-agnostic)."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass
class ModelResponse:
    """Normalized model turn."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    final: bool = False
    protocol: ProtocolKind | str = "openai"
    raw: Any = None
    parse_error: str | None = None

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "final": self.final,
            "protocol": self.protocol,
            "parse_error": self.parse_error,
        }


def _parse_args(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            val = json.loads(s)
            return dict(val) if isinstance(val, dict) else {"_raw": val}
        except json.JSONDecodeError:
            return {"_raw": s}
    return {"_raw": raw}


def tool_specs_to_openai(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema or {"type": "object", "properties": {}},
                },
            }
        )
    return out


def tool_specs_to_anthropic(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append(
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema
                or {"type": "object", "properties": {}},
            }
        )
    return out


def tool_specs_to_ollama(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    # Ollama chat tools mirror OpenAI function shape
    return tool_specs_to_openai(tools)


def tool_specs_to_json_schema_prompt(tools: Sequence[ToolSpec]) -> str:
    lines = [JSON_FALLBACK_SYSTEM, "", "Available tools:"]
    for t in tools:
        schema = json.dumps(t.input_schema or {}, ensure_ascii=False)
        lines.append(f"- {t.name}: {t.description} | schema={schema}")
    return "\n".join(lines)


class ProtocolAdapter:
    """Convert between provider wire formats and normalized ToolCall/ModelResponse."""

    def __init__(self, kind: ProtocolKind | str) -> None:
        if kind not in PROTOCOL_KINDS:
            raise ValueError(f"unknown protocol: {kind}")
        self.kind: ProtocolKind = kind  # type: ignore[assignment]

    def format_tools(self, tools: Sequence[ToolSpec]) -> Any:
        if self.kind == "openai":
            return tool_specs_to_openai(tools)
        if self.kind == "anthropic":
            return tool_specs_to_anthropic(tools)
        if self.kind == "ollama":
            return tool_specs_to_ollama(tools)
        return tool_specs_to_json_schema_prompt(tools)

    def request_extras(self, tools: Sequence[ToolSpec]) -> dict[str, Any]:
        """Provider-specific request fields (tools / system hint)."""
        if self.kind == "json_fallback":
            return {
                "tools": None,
                "system_extra": tool_specs_to_json_schema_prompt(tools),
                "response_format_hint": "json_object",
            }
        formatted = self.format_tools(tools)
        if self.kind == "anthropic":
            return {"tools": formatted, "tool_choice": {"type": "auto"}}
        return {"tools": formatted}

    def parse_response(self, raw: Any) -> ModelResponse:
        if self.kind == "openai":
            return self._parse_openai(raw)
        if self.kind == "anthropic":
            return self._parse_anthropic(raw)
        if self.kind == "ollama":
            return self._parse_ollama(raw)
        return self._parse_json_fallback(raw)

    def format_tool_result_message(
        self,
        call: ToolCall,
        result: dict[str, Any],
        *,
        wrap_untrusted: bool = True,
    ) -> dict[str, Any]:
        """Build a provider-shaped observation message for the next turn."""
        body = result
        text = json.dumps(body, ensure_ascii=False)
        if wrap_untrusted:
            text = f"<untrusted>\n{text}\n</untrusted>"
        if self.kind == "anthropic":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": text,
                    }
                ],
            }
        if self.kind == "json_fallback":
            return {
                "role": "user",
                "content": json.dumps(
                    {
                        "tool_result": {
                            "id": call.id,
                            "name": call.name,
                            "output": text,
                        }
                    },
                    ensure_ascii=False,
                ),
            }
        # openai + ollama
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": text,
        }

    def format_assistant_message(self, resp: ModelResponse) -> dict[str, Any]:
        """Re-serialize a normalized response as an assistant message for history."""
        if self.kind == "anthropic":
            blocks: list[dict[str, Any]] = []
            if resp.content:
                blocks.append({"type": "text", "text": resp.content})
            for c in resp.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": c.id,
                        "name": c.name,
                        "input": c.arguments,
                    }
                )
            return {"role": "assistant", "content": blocks}
        if self.kind == "json_fallback":
            payload: dict[str, Any]
            if resp.final or not resp.tool_calls:
                payload = {"final": True, "content": resp.content}
            else:
                payload = {
                    "final": False,
                    "tool_calls": [
                        {"name": c.name, "arguments": c.arguments}
                        for c in resp.tool_calls
                    ],
                }
            return {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}
        # openai / ollama
        msg: dict[str, Any] = {"role": "assistant", "content": resp.content or None}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in resp.tool_calls
            ]
        return msg

    # --- parsers ---------------------------------------------------------

    def _parse_openai(self, raw: Any) -> ModelResponse:
        data = raw if isinstance(raw, dict) else {}
        # Accept full chat.completion or bare message
        msg = data.get("choices", [{}])[0].get("message") if "choices" in data else data
        if not isinstance(msg, dict):
            msg = {}
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(tc.get("id") or _new_id()),
                    name=str(fn.get("name") or ""),
                    arguments=_parse_args(fn.get("arguments")),
                )
            )
        final = not calls and bool(
            data.get("finish_reason") in (None, "stop", "end_turn")
            or msg.get("finish_reason") in (None, "stop")
        )
        if calls:
            final = False
        elif content and not calls:
            final = True
        return ModelResponse(
            content=content,
            tool_calls=calls,
            final=final,
            protocol="openai",
            raw=raw,
        )

    def _parse_anthropic(self, raw: Any) -> ModelResponse:
        data = raw if isinstance(raw, dict) else {}
        content_blocks = data.get("content") or []
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        if isinstance(content_blocks, str):
            text_parts.append(content_blocks)
            content_blocks = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(str(block.get("text") or ""))
            elif btype == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or _new_id("toolu")),
                        name=str(block.get("name") or ""),
                        arguments=_parse_args(block.get("input")),
                    )
                )
        stop = data.get("stop_reason")
        final = not calls and stop in (None, "end_turn", "stop_sequence")
        if calls:
            final = False
        return ModelResponse(
            content="".join(text_parts),
            tool_calls=calls,
            final=final,
            protocol="anthropic",
            raw=raw,
        )

    def _parse_ollama(self, raw: Any) -> ModelResponse:
        data = raw if isinstance(raw, dict) else {}
        msg = data.get("message") if isinstance(data.get("message"), dict) else data
        if not isinstance(msg, dict):
            msg = {}
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            # Ollama may nest under function or use flat name/arguments
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            calls.append(
                ToolCall(
                    id=str(tc.get("id") or _new_id("ollama")),
                    name=str(fn.get("name") or ""),
                    arguments=_parse_args(fn.get("arguments")),
                )
            )
        final = not calls and bool(data.get("done", True))
        if calls:
            final = False
        return ModelResponse(
            content=content,
            tool_calls=calls,
            final=final,
            protocol="ollama",
            raw=raw,
        )

    def _parse_json_fallback(self, raw: Any) -> ModelResponse:
        text = ""
        if isinstance(raw, dict):
            # Accept already-parsed dict or message wrapper
            if "final" in raw or "tool_calls" in raw:
                payload = raw
            else:
                text = str(
                    (raw.get("message") or {}).get("content")
                    if isinstance(raw.get("message"), dict)
                    else raw.get("content")
                    or raw.get("response")
                    or ""
                )
                payload = None
        elif isinstance(raw, str):
            text = raw
            payload = None
        else:
            text = str(raw or "")
            payload = None

        parse_error: str | None = None
        if payload is None:
            payload, parse_error = _extract_json_object(text)

        if not isinstance(payload, dict):
            return ModelResponse(
                content=text,
                tool_calls=[],
                final=True,
                protocol="json_fallback",
                raw=raw,
                parse_error=parse_error or "not_a_json_object",
            )

        calls: list[ToolCall] = []
        for tc in payload.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "")
            if not name:
                continue
            calls.append(
                ToolCall(
                    id=str(tc.get("id") or _new_id("json")),
                    name=name,
                    arguments=_parse_args(tc.get("arguments") or tc.get("input")),
                )
            )
        content = payload.get("content")
        if content is None:
            content = payload.get("message") or ""
        content_s = content if isinstance(content, str) else str(content)
        explicit_final = bool(payload.get("final", False))
        final = explicit_final or (not calls)
        if calls:
            final = False
        return ModelResponse(
            content=content_s,
            tool_calls=calls,
            final=final,
            protocol="json_fallback",
            raw=raw,
            parse_error=parse_error,
        )


def _extract_json_object(text: str) -> tuple[Any, str | None]:
    s = (text or "").strip()
    if not s:
        return None, "empty"
    # Strip common markdown fences
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s), None
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(s)
        if not m:
            return None, "no_json_object"
        try:
            return json.loads(m.group(0)), None
        except json.JSONDecodeError as exc:
            return None, f"json_decode:{exc}"


def get_adapter(kind: ProtocolKind | str) -> ProtocolAdapter:
    return ProtocolAdapter(kind)


def detect_protocol(
    *,
    provider: str | None = None,
    tool_calling: bool | None = None,
) -> ProtocolKind:
    """Pick adapter from provider name / capability probe."""
    p = (provider or "").lower()
    if tool_calling is False:
        return "json_fallback"
    if "anthropic" in p or p == "claude":
        return "anthropic"
    if "ollama" in p:
        return "ollama"
    if p in ("openai", "openai_compat", "compatible", "openrouter"):
        return "openai"
    if tool_calling is True:
        return "openai"
    return "json_fallback"
