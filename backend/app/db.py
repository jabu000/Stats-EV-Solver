"""SQLite engine/session wiring."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_url = _settings.sqlalchemy_url
_is_sqlite = _url.startswith("sqlite")

# SQLite lives on disk next to the repo; make sure the directory exists before the
# engine tries to open it.
if _url.startswith("sqlite:///"):
    Path(_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _url,
    future=True,
    # Managed Postgres closes idle connections without telling the client, so the
    # first query after a quiet spell fails on a dead socket unless it is checked.
    pool_pre_ping=not _is_sqlite,
    pool_recycle=280 if not _is_sqlite else -1,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create tables. Import models first so they register on the metadata."""
    from app import tables  # noqa: F401  (registers mappers)

    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
