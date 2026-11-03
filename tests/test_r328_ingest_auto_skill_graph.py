"""R3.28: knowledge-ingest skill, auto-accept, graph defaults."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService  # noqa: E402
from localwiki.llmwiki import model_compile as mc  # noqa: E402
from localwiki.llmwiki.knowledge_ingest_skill import (  # noqa: E402
    SKILL_NAME,
    dedup_message_from_gate,
    load_skill_text,
    parse_ingest_payload,
    rate_ingest_risk,
)
from localwiki.sources import compile_source, import_bytes  # noqa: E402
from localwiki.wiki_graph import build_wiki_graph  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "extractors"


def test_parse_ingest_strict_and_loose() -> None:
    strict = parse_ingest_payload(
        json.dumps(
            {
                "summary": "摘要",
                "main_content": "主体",
                "key_points": ["A"],
                "quotes": [{"text": "原句", "note": "重要"}],
                "related": [{"title": "已有", "relation": "补充", "wikilink": "topics/x"}],
                "pages": [
                    {
                        "title": "主题",
                        "page_type": "Topic",
                        "summary": "短",
                        "body": "正文",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    assert strict["ok"] is True
    assert strict["skill_contract"] == "strict"
    assert strict["key_points"] == ["A"]
    assert strict["quotes"][0]["text"] == "原句"

    missing_kp = parse_ingest_payload(
        json.dumps(
            {
                "summary": "仅摘要",
                "pages": [
                    {
                        "title": "旧页",
                        "page_type": "Topic",
                        "summary": "s",
                        "body": "b",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    assert missing_kp["ok"] is False
    assert missing_kp["skill_contract"] == "partial"
    assert "key_points" in missing_kp["missing"]

    loose = parse_ingest_payload("这是一段非 JSON 精读笔记\n第二行")
    assert loose["skill_contract"] == "loose"
    assert loose["pages"]


def test_load_skill_packaged() -> None:
    text = load_skill_text(None)
    assert "knowledge-ingest" in text.lower() or "JSON" in text
    assert "key_points" in text or "核心" in text


def test_rate_ingest_risk_and_dedup() -> None:
    low = rate_ingest_risk(
        gate_meta={"needs_review": False, "actions": []},
        ops=[{"op": "create", "path": "references/src.md", "content": "短"}],
        settings={},
        skill_ok=True,
        skill_contract="strict",
    )
    assert low["level"] == "low"

    always = rate_ingest_risk(
        gate_meta={},
        ops=[],
        settings={"ingest_always_review": True},
        skill_ok=True,
        skill_contract="strict",
    )
    assert always["level"] == "high"
    assert "ingest_always_review" in always["reasons"]

    amb = rate_ingest_risk(
        gate_meta={
            "actions": [
                {
                    "action": "propose",
                    "path": "topics/a.md",
                    "detail": "ambiguous jaccard=0.4",
                }
            ]
        },
        ops=[{"op": "create", "path": "topics/a.md", "content": "x"}],
        skill_ok=True,
        skill_contract="strict",
    )
    assert amb["level"] == "high"

    msg = dedup_message_from_gate(
        {
            "actions": [
                {
                    "action": "drop_create",
                    "target": "topics/dup.md",
                    "confidence": "high",
                }
            ]
        }
    )
    assert msg["hits"]
    assert "查重" in msg["message"]


def test_heuristic_auto_accept_low_risk(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    meta = import_bytes(
        ws,
        b"# Unique Topic XYZ\n\nUnique content about XYZ widget protocol.\n",
        filename="unique_xyz.md",
    )
    out = compile_source(ws, meta["source_uid"], mode="heuristic")
    assert out["skill"] == SKILL_NAME
    assert "dedup" in out
    assert "message" in out["dedup"]
    assert "risk" in out
    assert out["compile_mode"] == "heuristic"
    summary = out.get("summary") or {}
    assert "key_points" in summary or "abstract" in summary
    if out["risk"]["level"] == "low":
        assert out["auto_accepted"] is True
        assert out["changeset"]["state"] == "accepted"
    else:
        assert out["auto_accepted"] is False
        assert out["changeset"]["state"] == "ready"


def test_always_review_blocks_auto(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    cfg = ws / ".kw" / "config.json"
    data = {}
    if cfg.is_file():
        data = json.loads(cfg.read_text(encoding="utf-8"))
    data["ingest_always_review"] = True
    cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = import_bytes(ws, (FIX / "sample.md").read_bytes(), filename="sample.md")
    out = compile_source(ws, meta["source_uid"], mode="heuristic")
    assert out["auto_accepted"] is False
    assert out["risk"]["level"] == "high"
    assert out["changeset"]["state"] == "ready"


class _FakeK7Model:
    model = "fake-k7"
    provider_name = "test"
    route_name = "knowledge_compile"
    timeout = 30.0

    def generate(self, messages: list[dict[str, Any]], *, tools: Any, extras: dict) -> Any:
        del tools, extras
        assert messages
        sys_c = messages[0].get("content") or ""
        assert "Existing wiki" in sys_c or "wiki" in sys_c.lower()
        payload = {
            "summary": "摘要：样例",
            "main_content": "主要内容概述",
            "key_points": ["观点一"],
            "quotes": [{"text": "原句", "note": "关键"}],
            "related": [],
            "pages": [
                {
                    "title": "K7主题",
                    "page_type": "Topic",
                    "summary": "要点",
                    "body": "## 精读\n\n正文。\n",
                }
            ],
        }
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                }
            ]
        }


def test_model_compile_k7_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    meta = import_bytes(ws, (FIX / "sample.md").read_bytes(), filename="sample.md")
    real = mc.model_compile_to_ops

    def fake_compile(root, *, text, meta, source_uid, model=None):
        return real(
            root,
            text=text,
            meta=meta,
            source_uid=source_uid,
            model=_FakeK7Model(),
        )

    monkeypatch.setattr(mc, "model_compile_to_ops", fake_compile)
    out = compile_source(ws, meta["source_uid"], mode="model")
    assert out["compile_mode"] == "model"
    assert out["skill"] == SKILL_NAME
    assert out["summary"]["skill_contract"] == "strict"
    assert out["summary"]["key_points"] == ["观点一"]
    assert "dedup" in out


def test_graph_defaults_weak_off_hide_on(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    wiki = ws / "wiki" / "topics"
    wiki.mkdir(parents=True)
    (wiki / "a.md").write_text("# A\n\n[[topics/b.md]]\n", encoding="utf-8")
    (wiki / "b.md").write_text("# B\n\n[[topics/a.md]]\n", encoding="utf-8")
    (wiki / "orphan.md").write_text("# Orphan\n\nsolo\n", encoding="utf-8")
    g = build_wiki_graph(ws)
    assert all(e.get("kind") != "weak" for e in g["edges"])
    ids = {n["id"] for n in g["nodes"]}
    assert "topics/orphan.md" not in ids
    ov = build_wiki_graph(ws, view="overview")
    assert all(n.get("kind") == "category" for n in ov["nodes"])
