"""Skill search sources configuration (§13.2)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore

DEFAULT_SOURCES = {
    "curated": [
        {
            "id": "anthropics/skills",
            "repo": "anthropics/skills",
            "kind_hint": "agent_skill",
            "entries": [
                {
                    "name": "pptx",
                    "path": "skills/pptx",
                    "description": "Create and edit PowerPoint presentations",
                    "kind": "agent_skill",
                },
                {
                    "name": "docx",
                    "path": "skills/docx",
                    "description": "Create and edit Word documents",
                    "kind": "agent_skill",
                },
            ],
        },
        {
            "id": "modelcontextprotocol/servers",
            "repo": "modelcontextprotocol/servers",
            "kind_hint": "mcp_server",
            "entries": [
                {
                    "name": "filesystem",
                    "path": "src/filesystem",
                    "description": "MCP filesystem server",
                    "kind": "mcp_server",
                },
            ],
        },
    ],
    "github": {
        "code_search": True,
        "repo_search": True,
        "topics": ["agent-skills", "mcp-server", "claude-skills"],
    },
    "cache_ttl_hours": 24,
}


@dataclass
class SkillHit:
    name: str
    kind: str  # agent_skill | mcp_server | unknown
    repo: str
    path: str = ""
    description: str = ""
    stars: int = 0
    updated_at: str | None = None
    license: str | None = None
    source: str = "curated"  # curated | github_code | github_repo
    in_curated_index: bool = False
    archived: bool = False
    trusted: bool = False
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.repo}::{self.path or self.name}".lower()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dedupe_key"] = self.dedupe_key
        return d


HttpGet = Callable[[str, dict[str, str] | None], dict[str, Any]]


def load_sources_config(path: Path | None = None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return json.loads(json.dumps(DEFAULT_SOURCES))
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("E_DEP_MISSING:pyyaml for sources.yaml")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("sources config must be object")
    # Merge over defaults
    out = json.loads(json.dumps(DEFAULT_SOURCES))
    out.update(data)
    return out


def write_default_sources(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".yaml", ".yml") and yaml is not None:
        path.write_text(yaml.safe_dump(DEFAULT_SOURCES, allow_unicode=True), encoding="utf-8")
    else:
        path.write_text(json.dumps(DEFAULT_SOURCES, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def curated_hits(config: dict[str, Any]) -> list[SkillHit]:
    hits: list[SkillHit] = []
    for block in config.get("curated") or []:
        repo = str(block.get("repo") or block.get("id") or "")
        for ent in block.get("entries") or []:
            hits.append(
                SkillHit(
                    name=str(ent.get("name") or ""),
                    kind=str(ent.get("kind") or block.get("kind_hint") or "unknown"),
                    repo=repo,
                    path=str(ent.get("path") or ""),
                    description=str(ent.get("description") or ""),
                    source="curated",
                    in_curated_index=True,
                    stars=int(ent.get("stars") or block.get("stars") or 100),
                    license=ent.get("license") or "MIT",
                    url=f"https://github.com/{repo}/tree/main/{ent.get('path') or ''}",
                )
            )
    return hits


def parse_github_code_search(payload: dict[str, Any]) -> list[SkillHit]:
    hits: list[SkillHit] = []
    for item in payload.get("items") or []:
        repo_obj = item.get("repository") or {}
        full = str(repo_obj.get("full_name") or "")
        path = str(item.get("path") or "")
        name = Path(path).parent.name if path else str(item.get("name") or "skill")
        hits.append(
            SkillHit(
                name=name,
                kind="agent_skill" if path.endswith("SKILL.md") else "unknown",
                repo=full,
                path=str(Path(path).parent).replace("\\", "/") if path else "",
                description=str(repo_obj.get("description") or ""),
                stars=int(repo_obj.get("stargazers_count") or 0),
                source="github_code",
                url=item.get("html_url"),
                license=(repo_obj.get("license") or {}).get("spdx_id")
                if isinstance(repo_obj.get("license"), dict)
                else None,
                archived=bool(repo_obj.get("archived")),
                raw={"github": item},
            )
        )
    return hits


def parse_github_repo_search(payload: dict[str, Any], *, kind_hint: str = "mcp_server") -> list[SkillHit]:
    hits: list[SkillHit] = []
    for item in payload.get("items") or []:
        full = str(item.get("full_name") or "")
        hits.append(
            SkillHit(
                name=str(item.get("name") or full.split("/")[-1]),
                kind=kind_hint,
                repo=full,
                path="",
                description=str(item.get("description") or ""),
                stars=int(item.get("stargazers_count") or 0),
                updated_at=item.get("updated_at"),
                source="github_repo",
                url=item.get("html_url"),
                license=(item.get("license") or {}).get("spdx_id")
                if isinstance(item.get("license"), dict)
                else None,
                archived=bool(item.get("archived")),
                raw={"github": item},
            )
        )
    return hits
