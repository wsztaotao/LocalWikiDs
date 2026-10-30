"""R2: import → compile → accept → pages."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from http.server import ThreadingHTTPServer

import pytest

from localwiki.server import LocalWikiServer  # noqa: E402
from localwiki.sources import compile_source, import_bytes  # noqa: E402
from localwiki.workspace import load_config  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "extractors"


@pytest.fixture()
def live_server(tmp_path: Path):
    ws = tmp_path / "ws"
    srv = LocalWikiServer(ws, port=0)
    assert srv.acquire_runtime_lock()
    handler = srv.make_handler()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.port = httpd.server_address[1]
    srv._httpd = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.05)
    token = load_config(srv.paths["config"])["api_token"]
    try:
        yield {"base": f"http://127.0.0.1:{srv.port}", "token": token, "ws": ws, "srv": srv}
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
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_import_compile_accept_offline(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    md = (FIX / "sample.md").read_bytes()
    meta = import_bytes(ws, md, filename="sample.md")
    assert meta["format"] == "md"
    assert meta["deduped"] is False
    out = compile_source(ws, meta["source_uid"], mode="heuristic")
    assert out.get("compile_mode") == "heuristic"
    uid = out["changeset"]["change_uid"]
    from localwiki.core import CommitService

    cs = CommitService(ws).accept(uid)
    assert cs.state == "accepted"
    pages = CommitService(ws).list_pages()
    assert any(p["path"].startswith("references/") for p in pages)


def test_api_import_compile(live_server) -> None:
    base, token = live_server["base"], live_server["token"]
    text = "# API Import\n\nHello from import API.\n"
    imp = _req(
        base,
        token,
        "POST",
        "/api/import",
        {"filename": "api.md", "content": text},
    )
    assert imp["ok"] is True
    sid = imp["data"]["source_uid"]
    sources = _req(base, token, "GET", "/api/sources")
    assert any(s["source_uid"] == sid for s in sources["data"])
    compiled = _req(
        base, token, "POST", "/api/compile", {"source_uid": sid, "mode": "heuristic"}
    )
    assert compiled["ok"] is True
    assert compiled["data"].get("compile_mode") == "heuristic"
    uid = compiled["data"]["changeset"]["change_uid"]
    rev = _req(base, token, "POST", "/api/review", {"uid": uid, "action": "accept"})
    assert rev["data"]["state"] == "accepted"
