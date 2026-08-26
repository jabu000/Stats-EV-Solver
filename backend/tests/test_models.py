"""Model behaviour: the projections must respond correctly to the inputs that matter."""

from __future__ import annotations

import pytest

from app.domain import Handedness, League, Market, RoofState
from app.features.context import (
    FootballPlayerProfile, ParkContext, PitcherProfile, WeatherContext,
)
from app.models.football import project_football
from app.models.mlb_hits import project_hits
from app.models.mlb_strikeouts import platoon_ratio, project_strikeouts


class TestStrikeouts:
    def test_projection_is_realistic_for_an_average_starter(self):
        """League-average arm, average opponent: about five strikeouts."""
        pitcher = PitcherProfile(
            player_key="P", name="Average", throws=Handedness.RIGHT,
            batters_faced=650, k_per_bf=0.222, innings_per_start=5.4, starts=24, team="HOME",
        )
        from app.features.context import BatterProfile, MlbGameContext
        lineup = [
            BatterProfile(f"b{i}", f"B{i}", plate_appearances=450, k_per_pa=0.222,
                          on_base_pct=0.315, lineup_slot=i + 1, team="AWAY")
            for i in range(9)
        ]
        game = MlbGameContext(
            game_id="G", home_team="HOME", away_team="AWAY",
            home_pitcher=pitcher, away_lineup=lineup, lineups_confirmed=True,
        )
        result = project_strikeouts(5.5, pitcher, game)
        assert 4.0 < result.projected_mean < 6.5

    def test_a_high_strikeout_lineup_raises_the_projection(self, pitcher, mlb_game, lineup):
        arm = pitcher(team="HOME")
        low = project_strikeouts(5.5, arm, mlb_game(away_lineup=lineup("AWAY", k_rate=0.17)))
        high = project_strikeouts(5.5, arm, mlb_game(away_lineup=lineup("AWAY", k_rate=0.28)))
        assert high.projected_mean > low.projected_mean

    def test_platoon_advantage_moves_the_projection_the_right_way(self, pitcher, mlb_game, lineup):
        """A right-hander gets more strikeouts against right-handed hitters."""
        arm = pitcher(throws=Handedness.RIGHT, team="HOME")
        vs_righties = project_strikeouts(
            5.5, arm, mlb_game(away_lineup=lineup("AWAY", hands=[Handedness.RIGHT] * 9))
        )
        vs_lefties = project_strikeouts(
            5.5, arm, mlb_game(away_lineup=lineup("AWAY", hands=[Handedness.LEFT] * 9))
        )
        assert vs_righties.projected_mean > vs_lefties.projected_mean

    def test_a_deeper_outing_means_more_strikeouts(self, pitcher, mlb_game):
        short = project_strikeouts(5.5, pitcher(innings=4.2, team="HOME"), mlb_game())
        long = project_strikeouts(5.5, pitcher(innings=6.8, team="HOME"), mlb_game())
        assert long.projected_mean > short.projected_mean

    def test_park_factor_is_applied(self, pitcher, mlb_game):
        arm = pitcher(team="HOME")
        suppressing = project_strikeouts(5.5, arm, mlb_game(park=ParkContext("Coors", k_factor=92)))
        boosting = project_strikeouts(5.5, arm, mlb_game(park=ParkContext("T-Mobile", k_factor=106)))
        assert boosting.projected_mean > suppressing.projected_mean

    def test_a_thin_sample_is_regressed_and_flagged(self, mlb_game):
        rookie = PitcherProfile(
            player_key="R", name="Rookie", throws=Handedness.RIGHT, batters_faced=40,
            k_per_bf=0.45, innings_per_start=4.5, starts=2, team="HOME",
        )
        result = project_strikeouts(4.5, rookie, mlb_game())
        assert result.confidence < 0.7
        assert any("starts of data" in w for w in result.warnings)

    def test_a_missing_lineup_degrades_rather_than_crashing(self, pitcher, mlb_game):
        result = project_strikeouts(
            5.5, pitcher(team="HOME"), mlb_game(away_lineup=[], lineups_confirmed=False)
        )
        assert result.projected_mean > 0
        assert any("Lineup not posted" in w for w in result.warnings)

    def test_every_projection_explains_itself(self, pitcher, mlb_game):
        result = project_strikeouts(5.5, pitcher(team="HOME"), mlb_game())
        names = {factor.name for factor in result.factors}
        assert {"Projection", "Pitcher strikeout rate", "Park", "Projected workload"} <= names

    def test_platoon_splits_are_regressed_not_taken_at_face_value(self):
        """A raw 1.30 split on a modest sample must not survive intact."""
        regressed = platoon_ratio(0.30, 0.23, 600, 600)
        assert 1.0 < regressed < 1.30


