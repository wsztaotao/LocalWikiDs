"""Wiki lint: deterministic checks (+ optional LLM stubs) per design §5.6.

Nine check classes. Lint only produces reports and repair proposals —
never unsupervised full-wiki rewrites.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from localwiki.llmwiki.schema import (
    REQUIRED_LINT_RULES,
    SchemaBundle,
    load_default_schema,
    load_workspace_schema,
)

Severity = Literal["error", "warning", "info"]
CheckName = str

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:", re.M)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass
class WikiPage:
    page_uid: str
    path: str
    title: str
    page_type: str
    body: str
    raw: str = ""
    aliases: list[str] = field(default_factory=list)
    inbound_from: list[str] = field(default_factory=list)
    outbound_to: list[str] = field(default_factory=list)
    front_matter: dict[str, Any] = field(default_factory=dict)
    stale_after: str | None = None
    marked_conflicts: list[str] = field(default_factory=list)
    disabled_experience_refs: list[str] = field(default_factory=list)
    memory_statements: list[str] = field(default_factory=list)


@dataclass
class LintFinding:
    check: CheckName
    severity: Severity
    message: str
    page_uid: str | None = None
    path: str | None = None
    proposal: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LintReport:
    report_uid: str
    generated_at: str
    findings: list[LintFinding]
    auto_modified_pages: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def by_check(self) -> dict[str, list[LintFinding]]:
        out: dict[str, list[LintFinding]] = {}
        for f in self.findings:
            out.setdefault(f.check, []).append(f)
        return out


def parse_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    m = FRONT_MATTER_RE.match(raw)
    if not m:
        # Also accept simple leading type:/title: lines without ---
        meta: dict[str, Any] = {}
        body_lines: list[str] = []
        for line in raw.splitlines():
            if re.match(r"^(type|title|description|status)\s*:", line) and not body_lines:
                key, _, val = line.partition(":")
                meta[key.strip()] = val.strip()
            else:
                body_lines.append(line)
        return meta, "\n".join(body_lines)
    yaml_text = m.group(1)
    body = raw[m.end() :]
    meta = _simple_yaml_map(yaml_text)
    return meta, body


def _simple_yaml_map(text: str) -> dict[str, Any]:
    """Minimal YAML subset for front matter (stdlib-friendly fallback)."""
    try:
        import yaml

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        out: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip().strip("\"'")
        return out


def load_wiki_pages(wiki_dir: Path) -> list[WikiPage]:
    wiki_dir = Path(wiki_dir)
    pages: list[WikiPage] = []
    if not wiki_dir.is_dir():
        return pages
    for path in sorted(wiki_dir.rglob("*.md")):
        if path.name in ("index.md", "log.md"):
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(raw)
        rel = str(path.relative_to(wiki_dir)).replace("\\", "/")
        uid = rel[:-3] if rel.endswith(".md") else rel
        title = str(meta.get("title") or path.stem)
        page_type = str(meta.get("type") or "")
        aliases = meta.get("kw", {}).get("aliases") if isinstance(meta.get("kw"), dict) else None
        if aliases is None:
            aliases = meta.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        stale = None
        if isinstance(meta.get("kw"), dict):
            stale = meta["kw"].get("stale_after")
        stale = stale or meta.get("stale_after")
        pages.append(
            WikiPage(
                page_uid=uid,
                path=rel,
                title=title,
                page_type=page_type,
                body=body,
                raw=raw,
                aliases=list(aliases or []),
                front_matter=meta,
                stale_after=str(stale) if stale else None,
                marked_conflicts=list(meta.get("conflicts") or []),
            )
        )
    _wire_links(pages)
    return pages


def _wire_links(pages: list[WikiPage]) -> None:
    by_uid = {p.page_uid: p for p in pages}
    by_path = {p.path: p for p in pages}
    for p in pages:
        outs: list[str] = []
        for _label, target in LINK_RE.findall(p.body):
            t = target.split("#", 1)[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:")):
                continue
            # normalize relative md links
            cand = t
            if cand.endswith(".md"):
                page = by_path.get(cand) or by_uid.get(cand[:-3])
            else:
                page = by_uid.get(cand) or by_path.get(cand + ".md")
            if page:
                outs.append(page.page_uid)
                if p.page_uid not in page.inbound_from:
                    page.inbound_from.append(p.page_uid)
            else:
                outs.append(f"?{t}")  # unresolved marker
        p.outbound_to = outs


# --- Nine deterministic checks ---


def check_format(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    findings: list[LintFinding] = []
    seen_uids: set[str] = set()
    for p in pages:
        if p.page_uid in seen_uids:
            findings.append(
                LintFinding(
                    "format",
                    "error",
                    f"duplicate UID {p.page_uid}",
                    p.page_uid,
                    p.path,
                    proposal="rename_or_merge",
                )
            )
        seen_uids.add(p.page_uid)
        if not p.page_type:
            findings.append(
                LintFinding(
                    "format",
                    "error",
                    "missing non-empty type",
                    p.page_uid,
                    p.path,
                    proposal="add_type_front_matter",
                )
            )
            continue
        spec = schema.page_types.get(p.page_type)
        if spec is None:
            findings.append(
                LintFinding(
                    "format",
                    "warning",
                    f"unknown page type {p.page_type!r}",
                    p.page_uid,
                    p.path,
                )
            )
            continue
        for section in spec.required_sections:
            if section not in p.body and f"## {section}" not in p.body:
                findings.append(
                    LintFinding(
                        "format",
                        "error",
                        f"missing required section {section!r}",
                        p.page_uid,
                        p.path,
                        proposal=f"add_section:{section}",
                    )
                )
        for kw in spec.kw_required:
            # kw.memory → look for memory key under kw or literal
            key = kw.split(".", 1)[-1] if kw.startswith("kw.") else kw
            kw_block = p.front_matter.get("kw")
            ok = isinstance(kw_block, dict) and key in kw_block
            if not ok and kw not in p.raw:
                findings.append(
                    LintFinding(
                        "format",
                        "error",
                        f"missing required field {kw}",
                        p.page_uid,
                        p.path,
                        proposal=f"add_field:{kw}",
                    )
                )
    return findings


def check_broken_links(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    known = {p.page_uid for p in pages} | {p.path for p in pages}
    for p in pages:
        for _label, target in LINK_RE.findall(p.body):
            t = target.split("#", 1)[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:")):
                continue
            ok = t in known or t.rstrip(".md") in {u for u in known} or (
                t.endswith(".md") and t[:-3] in {pg.page_uid for pg in pages}
            )
            # also path match
            if not ok:
                ok = any(pg.path == t or pg.page_uid == t.removesuffix(".md") for pg in pages)
            if not ok:
                findings.append(
                    LintFinding(
                        "broken_links",
                        "error",
                        f"broken link to {t!r}",
                        p.page_uid,
                        p.path,
                        proposal=f"fix_link:{t}",
                    )
                )
        defs = set(FOOTNOTE_DEF_RE.findall(p.body))
        for ref in FOOTNOTE_REF_RE.findall(p.body):
            if ref not in defs and not any(
                line.startswith(f"[^{ref}]:") for line in p.body.splitlines()
            ):
                # refs that are only definitions appear in both; defs check:
                if f"[^{ref}]:" not in p.body:
                    findings.append(
                        LintFinding(
                            "broken_links",
                            "error",
                            f"dangling footnote [^{ref}]",
                            p.page_uid,
                            p.path,
                            proposal=f"add_footnote_def:{ref}",
                        )
                    )
    return findings


def check_orphan_pages(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    if len(pages) <= 1:
        return findings
    for p in pages:
        if not p.inbound_from:
            findings.append(
                LintFinding(
                    "orphan_pages",
                    "warning",
                    "orphan page (in-degree 0)",
                    p.page_uid,
                    p.path,
                    proposal="add_inbound_links",
                )
            )
    return findings


def check_missing_concepts(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    known_titles = {_norm(p.title) for p in pages}
    for p in pages:
        known_titles.add(_norm(p.title))
        for a in p.aliases:
            known_titles.add(_norm(a))
    # Count Title-Case / 「」 terms across bodies
    counts: dict[str, int] = {}
    for p in pages:
        for m in re.finditer(r"\b([A-Z][a-zA-Z0-9]{2,}(?:\s+[A-Z][a-zA-Z0-9]+)*)\b", p.body):
            term = m.group(1)
            counts[term] = counts.get(term, 0) + 1
        for m in re.finditer(r"[「《]([^」》]{2,40})[」》]", p.body):
            term = m.group(1)
            counts[term] = counts.get(term, 0) + 1
    for term, n in counts.items():
        if n >= 2 and _norm(term) not in known_titles:
            findings.append(
                LintFinding(
                    "missing_concepts",
                    "info",
                    f"term {term!r} mentioned {n} times without a page",
                    None,
                    None,
                    proposal=f"create_concept:{term}",
                    meta={"term": term, "count": n},
                )
            )
    return findings


def check_contradictions(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    for p in pages:
        if p.marked_conflicts or "冲突" in p.body or "[[conflict]]" in p.body.lower():
            findings.append(
                LintFinding(
                    "contradictions",
                    "error",
                    "marked or textual conflict present",
                    p.page_uid,
                    p.path,
                    proposal="review_conflict",
                )
            )
        # dependency invalidation marker
        if "dependency_invalid" in p.raw or "依赖失效" in p.body:
            findings.append(
                LintFinding(
                    "contradictions",
                    "error",
                    "dependency invalidation marker",
                    p.page_uid,
                    p.path,
                    proposal="refresh_dependencies",
                )
            )
    return findings


def check_stale_content(
    pages: list[WikiPage],
    schema: SchemaBundle,
    *,
    today: date | None = None,
) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    today = today or date.today()
    for p in pages:
        if not p.stale_after:
            continue
        try:
            exp = date.fromisoformat(str(p.stale_after)[:10])
        except ValueError:
            findings.append(
                LintFinding(
                    "stale_content",
                    "warning",
                    f"invalid stale_after {p.stale_after!r}",
                    p.page_uid,
                    p.path,
                )
            )
            continue
        if exp < today:
            findings.append(
                LintFinding(
                    "stale_content",
                    "info",
                    f"content stale after {p.stale_after}",
                    p.page_uid,
                    p.path,
                    proposal="revise_stale_page",
                )
            )
    return findings


def check_duplicates(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    by_title: dict[str, list[WikiPage]] = {}
    for p in pages:
        keys = [_norm(p.title)] + [_norm(a) for a in p.aliases]
        for k in keys:
            if not k:
                continue
            by_title.setdefault(k, []).append(p)
    seen_pairs: set[tuple[str, str]] = set()
    for k, group in by_title.items():
        if len(group) < 2:
            continue
        uids = sorted({g.page_uid for g in group})
        if len(uids) < 2:
            continue
        pair = (uids[0], uids[1])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        findings.append(
            LintFinding(
                "duplicates",
                "warning",
                f"duplicate title/alias {k!r} across {uids}",
                uids[0],
                group[0].path,
                proposal="merge_with_redirect",
                meta={"uids": uids},
            )
        )
    return findings


def check_source_gaps(pages: list[WikiPage], schema: SchemaBundle) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    for p in pages:
        if p.page_type == "Question":
            # Long-unresolved heuristic: marker or empty resolution section
            if "未解决" in p.body or "open" in _norm(p.body) or "resolution" not in _norm(p.body):
                findings.append(
                    LintFinding(
                        "source_gaps",
                        "info",
                        "unresolved Question page — source gap",
                        p.page_uid,
                        p.path,
                        proposal="suggest_search_keywords",
                        meta={"keywords": [p.title]},
                    )
                )
    return findings


def check_memory_experience_consistency(
    pages: list[WikiPage],
    schema: SchemaBundle,
) -> list[LintFinding]:
    del schema
    findings: list[LintFinding] = []
    for p in pages:
        # Disabled experience still referenced
        for ref in p.disabled_experience_refs:
            if ref in p.body or ref in p.raw:
                findings.append(
                    LintFinding(
                        "memory_experience_consistency",
                        "warning",
                        f"disabled experience {ref!r} still referenced",
                        p.page_uid,
                        p.path,
                        proposal="remove_or_replace_experience_ref",
                    )
                )
        # Explicit marker in templates
        if "experience:disabled" in p.raw or "已停用经验" in p.body:
            findings.append(
                LintFinding(
                    "memory_experience_consistency",
                    "warning",
                    "template references disabled experience",
                    p.page_uid,
                    p.path,
                    proposal="review_experience_refs",
                )
            )
        # Memory vs wiki conflict marker
        if "memory_conflict" in p.raw or "记忆与结论冲突" in p.body:
            findings.append(
                LintFinding(
                    "memory_experience_consistency",
                    "warning",
                    "memory conflicts with wiki conclusion",
                    p.page_uid,
                    p.path,
                    proposal="queue_for_review",
                )
            )
    return findings


CHECK_FUNCS: dict[str, Callable[..., list[LintFinding]]] = {
    "format": check_format,
    "broken_links": check_broken_links,
    "orphan_pages": check_orphan_pages,
    "missing_concepts": check_missing_concepts,
    "contradictions": check_contradictions,
    "stale_content": check_stale_content,
    "duplicates": check_duplicates,
    "source_gaps": check_source_gaps,
    "memory_experience_consistency": check_memory_experience_consistency,
}


def run_lint(
    pages: list[WikiPage],
    *,
    schema: SchemaBundle | None = None,
    workspace: Path | None = None,
    enabled_only: bool = True,
    today: date | None = None,
) -> LintReport:
    if schema is None:
        if workspace is not None:
            schema = load_workspace_schema(workspace)
        else:
            schema = load_default_schema()

    findings: list[LintFinding] = []
    issues: list[str] = []
    for name in sorted(REQUIRED_LINT_RULES):
        rule = schema.lint_rules.get(name)
        if rule is None:
            issues.append(f"missing_rule_def:{name}")
            continue
        if enabled_only and not rule.enabled:
            continue
        fn = CHECK_FUNCS[name]
        if name == "stale_content":
            findings.extend(fn(pages, schema, today=today))
        else:
            findings.extend(fn(pages, schema))

    return LintReport(
        report_uid=_uid("lint"),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        findings=findings,
        auto_modified_pages=[],  # hard rule: no auto rewrite
        issues=issues,
    )


def lint_workspace(workspace: Path, **kwargs: Any) -> LintReport:
    pages = load_wiki_pages(Path(workspace) / "wiki")
    return run_lint(pages, workspace=Path(workspace), **kwargs)


def lint_report_as_dict(report: LintReport) -> dict[str, Any]:
    return asdict(report)
