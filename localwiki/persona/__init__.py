"""Work-style persona signals and traits (design §10)."""

from localwiki.persona.signals import (
    FORBIDDEN_TOPICS,
    WORK_STYLE_DIMENSIONS,
    StyleSignal,
    extract_style_signals,
)
from localwiki.persona.directives import (
    StyleDirectiveService,
    StyleDirectives,
    StyleFeatureFlags,
    compile_style_directives,
)
from localwiki.persona.ocean import (
    OceanReport,
    OceanService,
    OceanSettings,
    preview_style_answers,
)
from localwiki.persona.traits import (
    EvidenceGateConfig,
    ObservingBucket,
    PersonaTraitCandidate,
    TraitAggregateReport,
    aggregate_traits,
)

__all__ = [
    "FORBIDDEN_TOPICS",
    "WORK_STYLE_DIMENSIONS",
    "StyleSignal",
    "extract_style_signals",
    "EvidenceGateConfig",
    "ObservingBucket",
    "PersonaTraitCandidate",
    "TraitAggregateReport",
    "aggregate_traits",
    "StyleDirectiveService",
    "StyleDirectives",
    "StyleFeatureFlags",
    "compile_style_directives",
    "OceanReport",
    "OceanService",
    "OceanSettings",
    "preview_style_answers",
]
