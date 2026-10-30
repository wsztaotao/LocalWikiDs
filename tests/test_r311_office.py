"""R3.11 office bootstrap + exports + office_io."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.exports import (  # noqa: E402
    list_exports,
    materialize_docx_from_text,
    resolve_export,
    resolve_export_existing,
    save_bytes,
)
from localwiki.office_io import read_docx, write_docx, write_xlsx, read_xlsx  # noqa: E402
from localwiki.skills_hub.office_bootstrap import (  # noqa: E402
    bootstrap_office_skills,
    skills_empty,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_bootstrap_office_idempotent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    assert skills_empty(ws)
    r1 = bootstrap_office_skills(ws, force=False)
    assert r1["ok"]
    assert "office-docx" in r1["installed"] or "office-docx" in r1["skipped"]
    skill = ws / ".kw" / "skills" / "office-docx" / "SKILL.md"
    assert skill.is_file()
    r2 = bootstrap_office_skills(ws, force=False)
    assert "office-docx" in r2["skipped"]


def test_exports_roundtrip_and_escape(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    data = write_docx(title="T", bullet_points=["a", "b"])
    name = save_bytes(ws, "notes.docx", data)
    assert name == "notes.docx"
    items = list_exports(ws)
    assert any(i["name"] == "notes.docx" for i in items)
    p = resolve_export(ws, "notes.docx")
    assert "a" in read_docx(p) or "T" in read_docx(p)
    try:
        resolve_export(ws, "../evil.docx")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_xlsx_roundtrip(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    blob = write_xlsx([["h1", "h2"], [1, 2]])
    name = save_bytes(ws, "t.xlsx", blob)
    rows = read_xlsx(resolve_export(ws, name))
    assert rows[0][0] == "h1"


def test_materialize_docx_from_markdownish(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    text = (
        "逻辑哲学论核心观点\n"
        "- **世界的构成**：事实的总和\n"
        "- 语言的功能：图像\n"
        "[下载](/api/exports/logical_philosophy_overview)\n"
    )
    assert resolve_export_existing(ws, "logical_philosophy_overview") is None
    saved = materialize_docx_from_text(ws, "logical_philosophy_overview", text)
    assert saved.endswith(".docx")
    fp = resolve_export_existing(ws, "logical_philosophy_overview")
    assert fp is not None and fp.is_file()
    body = read_docx(fp)
    assert "世界的构成" in body or "事实" in body
