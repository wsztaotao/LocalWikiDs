"""R3.23: /demo mount, intent route, catalog compact, AGENTS preview rules."""

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

from localwiki.runtime.demo_static import resolve_demo_file  # noqa: E402
from localwiki.runtime.intent_route import (  # noqa: E402
    classify_user_intent,
    demo_nudge_prompt,
    demo_preview_ok,
    should_strip_export_history,
)
from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.server import LocalWikiServer  # noqa: E402
from localwiki.skills_hub.catalog_expose import format_catalog_md  # noqa: E402
from localwiki.workspace import load_config  # noqa: E402


def test_classify_intent_order() -> None:
    assert classify_user_intent("写个彩色 HTML 首页，我要看到页面") == "demo"
    assert classify_user_intent("约2000字短文并导出 Word") == "export_long"
    assert classify_user_intent("三句话简介导出docx") == "export_short"
    assert classify_user_intent("今天天气怎么样") == "chat"


def test_demo_preview_and_nudge() -> None:
    assert demo_preview_ok("打开 /demo/index.html 查看")
    assert demo_preview_ok("http://127.0.0.1:8765/demo/index.html")
    assert not demo_preview_ok("请 pip install Flask 后访问 5000")
    n = demo_nudge_prompt("要看到页面")
    assert ".kw/demo" in n
    assert "/demo/" in n
    assert "http.server" in n.lower() or "不必" in n or "不要另起" in n


def test_should_strip_history() -> None:
    assert should_strip_export_history("chat")
    assert should_strip_export_history("demo")
    assert not should_strip_export_history("export_short")


def test_resolve_demo_path_traversal(tmp_path: Path) -> None:
    demo = tmp_path / ".kw" / "demo"
    demo.mkdir(parents=True)
    (demo / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    assert resolve_demo_file(tmp_path, "/demo/index.html") is not None
    assert resolve_demo_file(tmp_path, "/demo/../secret.txt") is None
    assert resolve_demo_file(tmp_path, "/demo/../../secret.txt") is None


def test_catalog_compact_office_only() -> None:
    entries = [
        {"name": "office-docx", "kind": "skill", "description": "Word"},
        {
            "name": "byteplus-seed-speech-tts@local",
            "kind": "skill",
            "description": "Production guidance for BytePlus",
        },
        {"name": "docx@local", "kind": "skill", "description": "third party docx"},
    ]
    md = format_catalog_md(entries, compact=True)
    assert "office-docx" in md
    assert "byteplus" not in md.split("Other installed")[0]
    assert "Other installed" in md
    assert ".kw/skills" in md


def test_agents_r323_demo_path(tmp_path: Path) -> None:
    # R3.25+ still supersedes r323 demo contract in AGENTS.md
    assert AGENTS_MD_VERSION.startswith("r32")
    text = write_agents_md(tmp_path).read_text(encoding="utf-8")
    assert "/demo/" in text
    assert ".kw/demo" in text
    assert "http.server" in text  # mentioned as do-not primary
    assert "Do **not** start a separate" in text or "不必" in text or "Do **not** start" in text
    assert "lw_write" in text


@pytest.fixture()
def live_server(tmp_path: Path):
    ws = tmp_path / "ws"
    srv = LocalWikiServer(ws, port=0)
    from http.server import ThreadingHTTPServer

    if not srv.acquire_runtime_lock():
        raise RuntimeError("lock failed")
    handler = srv.make_handler()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    cfg = load_config(srv.paths["config"])
    base = f"http://127.0.0.1:{srv.port}"
    try:
        yield {"base": base, "token": cfg["api_token"], "srv": srv, "ws": ws}
    finally:
        httpd.shutdown()
        srv.release_runtime_lock()


def test_demo_http_200_and_traversal(live_server) -> None:
    ws: Path = live_server["ws"]
    base = live_server["base"]
    demo = ws / ".kw" / "demo"
    demo.mkdir(parents=True)
    (demo / "index.html").write_text("<html><body>R323</body></html>", encoding="utf-8")
    with urlopen(base + "/demo/index.html", timeout=5) as r:
        assert r.status == 200
        assert b"R323" in r.read()
    try:
        urlopen(base + "/demo/../AGENTS.md", timeout=5)
        raise AssertionError("expected 404")
    except HTTPError as e:
        assert e.code == 404
