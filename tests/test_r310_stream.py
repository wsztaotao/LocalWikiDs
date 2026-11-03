"""R3.10 streaming job buffer + delta parse."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.opencode_bridge import _delta_from_json_line  # noqa: E402
from localwiki.runtime.session_runner import SessionRunner  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_delta_from_json_line() -> None:
    assert "你好" in _delta_from_json_line(
        '{"type":"text","part":{"text":"你好"}}'
    )
    assert "增量" in _delta_from_json_line('{"type":"delta","text":"增量"}')
    assert "tok" in _delta_from_json_line('{"type":"token","delta":"tok"}')
    assert _delta_from_json_line("not-json") == ""


def test_split_stream_chunks_helper() -> None:
    # Mirror web/app.js splitStreamChunks for contract sanity
    def split_stream_chunks(text: str, chunk_size: int = 24) -> list[str]:
        s = str(text or "")
        size = max(8, chunk_size)
        if len(s) <= size:
            return [s] if s else []
        out: list[str] = []
        i = 0
        while i < len(s):
            end = min(i + size, len(s))
            out.append(s[i:end])
            i = end
        return out

    parts = split_stream_chunks("甲" * 80, 20)
    assert len(parts) >= 3
    assert "".join(parts) == "甲" * 80


def test_job_stream_with_fake_run(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)

    def fake_run(prompt, workspace, *, session_id=None, on_delta=None):
        del prompt, workspace, session_id
        if on_delta:
            on_delta("一")
            on_delta("二")
        return {"ok": True, "stdout": "一二"}

    runner = SessionRunner(ws, run_fn=fake_run)
    sid = runner.store.create()["id"]
    enq = runner.enqueue(sid, "hi")
    job_id = enq["job_id"]
    job = runner.get_job(job_id)
    assert job is not None
    # wait for done
    deadline = time.time() + 5
    while job.status not in ("done", "error") and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == "done"
    assert "一" in job.stream_text or "一二" in job.stream_text
    types = [e.get("type") for e in job.stream_events]
    assert "meta" in types
    assert "done" in types


def test_large_delta_is_sliced(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    blob = "甲" * 80

    def fake_run(prompt, workspace, *, session_id=None, on_delta=None):
        del prompt, workspace, session_id
        if on_delta:
            on_delta(blob)
        return {"ok": True, "stdout": blob}

    runner = SessionRunner(ws, run_fn=fake_run)
    sid = runner.store.create()["id"]
    job = runner.get_job(runner.enqueue(sid, "slice-me")["job_id"])
    assert job is not None
    deadline = time.time() + 8
    while job.status not in ("done", "error") and time.time() < deadline:
        time.sleep(0.05)
    assert job.status == "done"
    deltas = [e for e in job.stream_events if e.get("type") == "delta"]
    assert len(deltas) >= 3
    assert "".join(str(e.get("text") or "") for e in deltas) == blob
