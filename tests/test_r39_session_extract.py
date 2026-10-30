"""R3.9 session extract requires memory model."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.routed_model import ModelNotConfiguredError  # noqa: E402
from localwiki.session_extract import extract_after_turn  # noqa: E402
from localwiki.sessions import SessionStore  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


class _FakeMemory:
    model = "fake-mem"
    provider_name = "test"

    def generate(self, messages: list[dict[str, Any]], *, tools: Any, extras: dict) -> Any:
        del tools, extras, messages
        return {
            "choices": [{"message": {"role": "assistant", "content": "记忆：偏好简洁。\n"}}]
        }


def test_extract_without_model_fails(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SessionStore(ws)
    sid = store.create()["id"]
    store.append_message(sid, {"role": "user", "content": "以后请简洁", "ok": True})
    store.append_message(sid, {"role": "assistant", "content": "好的", "ok": True})

    def boom(*_a, **_k):
        raise ModelNotConfiguredError("missing", code="E_MODEL_UNAVAILABLE")

    import localwiki.agent.routed_model as rm

    monkeypatch.setattr(rm, "build_routed_model", boom)
    out = extract_after_turn(ws, sid, force=True)
    assert out["ok"] is False
    assert out["written"] is False
    assert (out.get("error") or {}).get("code") == "E_MODEL_UNAVAILABLE"


def test_extract_with_fake_model(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SessionStore(ws)
    sid = store.create()["id"]
    store.append_message(sid, {"role": "user", "content": "以后请简洁", "ok": True})
    store.append_message(sid, {"role": "assistant", "content": "好的", "ok": True})
    out = extract_after_turn(ws, sid, force=True, model=_FakeMemory())
    assert out["ok"] is True
    assert out["written"] is True
    assert out["model"] == "fake-mem"
    text = (ws / "wiki" / out["path"]).read_text(encoding="utf-8")
    assert "记忆" in text
