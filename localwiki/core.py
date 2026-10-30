"""CommitService and ChangeSet (R1 spec §4)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from localwiki.single_instance import WorkspaceLock
from localwiki.workspace import ensure_workspace, read_head, workspace_paths

E_CONFLICT = "E_CONFLICT"
E_NOT_FOUND = "E_NOT_FOUND"
E_INVALID_INPUT = "E_INVALID_INPUT"
E_DEDUP_BLOCK = "E_DEDUP_BLOCK"


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class PageOp:
    op: str  # create|update|delete
    path: str  # relative to wiki/
    content: str | None = None
    base_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.content is not None:
            d["content"] = self.content
        if self.base_sha256 is not None:
            d["base_sha256"] = self.base_sha256
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PageOp:
        return cls(
            op=d["op"],
            path=d["path"].replace("\\", "/").lstrip("/"),
            content=d.get("content"),
            base_sha256=d.get("base_sha256"),
        )


@dataclass
class ChangeSet:
    change_uid: str
    base_snapshot_id: str
    purpose: str
    ops: list[PageOp] = field(default_factory=list)
    state: str = "draft"
    created_at: str = field(default_factory=_utc)
    commit_id: str | None = None
    error: str | None = None
    gate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "change_uid": self.change_uid,
            "base_snapshot_id": self.base_snapshot_id,
            "purpose": self.purpose,
            "ops": [o.to_dict() for o in self.ops],
            "state": self.state,
            "created_at": self.created_at,
            "commit_id": self.commit_id,
            "error": self.error,
        }
        if self.gate is not None:
            d["gate"] = self.gate
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChangeSet:
        return cls(
            change_uid=d["change_uid"],
            base_snapshot_id=d["base_snapshot_id"],
            purpose=d.get("purpose") or "",
            ops=[PageOp.from_dict(o) for o in d.get("ops") or []],
            state=d.get("state") or "draft",
            created_at=d.get("created_at") or _utc(),
            commit_id=d.get("commit_id"),
            error=d.get("error"),
            gate=d.get("gate"),
        )


class CommitError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CommitService:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.paths = ensure_workspace(self.root)
        self._ensure_index_schema()

    def _changeset_path(self, uid: str) -> Path:
        return self.paths["changesets"] / f"{uid}.json"

    def list_changesets(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.paths["changesets"].glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def get_changeset(self, uid: str) -> ChangeSet:
        path = self._changeset_path(uid)
        if not path.exists():
            raise CommitError(E_NOT_FOUND, f"changeset not found: {uid}")
        return ChangeSet.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_changeset(self, cs: ChangeSet) -> ChangeSet:
        if cs.state not in (
            "draft",
            "ready",
            "accepted",
            "rejected",
            "conflicted",
            "failed",
        ):
            raise CommitError(E_INVALID_INPUT, f"bad state: {cs.state}")
        path = self._changeset_path(cs.change_uid)
        path.write_text(
            json.dumps(cs.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return cs

    def create_changeset(
        self,
        purpose: str,
        ops: list[dict[str, Any]] | list[PageOp],
        *,
        state: str = "ready",
        skip_gate: bool = False,
    ) -> ChangeSet:
        parsed = [
            o if isinstance(o, PageOp) else PageOp.from_dict(o) for o in ops
        ]
        if not parsed:
            raise CommitError(E_INVALID_INPUT, "ops required")
        for op in parsed:
            if op.op not in ("create", "update", "delete"):
                raise CommitError(E_INVALID_INPUT, f"bad op: {op.op}")
            if ".." in op.path or op.path.startswith(("/", "\\")):
                raise CommitError(E_INVALID_INPUT, f"bad path: {op.path}")
            if op.op in ("create", "update") and op.content is None:
                raise CommitError(E_INVALID_INPUT, "content required")
        gate_meta: dict[str, Any] | None = None
        if not skip_gate:
            from localwiki.wiki_gate import transform_ops

            parsed, gate_meta = transform_ops(self.root, parsed)
            if not parsed:
                raise CommitError(E_INVALID_INPUT, "ops empty after wiki gate")
            for op in parsed:
                if op.op not in ("create", "update", "delete"):
                    raise CommitError(E_INVALID_INPUT, f"bad op: {op.op}")
                if ".." in op.path or op.path.startswith(("/", "\\")):
                    raise CommitError(E_INVALID_INPUT, f"bad path: {op.path}")
                if op.op in ("create", "update") and op.content is None:
                    raise CommitError(E_INVALID_INPUT, "content required")
        cs = ChangeSet(
            change_uid=f"chg_{uuid.uuid4().hex[:12]}",
            base_snapshot_id=read_head(self.root),
            purpose=purpose,
            ops=parsed,
            state=state,
            gate=gate_meta,
        )
        return self.save_changeset(cs)

    def reject(self, uid: str) -> ChangeSet:
        cs = self.get_changeset(uid)
        if cs.state == "accepted":
            raise CommitError(E_INVALID_INPUT, "already accepted")
        cs.state = "rejected"
        return self.save_changeset(cs)

    def accept(self, uid: str) -> ChangeSet:
        cs = self.get_changeset(uid)
        if cs.state == "accepted":
            return cs
        if cs.state not in ("draft", "ready"):
            raise CommitError(E_INVALID_INPUT, f"cannot accept state={cs.state}")

        lock = WorkspaceLock(self.paths["lock"])
        if not lock.acquire(blocking=False):
            raise CommitError("E_PERMISSION", "workspace locked by another process")
        try:
            return self._accept_locked(cs)
        finally:
            lock.release()

    def _accept_locked(self, cs: ChangeSet) -> ChangeSet:
        from localwiki.wiki_gate import verify_ops

        try:
            verify_ops(self.root, cs.ops)
        except CommitError as exc:
            if getattr(exc, "code", None) == E_DEDUP_BLOCK or str(
                getattr(exc, "code", "")
            ) == "E_DEDUP_BLOCK":
                cs.state = "failed"
                cs.error = E_DEDUP_BLOCK
                self.save_changeset(cs)
            raise

        wiki = self.paths["wiki"]
        # Validate base hashes
        wiki_root = wiki.resolve()
        for op in cs.ops:
            target = (wiki / op.path).resolve()
            try:
                target.relative_to(wiki_root)
            except ValueError:
                cs.state = "failed"
                cs.error = "path escape"
                self.save_changeset(cs)
                raise CommitError(E_INVALID_INPUT, "path escape")
            current = file_sha256(target) if target.exists() else None
            if op.op == "create":
                if target.exists():
                    cs.state = "conflicted"
                    cs.error = E_CONFLICT
                    self.save_changeset(cs)
                    raise CommitError(E_CONFLICT, f"exists: {op.path}")
            elif op.op in ("update", "delete"):
                if current != op.base_sha256:
                    cs.state = "conflicted"
                    cs.error = E_CONFLICT
                    self.save_changeset(cs)
                    raise CommitError(
                        E_CONFLICT,
                        f"base_sha mismatch for {op.path}: expected {op.base_sha256}, got {current}",
                    )

        parent = read_head(self.root)
        if cs.base_snapshot_id != parent:
            # Soft check: still allow if page hashes match (linearize on page level)
            pass

        commit_id = f"cmt_{uuid.uuid4().hex[:12]}"
        new_snap = f"snap_{uuid.uuid4().hex[:8]}"
        tx_path = self.paths["transactions"] / f"{commit_id}.json"
        manifest = {
            "commit_id": commit_id,
            "parent": parent,
            "snapshot_id": new_snap,
            "change_uid": cs.change_uid,
            "ops": [o.to_dict() for o in cs.ops],
            "phase": "PREPARED",
            "at": _utc(),
        }
        tx_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # Write content objects and project files
        applied: list[dict[str, Any]] = []
        try:
            for op in cs.ops:
                target = wiki / op.path
                if op.op in ("create", "update"):
                    assert op.content is not None
                    digest = sha256_text(op.content)
                    obj = self.paths["objects"] / digest
                    if not obj.exists():
                        obj.write_text(op.content, encoding="utf-8")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(target.suffix + ".tmp")
                    tmp.write_text(op.content, encoding="utf-8")
                    tmp.replace(target)
                    applied.append({"path": op.path, "sha256": digest, "op": op.op})
                elif op.op == "delete":
                    if target.exists():
                        target.unlink()
                    applied.append({"path": op.path, "op": "delete"})

            commit_rec = {
                "commit_id": commit_id,
                "parent": parent,
                "snapshot_id": new_snap,
                "change_uid": cs.change_uid,
                "applied": applied,
                "at": _utc(),
            }
            (self.paths["commits"] / f"{commit_id}.json").write_text(
                json.dumps(commit_rec, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # Atomic HEAD
            head_tmp = self.paths["head"].with_suffix(".tmp")
            head_tmp.write_text(new_snap + "\n", encoding="utf-8")
            head_tmp.replace(self.paths["head"])

            manifest["phase"] = "COMMITTED"
            tx_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            self._refresh_index(applied)
            self._append_log(cs, new_snap)
            self._rebuild_index_md()

            cs.state = "accepted"
            cs.commit_id = commit_id
            cs.error = None
            return self.save_changeset(cs)
        except CommitError:
            raise
        except Exception as exc:  # noqa: BLE001
            cs.state = "failed"
            cs.error = str(exc)
            self.save_changeset(cs)
            raise CommitError("E_FAILED", str(exc)) from exc

    def recover_on_startup(self) -> list[str]:
        """Scan transactions; leave incomplete PREPARED without advancing HEAD."""
        notes: list[str] = []
        for tx in self.paths["transactions"].glob("*.json"):
            try:
                data = json.loads(tx.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("phase") == "PREPARED":
                notes.append(f"incomplete transaction left: {tx.name}")
            elif data.get("phase") == "COMMITTED":
                # Ensure index refreshed
                self._ensure_index_schema()
        return notes

    def list_pages(self) -> list[dict[str, Any]]:
        wiki = self.paths["wiki"]
        pages: list[dict[str, Any]] = []
        for p in sorted(wiki.rglob("*.md")):
            rel = p.relative_to(wiki).as_posix()
            pages.append(
                {
                    "path": rel,
                    "sha256": file_sha256(p),
                    "title": _guess_title(p),
                }
            )
        return pages

    def read_page(self, rel_path: str) -> dict[str, Any]:
        rel = rel_path.replace("\\", "/").lstrip("/")
        wiki_root = self.paths["wiki"].resolve()
        target = (self.paths["wiki"] / rel).resolve()
        try:
            target.relative_to(wiki_root)
        except ValueError:
            raise CommitError(E_INVALID_INPUT, "path escape")
        if not target.exists():
            raise CommitError(E_NOT_FOUND, rel)
        text = target.read_text(encoding="utf-8")
        return {"path": rel, "sha256": sha256_text(text), "content": text}

    def _ensure_index_schema(self) -> None:
        db = self.paths["index_db"]
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pages (
                  path TEXT PRIMARY KEY,
                  title TEXT,
                  sha256 TEXT,
                  snapshot_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                  key TEXT PRIMARY KEY,
                  value TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _refresh_index(self, applied: list[dict[str, Any]]) -> None:
        self._ensure_index_schema()
        snap = read_head(self.root)
        conn = sqlite3.connect(str(self.paths["index_db"]))
        try:
            for item in applied:
                path = item["path"]
                if item.get("op") == "delete":
                    conn.execute("DELETE FROM pages WHERE path=?", (path,))
                else:
                    full = self.paths["wiki"] / path
                    title = _guess_title(full) if full.exists() else path
                    sha = item.get("sha256") or file_sha256(full)
                    conn.execute(
                        """
                        INSERT INTO pages(path, title, sha256, snapshot_id)
                        VALUES(?,?,?,?)
                        ON CONFLICT(path) DO UPDATE SET
                          title=excluded.title,
                          sha256=excluded.sha256,
                          snapshot_id=excluded.snapshot_id
                        """,
                        (path, title, sha, snap),
                    )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('indexed_snapshot',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (snap,),
            )
            conn.commit()
        finally:
            conn.close()

    def _append_log(self, cs: ChangeSet, snap: str) -> None:
        log = self.paths["wiki"] / "log.md"
        day = _utc()[:10]
        lines = [
            f"\n## {day}\n",
            f"- **提交**：{cs.purpose or cs.change_uid}（snapshot `{snap}`，changeset `{cs.change_uid}`）\n",
        ]
        for op in cs.ops:
            lines.append(f"  - {op.op} `{op.path}`\n")
        with log.open("a", encoding="utf-8") as f:
            f.writelines(lines)

    def _rebuild_index_md(self) -> None:
        pages = [p for p in self.list_pages() if p["path"] not in ("index.md", "log.md")]
        lines = [
            "---\n",
            'okf_version: "0.2"\n',
            "---\n\n",
            "# 知识目录\n\n",
        ]
        if not pages:
            lines.append("- 尚无页面。\n")
        else:
            for p in pages:
                lines.append(f"- [{p['title']}]({p['path']})\n")
        (self.paths["wiki"] / "index.md").write_text("".join(lines), encoding="utf-8")


def _guess_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.name
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s.startswith("title:"):
            return s.split(":", 1)[1].strip().strip("\"'")
    return path.stem
