"""Context compaction — handoff summary with required fields (design §11.6 / AT-AGT-06).

Required handoff fields:
  goal, plan_status, decisions, modified_files (+ hashes), open_issues, approved_approvals.
Keep the most recent 6 turns verbatim. Raw trajectory is unchanged (session.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

REQUIRED_HANDOFF_FIELDS: tuple[str, ...] = (
    "goal",
    "plan_status",
    "decisions",
    "modified_files",
    "open_issues",
    "approved_approvals",
)

KEEP_RECENT_TURNS = 6


@dataclass
class HandoffSummary:
    goal: str = ""
    plan_status: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    modified_files: list[dict[str, str]] = field(default_factory=list)  # path, hash
    open_issues: list[str] = field(default_factory=list)
    approved_approvals: list[dict[str, Any]] = field(default_factory=list)
    omitted_turns: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "plan_status": list(self.plan_status),
            "decisions": list(self.decisions),
            "modified_files": list(self.modified_files),
            "open_issues": list(self.open_issues),
            "approved_approvals": list(self.approved_approvals),
            "omitted_turns": self.omitted_turns,
            "notes": self.notes,
        }

    def validate_required(self) -> list[str]:
        missing: list[str] = []
        data = self.to_dict()
        for key in REQUIRED_HANDOFF_FIELDS:
            if key not in data:
                missing.append(key)
                continue
            # Fields must be present (may be empty list/str) — key existence is enough;
            # but goal should be non-empty when known
            if key == "goal" and not str(data[key]).strip():
                missing.append("goal_empty")
        return missing

    def render(self) -> str:
        payload = self.to_dict()
        return (
            "[handoff summary]\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )


@dataclass
class CompactResult:
    messages: list[dict[str, Any]]
    handoff: HandoffSummary
    compacted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": list(self.messages),
            "handoff": self.handoff.to_dict(),
            "compacted": self.compacted,
        }


def _is_system(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "system"


def build_handoff(
    *,
    goal: str,
    plan_status: Sequence[dict[str, Any]] | None = None,
    decisions: Sequence[str] | None = None,
    modified_files: Sequence[dict[str, str]] | None = None,
    open_issues: Sequence[str] | None = None,
    approved_approvals: Sequence[dict[str, Any]] | None = None,
    omitted_turns: int = 0,
    notes: str = "",
) -> HandoffSummary:
    return HandoffSummary(
        goal=goal,
        plan_status=list(plan_status or []),
        decisions=list(decisions or []),
        modified_files=list(modified_files or []),
        open_issues=list(open_issues or []),
        approved_approvals=list(approved_approvals or []),
        omitted_turns=omitted_turns,
        notes=notes,
    )


def extract_hints_from_messages(
    messages: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Best-effort scrape of decisions / files / approvals from history."""
    decisions: list[str] = []
    modified: list[dict[str, str]] = []
    approvals: list[dict[str, Any]] = []
    issues: list[str] = []
    for m in messages:
        content = str(m.get("content") or "")
        if "decision:" in content.lower():
            decisions.append(content.strip()[:200])
        if m.get("role") == "tool" or "tool_result" in content:
            if "new_sha256" in content or "base_sha256" in content:
                try:
                    # crude path/hash extraction from JSON-ish content
                    if "path" in content:
                        modified.append({"path": "see_trace", "hash": "see_trace"})
                except Exception:
                    pass
        if "approved" in content.lower() and "risk" in content.lower():
            approvals.append({"note": content[:160]})
        if "error" in content.lower() or "failed" in content.lower():
            issues.append(content.strip()[:160])
    return {
        "decisions": decisions[:20],
        "modified_files": modified[:50],
        "approved_approvals": approvals[:20],
        "open_issues": issues[:20],
    }


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    goal: str,
    plan_status: Sequence[dict[str, Any]] | None = None,
    decisions: Sequence[str] | None = None,
    modified_files: Sequence[dict[str, str]] | None = None,
    open_issues: Sequence[str] | None = None,
    approved_approvals: Sequence[dict[str, Any]] | None = None,
    keep_recent: int = KEEP_RECENT_TURNS,
    force: bool = False,
) -> CompactResult:
    """Replace older turns with a handoff summary; keep system + last N turns."""
    if len(messages) <= keep_recent + 1 and not force:
        handoff = build_handoff(
            goal=goal,
            plan_status=plan_status,
            decisions=decisions,
            modified_files=modified_files,
            open_issues=open_issues,
            approved_approvals=approved_approvals,
            omitted_turns=0,
        )
        return CompactResult(messages=list(messages), handoff=handoff, compacted=False)

    head: list[dict[str, Any]] = []
    rest = list(messages)
    if rest and _is_system(rest[0]):
        head = [rest[0]]
        rest = rest[1:]

    if len(rest) <= keep_recent and not force:
        handoff = build_handoff(
            goal=goal,
            plan_status=plan_status,
            decisions=decisions,
            modified_files=modified_files,
            open_issues=open_issues,
            approved_approvals=approved_approvals,
            omitted_turns=0,
        )
        return CompactResult(messages=head + rest, handoff=handoff, compacted=False)

    recent = rest[-keep_recent:]
    omitted = rest[:-keep_recent] if keep_recent else rest
    hints = extract_hints_from_messages(omitted)

    handoff = build_handoff(
        goal=goal,
        plan_status=plan_status,
        decisions=list(decisions or hints["decisions"]),
        modified_files=list(modified_files or hints["modified_files"]),
        open_issues=list(open_issues or hints["open_issues"]),
        approved_approvals=list(approved_approvals or hints["approved_approvals"]),
        omitted_turns=len(omitted),
    )
    missing = handoff.validate_required()
    if missing and "goal_empty" in missing:
        handoff.goal = goal or "(unknown goal)"
        missing = handoff.validate_required()
    if missing:
        # Still emit, but mark notes — callers/tests assert fields exist
        handoff.notes = f"missing_optional:{','.join(missing)}"

    summary_msg = {"role": "system", "content": handoff.render()}
    return CompactResult(
        messages=head + [summary_msg] + recent,
        handoff=handoff,
        compacted=True,
    )
