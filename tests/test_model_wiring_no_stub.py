"""R3.5 RP2: unconfigured agent must not stub-succeed."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.__main__ import run_agent_exec  # noqa: E402
from localwiki.agent.routed_model import ModelNotConfiguredError, build_routed_model  # noqa: E402
from localwiki.personal_core import sync_core_model_settings  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_build_routed_model_requires_config(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    try:
        build_routed_model(ws, core_route="agent")
        raised = False
    except ModelNotConfiguredError:
        raised = True
    assert raised


def test_run_agent_exec_no_stub_success(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    payload, code = run_agent_exec("hello", workspace=ws, as_json=True)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_MODEL_UNAVAILABLE"
    assert code != 0


def test_sync_requires_source(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    try:
        sync_core_model_settings(ws, source_route="agent")
        ok = True
    except ValueError:
        ok = False
    assert ok is False
