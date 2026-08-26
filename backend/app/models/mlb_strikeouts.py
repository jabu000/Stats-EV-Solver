"""Pitcher strikeout projection.

The model works at the level of a single batter faced rather than the whole game,
because that is where the real information lives. For each hitter the starter is
projected to face we compute a strikeout probability from *that* matchup -- this
pitcher's rate against that side of the plate, log5-combined with that hitter's own
strikeout rate against this hand -- then adjust for park, weather, umpire and framing,
and sum across the projected batters faced.

Two things this gets right that a season-average approach does not:

* **Volume and rate are separate.** An elite arm on a short leash will not reach a 6.5
  line, and a mediocre arm going seven might. `expected_batters_faced` is a first-class
  term, not an afterthought.
* **Handedness is matchup-specific.** A lefty facing a lineup stacked with left-handed
  hitters is a different pitcher from the same lefty facing a righty-heavy one, and the
  gap between platoon splits is routinely five or six points of strikeout rate.
"""

from __future__ import annotations

from app.domain import Handedness
from app.features.context import BatterProfile, MlbGameContext, PitcherProfile
from app.features.mlb import (
    altitude_note,
    confidence_from_context,
    expected_batters_faced,
    framing_k_multiplier,
    lineup_handedness_summary,
    park_k_multiplier,
    umpire_k_multiplier,
    weather_k_multiplier,
)
from app.models.base import ModelOutput
from app.models.distributions import count_summary, log5, prob_count_at_least, shrink
from app.schemas import Factor
from app.static_data.loader import priors

#: Strikeout counts are overdispersed relative to Poisson: the underlying rate itself
#: varies with opponent, umpire and how the pitcher feels that night.
K_DISPERSION = 1.18

#: Recent form gets this much weight against the season rate.
RECENT_FORM_WEIGHT = 0.30


def project_strikeouts(
    line: float, pitcher: PitcherProfile, game: MlbGameContext
) -> ModelOutput:
    p = priors("MLB")
    league_k = p["pitcher_k_rate"]
    factors: list[Factor] = []
    warnings: list[str] = []

    # ---- 1. the pitcher's own rate, shrunk toward league average ---------------
    base_rate = shrink(
        pitcher.k_per_bf, league_k, pitcher.batters_faced, p["pitcher_k_rate_prior_weight"]
    )
    if pitcher.k_per_bf_recent is not None:
        base_rate = (
            1 - RECENT_FORM_WEIGHT
        ) * base_rate + RECENT_FORM_WEIGHT * pitcher.k_per_bf_recent
        factors.append(
            Factor(
                name="Recent form",
                detail=(
                    f"Last starts {pitcher.k_per_bf_recent:.1%} K/BF vs "
                    f"{pitcher.k_per_bf:.1%} season"
                ),
                direction="positive"
                if pitcher.k_per_bf_recent > pitcher.k_per_bf
                else "negative",
            )
        )
    factors.append(
        Factor(
            name="Pitcher strikeout rate",
            detail=(
                f"{base_rate:.1%} K per batter faced "
                f"({pitcher.batters_faced} BF of data, shrunk toward {league_k:.1%})"
            ),
            direction="positive" if base_rate > league_k else "negative",
        )
    )

    # ---- 2. environment multipliers -------------------------------------------
    environment = 1.0
    for multiplier, factor in (
        park_k_multiplier(game.park),
        weather_k_multiplier(game.weather),
        umpire_k_multiplier(game.umpire),
        framing_k_multiplier(
            game.home_catcher_framing
            if pitcher.team == game.home_team
            else game.away_catcher_framing
        ),
    ):
        environment *= multiplier
        if factor is not None:
            factors.append(factor)

    note = altitude_note(game.park)
    if note is not None:
        factors.append(note)

    # ---- 3. volume -------------------------------------------------------------
    opponent_lineup = game.lineup_against(pitcher.team or game.home_team)
    opponent_obp = _lineup_obp(opponent_lineup, default=0.315)
    opponent_runs = (
        game.away_implied_runs
        if pitcher.team == game.home_team
        else game.home_implied_runs
    )
    batters_faced, workload_factor = expected_batters_faced(
        pitcher, opponent_obp, opponent_runs
    )
    factors.append(workload_factor)

    # ---- 4. per-batter matchup, summed over the projected batters faced --------
    if opponent_lineup:
        factors.append(lineup_handedness_summary(opponent_lineup, pitcher))
        expected_k = _sum_over_lineup(
            pitcher, opponent_lineup, batters_faced, environment, league_k, base_rate
        )
    else:
        # No lineup posted: fall back to the pitcher's rate against a league-average
        # opponent, and say so rather than pretending we modelled the matchup.
        warnings.append("Lineup not posted -- matchup uses a league-average opponent")
        expected_k = base_rate * environment * batters_faced
        factors.append(
            Factor(
                name="Opposing lineup",
                detail="Not yet posted -- league-average hitters assumed",
                direction="neutral",
            )
        )

    expected_k = max(0.15, expected_k)
    factors.insert(
        0,
        Factor(
            name="Projection",
            detail=f"{expected_k:.2f} strikeouts vs a line of {line}",
            impact=expected_k,
            direction="positive" if expected_k > line else "negative",
        ),
    )

    if pitcher.starts < 4:
        warnings.append(
            f"Only {pitcher.starts} starts of data -- heavily regressed to league average"
        )

    return ModelOutput(
        projected_mean=expected_k,
        summary=count_summary(expected_k, K_DISPERSION),
        prob_higher=prob_count_at_least(expected_k, line + 0.5, K_DISPERSION),
        confidence=confidence_from_context(game, pitcher.batters_faced, 500),
        factors=factors,
        warnings=warnings,
    )


