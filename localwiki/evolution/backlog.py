"""Developer backlog tickets + learnings import/export (§23.13–23.14 / LW-46).

LocalWiki never modifies its own source; FEAT/BUG tickets are for external agents.
Imported ``.learnings/`` entries are marked ``Source: imported``.
Tickets are redacted (no secrets / private raw).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from localwiki.evolution.learnings import (
    FILE_FOR_KIND,
    LearningRecord,
    LearningStore,
    parse_learnings_markdown,
)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|password|token|secret|authorization)\s*[:=]\s*\S+"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PATH_USER_RE = re.compile(r"(?i)([A-Z]:\\Users\\)[^\\/]+")
_HOME_RE = re.compile(r"(?i)(/home/)[^/\s]+")

INTERNAL_ROOT_CAUSES = frozenset({"执行问题", "内部缺陷", "execution", "internal_defect"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def redact_text(text: str, max_chars: int = 4000) -> str:
    """Desensitize ticket / log snippets (§23.13)."""
    t = text or ""
    t = _SECRET_RE.sub(r"\1=[redacted]", t)
    t = _EMAIL_RE.sub("[email]", t)
    t = _PATH_USER_RE.sub(r"\1[user]", t)
    t = _HOME_RE.sub(r"\1[user]", t)
    t = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "bearer [redacted]", t)
    if len(t) > max_chars:
        t = t[: max_chars - 1] + "…"
    return t


@dataclass
class DevTicket:
    ticket_id: str
    kind: str  # feature | bug
    title: str
    steps_to_reproduce: list[str] = field(default_factory=list)
    log_excerpt: str = ""
    frequency: str = ""
    suggested_fix: str = ""
    pattern_key: str = ""
    evidence: list[str] = field(default_factory=list)
    created_at: str = ""
    redacted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(self.steps_to_reproduce, 1)) or "(none)"
        return "\n".join(
            [
                f"## [{self.ticket_id}] {self.title}",
                "",
                f"**Kind**: {self.kind}",
                f"**Created**: {self.created_at or _now_iso()}",
                f"**Pattern-Key**: {self.pattern_key or '(none)'}",
                f"**Frequency**: {self.frequency or '(unknown)'}",
                f"**Redacted**: {str(self.redacted).lower()}",
                "",
                "### Steps to reproduce",
                steps,
                "",
                "### Log excerpt",
                self.log_excerpt or "(none)",
                "",
                "### Suggested fix",
                self.suggested_fix or "(none)",
                "",
                "### Evidence",
                ", ".join(self.evidence) if self.evidence else "(none)",
                "",
            ]
        )


class BacklogWriter:
    """Write desensitized tickets to docs/FEATURE_REQUESTS.md and docs/BUG_REPORTS.md."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.docs = self.root / "docs"
        self.docs.mkdir(parents=True, exist_ok=True)
        self.feat_path = self.docs / "FEATURE_REQUESTS.md"
        self.bug_path = self.docs / "BUG_REPORTS.md"
        for p, title in (
            (self.feat_path, "FEATURE_REQUESTS"),
            (self.bug_path, "BUG_REPORTS"),
        ):
            if not p.exists():
                p.write_text(f"# {title}\n\n", encoding="utf-8")

    def _append(self, path: Path, ticket: DevTicket) -> DevTicket:
        ticket.log_excerpt = redact_text(ticket.log_excerpt)
        ticket.title = redact_text(ticket.title, max_chars=200)
        ticket.suggested_fix = redact_text(ticket.suggested_fix, max_chars=1000)
        ticket.steps_to_reproduce = [redact_text(s, max_chars=500) for s in ticket.steps_to_reproduce]
        ticket.redacted = True
        if not ticket.created_at:
            ticket.created_at = _now_iso()
        with path.open("a", encoding="utf-8") as f:
            f.write(ticket.to_markdown())
            if not ticket.to_markdown().endswith("\n"):
                f.write("\n")
        return ticket

    def add_feature(self, ticket: DevTicket) -> DevTicket:
        ticket.kind = "feature"
        if not ticket.ticket_id:
            ticket.ticket_id = f"FEAT-{uuid.uuid4().hex[:8]}"
        return self._append(self.feat_path, ticket)

    def add_bug(self, ticket: DevTicket) -> DevTicket:
        ticket.kind = "bug"
        if not ticket.ticket_id:
            ticket.ticket_id = f"BUG-{uuid.uuid4().hex[:8]}"
        return self._append(self.bug_path, ticket)


def ticket_from_feat_record(rec: dict[str, Any] | LearningRecord) -> DevTicket:
    if isinstance(rec, LearningRecord):
        d = rec.to_dict()
    else:
        d = dict(rec)
    return DevTicket(
        ticket_id=f"FEAT-{uuid.uuid4().hex[:8]}",
        kind="feature",
        title=str(d.get("summary") or "feature request"),
        steps_to_reproduce=["See learning record details"],
        log_excerpt=str(d.get("details") or ""),
        frequency=f"recurrence={d.get('recurrence_count') or 1}",
        suggested_fix=str(d.get("suggested_action") or ""),
        pattern_key=str(d.get("pattern_key") or ""),
        evidence=[str(d.get("record_id") or "")],
    )


