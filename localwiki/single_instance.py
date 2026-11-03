"""Workspace write lock / single-instance (R1 spec §2)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Process-local reentrancy for the same lock path (serve holds; accept nests).
_HOLD_DEPTH: dict[str, int] = {}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class WorkspaceLock:
    """File-based exclusive lock with PID so stale locks can be broken."""

    def __init__(self, lock_path: Path | str) -> None:
        self.path = Path(lock_path).resolve()
        self._key = str(self.path)

    def info(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"raw": True, "alive": True}
        pid = int(data.get("pid") or 0)
        return {
            "pid": pid,
            "held_at": data.get("held_at"),
            "alive": _pid_alive(pid),
            "depth": _HOLD_DEPTH.get(self._key, 0),
        }

    def is_locked(self) -> bool:
        info = self.info()
        if not info:
            return False
        if info.get("raw"):
            return True
        if info.get("alive"):
            return True
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        _HOLD_DEPTH.pop(self._key, None)
        return False

    def acquire(self, *, blocking: bool = True, timeout: float = 5.0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            if self._try_acquire():
                return True
            if not blocking or time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _try_acquire(self) -> bool:
        depth = _HOLD_DEPTH.get(self._key, 0)
        if depth > 0:
            _HOLD_DEPTH[self._key] = depth + 1
            return True

        if self.is_locked():
            info = self.info()
            # Same process already owns the file (e.g. after crash of depth map)
            if info and info.get("pid") == os.getpid() and info.get("alive"):
                _HOLD_DEPTH[self._key] = 1
                return True
            return False

        payload = {
            "pid": os.getpid(),
            "held_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            fd = os.open(
                str(self.path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            _HOLD_DEPTH[self._key] = 1
            return True
        except FileExistsError:
            if not self.is_locked():
                return self._try_acquire()
            info = self.info()
            if info and info.get("pid") == os.getpid() and info.get("alive"):
                _HOLD_DEPTH[self._key] = 1
                return True
            return False

    def release(self) -> None:
        depth = _HOLD_DEPTH.get(self._key, 0)
        if depth <= 0:
            return
        if depth > 1:
            _HOLD_DEPTH[self._key] = depth - 1
            return
        _HOLD_DEPTH.pop(self._key, None)
        try:
            info = self.info()
            if info and info.get("pid") == os.getpid():
                self.path.unlink(missing_ok=True)
            elif not info:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> WorkspaceLock:
        if not self.acquire():
            raise RuntimeError(f"workspace lock busy: {self.path}")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
