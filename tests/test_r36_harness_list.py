"""R3.6: harness list shape."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.skills_hub.harness import install_skill_or_harness, list_installed  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_harness_list_fields(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    out = install_skill_or_harness(
        ws,
        kind="harness",
        files={
            "manifest.json": '{"name": "demo-harness", "version": "0.1"}',
            "README.md": "demo",
        },
        force=True,
    )
    assert out.get("ok") is True
    listed = list_installed(ws)
    assert "harness" in listed
    assert listed["harness"][0]["name"] == "demo-harness"
    assert listed["harnesses"][0]["name"] == "demo-harness"
    assert "opencode_skills_path" in listed
