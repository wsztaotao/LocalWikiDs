"""Generate wiki/index.md and wiki/log.md from committed results (§5.7).

Both files are program-generated. Failed tasks never enter the log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Literal

LogOp = Literal["创建", "更新", "合并", "废弃", "体检", "回写"]
CommitStatus = Literal["succeeded", "failed", "cancelled"]

CATEGORY_DIRS = {
    "Concept": "concepts",
    "Topic": "topics",
    "Entity": "entities",
    "Decision": "decisions",
    "Question": "questions",
    "Source Summary": "references",
    "Memory": "memories",
    "Persona Trait": "profiles/style",
}


@dataclass(frozen=True)
class IndexEntry:
    page_uid: str
    title: str
    page_type: str
    path: str
    summary: str = ""
    category: str = ""


@dataclass(frozen=True)
class LogEntry:
    op: LogOp
    summary: str
    page_path: str
    page_title: str
    committed_at: datetime
    status: CommitStatus = "succeeded"
    task_uid: str = ""


@dataclass
class CommitEvent:
    """One commit attempt from CommitService (or test double)."""

    op: LogOp
    summary: str
    page_path: str
    page_title: str
    status: CommitStatus
    committed_at: datetime | None = None
    task_uid: str = ""

    def to_log_entry(self) -> LogEntry | None:
        if self.status != "succeeded":
            return None
        return LogEntry(
            op=self.op,
            summary=self.summary,
            page_path=self.page_path,
            page_title=self.page_title,
            committed_at=self.committed_at or datetime.now(),
            status="succeeded",
            task_uid=self.task_uid,
        )


def category_for(page_type: str, path: str = "") -> str:
    if path:
        parts = path.replace("\\", "/").split("/")
        if parts and parts[0]:
            return parts[0]
    return CATEGORY_DIRS.get(page_type, "misc")


def render_root_index(entries: Iterable[IndexEntry]) -> str:
    """Root wiki/index.md — okf_version only; category portals + highlights."""
    items = list(entries)
    by_cat: dict[str, list[IndexEntry]] = {}
    for e in items:
        cat = e.category or category_for(e.page_type, e.path)
        by_cat.setdefault(cat, []).append(e)

    lines = [
        "---",
        'okf_version: "0.2"',
        "---",
        "",
        "# Wiki Index",
        "",
        "## 分类入口",
        "",
    ]
    for cat in sorted(by_cat):
        lines.append(f"- [{cat}]({cat}/index.md)（{len(by_cat[cat])}）")
    lines.append("")
    lines.append("## 重点页")
    lines.append("")
    # Highlights: first 10 by title
    highlights = sorted(items, key=lambda e: e.title)[:10]
    if not highlights:
        lines.append("（暂无页面）")
    else:
        for e in highlights:
            href = e.path if e.path.endswith(".md") else e.path + ".md"
            summary = e.summary.strip() or e.page_type
            lines.append(f"- [{e.title}]({href}) — {summary}")
    lines.append("")
    return "\n".join(lines)


def render_category_index(category: str, entries: Iterable[IndexEntry]) -> str:
    items = sorted(entries, key=lambda e: e.title)
    lines = [
        "---",
        'okf_version: "0.2"',
        "---",
        "",
        f"# {category}",
        "",
    ]
    if not items:
        lines.append("（空）")
    else:
        for e in items:
            # Relative within category folder
            name = Path(e.path.replace("\\", "/")).name
            if not name.endswith(".md"):
                name = name + ".md"
            summary = e.summary.strip() or e.page_type
            lines.append(f"- [{e.title}]({name}) — {summary}")
    lines.append("")
    return "\n".join(lines)


def render_log(entries: Iterable[LogEntry]) -> str:
    """log.md: newest date first; only succeeded commits."""
    succeeded = [e for e in entries if e.status == "succeeded"]
    by_day: dict[date, list[LogEntry]] = {}
    for e in succeeded:
        d = e.committed_at.date()
        by_day.setdefault(d, []).append(e)

    lines = [
        "---",
        'okf_version: "0.2"',
        "---",
        "",
        "# Wiki Log",
        "",
    ]
    if not by_day:
        lines.append("（暂无提交记录）")
        lines.append("")
        return "\n".join(lines)

    for d in sorted(by_day.keys(), reverse=True):
        lines.append(f"## {d.isoformat()}")
        lines.append("")
        day_items = sorted(
            by_day[d], key=lambda e: e.committed_at, reverse=True
        )
        for e in day_items:
            href = e.page_path
            lines.append(
                f"- **{e.op}**：{e.summary}（[{e.page_title}]({href})）"
            )
        lines.append("")
    return "\n".join(lines)


def filter_log_events(events: Iterable[CommitEvent]) -> list[LogEntry]:
    """Convert commit events to log entries; drop failed/cancelled."""
    out: list[LogEntry] = []
    for ev in events:
        entry = ev.to_log_entry()
        if entry is not None:
            out.append(entry)
    return out


@dataclass
class IndexLogWriter:
    """Write generated index/log into a wiki directory (via explicit write).

    Callers that must go through CommitService should pass file contents
    into a ChangeSet instead of using write_to_wiki directly in production.
    For LW-08, direct write of *generated* index/log is allowed as the
    program-owned maintenance path described in §5.7.
    """

    wiki_dir: Path

    def build_indexes(self, entries: list[IndexEntry]) -> dict[str, str]:
        """Return mapping of relative path → file content."""
        files: dict[str, str] = {"index.md": render_root_index(entries)}
        by_cat: dict[str, list[IndexEntry]] = {}
        for e in entries:
            cat = e.category or category_for(e.page_type, e.path)
            by_cat.setdefault(cat, []).append(e)
        for cat, group in by_cat.items():
            files[f"{cat}/index.md"] = render_category_index(cat, group)
        return files

    def build_log(self, events: list[CommitEvent]) -> str:
        return render_log(filter_log_events(events))

    def write_indexes(self, entries: list[IndexEntry]) -> list[Path]:
        written: list[Path] = []
        for rel, content in self.build_indexes(entries).items():
            path = self.wiki_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append(path)
        return written

    def write_log(self, events: list[CommitEvent]) -> Path:
        path = self.wiki_dir / "log.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.build_log(events), encoding="utf-8", newline="\n")
        return path

    def regenerate(
        self,
        entries: list[IndexEntry],
        events: list[CommitEvent],
    ) -> dict[str, Path]:
        paths = {str(p.relative_to(self.wiki_dir)).replace("\\", "/"): p for p in self.write_indexes(entries)}
        log_path = self.write_log(events)
        paths["log.md"] = log_path
        return paths
