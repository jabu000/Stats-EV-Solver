"""The contract between data providers and models.

Providers are responsible for producing these structures; models are responsible for
turning them into probabilities. Nothing in `app/models/` imports a provider, and
nothing in `app/providers/` imports a model, so a data source can be swapped or a
model retuned without touching the other side.

Every field that a model reads has a defensible default here, because real slates are
full of holes: a lineup is not posted yet, a rookie has no splits, a college backup has
four carries all season. A missing value must degrade the projection's *confidence*,
never crash the board.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain import Handedness, League, RoofState


@dataclass
class WeatherContext:
    """Conditions at first pitch / kickoff.

    Indoors, all of this is inert -- `applies` is False and the models skip the
    adjustment rather than applying a neutral factor and pretending they modelled it.
    """

    temperature_f: float = 70.0
    wind_mph: float = 5.0
    #: Degrees the wind blows *toward*, meteorological convention, 0 = north.
    wind_direction_deg: float = 0.0
    humidity_pct: float = 50.0
    precipitation_chance: float = 0.0
    roof: RoofState = RoofState.OPEN
    applies: bool = True
    source: str = "default"

    @property
    def is_indoors(self) -> bool:
        return self.roof.is_indoors


@dataclass
class ParkContext:
    name: str = "Unknown Park"
    #: 100 = neutral.
    k_factor: float = 100.0
    hit_factor: float = 100.0
    hr_factor: float = 100.0
    altitude_ft: float = 0.0


# --------------------------------------------------------------------- baseball
@dataclass
class BatterProfile:
    """A hitter, as the models need to see one."""

    player_key: str
    name: str
    bats: Handedness = Handedness.RIGHT
    plate_appearances: int = 0
    #: Overall and platoon-split hit rate per plate appearance.
    hit_per_pa: float = 0.232
    hit_per_pa_vs_lhp: float | None = None
    hit_per_pa_vs_rhp: float | None = None
    #: Strikeout rate per plate appearance -- drives the opposing pitcher's K model.
    k_per_pa: float = 0.222
    k_per_pa_vs_lhp: float | None = None
    k_per_pa_vs_rhp: float | None = None
    on_base_pct: float = 0.315
    #: 1-9; 0 means "not in the posted lineup".
    lineup_slot: int = 0
    team: str | None = None

    def hit_rate_vs(self, pitcher_throws: Handedness) -> float:
        split = (
            self.hit_per_pa_vs_lhp
            if pitcher_throws is Handedness.LEFT
            else self.hit_per_pa_vs_rhp
        )
        return split if split is not None else self.hit_per_pa

    def k_rate_vs(self, pitcher_throws: Handedness) -> float:
        split = (
            self.k_per_pa_vs_lhp
            if pitcher_throws is Handedness.LEFT
            else self.k_per_pa_vs_rhp
        )
        return split if split is not None else self.k_per_pa

    def is_platoon_advantaged(self, pitcher_throws: Handedness) -> bool:
        """Switch hitters always bat with the platoon advantage."""
        if self.bats is Handedness.SWITCH:
            return True
        return self.bats is not pitcher_throws


@dataclass
class PitcherProfile:
    player_key: str
    name: str
    throws: Handedness = Handedness.RIGHT
    batters_faced: int = 0
    k_per_bf: float = 0.222
    k_per_bf_recent: float | None = None
    k_per_bf_vs_lhb: float | None = None
    k_per_bf_vs_rhb: float | None = None
    hit_per_bf: float = 0.222
    #: Innings and pitches the manager typically allows -- sets the batters-faced cap.
    innings_per_start: float = 5.3
    pitches_per_start: float = 88.0
    starts: int = 0
    team: str | None = None

    def k_rate_vs(self, batter_bats: Handedness) -> float:
        # A switch hitter takes the platoon advantage, i.e. bats opposite the pitcher.
        effective = batter_bats
        if batter_bats is Handedness.SWITCH:
            effective = (
                Handedness.LEFT if self.throws is Handedness.RIGHT else Handedness.RIGHT
            )
        split = (
            self.k_per_bf_vs_lhb
            if effective is Handedness.LEFT
            else self.k_per_bf_vs_rhb
        )
        return split if split is not None else self.k_per_bf


@dataclass
class UmpireProfile:
    """Plate umpire strike-zone tendency, as a multiplier on strikeout rate."""

    name: str = "Unknown"
    k_factor: float = 1.0
    known: bool = False


@dataclass
class MlbGameContext:
    game_id: str
    home_team: str
    away_team: str
    starts_at: datetime | None = None
    park: ParkContext = field(default_factory=ParkContext)
    weather: WeatherContext = field(default_factory=WeatherContext)
    umpire: UmpireProfile = field(default_factory=UmpireProfile)
    home_pitcher: PitcherProfile | None = None
    away_pitcher: PitcherProfile | None = None
    home_lineup: list[BatterProfile] = field(default_factory=list)
    away_lineup: list[BatterProfile] = field(default_factory=list)
    lineups_confirmed: bool = False
    #: Vegas-implied runs, used to scale expected plate appearances.
    home_implied_runs: float = 4.35
    away_implied_runs: float = 4.35
    #: Opposing bullpen hit rate allowed per batter faced.
    home_bullpen_hit_per_bf: float = 0.232
    away_bullpen_hit_per_bf: float = 0.232
    #: Catcher framing runs per 150 games; positive suppresses balls, lifting Ks.
    home_catcher_framing: float = 0.0
    away_catcher_framing: float = 0.0
    #: Team defensive outs-above-average, which suppresses opponent hits.
    home_defense_oaa: float = 0.0
    away_defense_oaa: float = 0.0

    def opponent_of(self, team: str) -> str:
        return self.away_team if team == self.home_team else self.home_team

    def lineup_against(self, pitcher_team: str) -> list[BatterProfile]:
        return self.away_lineup if pitcher_team == self.home_team else self.home_lineup


# --------------------------------------------------------------------- football
@dataclass
class FootballPlayerProfile:
    player_key: str
    name: str
    position: str = "WR"
    team: str | None = None
    games: int = 0
    snap_share: float = 0.7
    #: Usage shares, all relative to the player's own team.
    target_share: float = 0.15
    air_yards_share: float = 0.15
    rush_share: float = 0.10
    redzone_target_share: float = 0.12
    redzone_rush_share: float = 0.10
    #: Efficiency.
    yards_per_target: float = 7.9
    catch_rate: float = 0.645
    yards_per_carry: float = 4.35
    yards_per_attempt: float = 7.05
    #: QB-only: share of the team's dropbacks this player takes.
    dropback_share: float = 0.0
    #: Standard deviation of the player's per-game yardage, when we have enough games.
    yards_std: float | None = None
    injury_status: str | None = None

    @property
    def is_quarterback(self) -> bool:
        return self.position.upper() in ("QB",)


@dataclass
class FootballTeamContext:
    team: str
    plays_per_game: float = 63.0
    pass_rate: float = 0.575
    #: Pass rate over expectation: positive means pass-happier than game state implies.
    proe: float = 0.0
    seconds_per_play: float = 27.0
    #: Opponent-adjustment multipliers, 1.0 = average defence faced.
    opp_pass_defense_factor: float = 1.0
    opp_rush_defense_factor: float = 1.0
    opp_pace_factor: float = 1.0
    #: Program strength, used to shrink college numbers toward reality.
    rating: float = 0.0


@dataclass
class FootballGameContext:
    game_id: str
    league: League
    home_team: str
    away_team: str
    starts_at: datetime | None = None
    #: Market inputs. The spread is signed from the home team's perspective.
    spread: float = 0.0
    total: float = 44.5
    weather: WeatherContext = field(default_factory=WeatherContext)
    home: FootballTeamContext | None = None
    away: FootballTeamContext | None = None
    neutral_site: bool = False

    def implied_points(self, team: str) -> float:
        """Team total from the market: half the total, shifted by half the spread."""
        half = self.total / 2.0
        if team == self.home_team:
            return half - self.spread / 2.0
        return half + self.spread / 2.0

    def team_context(self, team: str) -> FootballTeamContext | None:
        if team == self.home_team:
            return self.home
        if team == self.away_team:
            return self.away
        return None

    def opponent_of(self, team: str) -> str:
        return self.away_team if team == self.home_team else self.home_team
