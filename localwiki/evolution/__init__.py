"""Self-evolution scaffolding (design §23)."""

from localwiki.evolution.aggregate import PatternAggregator, observe_and_maybe_promote
from localwiki.evolution.hooks import EvolutionHooks
from localwiki.evolution.learnings import LearningStore

__all__ = [
    "EvolutionHooks",
    "LearningStore",
    "PatternAggregator",
    "observe_and_maybe_promote",
]
