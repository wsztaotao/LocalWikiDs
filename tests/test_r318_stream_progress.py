"""R3.18 tool/status SSE progress + export plain text."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.export_plain import strip_markdown_for_export  # noqa: E402
from localwiki.runtime.opencode_bridge import _tool_events_from_json_line  # noqa: E402
from localwiki.runtime.session_runner import (  # noqa: E402
    Job,
    format_sse,
    tool_status_text,
)
from localwiki.tools.oc_export_cli import main as cli_main  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_strip_markdown_for_export() -> None:
    raw = "# 标题\n\n这是 **粗体** 与 *斜体*，还有 [链接](http://x)。\n\n```\ncode\n```\n"
    plain = strip_markdown_for_export(raw)
    assert "**" not in plain
    assert "#" not in plain.split("\n")[0] or not plain.startswith("#")
    assert "粗体" in plain
    assert "链接" in plain
    assert "http://x" not in plain
    assert "```" not in plain


def test_tool_status_text_mapping() -> None:
    assert "Word" in tool_status_text("export_docx", phase="start")
    assert "PDF" in tool_status_text("export_pdf", phase="start")
    assert "音频" in tool_status_text("export_tts", phase="start")
    assert "失败" in tool_status_text("bash", phase="end", ok=False)


def test_job_push_tool_status_not_in_stream_text() -> None:
    job = Job(job_id="j1", session_id="s1", content="x")
    job.push_event({"type": "tool", "name": "export_docx", "phase": "start", "ok": None})
    job.push_event({"type": "status", "text": "正在导出 Word…"})
    job.push_event({"type": "delta", "text": "正文"})
    assert job.stream_text == "正文"
    types = [e["type"] for e in job.stream_events]
    assert types == ["tool", "status", "delta"]
    raw = format_sse(job.stream_events[0]).decode("utf-8")
    assert "data:" in raw
    assert "export_docx" in raw


def test_bridge_tool_line_parse() -> None:
    line = json.dumps(
        {"type": "tool_use", "name": "export_pdf", "status": "running"},
        ensure_ascii=False,
    )
    evs = _tool_events_from_json_line(line)
    assert evs
    assert evs[0]["name"] == "export_pdf"


def test_oc_export_cli_strips_md(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    monkeypatch.setenv("LOCALWIKI_WORKSPACE", str(ws))
    rc = cli_main(
        [
            "docx",
            "--title",
            "**OpenCode**",
            "--body",
            "第一段 **加粗**。\n\n第二段 # 不是标题标记残留测试。",
            "--stem",
            "plain",
        ]
    )
    assert rc == 0
    files = list((ws / ".kw" / "exports").glob("plain_*.docx"))
    assert files
    from localwiki.office_io import read_docx

    text = read_docx(files[0])
    assert "**" not in text
    assert "加粗" in text


def test_session_runner_forwards_tool_to_sse(tmp_path: Path) -> None:
    from localwiki.runtime.session_runner import SessionRunner

    ensure_workspace(tmp_path)
    events_seen: list[str] = []

    def fake_run(prompt, workspace, **kwargs):
        on_tool = kwargs.get("on_tool")
        if on_tool:
            on_tool({"name": "export_docx", "phase": "start", "ok": None})
            on_tool({"name": "export_docx", "phase": "end", "ok": True})
        on_delta = kwargs.get("on_delta")
        if on_delta:
            on_delta("完成。")
        return {
            "ok": True,
            "stdout": "完成。",
            "tool_events": [{"name": "export_docx", "ok": True}],
        }

    runner = SessionRunner(tmp_path, run_fn=fake_run)
    meta = runner.store.create(title="r318")
    sid = meta["id"]
    enq = runner.enqueue(sid, "写一句并保存到word")
    job_id = enq["job_id"]
    deadline = time.time() + 5
    job = runner.get_job(job_id)
    assert job is not None
    idx = 0
    while time.time() < deadline:
        batch = job.wait_events(idx, timeout=0.2)
        for ev in batch:
            events_seen.append(str(ev.get("type")))
            idx += 1
            if ev.get("type") == "done":
                assert "tool" in events_seen
                assert "status" in events_seen
                return
    raise AssertionError(f"no done; saw {events_seen}")


def test_on_tool_callback_from_run_streaming_mock(monkeypatch, tmp_path: Path) -> None:
    """Drive tool emit path without real OpenCode binary."""
    from localwiki.runtime import opencode_bridge as ob

    ensure_workspace(tmp_path)
    seen: list[dict] = []

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeOut(
                [
                    json.dumps({"type": "tool_use", "name": "export_docx"}) + "\n",
                    json.dumps({"type": "text", "part": {"text": "hi"}}) + "\n",
                ]
            )
            self.stderr = _FakeErr()
            self.returncode = 0

        def poll(self):
            return 0 if self.stdout.done else None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class _FakeOut:
        def __init__(self, lines: list[str]) -> None:
            self._lines = [ln.encode("utf-8") for ln in lines]
            self._i = 0
            self.done = False

        def readline(self):
            if self._i >= len(self._lines):
                self.done = True
                return b""
            b = self._lines[self._i]
            self._i += 1
            return b

    class _FakeErr:
        def read(self):
            return b""

    monkeypatch.setattr(
        ob.subprocess,
        "Popen",
        lambda *a, **k: FakeProc(),
    )
    prepared = {
        "cmd": ["fake"],
        "env": {},
        "run_dir": str(tmp_path),
        "oc_model": "m",
        "binary": tmp_path / "bin",
    }
    (tmp_path / "bin").write_text("", encoding="utf-8")
    out = ob.run_streaming(
        "hi",
        workspace=tmp_path,
        on_tool=lambda d: seen.append(d),
        _prepared=prepared,
        timeout=5,
    )
    assert out.get("ok") is True
    assert any(x.get("phase") == "start" and x.get("name") == "export_docx" for x in seen)
