"""Provider normalisation and name resolution -- the quiet failure points."""

from __future__ import annotations

import pytest

from app.domain import League, Market, Side
from app.ingest.mapping import Candidate, PlayerResolver, normalize_name, similarity
from app.providers.base import default_variants
from app.providers.market import _spread_from_details
from app.providers.nflverse import aggregate_football_rows
from app.providers.underdog import UnderdogProvider, map_stat_key, parse_manual_import


class TestStatKeyMapping:
    def test_pitcher_strikeout_aliases_all_resolve(self):
        for key in ("strikeouts_thrown", "pitcher_strikeouts", "strikeouts"):
            assert map_stat_key(key, 5.5) is Market.STRIKEOUTS

    def test_one_plus_hit_is_only_recognised_at_the_half_line(self):
        """A 1.5-hit line is a different bet and must not be relabelled '1+ Hit'."""
        assert map_stat_key("hits", 0.5) is Market.HITS_1_PLUS
        assert map_stat_key("hits", 1.5) is None

    def test_anytime_td_is_only_recognised_at_the_half_line(self):
        assert map_stat_key("rush_rec_tds", 0.5) is Market.ANYTIME_TD
        assert map_stat_key("rush_rec_tds", 1.5) is None

    def test_hits_allowed_is_not_a_batter_market(self):
        assert map_stat_key("hits_allowed", 0.5) is None

    def test_unknown_keys_are_ignored_not_guessed(self):
        assert map_stat_key("fantasy_points", 20.5) is None
        assert map_stat_key(None, 1.0) is None


class TestUnderdogNormalisation:
    @pytest.fixture
    def payload(self):
        return {
            "teams": [{"id": "t1", "abbr": "CIN"}, {"id": "t2", "abbr": "BAL"}],
            "games": [{"id": "g1", "home_team_id": "t2", "away_team_id": "t1",
                       "scheduled_at": "2025-11-16T18:00:00Z", "sport_id": "NFL"}],
            "players": [{"id": "p1", "first_name": "Ja'Marr", "last_name": "Chase",
                         "sport_id": "NFL", "position": "WR", "team_id": "t1"}],
            "appearances": [{"id": "a1", "player_id": "p1", "match_id": "g1", "team_id": "t1"}],
            "over_under_lines": [{
                "id": "l1", "stat_value": "72.5", "status": "active",
                "options": [
                    {"id": "o1", "choice": "higher", "payout_multiplier": "1.0"},
                    {"id": "o2", "choice": "lower", "payout_multiplier": "1.0"},
                ],
                "over_under": {"appearance_stat": {"appearance_id": "a1",
                                                   "stat": "receiving_yards"}},
            }],
        }

    def test_stitches_the_cross_referenced_arrays_back_together(self, payload):
        line = UnderdogProvider().normalize(payload)[0]
        assert line.player_name == "Ja'Marr Chase"
        assert line.market is Market.RECEIVING_YARDS
        assert line.stat_line == 72.5
        assert line.team == "CIN" and line.opponent == "BAL"
        assert line.game_label == "CIN @ BAL"
        assert {o.side for o in line.options} == {Side.HIGHER, Side.LOWER}

    def test_reads_the_payout_multiplier(self, payload):
        payload["over_under_lines"][0]["options"][0]["payout_multiplier"] = "1.25"
        line = UnderdogProvider().normalize(payload)[0]
        assert next(o for o in line.options if o.side is Side.HIGHER).payout_multiplier == 1.25

    def test_skips_settled_lines(self, payload):
        payload["over_under_lines"][0]["status"] = "settled"
        assert UnderdogProvider().normalize(payload) == []

    def test_league_filter_is_respected(self, payload):
        assert UnderdogProvider().normalize(payload, League.MLB) == []
        assert len(UnderdogProvider().normalize(payload, League.NFL)) == 1

    def test_a_baseball_stat_on_a_football_player_is_rejected(self, payload):
        payload["over_under_lines"][0]["over_under"]["appearance_stat"]["stat"] = "strikeouts_thrown"
        assert UnderdogProvider().normalize(payload) == []

    def test_malformed_rows_are_skipped_not_fatal(self, payload):
        payload["over_under_lines"].extend([
            {"id": "bad1"},
            {"id": "bad2", "stat_value": "x", "over_under": {}},
            None,
        ])
        assert len(UnderdogProvider().normalize(payload)) == 1

    def test_a_line_with_no_options_is_dropped(self, payload):
        payload["over_under_lines"][0]["options"] = []
        assert UnderdogProvider().normalize(payload) == []


