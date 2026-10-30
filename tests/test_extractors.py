"""R2: extractors for MD/TXT/PDF/DOCX."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.extractors import ExtractError, extract_path  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "extractors"


def test_extract_md() -> None:
    r = extract_path(FIX / "sample.md")
    assert r.format == "md"
    assert "Sample MD" in r.text
    assert r.sha256
    assert r.locators


def test_extract_txt() -> None:
    r = extract_path(FIX / "sample.txt")
    assert r.format == "txt"
    assert "Plain text" in r.text


def test_extract_pdf() -> None:
    r = extract_path(FIX / "sample.pdf")
    assert r.format == "pdf"
    # Minimal PDF may yield empty text depending on parser; still must succeed
    assert r.sha256
    assert isinstance(r.text, str)


def test_extract_docx() -> None:
    r = extract_path(FIX / "sample.docx")
    assert r.format == "docx"
    assert "DOCX" in r.text or "LocalWiki" in r.text


def test_extract_rejects_huge(tmp_path: Path) -> None:
    p = tmp_path / "big.txt"
    p.write_bytes(b"x" * 100)
    with pytest.raises(ExtractError):
        extract_path(p, max_bytes=10)
