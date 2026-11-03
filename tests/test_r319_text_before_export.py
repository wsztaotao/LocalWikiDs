"""R3.19 Text then tools — still required for long-form (superseded version stamp by R3.21)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.runtime.opencode_bridge import write_opencode_run_config  # noqa: E402


def test_agents_md_version_r31x() -> None:
    # Stamp bumps (r319→r323…); long-form section remains Text-then-tools.
    assert AGENTS_MD_VERSION.startswith("r3")


def test_agents_text_then_tools_not_tools_first(tmp_path: Path) -> None:
    text = write_agents_md(tmp_path).read_text(encoding="utf-8")
    assert "Text then tools" in text
    assert "## Tools first" not in text
    assert "Phase A" in text and "Phase B" in text
    assert "NEVER" in text
    assert "export_docx" in text
    assert "Do **not** paste the full article a second time" in text


def test_agents_length_chinese_soft_target(tmp_path: Path) -> None:
    text = write_agents_md(tmp_path).read_text(encoding="utf-8")
    assert "Chinese character" in text
    assert "2500" in text
    assert AGENTS_MD_VERSION in text


def test_opencode_run_config_writes_agents_with_text_then_tools(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write_opencode_run_config(
        run,
        provider_id="qwen",
        model="qwen-max",
        kind="openai_compatible",
        base_url="https://example.com/v1",
    )
    agents = (run / "AGENTS.md").read_text(encoding="utf-8")
    assert AGENTS_MD_VERSION in agents
    assert "Text then tools" in agents
    assert "Phase A" in agents
