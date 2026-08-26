"""Batter "1+ hit" projection.

This is a threshold market, so the answer is *not* "project hits and compare to 0.5".
It is: for each plate appearance the hitter is likely to get, what is the chance he
gets a hit, and what is the chance at least one of those lands. That framing matters
because plate-appearance count is itself uncertain and is a large part of the edge --
a leadoff hitter in a game with a 5.5-run implied total gets meaningfully more chances
than a nine-hole hitter in a pitchers' duel, and the market prices the two much closer
together than the underlying probability justifies.

The model also splits each hitter's plate appearances between the starter and the
bullpen, because after the fifth inning he is facing a different pitcher entirely.
"""

from __future__ import annotations

import math

from app.features.context import BatterProfile, MlbGameContext, PitcherProfile
from app.features.mlb import (
    confidence_from_context,
    defense_hit_multiplier,
    expected_batters_faced,
    expected_plate_appearances,
    park_hit_multiplier,
    platoon_factor,
    weather_hit_multiplier,
)
from app.models.base import ModelOutput
from app.models.distributions import (
    DistributionSummary,
    log5,
    poisson_binomial_pmf,
    shrink,
)
from app.models.mlb_strikeouts import BATTER_SPLIT_PRIOR_WEIGHT, platoon_ratio
from app.schemas import Factor
from app.static_data.loader import priors

#: A hitter's per-PA hit probability is bounded well away from the extremes.
MIN_HIT_PROBABILITY = 0.05
MAX_HIT_PROBABILITY = 0.55

PITCHER_HIT_SPLIT_PRIOR_WEIGHT = 600.0


def project_hits(
    line: float, batter: BatterProfile, game: MlbGameContext
) -> ModelOutput:
    """Project P(at least one hit). `line` is 0.5 for this market."""
    p = priors("MLB")
    league_hit = p["batter_hit_per_pa"]
    factors: list[Factor] = []
    warnings: list[str] = []

    is_home = batter.team == game.home_team
    opposing_starter = game.away_pitcher if is_home else game.home_pitcher
    opposing_bullpen = (
        game.away_bullpen_hit_per_bf if is_home else game.home_bullpen_hit_per_bf
    )
    implied_runs = game.home_implied_runs if is_home else game.away_implied_runs

    # ---- 1. the hitter's own rate ---------------------------------------------
    base_rate = shrink(
        batter.hit_per_pa,
        league_hit,
        batter.plate_appearances,
        p["batter_hit_per_pa_prior_weight"],
    )
    factors.append(
        Factor(
            name="Hitter contact",
            detail=(
                f"{base_rate:.1%} hits per plate appearance "
                f"({batter.plate_appearances} PA of data)"
            ),
            direction="positive" if base_rate > league_hit else "negative",
        )
    )

    # ---- 2. handedness ---------------------------------------------------------
    if opposing_starter is not None:
        _, platoon = platoon_factor(batter, opposing_starter)
        factors.append(platoon)
        hand_ratio = platoon_ratio(
            batter.hit_rate_vs(opposing_starter.throws),
            batter.hit_per_pa,
            batter.plate_appearances,
            BATTER_SPLIT_PRIOR_WEIGHT,
        )
    else:
        hand_ratio = 1.0
        warnings.append("Opposing starter not announced -- league-average arm assumed")
        factors.append(
            Factor(
                name="Opposing starter",
                detail="Not yet announced -- league-average pitcher assumed",
                direction="neutral",
            )
        )

    # ---- 3. environment ---------------------------------------------------------
    environment = 1.0
    for multiplier, factor in (
        park_hit_multiplier(game.park),
        weather_hit_multiplier(game.weather),
        defense_hit_multiplier(game.away_defense_oaa if is_home else game.home_defense_oaa),
    ):
        environment *= multiplier
        if factor is not None:
            factors.append(factor)

    # ---- 4. the two pitchers he will face ---------------------------------------
    hitter_rate = base_rate * hand_ratio
    starter_rate = _matchup_rate(
        hitter_rate, opposing_starter, league_hit, environment, batter
    )
    bullpen_rate = _bounded(
        log5(hitter_rate, opposing_bullpen, league_hit) * environment
    )
    if opposing_starter is not None:
        factors.append(
            Factor(
                name="Opposing starter",
                detail=(
                    f"{opposing_starter.name} ({opposing_starter.throws.value}HP), "
                    f"{opposing_starter.hit_per_bf:.1%} hits allowed per batter faced"
                ),
                direction="positive"
                if opposing_starter.hit_per_bf > league_hit
                else "negative",
            )
        )
    factors.append(
        Factor(
            name="Opposing bullpen",
            detail=f"{opposing_bullpen:.1%} hits allowed per batter faced in relief",
            direction="positive" if opposing_bullpen > league_hit else "negative",
        )
    )

    # ---- 5. how many chances ------------------------------------------------------
    plate_appearances, pa_factor = expected_plate_appearances(
        batter.lineup_slot, implied_runs
    )
    factors.append(pa_factor)

    starter_batters = 24.0
    if opposing_starter is not None:
        starter_batters, _ = expected_batters_faced(
            opposing_starter, _lineup_obp(game, is_home), implied_runs
        )

    if batter.lineup_slot == 0:
        warnings.append("Not in the posted lineup -- assuming a mid-order spot")

    # ---- 6. combine ----------------------------------------------------------------
    probability, mean_hits, summary = _threshold_probability(
        plate_appearances, starter_batters, batter.lineup_slot or 6, starter_rate, bullpen_rate
    )

    factors.insert(
        0,
        Factor(
            name="Projection",
            detail=(
                f"{probability:.1%} chance of at least one hit "
                f"({mean_hits:.2f} projected hits)"
            ),
            impact=mean_hits,
            direction="positive" if probability > 0.5 else "negative",
        ),
    )

    return ModelOutput(
        projected_mean=mean_hits,
        summary=summary,
        prob_higher=probability,
        confidence=confidence_from_context(game, batter.plate_appearances, 400),
        factors=factors,
        warnings=warnings,
    )


