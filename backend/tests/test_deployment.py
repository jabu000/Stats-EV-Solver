"""Deployment surface: database URLs, the password gate, the in-process scheduler.

None of this affects a laptop install, which is exactly why it needs tests -- a defect
here shows up only once the thing is public, and the failure modes are "the track record
resets on every deploy" and "anyone with the URL can read your bets".
"""

from __future__ import annotations

import base64
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.security import BasicAuthMiddleware


class TestDatabaseUrlNormalisation:
    """Managed Postgres hands out a scheme SQLAlchemy 2 refuses to open."""

    @pytest.mark.parametrize(
        "given, expected",
        [
            (
                "postgres://u:p@host/db",
                "postgresql+psycopg://u:p@host/db",
            ),
            (
                "postgresql://u:p@host/db",
                "postgresql+psycopg://u:p@host/db",
            ),
            (
                "postgresql+psycopg://u:p@host/db",
                "postgresql+psycopg://u:p@host/db",
            ),
            ("sqlite:///./data/solver.db", "sqlite:///./data/solver.db"),
        ],
    )
    def test_the_url_render_hands_out_is_rewritten(self, given, expected):
        assert Settings(database_url=given).sqlalchemy_url == expected

    def test_the_password_survives_rewriting(self):
        url = Settings(
            database_url="postgres://solver:p%40ss:word@host:5432/db"
        ).sqlalchemy_url
        assert url.endswith("solver:p%40ss:word@host:5432/db")


class TestCredentialRedaction:
    def test_status_output_does_not_leak_the_database_password(self):
        from app.cli import _redact

        printed = _redact("postgresql+psycopg://solver:hunter2@host:5432/db")
        assert "hunter2" not in printed
        assert "solver" in printed and "host:5432/db" in printed

    def test_a_url_with_no_credentials_is_left_alone(self):
        from app.cli import _redact

        assert _redact("sqlite:////var/data/solver.db") == "sqlite:////var/data/solver.db"


def _gated_app(password: str = "letmein") -> TestClient:
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware, password=password)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/board/MLB")
    def board():
        return {"bets": []}

    return TestClient(app)


class TestPasswordGate:
    def test_an_unauthenticated_request_is_refused(self):
        response = _gated_app().get("/api/board/MLB")
        assert response.status_code == 401
        # Without the challenge header a browser never shows a login prompt.
        assert response.headers["www-authenticate"].startswith("Basic")

    def test_the_right_password_gets_through(self):
        token = base64.b64encode(b"anyone:letmein").decode()
        response = _gated_app().get(
            "/api/board/MLB", headers={"Authorization": f"Basic {token}"}
        )
        assert response.status_code == 200

    def test_the_wrong_password_does_not(self):
        token = base64.b64encode(b"anyone:wrong").decode()
        response = _gated_app().get(
            "/api/board/MLB", headers={"Authorization": f"Basic {token}"}
        )
        assert response.status_code == 401

    def test_only_the_password_is_checked_not_the_username(self):
        for user in (b"", b"jo", b"someone-else"):
            token = base64.b64encode(user + b":letmein").decode()
            assert (
                _gated_app()
                .get("/api/board/MLB", headers={"Authorization": f"Basic {token}"})
                .status_code
                == 200
            )

    def test_a_malformed_header_is_refused_not_crashed(self):
        client = _gated_app()
        for header in ("Basic !!!not-base64!!!", "Bearer letmein", "Basic", "garbage"):
            assert (
                client.get("/api/board/MLB", headers={"Authorization": header}).status_code
                == 401
            )

    def test_the_health_check_stays_open(self):
        """Render polls it without credentials; a gated health check fails the deploy."""
        assert _gated_app().get("/api/health").status_code == 200

    def test_no_password_configured_means_no_gate(self):
        """The laptop default has to stay frictionless."""
        assert Settings().access_password == ""


