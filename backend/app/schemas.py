"""Pydantic types crossing the API boundary and flowing through the pricing pipeline."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain import League, Market, Side


class Factor(BaseModel):
    """One line of the "Why" drawer.

    `impact` is expressed in the market's own units (strikeouts, yards, receptions)
    so the breakdown adds up to the projection instead of being a vague score.
    """

    name: str
    detail: str
    impact: float = 0.0
    # "positive" | "negative" | "neutral"
    direction: str = "neutral"


class Distribution(BaseModel):
    """Summary of the projected outcome distribution."""

    mean: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    std: float


class PricedBet(BaseModel):
    """A single side of a single Underdog line, priced by the model."""

    id: str
    league: League
    market: Market
    underdog_line_id: str | None = None

    player_key: str
    player_name: str
    position: str | None = None
    team: str | None = None
    opponent: str | None = None
    game_label: str | None = None
    game_id: str | None = None
    starts_at: datetime | None = None

    stat_line: float
    side: Side
    payout_multiplier: float = 1.0

    projected_mean: float
    distribution: Distribution
    model_probability: float
    calibrated_probability: float
    break_even_probability: float
    edge: float
    ev_per_dollar: float
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    # Rank score actually used for ordering; depends on the Best Value / Most Likely mode.
    score: float = 0.0
    is_calibrated: bool = False

    factors: list[Factor] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BoardFilters(BaseModel):
    """Filter options the UI renders, derived from the board actually returned."""

    teams: list[str] = Field(default_factory=list)
    games: list[str] = Field(default_factory=list)
    positions: list[str] = Field(default_factory=list)
    markets: list[Market] = Field(default_factory=list)


class BoardResponse(BaseModel):
    league: League
    mode: str
    generated_at: datetime
    source: str
    bets: list[PricedBet]
    filters: BoardFilters
    unmapped_count: int = 0
    notes: list[str] = Field(default_factory=list)


class EntryLeg(BaseModel):
    bet_id: str
    player_name: str
    market: Market
    side: Side
    stat_line: float
    probability: float
    payout_multiplier: float = 1.0
    game_id: str | None = None
    team: str | None = None


class EntryRequest(BaseModel):
    legs: list[EntryLeg]
    entry_type: str = "standard"  # "standard" | "insured"
    stake: float = 10.0
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25


class EntryOutcome(BaseModel):
    """One payout branch of an entry (e.g. "4 of 5 correct")."""

    correct: int
    probability: float
    multiplier: float
    contribution: float


class CorrelationWarning(BaseModel):
    leg_ids: list[str]
    kind: str
    detail: str
    severity: str = "info"  # "info" | "warn" | "block"


class EntryResponse(BaseModel):
    legs: int
    entry_type: str
    stake: float
    payout_table: list[EntryOutcome]
    expected_return: float
    expected_profit: float
    ev_percent: float
    win_probability: float
    kelly_stake: float
    kelly_full: float
    correlation_warnings: list[CorrelationWarning] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    provider: str
    label: str
    ok: bool
    mode: str
    status: str
    detail: str = ""
    duration_ms: int = 0
    requires_key: bool = False
    key_present: bool = True


class UnmappedEntry(BaseModel):
    league: League
    source_name: str
    team: str | None = None
    best_guess: str | None = None
    best_score: float = 0.0
    times_seen: int = 1


class CalibrationBucket(BaseModel):
    lower: float
    upper: float
    predicted: float
    actual: float
    count: int


class MarketRecord(BaseModel):
    league: League
    market: Market
    picks: int
    wins: int
    hit_rate: float
    expected_hit_rate: float
    roi: float
    brier: float


class TrackRecordResponse(BaseModel):
    total_picks: int
    graded_picks: int
    pending_picks: int
    wins: int
    hit_rate: float
    expected_hit_rate: float
    roi: float
    brier_score: float
    avg_clv: float | None = None
    calibration: list[CalibrationBucket] = Field(default_factory=list)
    by_market: list[MarketRecord] = Field(default_factory=list)
    roi_series: list[dict] = Field(default_factory=list)
    recent: list[dict] = Field(default_factory=list)
