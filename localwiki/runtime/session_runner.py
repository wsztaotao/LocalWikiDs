"""Session message queue: max 2 concurrent; FIFO; same session serial (R3.8/R3.10)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from localwiki.sessions.store import SessionStore

RunFn = Callable[..., dict[str, Any]]
MAX_RUNNING = 2
_HEARTBEAT_SEC = 10.0
_HEARTBEAT_TEXT = "OpenCode 仍在运行（工具/模型处理中）…"
_CONTINUE_DEFAULT = "请继续完成未完成的工作；优先补齐缺失文件并用 lw_write 写入。"

_TOOL_STATUS_START = {
    "export_docx": "正在导出 Word…",
    "export_pdf": "正在导出 PDF…",
    "export_tts": "正在合成音频…",
    "bash": "正在执行命令…",
    "write": "正在写入文件…",
    "lw_write": "正在写入大文件…",
    "edit": "正在编辑文件…",
    "apply_patch": "正在应用补丁…",
}
_TOOL_STATUS_END = {
    "export_docx": "已写出 Word",
    "export_pdf": "已写出 PDF",
    "export_tts": "已写出音频",
    "lw_write": "已写入文件",
}


def tool_status_text(name: str, *, phase: str, ok: bool | None = None) -> str:
    n = (name or "").strip()
    key = n.lower()
    if phase == "end":
        if ok is False:
            return f"工具 {n} 失败"
        return _TOOL_STATUS_END.get(key, f"已调用 {n}")
    return _TOOL_STATUS_START.get(key, f"正在调用 {n}…")


def _compact_prompt(history: list[dict[str, Any]], *, max_chars: int = 4000) -> str:
    """Build a short prompt from recent turns (prefer last user message).

    R3.21: when the current user turn is not an export request, strip prior
    assistant export promo / ``/api/exports/`` links to reduce cross-talk.
    """
    if not history:
        return ""
    last_user = ""
    for m in reversed(history):
        if (m.get("role") or "") == "user" and (m.get("content") or "").strip():
            last_user = str(m.get("content") or "").strip()
            break
    strip_exports = False
    try:
        from localwiki.runtime.export_intent import strip_export_promo
        from localwiki.runtime.intent_route import (
            classify_user_intent,
            should_strip_export_history,
        )

        kind = classify_user_intent(last_user) if last_user else "chat"
        strip_exports = bool(last_user) and should_strip_export_history(kind)
    except Exception:
        strip_export_promo = None  # type: ignore[assignment]
    if last_user and len(last_user) <= max_chars and len(history) <= 2:
        return last_user
    lines: list[str] = []
    for m in history[-6:]:
        role = m.get("role") or "user"
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if (
            strip_exports
            and strip_export_promo is not None
            and (role or "") == "assistant"
        ):
            cleaned = strip_export_promo(content)
            # Do not fall back to the original (would re-introduce export links).
            content = cleaned if cleaned.strip() else "(prior assistant reply)"
        if len(content) > 800:
            content = content[:800] + "…"
        lines.append(f"{role}: {content}")
    blob = "\n".join(lines)
    if len(blob) > max_chars:
        blob = blob[-max_chars:]
    return blob or last_user


@dataclass
class Job:
    job_id: str
    session_id: str
    content: str
    status: str = "queued"  # queued | running | done | error | cancelled
    error: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    stream_text: str = ""
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    wiki_hit: bool = False
    wiki_paths: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_reason: str | None = None  # user | demo_bash_denied | demo_spin | …
    _cv: threading.Condition = field(default_factory=threading.Condition)

    last_event_at: float = field(default_factory=time.time)

    def push_event(self, ev: dict[str, Any]) -> None:
        with self._cv:
            self.stream_events.append(ev)
            self.last_event_at = time.time()
            if ev.get("type") == "delta" and ev.get("text"):
                self.stream_text += str(ev.get("text") or "")
            self._cv.notify_all()

    def wait_events(self, after: int, timeout: float = 30.0) -> list[dict[str, Any]]:
        with self._cv:
            if after < len(self.stream_events):
                return list(self.stream_events[after:])
            self._cv.wait(timeout=timeout)
            return list(self.stream_events[after:])


class SessionRunner:
    """Process-wide runner keyed by workspace path string."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        run_fn: RunFn | None = None,
        max_running: int = MAX_RUNNING,
    ) -> None:
        self.root = Path(workspace).resolve()
        self.store = SessionStore(self.root)
        self.max_running = max_running
        self._run_fn = run_fn or self._default_run
        self._lock = threading.Lock()
        self._queue: deque[Job] = deque()
        self._jobs: dict[str, Job] = {}
        self._running_sessions: set[str] = set()
        self._running_count = 0

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Signal a running (or queued) job to stop; kill OC via cancel_event (R3.26)."""
        queued_cancel: Job | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise FileNotFoundError(job_id)
            if job.status in ("done", "error", "cancelled"):
                return {
                    "ok": True,
                    "job_id": job_id,
                    "status": job.status,
                    "already_finished": True,
                }
            if not job.cancel_reason:
                job.cancel_reason = "user"
            setattr(job.cancel_event, "_lw_cancel_reason", job.cancel_reason)
            job.cancel_event.set()
            # Direct kill callback registered by run_streaming (avoids wedged readline)
            kill = getattr(job.cancel_event, "_lw_kill", None)
            if callable(kill):
                try:
                    kill()
                except Exception:  # noqa: BLE001
                    pass
            if job.status == "queued":
                job.status = "cancelled"
                job.error = {
                    "code": "E_CANCELLED",
                    "message": "OpenCode run cancelled by user",
                }
                try:
                    self._queue.remove(job)
                except ValueError:
                    pass
                queued_cancel = job
            else:
                return {
                    "ok": True,
                    "job_id": job_id,
                    "status": "running",
                    "already_finished": False,
                }
        assert queued_cancel is not None
        queued_cancel.push_event(
            {
                "type": "done",
                "ok": False,
                "cancelled": True,
                "content": "（已停止）",
                "error": queued_cancel.error,
            }
        )
        return {
            "ok": True,
            "job_id": job_id,
            "status": "cancelled",
            "already_finished": False,
        }

    def enqueue(self, session_id: str, content: str) -> dict[str, Any]:
        text = (content or "").strip()
        if not text:
            raise ValueError("content required")
        self.store.get(session_id)
        job = Job(job_id=f"j_{uuid.uuid4().hex[:10]}", session_id=session_id, content=text)
        user_msg = {
            "role": "user",
            "content": text,
            "run_id": job.job_id,
            "ok": True,
        }
        self.store.append_message(session_id, user_msg)
        with self._lock:
            self._jobs[job.job_id] = job
            self._queue.append(job)
        self._pump()
        return {"ok": True, "job_id": job.job_id, "status": job.status}

    def enqueue_continue(
        self, session_id: str, message: str | None = None
    ) -> dict[str, Any]:
        """Continue an OpenCode session via stored ``oc_session_ref`` (R3.25)."""
        meta = self.store.get(session_id)
        ref = (meta or {}).get("oc_session_ref")
        if not ref:
            raise ValueError("E_NO_OC_SESSION")
        text = (message or "").strip() or _CONTINUE_DEFAULT
        return self.enqueue(session_id, text)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running_count,
                "max_running": self.max_running,
                "queued": sum(1 for j in self._queue if j.status == "queued"),
                "jobs": [
                    {
                        "job_id": j.job_id,
                        "session_id": j.session_id,
                        "status": j.status,
                        "error": j.error,
                        "wiki_hit": j.wiki_hit,
                    }
                    for j in list(self._jobs.values())[-50:]
                ],
            }

    def _pump(self) -> None:
        while True:
            job: Job | None = None
            with self._lock:
                if self._running_count >= self.max_running:
                    return
                for candidate in list(self._queue):
                    if candidate.status != "queued":
                        continue
                    if candidate.session_id in self._running_sessions:
                        continue
                    job = candidate
                    self._queue.remove(candidate)
                    job.status = "running"
                    self._running_count += 1
                    self._running_sessions.add(job.session_id)
                    break
            if job is None:
                return
            t = threading.Thread(target=self._execute, args=(job,), daemon=True)
            t.start()

    def _execute(self, job: Job) -> None:
        try:
            history = self.store.list_messages(job.session_id)
            compact = _compact_prompt(history)
            from localwiki.wiki_context import build_prompt_with_wiki, retrieve_wiki_context

            wiki = retrieve_wiki_context(self.root, job.content)
            job.wiki_hit = bool(wiki.get("hit"))
            job.wiki_paths = list(wiki.get("paths") or [])
            job.push_event(
                {
                    "type": "meta",
                    "wiki_hit": job.wiki_hit,
                    "wiki_paths": job.wiki_paths,
                }
            )
            prompt = build_prompt_with_wiki(compact, wiki)

            oc_ref = None
            try:
                meta = self.store.get(job.session_id)
                oc_ref = (meta or {}).get("oc_session_ref")
            except Exception:  # noqa: BLE001
                oc_ref = None

            def on_delta(piece: str) -> None:
                """Forward OC text as small SSE frames so the UI visibly streams.

                OpenCode often emits one large JSON text blob at the end; slicing
                here turns that into many ``delta`` events over the wire.
                """
                text = piece or ""
                if not text:
                    return
                step = 16
                if len(text) <= step:
                    job.push_event({"type": "delta", "text": text})
                    return
                for i in range(0, len(text), step):
                    job.push_event({"type": "delta", "text": text[i : i + step]})
                    # Yield so SSE waiters can flush between frames
                    time.sleep(0.035)

            last_oc_hint = {"text": _HEARTBEAT_TEXT, "busy": False}

            # R3.27: demo-only spin tracker (event-based, no wall soft-timeout)
            spin_tracker = None
            spin_nudge_requested = False
            try:
                from localwiki.runtime.intent_route import classify_user_intent
                from localwiki.runtime.oc_spin import DemoSpinTracker

                if classify_user_intent(job.content) == "demo":
                    spin_tracker = DemoSpinTracker()
                    try:
                        from localwiki.runtime.intent_route import demo_asset_gaps

                        spin_tracker.note_gaps(demo_asset_gaps(self.root))
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                spin_tracker = None

            def on_tool(info: dict[str, Any]) -> None:
                nonlocal spin_nudge_requested
                name = str(info.get("name") or "")
                phase = str(info.get("phase") or "start")
                ok = info.get("ok")
                title = info.get("title")
                ev: dict[str, Any] = {
                    "type": "tool",
                    "name": name,
                    "phase": phase,
                    "ok": ok,
                }
                if info.get("status") is not None:
                    ev["status"] = info.get("status")
                if title:
                    ev["title"] = title
                job.push_event(ev)
                st = tool_status_text(
                    name, phase=phase, ok=ok if isinstance(ok, bool) else None
                )
                if title and phase != "end":
                    st = f"{st.rstrip('…')}（{title}）…"
                last_oc_hint["text"] = st
                last_oc_hint["busy"] = phase != "end"
                job.push_event({"type": "status", "text": st})

                if spin_tracker is None or job.cancel_event.is_set():
                    return
                if phase == "end":
                    try:
                        from localwiki.runtime.intent_route import demo_asset_gaps

                        spin_tracker.note_gaps(demo_asset_gaps(self.root))
                    except Exception:  # noqa: BLE001
                        pass
                    spin_tracker.note_tool_end(
                        name,
                        path_or_title=str(title) if title else None,
                        ok=ok if isinstance(ok, bool) else True,
                    )
                    if spin_tracker.should_nudge():
                        spin_tracker.mark_nudge_sent()
                        spin_nudge_requested = True
                        job.push_event(
                            {
                                "type": "meta",
                                "spin_nudge": True,
                            }
                        )
                        job.push_event(
                            {
                                "type": "status",
                                "text": "检测到无交付进展（空转），将催促补齐 demo",
                            }
                        )
                    if spin_tracker.should_stop():
                        spin_tracker.mark_stop()
                        job.cancel_reason = "demo_spin"
                        setattr(
                            job.cancel_event, "_lw_cancel_reason", "demo_spin"
                        )
                        job.cancel_event.set()
                        kill = getattr(job.cancel_event, "_lw_kill", None)
                        if callable(kill):
                            try:
                                kill()
                            except Exception:  # noqa: BLE001
                                pass
                        job.push_event(
                            {
                                "type": "status",
                                "text": "因空转已停止（按 OpenCode 执行状况，非固定超时）",
                                "cancel_reason": "demo_spin",
                            }
                        )

            def on_event(ev: dict[str, Any]) -> None:
                """Forward OC structured events immediately for UI (R3.25)."""
                if not isinstance(ev, dict):
                    return
                et = str(ev.get("type") or ev.get("kind") or "")
                if et == "oc_status":
                    payload = {
                        "type": "oc_status",
                        "busy": bool(ev.get("busy")),
                        "session_status": ev.get("session_status"),
                        "step": ev.get("step"),
                        "tool": ev.get("tool"),
                    }
                    job.push_event(payload)
                    # human status line from structured state
                    parts: list[str] = []
                    if payload["busy"] or payload.get("session_status") == "busy":
                        parts.append("OpenCode busy")
                    elif payload.get("session_status"):
                        parts.append(f"OpenCode {payload['session_status']}")
                    tool = payload.get("tool")
                    if isinstance(tool, dict) and tool.get("name"):
                        tn = str(tool.get("name") or "").strip()
                        if tn.lower() not in (
                            "",
                            "invalid",
                            "unknown",
                            "error",
                            "null",
                            "none",
                        ):
                            phase = tool.get("phase") or "start"
                            parts.append(
                                f"正在 {tn}" if phase != "end" else f"已调用 {tn}"
                            )
                    if payload.get("step"):
                        parts.append(str(payload["step"]))
                    if parts:
                        text = " · ".join(parts)
                        last_oc_hint["text"] = text
                        last_oc_hint["busy"] = bool(payload["busy"])
                        job.push_event({"type": "status", "text": text})
                    return
                kind = str(ev.get("kind") or "")
                if kind == "session_status":
                    st = str(ev.get("status") or "")
                    last_oc_hint["busy"] = st == "busy"
                    text = f"OpenCode {st or 'status'}"
                    last_oc_hint["text"] = text
                    job.push_event(
                        {
                            "type": "oc_status",
                            "busy": st == "busy",
                            "session_status": st or None,
                        }
                    )
                    job.push_event({"type": "status", "text": text})
                elif kind == "step":
                    phase = str(ev.get("phase") or "start")
                    reason = ev.get("reason")
                    step = f"step {phase}" + (f" ({reason})" if reason else "")
                    job.push_event(
                        {
                            "type": "oc_status",
                            "busy": True,
                            "step": step,
                        }
                    )
                    last_oc_hint["text"] = f"OpenCode · {step}"
                    last_oc_hint["busy"] = True
                    job.push_event({"type": "status", "text": last_oc_hint["text"]})
                elif kind == "error":
                    msg = str(ev.get("message") or "opencode error")
                    job.push_event({"type": "status", "text": f"OpenCode 错误：{msg}"})

            stop_heartbeat = threading.Event()

            def _heartbeat() -> None:
                while not stop_heartbeat.wait(_HEARTBEAT_SEC):
                    if job.status not in ("running",):
                        return
                    if time.time() - job.last_event_at < _HEARTBEAT_SEC:
                        continue
                    # weak heartbeat only while still believed busy
                    hint = last_oc_hint.get("text") or _HEARTBEAT_TEXT
                    if last_oc_hint.get("busy"):
                        job.push_event(
                            {
                                "type": "status",
                                "text": f"{hint}（仍在执行）",
                            }
                        )
                    else:
                        job.push_event({"type": "status", "text": hint})

            hb = threading.Thread(target=_heartbeat, daemon=True)
            hb.start()

            job_started = time.time()

            def _persist_oc_ref(result_obj: dict[str, Any] | None) -> None:
                nonlocal oc_ref
                if not isinstance(result_obj, dict):
                    return
                sid = result_obj.get("oc_session_id")
                if isinstance(sid, str) and sid.strip():
                    oc_ref = sid.strip()
                    try:
                        self.store.update_meta(
                            job.session_id, oc_session_ref=oc_ref
                        )
                    except Exception:  # noqa: BLE001
                        pass

            def _call_run(p: str) -> dict[str, Any]:
                # Prefer cancel_event; only drop it on unexpected-keyword TypeError
                # (never swallow TypeErrors raised mid-run — that would orphan OC).
                try:
                    return self._run_fn(
                        p,
                        self.root,
                        session_id=oc_ref,
                        on_delta=on_delta,
                        on_tool=on_tool,
                        on_event=on_event,
                        cancel_event=job.cancel_event,
                    )
                except TypeError as exc:
                    msg = str(exc).lower()
                    if "cancel_event" not in msg and "unexpected keyword" not in msg:
                        raise
                    try:
                        return self._run_fn(
                            p,
                            self.root,
                            session_id=oc_ref,
                            on_delta=on_delta,
                            on_tool=on_tool,
                            on_event=on_event,
                        )
                    except TypeError:
                        try:
                            return self._run_fn(
                                p,
                                self.root,
                                session_id=oc_ref,
                                on_delta=on_delta,
                                on_tool=on_tool,
                            )
                        except TypeError:
                            try:
                                return self._run_fn(
                                    p, self.root, session_id=oc_ref, on_delta=on_delta
                                )
                            except TypeError:
                                try:
                                    return self._run_fn(
                                        p, self.root, session_id=oc_ref
                                    )
                                except TypeError:
                                    return self._run_fn(p, self.root)

            try:
                result = _call_run(prompt)
            finally:
                stop_heartbeat.set()
            _persist_oc_ref(result if isinstance(result, dict) else None)
            if not job.stream_text:
                out_once = str(result.get("stdout") or "")
                if out_once:
                    job.push_event({"type": "delta", "text": out_once})

            ok = bool(result.get("ok"))
            out_text = (
                (job.stream_text.strip() if job.stream_text else "")
                or result.get("stdout")
                or (result.get("data") if isinstance(result.get("data"), str) else None)
                or ""
            )
            if not out_text:
                err = result.get("error")
                if isinstance(err, dict) and err.get("message"):
                    out_text = str(err["message"])
                elif result.get("stderr"):
                    out_text = str(result.get("stderr"))[:1200]

            cancelled = bool(
                job.cancel_event.is_set()
                or (
                    isinstance(result, dict)
                    and (
                        result.get("cancelled")
                        or (
                            isinstance(result.get("error"), dict)
                            and result["error"].get("code") == "E_CANCELLED"
                        )
                    )
                )
            )
            # Resolve cancel_reason (R3.27)
            if isinstance(result, dict) and result.get("cancel_reason"):
                job.cancel_reason = str(result.get("cancel_reason"))
            elif cancelled and not job.cancel_reason:
                cr = getattr(job.cancel_event, "_lw_cancel_reason", None)
                job.cancel_reason = (
                    str(cr) if isinstance(cr, str) and cr.strip() else "user"
                )

            # R3.16/R3.23/R3.25/R3.27: one OC nudge retry — demo vs export
            try:
                from localwiki.runtime.export_intent import (
                    export_delivered,
                    is_long_form,
                    nudge_prompt,
                )
                from localwiki.runtime.intent_route import (
                    classify_user_intent,
                    demo_delivered,
                    demo_nudge_prompt,
                )

                kind = classify_user_intent(job.content)
                nudged = False
                # Do not retry after bash deny or spin stop
                hard_stop = job.cancel_reason in (
                    "demo_bash_denied",
                    "demo_spin",
                )
                if cancelled and hard_stop:
                    pass
                elif cancelled:
                    pass
                elif kind == "demo" and not demo_delivered(self.root, str(out_text)):
                    job.push_event({"type": "meta", "demo_nudge": True})
                    if spin_nudge_requested:
                        job.push_event({"type": "meta", "spin_nudge": True})
                    job.stream_text = ""
                    result2 = _call_run(
                        demo_nudge_prompt(
                            job.content, spin=bool(spin_nudge_requested)
                        )
                    )
                    _persist_oc_ref(result2 if isinstance(result2, dict) else None)
                    nudged = True
                    nudge_flag = "demo_nudged"
                elif kind in ("export_short", "export_long") and not export_delivered(
                    self.root,
                    result=result if isinstance(result, dict) else {},
                    out_text=str(out_text),
                    job_started=job_started,
                    user_text=job.content,
                ):
                    job.push_event({"type": "meta", "export_nudge": True})
                    job.stream_text = ""
                    result2 = _call_run(
                        nudge_prompt(
                            job.content,
                            long_form=(kind == "export_long")
                            or is_long_form(job.content),
                        )
                    )
                    _persist_oc_ref(result2 if isinstance(result2, dict) else None)
                    nudged = True
                    nudge_flag = "export_nudged"
                else:
                    result2 = None
                    nudge_flag = ""

                if nudged and result2 is not None:
                    if result2.get("stdout") and not job.stream_text:
                        job.push_event(
                            {"type": "delta", "text": str(result2.get("stdout") or "")}
                        )
                    if isinstance(result, dict) and isinstance(result2, dict):
                        te = list(result.get("tool_events") or []) + list(
                            result2.get("tool_events") or []
                        )
                        result = dict(result2)
                        result["tool_events"] = te
                        result[nudge_flag] = True
                    else:
                        result = result2
                    ok = bool(result.get("ok")) if isinstance(result, dict) else ok
                    out_text = (
                        (job.stream_text.strip() if job.stream_text else "")
                        or (result.get("stdout") if isinstance(result, dict) else "")
                        or out_text
                    )
                    # Refresh cancel state after nudge run (R3.27)
                    if isinstance(result, dict) and (
                        result.get("cancelled")
                        or (
                            isinstance(result.get("error"), dict)
                            and result["error"].get("code") == "E_CANCELLED"
                        )
                        or job.cancel_event.is_set()
                    ):
                        cancelled = True
                        if result.get("cancel_reason"):
                            job.cancel_reason = str(result.get("cancel_reason"))
                        elif not job.cancel_reason:
                            cr = getattr(job.cancel_event, "_lw_cancel_reason", None)
                            job.cancel_reason = (
                                str(cr)
                                if isinstance(cr, str) and cr.strip()
                                else "user"
                            )
            except Exception:  # noqa: BLE001
                pass

            # R3.25/R3.27: filter OC dump; synthesize incomplete / cancelled demo
            try:
                from localwiki.runtime.intent_route import (
                    classify_user_intent,
                    demo_asset_gaps,
                    demo_delivered,
                    demo_preview_ok,
                    looks_like_oc_dump,
                    synthesize_incomplete_reply,
                )

                kind_final = classify_user_intent(job.content)
                err_obj = result.get("error") if isinstance(result, dict) else None
                can_cont = bool(oc_ref) and job.cancel_reason != "demo_bash_denied"
                need_synth = (
                    cancelled
                    or looks_like_oc_dump(str(out_text))
                    or (
                        kind_final == "demo"
                        and not demo_delivered(self.root, str(out_text))
                    )
                )
                if need_synth:
                    gaps = demo_asset_gaps(self.root) if kind_final == "demo" else []
                    present: list[str] = []
                    demo_idx = self.root / ".kw" / "demo" / "index.html"
                    if demo_idx.is_file():
                        present.append(".kw/demo/index.html")
                    out_text = synthesize_incomplete_reply(
                        error=err_obj if isinstance(err_obj, dict) else None,
                        present=present,
                        missing=gaps,
                        preview_ok=demo_preview_ok(str(out_text) or "/demo/index.html")
                        and not gaps
                        and demo_idx.is_file(),
                        can_continue=can_cont and not cancelled,
                        cancel_reason=job.cancel_reason if cancelled else None,
                    )
                    if kind_final == "demo" and not demo_delivered(
                        self.root, "/demo/index.html" if demo_idx.is_file() else ""
                    ):
                        # keep ok flag honest for incomplete demo
                        if gaps or not demo_idx.is_file():
                            ok = False
                            if not isinstance(err_obj, dict) or cancelled:
                                err_obj = err_obj if isinstance(err_obj, dict) else {
                                    "code": "E_DEMO_INCOMPLETE",
                                    "message": "demo files incomplete",
                                }
                                if cancelled and job.cancel_reason:
                                    err_obj = {
                                        "code": "E_CANCELLED",
                                        "message": str(
                                            (result or {}).get("error", {}).get(
                                                "message"
                                            )
                                            if isinstance(result, dict)
                                            else ""
                                        )
                                        or job.cancel_reason,
                                    }
                                if isinstance(result, dict):
                                    result["error"] = err_obj
            except Exception:  # noqa: BLE001
                pass

            try:
                out_text = self._ensure_export_files(str(out_text))
            except Exception:  # noqa: BLE001
                pass
            # R3.26: generic openable URL rewrite on every assistant final text
            try:
                from localwiki.runtime.url_rewrite import rewrite_openable_urls
                from localwiki.workspace import load_config

                port = 8765
                try:
                    cfg = load_config(self.root / ".kw" / "config.json")
                    port = int(cfg.get("port") or 8765)
                except Exception:  # noqa: BLE001
                    import os

                    port = int(os.environ.get("LOCALWIKI_PORT") or 8765)
                out_text = rewrite_openable_urls(str(out_text), port=port)
            except Exception:  # noqa: BLE001
                pass
            if cancelled:
                ok = False
                if not isinstance(result, dict):
                    result = {}
                result = dict(result)
                result["cancelled"] = True
                if job.cancel_reason:
                    result["cancel_reason"] = job.cancel_reason
                result["error"] = result.get("error") or {
                    "code": "E_CANCELLED",
                    "message": "OpenCode run cancelled by user",
                }
                if not (out_text or "").strip():
                    out_text = "（已停止）"
            assistant = {
                "role": "assistant",
                "content": out_text or ("(empty)" if ok else "(failed)"),
                "run_id": job.job_id,
                "ok": ok,
                "error": result.get("error") if isinstance(result, dict) else None,
                "wiki_hit": job.wiki_hit,
                "wiki_paths": job.wiki_paths,
                "tool_events": (result.get("tool_events") if isinstance(result, dict) else None),
                "export_nudged": bool(
                    isinstance(result, dict) and result.get("export_nudged")
                ),
                "demo_nudged": bool(
                    isinstance(result, dict) and result.get("demo_nudged")
                ),
                "oc_session_ref": oc_ref,
                "cancelled": cancelled,
                "cancel_reason": job.cancel_reason if cancelled else None,
            }
            self.store.append_message(job.session_id, assistant)
            try:
                from localwiki.session_extract import extract_after_turn

                extract_after_turn(self.root, job.session_id, force=True)
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                if cancelled:
                    job.status = "cancelled"
                    job.error = result.get("error") if isinstance(result, dict) else {
                        "code": "E_CANCELLED",
                        "message": "OpenCode run cancelled by user",
                    }
                else:
                    job.status = "done" if ok else "error"
                    if not ok:
                        job.error = result.get("error") or {
                            "code": "E_FAILED",
                            "message": "run failed",
                        }
            job.push_event(
                {
                    "type": "done",
                    "ok": ok,
                    "cancelled": cancelled,
                    "cancel_reason": job.cancel_reason if cancelled else None,
                    "content": assistant["content"],
                    "error": job.error,
                    "wiki_hit": job.wiki_hit,
                    "wiki_paths": job.wiki_paths,
                    "oc_session_ref": oc_ref,
                    "can_continue": bool(oc_ref) and not cancelled,
                }
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job.status = "error"
                job.error = {"code": "E_FAILED", "message": str(exc)}
            try:
                self.store.append_message(
                    job.session_id,
                    {
                        "role": "assistant",
                        "content": str(exc),
                        "run_id": job.job_id,
                        "ok": False,
                        "error": {"code": "E_FAILED", "message": str(exc)},
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            job.push_event(
                {
                    "type": "done",
                    "ok": False,
                    "content": str(exc),
                    "error": job.error,
                }
            )
        finally:
            with self._lock:
                self._running_count = max(0, self._running_count - 1)
                self._running_sessions.discard(job.session_id)
            self._pump()

    def _ensure_export_files(self, text: str) -> str:
        """Keep only export links that already exist on disk (R3.15).

        Do **not** materialize apology/meta text into a fake .docx or reuse stale
        ``export.docx``. Missing links are stripped so the UI cannot offer a lie.
        """
        import re

        from localwiki.exports import resolve_export_existing

        out = text or ""
        names = re.findall(r"/api/exports/([A-Za-z0-9._-]+)", out)
        missing = False
        for name in dict.fromkeys(names):
            if resolve_export_existing(self.root, name) is not None:
                continue
            missing = True
            out = re.sub(
                rf"/api/exports/{re.escape(name)}\b",
                "",
                out,
            )
        if missing:
            out = re.sub(r"\n{3,}", "\n\n", out).rstrip()
            note = "（导出文件尚未生成；请让助手实际写入 .kw/exports 后再下载。）"
            if note not in out:
                out = out + "\n\n" + note
        return out

    @staticmethod
    def _default_run(
        prompt: str,
        workspace: Path,
        *,
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_tool: Callable[[dict[str, Any]], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        from localwiki.runtime.opencode_bridge import run_streaming

        return run_streaming(
            prompt,
            workspace=workspace,
            session_id=session_id,
            on_delta=on_delta,
            on_tool=on_tool,
            on_event=on_event,
            cancel_event=cancel_event,
            timeout=None,
        )


_RUNNERS: dict[str, SessionRunner] = {}
_RUNNERS_LOCK = threading.Lock()


def get_runner(
    workspace: Path | str,
    *,
    run_fn: RunFn | None = None,
) -> SessionRunner:
    key = str(Path(workspace).resolve())
    with _RUNNERS_LOCK:
        r = _RUNNERS.get(key)
        if r is None or run_fn is not None:
            r = SessionRunner(workspace, run_fn=run_fn)
            _RUNNERS[key] = r
        return r


def format_sse(ev: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