class TestManualImport:
    def test_parses_a_simple_csv(self):
        lines = parse_manual_import(
            "player,market,line,team,opponent\n"
            "Ja'Marr Chase,receiving yards,72.5,CIN,BAL\n"
            "Joe Burrow,passing yards,255.5,CIN,BAL\n",
            League.NFL,
        )
        assert [line.market for line in lines] == [Market.RECEIVING_YARDS, Market.PASSING_YARDS]

    def test_column_order_and_case_do_not_matter(self):
        lines = parse_manual_import("LINE,Player,MARKET\n4.5,Some Guy,receptions\n", League.NFL)
        assert lines[0].player_name == "Some Guy" and lines[0].stat_line == 4.5

    def test_threshold_markets_are_forced_to_the_half_line(self):
        lines = parse_manual_import("player,market,line\nAaron Judge,1+ hit,2\n", League.MLB)
        assert lines[0].stat_line == 0.5 and lines[0].market is Market.HITS_1_PLUS

    def test_accepts_raw_api_json(self):
        assert parse_manual_import("[]", League.NFL) == []

    def test_rows_with_an_unknown_market_are_skipped(self):
        assert parse_manual_import("player,market,line\nX,fantasy points,20.5\n", League.NFL) == []

    def test_a_csv_without_the_required_columns_is_rejected(self):
        from app.providers.base import ProviderError
        with pytest.raises(ProviderError):
            parse_manual_import("foo,bar\n1,2\n", League.NFL)

    def test_empty_input_is_empty_not_an_error(self):
        assert parse_manual_import("   ", League.NFL) == []


class TestPlayerResolution:
    @pytest.fixture
    def resolver(self):
        return PlayerResolver(None, League.NFL, [
            Candidate("1", "Ja'Marr Chase", "CIN", "WR"),
            Candidate("2", "Michael Harris II", "ATL", "CF"),
            Candidate("3", "Justin Jefferson", "MIN", "WR"),
            Candidate("4", "Justin Jackson", "DET", "RB"),
            Candidate("5", "A.J. Brown", "PHI", "WR"),
            Candidate("6", "José Ramírez", "CLE", "3B"),
        ])

    @pytest.mark.parametrize(("source", "expected"), [
        ("JaMarr Chase", "Ja'Marr Chase"),
        ("Ja'Marr Chase", "Ja'Marr Chase"),
        ("Michael Harris", "Michael Harris II"),
        ("AJ Brown", "A.J. Brown"),
        ("A.J. Brown", "A.J. Brown"),
        ("Jose Ramirez", "José Ramírez"),
    ])
    def test_resolves_real_world_spelling_differences(self, resolver, source, expected):
        assert resolver.resolve(source).canonical_name == expected

    def test_refuses_to_guess_at_a_name_it_does_not_know(self, resolver):
        result = resolver.resolve("Completely Different Person")
        assert not result.matched and result.resolved_by == "unmapped"

    def test_similar_names_are_not_confused(self, resolver):
        """The dangerous failure: pricing one player with another's numbers."""
        assert similarity("Justin Jefferson", "Justin Jackson") < 0.88
        assert resolver.resolve("Justin Jefferson").canonical_id == "3"

    def test_team_disambiguates_a_shared_name(self):
        resolver = PlayerResolver(None, League.NFL, [
            Candidate("a", "Josh Allen", "BUF", "QB"),
            Candidate("b", "Josh Allen", "JAX", "LB"),
        ])
        assert resolver.resolve("Josh Allen", "JAX").canonical_id == "b"

    def test_an_ambiguous_name_with_no_team_is_refused(self):
        resolver = PlayerResolver(None, League.NFL, [
            Candidate("a", "Josh Allen", "BUF", "QB"),
            Candidate("b", "Josh Allen", "JAX", "LB"),
        ])
        assert not resolver.resolve("Josh Allen").matched

    def test_normalisation_strips_accents_punctuation_and_suffixes(self):
        assert normalize_name("José Ramírez Jr.") == "jose ramirez"
        assert normalize_name("Ja'Marr  Chase") == "jamarr chase"

    def test_repeated_lookups_are_memoised(self, resolver):
        first = resolver.resolve("JaMarr Chase", "CIN")
        second = resolver.resolve("JaMarr Chase", "CIN")
        assert first is second


