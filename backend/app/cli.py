"""Command line entry points, for the scheduler and for running things by hand.

Every command is a thin wrapper over the same functions the API calls, so there is no
second implementation to drift out of sync with the one the UI exercises.

    python -m app.cli snapshot --league MLB
    python -m app.cli grade --date 2025-08-26
    python -m app.cli status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dateutil import parser as date_parser

from app.config import REPO_ROOT, get_settings
from app.db import init_db, session_scope
from app.domain import League

LOG_DIR = REPO_ROOT / "data" / "logs"
#: Trim a log once it passes this, so an always-on service cannot fill the disk.
MAX_LOG_BYTES = 5_000_000


def log(message: str, *, stream: str = "jobs") -> None:
    """Append a timestamped line to both stdout and the job log."""
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"{stamp}  {message}"
    print(line, flush=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{stream}.log"
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        # Keep the tail rather than pulling in a rotation dependency.
        tail = path.read_text(encoding="utf-8", errors="replace")[-MAX_LOG_BYTES // 2 :]
        path.write_text(f"[log truncated]\n{tail}", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _redact(url: str) -> str:
    """Strip credentials from a database URL before it is printed or logged.

    `status` output ends up in job logs and in screenshots of support requests; a
    managed Postgres URL carries its password inline.
    """
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def _leagues(value: str | None) -> list[League]:
    if not value or value.upper() == "ALL":
        return list(League)
    return [League(value.upper())]


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    return date_parser.parse(value).date()


# ------------------------------------------------------------------- commands
def cmd_snapshot(args: argparse.Namespace) -> int:
    """Record the current slate for each league, for later grading."""
    from app.api.deps import build_calibrator
    from app.api.boards import _persist_snapshot
    from app.ingest.pipeline import BoardBuilder
    from app.services.settings_store import load_settings

    init_db()
    failures = 0
    for league in _leagues(args.league):
        try:
            with session_scope() as session:
                settings = load_settings(session)
                board = BoardBuilder(
                    session, settings, build_calibrator(session)
                ).build(league)
                if not board.bets:
                    log(f"snapshot {league.value}: no bets ({'; '.join(board.notes) or 'quiet slate'})")
                    continue
                recorded, updated = _persist_snapshot(session, board)
                log(
                    f"snapshot {league.value}: {recorded} new, {updated} line updates "
                    f"(source={board.source})"
                )
        except Exception as exc:  # a bad slate must not kill the other leagues
            failures += 1
            log(f"snapshot {league.value} FAILED: {exc}")
    return 1 if failures else 0


def cmd_grade(args: argparse.Namespace) -> int:
    """Settle outstanding picks against real results.

    With no `--date`, every date that still has ungraded picks is attempted, not just
    yesterday. A scheduled run that fails -- the service was down, a feed was late, a
    Monday-night game finished after the job ran -- would otherwise leave those picks
    pending forever, and the track record would quietly become a biased sample of the
    days the job happened to work.
    """
    from app.grading.grader import GRADE_LOOKBACK_DAYS, auto_grade, pending_dates

    init_db()
    leagues = _leagues(args.league)

    if args.date:
        dates = [_parse_date(args.date, date.today() - timedelta(days=1))]
    else:
        with session_scope() as session:
            dates = pending_dates(session, leagues, max_days=GRADE_LOOKBACK_DAYS)
        if not dates:
            log("grade: nothing is waiting on results")
            return 0

    failures = 0
    for on in dates:
        for league in leagues:
            try:
                with session_scope() as session:
                    report = auto_grade(
                        session, league, on,
                        season=args.season, week=args.week,
                    )
                if report["pending_before"] == 0 and not args.date:
                    continue  # another league's date; not this one's problem
                log(
                    f"grade {league.value} {on}: "
                    f"{report['graded']}/{report['pending_before']} settled, "
                    f"{report['still_pending']} still pending"
                )
                for problem in report["problems"]:
                    log(f"  ! {league.value}: {problem}")
            except Exception as exc:
                failures += 1
                log(f"grade {league.value} {on} FAILED: {exc}")
    return 1 if failures else 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print what the platform currently knows. Useful for checking the service."""
    from app.grading.grader import build_track_record
    from app.tables import ProjectionRow

    init_db()
    settings = get_settings()
    with session_scope() as session:
        pending = (
            session.query(ProjectionRow).filter(ProjectionRow.graded_at.is_(None)).count()
        )
        record = build_track_record(session)

    payload = {
        "data_mode": settings.data_mode.value,
        "database": _redact(settings.sqlalchemy_url),
        "pending_picks": pending,
        "graded_picks": record.graded_picks,
        "hit_rate": record.hit_rate,
        "expected_hit_rate": record.expected_hit_rate,
        "roi": record.roi,
        "brier_score": record.brier_score,
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import os
    import uvicorn

    # A PaaS assigns the port and expects the process to bind every interface. Defaults
    # stay loopback-only so running this on a laptop does not quietly expose the board
    # to the coffee shop's wifi.
    host = args.host or os.environ.get("HOST", "127.0.0.1")
    port = args.port or int(os.environ.get("PORT", "8000"))

    uvicorn.run(
        "app.main:app", host=host, port=port, reload=args.reload, log_level="info"
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Report which upstream providers are answering. Mirrors the Settings tab."""
    from app.api.settings import test_connections

    init_db()
    with session_scope() as session:
        statuses = test_connections(session)
    worst = 0
    for status in statuses:
        mark = "ok  " if status.ok else "FAIL"
        print(f"{mark}  {status.label:32} {status.status:16} {status.detail}")
        worst = worst or (0 if status.ok else 1)
    return worst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="record the current slate for grading")
    snap.add_argument("--league", help="MLB, NFL, CFB, or ALL (default)")
    snap.set_defaults(func=cmd_snapshot)

    grade = sub.add_parser("grade", help="settle picks against real results")
    grade.add_argument("--league", help="MLB, NFL, CFB, or ALL (default)")
    grade.add_argument(
        "--date", help="date to grade (default: every date with pending picks)"
    )
    grade.add_argument("--season", type=int, help="football season")
    grade.add_argument("--week", type=int, help="football week (default: inferred from the date)")
    grade.set_defaults(func=cmd_grade)

    status = sub.add_parser("status", help="print current track-record state")
    status.set_defaults(func=cmd_status)

    check = sub.add_parser("check", help="test every upstream provider")
    check.set_defaults(func=cmd_check)

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", help="default: $HOST, else 127.0.0.1")
    serve.add_argument("--port", type=int, help="default: $PORT, else 8000")
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
