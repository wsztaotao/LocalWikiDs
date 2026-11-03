"""Feedback loop after deliverable acceptance (§9.8).

User may rate (1–5), write text feedback, or edit the artifact. The system:
1. Diffs the edit → ``ChangeObservation`` (motive never invented);
2. Maps feedback to ``ExperiencePack.selected`` items;
3. Emits revision / counterexample / memory candidates;
4. Small-edit accepts → transfer evidence (one success ≠ proof for all).
"""

from __future__ import annotations

import difflib
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from localwiki.experience.compiler import (
    HEAVY_EDIT_RATIO,
    SMALL_EDIT_RATIO,
    ChangeObservation,
    ExperienceCompileCandidate,
)
from localwiki.experience.pack import ExperiencePack, SelectedEntry

FeedbackCategory = str  # error | preference | missing | praise | other


def _uid(prefix: str = "wfb") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def compute_edit_metrics(before: str, after: str) -> dict[str, Any]:
    """Structural/length metrics only — no motive."""
    b = before or ""
    a = after or ""
    len_before = len(b)
    len_after = len(a)
    if len_before <= 0:
        ratio = 1.0 if len_after > 0 else 0.0
    else:
        # SequenceMatcher ratio is similarity; edit_ratio = 1 - similarity
        sim = difflib.SequenceMatcher(None, b, a).ratio()
        ratio = round(1.0 - sim, 4)
    b_lines = b.splitlines()
    a_lines = a.splitlines()
    sm = difflib.SequenceMatcher(None, b_lines, a_lines)
    opcodes = sm.get_opcodes()
    lines_added = sum(
        j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("insert", "replace")
    )
    lines_removed = sum(
        i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag in ("delete", "replace")
    )
    return {
        "len_before": len_before,
        "len_after": len_after,
        "edit_ratio": ratio,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "chars_delta": len_after - len_before,
    }


def diff_hunks(before: str, after: str, *, max_hunks: int = 20) -> list[dict[str, str]]:
    """Unified-diff style hunks for evidence (truncated)."""
    lines = list(
        difflib.unified_diff(
            (before or "").splitlines(),
            (after or "").splitlines(),
            fromfile="delivered",
            tofile="user_revised",
            lineterm="",
        )
    )
    hunks: list[dict[str, str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@") and current:
            hunks.append({"text": "\n".join(current)[:500]})
            current = [line]
            if len(hunks) >= max_hunks:
                break
        else:
            current.append(line)
    if current and len(hunks) < max_hunks:
        hunks.append({"text": "\n".join(current)[:500]})
    return hunks


@dataclass
class WorkFeedback:
    """User feedback on a deliverable (§9.2 Work Feedback)."""

    feedback_uid: str
    task_uid: str
    artifact_version: str
    rating: int | None = None  # 1–5
    text: str = ""
    category: FeedbackCategory = "other"
    accepted: bool = False
    delivered_text: str = ""
    revised_text: str | None = None
    experience_pack_uid: str | None = None
    reviewer: str = "user"

    def __post_init__(self) -> None:
        if self.rating is not None:
            self.rating = max(1, min(5, int(self.rating)))
        if not self.feedback_uid:
            self.feedback_uid = _uid("wfb")

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_uid": self.feedback_uid,
            "task_uid": self.task_uid,
            "artifact_version": self.artifact_version,
            "rating": self.rating,
            "text": self.text,
            "category": self.category,
            "accepted": self.accepted,
            "experience_pack_uid": self.experience_pack_uid,
            "reviewer": self.reviewer,
            "has_revision": self.revised_text is not None,
        }


@dataclass
class TransferEvidence:
    experience_uid: str
    version_sha: str
    feedback_uid: str
    edit_ratio: float | None
    note: str = "one_success_not_proof_for_all"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeedbackProcessResult:
    feedback: WorkFeedback
    observation: ChangeObservation | None
    candidates: list[ExperienceCompileCandidate] = field(default_factory=list)
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)
    transfers: list[TransferEvidence] = field(default_factory=list)
    linked_experience_uids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback": self.feedback.to_dict(),
            "observation": self.observation.to_dict() if self.observation else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "memory_candidates": list(self.memory_candidates),
            "transfers": [t.to_dict() for t in self.transfers],
            "linked_experience_uids": list(self.linked_experience_uids),
            "issues": list(self.issues),
        }


