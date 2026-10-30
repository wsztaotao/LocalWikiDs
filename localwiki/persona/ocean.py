"""Optional OCEAN reference view (§10.3).

Default: disabled. Results never drive StyleDirectives or any product feature.
Text inference is low-evidence and must be labeled as non-clinical.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

OCEAN_TRAITS: tuple[str, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
)

# Minimal IPIP-style self-report items (illustrative subset for scaffold tests).
# Real deployments may load a full public-domain inventory; this is not a clinical tool.
IPIP_SAMPLE_ITEMS: tuple[dict[str, Any], ...] = (
    {"id": "o1", "trait": "openness", "text": "我对新想法很感兴趣。", "keyed": "+"},
    {"id": "c1", "trait": "conscientiousness", "text": "我做事有条理。", "keyed": "+"},
    {"id": "e1", "trait": "extraversion", "text": "我在社交场合感到精力充沛。", "keyed": "+"},
    {"id": "a1", "trait": "agreeableness", "text": "我同情别人的感受。", "keyed": "+"},
    {"id": "n1", "trait": "neuroticism", "text": "我容易感到焦虑。", "keyed": "+"},
)

_TEXT_HINTS: dict[str, list[re.Pattern[str]]] = {
    "openness": [re.compile(r"新想法|创新|探索|好奇", re.I)],
    "conscientiousness": [re.compile(r"有条理|计划|准时|认真", re.I)],
    "extraversion": [re.compile(r"外向|社交|健谈", re.I)],
    "agreeableness": [re.compile(r"合作|体谅|友善", re.I)],
    "neuroticism": [re.compile(r"焦虑|紧张|情绪波动", re.I)],
}


def _uid(prefix: str = "ocean") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class OceanSettings:
    """OCEAN is opt-in; default False."""

    enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> OceanSettings:
        if not data:
            return cls()
        return cls(enabled=bool(data.get("enabled", False)))


@dataclass
class OceanScore:
    trait: str
    value: float  # 0..1 normalized rough score
    source: str  # self_report | text_inference
    label: str
    drives_features: bool = False  # always False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OceanReport:
    report_uid: str
    enabled: bool
    scores: list[OceanScore] = field(default_factory=list)
    disclaimer: str = (
        "文本推断，不是心理测评。OCEAN 仅供用户理解与校准工作风格维度，"
        "不直接驱动任何功能。"
    )
    drives_features: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_uid": self.report_uid,
            "enabled": self.enabled,
            "scores": [s.to_dict() for s in self.scores],
            "disclaimer": self.disclaimer,
            "drives_features": False,
        }


class OceanService:
    def __init__(self, settings: OceanSettings | None = None) -> None:
        self.settings = settings or OceanSettings()

    def enable(self) -> None:
        self.settings.enabled = True

    def disable(self) -> None:
        self.settings.enabled = False

    def is_enabled(self) -> bool:
        return bool(self.settings.enabled)

    def sample_items(self) -> list[dict[str, Any]]:
        return [dict(x) for x in IPIP_SAMPLE_ITEMS]

    def score_self_report(
        self,
        answers: dict[str, int],
    ) -> OceanReport:
        """Score voluntary Likert answers (1–5). No-op when disabled."""
        if not self.settings.enabled:
            return OceanReport(
                report_uid=_uid("ocean"),
                enabled=False,
                scores=[],
                disclaimer="OCEAN 默认关闭；开启后才计算。结果不驱动任何功能。",
            )
        buckets: dict[str, list[float]] = {t: [] for t in OCEAN_TRAITS}
        for item in IPIP_SAMPLE_ITEMS:
            raw = answers.get(item["id"])
            if raw is None:
                continue
            v = max(1, min(5, int(raw)))
            if item["keyed"] == "-":
                v = 6 - v
            buckets[item["trait"]].append(v / 5.0)
        scores = []
        for trait, vals in buckets.items():
            if not vals:
                continue
            scores.append(
                OceanScore(
                    trait=trait,
                    value=round(sum(vals) / len(vals), 3),
                    source="self_report",
                    label="自愿量表自评",
                    drives_features=False,
                )
            )
        return OceanReport(
            report_uid=_uid("ocean"),
            enabled=True,
            scores=scores,
            drives_features=False,
        )

    def infer_from_text(self, texts: Sequence[str]) -> OceanReport:
        """Low-evidence text inference — labeled, never drives features."""
        if not self.settings.enabled:
            return OceanReport(
                report_uid=_uid("ocean"),
                enabled=False,
                scores=[],
            )
        blob = "\n".join(texts or [])
        scores: list[OceanScore] = []
        for trait, pats in _TEXT_HINTS.items():
            hits = sum(1 for p in pats if p.search(blob))
            if hits:
                scores.append(
                    OceanScore(
                        trait=trait,
                        value=min(1.0, 0.4 + 0.2 * hits),
                        source="text_inference",
                        label="文本推断，不是心理测评",
                        drives_features=False,
                    )
                )
        return OceanReport(
            report_uid=_uid("ocean"),
            enabled=True,
            scores=scores,
            disclaimer=(
                "文本推断，不是心理测评。仅供参考校准工作风格；不驱动任何功能。"
            ),
            drives_features=False,
        )

    def as_feature_input(self, report: OceanReport) -> dict[str, Any]:
        """Explicit empty feature payload — OCEAN must not feed StyleDirectives."""
        return {
            "ocean_enabled": report.enabled,
            "drives_features": False,
            "traits_for_directives": [],  # always empty
            "scores_ignored_by_runtime": [s.to_dict() for s in report.scores],
        }


def preview_style_answers(
    question: str,
    *,
    with_style_prompt: str,
    without_style_prompt: str = "",
) -> dict[str, Any]:
    """Side-by-side preview contract for UI (§10.6 item 5).

    Does not call a model; returns prompt packages for the client to render.
    """
    return {
        "question": question,
        "with_style": {
            "label": "使用风格",
            "system_addon": with_style_prompt,
        },
        "without_style": {
            "label": "不使用风格",
            "system_addon": without_style_prompt or "",
        },
    }
