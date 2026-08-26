"""Shared model output type."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.distributions import DistributionSummary
from app.schemas import Factor


@dataclass
class ModelOutput:
    """What every market model returns.

    `prob_higher` is the probability the stat finishes strictly above the posted line.
    The probability of the "lower" side is its complement -- Underdog lines are
    half-integers, so there is no push to account for.
    """

    projected_mean: float
    summary: DistributionSummary
    prob_higher: float
    confidence: float = 0.5
    factors: list[Factor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def probability_for(self, side: str) -> float:
        return self.prob_higher if side == "higher" else 1.0 - self.prob_higher
