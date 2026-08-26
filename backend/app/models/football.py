"""Football prop models: receiving, rushing and passing yards, receptions, anytime TD.

Shared skeleton for all five markets:

1. **Volume** -- projected plays, split by a game-script-adjusted pass rate, then
   multiplied by the player's shrunk usage share. This is where most of the signal is.
2. **Efficiency** -- yards per catch or per carry, opponent- and weather-adjusted.
3. **Distribution** -- a compound of an overdispersed event count and a right-skewed
   per-event gain, sampled rather than approximated.

Step 3 is what separates this from a spreadsheet. Receiving yards are not normally
distributed around their mean: a receiver's most likely single-game outcome is well
below his average, with a long right tail from one broken play. Pricing a "higher" pick
off a symmetric distribution systematically overstates its chances, and the error is
largest exactly where the lines are set.
"""

from __future__ import annotations

import numpy as np

from app.domain import League, Market
from app.features.context import FootballGameContext, FootballPlayerProfile
from app.features.football import (
    football_confidence,
    game_script_pass_rate,
    implied_team_total,
    opponent_factors,
    projected_plays,
    talent_gap_factor,
    usage_factor,
    weather_passing_multiplier,
    weather_rushing_multiplier,
)
from app.models.base import ModelOutput
from app.models.distributions import (
    compound_yardage_distribution,
    count_summary,
    prob_count_at_least,
    prob_over_from_samples,
    summary_from_samples,
)
from app.schemas import Factor
from app.static_data.loader import priors

#: Overdispersion of event counts. Targets and carries vary more than Poisson because
#: game script, injuries and defensive attention move them together.
TARGET_DISPERSION = 1.30
CARRY_DISPERSION = 1.25
RECEPTION_DISPERSION = 1.22
ATTEMPT_DISPERSION = 1.20

#: Coefficient of variation of a single gain. Receiving is the most volatile -- most
#: catches are short and a few go the distance.
YPR_CV = 1.00
YPC_CV = 1.15
YPCOMP_CV = 0.85

#: Chance a player is effectively shut out: early injury exit, benching, total blanket.
ZERO_INFLATION = {"WR": 0.030, "TE": 0.035, "RB": 0.025, "QB": 0.015}

#: CFB projections are shrunk toward the team-average player harder than NFL ones.
CFB_SHRINK = 0.85


def _league_key(league: League) -> str:
    return "NFL" if league is League.NFL else "CFB"


def project_football(
    market: Market,
    line: float,
    player: FootballPlayerProfile,
    game: FootballGameContext,
) -> ModelOutput:
    """Dispatch to the right market model."""
    if market is Market.RECEIVING_YARDS:
        return _project_receiving(line, player, game, yards=True)
    if market is Market.RECEPTIONS:
        return _project_receiving(line, player, game, yards=False)
    if market is Market.RUSHING_YARDS:
        return _project_rushing(line, player, game)
    if market is Market.PASSING_YARDS:
        return _project_passing(line, player, game)
    if market is Market.ANYTIME_TD:
        return _project_anytime_td(line, player, game)
    raise ValueError(f"{market} is not a football market")


# --------------------------------------------------------------- shared volume
def _volume_context(
    player: FootballPlayerProfile, game: FootballGameContext
) -> tuple[float, float, float, list[Factor]]:
    """(plays, pass_rate, implied points, factors) for the player's team."""
    league_key = _league_key(game.league)
    team = player.team or game.home_team
    plays, plays_factor = projected_plays(game, team, league_key)
    pass_rate, script_factor = game_script_pass_rate(game, team, league_key)
    points, points_factor = implied_team_total(game, team)
    return plays, pass_rate, points, [plays_factor, script_factor, points_factor]


def _apply_talent_gap(
    game: FootballGameContext, player: FootballPlayerProfile, factors: list[Factor]
) -> float:
    multiplier, factor = talent_gap_factor(
        game, player.team or game.home_team, _league_key(game.league)
    )
    if factor is not None:
        factors.append(factor)
    return multiplier


def _zero_inflation(player: FootballPlayerProfile) -> float:
    base = ZERO_INFLATION.get(player.position.upper(), 0.03)
    if player.injury_status and player.injury_status.lower() in ("questionable", "doubtful"):
        base += 0.06
    return base


