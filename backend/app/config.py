"""Application configuration.

Everything that varies between "running on the developer's laptop with real network
access" and "running in a sandbox with recorded fixtures" is funnelled through here so
the rest of the codebase never branches on environment directly.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class DataMode(str, Enum):
    """Where providers read their data from."""

    LIVE = "live"
    FIXTURE = "fixture"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ data
    data_mode: DataMode = DataMode.FIXTURE
    fixture_dir: Path = REPO_ROOT / "backend" / "fixtures"
    static_data_dir: Path = REPO_ROOT / "backend" / "app" / "static_data"
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'solver.db'}"

    # --------------------------------------------------------------- secrets
    # Underdog's endpoint is usually readable unauthenticated. When it is not, the user
    # pastes a bearer token in the Settings tab, which is persisted to the DB and
    # overrides this value at request time.
    underdog_token: str = ""
    cfbd_api_key: str = ""

    # ------------------------------------------------------------- behaviour
    http_timeout_seconds: float = 20.0
    http_retries: int = 2
    cache_ttl_seconds: int = 300

    # ------------------------------------------------------------ deployment
    # Only needed when the app is not being served from the same origin as the API.
    # A comma-separated list; the local Vite dev server is always allowed.
    cors_origins: str = ""
    # When set, every request must carry HTTP Basic credentials with this password
    # (any username). Unset means no gate, which is the right default for localhost
    # and the wrong one for a public URL.
    access_password: str = ""
    # Run the snapshot/grade schedule inside the web process. Off by default: on a
    # laptop the launchd/systemd jobs do it, and running two schedulers would double
    # up. Turn it on for a single always-on deployment with no separate cron service.
    enable_scheduler: bool = False
    # UTC hours at which the in-process scheduler records slates. The last one also
    # grades. Comma-separated.
    scheduler_hours_utc: str = "16,20,23"

    @property
    def is_live(self) -> bool:
        return self.data_mode is DataMode.LIVE

    @property
    def sqlalchemy_url(self) -> str:
        """`database_url`, normalised to something SQLAlchemy 2 can actually open.

        Managed Postgres providers -- Render included -- hand out URLs beginning
        `postgres://`, a scheme SQLAlchemy dropped support for. Rewriting it here means
        the connection string can be pasted in exactly as the provider gives it.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def allowed_origins(self) -> list[str]:
        origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
        origins += [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins

    @property
    def scheduler_hours(self) -> list[int]:
        hours = []
        for chunk in self.scheduler_hours_utc.split(","):
            chunk = chunk.strip()
            if chunk.isdigit() and 0 <= int(chunk) <= 23:
                hours.append(int(chunk))
        return sorted(set(hours))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
