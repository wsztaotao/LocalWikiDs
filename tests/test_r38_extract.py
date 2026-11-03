"""R3.8/R3.9 session extract (model-backed with injectable fake)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.session_extract import extract_after_turn, is_valuable_turn  # noqa: E402
from localwiki.sessions import SessionStore  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


class _FakeMemory:
    model = "fake-memory"
    provider_name = "test"
    route_name = "memory"

    def generate(self, messages: list[dict[str, Any]], *, tools: Any, extras: dict) -> Any:
        del tools, extras
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "# Note\n\nRemember: use Chinese.\n",
                    }
                }
            ]
        }


def test_heuristic_and_force_write(tmp_path: Path) -> None:
    assert is_valuable_turn("以后请用中文", "ok")
    assert not is_valuable_turn("hi", "yo")
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SessionStore(ws)
    sid = store.create()["id"]
    store.append_message(sid, {"role": "user", "content": "hi", "ok": True})
    store.append_message(sid, {"role": "assistant", "content": "hello", "ok": True})
    out = extract_after_turn(ws, sid, force=True, model=_FakeMemory())
    assert out["ok"] is True
    assert out["written"] is True
    assert out.get("extract_mode") == "model"
    path = ws / "wiki" / out["path"]
    assert path.is_file()
    assert "Remember" in path.read_text(encoding="utf-8")
