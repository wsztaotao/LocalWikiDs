"""Skill Hub search with Chinese expansion and score breakdown (§13.2)."""

from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from localwiki.skills_hub.sources import (
    SkillHit,
    curated_hits,
    load_sources_config,
    parse_github_code_search,
    parse_github_repo_search,
)

# Local synonym table for Chinese → English skill keywords (offline-safe).
# LLM rerank via skill_search_rerank is optional and injectible.
CN_SYNONYMS: dict[str, list[str]] = {
    "ppt": ["pptx", "powerpoint", "slides", "presentation"],
    "ppt生成": ["pptx", "powerpoint", "slides", "presentation"],
    "幻灯片": ["pptx", "powerpoint", "slides", "presentation"],
    "演示文稿": ["pptx", "powerpoint", "slides", "presentation"],
    "文档": ["docx", "document", "word"],
    "表格": ["xlsx", "spreadsheet", "excel"],
    "浏览器": ["browser", "playwright", "puppeteer"],
    "搜索": ["search", "web-search", "tavily"],
    "邮件": ["email", "gmail", "mail"],
    "文件系统": ["filesystem", "files", "fs"],
    "mcp": ["mcp-server", "modelcontextprotocol"],
    "语音": [
        "voice",
        "speech",
        "tts",
        "asr",
        "whisper",
        "speech-to-text",
        "text-to-speech",
    ],
    "说话": ["voice", "speech", "tts"],
    "朗读": ["tts", "text-to-speech", "speech"],
    "听写": ["asr", "speech-to-text", "whisper"],
}

ExpandFn = Callable[[str], list[str]]
RerankFn = Callable[[str, list["ScoredSkill"]], list["ScoredSkill"]]
HttpGet = Callable[[str, dict[str, str] | None], dict[str, Any]]


@dataclass
class ScoreBreakdown:
    relevance: float
    quality: float
    score: float
    relevance_parts: dict[str, float] = field(default_factory=dict)
    quality_parts: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScoredSkill:
    hit: SkillHit
    breakdown: ScoreBreakdown
    match_reasons: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": self.hit.to_dict(),
            "breakdown": self.breakdown.to_dict(),
            "match_reasons": list(self.match_reasons),
            "expanded_terms": list(self.expanded_terms),
            # Flatten key display fields
            "name": self.hit.name,
            "kind": self.hit.kind,
            "stars": self.hit.stars,
            "score": self.breakdown.score,
            "in_curated_index": self.hit.in_curated_index,
        }


@dataclass
class SkillSearchResult:
    query: str
    expanded_terms: list[str]
    results: list[ScoredSkill]
    cached: bool = False
    rate_limited: bool = False
    source_errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "expanded_terms": list(self.expanded_terms),
            "results": [r.to_dict() for r in self.results],
            "cached": self.cached,
            "rate_limited": self.rate_limited,
            "source_errors": dict(self.source_errors),
        }


def expand_keywords(query: str, *, extra: ExpandFn | None = None) -> list[str]:
    """Expand Chinese/keywords to English skill search terms."""
    q = (query or "").strip()
    terms: list[str] = []
    if q:
        terms.append(q)
    # Whole-query map
    key = q.lower().replace(" ", "")
    for cn, engs in CN_SYNONYMS.items():
        if cn in q or cn == key:
            terms.extend(engs)
    # Token map
    for tok in re.split(r"[\s,，、]+", q):
        t = tok.strip().lower()
        if not t:
            continue
        if t not in terms:
            terms.append(t)
        if t in CN_SYNONYMS:
            terms.extend(CN_SYNONYMS[t])
    if extra:
        terms.extend(extra(q))
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        tl = t.lower().strip()
        if tl and tl not in seen:
            seen.add(tl)
            out.append(tl)
    return out


def _tokenize(text: str) -> set[str]:
    text = (text or "").lower()
    parts = set(re.findall(r"[a-z0-9_\-]+", text))
    # Keep CJK runs as tokens
    parts.update(re.findall(r"[\u4e00-\u9fff]+", text))
    return parts


def bm25_like(query_terms: list[str], doc: str) -> float:
    """Lightweight BM25-ish score without a corpus (TF + term coverage)."""
    tokens = _tokenize(doc)
    if not tokens or not query_terms:
        return 0.0
    score = 0.0
    for term in query_terms:
        t = term.lower()
        if t in tokens or any(t in tok for tok in tokens):
            # term frequency proxy
            tf = sum(1 for tok in tokens if t == tok or t in tok)
            score += 1.0 + math.log1p(tf)
    return score


