"""R3.9 model vs heuristic compile."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from localwiki.agent.routed_model import ModelNotConfiguredError  # noqa: E402
from localwiki.extractors import ExtractError  # noqa: E402
from localwiki.llmwiki import model_compile as mc  # noqa: E402
from localwiki.sources import compile_source, import_bytes  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "extractors"


class _FakeCompileModel:
    model = "fake-compile"
    provider_name = "test"
    route_name = "knowledge_compile"
    timeout = 30.0

    def generate(self, messages: list[dict[str, Any]], *, tools: Any, extras: dict) -> Any:
        del tools, extras, messages
        payload = {
            "summary": "摘要：样例文档",
            "pages": [
                {
                    "title": "样例主题",
                    "page_type": "Topic",
                    "summary": "要点",
                    "body": "## 精读\n\n模型生成的正文。\n",
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


def test_heuristic_compile_explicit(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    meta = import_bytes(ws, (FIX / "sample.md").read_bytes(), filename="sample.md")
    out = compile_source(ws, meta["source_uid"], mode="heuristic")
    assert out["compile_mode"] == "heuristic"
    assert out["changeset"]["state"] == "ready"


def test_model_compile_with_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    meta = import_bytes(ws, (FIX / "sample.md").read_bytes(), filename="sample.md")
    real = mc.model_compile_to_ops

    def fake_compile(root, *, text, meta, source_uid, model=None):
        return real(
            root,
            text=text,
            meta=meta,
            source_uid=source_uid,
            model=_FakeCompileModel(),
        )

    monkeypatch.setattr(mc, "model_compile_to_ops", fake_compile)
    out = compile_source(ws, meta["source_uid"], mode="model")
    assert out["compile_mode"] == "model"
    assert out.get("model") == "fake-compile"
    assert out["changeset"]["change_uid"]


def test_model_compile_missing_model_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    meta = import_bytes(ws, (FIX / "sample.md").read_bytes(), filename="sample.md")

    def boom(*_a, **_k):
        raise ModelNotConfiguredError("no model", code="E_MODEL_UNAVAILABLE")

    monkeypatch.setattr(mc, "model_compile_to_ops", boom)
    with pytest.raises(ExtractError) as ei:
        compile_source(ws, meta["source_uid"], mode="model")
    assert ei.value.code == "E_MODEL_UNAVAILABLE"
