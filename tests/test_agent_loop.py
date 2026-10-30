"""AT-AGT-09: OpenAI / Anthropic / Ollama + JSON fallback complete a standard task (LW-33)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.loop import (  # noqa: E402
    AgentLoop,
    ConservativeApprovalGate,
    LoopConfig,
)
from localwiki.agent.protocol import (  # noqa: E402
    PROTOCOL_KINDS,
    ProtocolAdapter,
    detect_protocol,
    get_adapter,
)
from localwiki.experience.pack import TaskSpec  # noqa: E402
from localwiki.tools.registry import ToolRegistry, ToolSpec, build_default_registry  # noqa: E402


def _task() -> TaskSpec:
    return TaskSpec(
        task_uid="task_agt09",
        goal="Search knowledge for localwiki then finish",
        task_type="research",
        must_pass_checks=["localwiki"],
        approval_mode="workspace-auto",
        tool_scope=["knowledge_*", "tool_search", "plan_*"],
    )


class ScriptedModel:
    """Replay provider-shaped payloads; advances on each generate()."""

    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.seen_tools: list[Any] = []
        self.seen_messages: list[list[dict[str, Any]]] = []

    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any,
        extras: dict[str, Any],
    ) -> Any:
        self.seen_messages.append(list(messages))
        self.seen_tools.append(tools)
        if self.calls >= len(self.payloads):
            # Safety: always terminate
            return self.payloads[-1]
        raw = self.payloads[self.calls]
        self.calls += 1
        return raw


def _registry_with_knowledge() -> ToolRegistry:
    reg = build_default_registry()
    reg.register(
        ToolSpec(
            name="knowledge_search",
            provider="native",
            description="Search wiki knowledge",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            risk="R0",
            category="knowledge_memory",
        ),
        handler=lambda args: {
            "ok": True,
            "hits": [{"title": "LocalWiki overview", "snippet": "localwiki agent runtime"}],
            "query": args.get("query"),
        },
        trust_description=True,
    )
    return reg


def _openai_payloads() -> list[Any]:
    return [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "knowledge_search",
                                    "arguments": json.dumps({"query": "localwiki"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Done. Found localwiki overview.",
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    ]


def _anthropic_payloads() -> list[Any]:
    return [
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "knowledge_search",
                    "input": {"query": "localwiki"},
                }
            ],
            "stop_reason": "tool_use",
        },
        {
            "content": [{"type": "text", "text": "Finished with localwiki notes."}],
            "stop_reason": "end_turn",
        },
    ]


def _ollama_payloads() -> list[Any]:
    return [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "knowledge_search",
                            "arguments": {"query": "localwiki"},
                        }
                    }
                ],
            },
            "done": True,
        },
        {
            "message": {
                "role": "assistant",
                "content": "Task complete: localwiki search ok.",
            },
            "done": True,
        },
    ]


def _json_fallback_payloads() -> list[Any]:
    return [
        {
            "content": json.dumps(
                {
                    "final": False,
                    "tool_calls": [
                        {"name": "knowledge_search", "arguments": {"query": "localwiki"}}
                    ],
                }
            )
        },
        {
            "content": json.dumps(
                {"final": True, "content": "Delivered localwiki research summary."}
            )
        },
    ]


def _run_protocol(kind: str, payloads: list[Any]) -> Any:
    model = ScriptedModel(payloads)
    loop = AgentLoop(
        _registry_with_knowledge(),
        model,
        protocol=kind,
        config=LoopConfig(max_steps=10),
    )
    return loop.run(_task())


def test_protocol_kinds_cover_three_plus_fallback() -> None:
    assert set(PROTOCOL_KINDS) == {"openai", "anthropic", "ollama", "json_fallback"}


def test_openai_compatible_completes_standard_task() -> None:
    result = _run_protocol("openai", _openai_payloads())
    assert result.status == "completed"
    assert result.ok
    assert result.protocol == "openai"
    assert any(t["call"]["name"] == "knowledge_search" for t in result.tool_trace)
    assert "localwiki" in result.content.lower()
    # observations wrapped
    # (checked via tool_trace ok)
    assert result.tool_trace[0]["result"]["ok"] is True


def test_anthropic_tool_use_completes_standard_task() -> None:
    result = _run_protocol("anthropic", _anthropic_payloads())
    assert result.status == "completed"
    assert result.ok
    assert result.protocol == "anthropic"
    assert result.tool_trace[0]["call"]["name"] == "knowledge_search"


def test_ollama_tools_completes_standard_task() -> None:
    result = _run_protocol("ollama", _ollama_payloads())
    assert result.status == "completed"
    assert result.ok
    assert result.protocol == "ollama"
    assert result.tool_trace[0]["call"]["name"] == "knowledge_search"


def test_json_fallback_completes_standard_task() -> None:
    result = _run_protocol("json_fallback", _json_fallback_payloads())
    assert result.status == "completed"
    assert result.ok
    assert result.protocol == "json_fallback"
    assert result.tool_trace[0]["call"]["name"] == "knowledge_search"
    assert "localwiki" in result.content.lower()


def test_all_four_protocols_pass_at_agt_09() -> None:
    cases = [
        ("openai", _openai_payloads()),
        ("anthropic", _anthropic_payloads()),
        ("ollama", _ollama_payloads()),
        ("json_fallback", _json_fallback_payloads()),
    ]
    for kind, payloads in cases:
        result = _run_protocol(kind, payloads)
        assert result.status == "completed", kind
        assert result.ok, kind
        assert len(result.tool_trace) >= 1, kind


def test_parse_openai_and_anthropic_adapters() -> None:
    oa = get_adapter("openai").parse_response(_openai_payloads()[0])
    assert oa.tool_calls[0].name == "knowledge_search"
    assert oa.final is False
    an = get_adapter("anthropic").parse_response(_anthropic_payloads()[0])
    assert an.tool_calls[0].id == "toolu_1"
    js = get_adapter("json_fallback").parse_response(
        {"content": '```json\n{"final":true,"content":"ok"}\n```'}
    )
    assert js.final is True
    assert js.content == "ok"


def test_detect_protocol() -> None:
    assert detect_protocol(provider="anthropic", tool_calling=True) == "anthropic"
    assert detect_protocol(provider="ollama", tool_calling=True) == "ollama"
    assert detect_protocol(provider="openai_compat", tool_calling=True) == "openai"
    assert detect_protocol(provider="ollama", tool_calling=False) == "json_fallback"


def test_r3_requires_approval_under_workspace_auto() -> None:
    """Security default: do not auto-run R3 without approver (no matrix widen)."""
    reg = build_default_registry()

    class R3Model:
        def generate(self, messages, *, tools, extras):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "win_command",
                                        "arguments": '{"command":"dir"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

    loop = AgentLoop(
        reg,
        R3Model(),
        protocol="openai",
        policy=ConservativeApprovalGate(),
        config=LoopConfig(max_steps=3),
    )
    result = loop.run(
        TaskSpec(
            task_uid="t_apr",
            goal="run dir",
            task_type="ops",
            approval_mode="workspace-auto",
        )
    )
    assert result.status == "approval_required"
    assert result.error == "E_APPROVAL_REQUIRED"
    assert result.approval_pending is not None
    assert result.approval_pending.risk == "R3"


def test_format_tools_shapes() -> None:
    reg = build_default_registry()
    specs = [reg.get("knowledge_search"), reg.get("tool_search")]
    specs = [s for s in specs if s]
    openai_tools = ProtocolAdapter("openai").format_tools(specs)
    assert openai_tools[0]["type"] == "function"
    anth = ProtocolAdapter("anthropic").format_tools(specs)
    assert "input_schema" in anth[0]
    prompt = ProtocolAdapter("json_fallback").format_tools(specs)
    assert "Available tools:" in prompt
    assert "knowledge_search" in prompt


def test_untrusted_wrapper_on_observations() -> None:
    model = ScriptedModel(_openai_payloads())
    loop = AgentLoop(
        _registry_with_knowledge(),
        model,
        protocol="openai",
        config=LoopConfig(max_steps=10, wrap_untrusted=True),
    )
    loop.run(_task())
    # Second generate should see a tool role message with <untrusted>
    assert len(model.seen_messages) >= 2
    flat = json.dumps(model.seen_messages[1], ensure_ascii=False)
    assert "<untrusted>" in flat