# ------------------------------------------------------------------- receiving
def _project_receiving(
    line: float,
    player: FootballPlayerProfile,
    game: FootballGameContext,
    *,
    yards: bool,
) -> ModelOutput:
    league_key = _league_key(game.league)
    p = priors(league_key)
    plays, pass_rate, _, factors = _volume_context(player, game)
    warnings: list[str] = []

    pass_factor, _, opponent_factor = opponent_factors(game, player.team or game.home_team)
    weather_multiplier, weather_factor = weather_passing_multiplier(game.weather)
    talent = _apply_talent_gap(game, player, factors)

    attempts = plays * pass_rate * weather_multiplier * talent
    targets = attempts * player.target_share * pass_factor
    if league_key == "CFB":
        # College usage is noisier; pull it toward an average share for the role.
        targets = CFB_SHRINK * targets + (1 - CFB_SHRINK) * attempts * 0.14

    catch_rate = min(0.92, max(0.35, player.catch_rate))
    catches = max(0.15, targets * catch_rate)
    yards_per_catch = max(3.0, player.yards_per_target / max(catch_rate, 0.2))
    # Weather hurts yards per catch as well as volume: fewer deep shots complete.
    yards_per_catch *= 1.0 - (1.0 - weather_multiplier) * 0.5

    factors.extend([usage_factor(player, "target"), opponent_factor, weather_factor])
    factors.append(
        Factor(
            name="Efficiency",
            detail=(
                f"{player.yards_per_target:.1f} yards per target, {catch_rate:.1%} catch rate"
                f" -> {yards_per_catch:.1f} yards per catch"
            ),
            direction="positive"
            if player.yards_per_target > p["yards_per_target"]
            else "negative",
        )
    )
    factors.append(
        Factor(
            name="Projected volume",
            detail=f"{targets:.1f} targets, {catches:.1f} catches",
            impact=targets,
        )
    )

    if player.games < 3:
        warnings.append(f"Only {player.games} games of usage data -- heavily regressed")
    if player.injury_status and player.injury_status.lower() not in ("", "active"):
        warnings.append(f"Injury status: {player.injury_status}")

    confidence = football_confidence(player, game, league_key)

    if not yards:
        # Receptions: a pure count, no yardage compounding.
        mean = catches
        factors.insert(
            0,
            Factor(
                name="Projection",
                detail=f"{mean:.2f} receptions vs a line of {line}",
                impact=mean,
                direction="positive" if mean > line else "negative",
            ),
        )
        return ModelOutput(
            projected_mean=mean,
            summary=count_summary(mean, RECEPTION_DISPERSION),
            prob_higher=prob_count_at_least(mean, line + 0.5, RECEPTION_DISPERSION),
            confidence=confidence,
            factors=factors,
            warnings=warnings,
        )

    samples = compound_yardage_distribution(
        event_mean=catches,
        event_dispersion=TARGET_DISPERSION,
        yards_per_event=yards_per_catch,
        yards_per_event_cv=YPR_CV,
        zero_inflation=_zero_inflation(player),
        seed=_seed(player.player_key, "rec"),
    )
    mean = float(samples.mean())
    factors.insert(
        0,
        Factor(
            name="Projection",
            detail=f"{mean:.1f} receiving yards vs a line of {line}",
            impact=mean,
            direction="positive" if mean > line else "negative",
        ),
    )
    return ModelOutput(
        projected_mean=mean,
        summary=summary_from_samples(samples),
        prob_higher=prob_over_from_samples(samples, line),
        confidence=confidence,
        factors=factors,
        warnings=warnings,
    )


