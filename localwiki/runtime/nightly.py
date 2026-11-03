"""Nightly personal-layer + evolution + FTS rebuild (R3 / D9)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from localwiki.core import CommitService, file_sha256
from localwiki.tools.native_workspace import AUTO_ACCEPT_PREFIXES, rebuild_wiki_fts
from localwiki.workspace import ensure_workspace

D9_PREFIXES = AUTO_ACCEPT_PREFIXES


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _write_d9_page(
    root: Path,
    wiki_rel: str,
    content: str,
    *,
    purpose: str,
) -> dict[str, Any]:
    """Create/update a D9 path and auto-accept via CommitService."""
    if not any(wiki_rel.startswith(p) for p in D9_PREFIXES):
        return {"ok": False, "error": "not_d9_path", "path": wiki_rel}
    svc = CommitService(root)
    target = root / "wiki" / wiki_rel
    if target.exists():
        op = {
            "op": "update",
            "path": wiki_rel,
            "content": content,
            "base_sha256": file_sha256(target),
        }
    else:
        op = {"op": "create", "path": wiki_rel, "content": content}
    cs = svc.create_changeset(purpose=purpose, ops=[op], state="ready")
    cs = svc.accept(cs.change_uid)
    return {
        "ok": True,
        "path": wiki_rel,
        "change_uid": cs.change_uid,
        "state": cs.state,
    }


def run_personal_layer(workspace: Path | str) -> dict[str, Any]:
    """Best-effort memory / experience / style touch under D9."""
    root = Path(workspace).resolve()
    ensure_workspace(root)
    steps: list[dict[str, Any]] = []
    stamp = _now_iso()

    # Memory marker page
    mem_body = (
        f"# Nightly memory note\n\n"
        f"Last nightly personal layer: `{stamp}`\n\n"
        f"Auto-accepted under D9 `memories/`.\n"
    )
    steps.append(
        _write_d9_page(
            root,
            "memories/_nightly.md",
            mem_body,
            purpose="nightly memory consolidate marker",
        )
    )

    # Experience marker
    exp_body = (
        f"# Nightly experience note\n\n"
        f"Compiled at `{stamp}`.\n"
    )
    steps.append(
        _write_d9_page(
            root,
            "experiences/_nightly.md",
            exp_body,
            purpose="nightly experience compile marker",
        )
    )

    # Style language hint
    style_body = (
        f"# Language style\n\n"
        f"Nightly refresh `{stamp}`.\n"
    )
    steps.append(
        _write_d9_page(
            root,
            "profiles/style/language.md",
            style_body,
            purpose="nightly persona/style update",
        )
    )

    # confirmed sidecar
    confirmed_path = root / ".kw" / "personal" / "confirmed.json"
    confirmed_path.parent.mkdir(parents=True, exist_ok=True)
    confirmed = {
        "updated_at": stamp,
        "paths": [s.get("path") for s in steps if s.get("ok")],
    }
    confirmed_path.write_text(
        json.dumps(confirmed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"ok": all(s.get("ok") for s in steps), "steps": steps, "confirmed": confirmed}


def run_nightly(
    workspace: Path | str,
    *,
    skip_evolution: bool = False,
    skip_fts: bool = False,
) -> dict[str, Any]:
    """Full nightly: personal → evolution auto-apply → FTS rebuild."""
    root = Path(workspace).resolve()
    ensure_workspace(root)
    report: dict[str, Any] = {
        "ok": True,
        "started_at": _now_iso(),
        "personal": None,
        "evolution": None,
        "fts": None,
    }

    personal = run_personal_layer(root)
    report["personal"] = personal
    if not personal.get("ok"):
        report["ok"] = False

    if not skip_evolution:
        from localwiki.evolution.gateway import run_auto_evolution

        evo = run_auto_evolution(root)
        report["evolution"] = evo
        if evo.get("paused"):
            pass  # pause is ok
        elif not evo.get("ok", True):
            report["ok"] = False

    if not skip_fts:
        try:
            fts = rebuild_wiki_fts(root)
            report["fts"] = fts
        except Exception as exc:  # noqa: BLE001
            report["fts"] = {"ok": False, "error": str(exc)}
            # FTS failure does not block nightly overall

    # R3.22: stock dedup scan (best-effort; never blocks nightly)
    try:
        from localwiki.wiki_merge import scan_wiki_duplicates

        report["dedup"] = scan_wiki_duplicates(root, apply_high_confidence=True)
    except Exception as exc:  # noqa: BLE001
        report["dedup"] = {"ok": False, "error": str(exc)}

    report["finished_at"] = _now_iso()
    log_path = root / ".kw" / "runtime" / "nightly_last.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def schedule_nightly(
    workspace: Path | str,
    *,
    hour: int = 3,
    minute: int = 0,
) -> Any | None:
    """Attach APScheduler cron job if apscheduler is installed; else None."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        return None

    root = Path(workspace).resolve()
    sched = BackgroundScheduler()

    def _job() -> None:
        run_nightly(root)

    sched.add_job(_job, "cron", hour=int(hour), minute=int(minute), id="localwiki_nightly")
    sched.start()
    return sched