class TestHits:
    def test_probability_is_realistic_for_a_league_average_hitter(self, mlb_game):
        from app.features.context import BatterProfile
        batter = BatterProfile(
            "b", "Average", plate_appearances=500, hit_per_pa=0.232,
            on_base_pct=0.315, lineup_slot=5, team="HOME",
        )
        result = project_hits(0.5, batter, mlb_game())
        # A .250-ish hitter with four plate appearances lands near 70% historically.
        assert 0.62 < result.prob_higher < 0.78

    def test_batting_leadoff_beats_batting_ninth(self, mlb_game):
        from app.features.context import BatterProfile
        def batter(slot):
            return BatterProfile("b", "B", plate_appearances=500, hit_per_pa=0.270,
                                 on_base_pct=0.340, lineup_slot=slot, team="HOME")
        assert project_hits(0.5, batter(1), mlb_game()).prob_higher > \
               project_hits(0.5, batter(9), mlb_game()).prob_higher

    def test_a_better_hitter_is_more_likely_to_get_a_hit(self, mlb_game):
        from app.features.context import BatterProfile
        def batter(rate):
            return BatterProfile("b", "B", plate_appearances=500, hit_per_pa=rate,
                                 on_base_pct=0.330, lineup_slot=3, team="HOME")
        assert project_hits(0.5, batter(0.310), mlb_game()).prob_higher > \
               project_hits(0.5, batter(0.190), mlb_game()).prob_higher

    def test_a_hitters_park_helps(self, mlb_game):
        from app.features.context import BatterProfile
        batter = BatterProfile("b", "B", plate_appearances=500, hit_per_pa=0.270,
                               on_base_pct=0.340, lineup_slot=2, team="HOME")
        coors = project_hits(0.5, batter, mlb_game(park=ParkContext("Coors", hit_factor=112)))
        petco = project_hits(0.5, batter, mlb_game(park=ParkContext("Petco", hit_factor=96)))
        assert coors.prob_higher > petco.prob_higher


class TestFootball:
    def test_a_favourite_runs_more_and_an_underdog_throws_more(self, football_game, receiver):
        back = FootballPlayerProfile(
            player_key="RB", name="Back", position="RB", team="HOME", games=9,
            rush_share=0.65, yards_per_carry=4.5,
        )
        favourite = project_football(Market.RUSHING_YARDS, 65.5, back, football_game(spread=-13.5))
        underdog = project_football(Market.RUSHING_YARDS, 65.5, back, football_game(spread=+9.5))
        assert favourite.projected_mean > underdog.projected_mean

        pass_fav = project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(spread=-13.5))
        pass_dog = project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(spread=+9.5))
        assert pass_dog.projected_mean > pass_fav.projected_mean

    def test_high_wind_suppresses_passing_and_lifts_rushing(self, football_game, receiver):
        calm = WeatherContext(temperature_f=45, wind_mph=4, source="test")
        gale = WeatherContext(temperature_f=45, wind_mph=26, source="test")
        assert project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(weather=gale)).projected_mean < \
               project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(weather=calm)).projected_mean

        back = FootballPlayerProfile("RB", "Back", "RB", "HOME", games=9, rush_share=0.6)
        assert project_football(Market.RUSHING_YARDS, 60.5, back, football_game(weather=gale)).projected_mean > \
               project_football(Market.RUSHING_YARDS, 60.5, back, football_game(weather=calm)).projected_mean

    def test_indoors_the_weather_adjustment_is_switched_off_not_neutralised(
        self, football_game, receiver, dome_weather
    ):
        result = project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(weather=dome_weather))
        weather = next(f for f in result.factors if f.name == "Weather")
        assert "Indoors" in weather.detail and weather.direction == "neutral"

    def test_a_higher_game_total_raises_every_projection(self, football_game, receiver):
        assert project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(total=56.5)).projected_mean > \
               project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(total=38.5)).projected_mean

    def test_target_share_drives_receiving_volume(self, football_game):
        def wr(share):
            return FootballPlayerProfile("W", "W", "WR", "HOME", games=9, target_share=share,
                                         yards_per_target=8.5, catch_rate=0.65)
        assert project_football(Market.RECEPTIONS, 4.5, wr(0.30), football_game()).projected_mean > \
               project_football(Market.RECEPTIONS, 4.5, wr(0.10), football_game()).projected_mean

    @pytest.mark.parametrize("market,line", [
        (Market.RECEIVING_YARDS, 70.5), (Market.RUSHING_YARDS, 60.5),
        (Market.PASSING_YARDS, 245.5), (Market.RECEPTIONS, 4.5), (Market.ANYTIME_TD, 0.5),
    ])
    def test_every_market_returns_a_usable_probability(self, football_game, market, line):
        player = FootballPlayerProfile(
            "X", "X", "QB" if market is Market.PASSING_YARDS else "WR", "HOME", games=9,
            target_share=0.22, rush_share=0.5, dropback_share=0.95,
            redzone_target_share=0.2, redzone_rush_share=0.2,
        )
        result = project_football(market, line, player, football_game())
        assert 0.0 < result.prob_higher < 1.0
        assert result.factors and result.factors[0].name == "Projection"

    def test_probability_falls_as_the_line_rises(self, football_game, receiver):
        probs = [
            project_football(Market.RECEIVING_YARDS, line, receiver, football_game()).prob_higher
            for line in (30.5, 55.5, 80.5, 110.5)
        ]
        assert all(a > b for a, b in zip(probs, probs[1:]))

    def test_college_projections_carry_lower_confidence_than_the_nfl(self, football_game, receiver):
        nfl = project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(league=League.NFL))
        cfb = project_football(Market.RECEIVING_YARDS, 70.5, receiver, football_game(league=League.CFB))
        assert cfb.confidence < nfl.confidence

    def test_a_big_college_talent_gap_trims_the_favourite(self, football_game, receiver):
        from app.features.context import FootballTeamContext
        blowout = football_game(
            league=League.CFB, spread=-31,
            home=FootballTeamContext("HOME", rating=28.0),
            away=FootballTeamContext("AWAY", rating=-9.0),
        )
        even = football_game(
            league=League.CFB, spread=-31,
            home=FootballTeamContext("HOME", rating=5.0),
            away=FootballTeamContext("AWAY", rating=4.0),
        )
        assert project_football(Market.RECEIVING_YARDS, 70.5, receiver, blowout).projected_mean < \
               project_football(Market.RECEIVING_YARDS, 70.5, receiver, even).projected_mean
