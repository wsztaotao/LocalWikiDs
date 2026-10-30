"""Nightly memory consolidate + forget/cascade (design §8.6 / §8.8).

Consolidate emits proposals and lifecycle updates — never auto-confirms inferred
memories. Delete clears FTS + vectors and marks dependents ``needs_review``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from localwiki.memory.dedup import normalize_statement
from localwiki.memory.embed import EmbeddingIndex
from localwiki.memory.store import MemoryRecord, MemoryStore

ForgetMode = Literal["disable", "delete", "purge"]


def _now(now: datetime | None = None) -> datetime:
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    return n


def _uid(prefix: str = "memc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class ConsolidateReport:
    episode_candidates: int = 0
    merge_proposals: int = 0
    dormant_uids: list[str] = field(default_factory=list)
    expired_uids: list[str] = field(default_factory=list)
    conflict_pairs: list[tuple[str, str]] = field(default_factory=list)
    experience_candidates: int = 0
    archived_episodes: int = 0
    proposals: list[MemoryRecord] = field(default_factory=list)
    auto_confirmed: int = 0  # must remain 0 for inferred upgrades


@dataclass
class ForgetResult:
    ok: bool
    uid: str
    mode: ForgetMode
    cleared_fts: bool = False
    cleared_vectors: bool = False
    dependents_flagged: list[str] = field(default_factory=list)
    audit_placeholder: dict[str, Any] | None = None
    detail: str = ""


class FtsIndex:
    """Lightweight lexical index stand-in until SQLite FTS5 lands."""

    def __init__(self, workspace: Path) -> None:
        self.path = Path(workspace) / ".kw" / "cache" / "memory_fts.json"
        self._data: dict[str, str] = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {str(k): str(v) for k, v in raw.items()}
            except json.JSONDecodeError:
                self._data = {}

    def upsert(self, uid: str, text: str) -> None:
        self._data[uid] = text
        self._flush()

    def remove(self, uid: str) -> bool:
        existed = uid in self._data
        self._data.pop(uid, None)
        self._flush()
        return existed

    def uids(self) -> set[str]:
        return set(self._data)

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class DependencyIndex:
    """Tracks experience/style objects that reference a memory UID."""

    def __init__(self, workspace: Path) -> None:
        self.path = Path(workspace) / ".kw" / "cache" / "memory_deps.json"
        self._data: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except json.JSONDecodeError:
                self._data = {}

    def link(self, dep_uid: str, memory_uid: str, *, kind: str) -> None:
        self._data[dep_uid] = {
            "memory_uid": memory_uid,
            "kind": kind,
            "status": "active",
        }
        self._flush()

    def get(self, dep_uid: str) -> dict[str, Any]:
        return dict(self._data.get(dep_uid) or {})

    def flag_for_memory(self, memory_uid: str) -> list[str]:
        flagged: list[str] = []
        for dep_uid, meta in self._data.items():
            if meta.get("memory_uid") == memory_uid:
                meta["status"] = "needs_review"
                flagged.append(dep_uid)
        if flagged:
            self._flush()
        return flagged

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def consolidate_nightly(
    store: MemoryStore,
    *,
    now: datetime | None = None,
) -> ConsolidateReport:
    """Run §8.6 steps; writes lifecycle updates, returns proposals (not confirmed)."""
    now = _now(now)
    report = ConsolidateReport()
    memories = store.list()

    # 1. Episode consolidation → T1 semantic candidates (proposed only)
    episodes = [m for m in memories if m.kind == "episodic"]
    themes: dict[str, list[MemoryRecord]] = {}
    for ep in episodes:
        key = normalize_statement(ep.statement)[:24] or ep.uid
        # coarse theme: shared bigrams head
        theme = re.sub(r"\d+", "", key)[:16] or key
        themes.setdefault(theme, []).append(ep)
    for theme, group in themes.items():
        if len(group) < 2:
            continue
        stmt = group[0].statement
        # Prefer a stable summary without inventing new facts
        cand = MemoryRecord(
            uid=_uid("mem"),
            kind="semantic",
            title=stmt[:80],
            statement=stmt,
            origin="consolidated",
            confirmation_state="proposed",  # never auto-confirm
            risk_tier="T1",
            classification="private",
            evidence_refs=[e.uid for e in group],
            body="夜间整合候选（待确认）",
            lifecycle="active",
        )
        report.proposals.append(cand)
        report.episode_candidates += 1
        # Mark source episodes as consolidated metadata via supersedes link on candidate only

    # 2. Merge duplicates among long-term (proposal count; actual merge is caller/Commit)
    by_norm: dict[str, list[MemoryRecord]] = {}
    for m in memories:
        if m.kind == "episodic":
            continue
        by_norm.setdefault(normalize_statement(m.statement), []).append(m)
    for group in by_norm.values():
        if len(group) > 1:
            report.merge_proposals += 1

    # 3. Decay → dormant
    for m in memories:
        if m.origin != "inferred":
            continue
        if m.importance >= 0.2:
            continue
        accessed = _parse_dt(m.last_accessed_at) or _parse_dt(m.created_at)
        if accessed is None:
            continue
        days = (now - accessed).total_seconds() / 86400.0
        if days >= 180 and m.lifecycle == "active":
            updated = m.with_updates(lifecycle="dormant")
            store.save(updated)
            report.dormant_uids.append(m.uid)

    # 4. Expire
    for m in memories:
        until = _parse_dt(m.valid_until)
        if until is not None and until < now and m.lifecycle != "expired":
            store.save(m.with_updates(lifecycle="expired"))
            report.expired_uids.append(m.uid)

    # 5. Procedural upgrade → experience candidate count (2+ independent evidence)
    for m in memories:
        if m.kind != "procedural":
            continue
        if len(m.evidence_refs) >= 2:
            report.experience_candidates += 1

    # 6. Archive old consolidated episodes (>90 days) — compress marker
    for ep in episodes:
        created = _parse_dt(ep.created_at)
        if created and (now - created).days >= 90:
            store.save(ep.with_updates(lifecycle="disabled", status="archived"))
            report.archived_episodes += 1

    assert report.auto_confirmed == 0
    return report


def forget_memory(
    store: MemoryStore,
    uid: str,
    *,
    mode: ForgetMode = "delete",
    confirm_purge: bool = False,
    fts: FtsIndex | None = None,
    embeddings: EmbeddingIndex | None = None,
    dependencies: DependencyIndex | None = None,
) -> ForgetResult:
    """Disable / delete / purge a memory with index cascade (§8.8)."""
    rec = store.get(uid)
    if rec is None and mode != "purge":
        return ForgetResult(ok=False, uid=uid, mode=mode, detail="not_found")

    if mode == "disable":
        if rec is None:
            return ForgetResult(ok=False, uid=uid, mode=mode, detail="not_found")
        store.save(rec.with_updates(lifecycle="disabled"))
        return ForgetResult(ok=True, uid=uid, mode=mode, detail="disabled")

    if mode == "purge" and not confirm_purge:
        return ForgetResult(
            ok=False, uid=uid, mode=mode, detail="purge_requires_confirm_purge=True"
        )

    cleared_fts = False
    cleared_vec = False
    flagged: list[str] = []

    if fts is not None:
        cleared_fts = fts.remove(uid)
    if embeddings is not None:
        cleared_vec = uid in embeddings.vectors
        embeddings.remove(uid)
    if dependencies is not None:
        flagged = dependencies.flag_for_memory(uid)

    purge_history = mode == "purge"
    store.delete(uid, purge_history=purge_history)

    audit = None
    if mode == "purge":
        audit_path = store.workspace / ".kw" / "history" / "memories" / "_purged.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit = {
            "uid": uid,
            "event": "purged",
            "ts": _now().isoformat(),
        }
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(audit, ensure_ascii=False) + "\n")

    return ForgetResult(
        ok=True,
        uid=uid,
        mode=mode,
        cleared_fts=cleared_fts,
        cleared_vectors=cleared_vec,
        dependents_flagged=flagged,
        audit_placeholder=audit,
        detail="deleted" if mode == "delete" else "purged",
    )