def score_hit(hit: SkillHit, query_terms: list[str], *, trusted_repos: set[str] | None = None) -> ScoredSkill:
    trusted_repos = trusted_repos or set()
    name_s = bm25_like(query_terms, hit.name)
    desc_s = bm25_like(query_terms, hit.description)
    path_s = bm25_like(query_terms, hit.path) * 0.5
    relevance = name_s * 2.0 + desc_s + path_s
    rel_parts = {"name": name_s, "description": desc_s, "path": path_s}

    stars_q = math.log1p(max(0, hit.stars))
    curated_q = 2.0 if hit.in_curated_index else 0.0
    license_q = 1.0 if hit.license else 0.0
    archived_penalty = -3.0 if hit.archived else 0.0
    trusted_q = 1.5 if (hit.trusted or hit.repo.lower() in {r.lower() for r in trusted_repos}) else 0.0
    quality = max(0.0, stars_q + curated_q + license_q + archived_penalty + trusted_q)
    qual_parts = {
        "log_stars": stars_q,
        "curated": curated_q,
        "license": license_q,
        "archived_penalty": archived_penalty,
        "trusted": trusted_q,
    }

    # score = relevance × quality (quality floor 0.5 so zero-star curated still ranks)
    q_factor = max(0.5, quality)
    total = relevance * q_factor

    reasons: list[str] = []
    matched = [t for t in query_terms if t in (hit.name + " " + hit.description + " " + hit.path).lower()]
    if matched:
        reasons.append("terms:" + ",".join(matched[:5]))
    if hit.in_curated_index:
        reasons.append("curated_index")
    if hit.stars:
        reasons.append(f"stars:{hit.stars}")
    if hit.license:
        reasons.append(f"license:{hit.license}")

    return ScoredSkill(
        hit=hit,
        breakdown=ScoreBreakdown(
            relevance=round(relevance, 4),
            quality=round(quality, 4),
            score=round(total, 4),
            relevance_parts={k: round(v, 4) for k, v in rel_parts.items()},
            quality_parts={k: round(v, 4) for k, v in qual_parts.items()},
        ),
        match_reasons=reasons,
        expanded_terms=list(query_terms),
    )


def dedupe_hits(hits: list[SkillHit]) -> list[SkillHit]:
    best: dict[str, SkillHit] = {}
    for h in hits:
        k = h.dedupe_key
        prev = best.get(k)
        if prev is None:
            best[k] = h
            continue
        # Prefer curated, then higher stars
        if h.in_curated_index and not prev.in_curated_index:
            best[k] = h
        elif h.stars > prev.stars:
            best[k] = h
    return list(best.values())


class _Cache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, SkillSearchResult]] = {}

    def get(self, key: str, ttl_s: float) -> SkillSearchResult | None:
        item = self._data.get(key)
        if not item:
            return None
        ts, val = item
        if time.time() - ts > ttl_s:
            return None
        cached = SkillSearchResult(
            query=val.query,
            expanded_terms=list(val.expanded_terms),
            results=list(val.results),
            cached=True,
            rate_limited=val.rate_limited,
            source_errors=dict(val.source_errors),
        )
        return cached

    def set(self, key: str, val: SkillSearchResult) -> None:
        self._data[key] = (time.time(), val)


_CACHE = _Cache()


