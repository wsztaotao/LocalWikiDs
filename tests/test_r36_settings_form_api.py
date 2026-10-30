"""R3.6: settings autonomy fields + presets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.personal_core import (  # noqa: E402
    PROVIDER_PRESETS,
    list_provider_presets,
    patch_autonomy_settings,
)
from localwiki.workspace import ensure_workspace, load_config, workspace_paths  # noqa: E402


def test_provider_presets_include_vendors() -> None:
    presets = list_provider_presets()["presets"]
    for key in ("ollama_local", "openai", "anthropic", "deepseek", "qwen"):
        assert key in presets
        assert "kind" in presets[key]
    assert PROVIDER_PRESETS["anthropic"]["kind"] == "anthropic"
    assert PROVIDER_PRESETS["deepseek"]["api_key"].startswith("secret:")


def test_patch_autonomy(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    out = patch_autonomy_settings(
        ws,
        nightly_local_hour=4,
        pause_evolution=True,
        local_agent_loop_enabled=True,
    )
    assert out["ok"]
    assert out["autonomy"]["nightly_local_hour"] == 4
    assert out["autonomy"]["pause_evolution"] is True
    assert out["local_agent_loop_enabled"] is True
    cfg = load_config(workspace_paths(ws)["config"])
    assert cfg["autonomy"]["nightly_local_hour"] == 4
