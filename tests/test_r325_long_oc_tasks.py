"""R3.25: long OC tasks — no wall clock, hung, lw_write, demo_delivered, continue."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from localwiki.workspace import ensure_workspace
from localwiki.runtime import opencode_bridge as ob
from localwiki.runtime.intent_route import (
    demo_delivered,
    demo_preview_ok,
    looks_like_oc_dump,
    synthesize_incomplete_reply,
)
from localwiki.runtime.oc_events import OcRunState, parse_ndjson_line
from localwiki.runtime.session_runner import SessionRunner
from localwiki.sessions.store import SessionStore
from localwiki.tools import lw_write_cli


class _FakeOut:
    def __init__(self, lines: list[str], *, pause_after: int | None = None) -> None:
        self._lines = [ln.encode("utf-8") for ln in lines]
        self._i = 0
        self.done = False
        self.pause_after = pause_after
        self._paused = False

    def readline(self):
        if self.pause_after is not None and self._i >= self.pause_after and not self._paused:
            self._paused = True
            time.sleep(0.05)
            return b""
        if self._i >= len(self._lines):
            self.done = True
            return b""
        b = self._lines[self._i]
        self._i += 1
        return b


class _FakeErr:
    def read(self):
        return b""


def _fake_proc(lines: list[str], *, hang: bool = False):
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeOut(lines, pause_after=(len(lines) if hang else None))
            self.stderr = _FakeErr()
            self.returncode = 0 if not hang else None
            self._killed = False

        def poll(self):
            if self._killed:
                return 1
            if hang and self.stdout._paused:
                return None
            return 0 if self.stdout.done else None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self._killed = True
            self.returncode = 1
            self.stdout.done = True

    return FakeProc()


def test_parse_tool_use_state_and_session_status() -> None:
    tool = parse_ndjson_line(
        json.dumps(
            {
                "type": "tool_use",
                "name": "write",
                "sessionID": "ses_abc",
                "part": {
                    "type": "tool",
                    "tool": "write",
                    "state": {"status": "running", "input": {"path": "a.css"}},
                },
            }
        )
    )
    assert tool is not None
    assert tool["kind"] == "tool"
    assert tool["phase"] == "start"
    assert tool["session_id"] == "ses_abc"
    assert tool.get("title") == "a.css"

    st = parse_ndjson_line(
        json.dumps(
            {
                "type": "session.status",
                "properties": {"status": {"type": "busy"}},
                "sessionID": "ses_abc",
            }
        )
    )
    assert st is not None
    assert st["kind"] == "session_status"
    assert st["status"] == "busy"


def test_hung_not_when_busy() -> None:
    state = OcRunState()
    state.note_ndjson(100.0)
    state.set_session_status("busy")
    assert state.should_hung_kill(2000.0, idle_sec=1800.0) is False
    state.set_session_status("idle")
    assert state.should_hung_kill(2000.0, idle_sec=1800.0) is True


def test_run_streaming_no_wall_timeout_when_none(monkeypatch, tmp_path: Path) -> None:
    ensure_workspace(tmp_path)
    lines = [
        json.dumps({"type": "session.status", "properties": {"status": {"type": "busy"}}, "sessionID": "s1"})
        + "\n",
        json.dumps(
            {
                "type": "tool_use",
                "name": "bash",
                "sessionID": "s1",
                "part": {"type": "tool", "tool": "bash", "state": {"status": "running"}},
            }
        )
        + "\n",
        json.dumps({"type": "text", "part": {"text": "done"}, "sessionID": "s1"}) + "\n",
        json.dumps({"type": "session.idle", "sessionID": "s1"}) + "\n",
    ]
    events: list[dict] = []
    monkeypatch.setattr(ob.subprocess, "Popen", lambda *a, **k: _fake_proc(lines))
    prepared = {
        "cmd": ["fake"],
        "env": {},
        "run_dir": str(tmp_path),
        "oc_model": "m",
        "binary": tmp_path / "bin",
    }
    (tmp_path / "bin").write_text("", encoding="utf-8")
    out = ob.run_streaming(
        "hi",
        workspace=tmp_path,
        timeout=None,
        on_event=lambda e: events.append(e),
        _prepared=prepared,
        hung_idle_sec=99999,
    )
    assert out.get("ok") is True
    assert out.get("oc_session_id") == "s1"
    assert any(e.get("type") == "oc_status" for e in events)


def test_run_streaming_hung_when_idle_silent(monkeypatch, tmp_path: Path) -> None:
    ensure_workspace(tmp_path)
    clock = {"t": 1000.0}

    def now() -> float:
        return clock["t"]

    lines = [
        json.dumps({"type": "session.idle", "sessionID": "s2"}) + "\n",
    ]
    proc = _fake_proc(lines, hang=True)
    monkeypatch.setattr(ob.subprocess, "Popen", lambda *a, **k: proc)

    def advance_and_read():
        # after first line consumed, jump clock past hung window
        while not proc.stdout._paused and not proc.stdout.done:
            time.sleep(0.01)
        clock["t"] = 1000.0 + 2000.0

    threading.Thread(target=advance_and_read, daemon=True).start()
    prepared = {
        "cmd": ["fake"],
        "env": {},
        "run_dir": str(tmp_path),
        "oc_model": "m",
        "binary": tmp_path / "bin",
    }
    (tmp_path / "bin").write_text("", encoding="utf-8")
    out = ob.run_streaming(
        "hi",
        workspace=tmp_path,
        timeout=None,
        _prepared=prepared,
        hung_idle_sec=1800.0,
        now_fn=now,
    )
    assert out.get("ok") is False
    assert (out.get("error") or {}).get("code") == "E_OC_HUNG"


def test_lw_write_cli_roundtrip_and_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_workspace(tmp_path)
    monkeypatch.setenv("LOCALWIKI_WORKSPACE", str(tmp_path))
    big = "x" * 50_000
    content_file = tmp_path / "payload.txt"
    content_file.write_text(big, encoding="utf-8")
    rc = lw_write_cli.main(
        ["--path", ".kw/demo/big.css", "--content-file", str(content_file)]
    )
    assert rc == 0
    assert (tmp_path / ".kw" / "demo" / "big.css").read_text(encoding="utf-8") == big
    rc2 = lw_write_cli.main(["--path", "../outside.txt", "--content-file", str(content_file)])
    assert rc2 == 2


def test_demo_delivered_requires_assets(tmp_path: Path) -> None:
    ensure_workspace(tmp_path)
    reply = "请打开 /demo/index.html"
    assert demo_preview_ok(reply)
    assert demo_delivered(tmp_path, reply) is False
    demo = tmp_path / ".kw" / "demo"
    demo.mkdir(parents=True, exist_ok=True)
    (demo / "index.html").write_text(
        '<link rel="stylesheet" href="styles.css"><script src="app.js"></script>',
        encoding="utf-8",
    )
    assert demo_delivered(tmp_path, reply) is False
    (demo / "styles.css").write_text("body{}", encoding="utf-8")
    (demo / "app.js").write_text("console.log(1)", encoding="utf-8")
    assert demo_delivered(tmp_path, reply) is True


def test_dump_filter_helpers() -> None:
    dump = "## Objective\nfoo\n## Next Move\nbar\nContinue if you have next steps"
    assert looks_like_oc_dump(dump)
    s = synthesize_incomplete_reply(
        error={"code": "E_OC_HUNG", "message": "silent"},
        missing=[".kw/demo/styles.css"],
        can_continue=True,
    )
    assert "E_OC_HUNG" in s
    assert "继续" in s


def test_sse_emits_tool_before_done(tmp_path: Path) -> None:
    ensure_workspace(tmp_path)
    store = SessionStore(tmp_path)
    meta = store.create(title="t")

    def run_fn(prompt, workspace, **kwargs):
        on_tool = kwargs.get("on_tool")
        on_event = kwargs.get("on_event")
        if on_event:
            on_event(
                {
                    "type": "oc_status",
                    "busy": True,
                    "session_status": "busy",
                    "tool": {"name": "lw_write", "phase": "start"},
                }
            )
        if on_tool:
            on_tool({"name": "lw_write", "phase": "start", "ok": None, "title": "a.css"})
            on_tool({"name": "lw_write", "phase": "end", "ok": True})
        if on_event:
            on_event({"type": "oc_status", "busy": False, "session_status": "idle"})
        return {
            "ok": True,
            "stdout": "预览 /demo/index.html",
            "oc_session_id": "oc_test_1",
            "tool_events": [{"name": "lw_write"}],
        }

    runner = SessionRunner(tmp_path, run_fn=run_fn)
    out = runner.enqueue(meta["id"], "做个彩色 html 首页让我打开看看")
    job = runner.get_job(out["job_id"])
    assert job is not None
    deadline = time.time() + 5
    while job.status in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
    types = [e.get("type") for e in job.stream_events]
    assert "tool" in types or "oc_status" in types
    done_i = next(i for i, t in enumerate(types) if t == "done")
    pre = types[:done_i]
    assert "tool" in pre or "oc_status" in pre or "status" in pre
    refreshed = store.get(meta["id"])
    assert refreshed.get("oc_session_ref") == "oc_test_1"


def test_continue_requires_oc_ref(tmp_path: Path) -> None:
    ensure_workspace(tmp_path)
    store = SessionStore(tmp_path)
    meta = store.create(title="t")
    runner = SessionRunner(
        tmp_path, run_fn=lambda *a, **k: {"ok": True, "stdout": "hi"}
    )
    with pytest.raises(ValueError, match="E_NO_OC_SESSION"):
        runner.enqueue_continue(meta["id"])
    store.update_meta(meta["id"], oc_session_ref="ses_x")
    out = runner.enqueue_continue(meta["id"], message="继续补 css")
    assert out.get("job_id")
