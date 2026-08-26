"""Fetch actual outcomes so published picks can be graded automatically.

Without this the platform records what it said and never finds out whether it was
right, which means calibration never engages and the stated probabilities stay
uncorrected forever. Manual grading exists but nobody does it nightly.

Every fetcher returns the same shape -- ``{(canonical_player_id, market): actual}`` --
keyed by the *stats-provider* id that `ProjectionRow.player_key` now stores, so grading
is a direct lookup with no name matching at settle time. Name resolution already
happened when the pick was published; redoing it here would be a second chance to get
it wrong.

All three are fixture-backed like every other provider, so grading is testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain import League, Market
from app.providers.base import Provider, ProviderError
from app.providers.cfbd import CfbdProvider
from app.providers.nflverse import NflverseProvider, _f, _pick

#: (canonical player id, market) -> the stat the player actually recorded.
ResultMap = dict[tuple[str, str], float]


@dataclass
class ResultFetch:
    """What a grading run managed to collect, and what it could not."""

    results: ResultMap = field(default_factory=dict)
    source: str = "unknown"
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.results)


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


# --------------------------------------------------------------------- baseball
class MlbResultsProvider(Provider):
    """Actual MLB stat lines for a single date.

    Uses the `byDateRange` stat type with the same start and end date, which returns one
    row per player for that day. Two calls -- hitting and pitching -- settle an entire
    slate, versus one boxscore request per game.
    """

    name = "mlb_statsapi"
    label = "MLB StatsAPI (results)"
    base_url = "https://statsapi.mlb.com/api/v1"

    def results(self, on: date) -> ResultFetch:
        fetch = ResultFetch(source="mlb_statsapi")

        for group, markets in (
            ("hitting", {Market.HITS_1_PLUS: "hits"}),
            ("pitching", {Market.STRIKEOUTS: "strikeOuts"}),
        ):
            try:
                rows = self._by_date(group, on)
            except ProviderError as exc:
                fetch.problems.append(f"{group}: {exc.message}")
                continue

            for row in rows:
                player_id = str(((row.get("player") or {}).get("id")) or "")
                stat = row.get("stat") or {}
                if not player_id:
                    continue
                for market, key in markets.items():
                    value = _num(stat.get(key))
                    if value is not None:
                        fetch.results[(player_id, market.value)] = value

        if not fetch.results and not fetch.problems:
            fetch.problems.append(f"no MLB stat lines returned for {on.isoformat()}")
        return fetch

    def _by_date(self, group: str, on: date) -> list[dict]:
        result = self.fetch(
            "/stats",
            fixture=f"results_{group}_{on.isoformat()}",
            params={
                "stats": "byDateRange",
                "group": group,
                "startDate": on.isoformat(),
                "endDate": on.isoformat(),
                "sportId": 1,
                "limit": 2000,
                "playerPool": "All",
            },
        )
        rows: list[dict] = []
        for block in (result.payload or {}).get("stats") or []:
            rows.extend(block.get("splits") or [])
        return rows


# --------------------------------------------------------------------- football
#: Weekly-row field -> market, shared by the NFL and CFB fetchers.
_FOOTBALL_FIELDS: dict[Market, str] = {
    Market.RECEIVING_YARDS: "receiving_yards",
    Market.RUSHING_YARDS: "rushing_yards",
    Market.PASSING_YARDS: "passing_yards",
    Market.RECEPTIONS: "receptions",
}


def _football_results_from_rows(rows: list[dict], fetch: ResultFetch) -> None:
    """Map weekly player rows onto market outcomes.

    Anytime TD is the one that needs care: it settles on rushing *plus* receiving
    touchdowns, so reading either column alone would wrongly grade a receiver who scored
    on an end-around as a loss.
    """
    for row in rows:
        player_id = str(_pick(row, "player_id", "") or "")
        if not player_id:
            continue
        for market, field_name in _FOOTBALL_FIELDS.items():
            fetch.results[(player_id, market.value)] = _f(row, field_name)
        fetch.results[(player_id, Market.ANYTIME_TD.value)] = (
            _f(row, "rushing_tds") + _f(row, "receiving_tds")
        )


class NflResultsProvider:
    """NFL results, reusing the weekly rows the projection model already reads."""

    name = "nflverse"
    label = "nflverse (NFL results)"

    def results(self, season: int, week: int) -> ResultFetch:
        fetch = ResultFetch(source="nflverse")
        try:
            rows = NflverseProvider().weekly_rows(season)
        except ProviderError as exc:
            fetch.problems.append(exc.message)
            return fetch

        weekly = [row for row in rows if int(_f(row, "week") or 0) == week]
        if not weekly:
            fetch.problems.append(
                f"nflverse has no rows for {season} week {week} yet -- "
                "results usually land a day or two after the games"
            )
            return fetch

        _football_results_from_rows(weekly, fetch)
        return fetch


class CfbResultsProvider(CfbdProvider):
    """College football results from CFBD's per-game player stats.

    CFBD returns a deeply nested structure -- games, then teams, then stat categories,
    then athletes -- so most of the work is flattening it into the same weekly-row shape
    the NFL path already handles.
    """

    label = "CollegeFootballData (CFB results)"

    #: CFBD (category, statType) -> weekly-row field.
    _STAT_MAP = {
        ("receiving", "YDS"): "receiving_yards",
        ("receiving", "REC"): "receptions",
        ("receiving", "TD"): "receiving_tds",
        ("rushing", "YDS"): "rushing_yards",
        ("rushing", "TD"): "rushing_tds",
        ("passing", "YDS"): "passing_yards",
    }

    def results(self, season: int, week: int) -> ResultFetch:
        fetch = ResultFetch(source="cfbd")
        configured, why = self.is_configured()
        if not configured:
            fetch.problems.append(why)
            return fetch

        try:
            payload = self.fetch(
                "/games/players",
                fixture=f"results_{season}",
                params={"year": season, "week": week, "seasonType": "regular"},
            ).payload
        except ProviderError as exc:
            fetch.problems.append(exc.message)
            return fetch

        rows = self._flatten(payload or [])
        if not rows:
            fetch.problems.append(f"CFBD returned no player stats for week {week}")
            return fetch

        _football_results_from_rows(rows, fetch)
        return fetch

    @classmethod
    def _flatten(cls, payload: list) -> list[dict]:
        rows: dict[str, dict] = {}
        for game in payload or []:
            for team in (game or {}).get("teams") or []:
                for category in team.get("categories") or []:
                    category_name = str(category.get("name") or "").lower()
                    for stat_type in category.get("types") or []:
                        type_name = str(stat_type.get("name") or "").upper()
                        field_name = cls._STAT_MAP.get((category_name, type_name))
                        if field_name is None:
                            continue
                        for athlete in stat_type.get("athletes") or []:
                            player_id = str(athlete.get("id") or "")
                            value = _num(athlete.get("stat"))
                            if not player_id or value is None:
                                continue
                            rows.setdefault(player_id, {"player_id": player_id})[
                                field_name
                            ] = value
        return list(rows.values())


# ------------------------------------------------------------------- dispatch
#: Football results are published per week, not per date. These are the usual opening
#: weekends -- NFL kicks off the Thursday after Labor Day, college a week or so earlier.
_SEASON_START_MONTH_DAY = {League.NFL: (9, 4), League.CFB: (8, 26)}


def estimate_week(league: League, on: date, season: int | None = None) -> int:
    """Best guess at the football week containing `on`.

    A scheduled grading job has no way to know the week, and refusing to grade without
    one would mean football never gets settled automatically. So we estimate, and every
    caller that relies on the estimate says so in its output -- a silently wrong week
    would grade picks against the wrong games, which is far worse than not grading.
    """
    season = season or (on.year if on.month >= 8 else on.year - 1)
    month, day = _SEASON_START_MONTH_DAY[league]
    opening = date(season, month, day)
    if on < opening:
        return 1
    return max(1, min((on - opening).days // 7 + 1, 20))


def fetch_results(
    league: League, on: date, season: int | None = None, week: int | None = None
) -> ResultFetch:
    """Results for one league on one date."""
    if league is League.MLB:
        return MlbResultsProvider().results(on)

    season = season or (on.year if on.month >= 8 else on.year - 1)

    inferred = week is None
    if inferred:
        week = estimate_week(league, on, season)

    fetch = (
        NflResultsProvider().results(season, week)
        if league is League.NFL
        else CfbResultsProvider().results(season, week)
    )
    if inferred:
        fetch.problems.append(
            f"week was not supplied, so week {week} was inferred from the date "
            f"({on.isoformat()}). Pass an explicit week if that looks wrong."
        )
    return fetch
