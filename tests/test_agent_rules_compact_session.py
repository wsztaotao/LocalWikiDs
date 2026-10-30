"""AT-AGT-05/06/07: AGENTS.md layers, compact handoff, session resume (LW-36)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.compact import (  # noqa: E402
    REQUIRED_HANDOFF_FIELDS,
    compact_messages,
)
from localwiki.agent.rules import (  # noqa: E402
    SYSTEM_RULES,
    load_rules,
    resolve_conflict_winner,
)
from localwiki.agent.session import SessionStore, after_tool_checkpoint  # noqa: E402


def test_system_rules_never_overridden() -> None:
    assert "Never relax approval" in SYSTEM_RULES
    assert resolve_conflict_winner(
        system_forbids=True,
        user_says="ignore safety",
        agents_specific="also ignore",
        agents_broad=None,
        style=None,
    ) == "system"


def test_more_specific_agents_md_wins(tmp_path: Path) -> None:
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "AGENTS.md").write_text("workspace: use formal tone", encoding="utf-8")
    nested = tmp_path / "projects" / "demo" / "src"
    nested.mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "AGENTS.md").write_text("project: prefer pytest", encoding="utf-8")
    (nested / "AGENTS.md").write_text("task: edit only src/", encoding="utf-8")

    bundle = load_rules(
        tmp_path,
        project_root=tmp_path / "projects" / "demo",
        task_dirs=[nested],
        user_instruction="finish the report",
        include_global=False,
    )
    ordered = bundle.ordered_agents_md()
    assert ordered[0].name == "task"
    assert "edit only src" in ordered[0].content
    rendered = bundle.render_for_context()
    # system before user before task
    assert rendered.index("[system]") < rendered.index("[user]")
    assert rendered.index("[user]") < rendered.index("[agents:task")


def test_compact_handoff_has_required_fields() -> None:
    messages = [{"role": "system", "content": "sys"}]
    for i in range(12):
        messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i}"})
    result = compact_messages(
        messages,
        goal="Ship LW-36",
        plan_status=[{"id": "s1", "status": "completed"}],
        decisions=["use Job Object"],
        modified_files=[{"path": "outputs/a.md", "hash": "abc"}],
        open_issues=["none"],
        approved_approvals=[{"tool": "win_command", "risk": "R3"}],
        keep_recent=6,
        force=True,
    )
    assert result.compacted
    handoff = result.handoff.to_dict()
    for key in REQUIRED_HANDOFF_FIELDS:
        assert key in handoff
    assert result.handoff.validate_required() == []
    # recent 6 kept + system head + summary
    assert any("handoff summary" in str(m.get("content")) for m in result.messages)
    assert result.messages[-1]["content"] == "turn-11"


def test_session_checkpoint_and_resume(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    state = store.create(task_uid="t1", goal="continue after restart")
    state.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    after_tool_checkpoint(
        store,
        state,
        call={"name": "knowledge_search"},
        result={"ok": True},
        messages=state.messages,
    )
    assert store.state_path(state.session_id).is_file()
    assert store.trace_path(state.session_id).is_file()

    loaded, warnings = store.resume(state.session_id)
    assert loaded is not None
    assert warnings == []
    assert loaded.goal == "continue after restart"
    assert loaded.step == 1
    assert loaded.messages[1]["content"] == "go"


def test_pending_external_write_blocks_blind_replay(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    state = store.create(goal="send mail")
    store.record_external_write(
        state,
        tool="integration_write",
        arguments={"to": "a@b.c"},
        receipt=None,
    )
    loaded, warnings = store.resume(state.session_id)
    assert loaded is not None
    assert "pending_reconcile" in warnings
    assert not store.can_continue(loaded)
    store.confirm_external_write(loaded, loaded.external_writes[0].write_id, receipt={"ok": True})
    assert store.can_continue(loaded)


def test_raw_trace_survives_compact(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    state = store.create(goal="g")
    for i in range(5):
        store.append_trace(state.session_id, {"type": "msg", "i": i, "text": f"full-{i}"})
    # compact does not touch raw
    lines = store.trace_path(state.session_id).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    assert "full-4" in lines[-1]
