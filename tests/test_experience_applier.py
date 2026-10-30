"""Tests for ExperienceApplier / ExperiencePack (LW-19 / §9.6–9.7)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.experience.applier import ExperienceApplier  # noqa: E402
from localwiki.experience.pack import (  # noqa: E402
    ExperienceItem,
    TaskSpec,
    select_experience_pack,
)


def _confirmed(**kwargs) -> ExperienceItem:
    defaults = dict(
        uid="exp_delivery_rule",
        kind="rule",
        title="技术设计需含实施节点",
        summary="技术设计类文档必须包含实施节点与验收条件",
        confirmation_state="user_confirmed",
        lifecycle="active",
        evidence_grade="multiple_independent_cases",
        task_types=["technical_design"],
        body="当任务类型为 technical_design 且需要后续实施时，必须写清实施节点。",
    )
    defaults.update(kwargs)
    return ExperienceItem(**defaults)


def _task(**kwargs) -> TaskSpec:
    defaults = dict(
        task_uid="task_01Jtest",
        goal="依据新需求写一份可实施的技术方案",
        task_type="technical_design",
        constraints=["Windows 11", "两周内交付"],
        deliverables=[{"kind": "docx", "name": "技术方案.docx"}],
        must_pass_checks=["包含实施节点", "包含验收条件"],
        tool_scope=["knowledge_*", "docx_create"],
        privacy={
            "allow_online_model": True,
            "allowed_classifications": ["normal", "private"],
        },
    )
    defaults.update(kwargs)
    return TaskSpec(**defaults)


def test_selected_and_rejected_have_reasons() -> None:
    items = [
        _confirmed(),
        _confirmed(
            uid="exp_short_memo",
            title="短备忘录",
            summary="短备忘",
            exclusions=["需要转交开发实施"],
            body="仅用于内部短备忘",
        ),
        _confirmed(
            uid="exp_wrong_type",
            title="聊天语气",
            task_types=["chat"],
            summary="聊天用口语",
        ),
    ]
    task = _task(goal="写技术方案并需要转交开发实施")
    pack = select_experience_pack(items, task)
    assert pack.selected
    for s in pack.selected:
        assert s.why
        assert s.uid
        assert s.version_sha
        assert s.grade
    assert pack.rejected
    for r in pack.rejected:
        assert r.why
        assert r.uid
    # exclusion hit
    assert any(r.uid == "exp_short_memo" for r in pack.rejected)
    assert any("exclusions" in r.why for r in pack.rejected)
    # task_type mismatch
    assert any(r.uid == "exp_wrong_type" for r in pack.rejected)


def test_unconfirmed_not_in_selected() -> None:
    items = [
        _confirmed(uid="exp_ok"),
        ExperienceItem(
            uid="exp_cand_07",
            kind="rule",
            title="候选未确认",
            summary="推断出来的写法",
            confirmation_state="unconfirmed",
            lifecycle="proposed",
            task_types=["technical_design"],
            evidence_grade="single_case",
        ),
    ]
    pack = select_experience_pack(items, _task())
    selected_uids = {s.uid for s in pack.selected}
    assert "exp_ok" in selected_uids
    assert "exp_cand_07" not in selected_uids
    assert "exp_cand_07" in pack.candidates_shown_not_used


def test_disabled_and_privacy_rejected() -> None:
    items = [
        _confirmed(uid="exp_disabled", lifecycle="disabled"),
        _confirmed(
            uid="exp_secret",
            privacy_classification="secret",
        ),
    ]
    pack = select_experience_pack(items, _task())
    assert pack.selected == []
    whys = {r.uid: r.why for r in pack.rejected}
    assert "disabled" in whys["exp_disabled"]
    assert "privacy" in whys["exp_secret"]


def test_budget_truncation_rejects_with_reason() -> None:
    items = [
        _confirmed(
            uid=f"exp_{i}",
            title=f"规则{i}",
            summary="x" * 200,
            body="细节 " * 80,
        )
        for i in range(5)
    ]
    # ~222 tokens each; budget fits one, rest rejected for budget
    pack = select_experience_pack(items, _task(), budget_tokens=250)
    assert len(pack.selected) == 1
    assert pack.rejected
    assert any("budget" in r.why for r in pack.rejected)


def test_applier_attach_taskspec_and_injection() -> None:
    applier = ExperienceApplier(
        [
            _confirmed(preferred_tools=["docx_create", "web_search"]),
            ExperienceItem(
                uid="exp_cand_x",
                kind="procedure",
                confirmation_state="unconfirmed",
                lifecycle="proposed",
                task_types=["technical_design"],
                title="未确认程序",
            ),
        ]
    )
    task = _task()
    updated, result = applier.attach_to_task_spec(task, apply_point="agent_plan")
    assert updated.experience_pack_uid == result.pack.pack_uid
    assert updated.approval_mode == task.approval_mode
    assert updated.tool_scope == task.tool_scope
    assert "exp_cand_x" not in result.pack.selected_uids()
    assert "exp_cand_x" in result.pack.candidates_shown_not_used
    assert result.pack.priority_note
    assert "[exp_delivery_rule]" in result.injection_text
    assert "docx_create" in result.preferred_tools
    d = result.pack.to_dict()
    assert "selected" in d and "rejected" in d
    assert d["budget_tokens"] > 0


def test_self_check_prefers_rubric() -> None:
    applier = ExperienceApplier(
        [
            _confirmed(uid="exp_rule", kind="rule"),
            _confirmed(
                uid="exp_rubric",
                kind="rubric",
                title="验收细则",
                summary="必须包含验收条件",
            ),
        ]
    )
    result = applier.apply(_task(), apply_point="agent_self_check")
    uids = result.pack.selected_uids()
    assert "exp_rubric" in uids
    assert "exp_rule" not in uids


def test_taskspec_schema_fields() -> None:
    t = _task()
    d = t.to_dict()
    assert d["schema_version"] == 3
    assert d["task_uid"]
    assert d["privacy"]["allowed_classifications"]
    assert "approval_mode" in d
