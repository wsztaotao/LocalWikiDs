"""R3.17 PDF export via reportlab + export_pdf tool."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.exports import resolve_export_existing  # noqa: E402
from localwiki.office_io import write_pdf  # noqa: E402
from localwiki.runtime.export_intent import (  # noqa: E402
    export_delivered,
    export_intent,
    nudge_prompt,
    wants_docx,
    wants_pdf,
)
from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.runtime.opencode_bridge import sync_oc_tools  # noqa: E402
from localwiki.tools.oc_export_cli import main as cli_main  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_write_pdf_header_and_chinese() -> None:
    data = write_pdf(title="深秋", paragraphs=["落叶萧萧，湖面如镜。", "第二段。"])
    assert data.startswith(b"%PDF")
    assert len(data) > 200


def test_oc_export_cli_pdf(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    monkeypatch.setenv("LOCALWIKI_WORKSPACE", str(ws))
    rc = cli_main(
        [
            "pdf",
            "--title",
            "OpenCode 介绍",
            "--body",
            "第一段。\n\n第二段关于工具。",
            "--stem",
            "intro",
        ]
    )
    assert rc == 0
    files = list((ws / ".kw" / "exports").glob("intro_*.pdf"))
    assert files
    assert files[0].read_bytes().startswith(b"%PDF")


def test_sync_oc_tools_includes_export_pdf(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    dest = sync_oc_tools(ws)
    assert (dest / "export_pdf.ts").is_file()
    assert "export_pdf" in (dest / "export_pdf.ts").read_text(encoding="utf-8")


def test_pdf_intent_and_delivered(tmp_path: Path) -> None:
    assert export_intent("输出到word和pdf")
    assert wants_pdf("分别输出到pdf文档")
    assert wants_docx("保存成 Word 文档")
    assert not wants_docx("只要 pdf 下载")  # 导出 alone removed from docx

    ws = tmp_path / "ws"
    ensure_workspace(ws)
    from localwiki.exports import save_bytes, unique_export_name
    from localwiki.office_io import write_docx

    docx = unique_export_name("only", ".docx")
    save_bytes(ws, docx, write_docx(title="t", paragraphs=["p"]))
    assert not export_delivered(
        ws,
        result={},
        out_text=f"/api/exports/{docx}",
        job_started=0,
        user_text="写介绍保存到word和pdf下载",
    )
    pdf_name = unique_export_name("also", ".pdf")
    save_bytes(ws, pdf_name, write_pdf(title="t", paragraphs=["p"]))
    assert export_delivered(
        ws,
        result={},
        out_text=f"/api/exports/{docx}\n/api/exports/{pdf_name}",
        job_started=0,
        user_text="写介绍保存到word和pdf下载",
    )
    assert resolve_export_existing(ws, pdf_name)


def test_agents_mentions_export_pdf_and_no_python_excuse(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    write_agents_md(ws, catalog_md="- office-pdf: read/write")
    text = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "export_pdf" in text
    assert AGENTS_MD_VERSION.startswith("r3")  # r317+ … r321+
    assert "Do **not** say Python is missing" in text
    assert "未安装 Python" in nudge_prompt("pdf please")


def test_nudge_mentions_pdf() -> None:
    p = nudge_prompt("need pdf")
    assert "export_pdf" in p
    assert "未安装 Python" in p
