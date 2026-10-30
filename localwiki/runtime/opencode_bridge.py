"""OpenCode CLI bridge: resolve vendored/path bin and non-interactive run."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


def _repo_root() -> Path:
    # localwiki/runtime/opencode_bridge.py → repo root
    return Path(__file__).resolve().parents[2]


def _kill_proc_tree(proc: Any) -> None:
    """Force-kill process and children (Windows OpenCode spawns a tree)."""
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    try:
        if os.name == "nt" and pid:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except Exception:  # noqa: BLE001
        pass


def resolve_bin(workspace: Path | str | None = None) -> Path | None:
    """Locate OpenCode binary: env → workspace runtime → repo third_party/runtime → PATH."""
    env = os.environ.get("LOCALWIKI_OPENCODE_BIN") or os.environ.get("OPENCODE_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p.resolve()

    candidates: list[Path] = []
    if workspace is not None:
        ws = Path(workspace).resolve()
        candidates.extend(
            [
                ws / "runtime" / "opencode" / "opencode.exe",
                ws / "runtime" / "opencode" / "opencode",
                ws / ".kw" / "runtime" / "opencode" / "opencode.exe",
                ws / ".kw" / "runtime" / "opencode" / "opencode",
            ]
        )
    repo = _repo_root()
    candidates.extend(
        [
            repo / "runtime" / "opencode" / "opencode.exe",
            repo / "runtime" / "opencode" / "opencode",
            repo / "third_party" / "opencode" / "opencode.exe",
            repo / "third_party" / "opencode" / "opencode",
        ]
    )
    for c in candidates:
        if c.is_file():
            return c.resolve()

    which = shutil.which("opencode")
    if which:
        return Path(which).resolve()
    return None


def read_version_file() -> dict[str, str]:
    path = _repo_root() / "third_party" / "opencode.VERSION"
    out: dict[str, str] = {"tag": "", "sha": "", "path": str(path)}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def opencode_status(workspace: Path | str | None = None) -> dict[str, Any]:
    bin_path = resolve_bin(workspace)
    ver = read_version_file()
    return {
        "ready": bin_path is not None,
        "bin": str(bin_path) if bin_path else None,
        "version": ver.get("tag") or ver.get("sha") or None,
        "version_file": ver,
    }


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _openai_compatible_base(kind: str, base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if kind == "ollama":
        if not base:
            base = "http://127.0.0.1:11434"
        if not base.endswith("/v1"):
            base = base + "/v1"
        return base
    return base


def sync_oc_tools(workspace: Path | str) -> Path:
    """Copy LocalWiki OpenCode tool templates into workspace/.opencode/tools (R3.16)."""
    root = Path(workspace).resolve()
    dest = root / ".opencode" / "tools"
    dest.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / "oc_tools_templates"
    if src.is_dir():
        for f in src.glob("*.ts"):
            target = dest / f.name
            target.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _venv_python(repo: Path) -> Path | None:
    for rel in (
        ".venv-work/Scripts/python.exe",
        ".venv-work/bin/python",
        ".venv/Scripts/python.exe",
        ".venv/bin/python",
    ):
        p = repo / rel
        if p.is_file():
            return p.resolve()
    return None


def write_opencode_run_config(
    run_dir: Path,
    *,
    provider_id: str,
    model: str,
    kind: str,
    base_url: str,
    catalog_md: str = "",
    write_ignore_all: bool = False,
) -> Path:
    """Write opencode.json (+ AGENTS.md) for ``run -m provider/model``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    pid = re.sub(r"[^A-Za-z0-9_.\-]+", "_", provider_id or "localwiki") or "localwiki"
    mid = (model or "").strip() or "default"
    api_base = _openai_compatible_base(kind, base_url)
    npm = "@ai-sdk/openai-compatible"
    if kind == "anthropic":
        npm = "@ai-sdk/anthropic"
    options: dict[str, Any] = {}
    if api_base:
        options["baseURL"] = api_base
    # Prefer injected env; ollama accepts any non-empty key
    if kind == "ollama":
        options["apiKey"] = "ollama"
    else:
        options["apiKey"] = "{env:LOCALWIKI_AGENT_API_KEY}"
    cfg: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{pid}/{mid}",
        "small_model": f"{pid}/{mid}",
        "permission": {
            "bash": "allow",
            "edit": "allow",
            "write": "allow",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "skill": "allow",
            "external_directory": "allow",
            "todowrite": "allow",
            "webfetch": "allow",
        },
        "provider": {
            pid: {
                "npm": npm,
                "name": f"LocalWiki ({pid})",
                "options": options,
                "models": {
                    mid: {
                        "name": mid,
                        "limit": {"context": 32768, "output": 8192},
                    }
                },
            }
        },
    }
    path = run_dir / "opencode.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from localwiki.runtime.lw_agents_md import write_agents_md

    write_agents_md(run_dir, catalog_md=catalog_md)
    if write_ignore_all:
        # Legacy empty run-dir mode only
        ignore = run_dir / ".gitignore"
        ignore.write_text(
            "*\n!opencode.json\n!AGENTS.md\n!.gitignore\n",
            encoding="utf-8",
        )
    return path


