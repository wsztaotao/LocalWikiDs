"""Build wiki relationship graph from ``[[wikilinks]]`` + categories/weak edges (R3.7 / R3.24)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from localwiki.wiki_categories import (
    category_label,
    cluster_titles,
    dir_category,
    parse_page_categories,
)

# [[target]] or [[target|alias]] — target may include path fragments
_WIKILINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")

WEAK_DEGREE_CAP = 8


def _guess_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path.stem
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip() or path.stem
    return path.stem


def _normalize_target(raw: str, *, source_rel: str) -> str:
    """Map link target to wiki-relative posix path (with .md)."""
    target = (raw or "").split("|", 1)[0].strip()
    target = target.replace("\\", "/").lstrip("/")
    if not target:
        return ""
    if "#" in target:
        target = target.split("#", 1)[0].strip()
    if not target:
        return ""
    if not target.endswith(".md"):
        target = target + ".md"
    if ".." in target.split("/"):
        src_dir = Path(source_rel).parent
        try:
            resolved = (src_dir / target).as_posix()
            parts: list[str] = []
            for p in resolved.split("/"):
                if p in ("", "."):
                    continue
                if p == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(p)
            target = "/".join(parts)
        except Exception:  # noqa: BLE001
            return ""
    return target.lstrip("/")


def extract_wikilinks(body: str) -> list[str]:
    out: list[str] = []
    for m in _WIKILINK_RE.finditer(body or ""):
        inner = m.group(1)
        if inner.strip():
            out.append(inner)
    return out


def _title_sim(a: str, b: str) -> float:
    from localwiki.wiki_categories import _jaccard, _token_set

    return _jaccard(_token_set(a), _token_set(b))


def _scan_pages(wiki: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    nodes: dict[str, dict[str, Any]] = {}
    path_set: set[str] = set()
    if not wiki.is_dir():
        return nodes, path_set
    files = sorted(wiki.rglob("*.md"))
    path_set = {p.relative_to(wiki).as_posix() for p in files}
    for p in files:
        rel = p.relative_to(wiki).as_posix()
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        title = _guess_title(p)
        # Prefer FM title if present
        from localwiki.llmwiki.lint import parse_front_matter

        meta, body = parse_front_matter(raw)
        if meta.get("title"):
            title = str(meta.get("title")).strip() or title
        cats = parse_page_categories(raw)
        dcat = dir_category(rel)
        nodes[rel] = {
            "id": rel,
            "label": title,
            "path": rel,
            "title": title,
            "kind": "page",
            "dir_category": dcat,
            "categories": cats,
            "clusters": [],
            "body": body,
            "raw": raw,
        }
    return nodes, path_set


def _assign_clusters(nodes: dict[str, dict[str, Any]]) -> None:
    by_dir: dict[str, list[dict[str, Any]]] = {}
    for n in nodes.values():
        by_dir.setdefault(n["dir_category"], []).append(
            {"path": n["path"], "title": n["title"]}
        )
    for _dcat, items in by_dir.items():
        mapping = cluster_titles(items)
        for path, cid in mapping.items():
            if path in nodes:
                nodes[path]["clusters"] = [] if cid == "_ungrouped" else [cid]


def _wikilink_edges(
    nodes: dict[str, dict[str, Any]], path_set: set[str]
) -> tuple[list[dict[str, Any]], int]:
    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str]] = set()
    unresolved = 0
    for rel, n in nodes.items():
        for raw in extract_wikilinks(n.get("raw") or n.get("body") or ""):
            tgt = _normalize_target(raw, source_rel=rel)
            if not tgt:
                unresolved += 1
                continue
            if tgt not in path_set:
                unresolved += 1
                continue
            key = (rel, tgt)
            if key in edge_keys or rel == tgt:
                continue
            edge_keys.add(key)
            edges.append({"source": rel, "target": tgt, "kind": "wikilink"})
    return edges, unresolved


def _weak_edges(nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Same dir / same cluster / shared custom category; degree cap per node."""
    paths = list(nodes.keys())
    # candidate scores per unordered pair
    scores: dict[tuple[str, str], float] = {}
    # index by dir, cluster, custom tag
    by_dir: dict[str, list[str]] = {}
    by_cluster: dict[str, list[str]] = {}
    by_tag: dict[str, list[str]] = {}
    for p, n in nodes.items():
        by_dir.setdefault(n["dir_category"], []).append(p)
        for c in n.get("clusters") or []:
            by_cluster.setdefault(f"{n['dir_category']}:{c}", []).append(p)
        for t in n.get("categories") or []:
            by_tag.setdefault(t, []).append(p)

    def add_pair(a: str, b: str, base: float) -> None:
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        sim = _title_sim(nodes[a]["title"], nodes[b]["title"])
        scores[key] = max(scores.get(key, 0.0), base + 0.01 * sim)

    for group in by_dir.values():
        # only pair within dir if small; else rely on cluster/tag + title sim sample
        if len(group) <= 40:
            for i, a in enumerate(group):
                for b in group[i + 1 :]:
                    add_pair(a, b, 0.5)
        else:
            # connect each to top-3 title-similar in same dir
            for a in group:
                ranked = sorted(
                    (b for b in group if b != a),
                    key=lambda b: _title_sim(nodes[a]["title"], nodes[b]["title"]),
                    reverse=True,
                )[:3]
                for b in ranked:
                    add_pair(a, b, 0.4)

    for group in by_cluster.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                add_pair(a, b, 0.8)

    for group in by_tag.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                add_pair(a, b, 0.7)

    # Greedy by score: keep edge only if both ends stay under degree cap
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    deg: dict[str, int] = {p: 0 for p in paths}
    chosen: list[tuple[str, str]] = []
    for (a, b), _sc in ranked:
        if deg[a] >= WEAK_DEGREE_CAP or deg[b] >= WEAK_DEGREE_CAP:
            continue
        chosen.append((a, b))
        deg[a] += 1
        deg[b] += 1
    return [{"source": a, "target": b, "kind": "weak"} for a, b in chosen]


