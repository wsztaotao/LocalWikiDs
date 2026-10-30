"""ToolRouter — select ≤40 tools per step (§11.2 / §12.1).

Selection: TaskSpec.tool_scope → preferred_tools → plan-step keywords.
Always includes ``tool_search`` meta-tool when registered.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from localwiki.tools.registry import ToolRegistry, ToolSpec

DEFAULT_MAX_TOOLS = 40


@dataclass
class ToolSelectResult:
    selected: list[str]
    reasons: dict[str, str] = field(default_factory=dict)
    omitted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": list(self.selected),
            "reasons": dict(self.reasons),
            "omitted": list(self.omitted),
        }


def _scope_match(name: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    for pat in patterns:
        if pat == "*" or pat == "*:*":
            return True
        if fnmatch.fnmatch(name, pat):
            return True
        # prefix form knowledge_* already covered by fnmatch
    return False


def _keyword_score(spec: ToolSpec, keywords: Sequence[str]) -> float:
    if not keywords:
        return 0.0
    blob = f"{spec.name} {spec.description} {spec.category}".lower()
    score = 0.0
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if k in blob:
            score += 1.0
        elif re.search(re.escape(k), blob):
            score += 0.5
    return score


class ToolRouter:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_tools: int = DEFAULT_MAX_TOOLS,
    ) -> None:
        self.registry = registry
        self.max_tools = max_tools

    def select(
        self,
        *,
        tool_scope: Sequence[str] | None = None,
        preferred_tools: Sequence[str] | None = None,
        keywords: Sequence[str] | None = None,
        max_tools: int | None = None,
    ) -> ToolSelectResult:
        limit = max_tools if max_tools is not None else self.max_tools
        scope = list(tool_scope or [])
        preferred = list(preferred_tools or [])
        kws = list(keywords or [])

        candidates = [
            t
            for t in self.registry.list_tools(enabled_only=True)
            if t.name != "tool_search" and _scope_match(t.name, scope)
        ]

        scored: list[tuple[float, ToolSpec, str]] = []
        for spec in candidates:
            score = 0.0
            why_parts: list[str] = []
            if spec.name in preferred or any(
                fnmatch.fnmatch(spec.name, p) for p in preferred
            ):
                score += 10.0
                why_parts.append("preferred_tools")
            ks = _keyword_score(spec, kws)
            if ks:
                score += ks
                why_parts.append(f"keywords={ks:.1f}")
            if not why_parts:
                why_parts.append("in_scope")
            scored.append((score, spec, "+".join(why_parts)))

        scored.sort(key=lambda x: (-x[0], x[1].name))

        selected: list[str] = []
        reasons: dict[str, str] = {}
        for score, spec, why in scored:
            if len(selected) >= limit - 1:  # reserve slot for tool_search
                break
            selected.append(spec.name)
            reasons[spec.name] = why

        omitted = [
            s.name for _, s, _ in scored if s.name not in selected
        ]

        # Always offer tool_search when present
        if self.registry.get("tool_search") and self.registry.get("tool_search").enabled:
            if "tool_search" not in selected:
                if len(selected) >= limit:
                    dropped = selected.pop()
                    omitted.insert(0, dropped)
                    reasons.pop(dropped, None)
                selected.append("tool_search")
                reasons["tool_search"] = "meta_always"

        return ToolSelectResult(selected=selected, reasons=reasons, omitted=omitted)

    def specs_for(self, result: ToolSelectResult) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for name in result.selected:
            spec = self.registry.get(name)
            if spec:
                out.append(spec)
        return out
