"""Per-leg edge, EV, and the two ranking modes the UI toggles between.

The EV identity that makes this clean: in an N-leg entry paying M, where the other
legs sit exactly at break-even `b`, the entry's expected return is

    M * p * b^(N-1)  =  M * b^N * (p / b)  =  p / b

because `M * b^N = 1` is the definition of break-even. So a single leg's expected
return per dollar is simply **p / b**, and its EV is `p/b - 1`. That is directly
interpretable ("this leg returns $1.09 per dollar risked") and it is exact, not a
heuristic.

Ranking then differs by mode:

* **Best Value** ranks by edge, shrunk by confidence, so a thin-sample outlier cannot
  top the board on the strength of data we do not trust.
* **Most Likely** ranks by probability -- but still refuses to show negative-EV legs,
  because "very likely to hit" and "worth betting" are different questions and a tool
  that conflates them will happily recommend chalk that loses money over time.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import Side
from app.pricing.entry import PayoutStructure, break_even_probability

#: How much an unconfident projection is pulled back toward break-even when ranking.
#: At confidence 0 only a third of the nominal edge survives; at 1.0 all of it does.
CONFIDENCE_FLOOR = 0.35


@dataclass
class ReferenceEntry:
    """The entry shape per-leg EV is quoted against. Configurable in Settings."""

    entry_type: str = "standard"
    legs: int = 3
    structure: PayoutStructure | None = None

    def payouts(self) -> PayoutStructure:
        return self.structure or PayoutStructure()


@dataclass
class PricedEdge:
    break_even: float
    edge: float
    ev_per_dollar: float
    score: float


def price_leg(
    probability: float,
    payout_multiplier: float,
    confidence: float,
    reference: ReferenceEntry,
    mode: str = "value",
) -> PricedEdge:
    """Edge, EV and ranking score for one side of one line."""
    break_even = break_even_probability(
        reference.payouts(),
        entry_type=reference.entry_type,
        legs=reference.legs,
        leg_multiplier=payout_multiplier,
    )
    probability = min(max(probability, 1e-6), 1 - 1e-6)

    edge = probability - break_even
    ev_per_dollar = probability / max(break_even, 1e-9) - 1.0

    if mode == "likely":
        # Rank by raw probability, but never above a leg that loses money.
        score = probability if ev_per_dollar > 0 else probability - 1.0
    else:
        shrink = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * confidence
        score = edge * shrink

    return PricedEdge(
        break_even=round(break_even, 6),
        edge=round(edge, 6),
        ev_per_dollar=round(ev_per_dollar, 6),
        score=round(score, 6),
    )


def best_side(prob_higher: float) -> Side:
    """Which side of the line the model prefers."""
    return Side.HIGHER if prob_higher >= 0.5 else Side.LOWER


def probability_for_side(prob_higher: float, side: Side) -> float:
    return prob_higher if side is Side.HIGHER else 1.0 - prob_higher
