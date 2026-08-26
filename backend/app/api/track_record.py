"""Track Record endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.domain import League, Market
from app.grading.grader import build_track_record, grade_from_results
from app.schemas import TrackRecordResponse

router = APIRouter(prefix="/api/track-record", tags=["track record"])


@router.get("", response_model=TrackRecordResponse)
def track_record(
    league: League | None = None,
    market: Market | None = None,
    session: Session = Depends(get_session),
) -> TrackRecordResponse:
    return build_track_record(session, league, market)


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
