"""Settings endpoints: credentials, payout structure, provider health, unmapped names."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.config import get_settings
from app.domain import League
from app.providers.base import Provider
from app.providers.cfbd import CfbdProvider
from app.providers.market import MarketProvider
from app.providers.mlb_statsapi import MlbStatsProvider
from app.providers.nflverse import NflverseProvider
from app.providers.underdog import UnderdogProvider
from app.providers.weather import WeatherProvider
from app.schemas import ProviderStatus, UnmappedEntry
from app.services.settings_store import clear_secret, load_settings, save_settings
from app.tables import PlayerAlias, ProviderCall, UnmappedPlayer

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings(session: Session = Depends(get_session)) -> dict:
    settings = load_settings(session)
    return {
        **settings.redacted(),
        "data_mode": get_settings().data_mode.value,
    }


@router.put("")
def update_settings(updates: dict, session: Session = Depends(get_session)) -> dict:
    settings = save_settings(session, updates)
    return {**settings.redacted(), "data_mode": get_settings().data_mode.value}


@router.delete("/secret/{key}")
def delete_secret(key: str, session: Session = Depends(get_session)) -> dict:
    return clear_secret(session, key).redacted()


@router.post("/test-connections", response_model=list[ProviderStatus])
def test_connections(session: Session = Depends(get_session)) -> list[ProviderStatus]:
    """Check every upstream and report what is actually answering.

    This is the first thing to run after switching to live mode: it tells you in one
    click whether Underdog is reachable, whether your token is still valid, and whether
    the CFB key is set -- rather than leaving you to infer it from an empty board.
    """
    user = load_settings(session)
    providers: list[Provider] = [
        UnderdogProvider(token=user.effective_underdog_token()),
        MlbStatsProvider(),
        NflverseProvider(),
        CfbdProvider(api_key=user.effective_cfbd_key()),
        WeatherProvider(),
        MarketProvider(),
    ]

    mode = get_settings().data_mode.value
    results: list[ProviderStatus] = []
    for provider in providers:
        started = time.perf_counter()
        try:
            ok, status, detail = provider.health_check()
        except Exception as exc:  # a provider bug must not break the settings page
            ok, status, detail = False, "error", str(exc)
        duration = int((time.perf_counter() - started) * 1000)

        key_present = True
        if provider.requires_key:
            key_present = bool(getattr(provider, "api_key", ""))

        session.add(
            ProviderCall(
                provider=provider.name, mode=mode, ok=ok,
                status=status, detail=detail[:500], duration_ms=duration,
            )
        )
        results.append(
            ProviderStatus(
                provider=provider.name, label=provider.label, ok=ok, mode=mode,
                status=status, detail=detail, duration_ms=duration,
                requires_key=provider.requires_key, key_present=key_present,
            )
        )
    session.commit()
    return results


@router.get("/unmapped", response_model=list[UnmappedEntry])
def unmapped_players(
    league: League | None = None, session: Session = Depends(get_session)
) -> list[UnmappedEntry]:
    """Names we could not resolve. Surfaced so a missing star is visible, not silent."""
    query = session.query(UnmappedPlayer)
    if league is not None:
        query = query.filter_by(league=league.value)
    rows = query.order_by(UnmappedPlayer.times_seen.desc()).limit(200).all()
    return [
        UnmappedEntry(
            league=League(row.league), source_name=row.source_name, team=row.team,
            best_guess=row.best_guess, best_score=round(row.best_score, 3),
            times_seen=row.times_seen,
        )
        for row in rows
    ]


@router.post("/unmapped/resolve")
def resolve_unmapped(payload: dict, session: Session = Depends(get_session)) -> dict:
    """Manually bind an unresolved name to a canonical player id."""
    league = str(payload.get("league") or "")
    source_name = str(payload.get("source_name") or "")
    canonical_id = str(payload.get("canonical_id") or "")
    canonical_name = str(payload.get("canonical_name") or canonical_id)
    if not (league and source_name and canonical_id):
        return {"ok": False, "error": "league, source_name and canonical_id are required"}

    from app.ingest.mapping import normalize_name

    session.merge(
        PlayerAlias(
            league=league, source="underdog", source_name=normalize_name(source_name),
            canonical_id=canonical_id, canonical_name=canonical_name,
            confidence=1.0, resolved_by="manual",
        )
    )
    session.query(UnmappedPlayer).filter_by(
        league=league, source_name=source_name
    ).delete()
    session.commit()
    return {"ok": True}
