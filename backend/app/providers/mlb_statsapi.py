"""MLB data from the public StatsAPI (statsapi.mlb.com).

Design note: naive implementations of this hit one endpoint per player and make ~400
requests for a single slate. Everything here uses the *bulk* stats endpoints instead,
so a full day of baseball -- schedule, lineups, probables, season rates and platoon
splits for every hitter and pitcher -- costs about seven requests.

The platoon splits are the reason this provider exists at all. Pitcher-vs-LHB/RHB and
batter-vs-LHP/RHP are the single largest non-market signal in these props, and they are
only available from the `statSplits` stat type with the `vl`/`vr` situation codes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from dateutil import parser as date_parser

from app.domain import Handedness, RoofState
from app.features.context import (
    BatterProfile,
    MlbGameContext,
    ParkContext,
    PitcherProfile,
    UmpireProfile,
    WeatherContext,
)
from app.providers.base import Provider, ProviderError
from app.static_data.loader import mlb_park, priors

_HAND = {"L": Handedness.LEFT, "R": Handedness.RIGHT, "S": Handedness.SWITCH}


def _hand(code: Any) -> Handedness:
    if isinstance(code, dict):
        code = code.get("code")
    return _HAND.get(str(code or "R").upper()[:1], Handedness.RIGHT)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # filter NaN


def _rate(numerator: Any, denominator: Any, default: float) -> float:
    den = _num(denominator, 0.0)
    if den <= 0:
        return default
    return _num(numerator, 0.0) / den


class MlbStatsProvider(Provider):
    name = "mlb_statsapi"
    label = "MLB StatsAPI"
    base_url = "https://statsapi.mlb.com/api/v1"

    #: Hydrations we need on the schedule call: who's pitching, who's batting, where.
    SCHEDULE_HYDRATE = (
        "probablePitcher(note),lineups,team,venue(location),weather,officials,decisions"
    )

    def __init__(self) -> None:
        super().__init__()
        self._priors = priors("MLB")

    # ------------------------------------------------------------------ fetches
    def schedule(self, on: date) -> dict:
        result = self.fetch(
            "/schedule",
            fixture=f"schedule_{on.isoformat()}",
            params={
                "sportId": 1,
                "date": on.isoformat(),
                "hydrate": self.SCHEDULE_HYDRATE,
            },
        )
        return result.payload

    def _bulk_stats(
        self, group: str, season: int, *, splits: str | None = None
    ) -> list[dict]:
        """One call for every qualified player's season line, or platoon split."""
        params: dict[str, Any] = {
            "stats": "statSplits" if splits else "season",
            "group": group,
            "season": season,
            "sportId": 1,
            "limit": 2000,
            "playerPool": "All",
        }
        if splits:
            params["sitCodes"] = splits
        suffix = f"_{splits}" if splits else ""
        result = self.fetch(
            "/stats", fixture=f"{group}_{season}{suffix}", params=params
        )
        payload = result.payload
        rows: list[dict] = []
        for block in payload.get("stats") or []:
            rows.extend(block.get("splits") or [])
        return rows

    # ------------------------------------------------------------- assembly
    def game_contexts(self, on: date, season: int | None = None) -> list[MlbGameContext]:
        """Build a full context per scheduled game."""
        season = season or on.year
        schedule = self.schedule(on)

        hitting = self._index_players(self._bulk_stats("hitting", season))
        pitching = self._index_players(self._bulk_stats("pitching", season))
        hit_vs_l = self._index_players(self._bulk_stats("hitting", season, splits="vl"))
        hit_vs_r = self._index_players(self._bulk_stats("hitting", season, splits="vr"))
        pit_vs_l = self._index_players(self._bulk_stats("pitching", season, splits="vl"))
        pit_vs_r = self._index_players(self._bulk_stats("pitching", season, splits="vr"))

        contexts: list[MlbGameContext] = []
        for day in schedule.get("dates") or []:
            for game in day.get("games") or []:
                context = self._build_game(
                    game, hitting, pitching, hit_vs_l, hit_vs_r, pit_vs_l, pit_vs_r
                )
                if context is not None:
                    contexts.append(context)
        return contexts

    @staticmethod
    def _index_players(rows: list[dict]) -> dict[str, dict]:
        """Map player id -> the `stat` block of that split."""
        indexed: dict[str, dict] = {}
        for row in rows:
            player = row.get("player") or {}
            pid = str(player.get("id") or "")
            if pid and row.get("stat"):
                indexed[pid] = row["stat"]
        return indexed

    def _build_game(
        self,
        game: dict,
        hitting: dict[str, dict],
        pitching: dict[str, dict],
        hit_vs_l: dict[str, dict],
        hit_vs_r: dict[str, dict],
        pit_vs_l: dict[str, dict],
        pit_vs_r: dict[str, dict],
    ) -> MlbGameContext | None:
        teams = game.get("teams") or {}
        home_team = ((teams.get("home") or {}).get("team") or {})
        away_team = ((teams.get("away") or {}).get("team") or {})
        home_abbr = home_team.get("abbreviation")
        away_abbr = away_team.get("abbreviation")
        if not home_abbr or not away_abbr:
            return None

        park_row = mlb_park(home_abbr) or {}
        park = ParkContext(
            name=park_row.get("name", (game.get("venue") or {}).get("name", "Unknown")),
            k_factor=_num(park_row.get("k"), 100.0),
            hit_factor=_num(park_row.get("hit"), 100.0),
            hr_factor=_num(park_row.get("hr"), 100.0),
            altitude_ft=_num(park_row.get("altitude_ft"), 0.0),
        )

        lineups = game.get("lineups") or {}
        home_lineup = self._lineup(
            lineups.get("homePlayers"), home_abbr, hitting, hit_vs_l, hit_vs_r
        )
        away_lineup = self._lineup(
            lineups.get("awayPlayers"), away_abbr, hitting, hit_vs_l, hit_vs_r
        )

        return MlbGameContext(
            game_id=str(game.get("gamePk") or ""),
            home_team=home_abbr,
            away_team=away_abbr,
            starts_at=self._parse_dt(game.get("gameDate")),
            park=park,
            weather=self._weather_from_schedule(game, park_row),
            umpire=self._umpire(game),
            home_pitcher=self._pitcher(
                (teams.get("home") or {}).get("probablePitcher"),
                home_abbr, pitching, pit_vs_l, pit_vs_r,
            ),
            away_pitcher=self._pitcher(
                (teams.get("away") or {}).get("probablePitcher"),
                away_abbr, pitching, pit_vs_l, pit_vs_r,
            ),
            home_lineup=home_lineup,
            away_lineup=away_lineup,
            lineups_confirmed=bool(home_lineup and away_lineup),
        )

    def _lineup(
        self,
        players: Any,
        team: str,
        hitting: dict[str, dict],
        vs_l: dict[str, dict],
        vs_r: dict[str, dict],
    ) -> list[BatterProfile]:
        profiles: list[BatterProfile] = []
        for slot, player in enumerate(players or [], start=1):
            pid = str(player.get("id") or "")
            if not pid:
                continue
            profiles.append(
                self.batter_profile(
                    pid,
                    player.get("fullName") or "Unknown",
                    _hand(player.get("batSide")),
                    team,
                    slot,
                    hitting.get(pid),
                    vs_l.get(pid),
                    vs_r.get(pid),
                )
            )
        return profiles

    def batter_profile(
        self,
        player_id: str,
        name: str,
        bats: Handedness,
        team: str | None,
        lineup_slot: int,
        season: dict | None,
        vs_l: dict | None,
        vs_r: dict | None,
    ) -> BatterProfile:
        season = season or {}
        pa = _num(season.get("plateAppearances"), 0.0)
        default_hit = self._priors["batter_hit_per_pa"]
        default_k = self._priors["pitcher_k_rate"]

        def split_rate(block: dict | None, key: str, default: float) -> float | None:
            if not block:
                return None
            # Only trust a split with a real sample behind it.
            if _num(block.get("plateAppearances"), 0.0) < 40:
                return None
            return _rate(block.get(key), block.get("plateAppearances"), default)

        return BatterProfile(
            player_key=player_id,
            name=name,
            bats=bats,
            plate_appearances=int(pa),
            hit_per_pa=_rate(season.get("hits"), pa, default_hit),
            hit_per_pa_vs_lhp=split_rate(vs_l, "hits", default_hit),
            hit_per_pa_vs_rhp=split_rate(vs_r, "hits", default_hit),
            k_per_pa=_rate(season.get("strikeOuts"), pa, default_k),
            k_per_pa_vs_lhp=split_rate(vs_l, "strikeOuts", default_k),
            k_per_pa_vs_rhp=split_rate(vs_r, "strikeOuts", default_k),
            on_base_pct=_num(season.get("obp"), 0.315),
            lineup_slot=lineup_slot,
            team=team,
        )

    def _pitcher(
        self,
        probable: dict | None,
        team: str,
        pitching: dict[str, dict],
        vs_l: dict[str, dict],
        vs_r: dict[str, dict],
    ) -> PitcherProfile | None:
        if not probable:
            return None
        pid = str(probable.get("id") or "")
        return self.pitcher_profile(
            pid,
            probable.get("fullName") or "Unknown",
            _hand(probable.get("pitchHand")),
            team,
            pitching.get(pid),
            vs_l.get(pid),
            vs_r.get(pid),
        )

    def pitcher_profile(
        self,
        player_id: str,
        name: str,
        throws: Handedness,
        team: str | None,
        season: dict | None,
        vs_l: dict | None,
        vs_r: dict | None,
    ) -> PitcherProfile:
        season = season or {}
        bf = _num(season.get("battersFaced"), 0.0)
        starts = _num(season.get("gamesStarted"), 0.0)
        innings = _num(str(season.get("inningsPitched", "0")).replace(".1", ".33").replace(".2", ".67"), 0.0)
        default_k = self._priors["pitcher_k_rate"]

        def split_rate(block: dict | None, key: str) -> float | None:
            if not block:
                return None
            if _num(block.get("battersFaced"), 0.0) < 50:
                return None
            return _rate(block.get(key), block.get("battersFaced"), default_k)

        return PitcherProfile(
            player_key=player_id,
            name=name,
            throws=throws,
            batters_faced=int(bf),
            k_per_bf=_rate(season.get("strikeOuts"), bf, default_k),
            k_per_bf_vs_lhb=split_rate(vs_l, "strikeOuts"),
            k_per_bf_vs_rhb=split_rate(vs_r, "strikeOuts"),
            hit_per_bf=_rate(season.get("hits"), bf, 0.222),
            innings_per_start=(innings / starts) if starts > 0 else 5.3,
            pitches_per_start=_num(season.get("numberOfPitches"), 0.0) / starts
            if starts > 0
            else 88.0,
            starts=int(starts),
            team=team,
        )

    # ------------------------------------------------------------------ detail
    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = date_parser.parse(str(value))
        except (ValueError, TypeError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _weather_from_schedule(game: dict, park_row: dict) -> WeatherContext:
        """StatsAPI sometimes carries game-time weather; treat it as a bonus, not a source.

        The dedicated weather provider is authoritative -- this only seeds a sensible
        default and, importantly, the roof state, which decides whether weather applies.
        """
        roof = RoofState(park_row.get("roof", "open"))
        raw = game.get("weather") or {}
        temp = _num(raw.get("temp"), 70.0)
        wind_text = str(raw.get("wind") or "")
        wind_mph = 5.0
        for token in wind_text.split():
            if token.isdigit():
                wind_mph = float(token)
                break
        return WeatherContext(
            temperature_f=temp,
            wind_mph=wind_mph,
            humidity_pct=50.0,
            roof=roof,
            applies=not roof.is_indoors,
            source="statsapi" if raw else "default",
        )

    @staticmethod
    def _umpire(game: dict) -> UmpireProfile:
        for official in game.get("officials") or []:
            if str(official.get("officialType") or "").lower().startswith("home"):
                return UmpireProfile(
                    name=(official.get("official") or {}).get("fullName", "Unknown"),
                    k_factor=1.0,
                    known=True,
                )
        return UmpireProfile()

    # ------------------------------------------------------------ diagnostics
    def health_check(self) -> tuple[bool, str, str]:
        try:
            payload = self.schedule(date.today())
        except ProviderError as exc:
            return False, exc.status, exc.message
        games = sum(len(d.get("games") or []) for d in payload.get("dates") or [])
        return True, "ok", f"{games} games on the schedule"
