"""Agent session checkpoint, resume, and raw trajectory (design §11.6 / AT-AGT-07).

Checkpoints: ``.kw/runtime/agents/<session>/state.json``
Raw trajectory: ``raw/agent-sessions/<session>/trace.jsonl``
Unknown external writes → ``pending_reconcile`` (never auto-replay).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

ReconcileStatus = Literal["ok", "pending_reconcile", "confirmed"]


@dataclass
class ExternalWriteRecord:
    write_id: str
    tool: str
    arguments: dict[str, Any]
    status: ReconcileStatus = "pending_reconcile"
    receipt: dict[str, Any] | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentSessionState:
    session_id: str
    task_uid: str = ""
    goal: str = ""
    step: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    protocol: str = "openai"
    approval_mode: str = "workspace-auto"
    decisions: list[str] = field(default_factory=list)
    modified_files: list[dict[str, str]] = field(default_factory=list)
    approved_approvals: list[dict[str, Any]] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)
    external_writes: list[ExternalWriteRecord] = field(default_factory=list)
    status: str = "running"
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_uid": self.task_uid,
            "goal": self.goal,
            "step": self.step,
            "messages": list(self.messages),
            "plan": list(self.plan),
            "tool_trace": list(self.tool_trace),
            "protocol": self.protocol,
            "approval_mode": self.approval_mode,
            "decisions": list(self.decisions),
            "modified_files": list(self.modified_files),
            "approved_approvals": list(self.approved_approvals),
            "open_issues": list(self.open_issues),
            "external_writes": [w.to_dict() for w in self.external_writes],
            "status": self.status,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSessionState:
        writes = [
            ExternalWriteRecord(**w) if isinstance(w, dict) else w
            for w in data.get("external_writes") or []
        ]
        return cls(
            session_id=str(data.get("session_id") or ""),
            task_uid=str(data.get("task_uid") or ""),
            goal=str(data.get("goal") or ""),
            step=int(data.get("step") or 0),
            messages=list(data.get("messages") or []),
            plan=list(data.get("plan") or []),
            tool_trace=list(data.get("tool_trace") or []),
            protocol=str(data.get("protocol") or "openai"),
            approval_mode=str(data.get("approval_mode") or "workspace-auto"),
            decisions=list(data.get("decisions") or []),
            modified_files=list(data.get("modified_files") or []),
            approved_approvals=list(data.get("approved_approvals") or []),
            open_issues=list(data.get("open_issues") or []),
            external_writes=writes,
            status=str(data.get("status") or "running"),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class SessionStore:
    """Persist checkpoints and append-only raw traces under a workspace root."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.runtime = self.root / ".kw" / "runtime" / "agents"
        self.raw = self.root / "raw" / "agent-sessions"

    def session_dir(self, session_id: str) -> Path:
        return self.runtime / session_id

    def state_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "state.json"

    def trace_path(self, session_id: str) -> Path:
        return self.raw / session_id / "trace.jsonl"

    def create(
        self,
        *,
        task_uid: str = "",
        goal: str = "",
        protocol: str = "openai",
        approval_mode: str = "workspace-auto",
        session_id: str | None = None,
    ) -> AgentSessionState:
        sid = session_id or f"agt_{uuid.uuid4().hex[:12]}"
        state = AgentSessionState(
            session_id=sid,
            task_uid=task_uid,
            goal=goal,
            protocol=protocol,
            approval_mode=approval_mode,
        )
        self.checkpoint(state)
        return state

    def checkpoint(self, state: AgentSessionState) -> Path:
        state.updated_at = time.time()
        d = self.session_dir(state.session_id)
        d.mkdir(parents=True, exist_ok=True)
        path = self.state_path(state.session_id)
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def append_trace(self, session_id: str, event: dict[str, Any]) -> None:
        tdir = self.raw / session_id
        tdir.mkdir(parents=True, exist_ok=True)
        path = self.trace_path(session_id)
        row = dict(event)
        row.setdefault("ts", time.time())
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def load(self, session_id: str) -> AgentSessionState | None:
        path = self.state_path(session_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentSessionState.from_dict(data)

    def resume(self, session_id: str) -> tuple[AgentSessionState | None, list[str]]:
        """Load checkpoint. Blocks auto-continue if pending external writes exist."""
        state = self.load(session_id)
        warnings: list[str] = []
        if state is None:
            return None, ["session_not_found"]
        pending = [
            w for w in state.external_writes if w.status == "pending_reconcile"
        ]
        if pending:
            warnings.append("pending_reconcile")
            state.status = "pending_reconcile"
            # Do not clear messages — caller must reconcile before replaying tools
        return state, warnings

    def record_external_write(
        self,
        state: AgentSessionState,
        *,
        tool: str,
        arguments: dict[str, Any],
        receipt: dict[str, Any] | None = None,
    ) -> ExternalWriteRecord:
        rec = ExternalWriteRecord(
            write_id=f"xw_{uuid.uuid4().hex[:10]}",
            tool=tool,
            arguments=dict(arguments),
            status="ok" if receipt else "pending_reconcile",
            receipt=receipt,
        )
        state.external_writes.append(rec)
        self.checkpoint(state)
        self.append_trace(
            state.session_id,
            {"type": "external_write", "record": rec.to_dict()},
        )
        return rec

    def confirm_external_write(
        self,
        state: AgentSessionState,
        write_id: str,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> bool:
        for w in state.external_writes:
            if w.write_id == write_id:
                w.status = "confirmed"
                if receipt is not None:
                    w.receipt = receipt
                self.checkpoint(state)
                return True
        return False

    def can_continue(self, state: AgentSessionState) -> bool:
        return not any(w.status == "pending_reconcile" for w in state.external_writes)

    def latest_session_id(self) -> str | None:
        if not self.runtime.is_dir():
            return None
        dirs = [p for p in self.runtime.iterdir() if p.is_dir()]
        if not dirs:
            return None
        dirs.sort(key=lambda p: (p / "state.json").stat().st_mtime if (p / "state.json").exists() else 0)
        return dirs[-1].name


def after_tool_checkpoint(
    store: SessionStore,
    state: AgentSessionState,
    *,
    call: dict[str, Any],
    result: dict[str, Any],
    messages: Sequence[dict[str, Any]] | None = None,
) -> None:
    """Convenience: bump step, append trace, save state (call after each tool)."""
    state.step += 1
    if messages is not None:
        state.messages = list(messages)
    state.tool_trace.append({"call": call, "result": result, "step": state.step})
    store.append_trace(
        state.session_id,
        {"type": "tool", "step": state.step, "call": call, "result": result},
    )
    store.checkpoint(state)