class TestSchedulerConfig:
    def test_it_is_off_unless_asked_for(self):
        assert Settings().enable_scheduler is False

    def test_hours_are_parsed_sorted_and_deduplicated(self):
        assert Settings(scheduler_hours_utc="23, 16,20, 16").scheduler_hours == [16, 20, 23]

    def test_nonsense_hours_are_dropped_rather_than_crashing_the_boot(self):
        assert Settings(scheduler_hours_utc="16,99,-3,abc,20").scheduler_hours == [16, 20]

    def test_an_entirely_invalid_schedule_yields_nothing_to_run(self):
        assert Settings(scheduler_hours_utc="nonsense").scheduler_hours == []


class TestCorsOrigins:
    def test_the_dev_server_is_always_allowed(self):
        assert "http://localhost:5173" in Settings().allowed_origins

    def test_configured_origins_are_added(self):
        origins = Settings(
            cors_origins="https://solver.onrender.com, https://example.com"
        ).allowed_origins
        assert "https://solver.onrender.com" in origins
        assert "https://example.com" in origins


class TestServeBinding:
    def test_the_default_host_stays_loopback(self):
        """Binding 0.0.0.0 by default would expose a laptop's board to the local network."""
        result = subprocess.run(
            [sys.executable, "-c",
             "import app.cli, argparse, inspect; "
             "src = inspect.getsource(app.cli.cmd_serve); "
             "print('127.0.0.1' in src and 'HOST' in src and 'PORT' in src)"],
            capture_output=True, text=True, check=True,
            env={"PYTHONPATH": "backend", "PATH": ""},
        )
        assert result.stdout.strip() == "True"


class TestSchedulerDecision:
    """The in-process schedule, driven by a clock handed to it.

    Only reachable when ENABLE_SCHEDULER is on, which is the fallback for a deployment
    with no separate cron service. It has to be right without anyone watching it.
    """

    HOURS = [16, 20, 23]

    def _at(self, hour, done=None):
        from datetime import datetime, timezone

        from app.scheduler import due_jobs

        return due_jobs(
            datetime(2026, 8, 26, hour, 30, tzinfo=timezone.utc), self.HOURS, done or set()
        )

    def test_nothing_runs_outside_a_scheduled_hour(self):
        jobs, _ = self._at(9)
        assert jobs == []

    def test_a_scheduled_hour_records_the_slate(self):
        jobs, _ = self._at(16)
        assert jobs == ["snapshot"]

    def test_the_last_hour_also_grades(self):
        jobs, _ = self._at(23)
        assert jobs == ["snapshot", "grade"]

    def test_recording_happens_before_grading(self):
        """Grading a slate that was never recorded settles nothing."""
        jobs, _ = self._at(23)
        assert jobs.index("snapshot") < jobs.index("grade")

    def test_an_hour_fires_once_however_often_it_is_ticked(self):
        done = set()
        first, done = self._at(20, done)
        assert first == ["snapshot"]
        for _ in range(60):
            again, done = self._at(20, done)
            assert again == []

    def test_the_same_hour_fires_again_the_next_day(self):
        from datetime import datetime, timezone

        from app.scheduler import due_jobs

        _, done = self._at(20)
        tomorrow = datetime(2026, 8, 27, 20, 5, tzinfo=timezone.utc)
        jobs, _ = due_jobs(tomorrow, self.HOURS, done)
        assert jobs == ["snapshot"]

    def test_yesterdays_marks_do_not_accumulate_forever(self):
        from datetime import datetime, timezone

        from app.scheduler import due_jobs

        stale = {(f"2026-08-{day:02d}", 20) for day in range(1, 26)}
        _, done = due_jobs(
            datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc), self.HOURS, stale
        )
        assert done == {("2026-08-26", 20)}

    def test_a_single_configured_hour_both_records_and_grades(self):
        from datetime import datetime, timezone

        from app.scheduler import due_jobs

        jobs, _ = due_jobs(
            datetime(2026, 8, 26, 23, 0, tzinfo=timezone.utc), [23], set()
        )
        assert jobs == ["snapshot", "grade"]
