"""R3.26: generic openable URL rewrite + cancel/stop."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localwiki.runtime.url_rewrite import rewrite_openable_urls


def test_rewrite_port_placeholder_zh():
    out = rewrite_openable_urls(
        "打开 http://127.0.0.1:端口/demo/index.html 看效果",
        port=8765,
    )
    assert "http://127.0.0.1:8765/demo/index.html" in out
    assert "端口" not in out.split("8765")[0][-4:]  # placeholder gone from URL


def test_rewrite_angle_placeholder_and_idempotent():
    s = "http://localhost:<LocalWiki端口>/x"
    once = rewrite_openable_urls(s, port=9001)
    assert once == "http://localhost:9001/x"
    assert rewrite_openable_urls(once, port=9001) == once


def test_rewrite_keeps_numeric_port():
    s = "见 http://127.0.0.1:8765/wiki/index.md 与外网 https://example.com/a"
    assert rewrite_openable_urls(s, port=9999) == s


def test_rewrite_non_demo_body():
    s = "结果页：http://127.0.0.1:{port}/reports/out.html"
    out = rewrite_openable_urls(s, port=8765)
    assert "http://127.0.0.1:8765/reports/out.html" in out


def test_agents_md_version_r326(tmp_path: Path):
    from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md

    path = write_agents_md(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert AGENTS_MD_VERSION.startswith("r326")
    assert "LOCALWIKI_PORT" in text
    assert "Openable addresses" in text
    assert "{port}" in text  # literal placeholder warned against


def test_prepare_injects_localwiki_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from localwiki.runtime import opencode_bridge as br
    from localwiki.workspace import ensure_workspace

    ensure_workspace(tmp_path)
    cfg = tmp_path / ".kw" / "config.json"
    cfg.write_text('{"port": 8123}', encoding="utf-8")
    monkeypatch.delenv("LOCALWIKI_PORT", raising=False)

    fake_bin = tmp_path / "opencode.exe"
    fake_bin.write_text("x", encoding="utf-8")
    monkeypatch.setattr(br, "resolve_bin", lambda workspace=None: fake_bin)

    err, prepared = br._prepare_opencode_run(
        "hi",
        workspace=tmp_path,
        inject_model_env=False,
    )
    assert err is None
    assert prepared is not None
    assert prepared["env"].get("LOCALWIKI_PORT") == "8123"


def test_run_streaming_cancel_kills_proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from localwiki.runtime import opencode_bridge as br

    class FakeProc:
        def __init__(self, *a, **k):
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self._alive = True
            self.killed = False
            self.returncode = None
            self.pid = 4242
            self.stdout.readline = self._readline
            self.stderr.read = lambda: b""

        def _readline(self):
            # Block until killed so cancel watcher / queue path is exercised
            for _ in range(200):
                if not self._alive:
                    return b""
                time.sleep(0.05)
            return b""

        def poll(self):
            return None if self._alive else 1

        def kill(self):
            self.killed = True
            self._alive = False
            self.returncode = 1

        def wait(self, timeout=None):
            return 1

    fake_bin = tmp_path / "oc"
    fake_bin.write_text("x", encoding="utf-8")
    monkeypatch.setattr(br, "resolve_bin", lambda workspace=None: fake_bin)

    prepared = {
        "cmd": [str(fake_bin), "run"],
        "env": {},
        "run_dir": tmp_path,
        "oc_model": None,
        "binary": fake_bin,
    }
    cancel = threading.Event()

    def _later():
        time.sleep(0.12)
        cancel.set()

    threading.Thread(target=_later, daemon=True).start()
    with (
        patch("subprocess.Popen", FakeProc),
        patch("subprocess.run") as _tk,  # taskkill on Windows
    ):
        out = br.run_streaming(
            "x",
            workspace=tmp_path,
            cancel_event=cancel,
            _prepared=prepared,
            hung_idle_sec=9999,
        )
    assert out.get("ok") is False
    assert out.get("error", {}).get("code") == "E_CANCELLED"
    assert out.get("cancelled") is True


def test_cancel_job_queued(tmp_path: Path):
    from localwiki.runtime.session_runner import SessionRunner
    from localwiki.sessions.store import SessionStore
    from localwiki.workspace import ensure_workspace

    ensure_workspace(tmp_path)
    store = SessionStore(tmp_path)
    meta = store.create(title="t")

    def slow_run(*a, **k):
        time.sleep(30)
        return {"ok": True, "stdout": "late"}

    runner = SessionRunner(tmp_path, run_fn=slow_run, max_running=0)
    # max_running=0 prevents pump from starting — but enqueue still queues
    # Actually max_running 0 means pump returns immediately; job stays queued.
    out = runner.enqueue(meta["id"], "hello")
    jid = out["job_id"]
    job = runner.get_job(jid)
    assert job is not None
    assert job.status == "queued"
    c = runner.cancel_job(jid)
    assert c["status"] == "cancelled"
    assert job.status == "cancelled"
    assert job.cancel_event.is_set()
