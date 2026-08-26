"""Entry builder endpoint: price a slip and size the stake."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.db import get_session as _  # noqa: F401
from app.pricing.entry import break_even_probability, price_entry
from app.schemas import EntryRequest, EntryResponse
from app.services.settings_store import load_settings

router = APIRouter(prefix="/api/entry", tags=["entries"])


@router.post("/ev", response_model=EntryResponse)
def entry_ev(
    request: EntryRequest, session: Session = Depends(get_session)
) -> EntryResponse:
    settings = load_settings(session)
    return price_entry(
        request.legs,
        structure=settings.payout_structure(),
        entry_type=request.entry_type,
        stake=request.stake,
        bankroll=request.bankroll or settings.bankroll,
        kelly_multiplier=request.kelly_fraction or settings.kelly_fraction,
    )


@router.get("/break-even")
def break_even(
    entry_type: str = "standard",
    legs: int = 3,
    session: Session = Depends(get_session),
) -> dict:
    """The per-leg probability an entry of this shape needs, for the UI to display."""
    settings = load_settings(session)
    structure = settings.payout_structure()
    return {
        "entry_type": entry_type,
        "legs": legs,
        "break_even": round(break_even_probability(structure, entry_type, legs), 5),
        "payouts": {
            str(k): v for k, v in structure.table(entry_type, legs).items()
        },
        "supported_sizes": structure.supported_sizes(entry_type),
    }
