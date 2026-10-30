"""Clear workspace locks if stale; optionally take over a live LocalWiki hold.

Handles both:
- ``.kw/lock.json`` (legacy / commit path)
- ``.kw/runtime/workspace.lock`` (serve single-instance)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cmdline(pid: int) -> str:
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
        except OSError:
            return ""
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        return (out or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _looks_like_localwiki(cmdline: str, workspace: Path) -> bool:
    cl = cmdline.lower().replace("\\", "/")
    if "localwiki" not in cl:
        return False
    ws = str(workspace.resolve()).lower().replace("\\", "/")
    if "--workspace" in cl:
        return ws in cl or workspace.name.lower() in cl
    return True


def _kill(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    try:
        os.kill(pid, 9)
        return True
    except OSError:
        return False


def _read_pid(lock: Path) -> int:
    if not lock.is_file():
        return 0
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        return int(data.get("pid") or 0)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0


def _clear_one(lock: Path, workspace: Path, *, takeover: bool) -> int:
    """Return 0 ok, 1 hard failure, 2 live foreign holder (non-fatal for startup)."""
    if not lock.is_file():
        return 0
    pid = _read_pid(lock)
    label = lock.as_posix()

    if pid > 0 and _pid_alive(pid):
        cmd = _cmdline(pid)
        if takeover and _looks_like_localwiki(cmd, workspace):
            print(f"[LocalWiki] taking over {label} from localwiki pid={pid}")
            if not _kill(pid):
                print(f"[LocalWiki] failed to stop pid={pid}", file=sys.stderr)
                return 1
            time.sleep(0.5)
            lock.unlink(missing_ok=True)
            print(f"[LocalWiki] previous instance stopped; cleared {label}")
            return 0
        print(f"[LocalWiki] lock held by live pid={pid} ({label})")
        if cmd:
            print(f"[LocalWiki] cmdline: {cmd[:240]}")
        if not takeover:
            print(
                "[LocalWiki] Close that window, or re-run start-localwiki.cmd "
                "(passes --takeover).",
                file=sys.stderr,
            )
        return 2

    lock.unlink(missing_ok=True)
    print(f"[LocalWiki] cleared stale lock {label}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace", type=Path)
    ap.add_argument(
        "--takeover",
        action="store_true",
        help="If lock held by a live LocalWiki for this workspace, kill it and clear lock",
    )
    args = ap.parse_args()
    root = Path(args.workspace).resolve()
    locks = [
        root / ".kw" / "lock.json",
        root / ".kw" / "runtime" / "workspace.lock",
    ]
    if not any(p.is_file() for p in locks):
        print("[LocalWiki] no lock file")
        return 0

    for lock in locks:
        code = _clear_one(lock, root, takeover=bool(args.takeover))
        if code == 1:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
