"""Probability calibration fitted from our own graded history.

A model can rank bets well and still state probabilities that are systematically wrong
-- claiming 70% on things that land 62% of the time. Ranking survives that; EV does not,
because EV is computed against a break-even threshold and a biased probability crosses
that threshold at the wrong place.

So once there is graded history, we fit a monotone (isotonic) correction per league and
market and publish the *corrected* number. Isotonic is the right choice here: it cannot
reorder our picks, it makes no assumption about the shape of the miscalibration, and it
degrades gracefully to near-identity when there is little to correct.

Until a market has `MIN_SAMPLES` graded picks, the transform is the identity and the
API flags the probability as uncalibrated, so the UI can say so rather than implying a
precision the platform has not earned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn_shim import IsotonicFit

#: Below this many graded picks a market's calibration is not trustworthy.
MIN_SAMPLES = 120


@dataclass
class Calibrator:
    """Per-(league, market) isotonic corrections."""

    fits: dict[tuple[str, str], IsotonicFit] = field(default_factory=dict)

    def apply(self, league: str, market: str, probability: float) -> tuple[float, bool]:
        """Return (calibrated probability, was_calibrated)."""
        fit = self.fits.get((league, market)) or self.fits.get((league, "*"))
        if fit is None:
            return probability, False
        return fit.predict(probability), True

    @property
    def is_empty(self) -> bool:
        return not self.fits


def fit_calibrator(records: list[tuple[str, str, float, bool]]) -> Calibrator:
    """Fit from `(league, market, predicted_probability, won)` tuples."""
    grouped: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for league, market, probability, won in records:
        grouped.setdefault((league, market), []).append((probability, 1.0 if won else 0.0))
        grouped.setdefault((league, "*"), []).append((probability, 1.0 if won else 0.0))

    fits: dict[tuple[str, str], IsotonicFit] = {}
    for key, pairs in grouped.items():
        if len(pairs) < MIN_SAMPLES:
            continue
        x = np.array([p for p, _ in pairs], dtype=float)
        y = np.array([o for _, o in pairs], dtype=float)
        fits[key] = IsotonicFit.fit(x, y)
    return Calibrator(fits=fits)
