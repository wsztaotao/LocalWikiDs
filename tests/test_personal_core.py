"""Personal Core: routes + agent context packing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.personal_core import (  # noqa: E402
    build_agent_context,
    core_route_for,
    list_core_routes,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_six_core_routes() -> None:
    routes = list_core_routes()
    assert routes == [
        "chat",
        "agent",
        "knowledge_compile",
        "memory",
        "experience_persona",
        "embedding",
    ]
    assert core_route_for("wiki_lint") == "knowledge_compile"
    assert core_route_for("persona") == "experience_persona"


def test_build_agent_context(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    (ws / "wiki" / "memories").mkdir(parents=True, exist_ok=True)
    (ws / "wiki" / "memories" / "m1.md").write_text("# Mem\nprefers concise\n", encoding="utf-8")
    (ws / "wiki" / "profiles" / "style").mkdir(parents=True, exist_ok=True)
    (ws / "wiki" / "profiles" / "style" / "language.md").write_text(
        "# Language\nUse short sentences.\n", encoding="utf-8"
    )
    ctx = build_agent_context(ws)
    assert ctx["ok"] is True
    assert "Memory" in ctx["text"] or "Language" in ctx["text"]
    assert ctx["meta"].get("ocean_default") is False
