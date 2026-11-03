"""AT-AGT-10: agent exec returns JSON; E_APPROVAL_REQUIRED exit (LW-37)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.__main__ import (  # noqa: E402
    E_APPROVAL_REQUIRED,
    EXIT_APPROVAL_REQUIRED,
    EXIT_OK,
    run_agent_exec,
)
from localwiki.tools.registry import build_default_registry  # noqa: E402


class _R3Model:
    def generate(self, messages, *, tools, extras):
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "win_command",
                                    "arguments": '{"command":"whoami"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }


class _FinishModel:
    """Immediate stop — used when a real model is injected (no offline stub)."""

    def generate(self, messages, *, tools, extras):
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }


def test_agent_exec_json_success(tmp_path: Path) -> None:
    payload, code = run_agent_exec(
        "summarize notes",
        workspace=tmp_path,
        mode="read-only",
        model=_FinishModel(),
    )
    assert code == EXIT_OK
    assert payload["ok"] is True
    assert "request_id" in payload
    assert payload["data"]["status"] == "completed"


def test_agent_exec_requires_model_when_none(tmp_path: Path) -> None:
    payload, code = run_agent_exec(
        "summarize notes",
        workspace=tmp_path,
        mode="read-only",
    )
    assert code != EXIT_OK
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_MODEL_UNAVAILABLE"


def test_agent_exec_approval_required(tmp_path: Path) -> None:
    payload, code = run_agent_exec(
        "run shell",
        workspace=tmp_path,
        mode="workspace-auto",
        model=_R3Model(),
        registry=build_default_registry(),
    )
    assert code == EXIT_APPROVAL_REQUIRED
    assert payload["ok"] is False
    assert payload["error"]["code"] == E_APPROVAL_REQUIRED
    assert payload["error"]["approval_id"]


def test_cli_agent_exec_subprocess_json(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "localwiki",
            "--workspace",
            str(tmp_path),
            "agent",
            "exec",
            "hello world",
            "--mode",
            "read-only",
            "--json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    # R3.5: unconfigured model must fail honestly (no offline stub PASS)
    assert proc.returncode != EXIT_OK
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert data["error"]["code"] == "E_MODEL_UNAVAILABLE"
