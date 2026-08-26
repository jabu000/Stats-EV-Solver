"""Game spreads and totals, used to derive implied team totals.

The market line is the single best predictor of game script, and game script drives
prop volume: a team favoured by two touchdowns runs the ball late, a 54-point total
means more snaps and more passing for everyone. Rather than trying to out-model the
market on game outcomes, we take its number and spend our modelling effort on how a
given player's usage maps onto it.

ESPN's public scoreboard carries odds without a key. When it is unavailable the models
fall back to league-average totals and the projection's confidence is marked down.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain import League
from app.providers.base import Provider, ProviderError

_ESPN_PATHS = {
    League.NFL: "football/nfl",
    League.CFB: "football/college-football",
    League.MLB: "baseball/mlb",
}


@dataclass
class GameOdds:
    game_id: str
    home_team: str
    away_team: str
    #: Signed from the home team's perspective: negative means the home side is favoured.
    spread: float | None = None
    total: float | None = None
    source: str = "espn"


class MarketProvider(Provider):
    name = "market"
    label = "Market lines (ESPN)"
    base_url = "https://site.api.espn.com/apis/site/v2/sports"

    def odds(self, league: League, on: date) -> dict[str, GameOdds]:
        """Odds keyed by both team abbreviations, so lookup works from either side."""
        path = _ESPN_PATHS[league]
        try:
            result = self.fetch(
                f"/{path}/scoreboard",
                fixture=f"{league.value.lower()}_{on.isoformat()}",
                params={"dates": on.strftime("%Y%m%d"), "limit": 400},
            )
        except ProviderError:
            return {}

        out: dict[str, GameOdds] = {}
        for event in (result.payload or {}).get("events") or []:
            parsed = self._parse_event(event)
            if parsed is None:
                continue
            out[parsed.game_id] = parsed
            out[f"{parsed.away_team}@{parsed.home_team}"] = parsed
            out[parsed.home_team] = parsed
            out[parsed.away_team] = parsed
        return out

    @staticmethod
    def _parse_event(event: dict) -> GameOdds | None:
        competitions = event.get("competitions") or []
        if not competitions:
            return None
        competition = competitions[0]

        home = away = None
        for competitor in competition.get("competitors") or []:
            team = (competitor.get("team") or {}).get("abbreviation")
            if competitor.get("homeAway") == "home":
                home = team
            elif competitor.get("homeAway") == "away":
                away = team
        if not home or not away:
            return None

        spread = total = None
        odds_rows = competition.get("odds") or []
        if odds_rows:
            row = odds_rows[0]
            total = _to_float(row.get("overUnder"))
            spread = _to_float(row.get("spread"))
            if spread is None:
                # Fall back to parsing "KC -3.5" out of the details string.
                spread = _spread_from_details(str(row.get("details") or ""), home)
        return GameOdds(
            game_id=str(event.get("id") or ""),
            home_team=home.upper(),
            away_team=away.upper(),
            spread=spread,
            total=total,
        )

    def health_check(self) -> tuple[bool, str, str]:
        found = self.odds(League.NFL, date.today())
        if not found:
            return False, "empty", "no games or odds returned"
        # `odds` deliberately keys each game under several aliases, so dedupe by game
        # id rather than by object -- GameOdds is a mutable dataclass and unhashable.
        unique = {odds.game_id: odds for odds in found.values()}
        with_total = sum(1 for o in unique.values() if o.total is not None)
        return True, "ok", f"{with_total} of {len(unique)} games have a posted total"


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread_from_details(details: str, home_team: str) -> float | None:
    """ESPN writes the spread as e.g. "KC -3.5", always naming the favourite.

    We store it home-relative, so a favoured road team flips the sign.
    """
    parts = details.split()
    if len(parts) < 2:
        return None
    favourite, number = parts[0], parts[-1]
    value = _to_float(number)
    if value is None:
        return None
    return value if favourite.upper() == home_team.upper() else -value