# --------------------------------------------------------------------- rushing
def _project_rushing(
    line: float, player: FootballPlayerProfile, game: FootballGameContext
) -> ModelOutput:
    league_key = _league_key(game.league)
    p = priors(league_key)
    plays, pass_rate, points, factors = _volume_context(player, game)
    warnings: list[str] = []

    _, rush_factor, opponent_factor = opponent_factors(game, player.team or game.home_team)
    weather_multiplier, weather_factor = weather_rushing_multiplier(game.weather)
    talent = _apply_talent_gap(game, player, factors)

    team_carries = plays * (1.0 - pass_rate) * weather_multiplier * talent
    carries = max(0.5, team_carries * player.rush_share)
    if league_key == "CFB":
        carries = CFB_SHRINK * carries + (1 - CFB_SHRINK) * team_carries * 0.35

    yards_per_carry = max(2.2, min(player.yards_per_carry * rush_factor, 7.5))

    factors.extend([usage_factor(player, "rush"), opponent_factor, weather_factor])
    factors.append(
        Factor(
            name="Efficiency",
            detail=f"{yards_per_carry:.2f} yards per carry (league {p['yards_per_carry']:.2f})",
            direction="positive"
            if yards_per_carry > p["yards_per_carry"]
            else "negative",
        )
    )
    factors.append(
        Factor(
            name="Projected volume",
            detail=f"{carries:.1f} carries of a projected {team_carries:.0f} team carries",
            impact=carries,
        )
    )

    if player.games < 3:
        warnings.append(f"Only {player.games} games of usage data -- heavily regressed")

    samples = compound_yardage_distribution(
        event_mean=carries,
        event_dispersion=CARRY_DISPERSION,
        yards_per_event=yards_per_carry,
        yards_per_event_cv=YPC_CV,
        zero_inflation=_zero_inflation(player),
        seed=_seed(player.player_key, "rush"),
    )
    mean = float(samples.mean())
    factors.insert(
        0,
        Factor(
            name="Projection",
            detail=f"{mean:.1f} rushing yards vs a line of {line}",
            impact=mean,
            direction="positive" if mean > line else "negative",
        ),
    )
    return ModelOutput(
        projected_mean=mean,
        summary=summary_from_samples(samples),
        prob_higher=prob_over_from_samples(samples, line),
        confidence=football_confidence(player, game, league_key),
        factors=factors,
        warnings=warnings,
    )


# --------------------------------------------------------------------- passing
def _project_passing(
    line: float, player: FootballPlayerProfile, game: FootballGameContext
) -> ModelOutput:
    league_key = _league_key(game.league)
    p = priors(league_key)
    plays, pass_rate, _, factors = _volume_context(player, game)
    warnings: list[str] = []

    pass_factor, _, opponent_factor = opponent_factors(game, player.team or game.home_team)
    weather_multiplier, weather_factor = weather_passing_multiplier(game.weather)
    talent = _apply_talent_gap(game, player, factors)

    dropback_share = player.dropback_share if player.dropback_share > 0 else 0.92
    attempts = max(4.0, plays * pass_rate * dropback_share * weather_multiplier * talent)

    yards_per_attempt = max(4.5, min(player.yards_per_attempt * pass_factor, 11.0))
    # Completions carry the yardage; weather costs completion percentage too.
    completion_rate = min(0.78, max(0.50, p["catch_rate"] + 0.01)) * (
        1.0 - (1.0 - weather_multiplier) * 0.4
    )
    completions = attempts * completion_rate
    yards_per_completion = (yards_per_attempt * attempts) / max(completions, 1e-6)
    yards_per_completion *= 1.0 - (1.0 - weather_multiplier) * 0.5

    factors.extend([usage_factor(player, "dropback"), opponent_factor, weather_factor])
    factors.append(
        Factor(
            name="Efficiency",
            detail=(
                f"{yards_per_attempt:.2f} yards per attempt "
                f"(league {p['yards_per_attempt']:.2f})"
            ),
            direction="positive"
            if yards_per_attempt > p["yards_per_attempt"]
            else "negative",
        )
    )
    factors.append(
        Factor(
            name="Projected volume",
            detail=f"{attempts:.0f} attempts, {completions:.0f} completions",
            impact=attempts,
        )
    )

    if player.dropback_share and player.dropback_share < 0.75:
        warnings.append(
            f"Only {player.dropback_share:.0%} of team dropbacks -- possible committee or injury"
        )

    samples = compound_yardage_distribution(
        event_mean=completions,
        event_dispersion=ATTEMPT_DISPERSION,
        yards_per_event=yards_per_completion,
        yards_per_event_cv=YPCOMP_CV,
        zero_inflation=_zero_inflation(player),
        seed=_seed(player.player_key, "pass"),
    )
    mean = float(samples.mean())
    factors.insert(
        0,
        Factor(
            name="Projection",
            detail=f"{mean:.0f} passing yards vs a line of {line}",
            impact=mean,
            direction="positive" if mean > line else "negative",
        ),
    )
    return ModelOutput(
        projected_mean=mean,
        summary=summary_from_samples(samples),
        prob_higher=prob_over_from_samples(samples, line),
        confidence=football_confidence(player, game, league_key),
        factors=factors,
        warnings=warnings,
    )