def ticket_from_err_record(rec: dict[str, Any], *, root_cause: str = "") -> DevTicket:
    return DevTicket(
        ticket_id=f"BUG-{uuid.uuid4().hex[:8]}",
        kind="bug",
        title=str(rec.get("summary") or "internal error"),
        steps_to_reproduce=[
            f"Trigger Pattern-Key {rec.get('pattern_key')}",
            "Observe repeated ERR with internal/execution root cause",
        ],
        log_excerpt=str(rec.get("details") or ""),
        frequency=f"recurrence={rec.get('recurrence_count')}; tasks={rec.get('distinct_tasks')}",
        suggested_fix=str(rec.get("suggested_action") or "Investigate internal defect"),
        pattern_key=str(rec.get("pattern_key") or ""),
        evidence=[str(rec.get("record_id") or ""), root_cause],
    )


class BacklogSync:
    """Scan learnings → developer tickets (no self-code mutation)."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.store = LearningStore(self.root)
        self.writer = BacklogWriter(self.root)

    def sync(
        self,
        *,
        root_cause_by_pattern: dict[str, str] | None = None,
    ) -> list[DevTicket]:
        root_cause_by_pattern = root_cause_by_pattern or {}
        out: list[DevTicket] = []
        for rec in self.store.list_records("FEAT"):
            out.append(self.writer.add_feature(ticket_from_feat_record(rec)))
        for rec in self.store.list_records("ERR"):
            try:
                count = int(rec.get("recurrence_count") or 1)
            except ValueError:
                count = 1
            pk = str(rec.get("pattern_key") or "")
            cause = root_cause_by_pattern.get(pk) or str(
                rec.get("classification") or rec.get("category") or ""
            )
            # Also peek details for root cause keywords
            blob = f"{cause} {rec.get('details') or ''} {rec.get('summary') or ''}"
            if count >= 3 and any(c in blob for c in INTERNAL_ROOT_CAUSES):
                out.append(self.writer.add_bug(ticket_from_err_record(rec, root_cause=cause)))
        return out

    def evolution_backlog(self) -> dict[str, Any]:
        """MCP-facing payload: desensitized open tickets."""
        feats = self.feat_path_text()
        bugs = self.bug_path_text()
        return {
            "features": redact_text(feats),
            "bugs": redact_text(bugs),
            "note": "LocalWiki does not modify its own source; review in Cursor/Codex/Claude Code.",
        }

    def feat_path_text(self) -> str:
        return self.writer.feat_path.read_text(encoding="utf-8")

    def bug_path_text(self) -> str:
        return self.writer.bug_path.read_text(encoding="utf-8")


class LearningsImportExport:
    """Import external ``.learnings/`` dirs; export workspace learnings."""

    VALID_FILES = frozenset(FILE_FOR_KIND.values())

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.store = LearningStore(self.root)

    def import_dir(self, source: Path | str) -> list[LearningRecord]:
        """Import Skill-compatible markdown; mark Source: imported. Skip illegal entries."""
        src = Path(source)
        # Accept either ``.learnings`` or ``learnings`` folder
        if src.is_dir() and src.name in (".learnings", "learnings"):
            base = src
        elif (src / ".learnings").is_dir():
            base = src / ".learnings"
        elif (src / "learnings").is_dir():
            base = src / "learnings"
        else:
            base = src
        imported: list[LearningRecord] = []
        for fname in self.VALID_FILES:
            path = base / fname
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for entry in parse_learnings_markdown(text):
                if not self._valid_entry(entry):
                    continue
                kind = entry.get("kind") or "LRN"
                rec = LearningRecord(
                    kind=kind,  # type: ignore[arg-type]
                    category=str(entry.get("category") or "imported"),
                    summary=redact_text(str(entry.get("summary") or "")),
                    details=redact_text(str(entry.get("details") or "")),
                    suggested_action=redact_text(str(entry.get("suggested_action") or "")),
                    area=str(entry.get("area") or "agent"),
                    priority=str(entry.get("priority") or "medium"),
                    status=str(entry.get("status") or "pending"),
                    source="imported",
                    pattern_key=str(entry.get("pattern_key") or ""),
                    evidence=["imported"],
                    classification="normal",
                    related=[],
                )
                # Avoid colliding pattern keys silently bumping local counts incorrectly:
                # prefix imported pattern keys if empty
                if not rec.pattern_key or rec.pattern_key == "(none)":
                    rec.pattern_key = f"imported.{uuid.uuid4().hex[:8]}"
                saved = self.store.append(rec)
                imported.append(saved)
        return imported

    def _valid_entry(self, entry: dict[str, Any]) -> bool:
        if not entry.get("record_id"):
            return False
        if not entry.get("summary") or entry.get("summary") == "(empty)":
            return False
        kind = entry.get("kind")
        return kind in FILE_FOR_KIND

    def export_dir(self, dest: Path | str) -> Path:
        dest_p = Path(dest)
        dest_p.mkdir(parents=True, exist_ok=True)
        for fname in self.VALID_FILES:
            src = self.store.dir / fname
            if src.exists():
                (dest_p / fname).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return dest_p


def skill_search_hint(summary: str) -> dict[str, Any]:
    """Optional Skill recommendation stub for FEAT (install still needs confirmation)."""
    # Lightweight keyword → suggestion; real search is skills_hub
    q = summary or ""
    hint = None
    if re.search(r"docx|word|表格", q, re.I):
        hint = {"query": "docx table", "note": "skill_search recommended; install needs user confirm"}
    elif re.search(r"pptx|幻灯", q, re.I):
        hint = {"query": "pptx", "note": "skill_search recommended; install needs user confirm"}
    return {"skill_search": hint}
