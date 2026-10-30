"""Curated office document skills catalog (R3.11)."""

from __future__ import annotations

from typing import Any

CATALOG_VERSION = "r315.1"

# Bundled skills live under localwiki/skills_bundled/<path>/
OFFICE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "office-docx",
        "kind": "bundled",
        "path": "office_docx",
        "title": "Word (DOCX) read/write",
    },
    {
        "id": "office-xlsx",
        "kind": "bundled",
        "path": "office_xlsx",
        "title": "Excel (XLSX) read/write",
    },
    {
        "id": "office-pdf",
        "kind": "bundled",
        "path": "office_pdf",
        "title": "PDF read",
    },
    {
        "id": "office-pptx",
        "kind": "bundled",
        "path": "office_pptx",
        "title": "PowerPoint (PPTX) read",
        "optional_dep": "pptx",
    },
    {
        "id": "office-tts",
        "kind": "bundled",
        "path": "office_tts",
        "title": "Local TTS (MP3 via edge-tts)",
        "optional_dep": "edge_tts",
    },
]


def catalog_version() -> str:
    return CATALOG_VERSION


def list_catalog() -> list[dict[str, Any]]:
    return [dict(x) for x in OFFICE_CATALOG]
