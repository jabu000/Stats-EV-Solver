"""The orchestrator: Underdog lines in, a ranked, priced board out.

Flow, identical for every league:

    lines -> resolve players -> attach game context -> project -> price -> rank

The design rule throughout is **degrade, never blank**. A slate where the lineups are
not posted, the weather API is down and half the names do not resolve should still
produce a board -- with lower confidence, visible warnings, and the unresolved names
surfaced in Settings. A tool that shows nothing when one upstream hiccups is a tool
nobody can rely on at 6pm on a Sunday.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.domain import MARKETS_BY_LEAGUE, League, Market, Side
from app.features.context import (
    FootballGameContext,
    FootballPlayerProfile,
    FootballTeamContext,
    MlbGameContext,
    ParkContext,
    WeatherContext,
)
from app.ingest.mapping import Candidate, PlayerResolver
from app.models.base import ModelOutput
from app.models.calibration import Calibrator
from app.models.football import project_football
from app.models.mlb_hits import project_hits
from app.models.mlb_strikeouts import project_strikeouts
from app.pricing.edge import ReferenceEntry, best_side, price_leg, probability_for_side
from app.providers.base import ProviderError
from app.providers.cfbd import CfbdProvider
from app.providers.market import MarketProvider
from app.providers.mlb_statsapi import MlbStatsProvider
from app.providers.nflverse import NflverseProvider
from app.providers.underdog import NormalizedLine, UnderdogProvider
from app.providers.weather import WeatherProvider
from app.schemas import (
    BoardFilters,
    BoardResponse,
    Distribution,
    PricedBet,
)
from app.services.settings_store import UserSettings
from app.static_data.loader import mlb_park, nfl_stadium
from app.domain import RoofState


class BoardBuilder:
    """Builds one league's board for one day."""

    def __init__(
        self,
        session: Session | None,
        settings: UserSettings,
        calibrator: Calibrator | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.calibrator = calibrator or Calibrator()
        self.notes: list[str] = []

    # ------------------------------------------------------------------ public
    def build(
        self,
        league: League,
        mode: str = "value",
        on: date | None = None,
        imported_lines: list[NormalizedLine] | None = None,
    ) -> BoardResponse:
        on = on or date.today()
        self.notes = []

        if imported_lines is not None:
            lines, source = imported_lines, "import"
        else:
            lines, source = self._fetch_lines(league)

        if not lines:
            return self._empty_board(league, mode, source)

        if league is League.MLB:
            bets = self._build_mlb(lines, on, mode)
        else:
            bets = self._build_football(league, lines, on, mode)

        bets.sort(key=lambda b: b.score, reverse=True)
        bets = self._apply_settings_filters(bets)

        return BoardResponse(
            league=league,
            mode=mode,
            generated_at=datetime.now(timezone.utc),
            source=source,
            bets=bets,
            filters=_derive_filters(league, bets),
            unmapped_count=self._unmapped_count(league),
            notes=self.notes,
        )

    # ------------------------------------------------------------------ lines
    def _fetch_lines(self, league: League) -> tuple[list[NormalizedLine], str]:
        provider = UnderdogProvider(token=self.settings.effective_underdog_token())
        try:
            payload, source = provider.fetch_lines()
        except ProviderError as exc:
            self.notes.append(f"Underdog lines unavailable: {exc.message}")
            return [], "unavailable"

        lines = provider.normalize(payload, league)
        wanted = set(MARKETS_BY_LEAGUE[league])
        lines = [line for line in lines if line.market in wanted]
        if not lines:
            self.notes.append(
                f"Underdog returned lines, but none for {league.value} in the tracked markets."
            )
        return lines, source

    # -------------------------------------------------------------------- MLB
    def _build_mlb(
        self, lines: list[NormalizedLine], on: date, mode: str
    ) -> list[PricedBet]:
        games = self._mlb_contexts(on)
        pitchers: dict[str, tuple] = {}
        batters: dict[str, tuple] = {}
        for game in games:
            for pitcher, lineup in (
                (game.home_pitcher, game.home_lineup),
                (game.away_pitcher, game.away_lineup),
            ):
                if pitcher is not None:
                    pitchers[pitcher.player_key] = (pitcher, game)
                for batter in lineup:
                    batters[batter.player_key] = (batter, game)

        pitcher_resolver = self._resolver(
            League.MLB,
            [
                Candidate(p.player_key, p.name, p.team, "P")
                for p, _ in pitchers.values()
            ],
        )
        batter_resolver = self._resolver(
            League.MLB,
            [Candidate(b.player_key, b.name, b.team) for b, _ in batters.values()],
        )

        bets: list[PricedBet] = []
        for line in lines:
            if line.market is Market.STRIKEOUTS:
                match = pitcher_resolver.resolve(line.player_name, line.team)
                entry = pitchers.get(match.canonical_id or "")
                if entry is None:
                    continue
                pitcher, game = entry
                output = project_strikeouts(line.stat_line, pitcher, game)
            else:
                match = batter_resolver.resolve(line.player_name, line.team)
                entry = batters.get(match.canonical_id or "")
                if entry is None:
                    continue
                batter, game = entry
                output = project_hits(line.stat_line, batter, game)

            bets.extend(
                self._price(line, output, mode, match.score, match.canonical_id or "")
            )
        return bets

    def _mlb_contexts(self, on: date) -> list[MlbGameContext]:
        try:
            games = MlbStatsProvider().game_contexts(on)
        except ProviderError as exc:
            self.notes.append(f"MLB stats unavailable: {exc.message}")
            return []

        weather_provider = WeatherProvider()
        market = MarketProvider().odds(League.MLB, on)

        for game in games:
            park_row = mlb_park(game.home_team) or {}
            if park_row:
                game.weather = weather_provider.forecast(
                    park_row.get("lat", 0.0),
                    park_row.get("lon", 0.0),
                    game.starts_at,
                    RoofState(park_row.get("roof", "open")),
                    fixture_key=game.home_team,
                )
            odds = market.get(game.game_id) or market.get(game.home_team)
            if odds is not None and odds.total is not None:
                spread = odds.spread or 0.0
                game.home_implied_runs = max(2.0, odds.total / 2 - spread / 2)
                game.away_implied_runs = max(2.0, odds.total / 2 + spread / 2)
            else:
                self.notes.append(
                    f"No market total for {game.away_team} @ {game.home_team}; "
                    "using league-average run expectations."
                ) if len(self.notes) < 4 else None
        return games

    # --------------------------------------------------------------- football
    def _build_football(
        self, league: League, lines: list[NormalizedLine], on: date, mode: str
    ) -> list[PricedBet]:
        profiles, teams = self._football_profiles(league, on)
        odds = MarketProvider().odds(league, on)
        weather_provider = WeatherProvider()

        resolver = self._resolver(
            league,
            [
                Candidate(p.player_key, p.name, p.team, p.position)
                for p in profiles.values()
            ],
        )

        game_cache: dict[str, FootballGameContext] = {}
        bets: list[PricedBet] = []

        for line in lines:
            match = resolver.resolve(line.player_name, line.team)
            player = profiles.get(match.canonical_id or "")
            if player is None:
                # No stats for this player: skip rather than invent a projection.
                continue

            game = self._football_game(
                league, line, player, teams, odds, weather_provider, game_cache
            )
            output = project_football(line.market, line.stat_line, player, game)
            bets.extend(
                self._price(
                    line, output, mode, match.score, match.canonical_id or "",
                    position=player.position,
                )
            )
        return bets

    def _football_profiles(
        self, league: League, on: date
    ) -> tuple[dict[str, FootballPlayerProfile], dict[str, FootballTeamContext]]:
        season = on.year if on.month >= 8 else on.year - 1
        try:
            if league is League.NFL:
                return NflverseProvider().build_profiles(season)
            return CfbdProvider(
                api_key=self.settings.effective_cfbd_key()
            ).build_profiles(season)
        except ProviderError as exc:
            self.notes.append(f"{league.value} stats unavailable: {exc.message}")
            return {}, {}

    def _football_game(
        self,
        league: League,
        line: NormalizedLine,
        player: FootballPlayerProfile,
        teams: dict[str, FootballTeamContext],
        odds: dict,
        weather_provider: WeatherProvider,
        cache: dict[str, FootballGameContext],
    ) -> FootballGameContext:
        team = (player.team or line.team or "UNK").upper()
        opponent = (line.opponent or "UNK").upper()
        key = line.game_id or f"{team}-{opponent}"
        if key in cache:
            return cache[key]

        game_odds = odds.get(line.game_id or "") or odds.get(team)
        if game_odds is not None:
            home, away = game_odds.home_team, game_odds.away_team
            spread = game_odds.spread or 0.0
            total = game_odds.total or (44.5 if league is League.NFL else 55.5)
        else:
            # Underdog tells us the two teams even when the market feed does not; assume
            # the line's opponent is at home only if we have nothing better.
            home, away = opponent, team
            spread, total = 0.0, 44.5 if league is League.NFL else 55.5
            if len(self.notes) < 6:
                self.notes.append(
                    f"No market spread/total for {team} vs {opponent}; "
                    "game script uses league averages."
                )

        stadium = nfl_stadium(home) if league is League.NFL else None
        if stadium:
            weather = weather_provider.forecast(
                stadium["lat"], stadium["lon"], line.starts_at,
                RoofState(stadium.get("roof", "open")), fixture_key=home,
            )
        else:
            weather = WeatherContext(source="default")

        game = FootballGameContext(
            game_id=key,
            league=league,
            home_team=home,
            away_team=away,
            starts_at=line.starts_at,
            spread=spread,
            total=total,
            weather=weather,
            home=teams.get(home) or FootballTeamContext(team=home),
            away=teams.get(away) or FootballTeamContext(team=away),
        )
        cache[key] = game
        return game

    # ------------------------------------------------------------------ pricing
    def _price(
        self,
        line: NormalizedLine,
        output: ModelOutput,
        mode: str,
        match_score: float,
        canonical_id: str,
        position: str | None = None,
    ) -> list[PricedBet]:
        """Price the side the model actually prefers.

        Only one side of a line is shown. Both sides are never simultaneously
        attractive -- their probabilities sum to one, so listing both would just be
        listing every line twice with the loser attached.
        """
        reference = ReferenceEntry(
            entry_type=self.settings.reference_entry_type,
            legs=self.settings.reference_entry_legs,
            structure=self.settings.payout_structure(),
        )

        # Choose the side from the *raw* model output, then calibrate the probability of
        # that side. Calibrating P(higher) instead would be a domain mismatch: the
        # graded history records the probability of the side we actually picked, which
        # is always at least 0.5, so a fit trained on it says nothing meaningful about a
        # 36% "higher" and would happily invert the pick.
        side = best_side(output.prob_higher)
        option = next((o for o in line.options if o.side is side), None)
        if option is None:
            return []

        raw_probability = probability_for_side(output.prob_higher, side)
        probability, was_calibrated = self.calibrator.apply(
            line.league.value, line.market.value, raw_probability
        )

        # A shaky name match should not read as a confident projection.
        confidence = output.confidence * (0.75 + 0.25 * min(match_score, 1.0))

        priced = price_leg(
            probability, option.payout_multiplier, confidence, reference, mode
        )

        warnings = list(output.warnings)
        if match_score < 0.95:
            warnings.append(
                f"Player name matched at {match_score:.0%} confidence -- verify identity"
            )

        return [
            PricedBet(
                id=f"{line.line_id}:{side.value}",
                league=line.league,
                market=line.market,
                underdog_line_id=line.line_id,
                # The canonical *stats-provider* id, not Underdog's. Results feeds are
                # keyed by MLB StatsAPI / nflverse / CFBD ids, so storing Underdog's id
                # here would leave graded picks impossible to join to actual outcomes.
                player_key=canonical_id,
                player_name=line.player_name,
                position=position or line.position,
                team=line.team,
                opponent=line.opponent,
                game_label=line.game_label,
                game_id=line.game_id,
                starts_at=line.starts_at,
                stat_line=line.stat_line,
                side=side,
                payout_multiplier=option.payout_multiplier,
                projected_mean=round(output.projected_mean, 3),
                distribution=Distribution(
                    mean=round(output.summary.mean, 2),
                    p10=round(output.summary.p10, 2),
                    p25=round(output.summary.p25, 2),
                    p50=round(output.summary.p50, 2),
                    p75=round(output.summary.p75, 2),
                    p90=round(output.summary.p90, 2),
                    std=round(output.summary.std, 2),
                ),
                model_probability=round(raw_probability, 5),
                calibrated_probability=round(probability, 5),
                break_even_probability=priced.break_even,
                edge=priced.edge,
                ev_per_dollar=priced.ev_per_dollar,
                confidence=round(confidence, 3),
                score=priced.score,
                is_calibrated=was_calibrated,
                factors=output.factors,
                warnings=warnings,
            )
        ]

    # ------------------------------------------------------------------ helpers
    def _resolver(self, league: League, candidates: list[Candidate]) -> PlayerResolver:
        return PlayerResolver(self.session, league, candidates)

    def _apply_settings_filters(self, bets: list[PricedBet]) -> list[PricedBet]:
        result = bets
        if self.settings.min_edge > 0:
            result = [b for b in result if b.edge >= self.settings.min_edge]
        if self.settings.min_confidence > 0:
            result = [b for b in result if b.confidence >= self.settings.min_confidence]
        if self.settings.hide_negative_ev:
            result = [b for b in result if b.ev_per_dollar > 0]
        return result

    def _unmapped_count(self, league: League) -> int:
        if self.session is None:
            return 0
        from app.tables import UnmappedPlayer

        return (
            self.session.query(UnmappedPlayer)
            .filter_by(league=league.value)
            .count()
        )

    def _empty_board(self, league: League, mode: str, source: str) -> BoardResponse:
        return BoardResponse(
            league=league,
            mode=mode,
            generated_at=datetime.now(timezone.utc),
            source=source,
            bets=[],
            filters=BoardFilters(markets=list(MARKETS_BY_LEAGUE[league])),
            unmapped_count=self._unmapped_count(league),
            notes=self.notes
            or [f"No {league.value} lines available for the tracked markets right now."],
        )


def _derive_filters(league: League, bets: list[PricedBet]) -> BoardFilters:
    """Filter options are derived from the board, so they never offer an empty result."""
    return BoardFilters(
        teams=sorted({b.team for b in bets if b.team}),
        games=sorted({b.game_label for b in bets if b.game_label}),
        positions=sorted({b.position for b in bets if b.position}),
        markets=[m for m in MARKETS_BY_LEAGUE[league] if any(b.market is m for b in bets)],
    )
