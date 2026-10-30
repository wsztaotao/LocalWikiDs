"""Tests for Skill Hub search (LW-30 / §13.2)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.skills_hub.search import (  # noqa: E402
    expand_keywords,
    search_skills,
)
from localwiki.skills_hub.sources import (  # noqa: E402
    load_sources_config,
    write_default_sources,
)


def test_chinese_keyword_expansion() -> None:
    terms = expand_keywords("ppt 生成")
    assert "ppt" in terms or "ppt生成" in "".join(terms) or any("pptx" in t for t in terms)
    assert "pptx" in terms
    assert "powerpoint" in terms
    assert "slides" in terms
    assert "presentation" in terms


def test_voice_keyword_expansion() -> None:
    terms = expand_keywords("语音")
    assert "语音" in terms
    assert "voice" in terms
    assert "speech" in terms
    assert any(t in terms for t in ("tts", "asr", "whisper"))


def test_search_curated_score_breakdown_visible() -> None:
    result = search_skills("pptx slides", offline=True, use_cache=False)
    assert result.expanded_terms
    assert result.results
    top = result.results[0]
    assert top.hit.name == "pptx"
    bd = top.breakdown.to_dict()
    assert "relevance" in bd and "quality" in bd and "score" in bd
    assert "relevance_parts" in bd and "name" in bd["relevance_parts"]
    assert "quality_parts" in bd and "curated" in bd["quality_parts"]
    assert "curated_index" in top.match_reasons
    d = top.to_dict()
    assert d["breakdown"]["score"] == top.breakdown.score


def test_voice_offline_no_fake_curated() -> None:
    # R3.14: fake voice curated removed; offline may be empty (installable_only)
    result = search_skills("语音", offline=True, use_cache=False)
    names = {r.hit.name for r in result.results}
    assert "voice-speech" not in names
    assert "pptx" not in names
    assert "docx" not in names
    assert "filesystem" not in names


def test_online_without_http_client_records_error() -> None:
    result = search_skills("voice", offline=False, http_get=None, use_cache=False)
    assert "github" in result.source_errors
    names = {r.hit.name for r in result.results}
    assert "pptx" not in names


def test_github_recorded_response_merged() -> None:
    recorded = {
        "https://api.github.com/search/code?q=filename:SKILL.md+filesystem": {
            "items": [
                {
                    "path": "skills/fs/SKILL.md",
                    "html_url": "https://github.com/acme/fs/blob/main/skills/fs/SKILL.md",
                    "repository": {
                        "full_name": "acme/fs",
                        "description": "filesystem skill",
                        "stargazers_count": 42,
                        "license": {"spdx_id": "MIT"},
                    },
                }
            ]
        },
    }

    def http_get(url: str, headers=None):
        # Match loosely
        for k, v in recorded.items():
            if "search/code" in url:
                return v
        if "search/repositories" in url:
            return {"items": []}
        return {"items": []}

    result = search_skills(
        "filesystem",
        http_get=http_get,
        use_cache=False,
        offline=False,
    )
    repos = {r.hit.repo for r in result.results}
    assert "acme/fs" in repos or "modelcontextprotocol/servers" in repos
    # Score breakdown present on github hit
    gh = next(r for r in result.results if r.hit.repo == "acme/fs")
    assert gh.breakdown.quality_parts["log_stars"] > 0


def test_sources_config_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    write_default_sources(path)
    cfg = load_sources_config(path)
    assert "curated" in cfg
    assert cfg["cache_ttl_hours"] == 24