def _tool_events_from_json_line(line: str) -> list[dict[str, Any]]:
    """Best-effort extract tool call events from one OC --format json line."""
    s = (line or "").strip()
    if not s.startswith("{"):
        return []
    try:
        ev = json.loads(s)
    except json.JSONDecodeError:
        return []
    if not isinstance(ev, dict):
        return []
    typ = str(ev.get("type") or "").lower()
    out: list[dict[str, Any]] = []

    def _ok_from(obj: dict[str, Any]) -> bool:
        if obj.get("error"):
            return False
        st = str(obj.get("status") or "").lower()
        return st not in ("error", "failed", "denied")

    if "tool" in typ or typ in ("tool_use", "tool-call", "tool.call", "step-finish"):
        name: Any = ev.get("name")
        if not name:
            tool_field = ev.get("tool")
            if isinstance(tool_field, str):
                name = tool_field
            elif isinstance(tool_field, dict):
                name = tool_field.get("name")
        part = ev.get("part")
        if not name and isinstance(part, dict):
            name = part.get("tool") or part.get("name")
        if name:
            out.append({"name": str(name), "ok": _ok_from(ev), "type": typ})
    part = ev.get("part")
    if isinstance(part, dict) and str(part.get("type") or "").lower().startswith("tool"):
        n = part.get("tool") or part.get("name")
        if n:
            out.append({"name": str(n), "ok": _ok_from(part), "type": typ})
    return out


def _coerce_text_blob(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        for key in ("text", "content", "delta", "value", "message"):
            v = part.get(key)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, dict):
                nested = _coerce_text_blob(v)
                if nested:
                    return nested
    return ""


def _text_from_event_dict(ev: dict[str, Any]) -> str:
    typ = str(ev.get("type") or "").lower()
    if typ in (
        "text",
        "message",
        "assistant",
        "content",
        "delta",
        "token",
        "part",
        "message.part",
        "message.delta",
        "text-delta",
        "text_delta",
    ):
        for key in ("part", "text", "content", "delta", "token", "message"):
            blob = _coerce_text_blob(ev.get(key))
            if blob:
                return blob
    if typ == "error":
        err = ev.get("error") or {}
        if isinstance(err, dict):
            msg = (err.get("data") or {}).get("message") if isinstance(err.get("data"), dict) else None
            msg = msg or err.get("message") or err.get("name")
            if msg:
                return f"[opencode error] {msg}"
    # some streams nest under properties.part
    props = ev.get("properties") if isinstance(ev.get("properties"), dict) else None
    if props:
        for key in ("text", "part", "delta", "content"):
            blob = _coerce_text_blob(props.get(key))
            if blob:
                return blob
    # Fallback: any top-level text-ish field
    for key in ("text", "delta", "content"):
        blob = _coerce_text_blob(ev.get(key))
        if blob and typ not in ("error", "tool", "step", "step-start", "step-finish"):
            return blob
    return ""


def _delta_from_json_line(line: str) -> str:
    line = (line or "").strip()
    if not line.startswith("{"):
        return ""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if not isinstance(ev, dict):
        return ""
    return _text_from_event_dict(ev)


def _extract_text_from_json_events(stdout: str) -> str:
    """Best-effort extract assistant text from ``opencode run --format json`` lines."""
    texts: list[str] = []
    for line in (stdout or "").splitlines():
        part = _delta_from_json_line(line)
        if part:
            texts.append(part)
    return "\n".join(texts).strip()


