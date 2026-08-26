"""Shared fixtures. Every test runs against the recorded slates, never the network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, engine  # noqa: E402
from app.domain import Handedness, League, RoofState  # noqa: E402
from app.features.context import (  # noqa: E402
    BatterProfile, FootballGameContext, FootballPlayerProfile, FootballTeamContext,
    MlbGameContext, ParkContext, PitcherProfile, WeatherContext,
)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)


@pytest.fixture
def lineup():
    def build(team="OPP", k_rate=0.222, hits=0.250, hands=None):
        hands = hands or [Handedness.RIGHT] * 9
        return [
            BatterProfile(
                player_key=f"{team}-{i}", name=f"{team} Batter {i}", bats=hand,
                plate_appearances=450, hit_per_pa=hits, k_per_pa=k_rate,
                on_base_pct=0.320, lineup_slot=i + 1, team=team,
            )
            for i, hand in enumerate(hands)
        ]
    return build


@pytest.fixture
def pitcher():
    def build(k_per_bf=0.24, throws=Handedness.RIGHT, innings=5.8, bf=650, team="HOME"):
        return PitcherProfile(
            player_key="P1", name="Test Pitcher", throws=throws, batters_faced=bf,
            k_per_bf=k_per_bf, k_per_bf_vs_lhb=k_per_bf * 0.93,
            k_per_bf_vs_rhb=k_per_bf * 1.08, hit_per_bf=0.225,
            innings_per_start=innings, starts=24, team=team,
        )
    return build


@pytest.fixture
def mlb_game(lineup, pitcher):
    def build(**overrides):
        defaults = dict(
            game_id="G1", home_team="HOME", away_team="AWAY",
            park=ParkContext(name="Test Park"),
            weather=WeatherContext(temperature_f=70, wind_mph=6, source="test"),
            home_pitcher=pitcher(team="HOME"),
            away_pitcher=pitcher(team="AWAY"),
            home_lineup=lineup("HOME"), away_lineup=lineup("AWAY"),
            lineups_confirmed=True,
        )
        defaults.update(overrides)
        return MlbGameContext(**defaults)
    return build


@pytest.fixture
def football_game():
    def build(league=League.NFL, spread=-2.5, total=47.5, **overrides):
        defaults = dict(
            game_id="FG1", league=league, home_team="HOME", away_team="AWAY",
            spread=spread, total=total,
            weather=WeatherContext(temperature_f=62, wind_mph=5, source="test"),
            home=FootballTeamContext(team="HOME", plays_per_game=64, pass_rate=0.58),
            away=FootballTeamContext(team="AWAY", plays_per_game=64, pass_rate=0.57),
        )
        defaults.update(overrides)
        return FootballGameContext(**defaults)
    return build


@pytest.fixture
def receiver():
    return FootballPlayerProfile(
        player_key="WR1", name="Test Receiver", position="WR", team="HOME", games=9,
        target_share=0.24, air_yards_share=0.30, yards_per_target=8.8,
        catch_rate=0.65, redzone_target_share=0.20,
    )


@pytest.fixture
def dome_weather():
    return WeatherContext(roof=RoofState.DOME, applies=False, source="indoors")
