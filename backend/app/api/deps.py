"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db import get_session
from app.models.calibration import Calibrator, fit_calibrator
from app.tables import ProjectionRow

__all__ = ["get_session", "build_calibrator"]


def build_calibrator(session: Session) -> Calibrator:
    """Fit calibration from graded history.

    Cheap enough to do per request at these volumes, and always current -- a stale
    cached calibrator that silently disagrees with the track record would be worse
    than the small cost of refitting.
    """
    rows = (
        session.query(
            ProjectionRow.league, ProjectionRow.market,
            ProjectionRow.model_probability, ProjectionRow.won,
        )
        .filter(ProjectionRow.won.isnot(None))
        .all()
    )
    return fit_calibrator([(r[0], r[1], r[2], bool(r[3])) for r in rows])
