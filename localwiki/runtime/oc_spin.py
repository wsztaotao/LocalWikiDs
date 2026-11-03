"""Event-based demo spin detection (R3.27) — no wall-clock soft timeout."""

from __future__ import annotations

from dataclasses import dataclass, field

SPIN_NUDGE_AFTER = 8
SPIN_STOP_AFTER = 16

# Tools that do not advance demo delivery
_SPIN_TOOLS = frozenset(
    {
        "read",
        "list",
        "glob",
        "grep",
        "todowrite",
        "todoread",
        "bash",
        "shell",
        "cmd",
        "powershell",
        "pwsh",
        "webfetch",
    }
)

_PROGRESS_TOOLS = frozenset({"lw_write", "write", "edit", "apply_patch"})


def _is_demo_path(path_or_title: str | None) -> bool:
    s = (path_or_title or "").replace("\\", "/").lower()
    return ".kw/demo" in s or s.startswith("demo/") or "/demo/" in s


@dataclass
class DemoSpinTracker:
    """Count consecutive non-progress tool ends for demo jobs."""

    nudge_after: int = SPIN_NUDGE_AFTER
    stop_after: int = SPIN_STOP_AFTER
    spin_count: int = 0
    last_gaps_len: int | None = None
    nudge_sent: bool = False
    stop_fired: bool = False
    progress_writes: int = 0
    _events: list[str] = field(default_factory=list)

    def note_gaps(self, gaps: list[str] | None) -> None:
        n = len(gaps or [])
        if self.last_gaps_len is not None and n < self.last_gaps_len:
            self._reset_spin("gaps_shrunk")
        self.last_gaps_len = n

    def note_tool_end(
        self,
        name: str,
        *,
        path_or_title: str | None = None,
        ok: bool | None = True,
    ) -> None:
        if self.stop_fired:
            return
        key = (name or "").strip().lower()
        if not key:
            return
        if key in _PROGRESS_TOOLS and _is_demo_path(path_or_title) and ok is not False:
            self.progress_writes += 1
            self._reset_spin(f"write:{key}")
            return
        if key in _SPIN_TOOLS:
            self.spin_count += 1
            self._events.append(key)
            return
        # other tools: mild spin (unknown) — count as spin to catch loops
        if key not in ("invalid", "unknown"):
            self.spin_count += 1
            self._events.append(key)

    def _reset_spin(self, reason: str) -> None:
        self.spin_count = 0
        self.nudge_sent = False
        self._events.append(f"reset:{reason}")

    def should_nudge(self) -> bool:
        if self.nudge_sent or self.stop_fired:
            return False
        return self.spin_count >= self.nudge_after

    def mark_nudge_sent(self) -> None:
        self.nudge_sent = True

    def should_stop(self) -> bool:
        if self.stop_fired:
            return True
        return self.spin_count >= self.stop_after

    def mark_stop(self) -> None:
        self.stop_fired = True