#: Plate appearances of platoon data needed before a split is trusted at face value.
#: Splits are far noisier than overall rates -- roughly a thousand batters faced before
#: a pitcher's platoon gap stabilises -- so they are regressed hard toward "no split".
PITCHER_SPLIT_PRIOR_WEIGHT = 600.0
BATTER_SPLIT_PRIOR_WEIGHT = 400.0

#: No hitter is a coin flip to strike out; this caps pathological log5 combinations.
MAX_BATTER_K_PROBABILITY = 0.60


def platoon_ratio(
    split_rate: float | None,
    overall_rate: float,
    sample_size: float,
    prior_weight: float,
) -> float:
    """Express a platoon split as a regressed *ratio* to the overall rate.

    Using the raw split as an absolute rate is the standard mistake here: it throws away
    the much better-estimated overall rate and lets a noisy few hundred plate
    appearances drive the projection. Converting to a ratio and shrinking it toward 1.0
    keeps the well-measured level and regresses only the part that is poorly measured.
    """
    if split_rate is None or overall_rate <= 0:
        return 1.0
    # The split is drawn from roughly half the player's plate appearances.
    return shrink(split_rate / overall_rate, 1.0, sample_size * 0.5, prior_weight)


def _sum_over_lineup(
    pitcher: PitcherProfile,
    lineup: list[BatterProfile],
    batters_faced: float,
    environment: float,
    league_k: float,
    pitcher_base_rate: float,
) -> float:
    """Walk the batting order for the projected number of batters faced.

    The starter does not face nine distinct hitters -- he faces the top of the order
    three times and the bottom twice. Cycling the order reproduces that, and the
    fractional final batter is handled by weighting the partial cycle.

    `pitcher_base_rate` is the shrunk, recent-form-weighted rate from the caller; the
    handedness split is applied to it as a regressed multiplier rather than replacing it.
    """
    if not lineup:
        return 0.0

    per_batter = []
    for batter in lineup:
        p_ratio = platoon_ratio(
            pitcher.k_rate_vs(batter.bats),
            pitcher.k_per_bf,
            pitcher.batters_faced,
            PITCHER_SPLIT_PRIOR_WEIGHT,
        )
        b_ratio = platoon_ratio(
            batter.k_rate_vs(pitcher.throws),
            batter.k_per_pa,
            batter.plate_appearances,
            BATTER_SPLIT_PRIOR_WEIGHT,
        )
        batter_rate = shrink(
            batter.k_per_pa, league_k, batter.plate_appearances, 200.0
        ) * b_ratio

        matchup = log5(pitcher_base_rate * p_ratio, batter_rate, league_k)
        per_batter.append(min(MAX_BATTER_K_PROBABILITY, matchup * environment))

    total = 0.0
    whole = int(batters_faced)
    for index in range(whole):
        total += per_batter[index % len(per_batter)]
    remainder = batters_faced - whole
    if remainder > 0:
        total += remainder * per_batter[whole % len(per_batter)]
    return total


def _lineup_obp(lineup: list[BatterProfile], default: float) -> float:
    if not lineup:
        return default
    values = [b.on_base_pct for b in lineup if b.on_base_pct > 0]
    return sum(values) / len(values) if values else default
