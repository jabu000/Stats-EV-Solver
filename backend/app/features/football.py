"""Football volume and efficiency adjustments, shared by NFL and CFB.

The organising idea: **don't try to out-predict the market on the game, and do spend
the modelling effort on how a player's usage maps onto it.** The spread and total are
the best available forecast of how many snaps a team gets, how many of them are passes,
and how many points are on the board -- so those become inputs, and the model's job is
the share and efficiency layer on top.

Game script is the piece naive models miss. A team favoured by 14 runs the ball out in
the fourth quarter; the same running back has a very different projection at -14 and at
+3, even with identical usage. That effect is worth more than most efficiency
adjustments and it is fully knowable before kickoff.
"""

from __future__ import annotations

from app.features.context import (
    FootballGameContext,
    FootballPlayerProfile,
    FootballTeamContext,
    WeatherContext,
)
from app.schemas import Factor
from app.static_data.loader import priors


def _direction(multiplier: float) -> str:
    if abs(multiplier - 1.0) < 0.005:
        return "neutral"
    return "positive" if multiplier > 1.0 else "negative"


# ------------------------------------------------------------------- volume
def projected_plays(
    game: FootballGameContext, team: str, league_key: str
) -> tuple[float, Factor]:
    """Offensive plays for a team.

    Pace sets the baseline; the game total nudges it, because high-scoring games run
    more plays (more possessions, more clock-stopping scores). Both teams' pace matter
    since they share the same game clock.
    """
    p = priors(league_key)
    own = game.team_context(team)
    opponent = game.team_context(game.opponent_of(team))

    base = own.plays_per_game if own else p["plays_per_game"]
    opponent_pace = opponent.plays_per_game if opponent else p["plays_per_game"]
    # Both offences share one game clock, so blend the two paces.
    blended = 0.65 * base + 0.35 * opponent_pace

    total_effect = 1.0 + 0.0055 * (game.total - 2 * p["team_points"])
    plays = max(45.0, min(blended * total_effect, 90.0))

    return plays, Factor(
        name="Projected plays",
        detail=(
            f"{plays:.0f} offensive plays (team pace {base:.0f}, opponent {opponent_pace:.0f},"
            f" game total {game.total:.1f})"
        ),
        impact=plays,
    )


def game_script_pass_rate(
    game: FootballGameContext, team: str, league_key: str
) -> tuple[float, Factor]:
    """Pass rate after adjusting for the projected game script.

    Underdogs throw and favourites run, and the effect is close to linear in the spread
    over the range that matters. A team's own pass-rate-over-expectation is added on
    top, so a pass-happy staff stays pass-happy even when favoured.
    """
    p = priors(league_key)
    own = game.team_context(team)
    base = own.pass_rate if own else p["pass_rate"]
    proe = own.proe if own else 0.0

    # Positive `deficit` means this team is the underdog.
    spread = game.spread if team == game.home_team else -game.spread
    deficit = spread

    script_effect = 0.0075 * deficit
    pass_rate = max(0.32, min(base + 0.5 * proe + script_effect, 0.78))

    role = (
        f"{abs(deficit):.1f}-point {'underdog' if deficit > 0 else 'favourite'}"
        if abs(deficit) >= 0.5
        else "pick'em"
    )
    return pass_rate, Factor(
        name="Game script",
        detail=f"{role} -> {pass_rate:.1%} pass rate (team baseline {base:.1%})",
        direction="positive" if script_effect > 0 else "negative",
    )


def implied_team_total(game: FootballGameContext, team: str) -> tuple[float, Factor]:
    points = game.implied_points(team)
    return points, Factor(
        name="Implied team total",
        detail=(
            f"{points:.1f} points (game total {game.total:.1f}, spread "
            f"{game.spread:+.1f} to the home side)"
        ),
        impact=points,
    )


# ------------------------------------------------------------------- weather
def weather_passing_multiplier(weather: WeatherContext) -> tuple[float, Factor]:
    """Wind and rain suppress passing.

    Wind is the one that actually matters and it is strongly non-linear: below about
    12 mph the effect is negligible, and above roughly 20 mph deep passing collapses.
    Cold has a smaller, real effect on ball handling; temperature alone is routinely
    over-weighted by public models.
    """
    if not weather.applies:
        return 1.0, Factor(
            name="Weather",
            detail=f"Indoors ({weather.roof.value}) -- no weather adjustment",
            direction="neutral",
        )

    wind_excess = max(0.0, weather.wind_mph - 12.0)
    wind_effect = -0.011 * wind_excess - 0.0012 * wind_excess**2
    rain_effect = -0.045 * weather.precipitation_chance
    cold_effect = -0.0018 * max(0.0, 40.0 - weather.temperature_f)

    multiplier = max(0.70, min(1.03, 1.0 + wind_effect + rain_effect + cold_effect))

    parts = [f"{weather.temperature_f:.0f}F", f"wind {weather.wind_mph:.0f} mph"]
    if weather.precipitation_chance > 0.15:
        parts.append(f"{weather.precipitation_chance * 100:.0f}% precip")
    return multiplier, Factor(
        name="Weather",
        detail=", ".join(parts)
        + (" -- significant passing headwind" if wind_excess > 6 else ""),
        direction=_direction(multiplier),
    )


