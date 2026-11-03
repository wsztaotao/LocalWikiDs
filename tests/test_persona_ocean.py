"""Tests for optional OCEAN + style preview (LW-24 / §10.3)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.persona.directives import (  # noqa: E402
    StyleDirectiveService,
    compile_style_directives,
)
from localwiki.persona.ocean import (  # noqa: E402
    OceanService,
    OceanSettings,
    preview_style_answers,
)
from localwiki.persona.traits import PersonaTraitCandidate  # noqa: E402


def test_ocean_default_disabled() -> None:
    svc = OceanService()
    assert svc.is_enabled() is False
    assert OceanSettings().enabled is False
    report = svc.score_self_report({"o1": 5, "c1": 4})
    assert report.enabled is False
    assert report.scores == []
    assert report.drives_features is False


def test_ocean_enabled_scores_do_not_drive_features() -> None:
    svc = OceanService()
    svc.enable()
    report = svc.score_self_report(
        {"o1": 5, "c1": 4, "e1": 3, "a1": 4, "n1": 2}
    )
    assert report.enabled is True
    assert report.scores
    assert report.drives_features is False
    assert all(s.drives_features is False for s in report.scores)

    feature_in = svc.as_feature_input(report)
    assert feature_in["drives_features"] is False
    assert feature_in["traits_for_directives"] == []

    # Compiling StyleDirectives must not consume OCEAN scores
    sd = compile_style_directives(
        [
            PersonaTraitCandidate(
                uid="trait_density_work",
                dimension="information_density",
                context="work",
                title="t",
                description="d",
                value_range=(1.0, 1.5),
                evidence_summary={},
                evidence_refs=[],
            )
        ]
    )
    assert "ocean" not in " ".join(sd.compiled_from).lower()
    applied = StyleDirectiveService(sd).apply("chat")
    assert "ocean" not in str(applied.fragment).lower()


def test_text_inference_labeled_not_clinical() -> None:
    svc = OceanService(OceanSettings(enabled=True))
    report = svc.infer_from_text(["我对新想法很感兴趣，做事有条理。"])
    assert report.enabled is True
    assert "不是心理测评" in report.disclaimer
    assert report.drives_features is False
    assert any(s.source == "text_inference" for s in report.scores)
    assert any("不是心理测评" in s.label for s in report.scores)


def test_preview_style_answers_contract() -> None:
    preview = preview_style_answers(
        "如何写技术方案？",
        with_style_prompt="回答先给结论，再用表格。",
    )
    assert preview["with_style"]["label"] == "使用风格"
    assert preview["without_style"]["label"] == "不使用风格"
    assert preview["with_style"]["system_addon"]
    assert preview["without_style"]["system_addon"] == ""


def test_style_js_exists_and_mentions_preview() -> None:
    text = (ROOT / "web" / "style.js").read_text(encoding="utf-8")
    assert "renderStylePreview" in text
    assert "使用风格" in text
    assert "不使用风格" in text
    assert "不驱动任何功能" in text
    assert "drives_features" in text
