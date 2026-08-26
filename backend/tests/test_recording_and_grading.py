"""Recording picks and settling them.

These cover the defects that made the Track Record untrustworthy: reads that wrote,
snapshots that duplicated, and grading that only ever happened by hand.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func

from app.db import Base, engine, session_scope
from app.domain import League, Market
from app.grading.results import (
    CfbResultsProvider, ResultFetch, _football_results_from_rows, estimate_week,
)
from app.main import app
from app.providers.cfbd import _is_completed, build_cfb_profiles
from app.tables import ProjectionRow, Snapshot


@pytest.fixture
def client():
    """A clean database per test -- these assert on absolute row counts."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _rows() -> int:
    with session_scope() as session:
        return session.query(ProjectionRow).count()


class TestReadsDoNotWrite:
    def test_loading_a_board_records_nothing(self, client):
        """The original defect: every GET inserted a full slate."""
        for _ in range(5):
            client.get("/api/board/MLB")
        assert _rows() == 0

    def test_filtered_reads_record_nothing_either(self, client):
        client.get("/api/board/NFL?team=KC")
        client.get("/api/board/NFL?market=receptions&mode=likely")
        assert _rows() == 0


class TestSnapshotIsIdempotent:
    def test_repeated_snapshots_record_each_pick_once(self, client):
        first = client.post("/api/board/MLB/snapshot").json()
        assert first["recorded"] > 0

        for _ in range(3):
            again = client.post("/api/board/MLB/snapshot").json()
            assert again["recorded"] == 0
            assert again["updated"] == first["recorded"]

        assert _rows() == first["recorded"]

    def test_the_projection_seed_is_stable_across_processes(self):
        """A per-player Monte-Carlo seed must not depend on `hash()`.

        String hashing is salted per process, so a seed derived from it is stable within
        one run and different in the next. That moved football projections by around a
        percent between runs, which is enough to flip the recommended side of a line the
        model prices as a coin flip -- and a flipped side is a new pick identity, so the
        overnight job would record a second row for a slate already recorded by the UI.
        """
        import subprocess
        import sys

        script = (
            "from app.models.football import _seed; "
            "print(_seed('cfb600056', 'rushing_yards'))"
        )
        seeds = {
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=True,
                env={"PYTHONPATH": "backend", "PATH": os.environ.get("PATH", "")},
            ).stdout.strip()
            for _ in range(3)
        }
        assert len(seeds) == 1, f"seed varied between processes: {seeds}"

    def test_no_pick_identity_is_ever_duplicated(self, client):
        for _ in range(3):
            client.post("/api/board/NFL/snapshot")
        with session_scope() as session:
            duplicated = (
                session.query(ProjectionRow.underdog_line_id)
                .group_by(
                    ProjectionRow.underdog_line_id,
                    ProjectionRow.side,
                    ProjectionRow.event_date,
                )
                .having(func.count() > 1)
                .count()
            )
        assert duplicated == 0

    def test_a_later_snapshot_updates_the_line_but_not_the_projection(self, client):
        client.post("/api/board/MLB/snapshot")
        with session_scope() as session:
            row = session.query(ProjectionRow).first()
            original_projection = row.projected_mean
            original_probability = row.calibrated_probability
            row_id = row.id
            # Simulate the line moving after we published.
            row.closing_line = 999.0
            session.commit()

        client.post("/api/board/MLB/snapshot")
        with session_scope() as session:
            row = session.get(ProjectionRow, row_id)
            assert row.projected_mean == original_projection
            assert row.calibrated_probability == original_probability
            assert row.closing_line != 999.0, "closing line should track the current line"

    def test_closing_line_is_populated_on_first_record(self, client):
        """CLV was always null before, because nothing ever set this."""
        client.post("/api/board/MLB/snapshot")
        with session_scope() as session:
            assert session.query(ProjectionRow).filter(
                ProjectionRow.closing_line.is_(None)
            ).count() == 0

    def test_a_settled_pick_is_not_touched_again(self, client):
        client.post("/api/board/MLB/snapshot")
        client.post("/api/track-record/grade/auto", json={"league": "MLB", "date": "2025-08-26"})
        with session_scope() as session:
            graded = session.query(ProjectionRow).filter(
                ProjectionRow.graded_at.isnot(None)
            ).first()
            assert graded is not None
            before = graded.closing_line
            graded_id = graded.id

        client.post("/api/board/MLB/snapshot")
        with session_scope() as session:
            assert session.get(ProjectionRow, graded_id).closing_line == before


