"""R3-A: wiki FTS5 search."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.tools.native_workspace import (  # noqa: E402
    fts_search,
    rebuild_wiki_fts,
)
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_rebuild_and_search(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True, exist_ok=True)
    (wiki / "topics" / "alpha.md").write_text(
        "# Alpha Page\n\nUniqueKeyword zebra\n", encoding="utf-8"
    )
    (wiki / "topics" / "beta.md").write_text(
        "# Beta\n\nNothing special\n", encoding="utf-8"
    )
    out = rebuild_wiki_fts(ws)
    assert out["ok"] and out["indexed"] >= 2
    hits = fts_search(ws, "UniqueKeyword")
    assert any(h["path"] == "topics/alpha.md" for h in hits)
    assert all("UniqueKeyword" in (h.get("snippet") or "") or h["path"].endswith("alpha.md") for h in hits[:1] or hits)


def test_empty_query(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    assert fts_search(ws, "") == []
    assert fts_search(ws, "   ") == []
