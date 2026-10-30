"""Persist workbench sessions under ``.kw/sessions/<id>/``."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

STORAGE_LIMIT_BYTES = 50 * 1024**3  # 50 GiB soft warn


def sessions_dir(workspace: Path | str) -> Path:
    return Path(workspace).resolve() / ".kw" / "sessions"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _dir_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total


class SessionStore:
    def __init__(self, workspace: Path | str) -> None:
        self.root = Path(workspace).resolve()
        self.base = sessions_dir(self.root)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        sid = (session_id or "").strip()
        if not sid or ".." in sid or "/" in sid or "\\" in sid:
            raise ValueError("invalid session id")
        return self.base / sid

    def list_sessions(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.base.is_dir():
            return items
        for d in self.base.iterdir():
            if not d.is_dir():
                continue
            meta = self._read_meta(d)
            if meta:
                items.append(meta)
        items.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
        return items

    def create(
        self,
        *,
        title: str | None = None,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        sid = f"s_{uuid.uuid4().hex[:12]}"
        d = self._path(sid)
        d.mkdir(parents=True, exist_ok=False)
        meta = {
            "id": sid,
            "title": (title or "新会话").strip() or "新会话",
            "created_at": _now(),
            "updated_at": _now(),
            "preset_id": preset_id or "local",
            "oc_session_ref": None,
            "bytes": 0,
        }
        self._write_meta(d, meta)
        (d / "messages.jsonl").write_text("", encoding="utf-8")
        (d / "extract.json").write_text(
            json.dumps({"writes": [], "last": None}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return meta

    def get(self, session_id: str) -> dict[str, Any]:
        d = self._path(session_id)
        if not d.is_dir():
            raise FileNotFoundError(session_id)
        meta = self._read_meta(d) or {"id": session_id}
        meta["messages"] = self.list_messages(session_id)
        meta["extract"] = self.read_extract(session_id)
        meta["bytes"] = _dir_size(d)
        return meta

    def delete(self, session_id: str) -> None:
        d = self._path(session_id)
        if not d.is_dir():
            raise FileNotFoundError(session_id)
        shutil.rmtree(d)

    def clear(self, *, confirm: bool, keep_extracted: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ValueError("confirm required")
        removed = 0
        kept = 0
        for meta in self.list_sessions():
            sid = meta["id"]
            if keep_extracted:
                ext = self.read_extract(sid)
                if ext.get("writes"):
                    kept += 1
                    continue
            self.delete(sid)
            removed += 1
        return {"removed": removed, "kept": kept}

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        path = self._path(session_id) / "messages.jsonl"
        if not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def append_message(self, session_id: str, message: dict[str, Any]) -> dict[str, Any]:
        d = self._path(session_id)
        if not d.is_dir():
            raise FileNotFoundError(session_id)
        msg = dict(message)
        msg.setdefault("ts", _now())
        with (d / "messages.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        meta = self._read_meta(d) or {"id": session_id}
        meta["updated_at"] = _now()
        meta["bytes"] = _dir_size(d)
        self._write_meta(d, meta)
        return msg

    def update_meta(self, session_id: str, **fields: Any) -> dict[str, Any]:
        d = self._path(session_id)
        meta = self._read_meta(d)
        if not meta:
            raise FileNotFoundError(session_id)
        meta.update({k: v for k, v in fields.items() if v is not None})
        meta["updated_at"] = _now()
        meta["bytes"] = _dir_size(d)
        self._write_meta(d, meta)
        return meta

    def read_extract(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id) / "extract.json"
        if not path.is_file():
            return {"writes": [], "last": None}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"writes": [], "last": None}
        except json.JSONDecodeError:
            return {"writes": [], "last": None}

    def write_extract(self, session_id: str, data: dict[str, Any]) -> None:
        d = self._path(session_id)
        if not d.is_dir():
            raise FileNotFoundError(session_id)
        (d / "extract.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def storage_stats(self) -> dict[str, Any]:
        used = _dir_size(self.base)
        return {
            "bytes": used,
            "limit_bytes": STORAGE_LIMIT_BYTES,
            "warn": used > STORAGE_LIMIT_BYTES,
        }

    def _read_meta(self, d: Path) -> dict[str, Any] | None:
        p = d / "meta.json"
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _write_meta(self, d: Path, meta: dict[str, Any]) -> None:
        (d / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
