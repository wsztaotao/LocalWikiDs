"""R3.32: category multiselect, draggable modals, ctx delete."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_html_multiselect_drag_delete() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="graph-cat-modal-select"' in html
    assert "multiple" in html.split('id="graph-cat-modal-select"', 1)[1][:120]
    assert 'id="graph-ctx-delete"' in html
    assert "lw-modal-drag-handle" in html
    assert 'data-lw-drag-for="graph-cat-modal-card"' in html
    assert 'data-lw-drag-for="graph-title-modal-card"' in html
    assert "新建分类（列表没有时补缺）" in html


def test_css_drag_and_multiselect() -> None:
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert ".lw-modal-drag-handle" in css
    assert ".lw-modal-card--dragging" in css
    assert ".lw-cat-multiselect" in css


def test_app_js_drag_delete_sync() -> None:
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "bindLwModalDrag" in js
    assert "deleteGraphPageFromCtx" in js
    assert "getGraphCatModalSelected" in js
    assert "syncGraphCatModalSelect" in js
    assert "syncGraphCatModalChecks" in js
    assert '/api/pages/delete' in js
