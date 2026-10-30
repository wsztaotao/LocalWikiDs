"""Tests for experience compiler (LW-18 / §9.4–9.5).

Every signal row in §9.4 has at least one test.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.behavior_log import BehaviorEvent, BehaviorLog  # noqa: E402
from localwiki.experience.compiler import (  # noqa: E402
    BEHAVIOR_MIN_N,
    BEHAVIOR_MIN_SPAN_DAYS,
    PLAN_REVISION_MIN,
    TOOL_REJECT_MIN,
    ExperienceCompiler,
    compile_agent_sessions,
    compile_behavior_stats,
)


def _sess(
    *events: dict,
    task_type: str = "technical_design",
    session_uid: str = "s1",
    task_uid: str = "t1",
) -> list[dict]:
    out = []
    for ev in events:
        row = {
            "session_uid": session_uid,
            "task_uid": task_uid,
            "task_type": task_type,
            **ev,
        }
        out.append(row)
    return out


# --- §9.4 row: plan modification → Procedure (≥2 same task type) ---


def test_plan_revision_requires_two_same_task_type() -> None:
    one = _sess(
        {"event": "plan_set", "payload": {"steps": ["a", "b"]}},
        {"event": "plan_update", "payload": {"steps_added": 1, "user_modified": True}},
        session_uid="s1",
        task_uid="t1",
    )
    report = compile_agent_sessions([one])
    assert report.by_signal("plan_revision") == []

    two = _sess(
        {"event": "plan_update", "payload": {"steps_removed": 1, "user_modified": True}},
        session_uid="s2",
        task_uid="t2",
    )
    report2 = compile_agent_sessions([one, two])
    cands = report2.by_signal("plan_revision")
    assert len(cands) == 1
    assert cands[0].kind == "procedure"
    assert cands[0].confirmation_state == "unconfirmed"
    assert cands[0].origin == "derived_from_agent_trace"
    assert cands[0].metadata["occurrence_count"] >= PLAN_REVISION_MIN


# --- §9.4 row: user_steer → Rule/Counterexample (1 narrow, 2+ broad) ---


def test_user_steer_one_narrow_counterexample() -> None:
    s = _sess(
        {"event": "user_goal", "payload": {"text": "write design"}},
        {"event": "user_steer", "payload": {"steer": "不要写实现细节"}},
        session_uid="steer1",
    )
    report = compile_agent_sessions([s])
    cands = report.by_signal("user_steer")
    assert len(cands) == 1
    assert cands[0].scope == "narrow"
    assert cands[0].kind == "counterexample"


def test_user_steer_two_broad_rule() -> None:
    s1 = _sess(
        {"event": "user_steer", "payload": {"steer": "先列风险"}},
        session_uid="st1",
        task_uid="tt1",
    )
    s2 = _sess(
        {"event": "user_steer", "payload": {"steer": "风险放前面"}},
        session_uid="st2",
        task_uid="tt2",
    )
    report = compile_agent_sessions([s1, s2])
    cands = report.by_signal("user_steer")
    assert len(cands) == 1
    assert cands[0].scope == "broad"
    assert cands[0].kind == "rule"
    assert cands[0].evidence_grade == "multiple_independent_cases"


# --- §9.4 row: reject tool → Rule (≥3 same class) ---


def test_tool_reject_requires_three() -> None:
    sessions = []
    for i in range(TOOL_REJECT_MIN - 1):
        sessions.append(
            _sess(
                {
                    "event": "approval_decision",
                    "decision": "reject",
                    "tool": "shell.run",
                    "tool_class": "shell",
                },
                session_uid=f"rj{i}",
                task_uid=f"tr{i}",
            )
        )
    assert compile_agent_sessions(sessions).by_signal("tool_reject") == []

    sessions.append(
        _sess(
            {
                "event": "approval_decision",
                "decision": "rejected",
                "payload": {"tool": "shell.exec", "tool_class": "shell"},
            },
            session_uid="rjX",
            task_uid="trX",
        )
    )
    cands = compile_agent_sessions(sessions).by_signal("tool_reject")
    assert len(cands) == 1
    assert cands[0].kind == "rule"
    assert cands[0].metadata["tool_class"] == "shell"
    assert cands[0].metadata["occurrence_count"] >= TOOL_REJECT_MIN


# --- §9.4 row: accept + small edit → transfer evidence ---


def test_accept_small_edit_transfer_evidence() -> None:
    s = _sess(
        {
            "event": "user_feedback",
            "accepted": True,
            "rating": 5,
            "metrics": {"len_before": 1000, "len_after": 980},
            "experience_uids": ["exp_rule_a"],
            "used_experience_uids": ["exp_rule_a"],
        },
        session_uid="xfer1",
    )
    report = compile_agent_sessions([s])
    cands = report.by_signal("transfer_evidence")
    assert len(cands) == 1
    assert cands[0].kind == "transfer_evidence"
    assert cands[0].evidence_grade == "transfer_verified"
    assert cands[0].metadata["one_success_not_proof_for_all"] is True
    assert "exp_rule_a" in cands[0].metadata["experience_uids"]
    assert report.observations
    assert report.observations[0].motive_inferred is False


# --- §9.4 row: heavy edit → ChangeObservation / quality preference ---


def test_heavy_edit_change_observation() -> None:
    s = _sess(
        {
            "event": "user_feedback",
            "accepted": False,
            "metrics": {"edit_ratio": 0.55, "len_before": 2000, "len_after": 900},
        },
        session_uid="hedit1",
    )
    report = compile_agent_sessions([s])
    cands = report.by_signal("heavy_edit")
    assert len(cands) == 1
    assert cands[0].kind == "change_observation"
    assert cands[0].change_observation is not None
    assert cands[0].change_observation.motive_inferred is False
    assert "rubric" in cands[0].metadata["suggested_kinds"]


# --- §9.4 row: failure/abandon → analysis blocked until user reason ---


def test_task_failure_blocked_without_user_reason() -> None:
    s = _sess(
        {"event": "session_end", "status": "abandoned"},
        session_uid="fail1",
    )
    report = compile_agent_sessions([s])
    cands = report.by_signal("task_failure")
    assert len(cands) == 1
    assert cands[0].kind == "failure_analysis"
    assert cands[0].blocked is True
    assert cands[0].block_reason == "needs_user_confirmed_failure_reason"
    assert cands[0].metadata["affects_experience"] is False


def test_task_failure_unblocked_with_user_reason() -> None:
    s = _sess(
        {
            "event": "session_end",
            "status": "failed",
            "failure_category": "missing_constraint",
            "user_reason": "漏了 Windows 兼容约束",
        },
        session_uid="fail2",
    )
    cands = compile_agent_sessions([s]).by_signal("task_failure")
    assert len(cands) == 1
    assert cands[0].blocked is False
    assert cands[0].metadata["affects_experience"] is True
    assert "missing_constraint" in cands[0].metadata["categories"]


# --- §9.4 behavior tendency: n≥8 and span≥14d ---


def test_behavior_tendency_requires_n_and_span() -> None:
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    # Too few
    few = [
        BehaviorEvent(
            ev_uid=f"ev{i}",
            ts=(now - timedelta(days=i)).isoformat(),
            cat="review",
            kind="proposal_edited",
            metrics={"len_before": 1000, "len_after": 650},
            ctx={"task_type": "technical_design"},
        )
        for i in range(5)
    ]
    assert compile_behavior_stats(few).by_signal("behavior_tendency") == []

    # Enough n but short span
    short = [
        BehaviorEvent(
            ev_uid=f"evs{i}",
            ts=(now - timedelta(hours=i)).isoformat(),
            cat="review",
            kind="proposal_edited",
            metrics={"len_before": 1000, "len_after": 650},
            ctx={"task_type": "technical_design"},
        )
        for i in range(BEHAVIOR_MIN_N)
    ]
    assert compile_behavior_stats(short).by_signal("behavior_tendency") == []

    ok = [
        BehaviorEvent(
            ev_uid=f"evo{i}",
            ts=(now - timedelta(days=i * 2)).isoformat(),
            cat="review",
            kind="proposal_edited",
            metrics={"len_before": 1000, "len_after": 650},
            ctx={"task_type": "technical_design"},
        )
        for i in range(BEHAVIOR_MIN_N)
    ]
    # span = (n-1)*2 days ≥ 14 when n≥8
    assert (BEHAVIOR_MIN_N - 1) * 2 >= BEHAVIOR_MIN_SPAN_DAYS
    cands = compile_behavior_stats(ok).by_signal("behavior_tendency")
    assert len(cands) == 1
    assert cands[0].origin == "derived_from_behavior"
    assert cands[0].kind == "rule"
    assert cands[0].metadata["statistical_only"] is True
    assert cands[0].metadata["n"] >= BEHAVIOR_MIN_N
    assert cands[0].confirmation_state == "unconfirmed"


# --- §9.5 publish gate ---


def test_unconfirmed_lifecycle_proposed(tmp_path: Path) -> None:
    sess_dir = tmp_path / "raw" / "agent-sessions"
    sess_dir.mkdir(parents=True)
    lines = [
        {
            "event": "user_steer",
            "task_type": "report",
            "session_uid": "ws1",
            "payload": {"steer": "用表格"},
        }
    ]
    (sess_dir / "ws1.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
        encoding="utf-8",
    )
    report = ExperienceCompiler(tmp_path).compile(include_behavior=False)
    assert report.candidates
    for c in report.candidates:
        assert c.confirmation_state == "unconfirmed"
        assert c.lifecycle == "proposed"


def test_workspace_behavior_integration(tmp_path: Path) -> None:
    blog = BehaviorLog(tmp_path)
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    # Write via public API then rewrite timestamps for span (unit test fixture)
    shard = tmp_path / "raw" / "behavior" / "2026-09.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(BEHAVIOR_MIN_N):
        rows.append(
            {
                "ev_uid": f"evb{i}",
                "ts": (now - timedelta(days=i * 2)).isoformat(),
                "cat": "review",
                "kind": "proposal_edited",
                "metrics": {"len_before": 1800, "len_after": 1100},
                "ctx": {"task_type": "technical_design"},
            }
        )
    shard.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    # Ensure BehaviorLog can read them
    assert len(blog.iter_events(category="review")) == BEHAVIOR_MIN_N
    report = ExperienceCompiler(tmp_path).compile(include_sessions=False)
    assert len(report.by_signal("behavior_tendency")) == 1
