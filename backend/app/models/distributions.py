"""Outcome distributions for prop markets.

The choice of distribution is where most naive prop models go wrong, and it matters
most exactly where the money is -- in the tail near the line.

* **Counts** (strikeouts, receptions) are *overdispersed* relative to Poisson: real
  variance exceeds the mean, because the rate itself varies game to game (opponent,
  weather, how the manager feels). A Poisson model is systematically overconfident on
  both tails. We use a negative binomial with dispersion fitted to the expected spread.
* **Sums of independent, non-identical Bernoullis** (does this batter get a hit in any
  of his plate appearances) are Poisson-binomial, computed exactly -- it is cheap at
  these sizes and avoids a normal approximation that is bad at the extremes.
* **Yardage** is a compound distribution: a random number of catches or carries, each
  producing a random gain. Modelling it directly as a normal understates the right tail
  badly, which is precisely the region a "higher" pick on receiving yards lives in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass
class DistributionSummary:
    """Quantile summary the API returns for display."""

    mean: float
    std: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


def _clamp_probability(p: float) -> float:
    """Keep probabilities strictly inside (0, 1).

    Nothing in sports is certain, and a 0 or 1 makes the downstream log/Kelly maths
    blow up. The bound is wide enough never to bind on a realistic projection.
    """
    return float(min(max(p, 1e-6), 1 - 1e-6))


# ------------------------------------------------------------------ count models
def negative_binomial_params(mean: float, variance: float) -> tuple[float, float]:
    """Convert (mean, variance) to scipy's (n, p) for a negative binomial.

    Falls back toward Poisson-like behaviour when the requested variance is at or below
    the mean, since a negative binomial cannot be underdispersed.
    """
    mean = max(mean, 1e-6)
    variance = max(variance, mean * 1.0001)
    p = mean / variance
    n = mean * p / (1 - p)
    return max(n, 1e-6), min(max(p, 1e-6), 1 - 1e-9)


def count_distribution(
    mean: float, dispersion: float = 1.15
) -> stats.rv_discrete:
    """A negative binomial with variance = `dispersion` x mean.

    `dispersion` of 1.0 is Poisson; empirically, pitcher strikeouts and receptions sit
    around 1.10-1.30 once opponent and game-script variation is folded in.
    """
    variance = max(mean * dispersion, mean * 1.0001)
    n, p = negative_binomial_params(mean, variance)
    return stats.nbinom(n, p)


def prob_count_at_least(mean: float, threshold: float, dispersion: float = 1.15) -> float:
    """P(X >= ceil(threshold)) for an overdispersed count.

    Underdog lines are half-integers, so `threshold` of 5.5 means "6 or more".
    """
    k = math.ceil(threshold)
    if k <= 0:
        return _clamp_probability(1.0)
    dist = count_distribution(mean, dispersion)
    return _clamp_probability(float(dist.sf(k - 1)))


def count_summary(mean: float, dispersion: float = 1.15) -> DistributionSummary:
    dist = count_distribution(mean, dispersion)
    q = dist.ppf([0.10, 0.25, 0.50, 0.75, 0.90])
    return DistributionSummary(
        mean=float(mean),
        std=float(math.sqrt(max(mean * dispersion, 1e-9))),
        p10=float(q[0]), p25=float(q[1]), p50=float(q[2]),
        p75=float(q[3]), p90=float(q[4]),
    )


# -------------------------------------------------------- poisson-binomial (exact)
def poisson_binomial_pmf(probabilities: list[float] | np.ndarray) -> np.ndarray:
    """Exact PMF of a sum of independent Bernoullis with different probabilities.

    Built by convolution, which is O(n^2) -- trivial for the 3-5 plate appearances or
    ~10 targets these markets involve, and exact where a normal approximation is not.
    """
    probs = np.asarray(list(probabilities), dtype=float)
    probs = np.clip(probs, 0.0, 1.0)
    pmf = np.array([1.0])
    for p in probs:
        shifted = np.zeros(len(pmf) + 1)
        shifted[:-1] += pmf * (1 - p)
        shifted[1:] += pmf * p
        pmf = shifted
    return pmf


def prob_at_least_one(probabilities: list[float] | np.ndarray) -> float:
    """P(at least one success). This is the "1+ Hit" and "Anytime TD" shape."""
    probs = np.clip(np.asarray(list(probabilities), dtype=float), 0.0, 1.0)
    if probs.size == 0:
        return _clamp_probability(0.0)
    return _clamp_probability(float(1.0 - np.prod(1.0 - probs)))


# ------------------------------------------------------------- continuous models
def gamma_from_mean_std(mean: float, std: float) -> stats.rv_continuous:
    """Gamma parameterised by mean and standard deviation.

    Gamma is the right family for yardage: strictly positive, right-skewed, and its
    skew falls naturally as volume rises, which matches how a 4-target receiver's
    distribution differs from a 12-target one.
    """
    mean = max(mean, 1e-6)
    std = max(std, mean * 0.05)
    shape = (mean / std) ** 2
    scale = std**2 / mean
    return stats.gamma(a=shape, scale=scale)


def compound_yardage_distribution(
    event_mean: float,
    event_dispersion: float,
    yards_per_event: float,
    yards_per_event_cv: float = 1.05,
    zero_inflation: float = 0.0,
    samples: int = 20000,
    seed: int = 12345,
) -> np.ndarray:
    """Monte-Carlo samples of (random event count) x (random yards per event).

    This is the honest way to price a yardage prop. The count of catches or carries is
    overdispersed, each gain is right-skewed, and the product inherits both -- a shape
    no single normal can represent. `zero_inflation` covers the real chance a player is
    shut out entirely (injury exit, game script, target drought), which is the exact
    scenario a "lower" pick is buying.
    """
    rng = np.random.default_rng(seed)

    n, p = negative_binomial_params(
        max(event_mean, 1e-6), max(event_mean * event_dispersion, event_mean * 1.0001)
    )
    counts = rng.negative_binomial(n, p, size=samples)

    # Per-event gains: gamma with the requested coefficient of variation.
    shape = 1.0 / max(yards_per_event_cv, 1e-3) ** 2
    scale = max(yards_per_event, 1e-6) / shape

    totals = np.zeros(samples)
    nonzero = counts > 0
    if nonzero.any():
        max_count = int(counts.max())
        # Draw a rectangular block of gains and mask it, which vectorises cleanly.
        gains = rng.gamma(shape, scale, size=(samples, max_count))
        mask = np.arange(max_count)[None, :] < counts[:, None]
        totals = (gains * mask).sum(axis=1)

    if zero_inflation > 0:
        totals = np.where(rng.random(samples) < zero_inflation, 0.0, totals)
    return totals


def prob_over_from_samples(samples: np.ndarray, line: float) -> float:
    """P(total > line). Underdog lines are half-integers, so ties are not a concern."""
    if samples.size == 0:
        return _clamp_probability(0.5)
    return _clamp_probability(float((samples > line).mean()))


def summary_from_samples(samples: np.ndarray) -> DistributionSummary:
    q = np.quantile(samples, [0.10, 0.25, 0.50, 0.75, 0.90])
    return DistributionSummary(
        mean=float(samples.mean()),
        std=float(samples.std(ddof=1)) if samples.size > 1 else 0.0,
        p10=float(q[0]), p25=float(q[1]), p50=float(q[2]),
        p75=float(q[3]), p90=float(q[4]),
    )


# ------------------------------------------------------------------------ helpers
def log5(player_rate: float, opponent_rate: float, league_rate: float) -> float:
    """Bill James' log5: combine a player rate and an opponent rate against league average.

    The right way to ask "how often does *this* pitcher strike out *these* hitters".
    Naive averaging of the two rates is wrong -- it is not linear in the extremes, and
    log5 is the odds-ratio combination that is.
    """
    league_rate = min(max(league_rate, 1e-6), 1 - 1e-6)
    a = min(max(player_rate, 1e-6), 1 - 1e-6)
    b = min(max(opponent_rate, 1e-6), 1 - 1e-6)

    numerator = (a * b) / league_rate
    denominator = numerator + ((1 - a) * (1 - b)) / (1 - league_rate)
    if denominator <= 0:
        return league_rate
    return _clamp_probability(numerator / denominator)


def shrink(
    observed: float, prior: float, sample_size: float, prior_weight: float
) -> float:
    """Empirical-Bayes shrinkage toward a prior.

    With no sample this returns the prior; with a large sample it returns the observed
    rate. This is what keeps a pitcher with two starts, or a college backup with four
    carries, from producing a projection the model has no right to be confident about.
    """
    if sample_size <= 0:
        return prior
    weight = sample_size / (sample_size + max(prior_weight, 1e-9))
    return weight * observed + (1 - weight) * prior
