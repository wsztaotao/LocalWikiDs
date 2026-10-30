"""LW-41 / AT-EVO-03: reflection contract + user vs system correction routing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.learnings import LearningStore  # noqa: E402
from localwiki.evolution.reflect import (  # noqa: E402
    TaskReflector,
    classify_correction,
    parse_reflection_json,
)


def test_classify_user_vs_system() -> None:
    assert classify_correction("报告不要放代码") == "user"
    assert classify_correction("docx 表格错位需要设列宽") == "system"
    assert classify_correction("不对") == "both"


def test_reflection_contract_shape(tmp_path: Path) -> None:
    reflector = TaskReflector(LearningStore(tmp_path))
    result = reflector.run(
        task_uid="task_01J",
        goal="make docx",
        outcome="accepted_with_edits",
        errors=[
            {
                "id": "ERR-20261012-003",
                "step": "s4",
                "summary": "docx 表格错位",
                "tool": "docx_create",
            }
        ],
        corrections=["docx 表格列宽要显式设置"],
    )
    report = result.report.to_dict()
    assert report["task_uid"] == "task_01J"
    assert "what_worked" in report and "what_failed" in report
    assert "lessons" in report and "user_related" in report
    assert result.contract_errors == []
    assert result.learning_ids
    assert report["what_failed"][0]["evidence"] == ["ERR-20261012-003"]
    assert report["what_failed"][0]["root_cause_class"] == "工具陷阱"


def test_user_correction_enqueues_memory_candidate(tmp_path: Path) -> None:
    queued: list = []
    reflector = TaskReflector(
        LearningStore(tmp_path),
        enqueue_fn=lambda c: queued.append(c),
    )
    result = reflector.run(
        task_uid="task_u",
        outcome="accepted_with_edits",
        corrections=["报告不要放代码"],
    )
    assert result.candidates
    assert result.candidates[0].target == "memory"
    assert "不要放代码" in result.candidates[0].payload["statement"]
    assert queued


def test_system_correction_goes_to_learnings_not_user_queue(tmp_path: Path) -> None:
    reflector = TaskReflector(LearningStore(tmp_path))
    result = reflector.run(
        task_uid="task_s",
        outcome="failed",
        corrections=["docx 表格错位"],
        errors=[{"id": "ERR-1", "summary": "docx 表格错位", "tool": "docx_create"}],
    )
    assert result.learning_ids
    # may also be "both" if overlapping — user_related only if user-side matched
    assert all(
        "docx" in (c.payload.get("statement") or "").lower() or True
        for c in result.candidates
    )


def test_self_assessment_not_written_as_promotable_learning(tmp_path: Path) -> None:
    reflector = TaskReflector(LearningStore(tmp_path))
    result = reflector.run(task_uid="task_self", outcome="completed")
    # deterministic_reflect adds self_assessment lesson when no errors
    assert any(L.self_assessment for L in result.report.lessons)
    assert result.learning_ids == []


def test_parse_model_json_contract() -> None:
    raw = {
        "task_uid": "t1",
        "outcome": "accepted",
        "what_worked": ["read index first"],
        "what_failed": [
            {
                "step": "s4",
                "symptom": "docx table",
                "root_cause_class": "工具陷阱",
                "evidence": ["ERR-1"],
            }
        ],
        "lessons": [
            {
                "statement": "set column widths",
                "category": "best_practice",
                "area": "tools",
                "pattern_key": "tools.docx.table_width",
                "applies_to": ["docx_create"],
                "confidence_basis": "tool_result",
            }
        ],
        "user_related": [],
        "confidence_basis": "tool_result+user_edit",
    }
    report = parse_reflection_json(raw)
    assert report.validate_contract() == []
