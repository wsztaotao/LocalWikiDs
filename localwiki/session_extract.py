"""Session → personal-layer extract via memory model (R3.9)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from localwiki.sessions.store import SessionStore

_PREF_RE = re.compile(
    r"(以后|不要|必须|always|never|prefer|请记住)",
    re.IGNORECASE,
)

_EXTRACT_SYSTEM = """You are LocalWiki memory extract.
From the conversation, write a concise markdown memory note for the user's personal wiki.
Include: durable preferences, decisions, reusable steps, or key facts worth remembering.
If little is durable, still produce a short factual note of the exchange.
Reply with markdown only (no JSON). Use the user's language."""


def is_valuable_turn(user: str, assistant: str, *, force: bool = False) -> bool:
    if force:
        return True
    u = user or ""
    a = assistant or ""
    if _PREF_RE.search(u):
        return True
    if "```" in a and len(a) > 80:
        return True
    if len(a.splitlines()) >= 8 and len(a) > 200:
        return True
    if "error" in a.lower() and ("code" in a.lower() or "E_" in a):
        return True
    return False


def _assistant_text(resp: Any) -> str:
    if isinstance(resp, dict):
        choices = resp.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            return str(msg.get("content") or "").strip()
    return str(resp or "").strip()


def extract_after_turn(
    workspace: Path | str,
    session_id: str,
    *,
    force: bool = False,
    model: Any | None = None,
) -> dict[str, Any]:
    """Call memory route; on hit, write memories/ via CommitService."""
    root = Path(workspace).resolve()
    store = SessionStore(root)
    detail = store.get(session_id)
    messages = detail.get("messages") or []
    if not messages:
        return {"ok": True, "written": False, "reason": "empty", "extract_mode": "model"}

    user_text = ""
    asst_text = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and not asst_text:
            asst_text = str(m.get("content") or "")
        elif m.get("role") == "user" and not user_text:
            user_text = str(m.get("content") or "")
        if user_text and asst_text:
            break

    if not is_valuable_turn(user_text, asst_text, force=force):
        return {
            "ok": True,
            "written": False,
            "reason": "not_valuable",
            "extract_mode": "model",
        }

    from localwiki.agent.routed_model import ModelNotConfiguredError, build_routed_model

    try:
        if model is None:
            model = build_routed_model(root, core_route="memory")
        resp = model.generate(
            [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": f"## User\n\n{user_text}\n\n## Assistant\n\n{asst_text}\n",
                },
            ],
            tools=None,
            extras={},
        )
        note = _assistant_text(resp)
    except ModelNotConfiguredError as exc:
        return {
            "ok": False,
            "written": False,
            "error": {
                "code": getattr(exc, "code", "E_MODEL_UNAVAILABLE"),
                "message": str(exc),
            },
            "extract_mode": "model",
            "route": "memory",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "written": False,
            "error": {"code": "E_MODEL", "message": str(exc)},
            "extract_mode": "model",
            "route": "memory",
        }

    if not note:
        return {
            "ok": False,
            "written": False,
            "error": {"code": "E_MODEL", "message": "memory model returned empty"},
            "extract_mode": "model",
            "route": "memory",
            "model": getattr(model, "model", None),
        }

    from localwiki.core import CommitService, file_sha256

    memories = root / "wiki" / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    fname = f"session_{session_id}_{len(messages)}.md"
    rel = f"memories/{fname}"
    body = (
        f"# Session extract\n\n"
        f"- session: `{session_id}`\n"
        f"- extract_mode: model\n"
        f"- route: memory\n"
        f"- model: `{getattr(model, 'model', '')}`\n\n"
        f"{note}\n"
    )
    svc = CommitService(root)
    target = memories / fname
    op_name = "update" if target.exists() else "create"
    op: dict[str, Any] = {"op": op_name, "path": rel, "content": body}
    if op_name == "update":
        op["base_sha256"] = file_sha256(target)
    cs = svc.create_changeset(
        purpose=f"session extract {session_id}",
        ops=[op],
        state="ready",
    )
    svc.accept(cs.change_uid)
    ext = store.read_extract(session_id)
    writes = list(ext.get("writes") or [])
    writes.append({"path": rel, "change_uid": cs.change_uid})
    store.write_extract(session_id, {"writes": writes, "last": rel})
    return {
        "ok": True,
        "written": True,
        "path": rel,
        "change_uid": cs.change_uid,
        "extract_mode": "model",
        "route": "memory",
        "model": getattr(model, "model", None),
    }
