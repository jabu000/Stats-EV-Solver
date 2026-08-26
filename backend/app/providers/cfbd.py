"""College football data from CollegeFootballData.com.

CFBD needs a free API key, entered in the Settings tab. Without one this provider
reports itself as unconfigured rather than failing mid-pipeline, and the CFB board
falls back to prior-driven projections with the confidence marked down accordingly.

CFBD returns season stats in *long* format -- one row per (player, category, statType) --
so most of the work here is pivoting that back into per-player totals. College numbers
are then shrunk far harder than NFL ones: the talent gap between a playoff team and a
bottom-tier opponent is enormous, sample sizes are small, and blowouts distort usage.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.features.context import FootballPlayerProfile, FootballTeamContext
from app.providers.base import Provider, ProviderError
from app.providers.nflverse import _derived_pass_rate, _to_profile
from app.static_data.loader import priors

#: CFBD (category, statType) -> our accumulator field.
_STAT_MAP: dict[tuple[str, str], str] = {
    ("receiving", "REC"): "receptions",
    ("receiving", "YDS"): "receiving_yards",
    ("receiving", "TD"): "receiving_tds",
    ("rushing", "CAR"): "carries",
    ("rushing", "YDS"): "rushing_yards",
    ("rushing", "TD"): "rushing_tds",
    ("passing", "ATT"): "attempts",
    ("passing", "YDS"): "passing_yards",
    ("passing", "TD"): "passing_tds",
}

_FIELDS = (
    "targets", "receptions", "receiving_yards", "air_yards", "carries",
    "rushing_yards", "attempts", "passing_yards", "receiving_tds", "rushing_tds",
)


class CfbdProvider(Provider):
    name = "cfbd"
    label = "CollegeFootballData (CFB)"
    base_url = "https://api.collegefootballdata.com"
    requires_key = True
    league_key = "CFB"

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__()
        self._key_override = api_key

    @property
    def api_key(self) -> str:
        return (self._key_override or self.settings.cfbd_api_key or "").strip()

    def headers(self) -> dict[str, str]:
        headers = super().headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def is_configured(self) -> tuple[bool, str]:
        if self.settings.is_live and not self.api_key:
            return False, "No CFBD API key. Add a free key in Settings to enable CFB."
        return True, ""

    # ------------------------------------------------------------------ fetches
    def season_player_stats(self, season: int) -> list[dict]:
        configured, why = self.is_configured()
        if not configured:
            raise ProviderError(self.name, why, status="not_configured")
        result = self.fetch(
            "/stats/player/season",
            fixture=f"player_season_{season}",
            params={"year": season, "seasonType": "regular"},
        )
        return result.payload or []

    def team_games(self, season: int) -> list[dict]:
        result = self.fetch(
            "/games",
            fixture=f"games_{season}",
            params={"year": season, "seasonType": "regular"},
        )
        return result.payload or []

    def ratings(self, season: int) -> dict[str, float]:
        """SP+-style team ratings, used to opponent-adjust and to shrink."""
        try:
            result = self.fetch(
                "/ratings/sp", fixture=f"ratings_{season}", params={"year": season}
            )
        except ProviderError:
            return {}
        out: dict[str, float] = {}
        for row in result.payload or []:
            team = row.get("team")
            rating = row.get("rating")
            if team is not None and rating is not None:
                try:
                    out[str(team)] = float(rating)
                except (TypeError, ValueError):
                    continue
        return out

    # ----------------------------------------------------------- aggregation
    def games_played_by_team(self, season: int) -> dict[str, int]:
        """Completed games per team.

        Teams do not all play the same number of games -- byes fall in different weeks,
        and games get postponed -- so a single slate-wide constant misstates every
        per-game rate it touches. Counting each team's own completed games is barely
        more work and is right.
        """
        counts: dict[str, int] = {}
        try:
            games = self.team_games(season)
        except ProviderError:
            return counts

        for game in games or []:
            if not _is_completed(game):
                continue
            for key in ("home_team", "away_team", "homeTeam", "awayTeam"):
                team = game.get(key)
                if team:
                    counts[str(team).upper()] = counts.get(str(team).upper(), 0) + 1
        return counts

    def build_profiles(
        self, season: int, games_played: int = 8
    ) -> tuple[dict[str, FootballPlayerProfile], dict[str, FootballTeamContext]]:
        rows = self.season_player_stats(season)
        ratings = self.ratings(season)
        return build_cfb_profiles(
            rows, games_played, ratings, self.games_played_by_team(season)
        )

    def health_check(self) -> tuple[bool, str, str]:
        configured, why = self.is_configured()
        if not configured:
            return False, "not_configured", why
        from datetime import date

        season = date.today().year if date.today().month >= 7 else date.today().year - 1
        try:
            rows = self.season_player_stats(season)
        except ProviderError as exc:
            return False, exc.status, exc.message
        return bool(rows), "ok" if rows else "empty", f"{len(rows)} player stat rows"


def build_cfb_profiles(
    rows: list[dict],
    games_played: int,
    ratings: dict[str, float] | None = None,
    games_by_team: dict[str, int] | None = None,
) -> tuple[dict[str, FootballPlayerProfile], dict[str, FootballTeamContext]]:
    """Pivot CFBD's long-format season stats into profiles and team contexts.

    `games_by_team` gives each team its own completed-game count; `games_played` is only
    the fallback for teams the schedule feed did not cover.
    """
    ratings = ratings or {}
    games_by_team = games_by_team or {}

    def team_games(team: str) -> int:
        return max(1, games_by_team.get(team.upper(), games_played))
    league_priors = priors("CFB")

    per_player: dict[str, dict[str, Any]] = {}
    team_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for row in rows:
        field = _STAT_MAP.get(
            (
                str(row.get("category") or "").lower(),
                str(row.get("statType") or "").upper(),
            )
        )
        if field is None or field == "passing_tds":
            continue
        try:
            value = float(row.get("stat") or 0)
        except (TypeError, ValueError):
            continue

        pid = str(row.get("playerId") or row.get("player") or "")
        team = str(row.get("team") or "").upper()
        if not pid or not team:
            continue

        bucket = per_player.setdefault(
            pid,
            {
                "name": str(row.get("player") or "Unknown"),
                "position": "WR",
                "team": team,
                "games": team_games(team),
                **{f: 0.0 for f in _FIELDS},
                "rec_yard_games": [], "rush_yard_games": [], "pass_yard_games": [],
            },
        )
        bucket[field] = bucket.get(field, 0.0) + value
        team_totals[team][field] += value

    profiles: dict[str, FootballPlayerProfile] = {}
    for pid, bucket in per_player.items():
        # CFBD carries no target counts, so receptions stand in for targets and the
        # catch rate falls back to the league prior. This is a genuine data gap, and it
        # is why CFB reception props are projected with lower confidence than NFL ones.
        if bucket["targets"] == 0 and bucket["receptions"] > 0:
            bucket["targets"] = bucket["receptions"] / league_priors["catch_rate"]
        bucket["position"] = _infer_position(bucket)
        totals = team_totals[bucket["team"]]
        if totals["targets"] == 0:
            totals["targets"] = totals["receptions"] / league_priors["catch_rate"]
        profiles[pid] = _to_profile(
            pid, bucket, totals, max(bucket["games"], 1), league_priors
        )

    contexts: dict[str, FootballTeamContext] = {}
    for team, totals in team_totals.items():
        plays = (totals["attempts"] + totals["carries"]) / team_games(team)
        pass_rate = _derived_pass_rate(
            totals["attempts"], totals["carries"], league_priors["pass_rate"]
        )
        contexts[team] = FootballTeamContext(
            team=team,
            plays_per_game=plays * 1.08 if plays > 0 else league_priors["plays_per_game"],
            pass_rate=pass_rate,
            proe=pass_rate - league_priors["pass_rate"],
            rating=ratings.get(team, 0.0),
        )
    return profiles, contexts


def _is_completed(game: dict) -> bool:
    """Whether a game has actually been played.

    CFBD spells this several ways across endpoint versions; a scored game counts even
    when no explicit completion flag is present.
    """
    for key in ("completed", "isCompleted"):
        if key in game:
            return bool(game[key])
    for key in ("home_points", "homePoints"):
        if game.get(key) is not None:
            return True
    return False


def _infer_position(bucket: dict) -> str:
    """CFBD season stats carry no position, so infer one from the usage mix."""
    if bucket["attempts"] > max(bucket["carries"], bucket["receptions"]) * 1.5:
        return "QB"
    if bucket["carries"] > bucket["receptions"] * 1.5:
        return "RB"
    return "WR"
