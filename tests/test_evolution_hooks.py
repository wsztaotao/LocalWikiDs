"""LW-39: hooks emit ERR/LRN; format readable by self-improvement Skill (§23.4/23.6)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.hooks import EvolutionHooks  # noqa: E402
from localwiki.evolution.learnings import (  # noqa: E402
    LearningStore,
    parse_learnings_markdown,
)


def test_tool_failure_generates_err(tmp_path: Path) -> None:
    hooks = EvolutionHooks(tmp_path)
    recs = hooks.on_tool_result(
        {"name": "win_command", "arguments": {"command": "x"}},
        {"ok": False, "output": {"error": "E_TIMEOUT", "exit_code": 1}},
        task_uid="task_1",
    )
    assert len(recs) == 1
    assert recs[0].kind == "ERR"
    text = (tmp_path / ".kw" / "learnings" / "ERRORS.md").read_text(encoding="utf-8")
    parsed = parse_learnings_markdown(text)
    assert parsed
    assert parsed[0]["kind"] == "ERR"
    assert "Summary" in text
    assert "Pattern-Key" in text
    assert "Distinct-Tasks" in text
    assert "Evidence" in text
    assert "Classification" in text


def test_user_correction_generates_lrn(tmp_path: Path) -> None:
    hooks = EvolutionHooks(tmp_path)
    recs = hooks.on_user_message("不对，应该是先读 index.md", task_uid="task_2")
    assert any(r.kind == "LRN" and r.category == "correction" for r in recs)
    text = (tmp_path / ".kw" / "learnings" / "LEARNINGS.md").read_text(encoding="utf-8")
    parsed = parse_learnings_markdown(text)
    assert parsed[0]["category"] == "correction"
    assert parsed[0]["status"] == "pending"


def test_approval_rejected_generates_lrn(tmp_path: Path) -> None:
    hooks = EvolutionHooks(tmp_path)
    recs = hooks.on_approval_decision(
        {"decision": "rejected", "reason": "too risky"},
        task_uid="task_3",
    )
    assert recs[0].kind == "LRN"
    assert recs[0].category == "correction"


def test_pattern_key_dedup_bumps_recurrence(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    hooks = EvolutionHooks(store)
    hooks.on_tool_result(
        {"name": "apply_patch"},
        {"ok": False, "output": {"error": "context_mismatch"}},
    )
    hooks.on_tool_result(
        {"name": "apply_patch"},
        {"ok": False, "output": {"error": "context_mismatch"}},
    )
    # Same pattern_key tools.apply_patch.failure → bump
    entries = store.list_records("ERR")
    assert len(entries) == 1
    assert int(entries[0]["recurrence_count"]) >= 2


def test_skill_compatible_required_sections(tmp_path: Path) -> None:
    """Format mirrors self-improvement Skill expectations."""
    hooks = EvolutionHooks(tmp_path)
    hooks.on_user_message("actually that was wrong")
    md = (tmp_path / ".kw" / "learnings" / "LEARNINGS.md").read_text(encoding="utf-8")
    for heading in ("### Summary", "### Details", "### Suggested Action", "### Metadata"):
        assert heading in md
    for meta in (
        "Source:",
        "Pattern-Key:",
        "Recurrence-Count:",
        "Distinct-Tasks:",
        "Evidence:",
        "Classification:",
    ):
        assert meta in md


def test_no_record_on_successful_tool(tmp_path: Path) -> None:
    hooks = EvolutionHooks(tmp_path)
    recs = hooks.on_tool_result(
        {"name": "knowledge_search"},
        {"ok": True, "output": {"hits": []}},
    )
    assert recs == []