class TestNflverseAggregation:
    @pytest.fixture
    def rows(self):
        out = []
        for week in range(1, 9):
            out += [
                {"player_id": "wr1", "player_display_name": "Star", "position": "WR",
                 "recent_team": "CIN", "week": week, "targets": 11, "receptions": 7,
                 "receiving_yards": 95, "receiving_air_yards": 130, "receiving_tds": 1},
                {"player_id": "wr2", "player_display_name": "Other", "position": "WR",
                 "recent_team": "CIN", "week": week, "targets": 5, "receptions": 3,
                 "receiving_yards": 34, "receiving_air_yards": 40},
                {"player_id": "rb1", "player_display_name": "Back", "position": "RB",
                 "recent_team": "CIN", "week": week, "carries": 16, "rushing_yards": 70,
                 "rushing_tds": 1, "targets": 3, "receptions": 2, "receiving_yards": 15},
            ]
        return out

    def test_shares_are_computed_against_team_totals(self, rows):
        profiles, _ = aggregate_football_rows(rows, "NFL")
        assert profiles["wr1"].target_share > profiles["wr2"].target_share
        assert profiles["rb1"].rush_share > 0.7

    def test_air_yards_share_is_regressed_on_targets_not_yardage(self, rows):
        """Using yardage as the sample size would make one deep target look like 40."""
        profiles, _ = aggregate_football_rows(rows, "NFL")
        assert 0.0 < profiles["wr1"].air_yards_share < 1.0

    def test_team_pace_and_pass_rate_are_derived(self, rows):
        _, teams = aggregate_football_rows(rows, "NFL")
        assert teams["CIN"].plays_per_game > 0
        assert 0.0 < teams["CIN"].pass_rate < 1.0

    def test_missing_columns_do_not_crash(self):
        profiles, _ = aggregate_football_rows(
            [{"player_id": "x", "recent_team": "KC", "week": 1}], "NFL"
        )
        assert "x" in profiles

    def test_rows_are_filtered_by_week(self, rows):
        early, _ = aggregate_football_rows(rows, "NFL", through_week=2)
        assert early["wr1"].games == 2


class TestFixtureFallback:
    @pytest.mark.parametrize(("fixture", "expected"), [
        ("schedule_2026-04-01", "schedule_default"),
        ("weekly_2025", "weekly_default"),
        ("hitting_2025_vl", "hitting_default_vl"),
        ("nfl_2026-08-26", "nfl_default"),
    ])
    def test_date_tokens_are_replaced_in_place(self, fixture, expected):
        assert expected in default_variants(fixture)

    def test_a_dateless_name_yields_no_date_candidate(self):
        assert "over_under_default_lines" not in default_variants("over_under_lines")


class TestMarketProvider:
    def test_spread_is_stored_relative_to_the_home_team(self):
        assert _spread_from_details("KC -3.5", "KC") == -3.5
        assert _spread_from_details("KC -3.5", "BUF") == 3.5

    def test_an_unparseable_spread_returns_none(self):
        assert _spread_from_details("EVEN", "KC") is None
        assert _spread_from_details("", "KC") is None
