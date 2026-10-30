"""R3.8: session store + runner queue."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.session_runner import SessionRunner  # noqa: E402
from localwiki.sessions import SessionStore  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_session_crud_and_storage(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SessionStore(ws)
    meta = store.create(title="t1", preset_id="local")
    sid = meta["id"]
    store.append_message(sid, {"role": "user", "content": "hi", "ok": True})
    got = store.get(sid)
    assert len(got["messages"]) == 1
    stats = store.storage_stats()
    assert stats["bytes"] >= 0
    assert stats["warn"] is False
    store.delete(sid)
    assert store.list_sessions() == []


def test_clear_keep_extracted(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SessionStore(ws)
    a = store.create(title="a")
    b = store.create(title="b")
    store.write_extract(a["id"], {"writes": [{"path": "memories/x.md"}], "last": "x"})
    out = store.clear(confirm=True, keep_extracted=True)
    assert out["kept"] == 1
    assert out["removed"] == 1
    ids = {s["id"] for s in store.list_sessions()}
    assert a["id"] in ids
    assert b["id"] not in ids


def test_runner_queue_max_two(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    store = SessionStore(ws)
    s1 = store.create(title="1")["id"]
    s2 = store.create(title="2")["id"]
    s3 = store.create(title="3")["id"]

    import threading

    active = {"n": 0}
    max_conc = {"n": 0}
    release = {"go": False}
    lock = threading.Lock()

    def slow_run(prompt: str, workspace: Path) -> dict:
        del prompt, workspace
        with lock:
            active["n"] += 1
            max_conc["n"] = max(max_conc["n"], active["n"])
        try:
            while not release["go"]:
                time.sleep(0.02)
            return {"ok": True, "stdout": "ok"}
        finally:
            with lock:
                active["n"] -= 1

    runner = SessionRunner(ws, run_fn=slow_run, max_running=2)
    runner.enqueue(s1, "one")
    runner.enqueue(s2, "two")
    runner.enqueue(s3, "three")
    deadline = time.time() + 2
    while time.time() < deadline and active["n"] < 2:
        time.sleep(0.02)
    time.sleep(0.1)
    assert active["n"] == 2
    assert max_conc["n"] <= 2
    assert runner.snapshot()["running"] == 2
    release["go"] = True
    deadline = time.time() + 3
    while time.time() < deadline:
        snap = runner.snapshot()
        if snap["running"] == 0 and snap["queued"] == 0:
            break
        time.sleep(0.05)
    assert runner.snapshot()["running"] == 0
    assert max_conc["n"] <= 2
    assert len(store.get(s3)["messages"]) >= 2
