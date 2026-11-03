"""AT-AGT-02: approval matrix cells + command classification (LW-34 / §11.4)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.policy import (  # noqa: E402
    APPROVAL_MATRIX,
    MODES,
    RISK_ORDER,
    PolicyEngine,
    all_matrix_cells,
    classify_command,
    matrix_action,
)
from localwiki.agent.protocol import ToolCall  # noqa: E402
from localwiki.tools.registry import ToolSpec, build_default_registry  # noqa: E402


def test_matrix_has_15_cells() -> None:
    cells = all_matrix_cells()
    assert len(cells) == 15
    assert len(MODES) == 3
    assert len(RISK_ORDER) == 5


def test_matrix_cells_match_design_11_4() -> None:
    """逐格对照 §11.4（含 full-access R3=auto、R4=approve）。"""
    expected = {
        ("read-only", "R0"): "auto",
        ("read-only", "R1"): "auto",
        ("read-only", "R2"): "reject",
        ("read-only", "R3"): "reject",
        ("read-only", "R4"): "reject",
        ("workspace-auto", "R0"): "auto",
        ("workspace-auto", "R1"): "auto",
        ("workspace-auto", "R2"): "auto",
        ("workspace-auto", "R3"): "approve",
        ("workspace-auto", "R4"): "approve",
        ("full-access", "R0"): "auto",
        ("full-access", "R1"): "auto",
        ("full-access", "R2"): "auto",
        ("full-access", "R3"): "auto",
        ("full-access", "R4"): "approve",
    }
    for mode, risk, action in all_matrix_cells():
        assert expected[(mode, risk)] == action
        assert APPROVAL_MATRIX[mode][risk] == action
        assert matrix_action(mode, risk) == action


def test_full_access_external_write_and_delete_still_approve() -> None:
    eng = PolicyEngine()
    reg = build_default_registry()
    write = ToolCall(id="1", name="integration_write", arguments={"body": "x"})
    d = eng.decide(write, reg.get("integration_write"), "full-access")
    assert d.action == "approve"
    assert d.risk_detail and d.risk_detail.external_write

    patch = ToolCall(
        id="2",
        name="apply_patch",
        arguments={"patch": "*** Begin Patch\n*** Delete File: outputs/a.txt\n*** End Patch"},
    )
    spec = ToolSpec(name="apply_patch", risk="R2", category="workspace")
    d2 = eng.decide(patch, spec, "full-access")
    assert d2.action == "approve"
    assert d2.risk_detail and d2.risk_detail.is_delete


def test_readonly_rejects_r2() -> None:
    eng = PolicyEngine()
    call = ToolCall(id="1", name="workspace_write", arguments={"path": "outputs/a.md"})
    spec = ToolSpec(name="workspace_write", risk="R2")
    d = eng.decide(call, spec, "read-only")
    assert d.action == "reject"


def test_workspace_auto_r3_needs_approval() -> None:
    eng = PolicyEngine()
    call = ToolCall(id="1", name="win_command", arguments={"command": "rmdir /s foo"})
    spec = ToolSpec(name="win_command", risk="R3", category="shell")
    d = eng.decide(call, spec, "workspace-auto")
    assert d.needs_approval
    assert d.risk == "R3"


def test_whitelist_command_downgrades_to_r0() -> None:
    eng = PolicyEngine()
    call = ToolCall(id="1", name="win_command", arguments={"command": "git status"})
    spec = ToolSpec(name="win_command", risk="R3", category="shell")
    detail = eng.classify_detailed(call, spec)
    assert detail.risk == "R0"
    assert detail.command_class == "readonly_whitelist"
    d = eng.decide(call, spec, "workspace-auto")
    assert d.action == "auto"


def test_pipe_and_interpreter_c_are_r3() -> None:
    r1, _, reasons = classify_command("dir | findstr x")
    assert r1 == "R3"
    assert "pipe_or_redirect" in reasons
    r2, _, reasons2 = classify_command('python -c "print(1)"')
    assert r2 == "R3"
    assert any("interpreter" in x for x in reasons2)


def test_model_claimed_safe_does_not_lower_risk() -> None:
    eng = PolicyEngine()
    call = ToolCall(
        id="1",
        name="win_command",
        arguments={"command": "format c:", "claimed_safe": True},
    )
    spec = ToolSpec(name="win_command", risk="R3", category="shell")
    detail = eng.classify_detailed(call, spec)
    assert detail.risk == "R3"
    assert "ignored_model_claimed_safe" in detail.reasons


def test_r1_rejected_when_network_disabled() -> None:
    eng = PolicyEngine(network_enabled=False)
    call = ToolCall(id="1", name="web_search", arguments={"query": "x"})
    spec = ToolSpec(name="web_search", risk="R1")
    d = eng.decide(call, spec, "workspace-auto")
    assert d.action == "reject"


def test_session_auto_approve_r3_non_external() -> None:
    eng = PolicyEngine()
    eng.remember_auto_approve("win_command", "R3")
    call = ToolCall(id="1", name="win_command", arguments={"command": "whoami"})
    spec = ToolSpec(name="win_command", risk="R3", category="shell")
    d = eng.decide(call, spec, "workspace-auto")
    assert d.action == "auto"