class TestAutoGrading:
    def test_records_then_settles_a_full_mlb_slate(self, client):
        recorded = client.post("/api/board/MLB/snapshot").json()["recorded"]
        report = client.post(
            "/api/track-record/grade/auto", json={"league": "MLB", "date": "2025-08-26"}
        ).json()["reports"][0]

        assert report["pending_before"] == recorded
        assert report["graded"] == recorded
        assert report["still_pending"] == 0

    def test_grading_is_keyed_on_the_canonical_player_id(self, client):
        """Underdog's id would not join to any results feed."""
        client.post("/api/board/MLB/snapshot")
        with session_scope() as session:
            keys = {row.player_key for row in session.query(ProjectionRow).all()}
        assert keys and all(not key.startswith("line-") for key in keys)

    def test_the_track_record_reflects_graded_picks(self, client):
        client.post("/api/board/MLB/snapshot")
        client.post("/api/track-record/grade/auto", json={"league": "MLB", "date": "2025-08-26"})
        record = client.get("/api/track-record").json()
        assert record["graded_picks"] > 0
        assert 0.0 <= record["hit_rate"] <= 1.0
        assert record["by_market"]

    def test_regrading_is_a_no_op(self, client):
        client.post("/api/board/MLB/snapshot")
        payload = {"league": "MLB", "date": "2025-08-26"}
        first = client.post("/api/track-record/grade/auto", json=payload).json()["graded"]
        second = client.post("/api/track-record/grade/auto", json=payload).json()["graded"]
        assert first > 0 and second == 0

    def test_football_grading_infers_a_week_and_says_so(self, client):
        client.post("/api/board/NFL/snapshot")
        report = client.post(
            "/api/track-record/grade/auto", json={"league": "NFL", "date": "2025-11-02"}
        ).json()["reports"][0]
        assert any("inferred" in problem for problem in report["problems"])

    def test_an_explicit_week_produces_no_inference_warning(self, client):
        client.post("/api/board/NFL/snapshot")
        report = client.post(
            "/api/track-record/grade/auto",
            json={"league": "NFL", "date": "2025-11-02", "week": 9},
        ).json()["reports"][0]
        assert not any("inferred" in problem for problem in report["problems"])
        assert report["graded"] > 0

    def test_a_date_with_no_results_grades_nothing_and_explains_why(self, client):
        client.post("/api/board/MLB/snapshot")
        report = client.post(
            "/api/track-record/grade/auto", json={"league": "MLB", "date": "1999-01-01"}
        ).json()["reports"][0]
        assert report["graded"] == 0

    def test_a_bad_date_is_rejected(self, client):
        assert client.post(
            "/api/track-record/grade/auto", json={"date": "not-a-date"}
        ).status_code == 400


class TestPendingEndpoint:
    def test_lists_ungraded_picks_with_the_keys_needed_to_settle_them(self, client):
        client.post("/api/board/MLB/snapshot")
        pending = client.get("/api/track-record/pending").json()
        assert pending
        for field in ("player_key", "market", "side", "stat_line", "event_date"):
            assert field in pending[0]

    def test_settled_picks_drop_off_the_list(self, client):
        client.post("/api/board/MLB/snapshot")
        before = len(client.get("/api/track-record/pending?league=MLB").json())
        client.post("/api/track-record/grade/auto", json={"league": "MLB", "date": "2025-08-26"})
        after = len(client.get("/api/track-record/pending?league=MLB").json())
        assert before > 0 and after == 0


class TestResultMapping:
    def test_anytime_td_sums_rushing_and_receiving(self):
        """Reading one column would mis-settle a receiver who scored on the ground."""
        fetch = ResultFetch()
        _football_results_from_rows(
            [{"player_id": "p1", "rushing_tds": 1, "receiving_tds": 1}], fetch
        )
        assert fetch.results[("p1", Market.ANYTIME_TD.value)] == 2.0

    def test_a_player_who_did_not_score_records_zero_not_missing(self):
        fetch = ResultFetch()
        _football_results_from_rows([{"player_id": "p1", "receiving_yards": 40}], fetch)
        assert fetch.results[("p1", Market.ANYTIME_TD.value)] == 0.0

    def test_all_five_football_markets_are_mapped(self):
        fetch = ResultFetch()
        _football_results_from_rows([{"player_id": "p1"}], fetch)
        for market in (
            Market.RECEIVING_YARDS, Market.RUSHING_YARDS, Market.PASSING_YARDS,
            Market.RECEPTIONS, Market.ANYTIME_TD,
        ):
            assert ("p1", market.value) in fetch.results

    def test_cfbd_nested_payload_is_flattened(self):
        rows = CfbResultsProvider._flatten([{
            "teams": [{"categories": [
                {"name": "receiving", "types": [
                    {"name": "YDS", "athletes": [{"id": "a1", "stat": "102"}]},
                    {"name": "TD", "athletes": [{"id": "a1", "stat": "1"}]},
                ]},
                {"name": "rushing", "types": [
                    {"name": "YDS", "athletes": [{"id": "a1", "stat": "15"}]},
                ]},
            ]}]
        }])
        assert rows == [{
            "player_id": "a1", "receiving_yards": 102.0,
            "receiving_tds": 1.0, "rushing_yards": 15.0,
        }]

    def test_unknown_cfbd_categories_are_ignored(self):
        assert CfbResultsProvider._flatten([{
            "teams": [{"categories": [
                {"name": "defensive", "types": [
                    {"name": "TOT", "athletes": [{"id": "d1", "stat": "9"}]}
                ]}
            ]}]
        }]) == []

    def test_malformed_payloads_do_not_crash(self):
        assert CfbResultsProvider._flatten([]) == []
        assert CfbResultsProvider._flatten([{"teams": None}]) == []


