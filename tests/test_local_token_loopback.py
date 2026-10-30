"""R3.5 RP1: /api/local-token loopback bootstrap."""

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

from localwiki.server import LocalWikiServer  # noqa: E402
from localwiki.workspace import load_config  # noqa: E402


@pytest.fixture()
def live_server(tmp_path: Path):
    ws = tmp_path / "ws"
    srv = LocalWikiServer(ws, port=0)
    from http.server import ThreadingHTTPServer

    assert srv.acquire_runtime_lock()
    handler = srv.make_handler()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.port = httpd.server_address[1]
    srv._httpd = httpd
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    cfg = load_config(srv.paths["config"])
    base = f"http://127.0.0.1:{srv.port}"
    try:
        yield {"base": base, "token": cfg["api_token"], "srv": srv}
    finally:
        httpd.shutdown()
        srv.release_runtime_lock()


def test_local_token_no_auth(live_server) -> None:
    req = Request(live_server["base"] + "/api/local-token")
    with urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert data["ok"] is True
    assert data["data"]["token"] == live_server["token"]


def test_status_includes_token_when_authed(live_server) -> None:
    token = live_server["token"]
    req = Request(
        live_server["base"] + "/api/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert data["ok"] is True
    assert data["data"]["token"] == token
    assert data["data"]["token_present"] is True
