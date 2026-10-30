"""Memory storage and page format (design §8.1–8.2).

Long-term kinds live as Markdown under ``wiki/memories/<kind>/``.
Episodic memories are JSONL under ``.kw/memory/episodes/``.

Version history is archived under ``.kw/history/memories/<uid>/``.
Until CommitService exists, ``MemoryStore.save`` writes via an injectable
adapter (default filesystem). Callers that already have CommitService should
pass ``writer`` that only stages ChangeSets — never bypass privacy defaults.
"""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

MemoryKind = Literal[
    "episodic", "semantic", "procedural", "preference", "relational"
]
MEMORY_KINDS: tuple[str, ...] = (
    "episodic",
    "semantic",
    "procedural",
    "preference",
    "relational",
)
LONG_TERM_KINDS: tuple[str, ...] = (
    "semantic",
    "procedural",
    "preference",
    "relational",
)

Origin = Literal["explicit_user", "observed", "inferred", "consolidated"]
ConfirmationState = Literal[
    "proposed", "user_confirmed", "auto_confirmed", "revoked", "superseded"
]
RiskTier = Literal["T0", "T1", "T2"]

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uid(prefix: str = "mem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        out: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip().strip("\"'")
        return out


def _dump_yaml(data: dict[str, Any]) -> str:
    try:
        import yaml

        return yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        ).strip()
    except Exception:
        # Minimal fallback
        lines: list[str] = []

        def emit(obj: Any, indent: int = 0) -> None:
            pad = "  " * indent
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        lines.append(f"{pad}{k}:")
                        emit(v, indent + 1)
                    elif v is None:
                        lines.append(f"{pad}{k}: null")
                    elif isinstance(v, bool):
                        lines.append(f"{pad}{k}: {'true' if v else 'false'}")
                    elif isinstance(v, (int, float)):
                        lines.append(f"{pad}{k}: {v}")
                    else:
                        lines.append(f"{pad}{k}: {v}")
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, (dict, list)):
                        lines.append(f"{pad}-")
                        emit(item, indent + 1)
                    else:
                        lines.append(f"{pad}- {item}")

        emit(data)
        return "\n".join(lines)


@dataclass
class MemoryRecord:
    uid: str
    kind: MemoryKind | str
    title: str = ""
    description: str = ""
    statement: str = ""
    subject: str = "user"
    applicability: dict[str, Any] = field(default_factory=dict)
    origin: Origin | str = "inferred"
    confirmation_state: ConfirmationState | str = "proposed"
    risk_tier: RiskTier | str = "T1"
    importance: float = 0.5
    classification: str = "private"
    scopes: list[str] = field(default_factory=lambda: ["personal"])
    created_at: str = ""
    last_accessed_at: str | None = None
    access_count: int = 0
    valid_until: str | None = None
    supersedes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    status: str = "stable"
    body: str = ""
    content_version: int = 1
    schema_version: int = 3
    lifecycle: str = "active"  # active | dormant | expired | disabled

    def __post_init__(self) -> None:
        if not self.uid:
            self.uid = _new_uid("ep" if self.kind == "episodic" else "mem")
        if not self.title:
            self.title = (self.statement or self.uid)[:80]
        if not self.created_at:
            self.created_at = _now_iso()

    def with_updates(self, **kwargs: Any) -> MemoryRecord:
        data = deepcopy(self.__dict__)
        data.update(kwargs)
        return MemoryRecord(**data)


def render_memory_page(rec: MemoryRecord) -> str:
    """Serialize a long-term memory to Markdown with YAML front matter (§8.2)."""
    fm: dict[str, Any] = {
        "type": "Memory",
        "title": rec.title,
        "description": rec.description or rec.statement,
        "status": rec.status,
        "kw": {
            "schema_version": rec.schema_version,
            "uid": rec.uid,
            "content_version": rec.content_version,
            "security": {
                "classification": rec.classification,
                "scopes": list(rec.scopes),
            },
            "memory": {
                "kind": rec.kind,
                "statement": rec.statement,
                "subject": rec.subject,
                "applicability": dict(rec.applicability),
                "origin": rec.origin,
                "confirmation_state": rec.confirmation_state,
                "risk_tier": rec.risk_tier,
                "importance": rec.importance,
                "created_at": rec.created_at,
                "last_accessed_at": rec.last_accessed_at,
                "access_count": rec.access_count,
                "valid_until": rec.valid_until,
                "supersedes": list(rec.supersedes),
                "evidence_refs": list(rec.evidence_refs),
                "lifecycle": rec.lifecycle,
            },
        },
    }
    body = rec.body.strip() + ("\n" if rec.body.strip() else "")
    return f"---\n{_dump_yaml(fm)}\n---\n\n{body}"


