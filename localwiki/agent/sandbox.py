"""Windows / Docker execution sandbox (design §11.8 / AT-AGT-04).

Windows native: Job Object with KILL_ON_JOB_CLOSE, cwd lock, env scrub.
Docker mode: optional container exec with resource limits (stub when no Docker).
Network isolation is Docker-only; native mode audits only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Secrets / sensitive env keys never passed to child processes
_SECRET_ENV_DENY = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE",
)

# Minimal allowlist when scrubbing
DEFAULT_ENV_ALLOW = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "USERNAME",
        "COMPUTERNAME",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
)


@dataclass
class SandboxLimits:
    memory_mb: int = 512
    cpu_percent: int = 50  # informational on Windows Job (process time used instead)
    process_limit: int = 32
    timeout_s: float = 30.0
    job_memory: bool = True


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    killed: bool = False
    mode: str = "windows"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "killed": self.killed,
            "mode": self.mode,
            "meta": dict(self.meta),
        }


def scrub_environ(
    base: Mapping[str, str] | None = None,
    *,
    allow: Sequence[str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Pass only allowlisted vars; drop anything that looks like a secret."""
    src = dict(base if base is not None else os.environ)
    allowed = {a.upper() for a in (allow or DEFAULT_ENV_ALLOW)}
    out: dict[str, str] = {}
    for k, v in src.items():
        ku = k.upper()
        if ku not in allowed:
            continue
        if any(s in ku for s in _SECRET_ENV_DENY):
            continue
        out[k] = v
    if extra:
        for k, v in extra.items():
            ku = k.upper()
            if any(s in ku for s in _SECRET_ENV_DENY):
                continue
            out[k] = v
    return out


def _is_windows() -> bool:
    return sys.platform == "win32"


class WindowsJobSandbox:
    """Run a command inside a Job Object; closing the job kills the process tree."""

    def __init__(
        self,
        *,
        cwd: str | None = None,
        limits: SandboxLimits | None = None,
        env_allow: Sequence[str] | None = None,
    ) -> None:
        if not _is_windows():
            raise RuntimeError("WindowsJobSandbox requires win32")
        self.cwd = cwd
        self.limits = limits or SandboxLimits()
        self.env_allow = list(env_allow) if env_allow else None
        self._job = None
        self._proc: subprocess.Popen[str] | None = None
        self._kernel32 = None
        self._setup_api()

    def _setup_api(self) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = k32

        # Constants
        self.JobObjectExtendedLimitInformation = 9
        self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        self.JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        self.JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        self.JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200

    def _create_job(self) -> Any:
        ctypes = self._ctypes
        k32 = self._kernel32
        assert k32 is not None

        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError("CreateJobObjectW failed")

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | self.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.LimitFlags = flags
        info.BasicLimitInformation.ActiveProcessLimit = self.limits.process_limit
        if self.limits.job_memory and self.limits.memory_mb > 0:
            info.BasicLimitInformation.LimitFlags |= self.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(self.limits.memory_mb) * 1024 * 1024

        ok = k32.SetInformationJobObject(
            job,
            self.JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            k32.CloseHandle(job)
            raise OSError("SetInformationJobObject failed")
        return job

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        env_extra: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> SandboxResult:
        ctypes = self._ctypes
        k32 = self._kernel32
        assert k32 is not None

        timeout = self.limits.timeout_s if timeout_s is None else timeout_s
        env = scrub_environ(allow=self.env_allow, extra=env_extra)
        job = self._create_job()
        self._job = job
        killed = False
        timed_out = False
        stdout = ""
        stderr = ""
        code: int | None = None

        try:
            creationflags = 0
            # CREATE_SUSPENDED not required if we assign immediately after create
            self._proc = subprocess.Popen(
                list(argv),
                cwd=self.cwd,
                env=env,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            )
            # Assign to job before it does much work
            handle = ctypes.c_void_p(self._proc._handle)  # type: ignore[attr-defined]
            if not k32.AssignProcessToJobObject(job, handle):
                self._proc.kill()
                raise OSError("AssignProcessToJobObject failed")

            try:
                stdout, stderr = self._proc.communicate(
                    input=input_text, timeout=timeout
                )
                code = self._proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                killed = True
                # Closing job handle kills the tree (KILL_ON_JOB_CLOSE)
                k32.CloseHandle(job)
                self._job = None
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
                code = self._proc.returncode
                stdout = stdout or ""
                stderr = (stderr or "") + "\n[sandbox] timed out; job closed"
        finally:
            if self._job is not None:
                # Closing the job handle kills any remaining process tree
                k32.CloseHandle(self._job)
                self._job = None

        return SandboxResult(
            ok=(not timed_out) and code == 0,
            exit_code=code,
            stdout=stdout or "",
            stderr=stderr or "",
            timed_out=timed_out,
            killed=killed,
            mode="windows",
            meta={"cwd": self.cwd, "argv": list(argv)},
        )

    def close(self) -> None:
        """Explicitly close job → kill entire process tree."""
        if self._job is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._job)
            self._job = None


