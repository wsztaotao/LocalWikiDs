"""R3.6: Ollama tags listing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.personal_core import list_ollama_tags  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_ollama_tags_mock(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)

    def http(method: str, url: str, **kwargs):
        assert method == "GET"
        assert url.endswith("/api/tags")
        return {
            "models": [
                {"name": "llama3.2:latest", "size": 1},
                {"name": "nomic-embed-text", "size": 2},
            ]
        }

    out = list_ollama_tags(ws, base_url="http://127.0.0.1:11434", http=http)
    assert out["ok"] is True
    assert out["error"] is None
    assert [m["name"] for m in out["models"]] == ["llama3.2:latest", "nomic-embed-text"]


def test_ollama_tags_unreachable(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)

    def http(method: str, url: str, **kwargs):
        raise RuntimeError("connection refused")

    out = list_ollama_tags(ws, http=http)
    assert out["ok"] is True
    assert out["models"] == []
    assert out["error"] and "unreachable" in out["error"]