def _classify_text_feedback(text: str) -> FeedbackCategory:
    t = (text or "").lower()
    if re.search(r"错|错误|不对|bug|incorrect|wrong", t):
        return "error"
    if re.search(r"喜欢|偏好|希望|请用|不要|prefer|please", t):
        return "preference"
    if re.search(r"缺少|遗漏|补充|missing|add", t):
        return "missing"
    if re.search(r"好|不错|满意|赞|great|good|lgtm", t):
        return "praise"
    return "other"


def _link_selected(
    pack: ExperiencePack | None,
    feedback: WorkFeedback,
) -> list[SelectedEntry]:
    if pack is None:
        return []
    # All selected are potentially linked; text may mention a uid
    linked = list(pack.selected)
    if feedback.text:
        mentioned = [s for s in pack.selected if s.uid in feedback.text]
        if mentioned:
            return mentioned
    return linked


def process_feedback(
    feedback: WorkFeedback,
    *,
    pack: ExperiencePack | None = None,
    session_uid: str | None = None,
) -> FeedbackProcessResult:
    """Run §9.8 pipeline. Always emits ChangeObservation when a revision exists."""
    if not feedback.feedback_uid:
        feedback.feedback_uid = _uid("wfb")
    if feedback.category == "other" and feedback.text:
        feedback.category = _classify_text_feedback(feedback.text)

    result = FeedbackProcessResult(feedback=feedback, observation=None)
    linked = _link_selected(pack, feedback)
    result.linked_experience_uids = [s.uid for s in linked]

    metrics: dict[str, Any] = {}
    ratio: float | None = None
    if feedback.revised_text is not None:
        metrics = compute_edit_metrics(feedback.delivered_text, feedback.revised_text)
        ratio = float(metrics["edit_ratio"])
        hunks = diff_hunks(feedback.delivered_text, feedback.revised_text)
        metrics["hunk_count"] = len(hunks)
        obs = ChangeObservation(
            observation_uid=_uid("chobs"),
            task_uid=feedback.task_uid,
            session_uid=session_uid,
            edit_ratio=ratio,
            metrics={**metrics, "hunks_sample": hunks[:3]},
            note="diff recorded; motive not inferred",
            motive_inferred=False,
        )
        result.observation = obs
    elif feedback.rating is not None or feedback.text:
        # Soft observation without text diff
        result.observation = ChangeObservation(
            observation_uid=_uid("chobs"),
            task_uid=feedback.task_uid,
            session_uid=session_uid,
            edit_ratio=None,
            metrics={"rating": feedback.rating, "has_text": bool(feedback.text)},
            note="feedback without revision text; no diff motive",
            motive_inferred=False,
        )

    accepted = feedback.accepted or (
        feedback.rating is not None and feedback.rating >= 4
    )
    small = ratio is not None and ratio <= SMALL_EDIT_RATIO
    heavy = ratio is not None and ratio >= HEAVY_EDIT_RATIO

    # 4) transfer evidence on accept + small edit
    if accepted and small and linked:
        for sel in linked:
            result.transfers.append(
                TransferEvidence(
                    experience_uid=sel.uid,
                    version_sha=sel.version_sha,
                    feedback_uid=feedback.feedback_uid,
                    edit_ratio=ratio,
                )
            )
            result.candidates.append(
                ExperienceCompileCandidate(
                    candidate_uid=_uid("xfer"),
                    signal="transfer_evidence",
                    kind="transfer_evidence",
                    title=f"Transfer for {sel.uid}",
                    summary=(
                        f"Accepted with edit_ratio={ratio}; "
                        f"transfer evidence for {sel.uid} only "
                        "(not proof that all selected experiences are correct)."
                    ),
                    origin="derived_from_agent_trace",
                    confirmation_state="unconfirmed",
                    evidence_grade="transfer_verified",
                    evidence_refs=[
                        f"feedback:{feedback.feedback_uid}",
                        f"exp:{sel.uid}",
                    ],
                    metadata={
                        "experience_uid": sel.uid,
                        "one_success_not_proof_for_all": True,
                        "edit_ratio": ratio,
                    },
                    change_observation=result.observation,
                )
            )

    # Heavy edit → quality / preference revision candidate
    if heavy and result.observation:
        result.candidates.append(
            ExperienceCompileCandidate(
                candidate_uid=_uid("rev"),
                signal="heavy_edit",
                kind="change_observation",
                title="Revision from heavy user edit",
                summary=(
                    f"User heavily revised artifact (edit_ratio={ratio}). "
                    "Propose quality-standard / writing preference update; "
                    "diff does not prove motive."
                ),
                origin="derived_from_agent_trace",
                confirmation_state="unconfirmed",
                evidence_grade="single_case",
                evidence_refs=[f"feedback:{feedback.feedback_uid}"],
                metadata={
                    "suggested_kinds": ["rubric", "preference", "rule"],
                    "linked_experiences": result.linked_experience_uids,
                },
                change_observation=result.observation,
            )
        )
        # Linked experiences may need revision candidates
        for sel in linked:
            result.candidates.append(
                ExperienceCompileCandidate(
                    candidate_uid=_uid("exprev"),
                    signal="heavy_edit",
                    kind="rule",
                    title=f"Revise experience {sel.uid}",
                    summary=(
                        f"Feedback category={feedback.category} with heavy edit; "
                        f"consider revising {sel.uid} ({sel.kind})."
                    ),
                    origin="derived_from_agent_trace",
                    confirmation_state="unconfirmed",
                    evidence_grade="single_case",
                    evidence_refs=[
                        f"feedback:{feedback.feedback_uid}",
                        f"exp:{sel.uid}",
                    ],
                    metadata={"revises": sel.uid, "category": feedback.category},
                    change_observation=result.observation,
                )
            )

    # Error / negative rating → counterexample candidate
    if feedback.category == "error" or (
        feedback.rating is not None and feedback.rating <= 2
    ):
        for sel in linked or [
            SelectedEntry(uid="pack_context", version_sha="", kind="rule", why="", grade="")
        ]:
            if sel.uid == "pack_context" and not linked:
                continue
            result.candidates.append(
                ExperienceCompileCandidate(
                    candidate_uid=_uid("cex"),
                    signal="user_steer",
                    kind="counterexample",
                    title=f"Counterexample vs {sel.uid}",
                    summary=(
                        f"Negative feedback (rating={feedback.rating}, "
                        f"category={feedback.category}) against selected experience."
                    ),
                    origin="derived_from_agent_trace",
                    confirmation_state="unconfirmed",
                    evidence_grade="single_case",
                    evidence_refs=[
                        f"feedback:{feedback.feedback_uid}",
                        f"exp:{sel.uid}",
                    ],
                    metadata={"against": sel.uid, "feedback_text_len": len(feedback.text)},
                    change_observation=result.observation,
                )
            )

    # Preference text → memory candidate (not auto-confirmed)
    if feedback.category == "preference" and feedback.text.strip():
        result.memory_candidates.append(
            {
                "kind": "preference",
                "statement": feedback.text.strip()[:300],
                "origin": "observed",
                "confirmation_state": "proposed",
                "evidence_refs": [f"feedback:{feedback.feedback_uid}"],
                "risk_tier": "T1",
            }
        )

    if result.observation is None and feedback.revised_text is None:
        result.issues.append("no_revision_diff")

    return result


def process_feedback_batch(
    items: Sequence[WorkFeedback],
    *,
    pack: ExperiencePack | None = None,
) -> list[FeedbackProcessResult]:
    return [process_feedback(fb, pack=pack) for fb in items]
