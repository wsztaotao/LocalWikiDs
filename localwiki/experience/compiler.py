"""Agent trajectory + behavior → experience candidates (design §9.4–9.5).

Deterministic heuristic compiler (no LLM). Each §9.4 signal type has an
explicit threshold; candidates stay ``unconfirmed`` until user calibration.
Unconfirmed items must not be treated as active experience (publish gate).
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

from localwiki.behavior_log import BehaviorEvent, BehaviorLog

# §9.4 thresholds
PLAN_REVISION_MIN = 2
STEER_BROAD_MIN = 2
TOOL_REJECT_MIN = 3
BEHAVIOR_MIN_N = 8
BEHAVIOR_MIN_SPAN_DAYS = 14

# Edit magnitude heuristics for accept / heavy-edit signals
SMALL_EDIT_RATIO = 0.15
HEAVY_EDIT_RATIO = 0.35

SignalKind = Literal[
    "plan_revision",
    "user_steer",
    "tool_reject",
    "transfer_evidence",
    "heavy_edit",
    "task_failure",
    "behavior_tendency",
]

CandidateKind = Literal[
    "procedure",
    "rule",
    "counterexample",
    "rubric",
    "preference",
    "transfer_evidence",
    "change_observation",
    "failure_analysis",
]

Origin = Literal["derived_from_agent_trace", "derived_from_behavior"]


def _uid(prefix: str = "expc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _edit_ratio(metrics: dict[str, Any] | None) -> float | None:
    """Return 0..1 edit ratio from feedback/metrics, or None if unknown."""
    if not metrics:
        return None
    if "edit_ratio" in metrics:
        try:
            return max(0.0, min(1.0, float(metrics["edit_ratio"])))
        except (TypeError, ValueError):
            return None
    before = metrics.get("len_before")
    after = metrics.get("len_after")
    if before is None or after is None:
        return None
    try:
        b = float(before)
        a = float(after)
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return 1.0 if a > 0 else 0.0
    return min(1.0, abs(a - b) / b)


@dataclass
class ChangeObservation:
    """Version / delivery diff observation (§9.5). Diff alone does not prove motive."""

    observation_uid: str
    task_uid: str | None
    session_uid: str | None
    edit_ratio: float | None
    metrics: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    motive_inferred: bool = False  # always False — do not invent motive

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperienceCompileCandidate:
    candidate_uid: str
    signal: SignalKind | str
    kind: CandidateKind | str
    title: str
    summary: str
    origin: Origin | str
    confirmation_state: str = "unconfirmed"
    evidence_grade: str = "single_case"
    evidence_refs: list[str] = field(default_factory=list)
    applicability: dict[str, Any] = field(default_factory=dict)
    scope: str = "narrow"  # narrow | broad
    lifecycle: str = "proposed"
    blocked: bool = False
    block_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    change_observation: ChangeObservation | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class ExperienceCompileReport:
    """Output of one compile pass — candidates only; no wiki writes."""

    candidates: list[ExperienceCompileCandidate] = field(default_factory=list)
    observations: list[ChangeObservation] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    stages: dict[str, Any] = field(default_factory=dict)

    def by_signal(self, signal: str) -> list[ExperienceCompileCandidate]:
        return [c for c in self.candidates if c.signal == signal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "observations": [o.to_dict() for o in self.observations],
            "issues": list(self.issues),
            "stages": dict(self.stages),
        }


def load_agent_session(path: Path | str) -> list[dict[str, Any]]:
    """Load ``raw/agent-sessions/<session_uid>.jsonl`` events."""
    p = Path(path)
    events: list[dict[str, Any]] = []
    if not p.is_file():
        return events
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _session_meta(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    task_type = None
    task_uid = None
    session_uid = None
    used_experience_uids: list[str] = []
    for ev in events:
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
        task_type = task_type or ev.get("task_type") or payload.get("task_type") or meta.get(
            "task_type"
        )
        task_uid = task_uid or ev.get("task_uid") or payload.get("task_uid")
        session_uid = session_uid or ev.get("session_uid") or payload.get("session_uid")
        for key in ("experience_uids", "used_experiences", "selected_experience_uids"):
            vals = ev.get(key) or payload.get(key) or meta.get(key)
            if isinstance(vals, list):
                used_experience_uids.extend(str(x) for x in vals)
    return {
        "task_type": task_type or "unknown",
        "task_uid": task_uid,
        "session_uid": session_uid,
        "used_experience_uids": list(dict.fromkeys(used_experience_uids)),
    }


def _is_plan_modification(ev: dict[str, Any]) -> bool:
    et = str(ev.get("event") or ev.get("type") or ev.get("kind") or "")
    if et == "plan_update":
        return True
    if et == "plan_set" and ev.get("user_modified"):
        return True
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    if et in ("plan_set", "plan_update") and (
        payload.get("user_modified")
        or payload.get("steps_added")
        or payload.get("steps_removed")
        or payload.get("steps_reordered")
    ):
        return True
    return bool(ev.get("user_modified"))


def _tool_reject_key(ev: dict[str, Any]) -> str | None:
    et = str(ev.get("event") or ev.get("type") or ev.get("kind") or "")
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    decision = str(
        ev.get("decision") or payload.get("decision") or ev.get("outcome") or ""
    ).lower()
    if et == "approval_decision" and decision in ("reject", "rejected", "deny", "denied"):
        tool = (
            ev.get("tool")
            or payload.get("tool")
            or payload.get("tool_name")
            or ev.get("tool_name")
            or payload.get("subject")
            or "unknown_tool"
        )
        tool_class = (
            ev.get("tool_class")
            or payload.get("tool_class")
            or payload.get("category")
            or str(tool).split(".")[0]
            or str(tool).split("_")[0]
        )
        return str(tool_class)
    if et == "tool_call" and decision in ("reject", "rejected"):
        return str(ev.get("tool") or payload.get("tool") or "unknown_tool")
    return None


def compile_agent_sessions(
    sessions: Sequence[Sequence[dict[str, Any]]],
    *,
    plan_revision_min: int = PLAN_REVISION_MIN,
    steer_broad_min: int = STEER_BROAD_MIN,
    tool_reject_min: int = TOOL_REJECT_MIN,
) -> ExperienceCompileReport:
    """Compile §9.4 agent-trajectory signals into experience candidates."""
    report = ExperienceCompileReport()
    report.stages["attribution"] = "agent_session"
    report.stages["case_rebuild"] = True

    plan_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    steers_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejects_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for events in sessions:
        if not events:
            continue
        meta = _session_meta(events)
        task_type = str(meta["task_type"])
        session_uid = meta.get("session_uid")
        task_uid = meta.get("task_uid")
        refs_base = [
            f"session:{session_uid}" if session_uid else None,
            f"task:{task_uid}" if task_uid else None,
        ]
        refs_base = [r for r in refs_base if r]

        # --- plan revisions ---
        for ev in events:
            if _is_plan_modification(ev):
                plan_by_type[task_type].append(
                    {"event": ev, "meta": meta, "refs": list(refs_base)}
                )

        # --- user_steer ---
        for ev in events:
            et = str(ev.get("event") or ev.get("type") or ev.get("kind") or "")
            if et == "user_steer":
                steers_by_type[task_type].append(
                    {"event": ev, "meta": meta, "refs": list(refs_base)}
                )

        # --- tool rejects ---
        for ev in events:
            key = _tool_reject_key(ev)
            if key:
                rejects_by_class[key].append(
                    {
                        "event": ev,
                        "meta": meta,
                        "refs": list(refs_base),
                        "task_type": task_type,
                    }
                )

        # --- feedback / artifact accept or heavy edit ---
        feedback_events = [
            ev
            for ev in events
            if str(ev.get("event") or ev.get("type") or ev.get("kind") or "")
            in ("user_feedback", "artifact_feedback")
        ]
        for ev in feedback_events:
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            metrics = dict(ev.get("metrics") or payload.get("metrics") or {})
            rating = ev.get("rating", payload.get("rating"))
            accepted = bool(
                ev.get("accepted", payload.get("accepted", False))
                or (isinstance(rating, (int, float)) and float(rating) >= 4)
                or str(ev.get("outcome") or payload.get("outcome") or "").lower()
                in ("accept", "accepted", "approve", "approved")
            )
            ratio = _edit_ratio(metrics)
            obs = ChangeObservation(
                observation_uid=_uid("chobs"),
                task_uid=str(task_uid) if task_uid else None,
                session_uid=str(session_uid) if session_uid else None,
                edit_ratio=ratio,
                metrics=metrics,
                note="diff recorded; motive not inferred",
                motive_inferred=False,
            )
            report.observations.append(obs)

            used = list(meta.get("used_experience_uids") or [])
            used.extend(
                str(x)
                for x in (
                    ev.get("experience_uids")
                    or payload.get("experience_uids")
                    or []
                )
                if x
            )
            used = list(dict.fromkeys(used))

            if accepted and ratio is not None and ratio <= SMALL_EDIT_RATIO:
                # transfer evidence — always record (each success)
                report.candidates.append(
                    ExperienceCompileCandidate(
                        candidate_uid=_uid("xfer"),
                        signal="transfer_evidence",
                        kind="transfer_evidence",
                        title=f"Transfer evidence for {task_type}",
                        summary=(
                            f"Artifact accepted with small edit_ratio={ratio:.2f}; "
                            f"record transfer for used experiences ({len(used) or 'none listed'})."
                        ),
                        origin="derived_from_agent_trace",
                        confirmation_state="unconfirmed",
                        evidence_grade="transfer_verified",
                        evidence_refs=refs_base
                        + [f"ev:{ev.get('ev_uid') or ev.get('uid') or 'feedback'}"],
                        applicability={"task_types": [task_type]},
                        scope="narrow",
                        metadata={
                            "edit_ratio": ratio,
                            "experience_uids": used,
                            "one_success_not_proof_for_all": True,
                        },
                        change_observation=obs,
                    )
                )
            elif ratio is not None and ratio >= HEAVY_EDIT_RATIO:
                report.candidates.append(
                    ExperienceCompileCandidate(
                        candidate_uid=_uid("hedit"),
                        signal="heavy_edit",
                        kind="change_observation",
                        title=f"Heavy edit on {task_type} deliverable",
                        summary=(
                            f"Deliverable heavily modified (edit_ratio={ratio:.2f}). "
                            "Candidate for quality standard / writing preference; "
                            "diff does not prove motive."
                        ),
                        origin="derived_from_agent_trace",
                        confirmation_state="unconfirmed",
                        evidence_grade="single_case",
                        evidence_refs=refs_base
                        + [f"ev:{ev.get('ev_uid') or ev.get('uid') or 'feedback'}"],
                        applicability={"task_types": [task_type]},
                        scope="narrow",
                        metadata={"edit_ratio": ratio, "suggested_kinds": ["rubric", "preference"]},
                        change_observation=obs,
                    )
                )

        # --- task failure / abandoned ---
        for ev in events:
            et = str(ev.get("event") or ev.get("type") or ev.get("kind") or "")
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            if et != "session_end":
                continue
            status = str(
                ev.get("status") or payload.get("status") or ev.get("result") or ""
            ).lower()
            if status not in (
                "failed",
                "failure",
                "abandoned",
                "cancelled",
                "canceled",
                "aborted",
            ):
                continue
            user_reason = (
                ev.get("user_reason")
                or payload.get("user_reason")
                or ev.get("failure_reason_confirmed")
                or payload.get("failure_reason_confirmed")
            )
            category = (
                ev.get("failure_category")
                or payload.get("failure_category")
                or "unclassified"
            )
            # Categories per §9.4: fact error, missing constraint, experience
            # inapplicable, execution issue, format preference
            blocked = not bool(user_reason)
            report.candidates.append(
                ExperienceCompileCandidate(
                    candidate_uid=_uid("fail"),
                    signal="task_failure",
                    kind="failure_analysis",
                    title=f"Failure analysis ({status}) — {task_type}",
                    summary=(
                        f"Session ended as {status}; provisional category={category}. "
                        + (
                            "User confirmed reason; may affect experience."
                            if user_reason
                            else "Awaiting user-confirmed reason before affecting experience."
                        )
                    ),
                    origin="derived_from_agent_trace",
                    confirmation_state="unconfirmed",
                    evidence_grade="single_case",
                    evidence_refs=refs_base
                    + [f"ev:{ev.get('ev_uid') or ev.get('uid') or 'session_end'}"],
                    applicability={"task_types": [task_type]},
                    scope="narrow",
                    blocked=blocked,
                    block_reason=(
                        "needs_user_confirmed_failure_reason" if blocked else None
                    ),
                    metadata={
                        "status": status,
                        "failure_category": category,
                        "user_reason": user_reason,
                        "affects_experience": bool(user_reason),
                        "categories": [
                            "fact_error",
                            "missing_constraint",
                            "experience_inapplicable",
                            "execution_issue",
                            "format_preference",
                        ],
                    },
                )
            )

    # Aggregate plan revisions (≥2 same task_type)
    for task_type, items in plan_by_type.items():
        if len(items) < plan_revision_min:
            report.stages.setdefault("plan_revision_held", []).append(
                {"task_type": task_type, "count": len(items), "min": plan_revision_min}
            )
            continue
        refs: list[str] = []
        for it in items:
            refs.extend(it["refs"])
        report.candidates.append(
            ExperienceCompileCandidate(
                candidate_uid=_uid("proc"),
                signal="plan_revision",
                kind="procedure",
                title=f"Procedure revision for {task_type}",
                summary=(
                    f"User modified plan steps on {len(items)} {task_type} sessions "
                    f"(≥{plan_revision_min}). Propose Procedure step revisions."
                ),
                origin="derived_from_agent_trace",
                confirmation_state="unconfirmed",
                evidence_grade="multiple_independent_cases"
                if len(items) >= 2
                else "single_case",
                evidence_refs=list(dict.fromkeys(refs)),
                applicability={"task_types": [task_type]},
                scope="narrow",
                metadata={"occurrence_count": len(items)},
            )
        )

    # Steer: 1 → narrow rule/counterexample; 2+ → broader
    for task_type, items in steers_by_type.items():
        count = len(items)
        scope = "broad" if count >= steer_broad_min else "narrow"
        kind: CandidateKind = "counterexample" if count == 1 else "rule"
        refs = []
        for it in items:
            refs.extend(it["refs"])
            ev = it["event"]
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            steer_text = str(
                ev.get("steer") or payload.get("steer") or payload.get("text") or ""
            )[:120]
            if steer_text:
                refs.append(f"steer:{steer_text[:40]}")
        report.candidates.append(
            ExperienceCompileCandidate(
                candidate_uid=_uid("steer"),
                signal="user_steer",
                kind=kind,
                title=f"Steer correction ({scope}) — {task_type}",
                summary=(
                    f"{count} user_steer event(s) on {task_type}. "
                    f"{'Narrow-scope' if scope == 'narrow' else 'Broad-scope'} "
                    f"{kind} candidate."
                ),
                origin="derived_from_agent_trace",
                confirmation_state="unconfirmed",
                evidence_grade=(
                    "multiple_independent_cases" if count >= 2 else "single_case"
                ),
                evidence_refs=list(dict.fromkeys(refs)),
                applicability={"task_types": [task_type]},
                scope=scope,
                metadata={"occurrence_count": count},
            )
        )

    # Tool rejects: ≥3 same class
    for tool_class, items in rejects_by_class.items():
        if len(items) < tool_reject_min:
            report.stages.setdefault("tool_reject_held", []).append(
                {
                    "tool_class": tool_class,
                    "count": len(items),
                    "min": tool_reject_min,
                }
            )
            continue
        refs = []
        task_types = set()
        for it in items:
            refs.extend(it["refs"])
            task_types.add(it["task_type"])
        report.candidates.append(
            ExperienceCompileCandidate(
                candidate_uid=_uid("trej"),
                signal="tool_reject",
                kind="rule",
                title=f"Tool constraint: avoid {tool_class}",
                summary=(
                    f"User rejected {tool_class} tool calls {len(items)} times "
                    f"(≥{tool_reject_min}). Propose tool preference / constraint Rule."
                ),
                origin="derived_from_agent_trace",
                confirmation_state="unconfirmed",
                evidence_grade="multiple_independent_cases",
                evidence_refs=list(dict.fromkeys(refs)),
                applicability={"task_types": sorted(task_types), "tools": [tool_class]},
                scope="narrow",
                metadata={
                    "tool_class": tool_class,
                    "occurrence_count": len(items),
                    "preferred_tools_hint": f"deprioritize:{tool_class}",
                },
            )
        )

    report.stages["publish_gate"] = "unconfirmed_must_not_act_as_confirmed"
    return report


def compile_behavior_stats(
    events: Iterable[BehaviorEvent] | Sequence[dict[str, Any]],
    *,
    min_n: int = BEHAVIOR_MIN_N,
    min_span_days: int = BEHAVIOR_MIN_SPAN_DAYS,
) -> ExperienceCompileReport:
    """Behavior → tendency candidates only (§9.4). Requires n≥8 and span≥14d."""
    report = ExperienceCompileReport()
    report.stages["attribution"] = "behavior_stats"

    normalized: list[tuple[datetime, BehaviorEvent | dict[str, Any]]] = []
    for item in events:
        if isinstance(item, BehaviorEvent):
            ts = _parse_ts(item.ts)
            if ts:
                normalized.append((ts, item))
        elif isinstance(item, dict):
            ts = _parse_ts(str(item.get("ts") or ""))
            if ts:
                normalized.append((ts, item))

    if not normalized:
        report.issues.append("no_behavior_events")
        return report

    # Group review edits by task_type for tendency rules
    by_task: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for ts, item in normalized:
        if isinstance(item, BehaviorEvent):
            cat = item.cat
            kind = item.kind
            metrics = dict(item.metrics)
            ctx = dict(item.ctx)
            ev_uid = item.ev_uid
        else:
            cat = str(item.get("cat") or "")
            kind = str(item.get("kind") or "")
            metrics = dict(item.get("metrics") or {})
            ctx = dict(item.get("ctx") or {})
            ev_uid = str(item.get("ev_uid") or "")
        if cat != "review" or kind not in (
            "proposal_edited",
            "proposal_accepted",
            "proposal_rejected",
        ):
            continue
        task_type = str(ctx.get("task_type") or "unknown")
        by_task[task_type].append(
            (ts, {"metrics": metrics, "kind": kind, "ev_uid": ev_uid, "ctx": ctx})
        )

    for task_type, items in by_task.items():
        n = len(items)
        times = [t for t, _ in items]
        span_days = (max(times) - min(times)).total_seconds() / 86400.0
        if n < min_n or span_days < min_span_days:
            report.stages.setdefault("behavior_held", []).append(
                {
                    "task_type": task_type,
                    "n": n,
                    "span_days": round(span_days, 2),
                    "min_n": min_n,
                    "min_span_days": min_span_days,
                }
            )
            continue

        # Tendency: average length reduction on proposal_edited
        ratios: list[float] = []
        for _, row in items:
            r = _edit_ratio(row["metrics"])
            if r is not None and row["kind"] == "proposal_edited":
                # reduction fraction when after < before
                m = row["metrics"]
                try:
                    b = float(m.get("len_before", 0))
                    a = float(m.get("len_after", 0))
                    if b > 0 and a < b:
                        ratios.append((b - a) / b)
                except (TypeError, ValueError):
                    pass
        if not ratios:
            report.issues.append(f"no_edit_ratios:{task_type}")
            continue
        avg_cut = sum(ratios) / len(ratios)
        pct = int(round(avg_cut * 100))
        refs = [f"ev:{row['ev_uid']}" for _, row in items if row.get("ev_uid")]
        report.candidates.append(
            ExperienceCompileCandidate(
                candidate_uid=_uid("btend"),
                signal="behavior_tendency",
                kind="rule",
                title=f"Tendency: keep {task_type} concise",
                summary=(
                    f"{task_type} proposals: user cut length by ~{pct}% on average "
                    f"(n={n}, span={span_days:.0f}d). Candidate rule: keep documents concise."
                ),
                origin="derived_from_behavior",
                confirmation_state="unconfirmed",
                evidence_grade="multiple_independent_cases",
                evidence_refs=refs[:32],
                applicability={"task_types": [task_type]},
                scope="narrow",
                metadata={
                    "n": n,
                    "span_days": round(span_days, 2),
                    "avg_cut_ratio": round(avg_cut, 4),
                    "statistical_only": True,
                },
            )
        )

    report.stages["publish_gate"] = "unconfirmed_must_not_act_as_confirmed"
    return report


class ExperienceCompiler:
    """Workspace-facing compiler: sessions under raw/agent-sessions + behavior log."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace)
        self.sessions_dir = self.workspace / "raw" / "agent-sessions"
        self.behavior = BehaviorLog(self.workspace)

    def load_sessions(self) -> list[list[dict[str, Any]]]:
        out: list[list[dict[str, Any]]] = []
        if not self.sessions_dir.is_dir():
            return out
        for path in sorted(self.sessions_dir.glob("*.jsonl")):
            events = load_agent_session(path)
            # Ensure session_uid on events
            for ev in events:
                ev.setdefault("session_uid", path.stem)
            out.append(events)
        return out

    def compile(
        self,
        *,
        include_behavior: bool = True,
        include_sessions: bool = True,
    ) -> ExperienceCompileReport:
        merged = ExperienceCompileReport()
        if include_sessions:
            sess_report = compile_agent_sessions(self.load_sessions())
            merged.candidates.extend(sess_report.candidates)
            merged.observations.extend(sess_report.observations)
            merged.issues.extend(sess_report.issues)
            merged.stages["sessions"] = sess_report.stages
        if include_behavior:
            beh_report = compile_behavior_stats(self.behavior.iter_events())
            merged.candidates.extend(beh_report.candidates)
            merged.issues.extend(beh_report.issues)
            merged.stages["behavior"] = beh_report.stages
        # §9.5: unconfirmed must not publish as confirmed
        for c in merged.candidates:
            if c.confirmation_state != "user_confirmed":
                c.lifecycle = "proposed"
        merged.stages["publish_gate"] = "unconfirmed_must_not_act_as_confirmed"
        return merged
