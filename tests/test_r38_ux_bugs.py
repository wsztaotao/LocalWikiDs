"""R3.8 patch: vendor model list, xlsx extract, proposal filter helpers."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.extractors import ExtractError, detect_format, extract_bytes, extract_path  # noqa: E402
from localwiki.personal_core import list_provider_models  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_detect_xlsx_by_extension() -> None:
    assert detect_format("成果信息.xlsx") == "xlsx"


def test_extract_xlsx(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "成果"
    ws.append(["名称", "状态"])
    ws.append(["分层进阶", "完成"])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    r = extract_bytes(data, filename="成果信息.xlsx")
    assert r.format == "xlsx"
    assert "分层进阶" in r.text
    assert r.sha256
    path = tmp_path / "sample.xlsx"
    path.write_bytes(data)
    r2 = extract_path(path)
    assert r2.format == "xlsx"
    assert "成果" in r2.text


def test_extract_still_rejects_unknown() -> None:
    with pytest.raises(ExtractError) as ei:
        extract_bytes(b"not-a-real-file", filename="weird.bin")
    assert ei.value.code == "E_INVALID_INPUT"


def test_list_provider_models_suggestions(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    # Write qwen provider without calling remote
    from localwiki.model_routing import load_workspace_routes, save_workspace_routes
    from localwiki.model_routing import ProviderConfig

    cfg = load_workspace_routes(ws)
    cfg.providers["qwen"] = ProviderConfig(
        kind="openai_compatible",
        location="online",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret:qwen",
    )
    save_workspace_routes(ws, cfg)

    def http(method: str, url: str, **kwargs):
        raise RuntimeError("offline")

    out = list_provider_models(ws, "qwen", http=http)
    assert out["ok"] is True
    names = [m["name"] for m in out["models"]]
    assert "qwen-plus" in names
    assert out["source"] in ("suggestions", "remote")


def test_list_provider_models_remote(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    from localwiki.model_routing import ProviderConfig, load_workspace_routes, save_workspace_routes

    cfg = load_workspace_routes(ws)
    cfg.providers["deepseek"] = ProviderConfig(
        kind="openai_compatible",
        location="online",
        base_url="https://api.deepseek.com/v1",
        api_key="",  # no secret — still can list via mock without auth
    )
    save_workspace_routes(ws, cfg)

    def http(method: str, url: str, **kwargs):
        assert method == "GET"
        assert url.endswith("/models")
        return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

    out = list_provider_models(ws, "deepseek", http=http)
    assert out["source"] == "remote"
    assert [m["name"] for m in out["models"][:2]] == [
        "deepseek-chat",
        "deepseek-reasoner",
    ]