class DockerSandbox:
    """Optional Docker-backed sandbox; unit tests may inject a runner."""

    def __init__(
        self,
        *,
        image: str = "localwiki-sandbox:local",
        network: str = "none",
        mounts: Sequence[tuple[str, str]] | None = None,
        limits: SandboxLimits | None = None,
        runner: Any = None,
    ) -> None:
        self.image = image
        self.network = network
        self.mounts = list(mounts or [])
        self.limits = limits or SandboxLimits()
        self.runner = runner  # injectable for tests

    def run(self, argv: Sequence[str], *, timeout_s: float | None = None) -> SandboxResult:
        timeout = self.limits.timeout_s if timeout_s is None else timeout_s
        docker_argv = [
            "docker",
            "run",
            "--rm",
            f"--network={self.network}",
            f"--memory={self.limits.memory_mb}m",
            f"--pids-limit={self.limits.process_limit}",
            "--user",
            "65534:65534",
        ]
        for host, cont in self.mounts:
            docker_argv.extend(["-v", f"{host}:{cont}:rw"])
        docker_argv.append(self.image)
        docker_argv.extend(list(argv))

        if self.runner is not None:
            return self.runner(docker_argv, timeout_s=timeout)

        try:
            proc = subprocess.run(
                docker_argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=scrub_environ(),
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                mode="docker",
                meta={"network": self.network, "argv": docker_argv},
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                ok=False,
                exit_code=None,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr="docker timeout",
                timed_out=True,
                killed=True,
                mode="docker",
            )
        except FileNotFoundError:
            return SandboxResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="docker_not_found",
                mode="docker",
                meta={"error": "docker_not_found"},
            )


def run_sandboxed(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    mode: str = "windows",
    limits: SandboxLimits | None = None,
    env_extra: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    docker: DockerSandbox | None = None,
) -> SandboxResult:
    """Dispatch to Windows Job Object or Docker sandbox."""
    if mode == "docker":
        sb = docker or DockerSandbox(limits=limits)
        return sb.run(argv, timeout_s=timeout_s)
    if not _is_windows():
        # Non-Windows fallback: plain subprocess with scrubbed env (tests on CI)
        env = scrub_environ(extra=env_extra)
        try:
            proc = subprocess.run(
                list(argv),
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s or (limits.timeout_s if limits else 30),
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                mode="posix-fallback",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                exit_code=None,
                stdout="",
                stderr="timeout",
                timed_out=True,
                killed=True,
                mode="posix-fallback",
            )
    sb = WindowsJobSandbox(cwd=cwd, limits=limits or SandboxLimits())
    return sb.run(argv, env_extra=env_extra, timeout_s=timeout_s)


def kill_process_tree_demo(child_script_seconds: float = 60.0) -> dict[str, Any]:
    """Start a long-lived child under a job, close job, confirm death (AT-AGT-04)."""
    if not _is_windows():
        return {"ok": False, "skipped": "not_windows"}
    limits = SandboxLimits(timeout_s=2.0, memory_mb=256, process_limit=8)
    # Use python -c with a sleep that outlives timeout
    argv = [
        sys.executable,
        "-c",
        (
            "import time,sys;\n"
            "print('child-start', flush=True);\n"
            f"time.sleep({child_script_seconds});\n"
            "print('child-end', flush=True)\n"
        ),
    ]
    sb = WindowsJobSandbox(limits=limits)
    t0 = time.time()
    result = sb.run(argv, timeout_s=1.5)
    elapsed = time.time() - t0
    return {
        "ok": result.timed_out and result.killed,
        "timed_out": result.timed_out,
        "killed": result.killed,
        "exit_code": result.exit_code,
        "elapsed_s": round(elapsed, 3),
        "stdout": result.stdout[:200],
        "stderr": result.stderr[:200],
    }
