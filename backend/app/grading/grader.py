"""Grade published projections against actual results, and score ourselves honestly.

Two metrics carry the weight here, and they answer different questions:

* **ROI** answers "did this make money" -- the only question that ultimately matters,
  but it is extremely noisy over a few hundred picks.
* **Brier score** and the calibration curve answer "are the stated probabilities
  right", which is what actually diagnoses a model. Brier converges far faster than
  ROI, so a bad model is visible in it weeks before the bankroll shows it.

A tool that only reported win rate would be flattering itself. Reporting expected-vs-
actual hit rate side by side makes overconfidence impossible to hide.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.domain import League, Market
from app.schemas import (
    CalibrationBucket,
    MarketRecord,
    TrackRecordResponse,
)
from app.tables import ProjectionRow, Snapshot

#: Probability buckets for the calibration curve.
BUCKET_EDGES = [0.0, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0]


def grade_projection(row: ProjectionRow, actual: float) -> ProjectionRow:
    """Settle one pick against its actual stat value."""
    line = row.stat_line
    if actual == line:
        # Underdog posts half-point lines, so this should not happen; if a whole-number
        # line ever appears, treat it as a push rather than silently calling it a loss.
        row.push = True
        row.won = None
    else:
        row.push = False
        row.won = (actual > line) if row.side == "higher" else (actual < line)
    row.actual_value = actual
    row.graded_at = datetime.now(timezone.utc)
    return row


def auto_grade(
    session: Session,
    league: League,
    on: date,
    season: int | None = None,
    week: int | None = None,
) -> dict:
    """Fetch actual results for one league and settle every pick they cover.

    Reports what it *could not* grade and why, rather than returning a bare count. A
    grading job that quietly settles 3 of 40 picks and calls it success is worse than
    one that fails loudly, because the track record silently becomes a biased sample of
    whichever players happened to be in the feed.
    """
    from app.grading.results import fetch_results

    fetched = fetch_results(league, on, season=season, week=week)

    pending = (
        session.query(ProjectionRow)
        .filter(ProjectionRow.graded_at.is_(None))
        .filter(ProjectionRow.league == league.value)
        .filter(ProjectionRow.event_date == on.isoformat())
        .all()
    )

    graded = 0
    unmatched: list[str] = []
    for row in pending:
        actual = fetched.results.get((row.player_key, row.market))
        if actual is None:
            unmatched.append(f"{row.player_name} ({Market(row.market).label})")
            continue
        grade_projection(row, actual)
        graded += 1
    session.commit()

    return {
        "league": league.value,
        "date": on.isoformat(),
        "pending_before": len(pending),
        "graded": graded,
        "still_pending": len(pending) - graded,
        "source": fetched.source,
        "problems": fetched.problems,
        # Cap the echo: an empty feed would otherwise list the entire slate.
        "unmatched_sample": unmatched[:15],
    }


def pending_dates(
    session: Session, leagues: list[League] | None = None, max_days: int = 14
) -> list[date]:
    """Event dates that still have ungraded picks, newest first.

    Bounded by `max_days` so a long-abandoned database does not make every grading run
    re-query months of dead dates.
    """
    query = session.query(ProjectionRow.event_date).filter(
        ProjectionRow.graded_at.is_(None), ProjectionRow.event_date.isnot(None)
    )
    if leagues:
        query = query.filter(ProjectionRow.league.in_([l.value for l in leagues]))

    found: list[date] = []
    for (value,) in query.distinct().all():
        try:
            found.append(date.fromisoformat(value))
        except (TypeError, ValueError):
            continue
    return sorted(found, reverse=True)[:max_days]


def pending_picks(
    session: Session, league: League | None = None, limit: int = 200
) -> list[dict]:
    """Ungraded picks, with the keys needed to settle them by hand."""
    query = session.query(ProjectionRow).filter(ProjectionRow.graded_at.is_(None))
    if league is not None:
        query = query.filter(ProjectionRow.league == league.value)

    rows = query.order_by(ProjectionRow.event_date.desc()).limit(limit).all()
    return [
        {
            "player_key": row.player_key,
            "player_name": row.player_name,
            "league": row.league,
            "market": row.market,
            "market_label": Market(row.market).label,
            "side": row.side,
            "stat_line": row.stat_line,
            "event_date": row.event_date,
            "probability": round(row.calibrated_probability, 4),
        }
        for row in rows
    ]


def grade_from_results(
    session: Session, results: dict[tuple[str, str], float], league: League | None = None
) -> int:
    """Grade every ungraded pick that has a result.

    `results` is keyed by (player_key, market) so it can come from any source -- a
    provider fetch, a CSV, or a manual entry -- without this function caring which.
    """
    query = session.query(ProjectionRow).filter(ProjectionRow.graded_at.is_(None))
    if league is not None:
        query = query.filter(ProjectionRow.league == league.value)

    graded = 0
    for row in query.all():
        actual = results.get((row.player_key, row.market))
        if actual is None:
            continue
        grade_projection(row, actual)
        graded += 1
    session.commit()
    return graded


def build_track_record(
    session: Session,
    league: League | None = None,
    market: Market | None = None,
    include_demo: bool = False,
) -> TrackRecordResponse:
    """Everything the Track Record tab shows.

    Demo picks from `make seed` are excluded unless explicitly asked for, so the numbers
    on screen always describe real published picks.
    """
    from app.api.deps import DEMO_SOURCES

    query = session.query(ProjectionRow)
    if not include_demo:
        query = query.join(
            Snapshot, ProjectionRow.snapshot_id == Snapshot.id
        ).filter(Snapshot.source.notin_(DEMO_SOURCES))
    if league is not None:
        query = query.filter(ProjectionRow.league == league.value)
    if market is not None:
        query = query.filter(ProjectionRow.market == market.value)

    rows = query.order_by(ProjectionRow.graded_at.asc().nulls_last()).all()
    graded = [r for r in rows if r.won is not None]
    pending = len(rows) - len(graded)

    if not graded:
        return TrackRecordResponse(
            total_picks=len(rows), graded_picks=0, pending_picks=pending,
            wins=0, hit_rate=0.0, expected_hit_rate=0.0, roi=0.0, brier_score=0.0,
        )

    wins = sum(1 for r in graded if r.won)
    hit_rate = wins / len(graded)
    expected = sum(r.calibrated_probability for r in graded) / len(graded)
    brier = sum(
        (r.calibrated_probability - (1.0 if r.won else 0.0)) ** 2 for r in graded
    ) / len(graded)

    return TrackRecordResponse(
        total_picks=len(rows),
        graded_picks=len(graded),
        pending_picks=pending,
        wins=wins,
        hit_rate=round(hit_rate, 4),
        expected_hit_rate=round(expected, 4),
        roi=round(_roi(graded), 4),
        brier_score=round(brier, 5),
        avg_clv=_average_clv(graded),
        calibration=_calibration(graded),
        by_market=_by_market(graded),
        roi_series=_roi_series(graded),
        recent=_recent(rows),
    )


def _roi(rows: list[ProjectionRow]) -> float:
    """Return per unit staked, treating each pick as a single-leg wager at its EV odds.

    A pick priced against a 55% break-even is effectively laying odds of 1/0.55; a win
    returns that, a loss returns nothing. This makes ROI comparable across entry shapes
    instead of depending on how the user happened to build their slips.
    """
    staked = 0.0
    returned = 0.0
    for row in rows:
        odds = 1.0 / max(row.break_even_probability, 1e-6)
        staked += 1.0
        if row.won:
            returned += odds
    return (returned - staked) / staked if staked else 0.0


def _average_clv(rows: list[ProjectionRow]) -> float | None:
    """Closing-line value: did the line move our way after we posted the pick?

    Positive CLV is the strongest available evidence that a model is finding real edges
    rather than getting lucky, because it shows up long before results do.
    """
    moves = []
    for row in rows:
        if row.closing_line is None:
            continue
        movement = row.closing_line - row.stat_line
        moves.append(movement if row.side == "higher" else -movement)
    if not moves:
        return None
    return round(sum(moves) / len(moves), 4)


def _calibration(rows: list[ProjectionRow]) -> list[CalibrationBucket]:
    buckets: list[CalibrationBucket] = []
    for low, high in zip(BUCKET_EDGES, BUCKET_EDGES[1:]):
        members = [
            r for r in rows if low <= r.calibrated_probability < high
        ]
        if not members:
            continue
        buckets.append(
            CalibrationBucket(
                lower=low, upper=high,
                predicted=round(
                    sum(r.calibrated_probability for r in members) / len(members), 4
                ),
                actual=round(sum(1 for r in members if r.won) / len(members), 4),
                count=len(members),
            )
        )
    return buckets


def _by_market(rows: list[ProjectionRow]) -> list[MarketRecord]:
    grouped: dict[tuple[str, str], list[ProjectionRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.league, row.market)].append(row)

    records: list[MarketRecord] = []
    for (league, market), members in sorted(grouped.items()):
        wins = sum(1 for r in members if r.won)
        records.append(
            MarketRecord(
                league=League(league),
                market=Market(market),
                picks=len(members),
                wins=wins,
                hit_rate=round(wins / len(members), 4),
                expected_hit_rate=round(
                    sum(r.calibrated_probability for r in members) / len(members), 4
                ),
                roi=round(_roi(members), 4),
                brier=round(
                    sum(
                        (r.calibrated_probability - (1.0 if r.won else 0.0)) ** 2
                        for r in members
                    ) / len(members),
                    5,
                ),
            )
        )
    return sorted(records, key=lambda r: -r.picks)


def _roi_series(rows: list[ProjectionRow]) -> list[dict]:
    """Cumulative units won over time, for the track-record chart."""
    ordered = sorted(rows, key=lambda r: r.graded_at or datetime.min)
    series: list[dict] = []
    cumulative = 0.0
    for index, row in enumerate(ordered, start=1):
        odds = 1.0 / max(row.break_even_probability, 1e-6)
        cumulative += (odds - 1.0) if row.won else -1.0
        series.append(
            {
                "index": index,
                "date": row.graded_at.date().isoformat() if row.graded_at else None,
                "units": round(cumulative, 3),
            }
        )
    return series


def _recent(rows: list[ProjectionRow], limit: int = 60) -> list[dict]:
    ordered = sorted(
        rows, key=lambda r: r.graded_at or datetime.min, reverse=True
    )[:limit]
    return [
        {
            "player_name": row.player_name,
            "league": row.league,
            "market": Market(row.market).label,
            "side": row.side,
            "stat_line": row.stat_line,
            "projected": round(row.projected_mean, 2),
            "probability": round(row.calibrated_probability, 4),
            "actual": row.actual_value,
            "won": row.won,
            "graded_at": row.graded_at.isoformat() if row.graded_at else None,
        }
        for row in ordered
    ]
