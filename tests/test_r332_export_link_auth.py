"""R3.32: workbench inline /api/exports/ links must use auth download path."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "web" / "app.js"


def test_app_js_wires_export_links_with_auth_download():
    src = APP_JS.read_text(encoding="utf-8")
    assert "wb-export-link" in src
    assert "wireExportLinks" in src
    assert "wbExportName" in src
    assert "data-export" in src
    # Must not open export paths in a new tab without Bearer
    assert 'wb-export-link"' in src or "wb-export-link'" in src
    assert "downloadExport(n)" in src
