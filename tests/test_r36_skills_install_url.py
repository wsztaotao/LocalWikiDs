"""R3.6: Skill install from files / GitHub zip mock."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.skills_hub.harness import install_skill_or_harness, list_installed  # noqa: E402
from localwiki.skills_hub.remote import parse_github_source_url  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_parse_github_url() -> None:
    g = parse_github_source_url(
        "https://github.com/acme/skills/tree/main/skills/hello"
    )
    assert g["repo"] == "acme/skills"
    assert g["ref"] == "main"
    assert g["path"] == "skills/hello"


def _zip_bytes(tree: dict[str, str], root_folder: str = "acme-skills-main") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, text in tree.items():
            zf.writestr(f"{root_folder}/{rel}", text)
    return buf.getvalue()


def test_install_from_repo_zip_mock(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    skill_md = "---\nname: hello\ndescription: hi\n---\n# Hello\n"
    raw = _zip_bytes({"skills/hello/SKILL.md": skill_md})

    def http_fetch(url: str, headers=None):
        assert "codeload.github.com" in url
        return raw

    out = install_skill_or_harness(
        ws,
        kind="skill",
        repo="acme/skills",
        path="skills/hello",
        ref="main",
        http_fetch=http_fetch,
        force=True,
    )
    assert out.get("ok") is True
    listed = list_installed(ws)
    assert listed["ok"]
    assert any("hello" in s["name"] for s in listed["skills"])
    assert listed.get("opencode_skills_aligned") is True


def test_install_files_still_works(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    out = install_skill_or_harness(
        ws,
        kind="skill",
        files={"SKILL.md": "---\nname: local-skill\ndescription: x\n---\n"},
        force=True,
    )
    assert out.get("ok") is True