def parse_memory_page(raw: str) -> MemoryRecord:
    """Parse a Memory markdown page into MemoryRecord."""
    m = FRONT_MATTER_RE.match(raw)
    if not m:
        raise ValueError("memory page missing YAML front matter")
    meta = _load_yaml(m.group(1))
    body = raw[m.end() :].lstrip("\n")
    kw = meta.get("kw") if isinstance(meta.get("kw"), dict) else {}
    mem = kw.get("memory") if isinstance(kw.get("memory"), dict) else {}
    security = kw.get("security") if isinstance(kw.get("security"), dict) else {}
    return MemoryRecord(
        uid=str(kw.get("uid") or mem.get("uid") or _new_uid()),
        kind=str(mem.get("kind") or "semantic"),
        title=str(meta.get("title") or ""),
        description=str(meta.get("description") or ""),
        statement=str(mem.get("statement") or ""),
        subject=str(mem.get("subject") or "user"),
        applicability=dict(mem.get("applicability") or {}),
        origin=str(mem.get("origin") or "inferred"),
        confirmation_state=str(mem.get("confirmation_state") or "proposed"),
        risk_tier=str(mem.get("risk_tier") or "T1"),
        importance=float(mem.get("importance") if mem.get("importance") is not None else 0.5),
        classification=str(security.get("classification") or "private"),
        scopes=list(security.get("scopes") or ["personal"]),
        created_at=str(mem.get("created_at") or ""),
        last_accessed_at=mem.get("last_accessed_at"),
        access_count=int(mem.get("access_count") or 0),
        valid_until=mem.get("valid_until"),
        supersedes=list(mem.get("supersedes") or []),
        evidence_refs=[str(x) for x in (mem.get("evidence_refs") or [])],
        status=str(meta.get("status") or "stable"),
        body=body,
        content_version=int(kw.get("content_version") or 1),
        schema_version=int(kw.get("schema_version") or 3),
        lifecycle=str(mem.get("lifecycle") or "active"),
    )


class MemoryWriter(Protocol):
    def write_text(self, path: Path, text: str) -> None: ...


class FilesystemWriter:
    """Scaffold writer until CommitService owns wiki/ mutations."""

    def write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