def weather_rushing_multiplier(weather: WeatherContext) -> tuple[float, Factor]:
    """Bad weather modestly *helps* rushing volume, because teams stop throwing."""
    if not weather.applies:
        return 1.0, Factor(
            name="Weather",
            detail=f"Indoors ({weather.roof.value}) -- no weather adjustment",
            direction="neutral",
        )
    wind_excess = max(0.0, weather.wind_mph - 12.0)
    multiplier = max(0.97, min(1.10, 1.0 + 0.006 * wind_excess + 0.03 * weather.precipitation_chance))
    return multiplier, Factor(
        name="Weather",
        detail=f"{weather.temperature_f:.0f}F, wind {weather.wind_mph:.0f} mph",
        direction=_direction(multiplier),
    )


# --------------------------------------------------------------- opponent
def opponent_factors(
    game: FootballGameContext, team: str
) -> tuple[float, float, Factor]:
    """Opponent defensive strength as multipliers on pass and rush production."""
    own = game.team_context(team)
    pass_factor = own.opp_pass_defense_factor if own else 1.0
    rush_factor = own.opp_rush_defense_factor if own else 1.0

    opponent = game.opponent_of(team)
    if abs(pass_factor - 1.0) < 0.005 and abs(rush_factor - 1.0) < 0.005:
        detail = f"vs {opponent} -- league-average defensive adjustment"
    else:
        detail = (
            f"vs {opponent}: pass defence {pass_factor:.2f}x, rush defence {rush_factor:.2f}x"
        )
    return pass_factor, rush_factor, Factor(
        name="Opponent defence", detail=detail, direction=_direction(pass_factor)
    )


def talent_gap_factor(
    game: FootballGameContext, team: str, league_key: str
) -> tuple[float, Factor | None]:
    """College-specific: a huge rating gap distorts usage through blowouts.

    When a playoff team plays a bottom-tier opponent, starters sit in the fourth quarter
    and the projection has to come down even though every rate stat says otherwise. NFL
    talent gaps are nowhere near large enough for this to matter, so it is CFB-only.
    """
    if league_key != "CFB":
        return 1.0, None

    own = game.team_context(team)
    opponent = game.team_context(game.opponent_of(team))
    if own is None or opponent is None:
        return 1.0, None

    gap = own.rating - opponent.rating
    if abs(gap) < 12:
        return 1.0, None

    # A heavy favourite loses late-game volume; a heavy underdog is chasing all day.
    multiplier = max(0.86, min(1.06, 1.0 - 0.0045 * max(0.0, gap - 12) + 0.0018 * max(0.0, -gap - 12)))
    return multiplier, Factor(
        name="Talent gap",
        detail=(
            f"{abs(gap):.0f}-point rating gap -- "
            + ("starters likely rested late" if gap > 0 else "trailing script all game")
        ),
        direction=_direction(multiplier),
    )


# ------------------------------------------------------------------ confidence
def football_confidence(
    player: FootballPlayerProfile,
    game: FootballGameContext,
    league_key: str,
) -> float:
    """How much of this projection is the player's own data versus a prior."""
    target_games = 6.0 if league_key == "NFL" else 8.0
    sample_score = min(1.0, player.games / target_games)
    market_score = 1.0 if game.total and abs(game.total - 44.5) > 1e-9 else 0.7
    weather_score = 1.0 if game.weather.source not in ("unavailable", "default") else 0.85
    league_score = 1.0 if league_key == "NFL" else 0.82

    score = (
        0.45 * sample_score
        + 0.20 * market_score
        + 0.10 * weather_score
        + 0.25 * league_score
    )
    if player.injury_status and player.injury_status.lower() not in ("", "active"):
        score *= 0.75
    return round(max(0.05, min(1.0, score)), 3)


def usage_factor(player: FootballPlayerProfile, kind: str) -> Factor:
    """Explain the usage share that drives the projection."""
    if kind == "target":
        return Factor(
            name="Target share",
            detail=(
                f"{player.target_share:.1%} of team targets, {player.air_yards_share:.1%} "
                f"of air yards over {player.games} games"
            ),
            direction="positive" if player.target_share > 0.18 else "neutral",
        )
    if kind == "rush":
        return Factor(
            name="Rush share",
            detail=f"{player.rush_share:.1%} of team carries over {player.games} games",
            direction="positive" if player.rush_share > 0.5 else "neutral",
        )
    return Factor(
        name="Dropback share",
        detail=f"{player.dropback_share:.1%} of team pass attempts",
        direction="positive" if player.dropback_share > 0.85 else "negative",
    )
