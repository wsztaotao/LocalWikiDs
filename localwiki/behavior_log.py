"""Usage behavior log — metadata only, no body text (design §7.4).

Events live under ``raw/behavior/YYYY-MM.jsonl``. Categories can be disabled
in settings; disabled categories stop writing immediately.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BEHAVIOR_CATEGORIES: tuple[str, ...] = (
    "review",
    "edit",
    "approval",
    "model",
    "time",
    "task",
    "tool",
    "answer",
)

# Keys that must never be persisted (body / content / keystrokes / titles)
_FORBIDDEN_KEYS = frozenset(
    {
        "body",
        "content",
        "text",
        "message",
        "prompt",
        "response",
        "window_title",
        "keystrokes",
        "input",
        "excerpt",
        "raw",
    }
)


def _uid() -> str:
    return f"ev_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_key(ts: datetime | None = None) -> str:
    t = ts or datetime.now(timezone.utc)
    return f"{t.year:04d}-{t.month:02d}"


@dataclass
class BehaviorSettings:
    """Per-category enable flags; default all on."""

    enabled: dict[str, bool] = field(default_factory=dict)

    def is_enabled(self, cat: str) -> bool:
        if cat not in BEHAVIOR_CATEGORIES:
            return False
        if cat in self.enabled:
            return bool(self.enabled[cat])
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": {
                c: self.is_enabled(c) for c in BEHAVIOR_CATEGORIES
            }
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BehaviorSettings:
        enabled = {}
        if data and isinstance(data.get("enabled"), dict):
            enabled = {str(k): bool(v) for k, v in data["enabled"].items()}
        return cls(enabled=enabled)


def _sanitize_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not metrics:
        return {}
    out: dict[str, Any] = {}
    for k, v in metrics.items():
        if str(k).lower() in _FORBIDDEN_KEYS:
            continue
        if isinstance(v, str) and len(v) > 64:
            # Long strings look like content — keep length only
            out[f"{k}_len"] = len(v)
            continue
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:64]
        else:
            out[k] = str(v)[:64]
    return out


@dataclass
class BehaviorEvent:
    ev_uid: str
    ts: str
    cat: str
    kind: str
    subject_type: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    ctx: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ev_uid": self.ev_uid,
            "ts": self.ts,
            "cat": self.cat,
            "kind": self.kind,
            "metrics": dict(self.metrics),
            "ctx": dict(self.ctx),
        }
        if self.subject_type:
            d["subject_type"] = self.subject_type
        return d


class BehaviorLog:
    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace)
        self._settings_path = self.workspace / ".kw" / "behavior_settings.json"
        self._dir = self.workspace / "raw" / "behavior"
        self._settings = self._load_settings()

    def _load_settings(self) -> BehaviorSettings:
        if self._settings_path.is_file():
            try:
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
                return BehaviorSettings.from_dict(data)
            except (json.JSONDecodeError, OSError):
                pass
        return BehaviorSettings()

    def settings(self) -> BehaviorSettings:
        return self._settings

    def update_settings(self, settings: BehaviorSettings) -> None:
        self._settings = settings
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def record(
        self,
        cat: str,
        kind: str,
        *,
        subject_type: str | None = None,
        metrics: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
        **extra: Any,
    ) -> BehaviorEvent | None:
        """Append one metadata event. Returns None if category disabled.

        Any ``body`` / ``content`` / similar kwargs are ignored (never written).
        """
        if cat not in BEHAVIOR_CATEGORIES:
            return None
        if not self._settings.is_enabled(cat):
            return None

        # Drop forbidden extras explicitly
        for bad in _FORBIDDEN_KEYS:
            extra.pop(bad, None)

        clean_metrics = _sanitize_metrics(metrics)
        clean_ctx = _sanitize_metrics(ctx)
        # Extra numeric/short flags may merge into metrics if safe
        for k, v in extra.items():
            if k.lower() in _FORBIDDEN_KEYS:
                continue
            if isinstance(v, (int, float, bool)):
                clean_metrics[k] = v

        event = BehaviorEvent(
            ev_uid=_uid(),
            ts=_now_iso(),
            cat=cat,
            kind=kind,
            subject_type=subject_type,
            metrics=clean_metrics,
            ctx=clean_ctx,
        )
        self._append(event)
        return event

    def _shard_path(self, ts: datetime | None = None) -> Path:
        return self._dir / f"{_month_key(ts)}.jsonl"

    def _append(self, event: BehaviorEvent) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._shard_path()
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        # Defense: never write known content markers
        assert "body" not in event.to_dict()
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def iter_events(self, *, category: str | None = None) -> list[BehaviorEvent]:
        out: list[BehaviorEvent] = []
        if not self._dir.is_dir():
            return out
        for path in sorted(self._dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if category and data.get("cat") != category:
                    continue
                out.append(
                    BehaviorEvent(
                        ev_uid=str(data.get("ev_uid") or ""),
                        ts=str(data.get("ts") or ""),
                        cat=str(data.get("cat") or ""),
                        kind=str(data.get("kind") or ""),
                        subject_type=data.get("subject_type"),
                        metrics=dict(data.get("metrics") or {}),
                        ctx=dict(data.get("ctx") or {}),
                    )
                )
        return out

    def stats(self) -> dict[str, int]:
        counts = {c: 0 for c in BEHAVIOR_CATEGORIES}
        for ev in self.iter_events():
            if ev.cat in counts:
                counts[ev.cat] += 1
        return counts

    def purge(self, *, categories: list[str] | None = None) -> int:
        """Delete events for given categories (or all). Returns removed count."""
        if not self._dir.is_dir():
            return 0
        cats = set(categories) if categories else set(BEHAVIOR_CATEGORIES)
        removed = 0
        for path in list(self._dir.glob("*.jsonl")):
            kept: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("cat") in cats:
                    removed += 1
                else:
                    kept.append(line)
            if kept:
                path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
            else:
                path.unlink(missing_ok=True)
        return removed
