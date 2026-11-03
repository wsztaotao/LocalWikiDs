"""R3.29: import + proposals merged into one panel with tabs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def test_sidebar_has_import_not_proposals_nav() -> None:
    assert 'data-panel="import"' in HTML
    assert 'data-panel="proposals"' not in HTML
    assert 'id="panel-proposals"' not in HTML
    assert 'id="tab-import-sources"' in HTML
    assert 'id="tab-import-proposals"' in HTML
    assert 'id="pane-import-sources"' in HTML
    assert 'id="pane-import-proposals"' in HTML
    assert 'id="source-list"' in HTML
    assert 'id="proposal-list"' in HTML


def test_app_js_has_subtab_and_navigate() -> None:
    assert "function showImportSubTab" in APP
    assert "function afterCompileNavigate" in APP
    assert "auto_accepted" in APP
    assert 'name === "proposals"' in APP
