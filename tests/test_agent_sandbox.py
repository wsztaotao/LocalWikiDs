"""AT-AGT-04: Job Object process-tree kill + env scrub (LW-34 / §11.8)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.sandbox import (  # noqa: E402
    DockerSandbox,
    SandboxLimits,
    SandboxResult,
    WindowsJobSandbox,
    kill_process_tree_demo,
    run_sandboxed,
    scrub_environ,
)


def test_scrub_environ_drops_secrets() -> None:
    dirty = {
        "PATH": "C:\\Windows\\System32",
        "API_KEY": "sk-secret",
        "OPENAI_API_KEY": "sk-x",
        "MY_TOKEN": "abc",
        "TEMP": "C:\\Temp",
        "LOCALWIKI_PASSWORD": "nope",
    }
    clean = scrub_environ(dirty)
    assert "PATH" in clean
    assert "TEMP" in clean
    assert "API_KEY" not in clean
    assert "OPENAI_API_KEY" not in clean
    assert "MY_TOKEN" not in clean
    assert "LOCALWIKI_PASSWORD" not in clean


def test_scrub_environ_extra_cannot_inject_secret() -> None:
    clean = scrub_environ({"PATH": "x"}, extra={"SECRET_TOKEN": "leak", "TASK_ID": "t1"})
    assert "SECRET_TOKEN" not in clean
    assert clean.get("TASK_ID") == "t1"  # non-secret extras allowed
    assert clean.get("PATH") == "x"


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is Windows-only")
def test_job_object_kills_process_tree_on_timeout() -> None:
    """超时后关闭 Job → 子进程树被终止（KILL_ON_JOB_CLOSE）。"""
    report = kill_process_tree_demo(child_script_seconds=60.0)
    assert report.get("ok") is True, report
    assert report["timed_out"] is True
    assert report["killed"] is True
    assert report["elapsed_s"] < 20  # must not wait for 60s sleep


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is Windows-only")
def test_windows_job_runs_short_command() -> None:
    sb = WindowsJobSandbox(limits=SandboxLimits(timeout_s=10, process_limit=8))
    result = sb.run([sys.executable, "-c", "print('sandbox-ok')"])
    assert result.ok
    assert "sandbox-ok" in result.stdout
    assert result.timed_out is False


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is Windows-only")
def test_run_sandboxed_windows_dispatch() -> None:
    result = run_sandboxed(
        [sys.executable, "-c", "print(1)"],
        mode="windows",
        limits=SandboxLimits(timeout_s=10),
    )
    assert result.ok
    assert result.mode == "windows"


def test_docker_sandbox_injectable_runner() -> None:
    def fake_runner(argv, *, timeout_s):
        assert "--network=none" in argv
        assert any(a.startswith("--memory=") for a in argv)
        return SandboxResult(
            ok=True, exit_code=0, stdout="docker-ok", stderr="", mode="docker"
        )

    sb = DockerSandbox(network="none", runner=fake_runner)
    result = sb.run(["echo", "hi"])
    assert result.ok
    assert result.stdout == "docker-ok"


def test_secrets_not_in_child_env_via_scrub() -> None:
    """Child env builder never includes secret-like keys (AT-AGT-04)."""
    os.environ["LOCALWIKI_TEST_API_KEY"] = "should-not-pass"
    try:
        env = scrub_environ()
        assert "LOCALWIKI_TEST_API_KEY" not in env
        for k in env:
            assert "KEY" not in k.upper() or k.upper() in {"PATH"}  # PATH ok
            assert "SECRET" not in k.upper()
            assert "TOKEN" not in k.upper()
    finally:
        os.environ.pop("LOCALWIKI_TEST_API_KEY", None)
