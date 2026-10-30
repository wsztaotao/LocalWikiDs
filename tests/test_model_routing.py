"""Tests for localwiki.model_routing (LW-09)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.model_routing import (  # noqa: E402
    ROUTE_NAMES,
    V11_ROUTE_MAP,
    default_v2_config,
    load_routes_dict,
    load_workspace_routes,
    migrate_v11_to_v2,
    save_workspace_routes,
)


def test_default_has_exactly_13_routes() -> None:
    cfg = default_v2_config()
    assert cfg.version == 2
    assert set(cfg.routes) == set(ROUTE_NAMES)
    assert len(ROUTE_NAMES) == 13


def test_v11_six_routes_migrate_to_thirteen() -> None:
    v11 = {
        "version": 1,
        "providers": {
            "ollama_local": {
                "kind": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "location": "local",
            },
            "openai_main": {
                "kind": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "api_key": "secret:openai_main",
                "location": "online",
            },
        },
        "routes": {
            "chat": {
                "primary": {"provider": "openai_main", "model": "gpt-x"},
                "fallback": [{"provider": "ollama_local", "model": "qwen"}],
            },
            "agent": {"primary": {"provider": "openai_main", "model": "gpt-agent"}},
            "knowledge": {
                "primary": {"provider": "ollama_local", "model": "compile-m"}
            },
            "experience": {
                "primary": {"provider": "ollama_local", "model": "exp-m"}
            },
            "nightly": {
                "primary": {"provider": "ollama_local", "model": "night-m"}
            },
            "persona": {
                "primary": {"provider": "ollama_local", "model": "persona-m"}
            },
        },
    }
    cfg = migrate_v11_to_v2(v11)
    assert cfg.version == 2
    assert set(cfg.routes.keys()) == set(ROUTE_NAMES)
    assert len(cfg.routes) == 13

    # Mapping checks
    assert cfg.routes["chat"].primary.model == "gpt-x"
    assert cfg.routes["agent"].primary.model == "gpt-agent"
    assert cfg.routes["knowledge_compile"].primary.model == "compile-m"
    assert cfg.routes["wiki_lint"].primary.model == "compile-m"
    assert cfg.routes["experience"].primary.model == "exp-m"
    assert cfg.routes["memory_consolidate"].primary.model == "night-m"
    assert cfg.routes["nightly"].primary.model == "night-m"
    assert cfg.routes["memory_extract"].primary.model == "persona-m"
    assert cfg.routes["persona"].primary.model == "persona-m"
    # New routes exist
    for name in ("embedding", "vision", "skill_search_rerank", "reflection"):
        assert name in cfg.routes
    assert "migrated_from_v11" in cfg.warnings


def test_load_routes_dict_auto_migrates() -> None:
    cfg = load_routes_dict(
        {
            "version": 1,
            "routes": {
                "chat": {"primary": {"provider": "ollama_local", "model": "a"}},
                "agent": {"primary": {"provider": "ollama_local", "model": "b"}},
                "knowledge": {"primary": {"provider": "ollama_local", "model": "c"}},
                "experience": {"primary": {"provider": "ollama_local", "model": "d"}},
                "nightly": {"primary": {"provider": "ollama_local", "model": "e"}},
                "persona": {"primary": {"provider": "ollama_local", "model": "f"}},
            },
        }
    )
    assert len(cfg.routes) == 13
    assert cfg.routes["knowledge_compile"].primary.model == "c"


def test_alias_wiki_compile_normalized() -> None:
    cfg = load_routes_dict(
        {
            "version": 2,
            "providers": {
                "ollama_local": {
                    "kind": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "location": "local",
                }
            },
            "routes": {
                "wiki_compile": {
                    "primary": {"provider": "ollama_local", "model": "wc"}
                },
                "chat": {"primary": {"provider": "ollama_local", "model": "c"}},
            },
        }
    )
    assert "knowledge_compile" in cfg.routes
    assert cfg.routes["knowledge_compile"].primary.model == "wc"
    assert "wiki_compile" not in cfg.routes


def test_workspace_roundtrip(tmp_path: Path) -> None:
    cfg = default_v2_config()
    save_workspace_routes(tmp_path, cfg)
    path = tmp_path / ".kw" / "model-routes.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert len(data["routes"]) == 13
    loaded = load_workspace_routes(tmp_path)
    assert set(loaded.routes) == set(ROUTE_NAMES)


def test_v11_map_covers_six_sources() -> None:
    assert set(V11_ROUTE_MAP) == {
        "chat",
        "agent",
        "knowledge",
        "experience",
        "nightly",
        "persona",
    }
    targets = {t for ts in V11_ROUTE_MAP.values() for t in ts}
    # All mapped targets are in the 13
    assert targets <= set(ROUTE_NAMES)


def test_web_model_routes_js_exists() -> None:
    path = ROOT / "web" / "model-routes.js"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "knowledge_compile" in text
    assert "skill_search_rerank" in text
    assert text.count('"chat"') >= 1
