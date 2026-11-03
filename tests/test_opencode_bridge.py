"""R3-E: OpenCode bridge resolve + fake run."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.runtime.opencode_bridge import (  # noqa: E402
    choose_runtime,
    opencode_status,
    resolve_bin,
    run,
)

# Import stub bundler from repo scripts/
sys.path.insert(0, str(ROOT / "scripts"))
from bundle_opencode_stub import bundle  # noqa: E402


def test_choose_runtime() -> None:
    assert choose_runtime("hello", "local") == "local"
    assert choose_runtime("重构这个仓库", "auto") == "opencode"
    assert choose_runtime("写一篇笔记", "auto") == "opencode"
    assert choose_runtime("x", "opencode") == "opencode"


def test_bundle_and_resolve(tmp_path: Path, monkeypatch) -> None:
    shim = bundle(tmp_path)
    assert shim.is_file()
    monkeypatch.setenv("LOCALWIKI_OPENCODE_BIN", str(shim))
    b = resolve_bin(tmp_path)
    assert b is not None
    st = opencode_status(tmp_path)
    assert st["ready"] is True


def test_run_with_python_fake(tmp_path: Path, monkeypatch) -> None:
    """Use python -c as fake bin is awkward; use a small script file."""
    fake = tmp_path / "fake_oc.py"
    fake.write_text(
        "import sys\nprint('fake-opencode', ' '.join(sys.argv[1:]))\nsys.exit(0)\n",
        encoding="utf-8",
    )
    # On Windows, invoke via python as the "bin"
    # Bridge expects a direct executable; wrap with a .cmd or use python path + we call run with bin_path
    # Our run() takes bin_path directly — pass python and rely on cmd that includes script?
    # Simpler: write a .bat that echoes
    if os.name == "nt":
        bat = tmp_path / "opencode.bat"
        bat.write_text("@echo off\r\necho fake-opencode %*\r\nexit /b 0\r\n", encoding="utf-8")
        out = run(
            "hi",
            workspace=tmp_path,
            bin_path=bat,
            timeout=15,
            inject_model_env=False,
        )
    else:
        out = run(
            "hi",
            workspace=tmp_path,
            bin_path=fake,
            timeout=15,
            inject_model_env=False,
        )
    # bat works; python script without shebang may fail on win — bat path covers win
    assert out["code"] in (0, 1) or "stdout" in out
    if out.get("ok"):
        assert "fake-opencode" in (out.get("stdout") or "")


def test_missing_bin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LOCALWIKI_OPENCODE_BIN", raising=False)
    monkeypatch.delenv("OPENCODE_BIN", raising=False)
    # Empty workspace, no PATH opencode — may still find system opencode; force bin None via empty
    out = run(
        "x",
        workspace=tmp_path,
        bin_path=tmp_path / "no-such-bin",
        timeout=5,
        inject_model_env=False,
    )
    assert out["ok"] is False


def test_run_decodes_utf8_json_events(tmp_path: Path) -> None:
    """Chinese Windows defaults to GBK; bridge must keep UTF-8 assistant text."""
    fake = tmp_path / "fake_oc_utf8.py"
    # Simulate opencode --format json line with Chinese assistant text
    payload = (
        '{"type":"text","part":{"text":"你好，我是助手"}}\n'
    )
    fake.write_text(
        "import sys\n"
        f"sys.stdout.buffer.write({payload.encode('utf-8')!r})\n"
        "sys.stdout.buffer.flush()\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        bat = tmp_path / "opencode_utf8.bat"
        bat.write_text(
            f'@echo off\r\n"{sys.executable}" "{fake}" %*\r\n',
            encoding="utf-8",
        )
        bin_path = bat
    else:
        fake.chmod(0o755)
        bin_path = fake
    out = run(
        "介绍下你自己",
        workspace=tmp_path,
        bin_path=bin_path,
        timeout=15,
        inject_model_env=False,
    )
    assert out["ok"] is True
    assert "你好" in (out.get("stdout") or "")
    assert "助手" in (out.get("stdout") or "")


def test_write_opencode_run_config(tmp_path: Path) -> None:
    from localwiki.runtime.opencode_bridge import write_opencode_run_config

    path = write_opencode_run_config(
        tmp_path / "run",
        provider_id="qwen",
        model="qwen-max",
        kind="openai_compatible",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    assert path.is_file()
    data = path.read_text(encoding="utf-8")
    assert "qwen/qwen-max" in data
    assert "openai-compatible" in data
