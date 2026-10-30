"""LW-46: developer backlog, desensitize, import .learnings, evolution UI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.evolution.backlog import (  # noqa: E402
    BacklogSync,
    BacklogWriter,
    DevTicket,
    LearningsImportExport,
    redact_text,
)
from localwiki.evolution.learnings import LearningRecord, LearningStore  # noqa: E402


def test_redact_secrets_and_emails() -> None:
    raw = "api_key=sk-live-SECRET password=hunter2 user@example.com C:\\Users\\Alice\\proj"
    out = redact_text(raw)
    assert "sk-live-SECRET" not in out
    assert "hunter2" not in out
    assert "user@example.com" not in out
    assert "[redacted]" in out or "[email]" in out
    assert "[user]" in out


def test_feature_and_bug_tickets_desensitized(tmp_path: Path) -> None:
    writer = BacklogWriter(tmp_path)
    feat = writer.add_feature(
        DevTicket(
            ticket_id="",
            kind="feature",
            title="需要导出 token=abc123",
            steps_to_reproduce=["call with password=secret"],
            log_excerpt="Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload",
            suggested_fix="add export",
            pattern_key="feat.export",
        )
    )
    assert feat.redacted
    text = (tmp_path / "docs" / "FEATURE_REQUESTS.md").read_text(encoding="utf-8")
    assert "abc123" not in text
    assert "password=secret" not in text or "[redacted]" in text
    assert "Bearer eyJ" not in text

    bug = writer.add_bug(
        DevTicket(
            ticket_id="",
            kind="bug",
            title="内部缺陷 crash",
            log_excerpt="api_key=xyz contact admin@corp.io",
            frequency="3",
            pattern_key="agent.internal.crash",
        )
    )
    bug_text = (tmp_path / "docs" / "BUG_REPORTS.md").read_text(encoding="utf-8")
    assert "xyz" not in bug_text
    assert "admin@corp.io" not in bug_text
    assert bug.ticket_id.startswith("BUG-")


def test_sync_err_to_bug_when_internal(tmp_path: Path) -> None:
    store = LearningStore(tmp_path)
    store.append(
        LearningRecord(
            kind="ERR",
            category="error",
            summary="内部缺陷：空指针",
            details="执行问题 in apply_patch handler",
            area="agent",
            pattern_key="agent.internal.npe",
            recurrence_count=3,
            distinct_tasks=2,
            related=["task_1", "task_2"],
            evidence=["tool_result"],
            classification="执行问题",
        )
    )
    store.append(
        LearningRecord(
            kind="FEAT",
            category="feature_request",
            summary="希望支持批量导出",
            area="ui",
            pattern_key="feat.batch_export",
            evidence=["user_message"],
        )
    )
    tickets = BacklogSync(tmp_path).sync(
        root_cause_by_pattern={"agent.internal.npe": "执行问题"}
    )
    assert any(t.kind == "bug" for t in tickets)
    assert any(t.kind == "feature" for t in tickets)
    assert (tmp_path / "docs" / "BUG_REPORTS.md").read_text(encoding="utf-8")
    payload = BacklogSync(tmp_path).evolution_backlog()
    assert "features" in payload and "bugs" in payload


def test_import_external_learnings_dir(tmp_path: Path) -> None:
    external = tmp_path / "other" / ".learnings"
    external.mkdir(parents=True)
    (external / "LEARNINGS.md").write_text(
        """# LEARNINGS

## [LRN-20260101-001] best_practice

**Logged**: 2026-01-01T00:00:00+00:00
**Priority**: medium
**Status**: pending
**Area**: tools

### Summary
External lesson about timeouts

### Details
Imported from another project

### Suggested Action
Increase timeout

### Metadata
- Source: runtime_hook
- Related: (none)
- Tags: timeout
- Pattern-Key: tools.timeout.external
- Recurrence-Count: 1
- First-Seen: 2026-01-01
- Last-Seen: 2026-01-01
- Distinct-Tasks: 1
- Evidence: tool_result
- Classification: normal
""",
        encoding="utf-8",
    )
    (external / "ERRORS.md").write_text("# ERRORS\n\n", encoding="utf-8")
    (external / "FEATURE_REQUESTS.md").write_text("# FEATURE_REQUESTS\n\n", encoding="utf-8")
    (external / "HEALS.md").write_text("# HEALS\n\n", encoding="utf-8")

    dest = tmp_path / "workspace"
    dest.mkdir()
    ie = LearningsImportExport(dest)
    imported = ie.import_dir(external)
    assert imported
    assert all(r.source == "imported" for r in imported)
    entries = LearningStore(dest).list_records("LRN")
    assert any(e.get("source") == "imported" for e in entries)
    assert any("timeout" in (e.get("summary") or "") for e in entries)

    export_to = tmp_path / "export_out"
    ie.export_dir(export_to)
    assert (export_to / "LEARNINGS.md").exists()


def test_evolution_js_has_five_tabs() -> None:
    path = ROOT / "web" / "evolution.js"
    text = path.read_text(encoding="utf-8")
    for label in ("学习记录", "改进提案", "已应用", "效果趋势", "设置"):
        assert label in text
    assert "自进化" in text
    assert "filterLearnings" in text
    assert "rollback" in text
    assert 'data-group="能力"' in text or "能力" in text
