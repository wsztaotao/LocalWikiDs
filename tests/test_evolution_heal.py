"""LW-40: self-healing ≤2 retries; HEAL needs evidence; no risk escalation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.protocol import ToolCall  # noqa: E402
from localwiki.evolution.heal import (  # noqa: E402
    MAX_HEAL_RETRIES,
    SelfHealer,
    has_verification_evidence,
)
from localwiki.evolution.learnings import LearningStore  # noqa: E402
from localwiki.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


def _reg_flaky() -> ToolRegistry:
    reg = ToolRegistry()
    state = {"n": 0}

    def handler(args):
        state["n"] += 1
        # fail first, succeed after heal retry flag or 2nd+ call
        if args.get("_heal_retry") or state["n"] > 1:
            return {"ok": True, "exit_code": 0, "verified": True, "n": state["n"]}
        return {"ok": False, "error": "E_TOOL_TIMEOUT", "exit_code": 1}

    reg.register(
        ToolSpec(name="workspace_read", risk="R0", category="workspace"),
        handler=handler,
    )
    reg.register(
        ToolSpec(name="win_command", risk="R3", category="shell"),
        handler=lambda a: {"ok": False, "error": "fail"},
    )
    return reg


def test_max_retries_is_two() -> None:
    assert MAX_HEAL_RETRIES == 2


def test_heal_success_writes_heal_with_evidence(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    healer = SelfHealer(_reg_flaky(), store)
    call = ToolCall(id="1", name="workspace_read", arguments={"ref": "x"})
    failed = {"ok": False, "output": {"error": "E_TOOL_TIMEOUT", "exit_code": 1}}
    result = healer.heal(call, failed, task_uid="t1")
    assert result.healed is True
    assert result.ok is True
    assert result.heal_record is not None
    assert result.heal_record.kind == "HEAL"
    assert "verification" in result.heal_record.evidence
    assert has_verification_evidence(result.final_result or {})
    md = (tmp_path / ".kw" / "learnings" / "HEALS.md").read_text(encoding="utf-8")
    assert "## [HEAL-" in md
    assert len([a for a in result.attempts if a.attempt > 0]) <= 2


def test_no_heal_record_without_verification(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(
        ToolSpec(name="workspace_read", risk="R0"),
        handler=lambda a: {"ok": False, "error": "still_bad", "exit_code": 2},
    )
    healer = SelfHealer(reg, LearningStore(tmp_path))
    call = ToolCall(id="1", name="workspace_read", arguments={})
    result = healer.heal(call, {"ok": False, "error": "boom"})
    assert result.healed is False
    assert result.heal_record is None
    assert "HEAL-" not in (tmp_path / ".kw" / "learnings" / "HEALS.md").read_text(
        encoding="utf-8"
    )


def test_cannot_escalate_risk_via_heal(tmp_path: Path) -> None:
    reg = ToolRegistry()
    reg.register(ToolSpec(name="workspace_write", risk="R2", category="workspace"))
    reg.register(ToolSpec(name="win_command", risk="R3", category="shell"))

    def escalate(call, result, attempt):
        # Illegal: repair by switching to R3 shell
        return ToolCall(
            id=f"bad{attempt}",
            name="win_command",
            arguments={"command": "echo hi"},
        )

    healer = SelfHealer(reg, LearningStore(tmp_path), repair_fn=escalate)
    call = ToolCall(id="1", name="workspace_write", arguments={"path": "outputs/a"})
    result = healer.heal(call, {"ok": False, "error": "fail"})
    assert result.healed is False
    assert result.blocked_reason == "risk_escalation_forbidden"
