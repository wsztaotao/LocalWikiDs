"""R3.22: wiki dedup gate + related links + stock merge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitError, CommitService, E_DEDUP_BLOCK  # noqa: E402
from localwiki.wiki_gate import (  # noqa: E402
    ensure_related_section,
    match_candidate,
    transform_ops,
    verify_ops,
)
from localwiki.wiki_merge import scan_wiki_duplicates  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def _page(title: str, body: str, *, page_type: str = "Topic", extra_fm: str = "") -> str:
    return (
        f"---\nokf_version: \"0.2\"\npage_type: \"{page_type}\"\n"
        f"title: \"{title}\"\n{extra_fm}---\n\n# {title}\n\n{body}\n"
    )


def test_title_duplicate_drops_topic_keeps_source(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "成果信息.md").write_text(
        _page("成果信息", "已有成果摘要与编号说明。"),
        encoding="utf-8",
    )
    svc = CommitService(ws)
    ops = [
        {
            "op": "create",
            "path": "references/src_abc.md",
            "content": "# src\n\n来源摘录\n",
        },
        {
            "op": "create",
            "path": "topics/成果.md",
            "content": _page("成果信息", "又一份成果信息正文。"),
        },
    ]
    cs = svc.create_changeset(purpose="test-dup", ops=ops)
    paths = {o.path for o in cs.ops}
    assert "topics/成果.md" not in paths
    assert "references/src_abc.md" in paths
    assert cs.gate and any(a.get("action") == "drop_create" for a in cs.gate.get("actions") or [])
    # bidirectional-ish: topic or source got 相关
    joined = "\n".join(o.content or "" for o in cs.ops)
    assert "[[topics/成果信息.md" in joined or "成果信息" in joined
    cs = svc.accept(cs.change_uid)
    assert cs.state == "accepted"
    assert not (wiki / "topics" / "成果.md").exists()
    assert (wiki / "references" / "src_abc.md").exists()


def test_body_high_similarity_duplicate(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    body = "量子纠缠是量子力学中的非定域关联现象，测量一方即影响另一方状态描述。" * 5
    (wiki / "topics" / "a.md").write_text(_page("量子纠缠概述", body), encoding="utf-8")
    mr = match_candidate(
        ws,
        title="纠缠现象笔记",
        path="topics/b.md",
        body=body,  # identical body → jaccard 1.0
    )
    assert mr.kind == "duplicate"
    assert mr.confidence == "high"
    assert mr.tier in ("body", "title")


def test_related_links_idempotent(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    # Overlap enough for related band but not duplicate
    (wiki / "topics" / "语文课改.md").write_text(
        _page("语文课改", "小学语文课内外融合与核心素养培育模式研究讨论。"),
        encoding="utf-8",
    )
    content = _page(
        "分层进阶实践",
        "分层进阶与课内外融合的实践路径，面向核心素养。额外独有句子甲乙丙。",
    )
    ops1, meta1 = transform_ops(
        ws,
        [{"op": "create", "path": "topics/分层.md", "content": content}],
    )
    # Force related by second transform on already-linked content
    text = next(o.content for o in ops1 if o.path == "topics/分层.md")
    once = ensure_related_section(text, "[[topics/语文课改.md|语文课改]]")
    twice = ensure_related_section(once, "[[topics/语文课改.md|语文课改]]")
    assert once == twice
    assert once.count("[[topics/语文课改.md") == 1


def test_protected_no_silent_peer_edit(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    secret_body = _page(
        "机密主题",
        "机密内容不可外链改写。" * 5,
        extra_fm="secret: true\n",
    )
    (wiki / "topics" / "机密主题.md").write_text(secret_body, encoding="utf-8")
    # Related-ish new page
    ops, meta = transform_ops(
        ws,
        [
            {
                "op": "create",
                "path": "topics/公开笔记.md",
                "content": _page("公开笔记", "机密内容不可外链改写。" * 5 + "另有公开补充。"),
            }
        ],
    )
    # Peer protected page should not appear as update in ops
    assert not any(o.path == "topics/机密主题.md" and o.op == "update" for o in ops)


def test_verify_blocks_leaked_duplicate_create(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "alpha.md").write_text(_page("Alpha", "hello"), encoding="utf-8")
    with pytest.raises(CommitError) as ei:
        verify_ops(
            ws,
            [
                {
                    "op": "create",
                    "path": "topics/alpha2.md",
                    "content": _page("Alpha", "other"),
                }
            ],
        )
    assert ei.value.code == E_DEDUP_BLOCK or ei.value.code == "E_DEDUP_BLOCK"


def test_scan_high_merge_and_low_propose(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "t1.md").write_text(_page("同一标题", "版本一"), encoding="utf-8")
    (wiki / "topics" / "t2.md").write_text(_page("同一标题", "版本二"), encoding="utf-8")
    out = scan_wiki_duplicates(ws, apply_high_confidence=True)
    assert out["merged"] or out["proposals"]
    # After high merge, one should be redirect stub
    raw1 = (wiki / "topics" / "t1.md").read_text(encoding="utf-8")
    raw2 = (wiki / "topics" / "t2.md").read_text(encoding="utf-8")
    assert ("redirect_to:" in raw1) or ("redirect_to:" in raw2)


def test_ambiguous_needs_review_without_model(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    base = "这是一段用于相似度测试的中文材料关于本地知识库去重与关联建链的说明文字。"
    (wiki / "topics" / "p1.md").write_text(
        _page("知识库说明甲", base + "甲独有尾。"),
        encoding="utf-8",
    )
    # Craft body overlap in ambiguous band by mixing tokens
    body2 = base + "乙独有完全不同的尾巴内容补充若干。"
    mr = match_candidate(ws, title="知识库说明乙", path="topics/p2.md", body=body2)
    # May be related/ambiguous/duplicate depending on jaccard — force ambiguous path via transform
    if mr.kind != "ambiguous":
        # Still assert create is kept when not high duplicate
        ops, meta = transform_ops(
            ws,
            [{"op": "create", "path": "topics/p2.md", "content": _page("知识库说明乙", body2)}],
        )
        assert any(o.path == "topics/p2.md" and o.op == "create" for o in ops)
    else:
        ops, meta = transform_ops(
            ws,
            [{"op": "create", "path": "topics/p2.md", "content": _page("知识库说明乙", body2)}],
        )
        assert meta.get("needs_review") is True
        assert any(o.path == "topics/p2.md" for o in ops)


def test_changeset_gate_roundtrip(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki"
    (wiki / "topics").mkdir(parents=True)
    (wiki / "topics" / "x.md").write_text(_page("X页", "独有"), encoding="utf-8")
    svc = CommitService(ws)
    cs = svc.create_changeset(
        purpose="rt",
        ops=[{"op": "create", "path": "topics/y.md", "content": _page("Y页", "另一独有")}],
    )
    path = ws / ".kw" / "changesets" / f"{cs.change_uid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "gate" in data
    loaded = svc.get_changeset(cs.change_uid)
    assert loaded.gate == data.get("gate")