def _summarize_failure(stdout: str, stderr: str) -> str:
    err = _strip_ansi(stderr or "")
    out = _strip_ansi(stdout or "")
    # Prefer last Error: line
    for blob in (err, out):
        for line in reversed(blob.splitlines()):
            s = line.strip()
            if not s:
                continue
            if s.lower().startswith("error:") or "exceed_context" in s or "Unsupported model" in s:
                return s[:800]
            if "AI_APICallError" in s or "ProviderModelNotFound" in s:
                return s[:800]
    # help text when wrong flags were used
    if "opencode run [message" in err and "--prompt" in " ".join([out, err]):
        return "OpenCode CLI rejected --prompt; LocalWiki should pass the message as a positional argument"
    combined = (err or out).strip()
    return (combined[:800] if combined else "opencode run failed (no stderr)")


def _prepare_opencode_run(
    prompt: str,
    *,
    workspace: Path | str,
    skills_path: Path | str | None = None,
    bin_path: Path | str | None = None,
    inject_model_env: bool = True,
    session_id: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (error_result, None) or (None, prepared)."""
    root = Path(workspace).resolve()
    binary = Path(bin_path) if bin_path else resolve_bin(root)
    if binary is None:
        return (
            {
                "ok": False,
                "error": {
                    "code": "E_DEP_MISSING",
                    "message": (
                        "opencode binary not found; run scripts/build_opencode.ps1 "
                        "or set LOCALWIKI_OPENCODE_BIN"
                    ),
                },
                "stdout": "",
                "stderr": "",
                "code": 127,
            },
            None,
        )

    skills = Path(skills_path) if skills_path else root / ".kw" / "skills"
    env = os.environ.copy()
    env["LOCALWIKI_WORKSPACE"] = str(root)
    # R3.26: always expose numeric LocalWiki HTTP port for openable URLs
    if not (env.get("LOCALWIKI_PORT") or "").strip():
        try:
            from localwiki.workspace import ensure_workspace, load_config

            paths = ensure_workspace(root)
            cfg = load_config(paths["config"])
            env["LOCALWIKI_PORT"] = str(int(cfg.get("port") or 8765))
        except Exception:  # noqa: BLE001
            env["LOCALWIKI_PORT"] = "8765"
    if skills.is_dir():
        env["OPENCODE_SKILLS_PATH"] = str(skills)

    repo = _repo_root()
    env["PYTHONPATH"] = (
        str(repo)
        + os.pathsep
        + (env.get("PYTHONPATH") or "")
    ).rstrip(os.pathsep)
    vpy = _venv_python(repo)
    if vpy is not None:
        env["LOCALWIKI_PYTHON"] = str(vpy)
        scripts = str(vpy.parent)
        env["PATH"] = scripts + os.pathsep + (env.get("PATH") or "")

    model_ref = ""
    provider_name = ""
    kind = "openai_compatible"
    base_url = ""
    if inject_model_env:
        try:
            from localwiki.agent.routed_model import agent_route_env_injection, build_routed_model

            model = build_routed_model(root, core_route="agent")
            injected = agent_route_env_injection(root)
            env.update(injected)
            if model.api_key and not env.get("LOCALWIKI_AGENT_API_KEY"):
                env["LOCALWIKI_AGENT_API_KEY"] = model.api_key
            if not env.get("LOCALWIKI_AGENT_API_KEY"):
                env["LOCALWIKI_AGENT_API_KEY"] = "ollama"
            env["_LOCALWIKI_INJECTED_MODEL_KEYS"] = ",".join(
                k
                for k in injected
                if k not in ("LOCALWIKI_AGENT_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
            )
            model_ref = model.model
            provider_name = model.provider_name or "localwiki"
            kind = model.kind
            base_url = model.base_url
        except Exception as exc:  # noqa: BLE001
            return (
                {
                    "ok": False,
                    "error": {
                        "code": getattr(exc, "code", "E_MODEL_UNAVAILABLE"),
                        "message": f"cannot inject agent model into OpenCode: {exc}",
                    },
                    "stdout": "",
                    "stderr": "",
                    "code": 2,
                },
                None,
            )

    # R3.16: run against real workspace so bash/edit/tools see .kw/exports + skills
    run_dir = root
    try:
        sync_oc_tools(root)
    except Exception:  # noqa: BLE001
        pass

    catalog_md = ""
    try:
        from localwiki.skills_hub.catalog_expose import (
            expose_harness_into_skills,
            format_catalog_md,
            list_enabled_skill_catalog,
        )

        expose_harness_into_skills(root)
        catalog_md = format_catalog_md(list_enabled_skill_catalog(root))
    except Exception:  # noqa: BLE001
        catalog_md = ""

    oc_model = ""
    if model_ref and provider_name:
        write_opencode_run_config(
            run_dir,
            provider_id=provider_name,
            model=model_ref,
            kind=kind,
            base_url=base_url,
            catalog_md=catalog_md,
            write_ignore_all=False,
        )
        pid = re.sub(r"[^A-Za-z0-9_.\-]+", "_", provider_name) or "localwiki"
        oc_model = f"{pid}/{model_ref}"
    else:
        from localwiki.runtime.lw_agents_md import write_agents_md

        write_agents_md(run_dir, catalog_md=catalog_md)

    # Mirror config under .kw/opencode-run for debugging / older UI paths
    try:
        from localwiki.runtime.lw_agents_md import write_agents_md as _write_agents

        mirror = root / ".kw" / "opencode-run"
        mirror.mkdir(parents=True, exist_ok=True)
        _write_agents(mirror, catalog_md=catalog_md)
        src_cfg = run_dir / "opencode.json"
        if src_cfg.is_file():
            (mirror / "opencode.json").write_text(
                src_cfg.read_text(encoding="utf-8"), encoding="utf-8"
            )
    except Exception:  # noqa: BLE001
        pass

    text = (prompt or "").strip()
    if len(text) > 12000:
        text = text[-12000:]

    cmd: list[str] = [str(binary), "run", "--auto", "--format", "json"]
    if oc_model:
        cmd.extend(["-m", oc_model])
    if session_id:
        cmd.extend(["-s", session_id])
    cmd.extend(["--dir", str(run_dir), text])

    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return None, {
        "cmd": cmd,
        "env": env,
        "run_dir": run_dir,
        "oc_model": oc_model,
        "binary": binary,
    }


def run(
    prompt: str,
    *,
    workspace: Path | str,
    skills_path: Path | str | None = None,
    timeout: float = 180.0,
    bin_path: Path | str | None = None,
    inject_model_env: bool = True,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Non-interactive ``opencode run <message>`` (Fake-friendly via LOCALWIKI_OPENCODE_BIN)."""
    return run_streaming(
        prompt,
        workspace=workspace,
        skills_path=skills_path,
        timeout=timeout,
        bin_path=bin_path,
        inject_model_env=inject_model_env,
        session_id=session_id,
        on_delta=None,
    )


def run_streaming(
    prompt: str,
    *,
    workspace: Path | str,
    skills_path: Path | str | None = None,
    timeout: float | None = None,
    bin_path: Path | str | None = None,
    inject_model_env: bool = True,
    session_id: str | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_tool: Callable[[dict[str, Any]], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
    hung_idle_sec: float = 1800.0,
    now_fn: Callable[[], float] | None = None,
    _prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stream ``opencode run --format json``; ``timeout=None`` means no wall-clock kill (R3.25)."""

    from localwiki.runtime.oc_events import OcRunState, apply_parsed, parse_ndjson_line

    def _decode_pipe(raw: bytes | str | None) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        return raw.decode("utf-8", errors="replace")

    _now = now_fn or time.time

    if _prepared is None:
        err, prepared = _prepare_opencode_run(
            prompt,
            workspace=workspace,
            skills_path=skills_path,
            bin_path=bin_path,
            inject_model_env=inject_model_env,
            session_id=session_id,
        )
        if err is not None:
            return err
        assert prepared is not None
        _prepared = prepared

    cmd = list(_prepared["cmd"])
    env = dict(_prepared["env"])
    run_dir = Path(_prepared["run_dir"])
    oc_model = _prepared.get("oc_model") or None
    binary = Path(_prepared["binary"])

    stdout_chunks: list[str] = []
    text_parts: list[str] = []
    tool_events: list[dict[str, Any]] = []
    cancel_reason: str | None = None
    tool_started: set[str] = set()
    state = OcRunState(last_ndjson_at=_now())
    hung = False
    cancelled = False
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(run_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        return {
            "ok": False,
            "error": {"code": "E_FAILED", "message": str(exc)},
            "stdout": "",
            "stderr": str(exc),
            "code": 1,
        }

    assert proc.stdout is not None
    assert proc.stderr is not None
    deadline: float | None = None
    if timeout is not None and float(timeout) > 0:
        deadline = _now() + float(timeout)
    err_thread_buf: list[bytes] = []

    def _read_stderr() -> None:
        try:
            err_thread_buf.append(proc.stderr.read() or b"")
        except Exception:  # noqa: BLE001
            pass

    def _emit_tool_cb(info: dict[str, Any]) -> None:
        name = str(info.get("name") or "").strip()
        if not name:
            return
        phase = str(info.get("phase") or "start")
        raw = str(info.get("raw_type") or info.get("status") or "")
        if phase == "start" and name not in tool_started:
            tool_started.add(name)
            if on_tool:
                on_tool(
                    {
                        "name": name,
                        "ok": None,
                        "phase": "start",
                        "raw_type": raw,
                        "status": info.get("status"),
                        "title": info.get("title"),
                    }
                )
        elif phase == "start" and name in tool_started:
            # running update — still notify UI
            if on_tool:
                on_tool(
                    {
                        "name": name,
                        "ok": None,
                        "phase": "start",
                        "raw_type": raw,
                        "status": info.get("status"),
                        "title": info.get("title"),
                    }
                )
        if phase == "end" and on_tool:
            tool_started.discard(name)
            on_tool(
                {
                    "name": name,
                    "ok": info.get("ok") if info.get("ok") is not None else True,
                    "phase": "end",
                    "raw_type": raw,
                    "status": info.get("status"),
                    "title": info.get("title"),
                }
            )

    def _emit_oc_status(force: bool = False) -> None:
        if not on_event:
            return
        payload = state.oc_status_payload()
        key = (
            payload.get("busy"),
            payload.get("session_status"),
            payload.get("step"),
            (payload.get("tool") or {}).get("name")
            if isinstance(payload.get("tool"), dict)
            else None,
            (payload.get("tool") or {}).get("phase")
            if isinstance(payload.get("tool"), dict)
            else None,
        )
        prev = getattr(state, "_last_status_key", None)
        if not force and key == prev:
            return
        state._last_status_key = key  # type: ignore[attr-defined]
        on_event({"type": "oc_status", **payload})

    def _maybe_deny_demo_bash(te: dict[str, Any]) -> None:
        """R3.27: cancel run when bash would delete/clear ``.kw/demo``."""
        nonlocal cancel_reason, cancelled
        if cancel_reason == "demo_bash_denied" or cancelled:
            return
        phase = str(te.get("phase") or "start")
        if phase == "end":
            return
        name = str(te.get("name") or "")
        from localwiki.runtime.demo_guard import (
            extract_bash_command,
            is_dangerous_demo_bash,
            tool_looks_like_bash,
        )

        if not tool_looks_like_bash(name):
            return
        cmd = extract_bash_command(te)
        if not cmd or not is_dangerous_demo_bash(cmd):
            return
        cancel_reason = "demo_bash_denied"
        if cancel_event is not None:
            setattr(cancel_event, "_lw_cancel_reason", cancel_reason)
            cancel_event.set()
        cancelled = True
        if on_event:
            on_event(
                {
                    "type": "status",
                    "text": "已拦截删除/清空 .kw/demo 的危险命令",
                    "cancel_reason": cancel_reason,
                }
            )

    def _handle_line(line: str) -> None:
        parsed = parse_ndjson_line(line)
        if parsed is not None:
            apply_parsed(state, parsed, now=_now())
            kind = parsed.get("kind")
            if kind == "tool":
                te = {
                    "name": parsed.get("name"),
                    "ok": parsed.get("ok"),
                    "type": parsed.get("raw_type"),
                    "phase": parsed.get("phase"),
                    "status": parsed.get("status"),
                    "title": parsed.get("title"),
                }
                tool_events.append(te)
                _maybe_deny_demo_bash(te)
                _emit_tool_cb(
                    {
                        "name": te["name"],
                        "phase": te.get("phase") or "start",
                        "ok": te.get("ok"),
                        "raw_type": te.get("type"),
                        "status": te.get("status"),
                        "title": te.get("title"),
                    }
                )
                if on_event:
                    on_event(dict(parsed))
                _emit_oc_status()
            elif kind in ("session_status", "step", "error"):
                if on_event:
                    on_event(dict(parsed))
                _emit_oc_status()
            elif kind == "meta" and parsed.get("session_id"):
                if on_event:
                    on_event({"type": "session_id", "session_id": parsed["session_id"]})
                # session id alone — no status spam
            # kind other/text: refresh timestamp only via apply_parsed; no oc_status spam
        # Legacy tool parse fallback when structured parse missed name-only lines
        if parsed is None or parsed.get("kind") == "other":
            for te in _tool_events_from_json_line(line):
                tool_events.append(te)
                name = str(te.get("name") or "")
                raw = str(te.get("type") or "")
                raw_l = raw.lower()
                is_end = (
                    te.get("ok") is False
                    or "finish" in raw_l
                    or "result" in raw_l
                    or raw_l.endswith(".end")
                )
                leg_phase = "end" if is_end and name in tool_started else "start"
                leg = {
                    "name": name,
                    "phase": leg_phase,
                    "ok": te.get("ok") if is_end else None,
                    "raw_type": raw,
                    "title": te.get("title"),
                }
                _maybe_deny_demo_bash(leg)
                _emit_tool_cb(leg)
                if name:
                    if is_end:
                        state.tool_end(name)
                    else:
                        state.tool_start(name)
                state.note_ndjson(_now())
        delta = _delta_from_json_line(line)
        if delta:
            joined = "".join(text_parts)
            if joined and delta.startswith(joined):
                extra = delta[len(joined) :]
                if extra:
                    text_parts.append(extra)
                    if on_delta:
                        on_delta(extra)
            elif joined and joined.endswith(delta):
                pass
            else:
                text_parts.append(delta)
                if on_delta:
                    on_delta(delta)

    et = threading.Thread(target=_read_stderr, daemon=True)
    et.start()

    # R3.26: stdout via queue so cancel/hung checks are not stuck on Windows readline
    line_q: queue.Queue[bytes | None] = queue.Queue()

    def _read_stdout() -> None:
        try:
            assert proc.stdout is not None
            while True:
                line_b = proc.stdout.readline()
                if not line_b:
                    break
                line_q.put(line_b)
        except Exception:  # noqa: BLE001
            pass
        finally:
            line_q.put(None)

    ot = threading.Thread(target=_read_stdout, daemon=True)
    ot.start()

    def _do_cancel_kill() -> None:
        nonlocal cancelled
        cancelled = True
        _kill_proc_tree(proc)
        # Unblock stdout reader if still stuck on readline (Windows)
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if proc.stderr is not None:
                proc.stderr.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            line_q.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass

    # Let SessionRunner.cancel_job kill even if this thread is wedged
    if cancel_event is not None:
        setattr(cancel_event, "_lw_kill", _do_cancel_kill)

    def _cancel_watch() -> None:
        if cancel_event is None:
            return
        while True:
            if cancel_event.wait(0.2):
                _do_cancel_kill()
                return
            if proc.poll() is not None:
                return

    cw = threading.Thread(target=_cancel_watch, daemon=True)
    cw.start()
    try:
        while True:
            now = _now()
            if cancelled or (cancel_event is not None and cancel_event.is_set()):
                _do_cancel_kill()
                break
            if deadline is not None and now > deadline:
                _kill_proc_tree(proc)
                raise subprocess.TimeoutExpired(cmd, timeout)
            if state.should_hung_kill(now, idle_sec=hung_idle_sec):
                hung = True
                _kill_proc_tree(proc)
                break
            try:
                line_b = line_q.get(timeout=0.2)
            except queue.Empty:
                if cancelled:
                    break
                if proc.poll() is not None and line_q.empty():
                    try:
                        line_b = line_q.get(timeout=0.3)
                    except queue.Empty:
                        break
                    if line_b is None:
                        break
                    # fall through to handle leftover line
                else:
                    continue
            else:
                if line_b is None:
                    if cancelled or proc.poll() is not None:
                        break
                    continue
            if line_b is None:
                break
            line = line_b.decode("utf-8", errors="replace")
            stdout_chunks.append(line)
            _handle_line(line)
        if not hung and not cancelled:
            proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_proc_tree(proc)
        stdout = "".join(stdout_chunks)
        et.join(timeout=1)
        ot.join(timeout=1)
        stderr = _decode_pipe(err_thread_buf[0] if err_thread_buf else b"")
        extracted = "".join(text_parts).strip() or _extract_text_from_json_events(stdout)
        return {
            "ok": False,
            "error": {
                "code": "E_TIMEOUT",
                "message": f"opencode timed out after {timeout}s",
            },
            "stdout": extracted,
            "stderr": stderr,
            "code": 124,
            "model": oc_model,
            "run_dir": str(run_dir),
            "tool_events": tool_events,
            "oc_session_id": state.oc_session_id,
        }
    finally:
        if cancel_event is not None:
            try:
                delattr(cancel_event, "_lw_kill")
            except Exception:  # noqa: BLE001
                pass

    et.join(timeout=1)
    ot.join(timeout=1)
    stdout = "".join(stdout_chunks)
    stderr = _decode_pipe(err_thread_buf[0] if err_thread_buf else b"")
    extracted = "".join(text_parts).strip() or _extract_text_from_json_events(stdout)
    if cancelled:
        reason = cancel_reason
        if not reason and cancel_event is not None:
            cr = getattr(cancel_event, "_lw_cancel_reason", None)
            if isinstance(cr, str) and cr.strip():
                reason = cr.strip()
        reason = reason or "user"
        msg_map = {
            "demo_bash_denied": "Blocked destructive bash targeting .kw/demo",
            "demo_spin": "Stopped: OpenCode demo spin (no delivery progress)",
            "user": "OpenCode run cancelled by user",
        }
        return {
            "ok": False,
            "error": {
                "code": "E_CANCELLED",
                "message": msg_map.get(reason, msg_map["user"]),
            },
            "stdout": extracted or _strip_ansi(stdout),
            "stderr": stderr,
            "code": 130,
            "model": oc_model,
            "run_dir": str(run_dir),
            "tool_events": tool_events,
            "oc_session_id": state.oc_session_id,
            "cancelled": True,
            "cancel_reason": reason,
        }
    if hung:
        return {
            "ok": False,
            "error": {
                "code": "E_OC_HUNG",
                "message": f"OpenCode silent ≥{int(hung_idle_sec)}s and not busy",
            },
            "stdout": extracted or _strip_ansi(stdout),
            "stderr": stderr,
            "code": 124,
            "model": oc_model,
            "run_dir": str(run_dir),
            "tool_events": tool_events,
            "oc_session_id": state.oc_session_id,
        }
    ok = proc.returncode == 0
    result: dict[str, Any] = {
        "ok": ok,
        "stdout": extracted or _strip_ansi(stdout),
        "stderr": stderr,
        "code": proc.returncode if proc.returncode is not None else 1,
        "bin": str(binary),
        "cmd": cmd[:6] + ["…"],
        "model": oc_model,
        "run_dir": str(run_dir),
        "tool_events": tool_events,
        "oc_session_id": state.oc_session_id,
    }
    if not ok:
        result["error"] = {
            "code": "E_OPENCODE",
            "message": _summarize_failure(stdout, stderr),
        }
        if not result["stdout"]:
            result["stdout"] = result["error"]["message"]
    return result


def choose_runtime(goal: str, requested: str = "auto") -> str:
    """Select runtime. R3.5: ``auto`` ≡ ``opencode`` (U5); local only when explicit."""
    del goal  # retained for API compatibility
    req = (requested or "auto").lower().strip()
    if req == "local":
        return "local"
    if req in ("opencode", "auto", ""):
        return "opencode"
    return "opencode"


def check_opencode_update() -> dict[str, Any]:
    """Compare pinned VERSION tag to GitHub latest (best-effort; may skip offline)."""
    import json as _json
    import urllib.request

    ver = read_version_file()
    pinned = ver.get("tag") or ""
    url = "https://api.github.com/repos/anomalyco/opencode/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LocalWiki"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("tag_name") or "")
        return {
            "ok": True,
            "pinned": pinned,
            "latest": latest,
            "update_available": bool(latest and pinned and latest != pinned),
            "hint": "Run scripts/update_opencode.ps1 -Tag <tag> to pin explicitly",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "pinned": pinned,
            "latest": None,
            "error": str(exc),
            "hint": "Run scripts/update_opencode.ps1 -Tag <tag> to pin explicitly",
        }
