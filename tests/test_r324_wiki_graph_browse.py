"""R3.24: categorized wiki graph browse."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService  # noqa: E402
from localwiki.wiki_categories import (  # noqa: E402
    cluster_titles,
    dir_category,
    parse_page_categories,
    set_categories_in_content,
)
from localwiki.wiki_graph import build_wiki_graph  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def _page(title: str, body: str, *, cats: list[str] | None = None) -> str:
    cat_line = ""
    if cats:
        cat_line = "categories: [" + ", ".join(f'"{c}"' for c in cats) + "]\n"
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"Topic\"\n"
        f"title: \"{title}\"\n{cat_line}---\n\n# {title}\n\n{body}\n"
    )


def test_dir_category_and_custom_meta(tmp_path: Path) -> None:
    assert dir_category("topics/a.md") == "topics"
    assert dir_category("references/src.md") == "references"
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "a.md").write_text(
        _page("成果甲", "关于成果", cats=["课题"]),
        encoding="utf-8",
    )
    g = build_wiki_graph(ws, view="overview", weak=True)
    ids = {c["id"] for c in (g["meta"]["categories"] or [])}
    assert "topics" in ids
    assert "custom:课题" in ids
    assert all(n.get("kind") == "category" for n in g["nodes"])


def test_weak_cap_and_weak_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    for i in range(12):
        (wiki / f"p{i}.md").write_text(
            _page(f"同一主题样例{i}", f"正文{i} 同一主题样例共享", cats=["同题"]),
            encoding="utf-8",
        )
    g_on = build_wiki_graph(ws, weak=True)
    weak_on = [e for e in g_on["edges"] if e.get("kind") == "weak"]
    assert weak_on
    # degree cap: no node > 8 weak
    deg: dict[str, int] = {}
    for e in weak_on:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    assert max(deg.values()) <= 8
    g_off = build_wiki_graph(ws, weak=False)
    assert all(e.get("kind") != "weak" for e in g_off["edges"])
    ov_on = build_wiki_graph(ws, view="overview", weak=True)
    ov_off = build_wiki_graph(ws, view="overview", weak=False)
    w_on = sum(e.get("weight") or 0 for e in ov_on["edges"])
    w_off = sum(e.get("weight") or 0 for e in ov_off["edges"])
    assert w_on >= w_off


def test_overview_vs_expanded(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "references").mkdir(parents=True)
    (wiki / "topics" / "t.md").write_text(
        _page("主题页", "见 [[references/src.md]]"),
        encoding="utf-8",
    )
    (wiki / "references" / "src.md").write_text(
        _page("来源页", "回 [[topics/t.md]]"),
        encoding="utf-8",
    )
    ov = build_wiki_graph(ws, view="overview", weak=False)
    assert all(n.get("kind") == "category" for n in ov["nodes"])
    assert any(e.get("kind") == "category_summary" for e in ov["edges"])
    ex = build_wiki_graph(ws, view="expanded", cats="topics", weak=False)
    paths = {n["path"] for n in ex["nodes"]}
    assert "topics/t.md" in paths
    assert "references/src.md" not in paths


def test_hide_isolates_and_cluster_off(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    (wiki / "linked.md").write_text(
        _page("有链", "见 [[topics/lonely.md]]"),
        encoding="utf-8",
    )
    (wiki / "lonely.md").write_text(
        _page("互链", "[[topics/linked.md]]"),
        encoding="utf-8",
    )
    (wiki / "orphan.md").write_text(_page("真孤立", "无任何链接"), encoding="utf-8")
    g = build_wiki_graph(
        ws, view="expanded", cats="topics", weak=False, hide_isolates=True
    )
    ids = {n["id"] for n in g["nodes"]}
    assert "topics/orphan.md" not in ids
    assert "topics/linked.md" in ids
    g2 = build_wiki_graph(ws, cluster=False)
    assert all(not (n.get("clusters") or []) for n in g2["nodes"])


def test_cluster_titles_groups() -> None:
    items = [
        {"path": "a.md", "title": "成果信息表"},
        {"path": "b.md", "title": "成果信息汇总"},
        {"path": "c.md", "title": "完全无关西瓜"},
    ]
    m = cluster_titles(items)
    assert m["a.md"] == m["b.md"]
    assert m["c.md"] == "_ungrouped" or m["c.md"] != m["a.md"]


def test_categories_api_commit(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    path = "topics/x.md"
    (wiki / "x.md").write_text(_page("X", "body"), encoding="utf-8")
    raw = (wiki / "x.md").read_text(encoding="utf-8")
    new_c = set_categories_in_content(raw, ["标签甲", "标签乙"])
    assert "标签甲" in new_c
    svc = CommitService(ws)
    from localwiki.core import file_sha256

    cs = svc.create_changeset(
        purpose="cats",
        ops=[
            {
                "op": "update",
                "path": path,
                "content": new_c,
                "base_sha256": file_sha256(wiki / "x.md"),
            }
        ],
        state="ready",
    )
    cs = svc.accept(cs.change_uid)
    assert cs.state == "accepted"
    assert parse_page_categories((ws / "wiki" / path).read_text(encoding="utf-8")) == [
        "标签甲",
        "标签乙",
    ]
