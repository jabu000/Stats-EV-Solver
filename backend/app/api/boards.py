"""Board endpoints: the ranked list behind the MLB, NFL and CFB tabs."""

from __future__ import annotations

from datetime import date, datetime, timezone
from json import dumps

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import build_calibrator, get_session
from app.domain import MARKETS_BY_LEAGUE, League, Market
from app.ingest.pipeline import BoardBuilder
from app.providers.underdog import parse_manual_import
from app.providers.base import ProviderError
from app.schemas import BoardResponse
from app.services.settings_store import load_settings
from app.tables import ProjectionRow, Snapshot

router = APIRouter(prefix="/api", tags=["boards"])


@router.get("/markets/{league}")
def markets_for_league(league: League) -> list[dict]:
    """The bet-type row under the tabs."""
    return [
        {"value": market.value, "label": market.label}
        for market in MARKETS_BY_LEAGUE[league]
    ]


@router.get("/board/{league}", response_model=BoardResponse)
def get_board(
    league: League,
    mode: str = Query("value", pattern="^(value|likely)$"),
    market: Market | None = None,
    team: str | None = None,
    game: str | None = None,
    position: str | None = None,
    search: str | None = None,
    min_edge: float | None = None,
    min_confidence: float | None = None,
    min_probability: float | None = None,
    limit: int = Query(300, ge=1, le=1000),
    persist: bool = True,
    session: Session = Depends(get_session),
) -> BoardResponse:
    """Build, filter and return one league's board."""
    settings = load_settings(session)
    builder = BoardBuilder(session, settings, build_calibrator(session))
    board = builder.build(league, mode)
    session.commit()

    if persist and board.bets:
        _persist_snapshot(session, board)

    board.bets = _filter(
        board.bets,
        market=market, team=team, game=game, position=position, search=search,
        min_edge=min_edge, min_confidence=min_confidence, min_probability=min_probability,
    )[:limit]
    return board


@router.post("/board/{league}/import", response_model=BoardResponse)
def import_board(
    league: League,
    payload: dict,
    mode: str = Query("value", pattern="^(value|likely)$"),
    session: Session = Depends(get_session),
) -> BoardResponse:
    """Price a manually pasted slate (CSV or the raw API JSON).

    This is the escape hatch for the day Underdog's endpoint is unreachable or starts
    demanding credentials nobody has -- the platform still works, it just needs the
    lines handed to it.
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Provide a `text` field containing CSV or JSON lines.")

    try:
        lines = parse_manual_import(text, league)
    except ProviderError as exc:
        raise HTTPException(400, exc.message) from exc
    except Exception as exc:
        raise HTTPException(400, f"Could not parse the pasted slate: {exc}") from exc

    if not lines:
        raise HTTPException(
            400,
            "No usable lines found. Expected a CSV with at least 'player' and 'line' "
            "columns, plus a 'market' column naming a tracked bet type.",
        )

    settings = load_settings(session)
    builder = BoardBuilder(session, settings, build_calibrator(session))
    board = builder.build(league, mode, imported_lines=lines)
    session.commit()
    return board


def _persist_snapshot(session: Session, board: BoardResponse) -> None:
    """Record what we published, so the Track Record can grade it later.

    Written once per refresh and never updated: the point of the track record is that
    it shows what we actually said at the time, not a retroactively improved version.
    """
    snapshot = Snapshot(
        league=board.league.value,
        captured_at=board.generated_at,
        source=board.source,
        line_count=len(board.bets),
    )
    session.add(snapshot)
    session.flush()

    for bet in board.bets:
        session.add(
            ProjectionRow(
                snapshot_id=snapshot.id,
                league=bet.league.value,
                market=bet.market.value,
                underdog_line_id=bet.underdog_line_id,
                player_key=bet.player_key,
                player_name=bet.player_name,
                team=bet.team,
                opponent=bet.opponent,
                game_id=bet.game_id,
                event_date=bet.starts_at.date().isoformat() if bet.starts_at else None,
                starts_at=bet.starts_at.replace(tzinfo=None) if bet.starts_at else None,
                stat_line=bet.stat_line,
                side=bet.side.value,
                payout_multiplier=bet.payout_multiplier,
                projected_mean=bet.projected_mean,
                model_probability=bet.model_probability,
                calibrated_probability=bet.calibrated_probability,
                break_even_probability=bet.break_even_probability,
                edge=bet.edge,
                ev_per_dollar=bet.ev_per_dollar,
                confidence=bet.confidence,
                factors_json=dumps([f.model_dump() for f in bet.factors]),
            )
        )
    session.commit()


def _filter(bets, **criteria):
    """Apply the UI's filter bar. Absent criteria are no-ops."""
    result = bets
    if criteria.get("market"):
        result = [b for b in result if b.market is criteria["market"]]
    if criteria.get("team"):
        wanted = criteria["team"].upper()
        result = [
            b for b in result
            if (b.team or "").upper() == wanted or (b.opponent or "").upper() == wanted
        ]
    if criteria.get("game"):
        result = [b for b in result if b.game_label == criteria["game"]]
    if criteria.get("position"):
        wanted = criteria["position"].upper()
        result = [b for b in result if (b.position or "").upper() == wanted]
    if criteria.get("search"):
        needle = criteria["search"].lower()
        result = [b for b in result if needle in b.player_name.lower()]
    if criteria.get("min_edge") is not None:
        result = [b for b in result if b.edge >= criteria["min_edge"]]
    if criteria.get("min_confidence") is not None:
        result = [b for b in result if b.confidence >= criteria["min_confidence"]]
    if criteria.get("min_probability") is not None:
        result = [b for b in result if b.calibrated_probability >= criteria["min_probability"]]
    return result
