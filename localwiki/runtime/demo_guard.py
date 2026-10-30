"""Guard against destructive bash targeting ``.kw/demo`` (R3.27)."""

from __future__ import annotations

import re
from typing import Any

_DEMO_PATH = re.compile(
    r"(?:^|[\\/\s\"'])\.?kw[\\/]+demo\b|[\\/]demo[\\/]|\.kw\\demo\b",
    re.IGNORECASE,
)

# delete / remove / rmdir family
_DELETE_CMD = re.compile(
    r"(?i)(?:^|[;&|]\s*|\n\s*)"
    r"(?:"
    r"rm\b(?:\s+-[a-zA-Z]+)*|"
    r"del\b|"
    r"erase\b|"
    r"rd\b|"
    r"rmdir\b|"
    r"Remove-Item\b|"
    r"ri\b(?=\s)"  # PowerShell alias often used with -Recurse
    r")"
)

# clear / truncate demo files
_CLEAR_CMD = re.compile(
    r"(?i)(?:"
    r"Clear-Content\b|"
    r"Set-Content\b\s+[^\n]*\.kw[\\/]+demo|"
    r"(?:echo|type)\s+(?:['\"]{2}|NUL|>)\s*>+\s*[^\n]*\.kw[\\/]+demo|"
    r">\s*[^\n]*\.kw[\\/]+demo[^\n]*$"
    r")"
)


def touches_demo_path(text: str) -> bool:
    return bool(_DEMO_PATH.search(text or ""))


def is_dangerous_demo_bash(cmd: str) -> bool:
    """True when command would delete/clear files under ``.kw/demo``."""
    s = (cmd or "").strip()
    if not s or not touches_demo_path(s):
        return False
    if _DELETE_CMD.search(s):
        return True
    if _CLEAR_CMD.search(s):
        return True
    # PowerShell Remove-Item anywhere in pipeline with demo path
    if re.search(r"(?i)Remove-Item\b", s) and touches_demo_path(s):
        return True
    return False


def extract_bash_command(info: dict[str, Any] | None) -> str | None:
    """Best-effort command string from an OC tool event / on_tool payload."""
    if not isinstance(info, dict):
        return None
    for key in ("command", "cmd", "title", "input", "path"):
        v = info.get(key)
        if isinstance(v, str) and v.strip():
            # title may be short; still useful
            return v.strip()
        if isinstance(v, dict):
            for k2 in ("command", "cmd", "script", "path", "title"):
                v2 = v.get(k2)
                if isinstance(v2, str) and v2.strip():
                    return v2.strip()
    state = info.get("state")
    if isinstance(state, dict):
        inp = state.get("input") or state.get("args")
        if isinstance(inp, dict):
            for k2 in ("command", "cmd", "script"):
                v2 = inp.get(k2)
                if isinstance(v2, str) and v2.strip():
                    return v2.strip()
        if isinstance(inp, str) and inp.strip():
            return inp.strip()
    return None


def tool_looks_like_bash(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return n in ("bash", "shell", "cmd", "powershell", "pwsh")
