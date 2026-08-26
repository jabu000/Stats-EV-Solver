"""In-process job schedule, for deployments with no separate cron service.

The laptop story is launchd/systemd (`make install-service`). A single always-on web
service has no such thing, and paying for a second service just to run two commands a
day is silly -- so the same commands can run on a background task inside the API.

Deliberately simple: wake every minute, run the jobs whose scheduled hour has arrived
and has not been run yet today. No cron expressions, no persistence of run state, no
catch-up on missed windows. Missing a snapshot costs one board; the grading job settles
every outstanding date whenever it does run, so a missed grade fixes itself.

Off unless `ENABLE_SCHEDULER=true`. Running it alongside the launchd/systemd jobs would
double up, which is harmless -- snapshots are idempotent -- but wasteful.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.cli import log
from app.config import get_settings

#: How often to check the clock. Fine-grained enough that a job fires within a minute
#: of its hour, coarse enough to be free.
TICK_SECONDS = 60


async def _run_blocking(func) -> None:
    """Run a synchronous job off the event loop, so requests keep being served."""
    await asyncio.to_thread(func)


def _snapshot() -> None:
    from app.cli import cmd_snapshot
    import argparse

    cmd_snapshot(argparse.Namespace(league=None))


def _grade() -> None:
    from app.cli import cmd_grade
    import argparse

    cmd_grade(argparse.Namespace(league=None, date=None, season=None, week=None))


def due_jobs(
    now: datetime, hours: list[int], done: set[tuple[str, int]]
) -> tuple[list[str], set[tuple[str, int]]]:
    """Decide what this tick should run, and return the updated run marks.

    Pure, so the schedule can be tested against a clock handed to it rather than by
    waiting an hour to find out. Job names come back in the order they must run --
    grading a slate that has not been recorded yet would settle nothing.
    """
    today = now.date().isoformat()
    # Forget yesterday's marks so today's hours can fire again.
    done = {mark for mark in done if mark[0] == today}

    if now.hour not in hours or (today, now.hour) in done:
        return [], done

    done = done | {(today, now.hour)}
    jobs = ["snapshot"]
    if now.hour == hours[-1]:
        jobs.append("grade")
    return jobs, done


async def run_scheduler() -> None:
    settings = get_settings()
    hours = settings.scheduler_hours
    if not hours:
        log("scheduler: no valid hours configured, not starting", stream="jobs")
        return

    log(
        f"scheduler: snapshots at {hours} UTC, grading at {hours[-1]}:00 UTC",
        stream="jobs",
    )

    runners = {"snapshot": _snapshot, "grade": _grade}
    done: set[tuple[str, int]] = set()
    while True:
        try:
            jobs, done = due_jobs(datetime.now(timezone.utc), hours, done)
            for name in jobs:
                await _run_blocking(runners[name])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bad slate must never kill the schedule
            log(f"scheduler tick FAILED: {exc}", stream="jobs")

        await asyncio.sleep(TICK_SECONDS)
