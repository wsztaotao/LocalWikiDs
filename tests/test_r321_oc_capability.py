"""R3.21: export split, AGENTS demo rules, write permission, compact filter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.export_intent import (  # noqa: E402
    is_long_form,
    nudge_prompt,
    strip_export_promo,
)
from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.runtime.opencode_bridge import write_opencode_run_config  # noqa: E402
from localwiki.runtime.session_runner import _compact_prompt  # noqa: E402


def test_agents_md_version_r32x() -> None:
    # R3.23 bumped stamp; prior r321 rules still covered by newer template.
    assert AGENTS_MD_VERSION.startswith("r32")


def test_is_long_form_boundaries() -> None:
    assert not is_long_form("写三句话简介并导出 Word")
    assert not is_long_form("约799字并导出docx")
    assert is_long_form("约800字短文并导出 Word")
    assert is_long_form("写一篇长文并导出pdf")
    assert is_long_form("先写正文再导出 word")
    assert not is_long_form("写一篇长文")  # no export intent
    assert not is_long_form("今天天气怎么样")


def test_nudge_short_vs_long() -> None:
    short = nudge_prompt("三句话导出word", long_form=False)
    long = nudge_prompt("约2000字并导出word", long_form=True)
    assert "短导出" in short
    assert "立刻调用" in short
    assert "长文导出" in long
    assert "禁止跳过正文" in long
    assert "未安装 Python" in short and "未安装 Python" in long
    assert "export_docx" in short and "export_pdf" in long


def test_strip_export_promo() -> None:
    raw = "正文说明\n下载：/api/exports/foo.docx\n点击下载 Word\n保留这句"
    out = strip_export_promo(raw)
    assert "/api/exports/" not in out
    assert "保留这句" in out
    assert "正文说明" in out


def test_compact_strips_exports_on_non_export_turn() -> None:
    hist = [
        {"role": "user", "content": "写简介导出word"},
        {
            "role": "assistant",
            "content": "好了 /api/exports/old.docx 请下载",
        },
        {"role": "user", "content": "今天北京天气怎么样"},
    ]
    blob = _compact_prompt(hist)
    assert "今天北京天气怎么样" in blob
    assert "/api/exports/" not in blob


def test_compact_keeps_exports_on_export_turn() -> None:
    hist = [
        {"role": "user", "content": "先导出版"},
        {
            "role": "assistant",
            "content": "链接 /api/exports/keep.docx",
        },
        {"role": "user", "content": "再导出一份 pdf"},
    ]
    blob = _compact_prompt(hist)
    assert "/api/exports/keep.docx" in blob


def test_permission_includes_write(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write_opencode_run_config(
        run,
        provider_id="qwen",
        model="qwen-max",
        kind="openai_compatible",
        base_url="https://example.com/v1",
    )
    cfg = json.loads((run / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["permission"].get("write") == "allow"
    agents = (run / "AGENTS.md").read_text(encoding="utf-8")
    assert AGENTS_MD_VERSION in agents


def test_agents_demo_and_split_rules(tmp_path: Path) -> None:
    text = write_agents_md(tmp_path).read_text(encoding="utf-8")
    assert AGENTS_MD_VERSION in text
    assert "/demo/" in text or "127.0.0.1" in text
    assert "Visible demo" in text or "看到页面" in text
    assert "No cross-talk exports" in text or "cross-talk" in text
    assert "Short export" in text
    assert "Long-form export" in text or "Text then tools" in text
    assert "## Tools first" not in text
    assert "**write**" in text or "write" in text
