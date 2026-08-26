"""NFL player usage and efficiency from nflverse's public data releases.

nflverse publishes season-long weekly player stats as CSV on GitHub releases, free and
without a key. We aggregate the weekly rows into the usage *shares* the models actually
want -- target share, air-yards share, rush share, red-zone share -- because a raw
per-game average bakes in last season's game scripts, while a share travels correctly
when this week's pace and pass rate are different.

Live mode downloads the CSV; fixture mode reads the same rows as JSON. Both feed one
aggregation path, so the offline board is computed by exactly the code that runs live.
"""

from __future__ import annotations

import io
from collections import defaultdict
from typing import Any

from app.features.context import FootballPlayerProfile, FootballTeamContext
from app.providers.base import Provider, ProviderError
from app.static_data.loader import priors

#: nflverse has renamed these files across releases; try newest first.
_STAT_FILES = (
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv",
    "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.csv",
)

#: Column aliases, again because the schema has drifted between releases.
_COLUMNS = {
    "player_id": ("player_id", "gsis_id", "playerId"),
    "name": ("player_display_name", "player_name", "full_name"),
    "position": ("position", "position_group"),
    "team": ("recent_team", "team", "team_abbr"),
    "week": ("week",),
    "targets": ("targets",),
    "receptions": ("receptions",),
    "receiving_yards": ("receiving_yards",),
    "air_yards": ("receiving_air_yards", "air_yards"),
    "carries": ("carries", "rushing_attempts"),
    "rushing_yards": ("rushing_yards",),
    "attempts": ("attempts", "passing_attempts"),
    "passing_yards": ("passing_yards",),
    "receiving_tds": ("receiving_tds",),
    "rushing_tds": ("rushing_tds",),
}


#: Prior weight for touchdown-derived red-zone shares. Touchdowns are noisy enough to
#: need real regression, but shrinking too hard erases the goal-line back entirely --
#: and a distinctive goal-line role is precisely the signal an anytime-TD market prices.
REDZONE_PRIOR_WEIGHT = 4.0


def _pick(row: dict, field: str, default: Any = None) -> Any:
    for key in _COLUMNS.get(field, (field,)):
        if key in row and row[key] not in (None, "", "NA"):
            return row[key]
    return default