def _member_sets(
    nodes: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """category_id -> member page paths (dir + custom)."""
    members: dict[str, set[str]] = {}
    for p, n in nodes.items():
        d = n["dir_category"]
        members.setdefault(d, set()).add(p)
        for t in n.get("categories") or []:
            cid = f"custom:{t}"
            members.setdefault(cid, set()).add(p)
    return members


def _category_summary_edges(
    members: dict[str, set[str]],
    page_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # map path -> set of category ids it belongs to
    path_cats: dict[str, set[str]] = {}
    for cid, paths in members.items():
        for p in paths:
            path_cats.setdefault(p, set()).add(cid)
    weights: dict[tuple[str, str], int] = {}
    for e in page_edges:
        sa, sb = path_cats.get(e["source"]) or set(), path_cats.get(e["target"]) or set()
        for ca in sa:
            for cb in sb:
                if ca == cb:
                    continue
                key = (ca, cb) if ca < cb else (cb, ca)
                weights[key] = weights.get(key, 0) + 1
    return [
        {
            "source": f"cat:{a}",
            "target": f"cat:{b}",
            "kind": "category_summary",
            "weight": w,
        }
        for (a, b), w in sorted(weights.items())
        if w > 0
    ]


def _parse_cats_param(cats: str | list[str] | None) -> set[str]:
    if cats is None:
        return set()
    if isinstance(cats, list):
        parts = [str(c).strip() for c in cats]
    else:
        parts = [p.strip() for p in str(cats).split(",") if p.strip()]
    return set(parts)


def build_wiki_graph(
    workspace: Path | str,
    *,
    view: str | None = None,
    cats: str | list[str] | None = None,
    weak: bool = True,
    hide_isolates: bool = False,
    cluster: bool = True,
    q: str | None = None,
) -> dict[str, Any]:
    """Return graph. Default (view=None): full page graph + meta (R3.7 compat)."""
    root = Path(workspace).resolve()
    wiki = root / "wiki"
    empty = {
        "ok": True,
        "nodes": [],
        "edges": [],
        "stats": {"nodes": 0, "edges": 0, "unresolved": 0},
        "meta": {
            "categories": [],
            "clusters": [],
            "weak_edge_count": 0,
            "wikilink_edge_count": 0,
        },
    }
    if not wiki.is_dir():
        return empty

    nodes, path_set = _scan_pages(wiki)
    if cluster:
        _assign_clusters(nodes)
    else:
        for n in nodes.values():
            n["clusters"] = []

    wl_edges, unresolved = _wikilink_edges(nodes, path_set)
    weak_list = _weak_edges(nodes) if weak else []
    page_edges = list(wl_edges) + list(weak_list)

    members = _member_sets(nodes)
    cat_meta = []
    for cid, paths in sorted(members.items(), key=lambda x: (-len(x[1]), x[0])):
        if not paths:
            continue
        cat_meta.append(
            {
                "id": cid,
                "label": category_label(cid),
                "member_count": len(paths),
            }
        )
    cluster_meta = []
    seen_cl: set[tuple[str, str]] = set()
    for n in nodes.values():
        for c in n.get("clusters") or []:
            key = (n["dir_category"], c)
            if key in seen_cl:
                continue
            seen_cl.add(key)
            members_c = [
                p
                for p, nn in nodes.items()
                if nn["dir_category"] == n["dir_category"] and c in (nn.get("clusters") or [])
            ]
            cluster_meta.append(
                {
                    "id": f"{n['dir_category']}:{c}",
                    "dir_category": n["dir_category"],
                    "label": c,
                    "member_count": len(members_c),
                }
            )

    meta = {
        "categories": cat_meta,
        "clusters": cluster_meta,
        "weak_edge_count": len(weak_list),
        "wikilink_edge_count": len(wl_edges),
    }

    view_norm = (view or "").strip().lower() or None
    selected = _parse_cats_param(cats)

    if view_norm == "overview":
        cat_nodes = [
            {
                "id": f"cat:{c['id']}",
                "label": f"{c['label']} ({c['member_count']})",
                "kind": "category",
                "category_id": c["id"],
                "member_count": c["member_count"],
                "path": "",
            }
            for c in cat_meta
        ]
        summary = _category_summary_edges(members, page_edges)
        return {
            "ok": True,
            "nodes": cat_nodes,
            "edges": summary,
            "stats": {
                "nodes": len(cat_nodes),
                "edges": len(summary),
                "unresolved": unresolved,
            },
            "meta": meta,
            "view": "overview",
        }

    if view_norm == "expanded":
        if not selected:
            # treat as overview
            return build_wiki_graph(
                root,
                view="overview",
                weak=weak,
                hide_isolates=hide_isolates,
                cluster=cluster,
                q=q,
            )
        # pages in any selected category
        allowed: set[str] = set()
        for cid in selected:
            allowed |= members.get(cid) or set()
        page_nodes = []
        for p in sorted(allowed):
            n = nodes[p]
            page_nodes.append(
                {
                    "id": n["id"],
                    "label": n["label"],
                    "path": n["path"],
                    "title": n["title"],
                    "kind": "page",
                    "dir_category": n["dir_category"],
                    "categories": n["categories"],
                    "clusters": n["clusters"],
                }
            )
        edges = [
            e
            for e in page_edges
            if e["source"] in allowed and e["target"] in allowed
        ]
        if hide_isolates:
            linked = {e["source"] for e in edges} | {e["target"] for e in edges}
            page_nodes = [n for n in page_nodes if n["id"] in linked]
            keep = {n["id"] for n in page_nodes}
            edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
        qn = (q or "").strip().lower()
        if qn:
            matched = {
                n["id"]
                for n in page_nodes
                if qn in (n.get("title") or "").lower()
                or qn in (n.get("path") or "").lower()
                or qn in (n.get("label") or "").lower()
            }
            neigh = set(matched)
            for e in edges:
                if e["source"] in matched:
                    neigh.add(e["target"])
                if e["target"] in matched:
                    neigh.add(e["source"])
            page_nodes = [n for n in page_nodes if n["id"] in neigh]
            keep = {n["id"] for n in page_nodes}
            edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
            for n in page_nodes:
                n["match"] = n["id"] in matched
        return {
            "ok": True,
            "nodes": page_nodes,
            "edges": edges,
            "stats": {
                "nodes": len(page_nodes),
                "edges": len(edges),
                "unresolved": unresolved,
            },
            "meta": meta,
            "view": "expanded",
        }

    # Default compat: all pages + wikilink (+ weak if enabled)
    out_nodes = []
    for n in nodes.values():
        out_nodes.append(
            {
                "id": n["id"],
                "label": n["label"],
                "path": n["path"],
                "title": n["title"],
                "kind": "page",
                "dir_category": n["dir_category"],
                "categories": n["categories"],
                "clusters": n["clusters"],
            }
        )
    edges = page_edges
    if hide_isolates:
        linked = {e["source"] for e in edges} | {e["target"] for e in edges}
        out_nodes = [n for n in out_nodes if n["id"] in linked]
        keep = {n["id"] for n in out_nodes}
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    # R3.7 tests expect only wikilink when calling without weak awareness —
    # but new default weak=True. Keep default weak=True per spec; r37 tests
    # still pass if we include weak as extra edges (pairs still contain wikilinks).
    return {
        "ok": True,
        "nodes": out_nodes,
        "edges": edges,
        "stats": {
            "nodes": len(out_nodes),
            "edges": len(edges),
            "unresolved": unresolved,
        },
        "meta": meta,
    }
