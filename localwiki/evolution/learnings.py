"""Learning records compatible with self-improvement Skill format (§23.6).

Files under ``.kw/learnings/``:
  LEARNINGS.md  ERRORS.md  FEATURE_REQUESTS.md  HEALS.md

Record-only for LW-39 — no reflection, aggregation, or proposals.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

LearningKind = Literal["LRN", "ERR", "FEAT", "HEAL"]
LearningStatus = Literal[
    "pending",
    "in_progress",
    "resolved",
    "wont_fix",
    "promoted",
    "promoted_to_skill",
]

AREAS = frozenset(
    {
        "wiki",
        "memory",
        "experience",
        "style",
        "agent",
        "tools",
        "skills",
        "routing",
        "ui",
        "integration",
    }
)

FILE_FOR_KIND: dict[str, str] = {
    "LRN": "LEARNINGS.md",
    "ERR": "ERRORS.md",
    "FEAT": "FEATURE_REQUESTS.md",
    "HEAL": "HEALS.md",
}

_ENTRY_HEADER_RE = re.compile(
    r"^## \[(LRN|ERR|FEAT|HEAL)-(\d{8})-(\d+)\]\s+(\S+)\s*$",
    re.M,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _date_stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


@dataclass
class LearningRecord:
    kind: LearningKind
    category: str  # best_practice | correction | quality | knowledge_gap | ...
    summary: str
    details: str = ""
    suggested_action: str = ""
    area: str = "agent"
    priority: str = "medium"
    status: LearningStatus | str = "pending"
    source: str = "runtime_hook"
    related: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    pattern_key: str = ""
    recurrence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    distinct_tasks: int = 1
    evidence: list[str] = field(default_factory=list)
    classification: str = "normal"
    record_id: str = ""  # e.g. LRN-20261012-004
    see_also: list[str] = field(default_factory=list)
    metadata_extra: dict[str, Any] = field(default_factory=dict)

    def ensure_ids(self) -> None:
        if not self.first_seen:
            self.first_seen = _now_iso()
        if not self.last_seen:
            self.last_seen = self.first_seen
        if self.area not in AREAS:
            self.area = "agent"

    def to_markdown(self) -> str:
        self.ensure_ids()
        rid = self.record_id or f"{self.kind}-{_date_stamp()}-000"
        lines = [
            f"## [{rid}] {self.category}",
            "",
            f"**Logged**: {self.last_seen}",
            f"**Priority**: {self.priority}",
            f"**Status**: {self.status}",
            f"**Area**: {self.area}",
            "",
            "### Summary",
            self.summary.strip() or "(empty)",
            "",
            "### Details",
            (self.details.strip() or "(none)"),
            "",
            "### Suggested Action",
            (self.suggested_action.strip() or "(none)"),
            "",
            "### Metadata",
            f"- Source: {self.source}",
            f"- Related: {', '.join(self.related) if self.related else '(none)'}",
            f"- Tags: {', '.join(self.tags) if self.tags else '(none)'}",
            f"- Pattern-Key: {self.pattern_key or '(none)'}",
            f"- Recurrence-Count: {self.recurrence_count}",
            f"- First-Seen: {self.first_seen}",
            f"- Last-Seen: {self.last_seen}",
            f"- Distinct-Tasks: {self.distinct_tasks}",
            f"- Evidence: {', '.join(self.evidence) if self.evidence else '(none)'}",
            f"- Classification: {self.classification}",
        ]
        if self.see_also:
            lines.append(f"- See Also: {', '.join(self.see_also)}")
        for k, v in self.metadata_extra.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        self.ensure_ids()
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "category": self.category,
            "summary": self.summary,
            "details": self.details,
            "suggested_action": self.suggested_action,
            "area": self.area,
            "priority": self.priority,
            "status": self.status,
            "source": self.source,
            "related": list(self.related),
            "tags": list(self.tags),
            "pattern_key": self.pattern_key,
            "recurrence_count": self.recurrence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "distinct_tasks": self.distinct_tasks,
            "evidence": list(self.evidence),
            "classification": self.classification,
            "see_also": list(self.see_also),
        }


def parse_learnings_markdown(text: str) -> list[dict[str, Any]]:
    """Parse Skill-compatible learning markdown into dicts (for readback tests)."""
    entries: list[dict[str, Any]] = []
    parts = re.split(r"(?=^## \[)", text, flags=re.M)
    for part in parts:
        m = _ENTRY_HEADER_RE.search(part)
        if not m:
            continue
        kind, date, seq, category = m.group(1), m.group(2), m.group(3), m.group(4)
        def _field(name: str) -> str:
            mm = re.search(rf"\*\*{re.escape(name)}\*\*:\s*(.+)", part)
            return mm.group(1).strip() if mm else ""

        def _section(title: str) -> str:
            mm = re.search(
                rf"### {re.escape(title)}\n([\s\S]*?)(?=\n### |\n## |\Z)",
                part,
            )
            return mm.group(1).strip() if mm else ""

        def _meta(label: str) -> str:
            mm = re.search(rf"- {re.escape(label)}:\s*(.+)", part)
            return mm.group(1).strip() if mm else ""

        entries.append(
            {
                "record_id": f"{kind}-{date}-{seq}",
                "kind": kind,
                "category": category,
                "priority": _field("Priority"),
                "status": _field("Status"),
                "area": _field("Area"),
                "logged": _field("Logged"),
                "summary": _section("Summary"),
                "details": _section("Details"),
                "suggested_action": _section("Suggested Action"),
                "pattern_key": _meta("Pattern-Key"),
                "recurrence_count": _meta("Recurrence-Count"),
                "first_seen": _meta("First-Seen"),
                "last_seen": _meta("Last-Seen"),
                "distinct_tasks": _meta("Distinct-Tasks"),
                "evidence": _meta("Evidence"),
                "classification": _meta("Classification"),
                "source": _meta("Source"),
                "related": _meta("Related"),
                "proposal_id": _meta("Proposal-Id"),
                "promotion_target": _meta("Promotion-Target"),
            }
        )
    return entries


class LearningStore:
    """Append-only learning files with Pattern-Key dedup."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.dir = self.root / ".kw" / "learnings"
        self._seq: dict[str, int] = {"LRN": 0, "ERR": 0, "FEAT": 0, "HEAL": 0}
        self._ensure_files()
        self._load_seq_from_disk()

    def _ensure_files(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name in FILE_FOR_KIND.values():
            path = self.dir / name
            if not path.exists():
                path.write_text(
                    f"# {name.replace('.md', '')}\n\n",
                    encoding="utf-8",
                )

    def _load_seq_from_disk(self) -> None:
        for kind, fname in FILE_FOR_KIND.items():
            text = (self.dir / fname).read_text(encoding="utf-8")
            for m in _ENTRY_HEADER_RE.finditer(text):
                if m.group(1) == kind:
                    self._seq[kind] = max(self._seq[kind], int(m.group(3)))

    def _next_id(self, kind: LearningKind) -> str:
        self._seq[kind] = self._seq.get(kind, 0) + 1
        return f"{kind}-{_date_stamp()}-{self._seq[kind]:03d}"

    def path_for(self, kind: LearningKind | str) -> Path:
        return self.dir / FILE_FOR_KIND[kind]

    def find_by_pattern_key(self, pattern_key: str) -> dict[str, Any] | None:
        if not pattern_key:
            return None
        for kind, fname in FILE_FOR_KIND.items():
            text = (self.dir / fname).read_text(encoding="utf-8")
            for entry in parse_learnings_markdown(text):
                if entry.get("pattern_key") == pattern_key:
                    entry["file"] = fname
                    return entry
        return None

    def append(self, record: LearningRecord) -> LearningRecord:
        """Write or bump recurrence for matching Pattern-Key. Summaries only — no private raw.

        ``classification=self_assessment`` does not increase Recurrence-Count
        (AT-EVO-04 / §23.7 — model self-eval is not countable).
        """
        record.ensure_ids()
        # Strip private-looking blobs from summary/details (best-effort)
        record.summary = _sanitize_summary(record.summary)
        record.details = _sanitize_summary(record.details)

        if record.pattern_key:
            existing = self.find_by_pattern_key(record.pattern_key)
            if existing:
                return self._bump_existing(existing, record)

        record.record_id = self._next_id(record.kind)  # type: ignore[arg-type]
        path = self.path_for(record.kind)
        with path.open("a", encoding="utf-8") as f:
            f.write(record.to_markdown())
            if not record.to_markdown().endswith("\n"):
                f.write("\n")
        return record

    def _bump_existing(
        self,
        existing: dict[str, Any],
        incoming: LearningRecord,
    ) -> LearningRecord:
        """Increase Recurrence-Count / Distinct-Tasks / Last-Seen when countable."""
        fname = existing["file"]
        path = self.dir / fname
        text = path.read_text(encoding="utf-8")
        rid = existing["record_id"]
        try:
            old_count = int(existing.get("recurrence_count") or "1")
        except ValueError:
            old_count = 1
        try:
            old_tasks = int(existing.get("distinct_tasks") or "1")
        except ValueError:
            old_tasks = 1

        now = _now_iso()
        countable = str(incoming.classification or "") not in (
            "self_assessment",
            "model_self_assessment",
        )
        new_count = old_count + 1 if countable else old_count

        # Distinct-Tasks: union Related task ids
        existing_related = str(existing.get("related") or "")
        rel_parts = {
            p.strip()
            for p in existing_related.split(",")
            if p.strip() and p.strip() != "(none)"
        }
        for r in incoming.related:
            if r:
                rel_parts.add(r)
        new_tasks = max(old_tasks, len(rel_parts)) if rel_parts else old_tasks
        if countable and incoming.related:
            # At least bump by one new task id when Related introduces unseen ids
            prev = {
                p.strip()
                for p in existing_related.split(",")
                if p.strip() and p.strip() != "(none)"
            }
            if any(r not in prev for r in incoming.related if r):
                new_tasks = max(new_tasks, old_tasks + 1, len(rel_parts))

        if countable and new_count != old_count:
            pattern = re.compile(
                rf"(## \[{re.escape(rid)}\][\s\S]*?- Recurrence-Count: ){old_count}"
            )
            text2, n = pattern.subn(rf"\g<1>{new_count}", text, count=1)
            if n:
                text = text2
        if new_tasks != old_tasks:
            text = re.sub(
                rf"(## \[{re.escape(rid)}\][\s\S]*?- Distinct-Tasks: ){old_tasks}\b",
                rf"\g<1>{new_tasks}",
                text,
                count=1,
            )
        if rel_parts:
            joined = ", ".join(sorted(rel_parts))
            text = re.sub(
                rf"(## \[{re.escape(rid)}\][\s\S]*?- Related: )[^\n]+",
                rf"\g<1>{joined}",
                text,
                count=1,
            )
        text = re.sub(
            rf"(## \[{re.escape(rid)}\][\s\S]*?- Last-Seen: )[^\n]+",
            rf"\g<1>{now}",
            text,
            count=1,
        )
        note = (
            f"\n<!-- bump {rid} pattern={incoming.pattern_key} "
            f"related={','.join(incoming.related)} countable={countable} at {now} -->\n"
        )
        path.write_text(text + note, encoding="utf-8")
        incoming.record_id = rid
        incoming.recurrence_count = new_count
        incoming.distinct_tasks = new_tasks
        incoming.last_seen = now
        incoming.first_seen = existing.get("first_seen") or incoming.first_seen
        return incoming

    def update_record_fields(self, record_id: str, fields: dict[str, str]) -> bool:
        """Update **Field** lines or Metadata ``- Label:`` lines for one record."""
        kind = record_id.split("-", 1)[0] if record_id else ""
        if kind not in FILE_FOR_KIND:
            return False
        path = self.path_for(kind)  # type: ignore[arg-type]
        text = path.read_text(encoding="utf-8")
        header = f"## [{record_id}]"
        start = text.find(header)
        if start < 0:
            return False
        next_h = text.find("\n## [", start + len(header))
        end = next_h if next_h >= 0 else len(text)
        block = text[start:end]
        for key, value in fields.items():
            # Bold header fields: **Status**: ...
            bold = re.compile(rf"(\*\*{re.escape(key)}\*\*:\s*)[^\n]+")
            if bold.search(block):
                block = bold.sub(rf"\g<1>{value}", block, count=1)
                continue
            # Metadata dash lines
            meta = re.compile(rf"(- {re.escape(key)}:\s*)[^\n]+")
            if meta.search(block):
                block = meta.sub(rf"\g<1>{value}", block, count=1)
            else:
                # Append under Metadata section
                if "### Metadata" in block:
                    block = block.rstrip() + f"\n- {key}: {value}\n"
                else:
                    block = block.rstrip() + f"\n\n### Metadata\n- {key}: {value}\n"
        text = text[:start] + block + text[end:]
        path.write_text(text, encoding="utf-8")
        return True

    def list_records(self, kind: LearningKind | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        kinds = [kind] if kind else list(FILE_FOR_KIND.keys())
        for k in kinds:
            text = self.path_for(k).read_text(encoding="utf-8")  # type: ignore[arg-type]
            out.extend(parse_learnings_markdown(text))
        return out


def _sanitize_summary(text: str, max_chars: int = 2000) -> str:
    t = (text or "").strip()
    # Drop obvious secrets
    t = re.sub(r"(?i)(api[_-]?key|password|token)\s*[:=]\s*\S+", r"\1=[redacted]", t)
    if len(t) > max_chars:
        t = t[: max_chars - 1] + "…"
    return t


def make_err(
    *,
    summary: str,
    details: str = "",
    area: str = "tools",
    pattern_key: str = "",
    related: Sequence[str] | None = None,
    evidence: Sequence[str] | None = None,
    priority: str = "medium",
) -> LearningRecord:
    return LearningRecord(
        kind="ERR",
        category="error",
        summary=summary,
        details=details,
        area=area,
        pattern_key=pattern_key or f"err.{uuid.uuid4().hex[:8]}",
        related=list(related or []),
        evidence=list(evidence or ["tool_result"]),
        priority=priority,
        source="runtime_hook",
    )


def make_lrn(
    *,
    category: str,
    summary: str,
    details: str = "",
    area: str = "agent",
    pattern_key: str = "",
    related: Sequence[str] | None = None,
    evidence: Sequence[str] | None = None,
) -> LearningRecord:
    return LearningRecord(
        kind="LRN",
        category=category,
        summary=summary,
        details=details,
        area=area,
        pattern_key=pattern_key or f"lrn.{uuid.uuid4().hex[:8]}",
        related=list(related or []),
        evidence=list(evidence or ["user_message"]),
        source="runtime_hook",
    )