# ---------------------------------------------------------------- anytime TD
def _project_anytime_td(
    line: float, player: FootballPlayerProfile, game: FootballGameContext
) -> ModelOutput:
    """P(at least one touchdown).

    Driven off the market's implied team total rather than the player's own scoring
    rate, because touchdown rates are the noisiest thing in football -- a handful of
    scores swings a season share. We convert implied points into expected team
    touchdowns, split those between the run and the pass using the projected game
    script, and apply the player's (heavily shrunk) share of each.
    """
    league_key = _league_key(game.league)
    p = priors(league_key)
    plays, pass_rate, points, factors = _volume_context(player, game)
    warnings: list[str] = []

    _apply_talent_gap(game, player, factors)
    pass_factor, rush_factor, opponent_factor = opponent_factors(
        game, player.team or game.home_team
    )

    # Points -> touchdowns, anchored on the league's own points/TD relationship.
    td_per_point = p["td_per_team_game"] / max(p["team_points"], 1e-6)
    team_tds = max(0.4, points * td_per_point)

    # Split team touchdowns between passing and rushing scores using game script.
    passing_tds = team_tds * (0.45 + 0.35 * (pass_rate - p["pass_rate"]) * 2)
    passing_tds = max(0.0, min(passing_tds, team_tds))
    rushing_tds = team_tds - passing_tds

    if player.is_quarterback:
        # Anytime TD for a QB means a rushing score, not a thrown one.
        expected = rushing_tds * player.redzone_rush_share * rush_factor
        share_detail = f"{player.redzone_rush_share:.1%} of team goal-line carries"
    else:
        expected = (
            passing_tds * player.redzone_target_share * pass_factor
            + rushing_tds * player.redzone_rush_share * rush_factor
        )
        share_detail = (
            f"{player.redzone_target_share:.1%} of receiving TDs, "
            f"{player.redzone_rush_share:.1%} of rushing TDs"
        )

    expected = max(0.01, min(expected, 2.5))

    factors.extend([opponent_factor])
    factors.append(
        Factor(
            name="Team touchdowns",
            detail=(
                f"{team_tds:.2f} projected team TDs ({passing_tds:.2f} passing, "
                f"{rushing_tds:.2f} rushing)"
            ),
            impact=team_tds,
        )
    )
    factors.append(
        Factor(
            name="Scoring share",
            detail=share_detail,
            direction="positive" if expected > 0.45 else "neutral",
        )
    )
    factors.append(
        Factor(
            name="Data caveat",
            detail=(
                "Red-zone share is inferred from touchdown history, not snap-level "
                "goal-line data -- the noisiest input in this model"
            ),
            direction="neutral",
        )
    )

    if player.games < 4:
        warnings.append("Touchdown share on a small sample -- treat with caution")

    # Overdispersed count: P(at least one) from a negative binomial, not a bare Poisson.
    probability = prob_count_at_least(expected, 0.5, dispersion=1.15)
    factors.insert(
        0,
        Factor(
            name="Projection",
            detail=f"{probability:.1%} chance to score ({expected:.2f} expected TDs)",
            impact=expected,
            direction="positive" if probability > 0.5 else "negative",
        ),
    )
    return ModelOutput(
        projected_mean=expected,
        summary=count_summary(expected, 1.15),
        prob_higher=probability,
        confidence=football_confidence(player, game, league_key) * 0.9,
        factors=factors,
        warnings=warnings,
    )


def _seed(player_key: str, market: str) -> int:
    """Deterministic per-player seed, so a refresh does not jitter the board."""
    return abs(hash(f"{player_key}:{market}")) % (2**31)
