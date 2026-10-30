"""Migrate legacy / flat memory dumps into §8.2 Memory pages (LW-13).

v0.11 had no five-kind memory store; this accepts common export shapes:
- ``.kw/memory/legacy_memories.json`` — list of {id, text, type|category, confirmed}
- ``.kw/memory/legacy_memories.jsonl`` — one object per line

Idempotent: skips UIDs already present in MemoryStore.
Does not invent confirmations for unconfirmed rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from localwiki.memory.store import MemoryRecord, MemoryStore

LEGACY_JSON = Path(".kw") / "memory" / "legacy_memories.json"
LEGACY_JSONL = Path(".kw") / "memory" / "legacy_memories.jsonl"

_TYPE_MAP = {
    "preference": "preference",
    "pref": "preference",
    "semantic": "semantic",
    "fact": "semantic",
    "procedural": "procedural",
    "procedure": "procedural",
    "habit": "procedural",
    "relational": "relational",
    "relation": "relational",
    "episodic": "episodic",
    "episode": "episodic",
    "experience": "procedural",  # soft map; real experiences belong in §9
}


@dataclass
class LegacyMigrateReport:
    migrated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    uids: list[str] = field(default_factory=list)


def _normalize_kind(raw: Any) -> str:
    key = str(raw or "semantic").strip().lower()
    return _TYPE_MAP.get(key, "semantic")


def _item_to_record(item: dict[str, Any]) -> MemoryRecord:
    uid = str(item.get("id") or item.get("uid") or "")
    if not uid:
        uid = f"legacy_{abs(hash(str(item))) % (10**10)}"
    if not uid.startswith(("mem_", "ep_", "legacy_")):
        uid = f"mem_{uid}"
    kind = _normalize_kind(item.get("type") or item.get("category") or item.get("kind"))
    text = str(item.get("text") or item.get("statement") or item.get("content") or "")
    confirmed = bool(item.get("confirmed") or item.get("confirmation_state") == "user_confirmed")
    return MemoryRecord(
        uid=uid if kind != "episodic" else uid.replace("mem_", "ep_", 1),
        kind=kind,
        title=text[:80] or uid,
        statement=text,
        origin="observed",
        confirmation_state="user_confirmed" if confirmed else "proposed",
        risk_tier="T0" if kind == "preference" and confirmed else "T1",
        classification="private",
        body=str(item.get("body") or ""),
        evidence_refs=[str(x) for x in (item.get("evidence_refs") or [])],
    )


def _load_legacy_items(workspace: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    jpath = workspace / LEGACY_JSON
    if jpath.is_file():
        data = json.loads(jpath.read_text(encoding="utf-8"))
        if isinstance(data, list):
            items.extend(x for x in data if isinstance(x, dict))
        elif isinstance(data, dict) and isinstance(data.get("memories"), list):
            items.extend(x for x in data["memories"] if isinstance(x, dict))
    jlpath = workspace / LEGACY_JSONL
    if jlpath.is_file():
        for line in jlpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                items.append(obj)
    return items


def migrate_legacy_memories(workspace: Path | str) -> LegacyMigrateReport:
    workspace = Path(workspace)
    store = MemoryStore(workspace)
    store.ensure_dirs()
    report = LegacyMigrateReport()
    existing = {r.uid for r in store.list()}

    for item in _load_legacy_items(workspace):
        try:
            rec = _item_to_record(item)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(str(exc))
            continue
        if rec.uid in existing:
            report.skipped += 1
            continue
        store.save(rec)
        existing.add(rec.uid)
        report.migrated += 1
        report.uids.append(rec.uid)

    marker = workspace / ".kw" / "memory" / ".legacy_migrated"
    if report.migrated or report.skipped:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"migrated_uids": report.uids}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report
