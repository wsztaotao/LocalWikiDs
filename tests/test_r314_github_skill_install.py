"""R3.14: GitHub token, search auth, installable filter, zip path auto."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.skills_hub.github_auth import (  # noqa: E402
    clear_github_token,
    resolve_github_token,
    set_github_token,
)
from localwiki.skills_hub.remote import FetchError, fetch_github_as_files  # noqa: E402
from localwiki.skills_hub.search import (  # noqa: E402
    github_query_terms,
    search_skills,
)
from localwiki.skills_hub.sources import DEFAULT_SOURCES, curated_hits  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_no_fake_voice_curated() -> None:
    hits = curated_hits(DEFAULT_SOURCES)
    names = {h.name for h in hits}
    assert "voice-speech" not in names
    repos = {h.repo for h in hits}
    assert not any(r.startswith("localwiki/") for r in repos)


def test_github_query_terms_skip_cjk() -> None:
    terms = github_query_terms(["语音", "voice", "speech", "tts", "asr", "extra"])
    assert "语音" not in terms
    assert terms[:3] == ["voice", "speech", "tts"]
    assert len(terms) <= 4


def test_code_search_auth_required_and_repo_fallback() -> None:
    def http_get(url: str, headers=None):
        if "search/code" in url:
            return {"message": "Requires authentication", "status": "401"}
        if "search/repositories" in url and "topic:" not in url:
            return {
                "items": [
                    {
                        "full_name": "acme/voice-tts-skill",
                        "name": "voice-tts-skill",
                        "description": "Voice speech TTS agent skill",
                        "stargazers_count": 12,
                    }
                ]
            }
        if "search/repositories" in url:
            return {"items": []}
        return {"items": []}

    result = search_skills(
        "语音",
        offline=False,
        http_get=http_get,
        use_cache=False,
        installable_only=True,
    )
    assert result.source_errors.get("github_code") == "auth_required"
    names = {r.hit.name for r in result.results}
    assert "voice-tts-skill" in names
    assert "pptx" not in names


def test_resolve_github_token_env_over_secret(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    set_github_token(ws, "secret-tok")
    monkeypatch.setenv("GITHUB_TOKEN", "env-tok")
    assert resolve_github_token(ws) == "env-tok"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert resolve_github_token(ws) == "secret-tok"
    clear_github_token(ws)
    assert resolve_github_token(ws) is None


def test_fetch_auto_path_unique_skill_md() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/skills/voice/SKILL.md", b"# voice\n")
        zf.writestr("repo-main/skills/voice/run.py", b"print(1)\n")
        zf.writestr("repo-main/README.md", b"hi\n")
    data = buf.getvalue()

    def http_fetch(url: str, headers=None):
        return data

    files = fetch_github_as_files(
        repo="acme/voice",
        ref="main",
        path="wrong/path",
        http_fetch=http_fetch,
    )
    assert "SKILL.md" in files
    assert files["SKILL.md"].startswith(b"# voice")


def test_fetch_ambiguous_skill_paths() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo-main/a/SKILL.md", b"a\n")
        zf.writestr("repo-main/b/SKILL.md", b"b\n")
    data = buf.getvalue()

    def http_fetch(url: str, headers=None):
        return data

    try:
        fetch_github_as_files(
            repo="acme/multi",
            ref="main",
            path="missing",
            http_fetch=http_fetch,
        )
        assert False, "expected FetchError"
    except FetchError as exc:
        assert "ambiguous_skill_paths" in str(exc)
