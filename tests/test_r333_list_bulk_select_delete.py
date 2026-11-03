"""R3.33: wiki page list + workbench sessions select-all and bulk delete."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"
INDEX = ROOT / "web" / "index.html"


def test_html_has_select_all_and_bulk_delete_controls():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="wiki-pages-select-all"' in html
    assert 'id="btn-wiki-pages-delete"' in html
    assert 'id="wb-sessions-select-all"' in html
    assert 'id="btn-wb-sessions-delete"' in html


def test_app_js_wires_bulk_delete_helpers():
    src = APP_JS.read_text(encoding="utf-8")
    assert "deleteSelectedWikiPages" in src
    assert "deleteSelectedSessions" in src
    assert "wiki-page-cb" in src
    assert "wb-session-cb" in src
    assert "syncWikiPagesSelectAllState" in src
    assert "syncWbSessionsSelectAllState" in src
    assert 'POST", "/api/pages/delete"' in src or "POST\", \"/api/pages/delete\"" in src
