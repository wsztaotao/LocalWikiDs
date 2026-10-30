"""Resolve GitHub token for skill search/install (R3.14)."""

from __future__ import annotations

import os
from pathlib import Path

SECRET_NAME = "github"


def resolve_github_token(workspace: Path | str | None = None) -> str | None:
    """Prefer ``GITHUB_TOKEN`` env, then secret store name ``github``."""
    env = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if env:
        return env
    if workspace is None:
        return None
    try:
        from localwiki.secrets import open_secret_store

        val = open_secret_store(Path(workspace)).get(SECRET_NAME)
        if val and str(val).strip():
            return str(val).strip()
    except Exception:  # noqa: BLE001
        return None
    return None


def github_token_configured(workspace: Path | str | None = None) -> bool:
    return bool(resolve_github_token(workspace))


def set_github_token(workspace: Path | str, token: str) -> None:
    from localwiki.secrets import open_secret_store

    open_secret_store(Path(workspace)).set(SECRET_NAME, token)


def clear_github_token(workspace: Path | str) -> bool:
    from localwiki.secrets import open_secret_store

    return bool(open_secret_store(Path(workspace)).delete(SECRET_NAME))
