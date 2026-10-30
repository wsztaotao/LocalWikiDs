"""LocalWiki CLI entry (design §15.6). LW-37: ``agent exec``; R1: serve/doctor/init.

Usage:
  python -m localwiki --workspace <path> agent exec "<goal>" [--mode MODE] --json
  python -m localwiki --workspace <path> serve [--port N]
  python -m localwiki --workspace <path> doctor
  python -m localwiki --workspace <path> init
  python -m localwiki --workspace <path> nightly
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_APPROVAL_REQUIRED = 10  # E_APPROVAL_REQUIRED

E_APPROVAL_REQUIRED = "E_APPROVAL_REQUIRED"


def _json_out(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if payload.get("ok"):
            print(payload.get("data", {}).get("content") or payload.get("message") or "ok")
        else:
            err = payload.get("error") or {}
            print(f"{err.get('code')}: {err.get('message')}", file=sys.stderr)


def run_agent_exec(
    goal: str,
    *,
    workspace: Path | str,
    mode: str = "workspace-auto",
    protocol: str = "openai",
    model: Any | None = None,
    registry: Any | None = None,
    as_json: bool = True,
) -> tuple[dict[str, Any], int]:
    """Execute one non-interactive agent turn loop. Returns (payload, exit_code)."""
    from localwiki.agent.loop import AgentLoop, LoopConfig
    from localwiki.agent.policy import PolicyEngine
    from localwiki.evolution.hooks import EvolutionHooks, LoopHookAdapter
    from localwiki.experience.pack import TaskSpec
    from localwiki.tools.registry import build_default_registry

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    reg = registry or build_default_registry(workspace=ws)
    if model is None:
        from localwiki.agent.routed_model import ModelNotConfiguredError, build_routed_model

        try:
            model = build_routed_model(ws, core_route="agent")
        except ModelNotConfiguredError as exc:
            return {
                "ok": False,
                "request_id": request_id,
                "data": None,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "recoverable": True,
                    "actions": [{"label": "Open Settings", "href": "#settings"}],
                },
            }, EXIT_ERROR

    policy = PolicyEngine(workspace_roots=[str(ws.resolve())])
    evo = EvolutionHooks(ws)
    hooks = LoopHookAdapter(evo, task_uid=request_id)

    task = TaskSpec(
        task_uid=request_id,
        goal=goal,
        task_type="cli_exec",
        approval_mode=mode,
        tool_scope=["*"],
    )

    loop = AgentLoop(
        reg,
        model,
        protocol=protocol,
        policy=policy,
        hooks=hooks,
        config=LoopConfig(approval_mode=mode, max_steps=60),
        approver=None,
    )
    result = loop.run(task)
    data = result.to_dict()

    if result.status == "approval_required":
        payload = {
            "ok": False,
            "request_id": request_id,
            "data": data,
            "error": {
                "code": E_APPROVAL_REQUIRED,
                "message": (
                    result.approval_pending.reason
                    if result.approval_pending
                    else "approval required"
                ),
                "recoverable": True,
                "approval_id": (
                    result.approval_pending.request_id
                    if result.approval_pending
                    else None
                ),
                "actions": [{"label": "Approve in desktop session", "href": "#/agents/approvals"}],
            },
        }
        return payload, EXIT_APPROVAL_REQUIRED

    if result.status == "completed" and result.ok:
        payload = {
            "ok": True,
            "request_id": request_id,
            "data": data,
            "error": None,
        }
        return payload, EXIT_OK

    code = result.error or f"E_AGENT_{result.status.upper()}"
    payload = {
        "ok": False,
        "request_id": request_id,
        "data": data,
        "error": {
            "code": code if str(code).startswith("E_") else f"E_AGENT_{result.status.upper()}",
            "message": result.content or result.status,
            "recoverable": result.status in ("stopped", "cancelled"),
        },
    }
    return payload, EXIT_ERROR


class _DefaultExecModel:
    """Minimal offline model: finish immediately with the goal echoed."""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        self._done = False

    def generate(self, messages, *, tools, extras):
        if self._done:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Completed (offline stub): {self.goal}",
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        self._done = True
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"Completed (offline stub): {self.goal}",
                    },
                    "finish_reason": "stop",
                }
            ]
        }


def cmd_init(workspace: Path | str, *, as_json: bool) -> int:
    from localwiki.workspace import ensure_workspace, load_config

    paths = ensure_workspace(workspace)
    cfg = load_config(paths["config"])
    payload = {
        "ok": True,
        "data": {
            "workspace": str(paths["root"]),
            "workspace_uid": cfg.get("workspace_uid"),
            "port": cfg.get("port", 8765),
            "token_saved": bool(cfg.get("api_token")),
        },
    }
    _json_out(payload, as_json=True)
    return EXIT_OK


def cmd_doctor(workspace: Path | str, *, as_json: bool) -> int:
    from localwiki.diagnostics import doctor

    report = doctor(workspace)
    _json_out({"ok": report["ok"], "data": report}, as_json=True)
    return EXIT_OK if report["ok"] else EXIT_ERROR


def cmd_serve(workspace: Path | str, *, port: int | None, as_json: bool) -> int:
    from localwiki.server import run_server

    # Optional nightly scheduler (03:00); missing apscheduler → skip quietly
    try:
        from localwiki.runtime.nightly import schedule_nightly

        schedule_nightly(workspace, hour=3, minute=0)
    except Exception:  # noqa: BLE001
        pass

    try:
        run_server(workspace, port=port)
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        _json_out(
            {"ok": False, "error": {"code": "E_FAILED", "message": str(exc)}},
            as_json=True,
        )
        return EXIT_ERROR
    return EXIT_OK


def cmd_nightly(workspace: Path | str, *, as_json: bool) -> int:
    from localwiki.runtime.nightly import run_nightly

    report = run_nightly(workspace)
    _json_out({"ok": bool(report.get("ok")), "data": report}, as_json=True)
    return EXIT_OK if report.get("ok") else EXIT_ERROR


def cmd_backup(workspace: Path | str, dest: str, *, as_json: bool) -> int:
    from localwiki.recovery import RecoveryError, backup_workspace

    try:
        out = backup_workspace(workspace, dest, as_zip=True)
        _json_out({"ok": True, "data": out}, as_json=True)
        return EXIT_OK
    except RecoveryError as exc:
        _json_out({"ok": False, "error": {"code": exc.code, "message": exc.message}}, as_json=True)
        return EXIT_ERROR


def cmd_verify_backup(path: str, *, as_json: bool) -> int:
    from localwiki.recovery import verify_backup

    out = verify_backup(path)
    _json_out({"ok": bool(out.get("ok")), "data": out}, as_json=True)
    return EXIT_OK if out.get("ok") else EXIT_ERROR


def cmd_restore_backup(backup: str, dest: str, *, as_json: bool) -> int:
    from localwiki.recovery import RecoveryError, restore_backup

    try:
        out = restore_backup(backup, dest)
        _json_out({"ok": True, "data": out}, as_json=True)
        return EXIT_OK
    except RecoveryError as exc:
        _json_out({"ok": False, "error": {"code": exc.code, "message": exc.message}}, as_json=True)
        return EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="localwiki", description="LocalWiki Personal Core CLI")
    p.add_argument("--workspace", default=".", help="Workspace root")
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="command")

    agent = sub.add_parser("agent", help="Agent commands")
    agent_sub = agent.add_subparsers(dest="agent_cmd")
    ex = agent_sub.add_parser("exec", help="Non-interactive agent execution")
    ex.add_argument("goal", help="Task goal")
    ex.add_argument(
        "--mode",
        default="workspace-auto",
        choices=["read-only", "workspace-auto", "full-access"],
    )
    ex.add_argument(
        "--protocol",
        default="openai",
        choices=["openai", "anthropic", "ollama", "json_fallback"],
    )

    serve = sub.add_parser("serve", help="Start local HTTP server")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default from config)")

    sub.add_parser("doctor", help="Diagnose workspace health")
    sub.add_parser("init", help="Initialize empty workspace")
    sub.add_parser("nightly", help="Run nightly personal + evolution + FTS pipeline")

    bk = sub.add_parser("backup", help="Backup workspace (no secrets)")
    bk.add_argument("dest", help="Destination zip path or directory outside workspace")

    vb = sub.add_parser("verify-backup", help="Verify backup archive")
    vb.add_argument("path", help="Backup zip or directory")

    rb = sub.add_parser("restore-backup", help="Restore backup to a new empty directory")
    rb.add_argument("backup", help="Backup zip or directory")
    rb.add_argument("dest", help="New workspace path (must not exist)")

    # Placeholders so other §15.6 verbs parse without crashing
    for name in (
        "mcp",
        "ingest",
        "query",
        "lint",
        "style",
        "memory",
        "skills",
        "models",
    ):
        sub.add_parser(name, help=f"{name} (stub — later / optional)")

    return p


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)

    as_json = bool(getattr(args, "json", False) or "--json" in argv)

    if args.command == "agent" and getattr(args, "agent_cmd", None) == "exec":
        from localwiki.personal_core import build_agent_context

        ctx = build_agent_context(args.workspace, surface="agent")
        goal = args.goal
        if ctx.get("text"):
            goal = f"{goal}\n\n---\nPersonal context:\n{ctx['text']}"
        payload, code = run_agent_exec(
            goal,
            workspace=args.workspace,
            mode=args.mode,
            protocol=args.protocol,
            as_json=as_json,
        )
        payload["personal_context_meta"] = ctx.get("meta")
        _json_out(payload, as_json=True)
        return code

    if args.command == "init":
        return cmd_init(args.workspace, as_json=as_json)

    if args.command == "doctor":
        return cmd_doctor(args.workspace, as_json=as_json)

    if args.command == "serve":
        return cmd_serve(
            args.workspace,
            port=getattr(args, "port", None),
            as_json=as_json,
        )

    if args.command == "nightly":
        return cmd_nightly(args.workspace, as_json=as_json)

    if args.command == "backup":
        return cmd_backup(args.workspace, args.dest, as_json=as_json)

    if args.command == "verify-backup":
        return cmd_verify_backup(args.path, as_json=as_json)

    if args.command == "restore-backup":
        return cmd_restore_backup(args.backup, args.dest, as_json=as_json)

    if args.command in (
        "mcp",
        "ingest",
        "query",
        "lint",
        "style",
        "memory",
        "skills",
        "models",
        None,
    ):
        payload = {
            "ok": False,
            "error": {
                "code": "E_DEP_MISSING",
                "message": f"command '{args.command}' not implemented in this scaffold yet",
            },
        }
        if as_json or args.command is None:
            _json_out(payload, as_json=True)
        else:
            print(payload["error"]["message"], file=sys.stderr)
        return EXIT_ERROR

    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