def github_query_terms(terms: list[str], *, limit: int = 4) -> list[str]:
    """English-only terms for GitHub API queries (skip CJK)."""
    out: list[str] = []
    for t in terms:
        s = (t or "").strip().lower()
        if not s:
            continue
        if re.search(r"[\u4e00-\u9fff]", s):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _gh_message_error(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    msg = str(payload.get("message") or "")
    if not msg:
        return None
    low = msg.lower()
    if "rate limit" in low:
        return "rate_limited"
    if "requires authentication" in low or "bad credentials" in low:
        return "auth_required"
    if payload.get("status") == "401" or "401" in msg:
        return "auth_required"
    return None


def is_installable_hit(hit: SkillHit) -> bool:
    repo = (hit.repo or "").strip()
    if not repo or "/" not in repo:
        return False
    if repo.lower().startswith("localwiki/"):
        return False
    return True


def search_skills(
    query: str,
    *,
    sources_config: dict[str, Any] | None = None,
    sources_path: Path | None = None,
    http_get: HttpGet | None = None,
    github_token: str | None = None,
    expand_fn: ExpandFn | None = None,
    rerank_fn: RerankFn | None = None,
    trusted_repos: list[str] | None = None,
    use_cache: bool = True,
    offline: bool = False,
    installable_only: bool = True,
) -> SkillSearchResult:
    """Search curated (+ optional GitHub) sources; expose score breakdown."""
    config = sources_config or load_sources_config(sources_path)
    terms = expand_keywords(query, extra=expand_fn)
    ttl_h = float(config.get("cache_ttl_hours") or 24)
    cache_key = f"{query}|{','.join(terms)}|{offline}|{installable_only}|tok={bool(github_token)}"
    if use_cache:
        hit = _CACHE.get(cache_key, ttl_h * 3600)
        if hit:
            return hit

    collected: list[SkillHit] = []
    errors: dict[str, str] = {}
    rate_limited = False
    code_hits = 0

    # Curated always available offline
    collected.extend(curated_hits(config))

    gh_cfg = config.get("github") or {}
    from urllib.parse import quote

    en_terms = github_query_terms(terms)
    # Use + for GitHub q= (spaces must not appear raw in URL)
    q_join = "+".join(en_terms) if en_terms else "+".join(
        t for t in terms[:2] if not re.search(r"[\u4e00-\u9fff]", t)
    )
    if not q_join and en_terms:
        q_join = "+".join(en_terms)

    if not offline and http_get is not None and (gh_cfg.get("code_search") or gh_cfg.get("repo_search")):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "LocalWiki-SkillSearch"}
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        if gh_cfg.get("code_search") and q_join:
            url = f"https://api.github.com/search/code?q={quote('filename:SKILL.md '+ ' '.join(en_terms), safe='')}"
            try:
                payload = http_get(url, headers)
                err = _gh_message_error(payload if isinstance(payload, dict) else None)
                if err == "rate_limited":
                    rate_limited = True
                    errors["github_code"] = "rate_limited"
                elif err == "auth_required":
                    errors["github_code"] = "auth_required"
                else:
                    before = len(collected)
                    collected.extend(parse_github_code_search(payload if isinstance(payload, dict) else {}))
                    code_hits = len(collected) - before
            except Exception as exc:
                errors["github_code"] = str(exc)
        if gh_cfg.get("repo_search") and q_join:
            # Topic-scoped (optional)
            for topic in gh_cfg.get("topics") or ["agent-skills"]:
                url = (
                    "https://api.github.com/search/repositories?q="
                    + quote(" ".join(en_terms) + f" topic:{topic}", safe="")
                )
                try:
                    payload = http_get(url, headers)
                    err = _gh_message_error(payload if isinstance(payload, dict) else None)
                    if err:
                        errors[f"github_repo:{topic}"] = err
                        if err == "rate_limited":
                            rate_limited = True
                        continue
                    kind = "mcp_server" if "mcp" in topic else "agent_skill"
                    collected.extend(
                        parse_github_repo_search(
                            payload if isinstance(payload, dict) else {}, kind_hint=kind
                        )
                    )
                except Exception as exc:
                    errors[f"github_repo:{topic}"] = str(exc)
            # R3.14: unscoped repo fallback when code empty / auth failed
            if code_hits == 0 or errors.get("github_code") == "auth_required":
                url = (
                    "https://api.github.com/search/repositories?q="
                    + quote(" ".join(en_terms), safe="")
                )
                try:
                    payload = http_get(url, headers)
                    err = _gh_message_error(payload if isinstance(payload, dict) else None)
                    if err:
                        errors["github_repo_fallback"] = err
                        if err == "rate_limited":
                            rate_limited = True
                    else:
                        collected.extend(
                            parse_github_repo_search(
                                payload if isinstance(payload, dict) else {},
                                kind_hint="agent_skill",
                            )
                        )
                except Exception as exc:
                    errors["github_repo_fallback"] = str(exc)
        if not github_token and errors.get("github_code") == "auth_required":
            errors.setdefault(
                "hint",
                "configure_github_token_for_code_search",
            )
    elif not offline and http_get is None and (gh_cfg.get("code_search") or gh_cfg.get("repo_search")):
        errors["github"] = "no_http_client"

    # Mark curated overlap
    curated_keys = {h.dedupe_key for h in collected if h.source == "curated"}
    for h in collected:
        if h.source != "curated" and h.dedupe_key in curated_keys:
            h.in_curated_index = True

    unique = dedupe_hits(collected)
    trusted = set(trusted_repos or [])
    scored = [score_hit(h, terms, trusted_repos=trusted) for h in unique]
    # Non-empty query: hide zero-relevance (incl. curated) — R3.12 Q1=A
    if terms:
        scored = [s for s in scored if s.breakdown.relevance > 0]
    if installable_only and terms:
        scored = [s for s in scored if is_installable_hit(s.hit)]
    scored.sort(key=lambda s: s.breakdown.score, reverse=True)

    if rerank_fn:
        scored = rerank_fn(query, scored)

    result = SkillSearchResult(
        query=query,
        expanded_terms=terms,
        results=scored,
        cached=False,
        rate_limited=rate_limited,
        source_errors=errors,
    )
    if use_cache:
        _CACHE.set(cache_key, result)
    return result