class TestWeekInference:
    @pytest.mark.parametrize(("day", "expected"), [
        ("2025-09-04", 1), ("2025-09-11", 2), ("2025-10-16", 7),
    ])
    def test_nfl_weeks_track_the_calendar(self, day, expected):
        assert estimate_week(League.NFL, date.fromisoformat(day)) == expected

    def test_before_the_season_opens_is_week_one(self):
        assert estimate_week(League.NFL, date(2025, 8, 1)) == 1

    def test_the_estimate_is_bounded(self):
        assert 1 <= estimate_week(League.CFB, date(2026, 6, 1)) <= 20


class TestDemoDataIsolation:
    def test_seeded_picks_are_excluded_by_default(self, client):
        # Insert a demo snapshot directly rather than shelling out to the seed script.
        with session_scope() as session:
            snapshot = Snapshot(league="MLB", source="seed", line_count=1)
            session.add(snapshot)
            session.flush()
            session.add(ProjectionRow(
                snapshot_id=snapshot.id, league="MLB", market="strikeouts",
                underdog_line_id="demo-1", player_key="demo", player_name="Demo",
                stat_line=5.5, side="higher", projected_mean=6.0,
                model_probability=0.9, calibrated_probability=0.9,
                break_even_probability=0.55, edge=0.35, ev_per_dollar=0.6, won=True,
                graded_at=date.today(),
            ))
            session.commit()

        assert client.get("/api/track-record").json()["graded_picks"] == 0
        assert client.get("/api/track-record?include_demo=true").json()["graded_picks"] == 1

    def test_seeded_picks_never_reach_the_calibrator(self, client):
        from app.api.deps import build_calibrator

        with session_scope() as session:
            snapshot = Snapshot(league="MLB", source="seed", line_count=1)
            session.add(snapshot)
            session.flush()
            for index in range(400):
                session.add(ProjectionRow(
                    snapshot_id=snapshot.id, league="MLB", market="strikeouts",
                    underdog_line_id=f"demo-{index}", player_key="demo",
                    player_name="Demo", stat_line=5.5, side="higher",
                    projected_mean=6.0, model_probability=0.8,
                    calibrated_probability=0.8, break_even_probability=0.55,
                    edge=0.25, ev_per_dollar=0.4, won=False, graded_at=date.today(),
                ))
            session.commit()
            assert build_calibrator(session).is_empty


class TestCfbGamesPlayed:
    def test_each_team_uses_its_own_game_count(self):
        rows = [
            {"playerId": "1", "player": "A", "team": "GA",
             "category": "rushing", "statType": "CAR", "stat": 100},
            {"playerId": "2", "player": "B", "team": "VAN",
             "category": "rushing", "statType": "CAR", "stat": 100},
        ]
        profiles, _ = build_cfb_profiles(rows, 8, {}, {"GA": 12, "VAN": 4})
        assert profiles["1"].games == 12
        assert profiles["2"].games == 4

    def test_falls_back_when_the_schedule_is_unavailable(self):
        rows = [{"playerId": "1", "player": "A", "team": "GA",
                 "category": "rushing", "statType": "CAR", "stat": 100}]
        profiles, _ = build_cfb_profiles(rows, 8, {}, {})
        assert profiles["1"].games == 8

    @pytest.mark.parametrize(("game", "expected"), [
        ({"completed": True}, True), ({"completed": False}, False),
        ({"home_points": 21}, True), ({"homePoints": 0}, True), ({}, False),
    ])
    def test_completion_detection_handles_every_spelling(self, game, expected):
        assert _is_completed(game) is expected
