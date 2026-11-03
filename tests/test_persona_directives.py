"""Tests for StyleDirectives (LW-23 / §10.6)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.persona.directives import (  # noqa: E402
    StyleDirectiveService,
    StyleFeatureFlags,
    compile_style_directives,
)
from localwiki.persona.traits import PersonaTraitCandidate  # noqa: E402


def _trait(dim: str, lo: float, hi: float, uid: str = "trait_x") -> PersonaTraitCandidate:
    return PersonaTraitCandidate(
        uid=uid,
        dimension=dim,
        context="work",
        title=dim,
        description="t",
        value_range=(lo, hi),
        evidence_summary={"statements": 1},
        evidence_refs=["r1"],
        confirmation_state="user_confirmed",
        lifecycle="active",
        evidence_grade="direct_statement",
    )


def test_compile_maps_dimensions() -> None:
    sd = compile_style_directives(
        [
            _trait("information_density", 0.8, 1.6, "trait_density_work"),
            _trait("expression_style", 0.8, 1.5, "trait_expr"),
            _trait("structure_preference", 0.7, 1.4, "trait_struct"),
        ]
    )
    assert sd.response["length"] == "concise"
    assert sd.response["tone"] == "direct"
    assert sd.response["format"] == "tables_and_lists"
    assert "trait_density_work" in sd.compiled_from
    assert sd.cannot_relax_approvals is True
    assert sd.cannot_alter_facts is True
    assert "不得放宽安全审批" in sd.prompt_text
    d = sd.to_dict()
    assert d["sd_uid"] and d["version_sha"]


def test_chat_agent_artifact_apply_and_independent_disable() -> None:
    sd = compile_style_directives(
        [
            _trait("information_density", 1.0, 1.5, "td"),
            _trait("plan_granularity", -1.5, -0.8, "tp"),
            _trait("autonomy_preference", 1.0, 1.5, "ta"),
        ]
    )
    svc = StyleDirectiveService(sd)

    chat = svc.apply("chat")
    agent = svc.apply("agent", approval_mode="workspace-auto")
    art = svc.apply("artifact")
    assert chat.applied and agent.applied and art.applied
    assert chat.prompt_addon
    assert agent.fragment["approval_mode"] == "workspace-auto"
    assert agent.fragment["approval_mode_locked"] is True
    assert agent.fragment["cannot_relax_approvals"] is True
    assert art.fragment["density"] == "high"

    # Disable chat only
    svc.disable("chat")
    assert svc.apply("chat").applied is False
    assert svc.apply("chat").disabled_reason == "surface_disabled:chat"
    assert svc.apply("agent").applied is True
    assert svc.apply("artifact").applied is True

    # Disable agent
    svc.disable("agent")
    assert svc.apply("agent").applied is False
    assert svc.apply("artifact").applied is True

    # Re-enable chat, disable artifact
    svc.enable("chat")
    svc.disable("artifact")
    assert svc.apply("chat").applied is True
    assert svc.apply("artifact").applied is False

    # Global off
    svc.disable()
    assert svc.apply("chat").applied is False
    assert svc.apply("chat").disabled_reason == "global_disabled"


def test_priority_fields_do_not_override_user() -> None:
    sd = compile_style_directives([_trait("information_density", 1, 1.5, "t1")])
    svc = StyleDirectiveService(sd)
    r = svc.apply(
        "chat",
        user_instruction="这次请写得很详细",
        project_constraints=["必须含风险章节"],
    )
    assert r.fragment["user_instruction_priority"] is True
    assert "必须含风险章节" in r.fragment["project_constraints"]
    assert "当前用户指令" in sd.priority_note


def test_flags_roundtrip() -> None:
    f = StyleFeatureFlags.from_dict({"global_enabled": True, "chat": False})
    assert f.chat is False
    assert f.agent is True
    assert f.is_enabled("chat") is False
