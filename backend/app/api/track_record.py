"""Track Record endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from dateutil import parser as date_parser
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.domain import League, Market
from app.grading.grader import (
    GRADE_LOOKBACK_DAYS,
    auto_grade,
    build_track_record,
    grade_from_results,
    pending_dates,
    pending_picks,
)
from app.schemas import TrackRecordResponse

router = APIRouter(prefix="/api/track-record", tags=["track record"])


@router.get("", response_model=TrackRecordResponse)
def track_record(
    league: League | None = None,
    market: Market | None = None,
    include_demo: bool = False,
    session: Session = Depends(get_session),
) -> TrackRecordResponse:
    return build_track_record(session, league, market, include_demo=include_demo)


@router.post("/grade")
def grade(payload: dict, session: Session = Depends(get_session)) -> dict:
    """Grade pending picks from supplied results.

    Body: `{"results": [{"player_key": "...", "market": "...", "actual": 7}]}`.
    Kept source-agnostic so results can come from a provider fetch, a CSV, or by hand.
    """
    entries = payload.get("results")
    if not isinstance(entries, list) or not entries:
        raise HTTPException(400, "Provide a non-empty `results` array.")

    results: dict[tuple[str, str], float] = {}
    for entry in entries:
        try:
            key = (str(entry["player_key"]), str(entry["market"]))
            results[key] = float(entry["actual"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                400, f"Each result needs player_key, market and a numeric actual: {exc}"
            ) from exc

    graded = grade_from_results(session, results)
    return {"graded": graded, "submitted": len(results)}


@router.get("/pending")
def pending(
    league: League | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> list[dict]:
    """Picks still waiting on a result, with the keys needed to grade them."""
    return pending_picks(session, league, limit)


@router.post("/grade/auto")
def grade_auto(payload: dict | None = None, session: Session = Depends(get_session)) -> dict:
    """Fetch real results and settle the picks they cover.

    Body is optional: `{"league": "MLB", "date": "2025-08-26", "week": 11}`. With no
    league, every league is attempted. With no date, yesterday is used, since results
    are not final until games finish.

    Football needs a `week` -- results are published per week, not per date -- and the
    response says so rather than silently grading nothing.
    """
    payload = payload or {}

    if payload.get("league"):
        try:
            leagues = [League(str(payload["league"]).upper())]
        except ValueError as exc:
            raise HTTPException(400, f"Unknown league: {exc}") from exc
    else:
        leagues = list(League)

    explicit_date = bool(payload.get("date"))
    if explicit_date:
        try:
            dates = [date_parser.parse(str(payload["date"])).date()]
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"Could not parse date: {exc}") from exc
    else:
        # "Grade now" should settle everything outstanding, not just yesterday --
        # picks sit unsettled for a few days when a job is missed or a feed is late.
        dates = pending_dates(session, leagues, max_days=GRADE_LOOKBACK_DAYS)
        if not dates:
            return {"date": None, "graded": 0, "reports": [],
                    "note": "Nothing is waiting on results."}

    week = payload.get("week")
    season = payload.get("season")
    reports = [
        auto_grade(
            session, league, on,
            season=int(season) if season else None,
            week=int(week) if week else None,
        )
        for on in dates
        for league in leagues
    ]
    # When the dates were discovered rather than asked for, drop the leagues that had
    # nothing outstanding -- they are noise. An explicitly requested date is a question,
    # so it gets an answer even when the answer is "nothing was pending".
    if not explicit_date:
        reports = [r for r in reports if r["pending_before"] > 0]
    return {
        "date": dates[0].isoformat() if len(dates) == 1 else
                f"{dates[-1].isoformat()} to {dates[0].isoformat()}",
        "graded": sum(r["graded"] for r in reports),
        "reports": reports,
    }