def _f(row: dict, field: str) -> float:
    try:
        return float(_pick(row, field, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


class NflverseProvider(Provider):
    name = "nflverse"
    label = "nflverse (NFL stats)"
    league_key = "NFL"

    def weekly_rows(self, season: int) -> list[dict]:
        """Weekly player stat rows for a season."""
        if not self.settings.is_live:
            return self.fetch("", fixture=f"weekly_{season}").payload or []

        errors: list[str] = []
        for template in _STAT_FILES:
            url = template.format(season=season)
            try:
                result = self.fetch(
                    url, fixture=f"weekly_{season}", parse="text"
                )
                return self._parse_csv(result.payload)
            except ProviderError as exc:
                errors.append(f"{url.rsplit('/', 1)[-1]}: {exc.message}")
        raise ProviderError(self.name, "; ".join(errors), status="unavailable")

    @staticmethod
    def _parse_csv(text: str) -> list[dict]:
        import csv

        return list(csv.DictReader(io.StringIO(text)))

    # ----------------------------------------------------------- aggregation
    def build_profiles(
        self, season: int, through_week: int | None = None
    ) -> tuple[dict[str, FootballPlayerProfile], dict[str, FootballTeamContext]]:
        rows = self.weekly_rows(season)
        return aggregate_football_rows(rows, self.league_key, through_week)

    def health_check(self) -> tuple[bool, str, str]:
        from datetime import date

        season = date.today().year if date.today().month >= 8 else date.today().year - 1
        try:
            rows = self.weekly_rows(season)
        except ProviderError as exc:
            return False, exc.status, exc.message
        return bool(rows), "ok" if rows else "empty", f"{len(rows)} weekly rows"


def aggregate_football_rows(
    rows: list[dict], league_key: str, through_week: int | None = None
) -> tuple[dict[str, FootballPlayerProfile], dict[str, FootballTeamContext]]:
    """Turn weekly player rows into shrunk usage shares and team pace context.

    Shares are computed against team totals from the same rows, so they are internally
    consistent even when the source is partial. Per-game yardage variance is kept where
    a player has enough games to estimate it -- that spread is what separates a boom/bust
    deep threat from a possession receiver at the same projected mean, and it is exactly
    what the tails of a yardage prop are sensitive to.
    """
    league_priors = priors(league_key)

    per_player: dict[str, dict[str, Any]] = {}
    team_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    team_weeks: dict[str, set] = defaultdict(set)

    for row in rows:
        if through_week is not None and _f(row, "week") > through_week:
            continue
        pid = str(_pick(row, "player_id", "") or "")
        team = str(_pick(row, "team", "") or "").upper()
        if not pid or not team:
            continue

        bucket = per_player.setdefault(
            pid,
            {
                "name": str(_pick(row, "name", "Unknown")),
                "position": str(_pick(row, "position", "WR") or "WR").upper(),
                "team": team,
                "games": 0,
                "targets": 0.0, "receptions": 0.0, "receiving_yards": 0.0,
                "air_yards": 0.0, "carries": 0.0, "rushing_yards": 0.0,
                "attempts": 0.0, "passing_yards": 0.0,
                "receiving_tds": 0.0, "rushing_tds": 0.0,
                "rec_yard_games": [], "rush_yard_games": [], "pass_yard_games": [],
            },
        )
        bucket["team"] = team  # last team seen wins, handling mid-season trades
        bucket["games"] += 1
        for field in (
            "targets", "receptions", "receiving_yards", "air_yards", "carries",
            "rushing_yards", "attempts", "passing_yards", "receiving_tds", "rushing_tds",
        ):
            value = _f(row, field)
            bucket[field] += value
            team_totals[team][field] += value
        bucket["rec_yard_games"].append(_f(row, "receiving_yards"))
        bucket["rush_yard_games"].append(_f(row, "rushing_yards"))
        bucket["pass_yard_games"].append(_f(row, "passing_yards"))
        team_weeks[team].add(_f(row, "week"))

    profiles: dict[str, FootballPlayerProfile] = {}
    for pid, bucket in per_player.items():
        team = bucket["team"]
        totals = team_totals[team]
        games = max(bucket["games"], 1)
        profiles[pid] = _to_profile(pid, bucket, totals, games, league_priors)

    contexts: dict[str, FootballTeamContext] = {}
    for team, totals in team_totals.items():
        weeks = max(len(team_weeks[team]), 1)
        plays = (totals["attempts"] + totals["carries"]) / weeks
        pass_rate = _derived_pass_rate(
            totals["attempts"], totals["carries"], league_priors["pass_rate"]
        )
        contexts[team] = FootballTeamContext(
            team=team,
            # Recorded plays exclude sacks/penalties, so scale to a true play count.
            plays_per_game=plays * 1.08 if plays > 0 else league_priors["plays_per_game"],
            pass_rate=pass_rate,
            proe=pass_rate - league_priors["pass_rate"],
        )
    return profiles, contexts


#: Below this many recorded plays a team's derived pass rate is noise, not a tendency.
MIN_PLAYS_FOR_PASS_RATE = 40.0


def _derived_pass_rate(attempts: float, carries: float, prior: float) -> float:
    """Team pass rate, falling back to the prior when the sample cannot support one.

    A partial feed can leave a team with carries but no recorded pass attempts. Dividing
    anyway yields a 0% pass rate, which would tell the model the team never throws --
    a far worse answer than admitting we do not know.
    """
    total = attempts + carries
    if total < MIN_PLAYS_FOR_PASS_RATE or attempts <= 0:
        return prior
    return attempts / total


def _estimate_snap_share(position: str, b: dict, games: int) -> float:
    """Rough snap share from usage, since the weekly stat file carries no snap counts.

    Each position is judged against a plausible full-time workload for that position
    rather than against a single receiver-shaped yardstick.
    """
    per_game = {
        "QB": b["attempts"] / max(games, 1) / 32.0,
        "RB": (b["carries"] + b["targets"]) / max(games, 1) / 18.0,
        "WR": b["targets"] / max(games, 1) / 8.0,
        "TE": b["targets"] / max(games, 1) / 6.0,
    }.get(position, b["targets"] / max(games, 1) / 8.0)
    return max(0.05, min(0.98, 0.25 + 0.70 * min(1.0, per_game)))


def _to_profile(
    pid: str, b: dict, totals: dict[str, float], games: int, p: dict[str, float]
) -> FootballPlayerProfile:
    def share(
        value: float, total: float, prior: float, weight: float, n: float | None = None
    ) -> float:
        """Empirical-Bayes shrink of a share toward its prior.

        `n` is the *event* count the share is estimated from, which is not always the
        numerator: air-yards share is estimated from how many targets we saw, not from
        the yardage itself. Using the yardage would treat one deep target as 40
        observations and barely shrink at all.
        """
        if total <= 0:
            return prior
        observed = value / total
        events = n if n is not None else value
        return (observed * events + prior * weight) / (events + weight)

    def rate(num: float, den: float, prior: float, weight: float) -> float:
        return (num + prior * weight) / (den + weight) if den + weight > 0 else prior

    position = b["position"]
    target_weight = p["target_share_prior_weight"] / max(games, 1) ** 0.5
    rush_weight = p["rush_share_prior_weight"] / max(games, 1) ** 0.5

    def std(series: list[float]) -> float | None:
        clean = [v for v in series if v is not None]
        if len(clean) < 4:
            return None
        mean = sum(clean) / len(clean)
        var = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
        return var**0.5

    return FootballPlayerProfile(
        player_key=pid,
        name=b["name"],
        position=position,
        team=b["team"],
        games=games,
        snap_share=_estimate_snap_share(position, b, games),
        target_share=share(b["targets"], totals["targets"], 0.12, target_weight),
        air_yards_share=share(
            b["air_yards"], totals["air_yards"], 0.12, target_weight, n=b["targets"]
        ),
        rush_share=share(b["carries"], totals["carries"], 0.10, rush_weight),
        # Weekly stat files carry no red-zone splits, so touchdown share is used as a
        # proxy for goal-line usage. It is noisy -- a handful of scores drives it -- so
        # it is shrunk hard and the anytime-TD model leans on volume as well.
        redzone_target_share=share(
            b["receiving_tds"], max(totals["receiving_tds"], 1), 0.12,
            REDZONE_PRIOR_WEIGHT, n=b["receiving_tds"],
        ),
        redzone_rush_share=share(
            b["rushing_tds"], max(totals["rushing_tds"], 1), 0.10,
            REDZONE_PRIOR_WEIGHT, n=b["rushing_tds"],
        ),
        yards_per_target=rate(
            b["receiving_yards"], b["targets"], p["yards_per_target"], p["efficiency_prior_weight"] * 0.25
        ),
        catch_rate=rate(b["receptions"], b["targets"], p["catch_rate"], p["efficiency_prior_weight"] * 0.4),
        yards_per_carry=rate(
            b["rushing_yards"], b["carries"], p["yards_per_carry"], p["efficiency_prior_weight"]
        ),
        yards_per_attempt=rate(
            b["passing_yards"], b["attempts"], p["yards_per_attempt"], p["efficiency_prior_weight"]
        ),
        dropback_share=min(1.0, b["attempts"] / max(totals["attempts"], 1))
        if position == "QB"
        else 0.0,
        yards_std=std(
            b["pass_yard_games"] if position == "QB"
            else b["rec_yard_games"] if position in ("WR", "TE")
            else b["rush_yard_games"]
        ),
    )
