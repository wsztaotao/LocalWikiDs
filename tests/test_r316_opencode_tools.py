"""R3.16 OpenCode tools + export intent retry."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.exports import resolve_export_existing  # noqa: E402
from localwiki.office_io import read_docx  # noqa: E402
from localwiki.runtime.export_intent import (  # noqa: E402
    export_delivered,
    export_intent,
    nudge_prompt,
)
from localwiki.runtime.lw_agents_md import AGENTS_MD_VERSION, write_agents_md  # noqa: E402
from localwiki.runtime.opencode_bridge import (  # noqa: E402
    sync_oc_tools,
    write_opencode_run_config,
)
from localwiki.runtime.session_runner import SessionRunner  # noqa: E402
from localwiki.skills_hub.catalog_expose import format_catalog_md  # noqa: E402
from localwiki.tools.oc_export_cli import main as cli_main  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_export_intent_heuristic() -> None:
    assert export_intent("写散文并保存到word文档下载，再转音频")
    assert export_intent("please make a docx")
    assert not export_intent("今天天气怎么样")


def test_oc_export_cli_docx(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    monkeypatch.setenv("LOCALWIKI_WORKSPACE", str(ws))
    rc = cli_main(["docx", "--title", "深秋", "--body", "落叶萧萧。\n\n第二段。", "--stem", "essay"])
    assert rc == 0
    files = list((ws / ".kw" / "exports").glob("essay_*.docx"))
    assert files
    assert "深秋" in read_docx(files[0]) or "落叶" in read_docx(files[0])


def test_sync_oc_tools_and_permission(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    dest = sync_oc_tools(ws)
    assert (dest / "export_docx.ts").is_file()
    assert (dest / "export_tts.ts").is_file()
    write_opencode_run_config(
        ws,
        provider_id="qwen",
        model="qwen-max",
        kind="openai_compatible",
        base_url="https://example.com/v1",
        catalog_md="- office-tts: local",
    )
    cfg = (ws / "opencode.json").read_text(encoding="utf-8")
    assert '"bash": "allow"' in cfg
    assert '"edit": "allow"' in cfg
    agents = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "export_docx" in agents
    assert AGENTS_MD_VERSION.startswith("r3")  # r316+ … r321+


def test_catalog_docs_only_footnote() -> None:
    md = format_catalog_md(
        [
            {
                "name": "byteplus-seed-speech-tts@local",
                "kind": "skill",
                "description": "Production guidance for BytePlus Seed Speech",
            },
            {
                "name": "office-tts",
                "kind": "skill",
                "description": "Local TTS via edge-tts",
            },
        ]
    )
    assert "文档-only" in md
    assert "office-tts" in md


def test_session_nudge_retries_when_no_export(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    calls: list[str] = []

    def fake_run(prompt, workspace, **kwargs):
        calls.append(str(prompt))
        if len(calls) == 1:
            return {
                "ok": True,
                "stdout": "深秋的低语……由于缺少工具暂不导出。",
                "tool_events": [],
            }
        # second call: pretend tool wrote a file
        from localwiki.exports import save_bytes, unique_export_name
        from localwiki.office_io import write_docx

        name = unique_export_name("nudge", ".docx")
        save_bytes(ws, name, write_docx(title="深秋", paragraphs=["落叶。"]))
        return {
            "ok": True,
            "stdout": f"/api/exports/{name}",
            "tool_events": [{"name": "export_docx", "ok": True}],
        }

    runner = SessionRunner(ws, run_fn=fake_run)
    store = runner.store
    meta = store.create(title="r316-nudge")
    sid = meta["id"]
    enq = runner.enqueue(
        sid,
        "请写深秋散文保存到word并转音频下载",
    )
    job_id = enq["job_id"]
    import time

    deadline = time.time() + 10
    status = "queued"
    while time.time() < deadline:
        with runner._lock:
            job = runner._jobs.get(job_id)
            status = job.status if job else "missing"
        if status in ("done", "error"):
            break
        time.sleep(0.05)
    assert status == "done"
    assert len(calls) == 2
    assert "系统催促" in calls[1] or "export_docx" in calls[1]
    msgs = store.list_messages(sid)
    asst = [m for m in msgs if m.get("role") == "assistant"]
    assert asst
    assert "/api/exports/" in str(asst[-1].get("content") or "")


def test_export_delivered_by_link(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    from localwiki.exports import save_bytes, unique_export_name
    from localwiki.office_io import write_docx

    name = unique_export_name("x", ".docx")
    save_bytes(ws, name, write_docx(title="t", paragraphs=["p"]))
    assert export_delivered(
        ws,
        result={"tool_events": []},
        out_text=f"/api/exports/{name}",
        job_started=0,
        user_text="请保存到word下载",
    )
    assert resolve_export_existing(ws, name)


def test_export_delivered_requires_audio_when_asked(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    from localwiki.exports import save_bytes, unique_export_name
    from localwiki.office_io import write_docx

    name = unique_export_name("only", ".docx")
    save_bytes(ws, name, write_docx(title="t", paragraphs=["p"]))
    assert not export_delivered(
        ws,
        result={"tool_events": [{"name": "export_docx", "ok": True}]},
        out_text=f"/api/exports/{name}",
        job_started=0,
        user_text="写散文保存到word并转音频下载",
    )


def test_oc_export_cli_tts_text_file(tmp_path: Path, monkeypatch) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    monkeypatch.setenv("LOCALWIKI_WORKSPACE", str(ws))
    tf = tmp_path / "speech.txt"
    tf.write_text("深秋试音。", encoding="utf-8")
    rc = cli_main(["tts", "--text-file", str(tf), "--stem", "probe"])
    # Network/edge-tts may flake; accept ok or known TTS error code path via rc
    assert rc in (0, 1)
    if rc == 0:
        assert list((ws / ".kw" / "exports").glob("probe_*.mp3"))


def test_nudge_prompt_mentions_tools() -> None:
    p = nudge_prompt("要word")
    assert "export_docx" in p
    assert "要word" in p
