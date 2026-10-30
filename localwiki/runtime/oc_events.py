"""Normalize OpenCode ``--format json`` NDJSON lines (R3.25)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


_RUNNING_STATUSES = frozenset({"pending", "running", "in_progress", "active", "busy"})
_END_STATUSES = frozenset({"completed", "complete", "error", "failed", "denied", "cancelled", "canceled"})


@dataclass
class OcRunState:
    """Mutable process-side view of OC activity for hung gating + SSE."""

    last_ndjson_at: float = -1.0
    session_status: str | None = None  # busy | idle | …
    busy: bool = False
    running_tools: set[str] = field(default_factory=set)
    last_tool: str | None = None
    last_tool_phase: str | None = None
    last_step: str | None = None
    oc_session_id: str | None = None

    def note_ndjson(self, now: float) -> None:
        self.last_ndjson_at = now

    def set_session_status(self, status: str | None) -> None:
        st = (status or "").strip().lower() or None
        self.session_status = st
        if st == "busy":
            self.busy = True
        elif st in ("idle", "complete", "completed", "done"):
            self.busy = False

    def tool_start(self, name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        self.running_tools.add(n)
        self.last_tool = n
        self.last_tool_phase = "start"
        self.busy = True

    def tool_end(self, name: str) -> None:
        n = (name or "").strip()
        if n:
            self.running_tools.discard(n)
            self.last_tool = n
            self.last_tool_phase = "end"
        if not self.running_tools and self.session_status != "busy":
            # keep busy if session still says busy
            if self.session_status != "busy":
                pass

    def should_hung_kill(self, now: float, *, idle_sec: float = 1800.0) -> bool:
        """True when silent ≥ idle_sec and OC is not known-busy."""
        if self.last_ndjson_at < 0:
            return False
        if (now - self.last_ndjson_at) < float(idle_sec):
            return False
        if self.busy or self.session_status == "busy":
            return False
        if self.running_tools:
            return False
        return True

    def status_text(self) -> str:
        parts: list[str] = []
        if self.busy or self.session_status == "busy":
            parts.append("OpenCode busy")
        elif self.session_status:
            parts.append(f"OpenCode {self.session_status}")
        else:
            parts.append("OpenCode 运行中")
        if self.last_tool and self.last_tool_phase == "start":
            parts.append(f"正在 {self.last_tool}")
        elif self.last_tool and self.last_tool_phase == "end":
            parts.append(f"已调用 {self.last_tool}")
        if self.last_step:
            parts.append(self.last_step)
        return " · ".join(parts)

    def oc_status_payload(self) -> dict[str, Any]:
        tool = None
        if self.last_tool:
            tool = {
                "name": self.last_tool,
                "phase": self.last_tool_phase or "start",
            }
        return {
            "busy": bool(self.busy or self.session_status == "busy" or self.running_tools),
            "session_status": self.session_status,
            "step": self.last_step,
            "tool": tool,
        }


def extract_session_id(ev: dict[str, Any]) -> str | None:
    for key in ("sessionID", "sessionId", "session_id"):
        v = ev.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    props = ev.get("properties")
    if isinstance(props, dict):
        for key in ("sessionID", "sessionId", "session_id"):
            v = props.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        info = props.get("info")
        if isinstance(info, dict):
            for key in ("id", "sessionID", "sessionId"):
                v = info.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


_BAD_TOOL_NAMES = frozenset(
    {
        "",
        "invalid",
        "unknown",
        "error",
        "null",
        "undefined",
        "none",
        "tool",
        "tool_use",
        "tool-call",
        "tool.call",
        "tool-result",
        "tool.result",
    }
)


def _tool_name_from(ev: dict[str, Any]) -> str | None:
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
    props = ev.get("properties")
    if not name and isinstance(props, dict):
        part = props.get("part") if isinstance(props.get("part"), dict) else None
        if part:
            name = part.get("tool") or part.get("name")
        if not name:
            name = props.get("name") or props.get("tool")
    if not name:
        return None
    n = str(name).strip()
    if not n or n.lower() in _BAD_TOOL_NAMES:
        return None
    return n


def _state_status(obj: dict[str, Any]) -> str | None:
    st = obj.get("state")
    if isinstance(st, dict):
        s = st.get("status")
        if isinstance(s, str) and s.strip():
            return s.strip().lower()
    s2 = obj.get("status")
    if isinstance(s2, str) and s2.strip():
        return s2.strip().lower()
    return None


def _tool_title(ev: dict[str, Any]) -> str | None:
    for src in (ev, ev.get("part") if isinstance(ev.get("part"), dict) else None):
        if not isinstance(src, dict):
            continue
        for key in ("title", "path", "file", "filename"):
            v = src.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:120]
        state = src.get("state")
        if isinstance(state, dict):
            inp = state.get("input") or state.get("args")
            if isinstance(inp, dict):
                for key in ("path", "file", "filename", "title"):
                    v = inp.get(key)
                    if isinstance(v, str) and v.strip():
                        return v.strip()[:120]
    return None


def parse_ndjson_line(line: str) -> dict[str, Any] | None:
    """Return a normalized event dict, or None if not JSON."""
    s = (line or "").strip()
    if not s.startswith("{"):
        return None
    try:
        ev = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(ev, dict):
        return None

    typ = str(ev.get("type") or "").lower()
    sid = extract_session_id(ev)
    out: dict[str, Any] = {"raw_type": typ, "session_id": sid}

    # session.status / session.idle
    if typ in ("session.status", "session_status") or typ.endswith(".status"):
        props = ev.get("properties") if isinstance(ev.get("properties"), dict) else ev
        st = None
        if isinstance(props, dict):
            info = props.get("status") or props.get("type")
            if isinstance(info, dict):
                st = info.get("type") or info.get("status")
            elif isinstance(info, str):
                st = info
            if not st and isinstance(props.get("info"), dict):
                st = props["info"].get("type") or props["info"].get("status")
        out["kind"] = "session_status"
        out["status"] = str(st).lower() if st else None
        return out

    if typ in ("session.idle", "session_idle") or typ.endswith(".idle"):
        out["kind"] = "session_status"
        out["status"] = "idle"
        return out

    if typ in ("step_start", "step-start", "step.start"):
        reason = ev.get("reason") or (ev.get("properties") or {}).get("reason") if isinstance(ev.get("properties"), dict) else None
        out["kind"] = "step"
        out["phase"] = "start"
        out["reason"] = str(reason) if reason else None
        return out

    if typ in ("step_finish", "step-finish", "step.finish", "step_finish"):
        reason = ev.get("reason") or (ev.get("properties") or {}).get("reason") if isinstance(ev.get("properties"), dict) else None
        out["kind"] = "step"
        out["phase"] = "end"
        out["reason"] = str(reason) if reason else None
        return out

    if typ == "error" or (isinstance(ev.get("error"), dict) and typ in ("", "error")):
        err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
        msg = None
        if isinstance(err, dict):
            data = err.get("data")
            if isinstance(data, dict):
                msg = data.get("message")
            msg = msg or err.get("message") or err.get("name")
        out["kind"] = "error"
        out["message"] = str(msg or "opencode error")
        return out

    # tool_use / tool parts
    name = _tool_name_from(ev)
    part = ev.get("part") if isinstance(ev.get("part"), dict) else None
    part_type = str((part or {}).get("type") or "").lower() if part else ""
    is_tool = (
        "tool" in typ
        or typ in ("tool_use", "tool-call", "tool.call", "tool-result", "tool.result")
        or part_type.startswith("tool")
    )
    if is_tool and name:
        status = _state_status(part or ev) or _state_status(ev)
        raw_l = typ
        phase = "start"
        ok: bool | None = None
        if status in _END_STATUSES or "finish" in raw_l or "result" in raw_l or raw_l.endswith(".end"):
            phase = "end"
            ok = status not in ("error", "failed", "denied")
            if status in ("error", "failed", "denied"):
                ok = False
            elif status in _END_STATUSES:
                ok = True
        elif status in _RUNNING_STATUSES:
            phase = "start"
            ok = None
        elif (part or {}).get("error") or ev.get("error"):
            phase = "end"
            ok = False
        out["kind"] = "tool"
        out["name"] = name
        out["phase"] = phase
        out["ok"] = ok
        out["status"] = status
        out["title"] = _tool_title(ev)
        return out

    # Only session id harvest for unknown types
    if sid:
        out["kind"] = "meta"
        return out
    return {"kind": "other", "raw_type": typ, "session_id": sid}


def apply_parsed(state: OcRunState, parsed: dict[str, Any], *, now: float) -> None:
    """Update OcRunState from a parse_ndjson_line result."""
    state.note_ndjson(now)
    sid = parsed.get("session_id")
    if isinstance(sid, str) and sid.strip():
        state.oc_session_id = sid.strip()
    kind = parsed.get("kind")
    if kind == "session_status":
        state.set_session_status(parsed.get("status"))
    elif kind == "step":
        phase = parsed.get("phase") or "start"
        reason = parsed.get("reason")
        state.last_step = f"step {phase}" + (f" ({reason})" if reason else "")
        if phase == "start":
            state.busy = True
    elif kind == "tool":
        name = str(parsed.get("name") or "")
        phase = str(parsed.get("phase") or "start")
        if phase == "end":
            state.tool_end(name)
        else:
            state.tool_start(name)
    elif kind == "error":
        # keep last timestamps; do not clear busy
        pass
