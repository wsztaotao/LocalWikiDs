"""R1: HTTP API component tests."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
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
    srv = LocalWikiServer(ws, port=0)  # port assigned below
    # ThreadingHTTPServer needs concrete port; bind 0 then read
    from http.server import ThreadingHTTPServer

    if not srv.acquire_runtime_lock():
        raise RuntimeError("lock failed")
    handler = srv.make_handler()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.port = httpd.server_address[1]
    srv._httpd = httpd
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    # wait ready
    time.sleep(0.05)
    cfg = load_config(srv.paths["config"])
    token = cfg["api_token"]
    base = f"http://127.0.0.1:{srv.port}"
    try:
        yield {"base": base, "token": token, "srv": srv, "ws": ws}
    finally:
        httpd.shutdown()
        srv.release_runtime_lock()


def _req(base: str, token: str, method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_status_requires_token(live_server) -> None:
    base = live_server["base"]
    req = Request(base + "/api/status")
    with pytest.raises(HTTPError) as ei:
        urlopen(req, timeout=5)
    assert ei.value.code == 401


def test_propose_review_pages(live_server) -> None:
    base = live_server["base"]
    token = live_server["token"]
    st = _req(base, token, "GET", "/api/status")
    assert st["ok"] is True
    assert st["data"]["snapshot_id"]

    prop = _req(
        base,
        token,
        "POST",
        "/api/propose",
        {
            "purpose": "demo page",
            "ops": [
                {
                    "op": "create",
                    "path": "demo.md",
                    "content": "# Demo\n\nhello from test\n",
                }
            ],
        },
    )
    assert prop["ok"] is True
    uid = prop["data"]["change_uid"]

    listing = _req(base, token, "GET", "/api/proposals")
    assert any(p["change_uid"] == uid for p in listing["data"])

    rev = _req(
        base,
        token,
        "POST",
        "/api/review",
        {"uid": uid, "action": "accept"},
    )
    assert rev["ok"] is True
    assert rev["data"]["state"] == "accepted"

    pages = _req(base, token, "GET", "/api/pages")
    assert any(p["path"] == "demo.md" for p in pages["data"])

    content = _req(base, token, "GET", "/api/pages/content?path=demo.md")
    assert "hello from test" in content["data"]["content"]
