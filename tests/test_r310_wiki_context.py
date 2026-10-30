"""R3.10 wiki context retrieval."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.tools.native_workspace import rebuild_wiki_fts  # noqa: E402
from localwiki.wiki_context import build_prompt_with_wiki, retrieve_wiki_context  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_retrieve_hit_and_miss(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    page = ws / "wiki" / "topics" / "demo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# 逻辑哲学论笔记\n\n图像论与可说不可说。\n", encoding="utf-8")
    rebuild_wiki_fts(ws)
    hit = retrieve_wiki_context(ws, "逻辑哲学论")
    assert hit["hit"] is True
    assert hit["paths"]
    prompt = build_prompt_with_wiki("介绍下这本书", hit)
    assert "本地知识库" in prompt
    assert "介绍下这本书" in prompt

    miss = retrieve_wiki_context(ws, "zzzz-no-such-topic-xyz")
    assert miss["hit"] is False
    p2 = build_prompt_with_wiki("hello", miss)
    # R3.15: miss must not force a "未命中" opening line
    assert "未命中" not in p2
    assert "【用户问题】" in p2
    assert "hello" in p2


def test_retrieve_full_sentence_chinese(tmp_path: Path) -> None:
    """整句问法也要靠子串命中标题短语（R3.10 补丁）。"""
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    page = ws / "wiki" / "topics" / "tractatus.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# 逻辑哲学论概览\n\n世界是事实的总和。\n", encoding="utf-8")
    rebuild_wiki_fts(ws)
    hit = retrieve_wiki_context(ws, "逻辑哲学论的核心观点是什么")
    assert hit["hit"] is True
    assert any("tractatus" in p or "逻辑" in p for p in hit["paths"]) or hit["paths"]
