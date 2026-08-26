"""Seed a graded pick history so the Track Record tab has something to show.

Simulating outcomes rather than shipping a static table means the calibration curve,
Brier score and ROI are all *computed* by the real grading code -- if that code is
wrong, this seed will show it. Outcomes are drawn from a deliberately **overconfident**
version of the model (true probability shrunk toward a coin flip), which is the failure
mode a track record exists to catch: the calibration chart should visibly bend away from
the diagonal, and expected hit rate should sit above actual.

This is demo data. Delete the database to clear it before tracking real picks.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.domain import MARKETS_BY_LEAGUE, League  # noqa: E402
from app.tables import ProjectionRow, Snapshot  # noqa: E402

RNG = random.Random(4242)

#: How overconfident the simulated model is. 1.0 = perfectly calibrated.
OVERCONFIDENCE = 0.72


def seed(days: int = 45, picks_per_day: int = 14) -> int:
    init_db()
    created = 0
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        if session.query(ProjectionRow).filter(ProjectionRow.won.isnot(None)).count():
            print("Graded history already present; nothing seeded.")
            return 0

        for day_offset in range(days, 0, -1):
            day = now - timedelta(days=day_offset)
            league = RNG.choice(list(League))
            snapshot = Snapshot(
                league=league.value, captured_at=day, source="seed",
                line_count=picks_per_day, notes="Simulated history for demonstration",
            )
            session.add(snapshot)
            session.flush()

            for index in range(picks_per_day):
                market = RNG.choice(MARKETS_BY_LEAGUE[league])
                stated = RNG.uniform(0.52, 0.82)
                break_even = 0.5503

                # The truth the model does not know: it is overconfident.
                true = 0.5 + (stated - 0.5) * OVERCONFIDENCE
                won = RNG.random() < true

                line = round(RNG.uniform(0.5, 80.5) * 2) / 2
                projected = line * RNG.uniform(0.9, 1.1)
                side = RNG.choice(["higher", "lower"])
                actual = line + (
                    abs(RNG.gauss(2, 3)) if (won == (side == "higher")) else -abs(RNG.gauss(2, 3))
                )

                session.add(
                    ProjectionRow(
                        snapshot_id=snapshot.id,
                        league=league.value,
                        market=market.value,
                        underdog_line_id=f"seed-{day_offset}-{index}",
                        player_key=f"seed-player-{index}",
                        player_name=f"Sample Player {index + 1}",
                        team="---",
                        event_date=day.date().isoformat(),
                        starts_at=day.replace(tzinfo=None),
                        stat_line=line,
                        side=side,
                        payout_multiplier=1.0,
                        projected_mean=round(projected, 2),
                        model_probability=round(stated, 5),
                        calibrated_probability=round(stated, 5),
                        break_even_probability=break_even,
                        edge=round(stated - break_even, 5),
                        ev_per_dollar=round(stated / break_even - 1, 5),
                        confidence=round(RNG.uniform(0.5, 1.0), 3),
                        factors_json="[]",
                        actual_value=round(max(0.0, actual), 2),
                        won=won,
                        push=False,
                        graded_at=day.replace(tzinfo=None) + timedelta(hours=6),
                        closing_line=line + round(RNG.gauss(0, 0.6) * 2) / 2,
                    )
                )
                created += 1
    return created


if __name__ == "__main__":
    count = seed()
    print(f"Seeded {count} graded picks.")
