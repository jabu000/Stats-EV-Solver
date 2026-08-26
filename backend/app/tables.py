"""Persisted tables.

The store has three jobs:

1. Remember user configuration (tokens, keys, entry structure) across restarts.
2. Remember name-resolution decisions so a fuzzy match only has to be made once.
3. Keep an immutable audit trail of every projection we published, so the Track
   Record tab can grade us honestly later. Nothing in the pricing path mutates a
   published snapshot -- a refresh writes a new one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SettingRow(Base):
    """Single-row-per-key user settings, stored as JSON text."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class PlayerAlias(Base):
    """Resolved mapping from a source's player name to our canonical player id.

    Underdog spells names differently from MLB StatsAPI, nflverse and CFBD
    ("Michael Harris II" vs "Michael Harris", "Jr."/"Sr." handling, accents). Once a
    match is confirmed we store it so the fuzzy matcher never runs for that name again.
    """

    __tablename__ = "player_aliases"
    __table_args__ = (
        UniqueConstraint("league", "source", "source_name", name="uq_alias_lookup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_id: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    # "exact" | "alias" | "fuzzy" | "manual"
    resolved_by: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class UnmappedPlayer(Base):
    """A source name we could not resolve confidently.

    These are surfaced in the Settings tab rather than silently dropped -- an
    unresolved star player is the difference between a good board and a broken one.
    """

    __tablename__ = "unmapped_players"
    __table_args__ = (
        UniqueConstraint("league", "source_name", name="uq_unmapped_lookup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league: Mapped[str] = mapped_column(String(8), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    team: Mapped[str | None] = mapped_column(String(64))
    best_guess: Mapped[str | None] = mapped_column(String(128))
    best_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    times_seen: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Snapshot(Base):
    """One refresh run for one league."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )
    # "live" | "fixture" | "import"
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    projections: Mapped[list["ProjectionRow"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class ProjectionRow(Base):
    """A single priced pick as it was published at snapshot time.

    `graded_*` columns are filled in later by the grader; everything else is written
    once and never updated, so the track record reflects what we actually said.
    """

    __tablename__ = "projections"
    __table_args__ = (
        Index("ix_projection_grading", "league", "market", "graded_at"),
        Index("ix_projection_event", "event_date", "player_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot: Mapped[Snapshot] = relationship(back_populates="projections")

    # --- identity of the pick -------------------------------------------------
    league: Mapped[str] = mapped_column(String(8), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    underdog_line_id: Mapped[str | None] = mapped_column(String(64), index=True)
    player_key: Mapped[str] = mapped_column(String(64), nullable=False)
    player_name: Mapped[str] = mapped_column(String(128), nullable=False)
    team: Mapped[str | None] = mapped_column(String(32))
    opponent: Mapped[str | None] = mapped_column(String(32))
    game_id: Mapped[str | None] = mapped_column(String(64))
    event_date: Mapped[str | None] = mapped_column(String(10), index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)

    # --- the offer ------------------------------------------------------------
    stat_line: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    payout_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # --- what we said ---------------------------------------------------------
    projected_mean: Mapped[float] = mapped_column(Float, nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated_probability: Mapped[float] = mapped_column(Float, nullable=False)
    break_even_probability: Mapped[float] = mapped_column(Float, nullable=False)
    edge: Mapped[float] = mapped_column(Float, nullable=False)
    ev_per_dollar: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    factors_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    # --- how it went ----------------------------------------------------------
    actual_value: Mapped[float | None] = mapped_column(Float)
    won: Mapped[bool | None] = mapped_column(Boolean)
    push: Mapped[bool | None] = mapped_column(Boolean)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Underdog's line at lock time, for closing-line value.
    closing_line: Mapped[float | None] = mapped_column(Float)


class ProviderCall(Base):
    """Log of provider fetches, used by the Settings "Test connections" panel."""

    __tablename__ = "provider_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    called_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )
