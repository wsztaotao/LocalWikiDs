"""R3-D: skill + harness install + scan block."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.skills_hub.harness import (  # noqa: E402
    install_harness_from_tree,
    install_skill_or_harness,
    list_installed,
    stuck_search_install,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_install_skill_fixture(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    files = {
        "SKILL.md": "---\nname: demo-skill\ndescription: demo\n---\n\n# Demo\n",
    }
    out = install_skill_or_harness(ws, kind="skill", files=files, force=True)
    assert out["ok"] is True
    listed = list_installed(ws)
    assert any("demo-skill" in s["name"] for s in listed["skills"])


def test_install_harness(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    files = {
        "manifest.json": '{"name": "demo-harness", "components": ["prompt.md"]}',
        "prompt.md": "Be helpful.\n",
    }
    out = install_harness_from_tree(ws, files)
    assert out["ok"] is True
    assert (ws / ".kw" / "harness" / "demo-harness" / "manifest.json").is_file()


def test_stuck_hook_with_mock(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    out = stuck_search_install(
        ws,
        "ppt",
        mock_files={
            "SKILL.md": "---\nname: ppt-helper\ndescription: slides\n---\n\n# PPT\n",
        },
    )
    assert out["ok"]
    assert out["installed"] and out["installed"]["ok"]
