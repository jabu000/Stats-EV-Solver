"""Baseball-specific adjustments applied on top of raw player rates.

Each function returns a *multiplier* on a rate plus a human-readable `Factor`, so the
same computation that moves the projection also populates the "Why" drawer. If a factor
cannot be explained on screen it should not be moving the number.

Magnitudes here are deliberately modest. Park and platoon effects are large and well
established; weather effects on strikeout rate are real but small, and inflating them
would produce a model that looks clever and prices badly.
"""

from __future__ import annotations

import math

from app.features.context import (
    BatterProfile,
    MlbGameContext,
    ParkContext,
    PitcherProfile,
    UmpireProfile,
    WeatherContext,
)
from app.schemas import Factor

#: A starter faces roughly three batters an inning plus whoever reaches base.
_OUTS_PER_INNING = 3.0


def _direction(multiplier: float, *, higher_is_positive: bool = True) -> str:
    if abs(multiplier - 1.0) < 0.004:
        return "neutral"
    good = multiplier > 1.0
    return "positive" if good == higher_is_positive else "negative"


# ---------------------------------------------------------------------- park
def park_k_multiplier(park: ParkContext) -> tuple[float, Factor]:
    """Park effect on strikeout rate.

    Foul territory, backstop distance and sightlines all move strikeouts a few percent.
    Altitude is the big one: thin air flattens breaking balls, which is why Coors
    suppresses strikeouts as well as inflating offence.
    """
    multiplier = park.k_factor / 100.0
    return multiplier, Factor(
        name="Park",
        detail=f"{park.name} strikeout factor {park.k_factor:.0f} (100 = neutral)",
        direction=_direction(multiplier),
    )


def park_hit_multiplier(park: ParkContext) -> tuple[float, Factor]:
    multiplier = park.hit_factor / 100.0
    return multiplier, Factor(
        name="Park",
        detail=f"{park.name} hit factor {park.hit_factor:.0f} (100 = neutral)",
        direction=_direction(multiplier),
    )


# ------------------------------------------------------------------- weather
def weather_k_multiplier(weather: WeatherContext) -> tuple[float, Factor]:
    """Weather effect on strikeouts.

    Warm, humid air is hitter-friendly: the ball carries and breaking pitches move less,
    so strikeouts fall slightly. The effect is genuinely small -- about a percent per
    ten degrees -- and is capped so a freak forecast cannot dominate the projection.
    """
    if not weather.applies:
        return 1.0, Factor(
            name="Weather",
            detail=f"Indoors ({weather.roof.value}) -- no weather adjustment",
            direction="neutral",
        )

    temp_effect = -0.0011 * (weather.temperature_f - 70.0)
    humidity_effect = -0.0004 * (weather.humidity_pct - 50.0)
    multiplier = max(0.94, min(1.06, 1.0 + temp_effect + humidity_effect))

    return multiplier, Factor(
        name="Weather",
        detail=(
            f"{weather.temperature_f:.0f}F, {weather.humidity_pct:.0f}% humidity, "
            f"wind {weather.wind_mph:.0f} mph"
        ),
        direction=_direction(multiplier),
    )


def weather_hit_multiplier(weather: WeatherContext) -> tuple[float, Factor]:
    """Weather effect on batting average on balls in play.

    Warm air carries; a stiff wind blowing out turns fly-ball outs into hits, and one
    blowing in does the reverse. We do not know each park's outfield orientation, so
    wind is treated as a magnitude-only effect with a small coefficient rather than
    pretending to a precision we do not have.
    """
    if not weather.applies:
        return 1.0, Factor(
            name="Weather",
            detail=f"Indoors ({weather.roof.value}) -- no weather adjustment",
            direction="neutral",
        )

    temp_effect = 0.0016 * (weather.temperature_f - 70.0)
    wind_effect = 0.0018 * max(0.0, weather.wind_mph - 8.0)
    rain_effect = -0.02 * weather.precipitation_chance
    multiplier = max(0.93, min(1.08, 1.0 + temp_effect + wind_effect + rain_effect))

    detail = (
        f"{weather.temperature_f:.0f}F, wind {weather.wind_mph:.0f} mph"
        + (
            f", {weather.precipitation_chance * 100:.0f}% precip"
            if weather.precipitation_chance > 0.1
            else ""
        )
    )
    return multiplier, Factor(name="Weather", detail=detail, direction=_direction(multiplier))


# ------------------------------------------------------------ umpire & framing
def umpire_k_multiplier(umpire: UmpireProfile) -> tuple[float, Factor] | tuple[float, None]:
    if not umpire.known:
        return 1.0, None
    return umpire.k_factor, Factor(
        name="Umpire",
        detail=f"{umpire.name}, strikeout factor {umpire.k_factor:.2f}",
        direction=_direction(umpire.k_factor),
    )


def framing_k_multiplier(framing_runs: float) -> tuple[float, Factor] | tuple[float, None]:
    """Catcher framing, converted from runs per 150 games to a strikeout multiplier.

    A elite framer is worth on the order of 10-15 runs a season, and a called strike is
    worth roughly 0.12 runs, so the implied change in called strikes is small but real.
    """
    if abs(framing_runs) < 0.5:
        return 1.0, None
    multiplier = max(0.96, min(1.04, 1.0 + framing_runs * 0.0022))
    return multiplier, Factor(
        name="Catcher framing",
        detail=f"{framing_runs:+.1f} framing runs/150 behind the plate",
        direction=_direction(multiplier),
    )


