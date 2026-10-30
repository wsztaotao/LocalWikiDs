"""Workbench chat sessions under ``.kw/sessions/`` (R3.8)."""

from __future__ import annotations

from localwiki.sessions.store import (
    STORAGE_LIMIT_BYTES,
    SessionStore,
    sessions_dir,
)

__all__ = [
    "STORAGE_LIMIT_BYTES",
    "SessionStore",
    "sessions_dir",
]
