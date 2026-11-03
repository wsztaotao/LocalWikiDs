"""R3.15 export fidelity + local TTS."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.exports import (  # noqa: E402
    resolve_export_existing,
    save_bytes,
    unique_export_name,
)
from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.runtime.session_runner import SessionRunner  # noqa: E402
from localwiki.skills_hub.office_catalog import list_catalog  # noqa: E402
from localwiki.tts_io import TtsError, synthesize_mp3  # noqa: E402
from localwiki.wiki_context import build_prompt_with_wiki  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_wiki_miss_no_forced_preamble() -> None:
    p = build_prompt_with_wiki("写一首深秋散文", {"hit": False, "context_text": ""})
    assert "未命中" not in p
    assert p.startswith("【用户问题】")
    assert "深秋散文" in p


def test_unique_export_name_distinct() -> None:
    a = unique_export_name("autumn", ".docx")
    b = unique_export_name("autumn", ".docx")
    assert a.endswith(".docx")
    assert a.startswith("autumn_")
    assert a != b
    c = unique_export_name("speech", ".mp3")
    assert c.endswith(".mp3")


def test_ensure_export_strips_missing_links(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    runner = SessionRunner(ws, run_fn=lambda *a, **k: {"ok": True, "stdout": ""})
    text = "散文写好了\n/api/exports/missing_autumn.docx\n请下载"
    out = runner._ensure_export_files(text)
    assert "/api/exports/missing_autumn.docx" not in out
    assert "尚未生成" in out

    real = unique_export_name("ok", ".docx")
    save_bytes(ws, real, b"PK\x03\x04fake")
    text2 = f"好了 /api/exports/{real}"
    out2 = runner._ensure_export_files(text2)
    assert f"/api/exports/{real}" in out2
    assert "尚未生成" not in out2


def test_ensure_no_auto_materialize_export_docx(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    runner = SessionRunner(ws, run_fn=lambda *a, **k: {"ok": True, "stdout": ""})
    out = runner._ensure_export_files("由于当前环境无法生成 Word，请本地查看。")
    assert "/api/exports/" not in out
    assert resolve_export_existing(ws, "export.docx") is None


def test_office_tts_in_catalog() -> None:
    ids = {x["id"] for x in list_catalog()}
    assert "office-tts" in ids


def test_agents_md_mentions_tts_and_unique(tmp_path: Path) -> None:
    path = write_agents_md(tmp_path / "run")
    text = path.read_text(encoding="utf-8")
    assert AGENTS_MD_VERSION.startswith("r31")  # r315+ / r316+
    assert "export_docx" in text or "tts_io" in text or "synthesize_mp3" in text
    assert "未命中本地知识库" in text or "缺少" in text  # must forbid empty excuses


def test_synthesize_mp3_empty() -> None:
    with pytest.raises(TtsError) as ei:
        synthesize_mp3("  ")
    assert ei.value.code == "E_INVALID_INPUT"


def test_synthesize_mp3_mocked() -> None:
    fake = b"ID3" + b"\x00" * 64

    class _Comm:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            yield {"type": "audio", "data": fake}

    with patch("edge_tts.Communicate", _Comm):
        data = synthesize_mp3("深秋的风掠过湖面。")
    assert data.startswith(b"ID3")
    assert len(data) >= 32
