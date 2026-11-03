"""R3.9 OpenCode general AGENTS.md."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.runtime.opencode_bridge import write_opencode_run_config  # noqa: E402


def test_write_agents_md_general_persona(tmp_path: Path) -> None:
    path = write_agents_md(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "LocalWiki" in text
    assert "writing" in text.lower() or "写作" in text or "general" in text.lower()
    assert "software engineering" in text.lower() or "工程" in text
    assert AGENTS_MD_VERSION in text
    assert "only do software" not in text.lower() or "Do **not** default" in text


def test_write_opencode_run_config_includes_agents(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write_opencode_run_config(
        run,
        provider_id="qwen",
        model="qwen-max",
        kind="openai_compatible",
        base_url="https://example.com/v1",
        write_ignore_all=True,
    )
    assert (run / "AGENTS.md").is_file()
    ignore = (run / ".gitignore").read_text(encoding="utf-8")
    assert "AGENTS.md" in ignore
