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

    @property
    def is_live(self) -> bool:
        return self.data_mode is DataMode.LIVE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
