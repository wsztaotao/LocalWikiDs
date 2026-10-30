"""R3.13: catalog expose + AGENTS install section + harness expose."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.skills_hub.catalog_expose import (  # noqa: E402
    expose_harness_into_skills,
    format_catalog_md,
    list_enabled_skill_catalog,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_catalog_and_agents_include_office(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    skill = ws / ".kw" / "skills" / "office-docx"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: office-docx\ndescription: Word docs\n---\n\n# Docx\n",
        encoding="utf-8",
    )
    entries = list_enabled_skill_catalog(ws)
    names = {e["name"] for e in entries}
    assert "office-docx" in names
    md = format_catalog_md(entries)
    assert "office-docx" in md
    assert "Installed skills" in md
    path = write_agents_md(tmp_path / "run", catalog_md=md)
    text = path.read_text(encoding="utf-8")
    assert AGENTS_MD_VERSION.startswith("r31")  # r313+ / r317+
    assert "office-docx" in text
    assert "Installed skills" in text


def test_expose_harness_writes_skill_wrapper(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    h = ws / ".kw" / "harness" / "demo-pack"
    h.mkdir(parents=True)
    (h / "manifest.json").write_text('{"name":"demo-pack"}\n', encoding="utf-8")
    out = expose_harness_into_skills(ws)
    assert out["ok"]
    assert "demo-pack" in out["exposed"]
    wrapper = ws / ".kw" / "skills" / ".harness-expose" / "demo-pack" / "SKILL.md"
    assert wrapper.is_file()
    entries = list_enabled_skill_catalog(ws)
    assert any(e["name"] == "demo-pack" and e["kind"] == "harness" for e in entries)


def test_dot_skills_dir_not_listed_as_skill(tmp_path: Path) -> None:
    from localwiki.skills_hub.harness import list_installed

    ws = tmp_path / "ws"
    ensure_workspace(ws)
    (ws / ".kw" / "skills" / ".harness-expose").mkdir(parents=True)
    (ws / ".kw" / "skills" / "office-docx").mkdir(parents=True)
    (ws / ".kw" / "skills" / "office-docx" / "SKILL.md").write_text("# x\n", encoding="utf-8")
    names = {s["name"] for s in list_installed(ws).get("skills") or []}
    assert "office-docx" in names
    assert ".harness-expose" not in names
