"""Underdog Pick'em line provider.

Underdog publishes no documented public API. The endpoint below is the one their web
client uses; it is usually readable without credentials, but that is not a promise --
they add Cloudflare challenges and auth requirements from time to time. So this module
has three ways in, tried in order by the ingest pipeline:

1. the unauthenticated endpoint,
2. the same endpoint with a bearer token the user pastes into the Settings tab,
3. a manual CSV/JSON paste, so a broken scrape never leaves the platform with nothing.

The response is a set of cross-referenced arrays (lines -> over_unders -> appearances
-> players/games), so `normalize` stitches them back together and, critically, maps
Underdog's stat keys onto our `Market` enum. Two of the requested markets are not
"over/under" markets at Underdog at all -- **1+ Hit** is a 0.5 line on `hits` and
**Anytime TD** is a 0.5 line on a rush+rec touchdown stat -- and they are recognised
here rather than being papered over downstream.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser

from app.domain import League, Market, Side
from app.providers.base import Provider, ProviderError

# Underdog stat key -> our market. Keys have been renamed across API versions, so
# several aliases map to the same market on purpose.
_STAT_KEY_MAP: dict[str, Market] = {
    # MLB -- pitcher strikeouts
    "strikeouts_thrown": Market.STRIKEOUTS,
    "pitcher_strikeouts": Market.STRIKEOUTS,
    "strikeouts": Market.STRIKEOUTS,
    "pitching_strikeouts": Market.STRIKEOUTS,
    # MLB -- batter hits (only a 0.5 line becomes "1+ Hit")
    "hits": Market.HITS_1_PLUS,
    "batter_hits": Market.HITS_1_PLUS,
    "hits_allowed": None,  # explicitly not a batter market
    # Football
    "receiving_yards": Market.RECEIVING_YARDS,
    "rec_yards": Market.RECEIVING_YARDS,
    "rushing_yards": Market.RUSHING_YARDS,
    "rush_yards": Market.RUSHING_YARDS,
    "passing_yards": Market.PASSING_YARDS,
    "pass_yards": Market.PASSING_YARDS,
    "receptions": Market.RECEPTIONS,
    "rush_rec_tds": Market.ANYTIME_TD,
    "rushing_receiving_touchdowns": Market.ANYTIME_TD,
    "touchdowns": Market.ANYTIME_TD,
    "total_touchdowns": Market.ANYTIME_TD,
    "tds": Market.ANYTIME_TD,
}

_SPORT_MAP: dict[str, League] = {
    "MLB": League.MLB,
    "BASEBALL": League.MLB,
    "NFL": League.NFL,
    "CFB": League.CFB,
    "NCAAF": League.CFB,
    "COLLEGE_FOOTBALL": League.CFB,
    "CFBALL": League.CFB,
}

# Markets that only exist as an "at least one" threshold.
_THRESHOLD_ONLY = {Market.HITS_1_PLUS: 0.5, Market.ANYTIME_TD: 0.5}


@dataclass
class LineOption:
    """One side of a line, with the payout multiplier that sets its break-even."""

    option_id: str
    side: Side
    payout_multiplier: float = 1.0


@dataclass
class NormalizedLine:
    """An Underdog line flattened into something the models can price."""

    line_id: str
    league: League
    market: Market
    player_name: str
    player_id: str | None
    position: str | None
    team: str | None
    opponent: str | None
    game_id: str | None
    game_label: str | None
    starts_at: datetime | None
    stat_line: float
    options: list[LineOption] = field(default_factory=list)
    raw_stat_key: str | None = None
    correlation_group: str | None = None

    @property
    def event_date(self) -> str | None:
        return self.starts_at.date().isoformat() if self.starts_at else None


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _index_by_id(rows: Any) -> dict[str, dict]:
    if not isinstance(rows, list):
        return {}
    return {str(row.get("id")): row for row in rows if isinstance(row, dict) and row.get("id")}


def map_stat_key(stat_key: str | None, stat_line: float | None) -> Market | None:
    """Resolve an Underdog stat key + line value to one of our markets.

    A threshold-only market is only recognised at its 0.5 line: a 1.5-hit line is a
    different bet from "1+ hit" and must not be relabelled as one.
    """
    if not stat_key:
        return None
    market = _STAT_KEY_MAP.get(stat_key.strip().lower())
    if market is None:
        return None
    required = _THRESHOLD_ONLY.get(market)
    if required is not None and (stat_line is None or abs(stat_line - required) > 1e-9):
        return None
    return market


class UnderdogProvider(Provider):
    name = "underdog"
    label = "Underdog Pick'em"
    base_url = "https://api.underdogfantasy.com"

    #: Tried in order -- Underdog has bumped this version several times.
    ENDPOINTS = (
        "/beta/v6/over_under_lines",
        "/beta/v5/over_under_lines",
    )

    def __init__(self, token: str | None = None) -> None:
        super().__init__()
        self._token_override = token

    @property
    def token(self) -> str:
        return (self._token_override or self.settings.underdog_token or "").strip()

    def headers(self) -> dict[str, str]:
        headers = super().headers()
        headers["Referer"] = "https://underdogfantasy.com/"
        headers["Origin"] = "https://underdogfantasy.com"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # -------------------------------------------------------------- fetching
    def fetch_lines(self) -> tuple[dict, str]:
        """Return the raw payload plus where it came from.

        In fixture mode this reads the recorded slate. In live mode it walks the
        endpoint versions until one answers, so a version bump degrades to the older
        endpoint rather than to an empty board.
        """
        if not self.settings.is_live:
            result = self.fetch("", fixture="over_under_lines")
            return result.payload, "fixture"

        errors: list[str] = []
        for endpoint in self.ENDPOINTS:
            try:
                result = self.fetch(endpoint, fixture="over_under_lines")
                if isinstance(result.payload, dict) and result.payload.get("over_under_lines"):
                    return result.payload, result.source
                errors.append(f"{endpoint}: responded without any lines")
            except ProviderError as exc:
                errors.append(f"{endpoint}: {exc.message}")

        hint = (
            " Paste a bearer token in Settings, or import the slate manually."
            if not self.token
            else " The saved token may have expired."
        )
        raise ProviderError(self.name, "; ".join(errors) + hint, status="unavailable")

    # ------------------------------------------------------------ normalising
    def normalize(self, payload: dict, league: League | None = None) -> list[NormalizedLine]:
        """Flatten the cross-referenced arrays into `NormalizedLine` records."""
        if not isinstance(payload, dict):
            raise ProviderError(self.name, "expected a JSON object at the top level")

        players = _index_by_id(payload.get("players"))
        appearances = _index_by_id(payload.get("appearances"))
        games = _index_by_id(payload.get("games"))
        solo_games = _index_by_id(payload.get("solo_games"))
        teams = _index_by_id(payload.get("teams"))
        all_games = {**games, **solo_games}

        lines: list[NormalizedLine] = []
        for raw in payload.get("over_under_lines") or []:
            normalized = self._normalize_one(
                raw, players, appearances, all_games, teams, league
            )
            if normalized is not None:
                lines.append(normalized)
        return lines

    def _normalize_one(
        self,
        raw: dict,
        players: dict[str, dict],
        appearances: dict[str, dict],
        games: dict[str, dict],
        teams: dict[str, dict],
        league_filter: League | None,
    ) -> NormalizedLine | None:
        if not isinstance(raw, dict):
            return None
        # Suspended/settled lines are still present in the payload; skip them.
        if str(raw.get("status", "active")).lower() not in ("active", "", "open"):
            return None

        over_under = raw.get("over_under") or {}
        appearance_stat = over_under.get("appearance_stat") or {}
        stat_key = appearance_stat.get("stat") or appearance_stat.get("display_stat")
        stat_line = _to_float(raw.get("stat_value"))
        if stat_line is None:
            return None

        market = map_stat_key(stat_key, stat_line)
        if market is None:
            return None

        appearance = appearances.get(str(appearance_stat.get("appearance_id") or "")) or {}
        player = players.get(str(appearance.get("player_id") or "")) or {}

        sport_id = str(player.get("sport_id") or appearance.get("sport_id") or "").upper()
        league = _SPORT_MAP.get(sport_id)
        if league is None:
            league = self._infer_league(market, sport_id)
        if league is None or (league_filter and league is not league_filter):
            return None
        # A football stat key never belongs to a baseball slate and vice versa.
        if league is League.MLB and market not in (Market.STRIKEOUTS, Market.HITS_1_PLUS):
            return None
        if league.is_football and market in (Market.STRIKEOUTS, Market.HITS_1_PLUS):
            return None

        player_name = self._player_name(player, over_under)
        if not player_name:
            return None

        team_id = str(appearance.get("team_id") or player.get("team_id") or "")
        team = self._team_abbr(teams.get(team_id))
        game = games.get(str(appearance.get("match_id") or "")) or {}
        home = self._team_abbr(teams.get(str(game.get("home_team_id") or "")))
        away = self._team_abbr(teams.get(str(game.get("away_team_id") or "")))
        opponent = None
        if team and home and away:
            opponent = away if team == home else home

        options = self._options(raw)
        if not options:
            return None

        return NormalizedLine(
            line_id=str(raw.get("id") or f"{player_name}-{market.value}-{stat_line}"),
            league=league,
            market=market,
            player_name=player_name,
            player_id=str(player.get("id")) if player.get("id") else None,
            position=player.get("position") or appearance.get("position"),
            team=team,
            opponent=opponent,
            game_id=str(game.get("id")) if game.get("id") else None,
            game_label=f"{away} @ {home}" if home and away else game.get("title"),
            starts_at=_parse_dt(game.get("scheduled_at") or raw.get("expires_at")),
            stat_line=stat_line,
            options=options,
            raw_stat_key=stat_key,
            correlation_group=over_under.get("strict_correlation_id"),
        )

    @staticmethod
    def _infer_league(market: Market, sport_id: str) -> League | None:
        """Last resort when the payload omits a usable sport id."""
        if "COLLEGE" in sport_id or "NCAA" in sport_id:
            return League.CFB
        if market in (Market.STRIKEOUTS, Market.HITS_1_PLUS):
            return League.MLB
        return League.NFL if sport_id in ("", "NFL") else None

    @staticmethod
    def _player_name(player: dict, over_under: dict) -> str | None:
        first = (player.get("first_name") or "").strip()
        last = (player.get("last_name") or "").strip()
        if first or last:
            return f"{first} {last}".strip()
        # Older payloads only carry a title like "Aaron Judge Hits".
        title = (over_under.get("title") or "").strip()
        return title or None

    @staticmethod
    def _team_abbr(team: dict | None) -> str | None:
        if not team:
            return None
        return team.get("abbr") or team.get("abbreviation") or team.get("name")

    @staticmethod
    def _options(raw: dict) -> list[LineOption]:
        options: list[LineOption] = []
        for opt in raw.get("options") or []:
            choice = str(opt.get("choice") or "").lower()
            if choice not in ("higher", "lower"):
                continue
            options.append(
                LineOption(
                    option_id=str(opt.get("id") or f"{raw.get('id')}-{choice}"),
                    side=Side(choice),
                    payout_multiplier=_to_float(opt.get("payout_multiplier"), 1.0) or 1.0,
                )
            )
        return options

    # ----------------------------------------------------------- diagnostics
    def health_check(self) -> tuple[bool, str, str]:
        try:
            payload, source = self.fetch_lines()
        except ProviderError as exc:
            return False, exc.status, exc.message
        count = len(payload.get("over_under_lines") or []) if isinstance(payload, dict) else 0
        if not count:
            return False, "empty", "endpoint answered but returned no lines"
        return True, "ok", f"{count} lines via {source}"


# ------------------------------------------------------------------ manual import
_CSV_ALIASES = {
    "player": "player", "name": "player", "player_name": "player",
    "market": "market", "stat": "market", "bet": "market", "stat_type": "market",
    "line": "line", "stat_value": "line", "value": "line", "total": "line",
    "team": "team", "opponent": "opponent", "opp": "opponent",
    "position": "position", "pos": "position",
    "multiplier": "multiplier", "payout": "multiplier", "payout_multiplier": "multiplier",
    "game": "game", "matchup": "game", "start": "starts_at", "starts_at": "starts_at",
}

_MARKET_ALIASES = {
    "strikeouts": Market.STRIKEOUTS, "ks": Market.STRIKEOUTS, "k": Market.STRIKEOUTS,
    "so": Market.STRIKEOUTS, "pitcher strikeouts": Market.STRIKEOUTS,
    "1+ hit": Market.HITS_1_PLUS, "hits": Market.HITS_1_PLUS, "hit": Market.HITS_1_PLUS,
    "receiving yards": Market.RECEIVING_YARDS, "rec yards": Market.RECEIVING_YARDS,
    "rushing yards": Market.RUSHING_YARDS, "rush yards": Market.RUSHING_YARDS,
    "passing yards": Market.PASSING_YARDS, "pass yards": Market.PASSING_YARDS,
    "receptions": Market.RECEPTIONS, "rec": Market.RECEPTIONS,
    "anytime td": Market.ANYTIME_TD, "anytime touchdown": Market.ANYTIME_TD,
    "td": Market.ANYTIME_TD, "touchdown": Market.ANYTIME_TD,
}


def parse_manual_import(text: str, league: League) -> list[NormalizedLine]:
    """Parse a pasted slate: either the raw API JSON, or a simple CSV.

    Accepted CSV header (order-insensitive, extra columns ignored)::

        player,market,line,team,opponent,position,multiplier

    This is the fallback that keeps the platform usable on a day Underdog's endpoint
    is unreachable.
    """
    text = text.strip()
    if not text:
        return []

    if text.startswith("{") or text.startswith("["):
        payload = json.loads(text)
        if isinstance(payload, list):
            payload = {"over_under_lines": payload}
        return UnderdogProvider().normalize(payload, league)

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ProviderError("import", "CSV has no header row")

    field_map = {
        name: _CSV_ALIASES[(name or "").strip().lower()]
        for name in reader.fieldnames
        if (name or "").strip().lower() in _CSV_ALIASES
    }
    if "player" not in field_map.values() or "line" not in field_map.values():
        raise ProviderError("import", "CSV needs at least 'player' and 'line' columns")

    lines: list[NormalizedLine] = []
    for index, row in enumerate(reader):
        record = {field_map[k]: (v or "").strip() for k, v in row.items() if k in field_map}
        player_name = record.get("player")
        stat_line = _to_float(record.get("line"))
        if not player_name or stat_line is None:
            continue

        market = _MARKET_ALIASES.get((record.get("market") or "").strip().lower())
        if market is None:
            continue
        # Keep the same threshold discipline as the API path.
        required = _THRESHOLD_ONLY.get(market)
        if required is not None:
            stat_line = required

        multiplier = _to_float(record.get("multiplier"), 1.0) or 1.0
        line_id = f"import-{index}-{player_name.replace(' ', '_')}-{market.value}"
        lines.append(
            NormalizedLine(
                line_id=line_id,
                league=league,
                market=market,
                player_name=player_name,
                player_id=None,
                position=record.get("position") or None,
                team=record.get("team") or None,
                opponent=record.get("opponent") or None,
                game_id=None,
                game_label=record.get("game") or None,
                starts_at=_parse_dt(record.get("starts_at")),
                stat_line=stat_line,
                options=[
                    LineOption(f"{line_id}-higher", Side.HIGHER, multiplier),
                    LineOption(f"{line_id}-lower", Side.LOWER, multiplier),
                ],
                raw_stat_key=record.get("market"),
            )
        )
    return lines