def _matchup_rate(
    hitter_rate: float,
    pitcher: PitcherProfile | None,
    league_hit: float,
    environment: float,
    batter: BatterProfile,
) -> float:
    """log5 the hitter against this specific starter, then apply the environment."""
    if pitcher is None:
        return _bounded(hitter_rate * environment)
    pitcher_rate = shrink(
        pitcher.hit_per_bf, league_hit, pitcher.batters_faced, 250.0
    )
    return _bounded(log5(hitter_rate, pitcher_rate, league_hit) * environment)


def _threshold_probability(
    plate_appearances: float,
    starter_batters: float,
    lineup_slot: int,
    starter_rate: float,
    bullpen_rate: float,
) -> tuple[float, float, DistributionSummary]:
    """Mix P(at least one hit) over the integer plate-appearance counts.

    Expected plate appearances is fractional -- 4.3, say -- but a hitter gets four or
    five, never 4.3, and those two cases have materially different probabilities. We
    evaluate both and weight them, rather than evaluating the fraction and hoping.
    """
    low = int(math.floor(plate_appearances))
    high = low + 1
    weight_high = plate_appearances - low

    total_probability = 0.0
    total_mean = 0.0
    pmfs: list[tuple[float, list[float]]] = []

    for count, weight in ((low, 1 - weight_high), (high, weight_high)):
        if weight <= 0 or count <= 0:
            continue
        per_pa = _per_pa_rates(count, starter_batters, lineup_slot, starter_rate, bullpen_rate)
        pmf = poisson_binomial_pmf(per_pa)
        total_probability += weight * float(1.0 - pmf[0])
        total_mean += weight * sum(per_pa)
        pmfs.append((weight, list(pmf)))

    return total_probability, total_mean, _summary_from_pmfs(pmfs, total_mean)


def _per_pa_rates(
    count: int,
    starter_batters: float,
    lineup_slot: int,
    starter_rate: float,
    bullpen_rate: float,
) -> list[float]:
    """Assign each plate appearance to the starter or the bullpen.

    Batting order position decides this exactly: the hitter in the k-th slot comes up
    for the n-th time as the (9*(n-1) + k)-th batter of the game, so we can just check
    which of those fall inside the starter's projected workload.
    """
    rates: list[float] = []
    for appearance in range(count):
        batter_number = 9 * appearance + lineup_slot
        rates.append(starter_rate if batter_number <= starter_batters else bullpen_rate)
    return rates


def _summary_from_pmfs(
    pmfs: list[tuple[float, list[float]]], mean: float
) -> DistributionSummary:
    """Quantiles of the blended hit-count distribution."""
    blended: dict[int, float] = {}
    for weight, pmf in pmfs:
        for hits, probability in enumerate(pmf):
            blended[hits] = blended.get(hits, 0.0) + weight * probability

    def quantile(q: float) -> float:
        cumulative = 0.0
        for hits in sorted(blended):
            cumulative += blended[hits]
            if cumulative >= q:
                return float(hits)
        return float(max(blended) if blended else 0)

    variance = sum(p * (h - mean) ** 2 for h, p in blended.items())
    return DistributionSummary(
        mean=mean,
        std=math.sqrt(max(variance, 0.0)),
        p10=quantile(0.10), p25=quantile(0.25), p50=quantile(0.50),
        p75=quantile(0.75), p90=quantile(0.90),
    )


def _bounded(rate: float) -> float:
    return min(max(rate, MIN_HIT_PROBABILITY), MAX_HIT_PROBABILITY)


def _lineup_obp(game: MlbGameContext, is_home: bool) -> float:
    lineup = game.home_lineup if is_home else game.away_lineup
    values = [b.on_base_pct for b in lineup if b.on_base_pct > 0]
    return sum(values) / len(values) if values else 0.315
