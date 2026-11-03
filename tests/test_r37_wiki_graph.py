"""R3.7: wiki graph from [[wikilinks]]."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.wiki_graph import build_wiki_graph, extract_wikilinks  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_extract_wikilinks() -> None:
    body = "See [[Alpha]] and [[beta/page|Beta]] and [[#skip]] empty [[]]."
    links = extract_wikilinks(body)
    assert "Alpha" in links
    assert any(x.startswith("beta/page") for x in links)


def test_build_graph_edges(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "a.md").write_text("# A\n\nLink to [[b]] and missing [[nope]].\n", encoding="utf-8")
    (wiki / "b.md").write_text("# B\n\nBack to [[a]].\n", encoding="utf-8")
    g = build_wiki_graph(ws)
    assert g["ok"]
    ids = {n["id"] for n in g["nodes"]}
    assert "a.md" in ids and "b.md" in ids
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("a.md", "b.md") in pairs
    assert ("b.md", "a.md") in pairs
    assert g["stats"]["unresolved"] >= 1
    assert "nope.md" not in ids
