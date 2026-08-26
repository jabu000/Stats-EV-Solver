"""Cached loaders for the static reference data shipped with the repo."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _load(filename: str) -> dict[str, Any]:
    return json.loads((_DIR / filename).read_text(encoding="utf-8"))


def mlb_parks() -> dict[str, dict]:
    return _load("mlb_parks.json")["parks"]


def mlb_park(team: str | None) -> dict | None:
    """Park for the *home* team of the game."""
    if not team:
        return None
    return mlb_parks().get(team.upper())


def nfl_stadiums() -> dict[str, dict]:
    return _load("nfl_stadiums.json")["stadiums"]


def nfl_stadium(team: str | None) -> dict | None:
    if not team:
        return None
    return nfl_stadiums().get(team.upper())


def priors(league: str) -> dict[str, float]:
    data = _load("priors.json")
    # CFB and NFL share a shape; MLB has its own keys.
    return data.get(league.upper(), data["NFL"])
