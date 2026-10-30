"""R3.5: default runtime opencode; local loop gated."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.personal_core import loop_enabled  # noqa: E402
from localwiki.runtime.opencode_bridge import choose_runtime  # noqa: E402
from localwiki.server import LocalWikiServer  # noqa: E402
from localwiki.workspace import ensure_workspace, load_config, save_config  # noqa: E402


def test_choose_runtime_defaults_opencode() -> None:
    assert choose_runtime("any", "auto") == "opencode"
    assert choose_runtime("any", "") == "opencode"


def test_loop_enabled_default_false(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    assert loop_enabled(ws) is False


@pytest.fixture()
def live_server(tmp_path: Path):
    ws = tmp_path / "ws"
    srv = LocalWikiServer(ws, port=0)
    from http.server import ThreadingHTTPServer

    assert srv.acquire_runtime_lock()
    handler = srv.make_handler()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    cfg = load_config(srv.paths["config"])
    base = f"http://127.0.0.1:{srv.port}"
    try:
        yield {"base": base, "token": cfg["api_token"], "ws": ws, "srv": srv}
    finally:
        httpd.shutdown()
        srv.release_runtime_lock()


def _post(base: str, token: str, path: str, body: dict):
    from urllib.error import HTTPError

    req = Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def test_local_runtime_rejected_when_loop_off(live_server) -> None:
    out = _post(
        live_server["base"],
        live_server["token"],
        "/api/agent/exec",
        {"goal": "hi", "runtime": "local"},
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "E_DISABLED"


def test_enable_loop_via_settings(live_server) -> None:
    out = _post(
        live_server["base"],
        live_server["token"],
        "/api/settings",
        {"local_agent_loop_enabled": True},
    )
    assert out["ok"] is True
    assert out["data"]["local_agent_loop_enabled"] is True
    assert loop_enabled(live_server["ws"]) is True