@dataclass
class MemoryStore:
    workspace: Path
    writer: MemoryWriter = field(default_factory=FilesystemWriter)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)

    def ensure_dirs(self) -> None:
        for kind in LONG_TERM_KINDS:
            (self.workspace / "wiki" / "memories" / kind).mkdir(parents=True, exist_ok=True)
        (self.workspace / ".kw" / "memory" / "episodes").mkdir(parents=True, exist_ok=True)
        (self.workspace / ".kw" / "history" / "memories").mkdir(parents=True, exist_ok=True)

    def _long_term_path(self, rec: MemoryRecord) -> Path:
        safe = re.sub(r"[^\w\-]+", "_", rec.uid)[:80]
        return (
            self.workspace
            / "wiki"
            / "memories"
            / str(rec.kind)
            / f"{safe}.md"
        )

    def _episode_path(self) -> Path:
        return self.workspace / ".kw" / "memory" / "episodes" / "episodes.jsonl"

    def _history_dir(self, uid: str) -> Path:
        return self.workspace / ".kw" / "history" / "memories" / uid

    def get(self, uid: str) -> MemoryRecord | None:
        # Long-term markdown
        for kind in LONG_TERM_KINDS:
            folder = self.workspace / "wiki" / "memories" / kind
            if not folder.is_dir():
                continue
            for path in folder.glob("*.md"):
                try:
                    rec = parse_memory_page(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if rec.uid == uid:
                    return rec
        # Episodes
        for ep in self._read_episodes():
            if ep.uid == uid:
                return ep
        return None

    def list(self, kind: str | None = None) -> list[MemoryRecord]:
        out: list[MemoryRecord] = []
        kinds = (kind,) if kind and kind != "episodic" else LONG_TERM_KINDS
        if kind is None or kind in LONG_TERM_KINDS:
            for k in kinds:
                folder = self.workspace / "wiki" / "memories" / k
                if not folder.is_dir():
                    continue
                for path in sorted(folder.glob("*.md")):
                    try:
                        out.append(parse_memory_page(path.read_text(encoding="utf-8")))
                    except Exception:
                        continue
        if kind in (None, "episodic"):
            out.extend(self._read_episodes())
        return out

    def _read_episodes(self) -> list[MemoryRecord]:
        path = self._episode_path()
        if not path.is_file():
            return []
        out: list[MemoryRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(_record_from_dict(data))
        return out

    def save(self, rec: MemoryRecord) -> Path:
        """Write memory; bump version and archive previous long-term page."""
        self.ensure_dirs()
        existing = self.get(rec.uid)
        if existing and existing.kind in LONG_TERM_KINDS:
            # Archive previous version
            hist = self._history_dir(rec.uid)
            hist.mkdir(parents=True, exist_ok=True)
            ver = existing.content_version
            archive = hist / f"v{ver}.md"
            self.writer.write_text(archive, render_memory_page(existing))
            if rec.content_version <= existing.content_version:
                rec = replace(rec, content_version=existing.content_version + 1)

        if rec.kind == "episodic":
            return self._save_episode(rec)

        if rec.kind not in LONG_TERM_KINDS:
            raise ValueError(f"unknown memory kind: {rec.kind}")

        path = self._long_term_path(rec)
        self.writer.write_text(path, render_memory_page(rec))
        return path

    def _save_episode(self, rec: MemoryRecord) -> Path:
        path = self._episode_path()
        episodes = {e.uid: e for e in self._read_episodes()}
        if rec.uid in episodes and rec.content_version <= episodes[rec.uid].content_version:
            rec = replace(rec, content_version=episodes[rec.uid].content_version + 1)
        episodes[rec.uid] = rec
        lines = [
            json.dumps(_record_to_dict(e), ensure_ascii=False)
            for e in episodes.values()
        ]
        text = "\n".join(lines) + ("\n" if lines else "")
        self.writer.write_text(path, text)
        return path

    def list_versions(self, uid: str) -> list[MemoryRecord]:
        """Return archived versions (oldest first) plus current if present."""
        hist = self._history_dir(uid)
        versions: list[MemoryRecord] = []
        if hist.is_dir():
            for path in sorted(hist.glob("v*.md")):
                try:
                    versions.append(parse_memory_page(path.read_text(encoding="utf-8")))
                except Exception:
                    continue
        current = self.get(uid)
        if current and current.kind in LONG_TERM_KINDS:
            versions.append(current)
        return versions

    def delete(self, uid: str, *, purge_history: bool = False) -> bool:
        """Remove memory page/episode; optionally wipe version history (§8.8)."""
        found = self.get(uid)
        if found is None and not purge_history:
            return False
        if found is not None:
            if found.kind == "episodic":
                episodes = [e for e in self._read_episodes() if e.uid != uid]
                path = self._episode_path()
                lines = [
                    json.dumps(_record_to_dict(e), ensure_ascii=False) for e in episodes
                ]
                text = "\n".join(lines) + ("\n" if lines else "")
                self.writer.write_text(path, text)
            else:
                path = self._long_term_path(found)
                if path.is_file():
                    path.unlink()
                # Also try glob by uid in case kind path differs
                for kind in LONG_TERM_KINDS:
                    folder = self.workspace / "wiki" / "memories" / kind
                    if not folder.is_dir():
                        continue
                    for p in folder.glob("*.md"):
                        try:
                            if parse_memory_page(p.read_text(encoding="utf-8")).uid == uid:
                                p.unlink(missing_ok=True)
                        except Exception:
                            continue
        if purge_history:
            hist = self._history_dir(uid)
            if hist.is_dir():
                for p in hist.glob("*"):
                    p.unlink(missing_ok=True)
                try:
                    hist.rmdir()
                except OSError:
                    pass
        return True


def _record_to_dict(rec: MemoryRecord) -> dict[str, Any]:
    return {
        "uid": rec.uid,
        "kind": rec.kind,
        "title": rec.title,
        "description": rec.description,
        "statement": rec.statement,
        "subject": rec.subject,
        "applicability": rec.applicability,
        "origin": rec.origin,
        "confirmation_state": rec.confirmation_state,
        "risk_tier": rec.risk_tier,
        "importance": rec.importance,
        "classification": rec.classification,
        "scopes": rec.scopes,
        "created_at": rec.created_at,
        "last_accessed_at": rec.last_accessed_at,
        "access_count": rec.access_count,
        "valid_until": rec.valid_until,
        "supersedes": rec.supersedes,
        "evidence_refs": rec.evidence_refs,
        "status": rec.status,
        "body": rec.body,
        "content_version": rec.content_version,
        "schema_version": rec.schema_version,
        "lifecycle": rec.lifecycle,
    }


def _record_from_dict(data: dict[str, Any]) -> MemoryRecord:
    return MemoryRecord(
        uid=str(data.get("uid") or _new_uid("ep")),
        kind=str(data.get("kind") or "episodic"),
        title=str(data.get("title") or ""),
        description=str(data.get("description") or ""),
        statement=str(data.get("statement") or data.get("text") or ""),
        subject=str(data.get("subject") or "user"),
        applicability=dict(data.get("applicability") or {}),
        origin=str(data.get("origin") or "observed"),
        confirmation_state=str(data.get("confirmation_state") or "proposed"),
        risk_tier=str(data.get("risk_tier") or "T1"),
        importance=float(data.get("importance") if data.get("importance") is not None else 0.5),
        classification=str(data.get("classification") or "private"),
        scopes=list(data.get("scopes") or ["personal"]),
        created_at=str(data.get("created_at") or ""),
        last_accessed_at=data.get("last_accessed_at"),
        access_count=int(data.get("access_count") or 0),
        valid_until=data.get("valid_until"),
        supersedes=list(data.get("supersedes") or []),
        evidence_refs=[str(x) for x in (data.get("evidence_refs") or [])],
        status=str(data.get("status") or "stable"),
        body=str(data.get("body") or ""),
        content_version=int(data.get("content_version") or 1),
        schema_version=int(data.get("schema_version") or 3),
        lifecycle=str(data.get("lifecycle") or "active"),
    )