# ------------------------------------------------------------------ workload
def expected_batters_faced(
    pitcher: PitcherProfile, opponent_obp: float, implied_runs: float
) -> tuple[float, Factor]:
    """How many hitters the starter is likely to see.

    Batters faced is the volume term of the strikeout model and matters at least as much
    as the rate: an elite arm pulled after four innings will not reach a 6.5 line. It is
    driven by how deep this pitcher usually goes, inflated by baserunners (every one is
    an extra batter), and trimmed when the opponent projects to score heavily, because
    that is exactly when a starter gets an early hook.
    """
    innings = max(1.0, min(pitcher.innings_per_start, 8.0))
    obp = min(max(opponent_obp, 0.250), 0.400)

    # Outs needed / (1 - OBP) is the standard way to turn innings into batters faced.
    base = innings * _OUTS_PER_INNING / (1.0 - obp)

    # A heavy implied run total shortens outings; a light one lengthens them slightly.
    script = 1.0 - 0.035 * (implied_runs - 4.35)
    batters = max(8.0, min(base * max(0.85, min(1.10, script)), 32.0))

    detail = (
        f"{innings:.1f} IP/start, opponent OBP {obp:.3f}"
        f" -> {batters:.1f} batters faced"
    )
    if pitcher.starts < 5:
        detail += f" (only {pitcher.starts} starts of data)"
    return batters, Factor(name="Projected workload", detail=detail, impact=batters)


def expected_plate_appearances(
    lineup_slot: int, implied_runs: float
) -> tuple[float, Factor]:
    """Expected plate appearances from batting-order position.

    The leadoff hitter gets roughly three-quarters of a plate appearance more than the
    nine-hole over a game, which is a large edge on a "1+ hit" market -- an extra
    look is worth several percentage points of hit probability.
    """
    slot = lineup_slot if 1 <= lineup_slot <= 9 else 6
    # Nine batters share the team's trips to the plate; earlier slots bat sooner in the
    # final incomplete cycle, which is where the difference comes from.
    base = 4.65 - 0.085 * (slot - 1)
    scaled = base * (1.0 + 0.055 * (implied_runs - 4.35))
    plate_appearances = max(3.2, min(scaled, 5.4))

    return plate_appearances, Factor(
        name="Lineup spot",
        detail=(
            f"Batting {slot}{_ordinal_suffix(slot)}, team implied {implied_runs:.1f} runs"
            f" -> {plate_appearances:.2f} PA"
        ),
        impact=plate_appearances,
    )


def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ------------------------------------------------------------------- platoon
def platoon_factor(
    batter: BatterProfile, pitcher: PitcherProfile
) -> tuple[bool, Factor]:
    """Report the handedness matchup.

    The numeric effect is already carried by the split rates themselves; this only
    surfaces *why* the number moved, which is the first thing anyone checks.
    """
    advantaged = batter.is_platoon_advantaged(pitcher.throws)
    bats = "Switch" if batter.bats.value == "S" else batter.bats.value + "HB"
    detail = f"{bats} vs {pitcher.throws.value}HP -- " + (
        "platoon advantage to the hitter" if advantaged else "advantage to the pitcher"
    )
    return advantaged, Factor(
        name="Handedness",
        detail=detail,
        direction="positive" if advantaged else "negative",
    )


def lineup_handedness_summary(
    lineup: list[BatterProfile], pitcher: PitcherProfile
) -> Factor:
    """How the opposing lineup is stacked against this starter's hand."""
    if not lineup:
        return Factor(
            name="Opposing lineup",
            detail="Lineup not posted -- using team-average handedness split",
            direction="neutral",
        )
    advantaged = sum(1 for b in lineup if b.is_platoon_advantaged(pitcher.throws))
    return Factor(
        name="Opposing lineup",
        detail=(
            f"{advantaged} of {len(lineup)} hitters have the platoon edge on "
            f"{pitcher.throws.value}HP"
        ),
        direction="negative" if advantaged >= 6 else "neutral",
    )


def defense_hit_multiplier(oaa: float) -> tuple[float, Factor] | tuple[float, None]:
    """Team defensive range, as a multiplier on the opponent's hit rate."""
    if abs(oaa) < 1.0:
        return 1.0, None
    multiplier = max(0.95, min(1.05, 1.0 - oaa * 0.0018))
    return multiplier, Factor(
        name="Defense",
        detail=f"Fielding {oaa:+.0f} outs above average behind the pitcher",
        direction=_direction(multiplier),
    )


def altitude_note(park: ParkContext) -> Factor | None:
    if park.altitude_ft < 3000:
        return None
    return Factor(
        name="Altitude",
        detail=(
            f"{park.altitude_ft:,.0f} ft -- thin air flattens breaking balls and "
            "carries batted balls"
        ),
        direction="neutral",
    )


def confidence_from_context(
    game: MlbGameContext, sample_size: float, sample_target: float
) -> float:
    """A 0-1 confidence score shown next to every projection.

    Confidence is not probability. It answers "how much of this projection came from
    this player's own data rather than from a prior", and it is what stops a
    thin-sample outlier from topping the Best Value board.
    """
    sample_score = min(1.0, sample_size / max(sample_target, 1e-9))
    lineup_score = 1.0 if game.lineups_confirmed else 0.55
    weather_score = 1.0 if game.weather.source not in ("unavailable", "default") else 0.85
    return round(
        max(0.05, min(1.0, 0.55 * sample_score + 0.30 * lineup_score + 0.15 * weather_score)),
        3,
    )
