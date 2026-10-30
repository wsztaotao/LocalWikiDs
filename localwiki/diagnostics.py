"""Doctor diagnostics (R1 spec §7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from localwiki.single_instance import WorkspaceLock
from localwiki.workspace import ensure_workspace, load_config, read_head, workspace_paths


def doctor(workspace: Path | str, *, ensure: bool = True) -> dict[str, Any]:
    """Return a structured health report for a workspace."""
    root = Path(workspace).resolve()
    checks: list[dict[str, Any]] = []
    ok = True

    if ensure:
        try:
            paths = ensure_workspace(root)
        except OSError as exc:
            return {
                "ok": False,
                "workspace": str(root),
                "checks": [
                    {
                        "id": "ensure_workspace",
                        "ok": False,
                        "message": str(exc),
                    }
                ],
            }
    else:
        paths = workspace_paths(root)

    def add(cid: str, passed: bool, message: str, **extra: Any) -> None:
        nonlocal ok
        if not passed:
            ok = False
        item = {"id": cid, "ok": passed, "message": message}
        item.update(extra)
        checks.append(item)

    add("root_exists", root.is_dir(), f"workspace root: {root}")
    for key in ("schema", "wiki", "kw", "history", "objects", "commits", "changesets", "transactions"):
        p = paths[key]
        add(f"dir_{key}", p.is_dir(), str(p))

    head = read_head(root)
    add("head", bool(head), f"HEAD={head}", snapshot_id=head)

    cfg = load_config(paths["config"])
    add(
        "config",
        bool(cfg.get("workspace_uid")),
        f"workspace_uid={cfg.get('workspace_uid')}",
        has_token=bool(cfg.get("api_token")),
        port=cfg.get("port", 8765),
    )

    lock = WorkspaceLock(paths["lock"])
    info = lock.info()
    if info and info.get("alive"):
        add(
            "lock",
            True,
            f"locked by pid={info.get('pid')}",
            locked=True,
            lock_info=info,
        )
    else:
        add("lock", True, "not locked", locked=False)

    index_db = paths["index_db"]
    index_stale = False
    if index_db.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(index_db))
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key='indexed_snapshot'"
                ).fetchone()
                indexed = row[0] if row else None
                index_stale = indexed != head
                add(
                    "index",
                    True,
                    f"indexed_snapshot={indexed}",
                    index_stale=index_stale,
                )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            index_stale = True
            add("index", False, f"index read failed: {exc}", index_stale=True)
    else:
        index_stale = True
        add("index", True, "index.sqlite missing (will create on first commit)", index_stale=True)

    # Secrets reachability (optional)
    try:
        from localwiki.secrets import open_secret_store

        open_secret_store(root)
        add("secrets", True, "secrets module reachable")
    except Exception as exc:  # noqa: BLE001
        add("secrets", True, f"secrets optional: {exc}")

    # OpenCode readiness (R3) — missing bin is informational, not doctor fail
    try:
        from localwiki.runtime.opencode_bridge import opencode_status

        oc = opencode_status(root)
        add(
            "opencode.ready",
            True,
            f"ready={oc.get('ready')} bin={oc.get('bin')}",
            **oc,
        )
    except Exception as exc:  # noqa: BLE001
        add("opencode.ready", True, f"opencode check skipped: {exc}", ready=False)

    return {
        "ok": ok and not any(c["id"].startswith("dir_") and not c["ok"] for c in checks),
        "workspace": str(root),
        "snapshot_id": head,
        "index_stale": index_stale,
        "checks": checks,
        "config": {
            "workspace_uid": cfg.get("workspace_uid"),
            "port": cfg.get("port", 8765),
            "has_token": bool(cfg.get("api_token")),
        },
        "opencode": next(
            (c for c in checks if c["id"] == "opencode.ready"),
            {"ready": False},
        ),
    }
