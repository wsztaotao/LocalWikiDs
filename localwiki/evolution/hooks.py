"""Runtime evolution hooks — deterministic signal capture only (§23.4 / LW-39).

No model calls. No reflection / proposals (those are P9). Records ERR/LRN/FEAT
via LearningStore for later self-improvement Skill consumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from localwiki.evolution.learnings import (
    LearningRecord,
    LearningStore,
    make_err,
    make_lrn,
)

# User correction cues (§23.4)
_CORRECTION_RE = re.compile(
    r"(不对|应该是|其实|你搞错了|过时了|\bno\b|\bactually\b|\bwrong\b)",
    re.I,
)
_FEAT_RE = re.compile(
    r"(能不能|希望你能|为什么不能|can you also|could you also)",
    re.I,
)
_E_CODE_RE = re.compile(r"\bE_[A-Z0-9_]+\b")


@dataclass
class HookEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    records: list[LearningRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload": dict(self.payload),
            "records": [r.to_dict() for r in self.records],
        }


class EvolutionHooks:
    """Built-in mount points matching §23.4."""

    def __init__(self, store: LearningStore | Path | str) -> None:
        if isinstance(store, LearningStore):
            self.store = store
        else:
            self.store = LearningStore(store)
        self.events: list[HookEvent] = []

    def on_user_message(
        self,
        text: str,
        *,
        task_uid: str = "",
        classification: str = "normal",
    ) -> list[LearningRecord]:
        out: list[LearningRecord] = []
        if _CORRECTION_RE.search(text or ""):
            rec = make_lrn(
                category="correction",
                summary="User correction signal detected",
                details=_short(text),
                area="agent",
                pattern_key="user.correction.generic",
                related=[task_uid] if task_uid else [],
                evidence=["user_message"],
            )
            rec.classification = classification
            out.append(self.store.append(rec))
        if _FEAT_RE.search(text or ""):
            feat = LearningRecord(
                kind="FEAT",
                category="feature_request",
                summary="Feature request signal detected",
                details=_short(text),
                area="ui",
                pattern_key="feat.user_request",
                related=[task_uid] if task_uid else [],
                evidence=["user_message"],
                classification=classification,
            )
            out.append(self.store.append(feat))
        self.events.append(HookEvent("on_user_message", {"text": _short(text)}, out))
        return out

    def on_tool_result(
        self,
        call: dict[str, Any],
        result: dict[str, Any],
        *,
        task_uid: str = "",
    ) -> list[LearningRecord]:
        """Failure → ERR. Success alone does not write (best_practice needs accept signal)."""
        out: list[LearningRecord] = []
        if _is_tool_failure(result):
            name = str(call.get("name") or result.get("tool") or "unknown")
            err_code = _extract_error_code(result)
            summary = f"Tool failure: {name}"
            if err_code:
                summary = f"Tool failure: {name} ({err_code})"
            area = "integration" if name.startswith(("mcp.", "integration_", "web_", "tavily")) else "tools"
            rec = make_err(
                summary=summary,
                details=_short(str(result)),
                area=area,
                pattern_key=f"tools.{name}.failure",
                related=[task_uid] if task_uid else [],
                evidence=["tool_result"],
                priority="high" if err_code else "medium",
            )
            out.append(self.store.append(rec))
        self.events.append(
            HookEvent(
                "on_tool_result",
                {"call": call, "ok": not _is_tool_failure(result)},
                out,
            )
        )
        return out

    def on_approval_decision(
        self,
        decision: dict[str, Any],
        *,
        task_uid: str = "",
    ) -> list[LearningRecord]:
        out: list[LearningRecord] = []
        dec = str(decision.get("decision") or "").lower()
        if dec in ("rejected", "reject", "denied"):
            rec = make_lrn(
                category="correction",
                summary="Approval rejected",
                details=_short(str(decision.get("reason") or decision)),
                area="agent",
                pattern_key="approval.rejected",
                related=[task_uid] if task_uid else [],
                evidence=["approval_decision"],
            )
            out.append(self.store.append(rec))
        self.events.append(HookEvent("on_approval_decision", decision, out))
        return out

    def on_task_end(
        self,
        session: dict[str, Any],
        *,
        outcome: str = "",
    ) -> list[LearningRecord]:
        """Record-only bookmark for P9 reflection — no model call."""
        # LW-39: do not invent lessons; only stamp a lightweight trail if failures exist
        out: list[LearningRecord] = []
        self.events.append(
            HookEvent(
                "on_task_end",
                {"session_id": session.get("session_id"), "outcome": outcome},
                out,
            )
        )
        return out

    def on_feedback(
        self,
        feedback: dict[str, Any],
        *,
        task_uid: str = "",
    ) -> list[LearningRecord]:
        out: list[LearningRecord] = []
        score = feedback.get("score")
        edit_rate = float(feedback.get("edit_rate") or 0)
        if (isinstance(score, (int, float)) and score <= 2) or edit_rate >= 0.4:
            rec = make_lrn(
                category="quality",
                summary="Low score or high edit rate",
                details=_short(str(feedback)),
                area="agent",
                pattern_key="feedback.quality.low",
                related=[task_uid] if task_uid else [],
                evidence=["user_feedback"],
            )
            out.append(self.store.append(rec))
        elif edit_rate <= 0.1 and feedback.get("accepted"):
            rec = make_lrn(
                category="best_practice",
                summary="Accepted with low edit rate",
                details=_short(str(feedback)),
                area="agent",
                pattern_key="feedback.best_practice",
                related=[task_uid] if task_uid else [],
                evidence=["user_feedback"],
            )
            out.append(self.store.append(rec))
        self.events.append(HookEvent("on_feedback", feedback, out))
        return out

    def on_model_error(
        self,
        error: dict[str, Any],
        *,
        task_uid: str = "",
    ) -> list[LearningRecord]:
        rec = make_err(
            summary=f"Model/routing error: {error.get('kind') or 'unknown'}",
            details=_short(str(error)),
            area="routing",
            pattern_key=f"routing.{error.get('kind') or 'error'}",
            related=[task_uid] if task_uid else [],
            evidence=["model_error"],
            priority="high",
        )
        out = [self.store.append(rec)]
        self.events.append(HookEvent("on_model_error", error, out))
        return out

    def on_lint_report(
        self,
        report: dict[str, Any],
        *,
        task_uid: str = "",
    ) -> list[LearningRecord]:
        out: list[LearningRecord] = []
        issues = report.get("issues") or report.get("findings") or []
        if issues:
            rec = make_err(
                summary=f"Lint report with {len(issues)} issue(s)",
                details=_short(str(issues[:5])),
                area="wiki",
                pattern_key="wiki.lint.issues",
                related=[task_uid] if task_uid else [],
                evidence=["lint_report"],
            )
            out.append(self.store.append(rec))
        self.events.append(HookEvent("on_lint_report", {"count": len(issues)}, out))
        return out


def _short(text: str, n: int = 400) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _is_tool_failure(result: dict[str, Any]) -> bool:
    if result.get("ok") is False:
        return True
    output = result.get("output") if isinstance(result.get("output"), dict) else result
    if isinstance(output, dict):
        if output.get("ok") is False:
            return True
        if output.get("error") or output.get("timed_out"):
            return True
        code = output.get("exit_code")
        if code is not None and code != 0:
            return True
        err = str(output.get("error") or "")
        if _E_CODE_RE.search(err) or "context_mismatch" in err:
            return True
    blob = str(result)
    if "context_mismatch" in blob:
        return True
    return False


def _extract_error_code(result: dict[str, Any]) -> str | None:
    m = _E_CODE_RE.search(str(result))
    return m.group(0) if m else None


# Adapter so AgentLoop HookSink can use EvolutionHooks
class LoopHookAdapter:
    """Bridge EvolutionHooks to AgentLoop's HookSink protocol."""

    def __init__(self, hooks: EvolutionHooks, *, task_uid: str = "") -> None:
        self.hooks = hooks
        self.task_uid = task_uid

    def on_tool_result(self, call: Any, result: dict[str, Any]) -> None:
        payload = call.to_dict() if hasattr(call, "to_dict") else dict(call)
        self.hooks.on_tool_result(payload, result, task_uid=self.task_uid)

    def on_task_end(self, session: dict[str, Any]) -> None:
        self.hooks.on_task_end(session)

    def on_approval_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        return None
