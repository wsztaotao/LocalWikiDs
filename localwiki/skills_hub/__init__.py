"""Skill Hub: search, scan, install, runtime (design §13)."""

from localwiki.skills_hub.installer import install_from_tree, uninstall_skill
from localwiki.skills_hub.registry import SkillRecord, SkillRegistry, tree_sha256
from localwiki.skills_hub.runtime import list_prompt_entries, read_skill_body, startup_integrity_check
from localwiki.skills_hub.search import SkillSearchResult, search_skills
from localwiki.skills_hub.sources import SkillHit, load_sources_config

__all__ = [
    "SkillHit",
    "SkillRecord",
    "SkillRegistry",
    "SkillSearchResult",
    "install_from_tree",
    "list_prompt_entries",
    "load_sources_config",
    "read_skill_body",
    "search_skills",
    "startup_integrity_check",
    "tree_sha256",
    "uninstall_skill",
]
