"""R3.31: graph context menu visibility + category/title modals."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_html_has_ctx_menu_and_modals() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="graph-ctx-menu"' in html
    assert "lw-ctx-menu" in html
    assert "dropdown-menu" not in html.split('id="graph-ctx-menu"', 1)[1][:200]
    assert 'id="graph-cat-modal"' in html
    assert 'id="graph-title-modal"' in html
    assert 'id="graph-cat-new-input"' in html
    assert 'id="btn-graph-cat-add"' in html
    assert 'id="graph-modal-title-input"' in html
    assert 'id="graph-ctx-edit-cats"' in html
    assert 'id="graph-ctx-edit-title"' in html


def test_css_self_managed_ctx_menu() -> None:
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert ".lw-ctx-menu" in css
    assert ".lw-modal" in css
    assert ".lw-modal-cat-list" in css


def test_app_js_ctx_show_not_only_dnone() -> None:
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function showGraphCtxMenu" in js
    assert "lw-ctx-menu" in (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "openGraphCatModal" in js
    assert "openGraphTitleModal" in js
    assert "addGraphCatFromModalInput" in js
    assert "graphCtxIgnoreClickUntil" in js
    assert "onGraphPageContext" in js
    # Must not rely solely on Bootstrap dropdown-menu .show trap
    assert 'classList.remove("d-none")' in js
    assert "openLwModal" in js
