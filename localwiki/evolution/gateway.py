"""Thin evolution gateway: status / pause / list / auto-apply / rollback (R3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from localwiki.evolution.apply import EvolutionApplier, EvolutionRegistry
from localwiki.evolution.learnings import LearningStore
from localwiki.evolution.proposals import ProposalStore, check_freeze_zone


def _pause_path(root: Path) -> Path:
    return root / ".kw" / "evolution" / "pause.json"


def is_paused(workspace: Path | str) -> bool:
    p = _pause_path(Path(workspace))
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("pause"))
    except json.JSONDecodeError:
        return False


def set_pause(workspace: Path | str, pause: bool) -> dict[str, Any]:
    root = Path(workspace)
    path = _pause_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"pause": bool(pause)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "pause": bool(pause)}


def status(workspace: Path | str) -> dict[str, Any]:
    root = Path(workspace)
    reg = EvolutionRegistry(root)
    st = reg.load_state()
    versions = reg.list_versions()
    return {
        "ok": True,
        "pause": is_paused(root),
        "level": st.get("level"),
        "current_version": st.get("current_version"),
        "recent_versions": versions[-10:],
        "evolution_auto_apply": True,
    }


def list_learnings(workspace: Path | str, *, limit: int = 50) -> dict[str, Any]:
    store = LearningStore(Path(workspace))
    items = store.list_records()
    return {"ok": True, "items": list(items)[-int(limit) :]}


def list_proposals(workspace: Path | str) -> dict[str, Any]:
    store = ProposalStore(Path(workspace))
    items = store.list_proposals()
    out = [p.to_dict() if hasattr(p, "to_dict") else p for p in items]
    return {"ok": True, "items": out}


def rollback(workspace: Path | str, version_id: str) -> dict[str, Any]:
    applier = EvolutionApplier(Path(workspace))
    result = applier.rollback(version_id, reason="user_api", automatic=False)
    if result is None:
        return {"ok": False, "error": {"code": "E_NOT_FOUND", "message": f"version {version_id}"}}
    if hasattr(result, "to_dict"):
        return {"ok": True, "version": result.to_dict()}
    return {"ok": True, "result": result}


def run_auto_evolution(workspace: Path | str) -> dict[str, Any]:
    """Nightly: if not paused, auto-apply pending V0/V1 under E2; freeze always reject."""
    root = Path(workspace)
    if is_paused(root):
        return {"ok": True, "paused": True, "applied": []}

    applier = EvolutionApplier(root)
    try:
        applier.set_level("E2")
    except Exception:  # noqa: BLE001
        pass

    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    store = ProposalStore(root)
    for prop in store.list_proposals():
        if prop.status not in ("proposed", "passed", "awaiting_approval"):
            continue
        hits = check_freeze_zone(
            target=prop.target,
            diff=prop.diff,
            kind=prop.kind,
            rationale=prop.rationale,
        )
        if hits:
            prop.status = "rejected"
            prop.reject_reason = "freeze_zone"
            store.save(prop)
            rejected.append(
                {
                    "proposal_uid": prop.proposal_uid,
                    "reason": "freeze_zone",
                    "hits": [h.to_dict() if hasattr(h, "to_dict") else str(h) for h in hits],
                }
            )
            continue
        try:
            ver = applier.apply(prop, eval_passed=True, user_confirmed=False)
            if ver is None:
                rejected.append(
                    {
                        "proposal_uid": prop.proposal_uid,
                        "reason": prop.reject_reason or prop.status,
                    }
                )
            else:
                applied.append(ver.to_dict())
        except Exception as exc:  # noqa: BLE001
            rejected.append({"proposal_uid": prop.proposal_uid, "error": str(exc)})

    return {"ok": True, "paused": False, "applied": applied, "rejected": rejected}
