"""Stock wiki duplicate scan + merge (R3.22)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from localwiki.core import CommitService, PageOp, file_sha256
from localwiki.wiki_gate import (
    BODY_DUP,
    is_protected_content,
    is_source_page,
    is_topic_like,
    jaccard,
    normalize_title,
    ensure_related_section,
    _token_set,
    _wikilink,
    _list_existing_pages,
)
from localwiki.wiki_graph import build_wiki_graph, extract_wikilinks


def _inbound_counts(root: Path) -> dict[str, int]:
    g = build_wiki_graph(root)
    counts: dict[str, int] = {n["id"]: 0 for n in g.get("nodes") or []}
    for e in g.get("edges") or []:
        tgt = e.get("target")
        if tgt in counts:
            counts[tgt] += 1
    return counts


def choose_canonical(root: Path, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    inbound = _inbound_counts(root)
    ia, ib = inbound.get(a["path"], 0), inbound.get(b["path"], 0)
    if ia != ib:
        return a if ia > ib else b
    pa = root / "wiki" / a["path"]
    pb = root / "wiki" / b["path"]
    try:
        ma, mb = pa.stat().st_mtime, pb.stat().st_mtime
        if ma != mb:
            return a if ma < mb else b  # older wins
    except OSError:
        pass
    return a if a["path"] <= b["path"] else b


def _redirect_stub(canonical_path: str, canonical_title: str, former_title: str) -> str:
    link = _wikilink(canonical_path, canonical_title)
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"Topic\"\n"
        f"title: \"{former_title}\"\nredirect_to: \"{canonical_path}\"\n---\n\n"
        f"# {former_title}\n\n"
        f"> 已合并至 {link}（R3.22 去重）。\n\n"
        f"## 相关\n\n- {link}\n"
    )


def merge_pair(root: Path, primary: dict[str, Any], secondary: dict[str, Any]) -> list[PageOp]:
    """Build ops: enrich primary with secondary source links; stub secondary."""
    root = Path(root).resolve()
    p_path, s_path = primary["path"], secondary["path"]
    p_raw = (root / "wiki" / p_path).read_text(encoding="utf-8", errors="replace")
    s_raw = (root / "wiki" / s_path).read_text(encoding="utf-8", errors="replace")

    # Collect reference wikilinks from secondary
    new_p = p_raw
    for raw_link in extract_wikilinks(s_raw):
        target = raw_link.split("|", 1)[0].strip()
        if target.startswith("references/") or "/references/" in f"/{target}":
            title = raw_link.split("|", 1)[1].strip() if "|" in raw_link else Path(target).stem
            new_p = ensure_related_section(new_p, _wikilink(target, title))
    # Link primary ↔ note redirect
    new_p = ensure_related_section(new_p, _wikilink(s_path, secondary.get("title") or Path(s_path).stem))
    # Prefer explicit sources mentioned in secondary body as markdown paths
    for m in __import__("re").findall(r"references/[A-Za-z0-9_./\-]+\.md", s_raw):
        new_p = ensure_related_section(new_p, _wikilink(m, Path(m).stem))

    stub = _redirect_stub(p_path, primary.get("title") or Path(p_path).stem, secondary.get("title") or Path(s_path).stem)

    ops = [
        PageOp(
            op="update",
            path=p_path,
            content=new_p,
            base_sha256=file_sha256(root / "wiki" / p_path),
        ),
        PageOp(
            op="update",
            path=s_path,
            content=stub,
            base_sha256=file_sha256(root / "wiki" / s_path),
        ),
    ]
    return ops


def _pair_duplicates(pages: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Return (a, b, confidence) pairs among topic-like pages."""
    topics = [
        p
        for p in pages
        if is_topic_like(p["path"], content=p.get("raw"))
        and not is_source_page(p["path"])
        and not p.get("redirect_to")
    ]
    pairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    seen: set[tuple[str, str]] = set()

    # Title groups
    by_title: dict[str, list[dict[str, Any]]] = {}
    for p in topics:
        keys = {normalize_title(p["title"])} | {
            normalize_title(a) for a in (p.get("aliases") or [])
        }
        for k in keys:
            if k:
                by_title.setdefault(k, []).append(p)
    for group in by_title.values():
        uids = sorted({g["path"] for g in group})
        if len(uids) < 2:
            continue
        for i in range(len(uids)):
            for j in range(i + 1, len(uids)):
                key = (uids[i], uids[j])
                if key in seen:
                    continue
                seen.add(key)
                a = next(x for x in topics if x["path"] == uids[i])
                b = next(x for x in topics if x["path"] == uids[j])
                pairs.append((a, b, "high"))

    # Body similarity (body-first). Cap quadratic work for large wikis.
    body_topics = topics[:80]
    for i, a in enumerate(body_topics):
        ta = _token_set((a.get("body") or "")[:2000])
        if not ta:
            continue
        for b in body_topics[i + 1 :]:
            key = tuple(sorted((a["path"], b["path"])))
            if key in seen:
                continue
            score = jaccard(ta, _token_set((b.get("body") or "")[:2000]))
            if score >= BODY_DUP:
                seen.add(key)
                pairs.append((a, b, "high"))
            elif score >= 0.70:
                seen.add(key)
                pairs.append((a, b, "low"))
    return pairs


def scan_wiki_duplicates(
    root: Path | str,
    *,
    apply_high_confidence: bool = True,
    max_pairs: int = 25,
) -> dict[str, Any]:
    root = Path(root).resolve()
    pages = _list_existing_pages(root)
    pairs = _pair_duplicates(pages)
    # Prefer high-confidence title merges first; cap work per run
    pairs.sort(key=lambda t: (0 if t[2] == "high" else 1, t[0]["path"], t[1]["path"]))
    pairs = pairs[: max(1, int(max_pairs))]
    merged: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    svc = CommitService(root)

    for a, b, conf in pairs:
        if a.get("protected") or b.get("protected") or is_protected_content(a.get("raw") or "") or is_protected_content(
            b.get("raw") or ""
        ):
            skipped.append({"a": a["path"], "b": b["path"], "reason": "protected"})
            continue
        # Never merge away a sole source page as secondary if it's references
        if is_source_page(a["path"]) or is_source_page(b["path"]):
            skipped.append({"a": a["path"], "b": b["path"], "reason": "source_page"})
            continue
        primary = choose_canonical(root, a, b)
        secondary = b if primary is a else a
        ops = merge_pair(root, primary, secondary)
        purpose = f"dedup-{'merge' if conf == 'high' else 'proposal'} {secondary['path']} -> {primary['path']}"
        if conf == "high" and apply_high_confidence:
            try:
                cs = svc.create_changeset(
                    purpose=purpose, ops=ops, state="ready", skip_gate=True
                )
                cs = svc.accept(cs.change_uid)
            except Exception as exc:  # noqa: BLE001
                skipped.append(
                    {
                        "a": a["path"],
                        "b": b["path"],
                        "reason": f"accept_failed:{exc}",
                    }
                )
                continue
            merged.append(
                {
                    "primary": primary["path"],
                    "secondary": secondary["path"],
                    "change_uid": cs.change_uid,
                    "state": cs.state,
                }
            )
        else:
            cs = svc.create_changeset(
                purpose=purpose, ops=ops, state="ready", skip_gate=True
            )
            proposals.append(
                {
                    "primary": primary["path"],
                    "secondary": secondary["path"],
                    "change_uid": cs.change_uid,
                    "confidence": conf,
                }
            )
    return {
        "ok": True,
        "merged": merged,
        "proposals": proposals,
        "skipped": skipped,
        "pair_count": len(pairs),
    }
