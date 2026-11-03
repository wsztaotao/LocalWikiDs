"""Style signal extraction — self/user_authored only (design §10.4, §10.7).

Third-party speech and forbidden topics never produce signals.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Sequence

SpeakerRole = Literal["self", "other", "ai", "system", "unknown"]
MaterialRole = Literal[
    "user_authored",
    "user_revised",
    "collaborative",
    "reference_only",
    "ai_generated",
    "unknown",
]
SignalSource = Literal[
    "text",
    "behavior",
    "agent_trace",
    "direct_statement",
]

# §10.2 dimensions
WORK_STYLE_DIMENSIONS: tuple[str, ...] = (
    "decision_basis",
    "decision_speed",
    "risk_preference",
    "information_density",
    "expression_style",
    "structure_preference",
    "perspective",
    "autonomy_preference",
    "plan_granularity",
    "quality_orientation",
    "learning_style",
    "work_rhythm",
)

# §10.7 forbidden inference topics (discard on hit)
FORBIDDEN_TOPICS: tuple[str, ...] = (
    "health",
    "mental_health",
    "religion",
    "politics",
    "sexual_orientation",
    "ethnicity",
    "family",
    "finance",
    "criminal_record",
    "biometrics",
    "third_party_personality",
)

_FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("health", re.compile(r"健康|疾病|抑郁|焦虑|体检|mental\s*health|diagnos|illness", re.I)),
    ("mental_health", re.compile(r"心理疾病|精神病|自杀|PTSD|bipolar", re.I)),
    ("religion", re.compile(r"宗教|佛教|基督教|伊斯兰|信仰|church|mosque", re.I)),
    ("politics", re.compile(r"政党|选举|左派|右派|政治立场|democrat|republican", re.I)),
    ("sexual_orientation", re.compile(r"性取向|同性恋|LGBT|gay|lesbian", re.I)),
    ("ethnicity", re.compile(r"民族|种族|血统|ethnicity|race\b", re.I)),
    ("family", re.compile(r"配偶|离婚|孩子成绩|父母关系|家人隐私", re.I)),
    ("finance", re.compile(r"工资|存款|银行卡|负债|asset|salary\b|net\s*worth", re.I)),
    ("criminal_record", re.compile(r"犯罪记录|案底|坐牢|felony|arrest", re.I)),
    ("biometrics", re.compile(r"指纹|虹膜|基因|DNA|biometric", re.I)),
    (
        "third_party_personality",
        re.compile(r"(他|她|他们|同事\w*|对方).{0,8}(性格|人品|脾气|内向|外向)", re.I),
    ),
]

# Heuristic cue → (dimension, direction -2..+2, note)
_TEXT_CUES: list[tuple[re.Pattern[str], str, float, str]] = [
    (re.compile(r"简短|简洁|要点|精简|少写点|简短点", re.I), "information_density", 1.2, "prefers concise"),
    (re.compile(r"详细|详尽|展开说说|详细点|写全", re.I), "information_density", -1.2, "prefers detailed"),
    (re.compile(r"直接说|别绕|开门见山|直说", re.I), "expression_style", 1.2, "direct tone"),
    (re.compile(r"委婉|含蓄|注意措辞", re.I), "expression_style", -1.0, "soft tone"),
    (re.compile(r"用表格|列表|分点|bullet", re.I), "structure_preference", 1.2, "lists/tables"),
    (re.compile(r"写成段落|叙述一下", re.I), "structure_preference", -1.0, "narrative"),
    (re.compile(r"先给结论|先说结论", re.I), "decision_speed", 0.8, "conclusion first"),
    (re.compile(r"先分析再决定|充分论证", re.I), "decision_speed", -1.0, "analyze first"),
    (re.compile(r"数据|指标|证据|对比表", re.I), "decision_basis", 1.0, "data/evidence"),
    (re.compile(r"凭经验|直觉上", re.I), "decision_basis", -1.0, "intuition"),
    (re.compile(r"少问我|别老确认|自己决定", re.I), "autonomy_preference", 1.2, "less check-in"),
    (re.compile(r"先问我|每步确认|不要自作主张", re.I), "autonomy_preference", -1.2, "more check-in"),
    (re.compile(r"步骤细一点|拆细", re.I), "plan_granularity", 1.2, "fine plan"),
    (re.compile(r"大方向就行|粗一点|里程碑", re.I), "plan_granularity", -1.0, "coarse plan"),
    (re.compile(r"精益求精|再打磨|质量优先", re.I), "quality_orientation", 1.2, "polish"),
    (re.compile(r"够用就行|先交付|快速出", re.I), "quality_orientation", -1.0, "good enough"),
    (re.compile(r"先看例子|给个示例", re.I), "learning_style", -1.0, "example first"),
    (re.compile(r"先讲原理|为什么这样", re.I), "learning_style", 1.0, "principle first"),
    (re.compile(r"先讲全貌|全局", re.I), "perspective", -1.0, "overview first"),
    (re.compile(r"先讲细节|落到实现", re.I), "perspective", 1.0, "detail first"),
    (re.compile(r"可以大胆试|愿意冒险", re.I), "risk_preference", 1.0, "try new"),
    (re.compile(r"稳妥|保守|别冒险", re.I), "risk_preference", -1.2, "conservative"),
]

_DIRECT_STATEMENT = re.compile(
    r"(我喜欢|我不喜欢|我偏好|请以后|以后都|我希望你|请你总是|I (?:prefer|like|want))",
    re.I,
)

_ALLOWED_SPEAKERS = frozenset({"self"})
_ALLOWED_MATERIALS = frozenset({"user_authored", "user_revised"})


def _uid(prefix: str = "sig") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def hits_forbidden(text: str) -> str | None:
    """Return forbidden topic key if text matches §10.7 list."""
    if not text:
        return None
    for key, pat in _FORBIDDEN_PATTERNS:
        if pat.search(text):
            return key
    return None


def is_self_attributable(
    *,
    speaker_role: str | None = None,
    material_role: str | None = None,
) -> bool:
    """Only self / user_authored (and user_revised contributions) may yield signals."""
    if speaker_role in _ALLOWED_SPEAKERS:
        return True
    if material_role in _ALLOWED_MATERIALS:
        return True
    return False


@dataclass
class StyleSignal:
    signal_uid: str
    dimension: str
    direction: float  # -2 .. +2
    source: SignalSource | str
    evidence_ref: str
    excerpt: str = ""
    context: str = "work"
    note: str = ""
    speaker_role: str = "self"
    material_role: str | None = None
    discarded: bool = False
    discard_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SignalExtractReport:
    signals: list[StyleSignal] = field(default_factory=list)
    discarded: list[StyleSignal] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def kept(self) -> list[StyleSignal]:
        return [s for s in self.signals if not s.discarded]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [s.to_dict() for s in self.signals],
            "discarded": [s.to_dict() for s in self.discarded],
            "issues": list(self.issues),
        }


def _clip_direction(v: float) -> float:
    return max(-2.0, min(2.0, float(v)))


def _make_signal(
    *,
    dimension: str,
    direction: float,
    source: str,
    evidence_ref: str,
    excerpt: str,
    speaker_role: str,
    material_role: str | None,
    note: str = "",
    context: str = "work",
    metadata: dict[str, Any] | None = None,
) -> StyleSignal:
    forbidden = hits_forbidden(excerpt) or hits_forbidden(note)
    sig = StyleSignal(
        signal_uid=_uid("sig"),
        dimension=dimension,
        direction=_clip_direction(direction),
        source=source,
        evidence_ref=evidence_ref,
        excerpt=excerpt[:240],
        context=context,
        note=note,
        speaker_role=speaker_role,
        material_role=material_role,
        metadata=dict(metadata or {}),
    )
    if forbidden:
        sig.discarded = True
        sig.discard_reason = f"forbidden_topic:{forbidden}"
    if dimension not in WORK_STYLE_DIMENSIONS:
        sig.discarded = True
        sig.discard_reason = f"unknown_dimension:{dimension}"
    return sig


def extract_from_text(
    text: str,
    *,
    speaker_role: str = "unknown",
    material_role: str | None = None,
    evidence_ref: str = "text:unknown",
    context: str = "work",
) -> SignalExtractReport:
    report = SignalExtractReport()
    if not text or not text.strip():
        report.issues.append("empty_text")
        return report

    if not is_self_attributable(speaker_role=speaker_role, material_role=material_role):
        # Explicitly do not produce signals for third party / unknown / AI
        report.issues.append(
            f"skipped_non_self:speaker={speaker_role},material={material_role}"
        )
        return report

    forbidden = hits_forbidden(text)
    if forbidden:
        # §10.7: hit → discard, do not save (even if mixed with style cues)
        dummy = _make_signal(
            dimension="information_density",
            direction=0.0,
            source="text",
            evidence_ref=evidence_ref,
            excerpt=text[:120],
            speaker_role=speaker_role,
            material_role=material_role,
            note="blocked",
        )
        dummy.discarded = True
        dummy.discard_reason = f"forbidden_topic:{forbidden}"
        report.discarded.append(dummy)
        return report

    source: SignalSource = (
        "direct_statement" if _DIRECT_STATEMENT.search(text) else "text"
    )
    for pat, dim, direction, note in _TEXT_CUES:
        m = pat.search(text)
        if not m:
            continue
        # Expand excerpt around match
        start = max(0, m.start() - 20)
        end = min(len(text), m.end() + 40)
        excerpt = text[start:end]
        if hits_forbidden(excerpt):
            continue
        sig = _make_signal(
            dimension=dim,
            direction=direction,
            source=source,
            evidence_ref=evidence_ref,
            excerpt=excerpt,
            speaker_role=speaker_role or "self",
            material_role=material_role,
            note=note,
            context=context,
        )
        if sig.discarded:
            report.discarded.append(sig)
        else:
            report.signals.append(sig)
    return report


def extract_from_behavior_event(
    event: dict[str, Any] | Any,
    *,
    context: str = "work",
) -> SignalExtractReport:
    """Behavior metadata → weak style signals (never from body text)."""
    report = SignalExtractReport()
    if hasattr(event, "to_dict"):
        data = event.to_dict()
    elif isinstance(event, dict):
        data = event
    else:
        report.issues.append("invalid_event")
        return report

    cat = str(data.get("cat") or "")
    kind = str(data.get("kind") or "")
    metrics = dict(data.get("metrics") or {})
    ev_uid = str(data.get("ev_uid") or "ev")
    ref = f"ev:{ev_uid}"

    def add(dim: str, direction: float, note: str) -> None:
        sig = _make_signal(
            dimension=dim,
            direction=direction,
            source="behavior",
            evidence_ref=ref,
            excerpt=f"{cat}/{kind}",
            speaker_role="self",
            material_role="user_authored",
            note=note,
            context=context,
            metadata={"cat": cat, "kind": kind, "metrics": metrics},
        )
        if sig.discarded:
            report.discarded.append(sig)
        else:
            report.signals.append(sig)

    if cat == "review" and kind == "proposal_edited":
        try:
            before = float(metrics.get("len_before") or 0)
            after = float(metrics.get("len_after") or 0)
        except (TypeError, ValueError):
            before = after = 0.0
        if before > 0 and after < before * 0.85:
            add("information_density", 1.0, "cut length on review")
        elif before > 0 and after > before * 1.15:
            add("information_density", -0.8, "expanded on review")
        if metrics.get("sections_removed"):
            add("structure_preference", 0.6, "removed sections")

    if cat == "answer":
        if "short" in kind or kind in ("ask_shorter", "prefer_short"):
            add("information_density", 1.2, "asked shorter")
        if "detail" in kind or kind in ("ask_longer", "prefer_detail"):
            add("information_density", -1.2, "asked longer")

    if cat == "approval":
        decision = str(metrics.get("decision") or kind).lower()
        risk = str(metrics.get("risk") or metrics.get("risk_level") or "").lower()
        if decision in ("reject", "rejected") and risk in ("high", "r2", "r3"):
            add("risk_preference", -1.0, "rejected high-risk")
        if decision in ("approve", "approved") and risk in ("high", "r2", "r3"):
            add("risk_preference", 0.6, "approved high-risk")
        if kind in ("fast_decision",) or float(metrics.get("decision_seconds") or 999) < 30:
            add("decision_speed", 0.8, "fast approval")

    if cat == "task" and "steer" in kind:
        add("plan_granularity", 0.5, "steered plan")
        add("autonomy_preference", -0.4, "intervened")

    return report


def extract_from_agent_trace(
    events: Sequence[dict[str, Any]],
    *,
    context: str = "work",
) -> SignalExtractReport:
    """Only user_steer / user_feedback / approval_decision from the user."""
    report = SignalExtractReport()
    for ev in events:
        et = str(ev.get("event") or ev.get("type") or ev.get("kind") or "")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        # Steer text is user self
        if et == "user_steer":
            text = str(ev.get("steer") or payload.get("steer") or payload.get("text") or "")
            sub = extract_from_text(
                text,
                speaker_role="self",
                material_role="user_authored",
                evidence_ref=f"session:{ev.get('session_uid') or 'unknown'}/steer",
                context=context,
            )
            # Retag source
            for s in sub.signals:
                s.source = "agent_trace"
                report.signals.append(s)
            report.discarded.extend(sub.discarded)
            report.issues.extend(sub.issues)
        if et == "user_feedback":
            text = str(ev.get("text") or payload.get("text") or "")
            if text:
                sub = extract_from_text(
                    text,
                    speaker_role="self",
                    material_role="user_authored",
                    evidence_ref=f"session:{ev.get('session_uid') or 'unknown'}/feedback",
                    context=context,
                )
                for s in sub.signals:
                    s.source = "agent_trace"
                    report.signals.append(s)
                report.discarded.extend(sub.discarded)
        # Assistant / other speaker messages in trace must not be scanned as self
        if et in ("assistant_message", "other_message", "tool_result"):
            role = str(ev.get("speaker_role") or payload.get("speaker_role") or "other")
            text = str(ev.get("text") or payload.get("text") or "")
            if text and role != "self":
                sub = extract_from_text(
                    text,
                    speaker_role=role,
                    evidence_ref="trace:skipped",
                    context=context,
                )
                assert sub.signals == []
                report.issues.extend(sub.issues)
    return report


def extract_style_signals(
    *,
    texts: Iterable[dict[str, Any]] | None = None,
    behavior_events: Iterable[Any] | None = None,
    agent_sessions: Iterable[Sequence[dict[str, Any]]] | None = None,
    context: str = "work",
) -> SignalExtractReport:
    """Unified entry: merge signals from text / behavior / agent traces."""
    merged = SignalExtractReport()
    for item in texts or []:
        sub = extract_from_text(
            str(item.get("text") or ""),
            speaker_role=str(item.get("speaker_role") or "unknown"),
            material_role=item.get("material_role"),
            evidence_ref=str(item.get("evidence_ref") or "text"),
            context=str(item.get("context") or context),
        )
        merged.signals.extend(sub.signals)
        merged.discarded.extend(sub.discarded)
        merged.issues.extend(sub.issues)
    for ev in behavior_events or []:
        sub = extract_from_behavior_event(ev, context=context)
        merged.signals.extend(sub.signals)
        merged.discarded.extend(sub.discarded)
        merged.issues.extend(sub.issues)
    for session in agent_sessions or []:
        sub = extract_from_agent_trace(session, context=context)
        merged.signals.extend(sub.signals)
        merged.discarded.extend(sub.discarded)
        merged.issues.extend(sub.issues)
    return merged
