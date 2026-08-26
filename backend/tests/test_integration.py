"""End-to-end: fixtures in, a ranked and priced board out, through the real API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import MARKETS_BY_LEAGUE, League
from app.grading.grader import build_track_record, grade_from_results
from app.main import app
from app.tables import ProjectionRow

LEAGUES = ["MLB", "NFL", "CFB"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestBoards:
    @pytest.mark.parametrize("league", LEAGUES)
    def test_every_league_produces_a_board(self, client, league):
        board = client.get(f"/api/board/{league}").json()
        assert board["bets"], f"{league} produced no bets"
        assert board["source"] in ("fixture", "live", "cache")

    @pytest.mark.parametrize("league", LEAGUES)
    def test_only_the_tracked_markets_appear(self, client, league):
        allowed = {m.value for m in MARKETS_BY_LEAGUE[League(league)]}
        assert {bet["market"] for bet in client.get(f"/api/board/{league}").json()["bets"]} <= allowed

    def test_bets_are_sorted_by_the_ranking_score(self, client):
        scores = [bet["score"] for bet in client.get("/api/board/NFL").json()["bets"]]
        assert scores == sorted(scores, reverse=True)

    def test_most_likely_mode_leads_with_the_highest_probability(self, client):
        bets = client.get("/api/board/NFL?mode=likely").json()["bets"]
        positive = [b for b in bets if b["ev_per_dollar"] > 0]
        assert positive[0]["calibrated_probability"] == max(
            b["calibrated_probability"] for b in positive
        )

    def test_the_two_modes_order_the_board_differently(self, client):
        value = [b["id"] for b in client.get("/api/board/NFL?mode=value").json()["bets"]]
        likely = [b["id"] for b in client.get("/api/board/NFL?mode=likely").json()["bets"]]
        assert value != likely

    @pytest.mark.parametrize("league", LEAGUES)
    def test_every_bet_is_internally_consistent(self, client, league):
        for bet in client.get(f"/api/board/{league}").json()["bets"]:
            assert 0.0 < bet["calibrated_probability"] < 1.0
            assert 0.0 < bet["break_even_probability"] < 1.0
            assert bet["edge"] == pytest.approx(
                bet["calibrated_probability"] - bet["break_even_probability"], abs=1e-4
            )
            # EV and edge must always agree on sign, or the board contradicts itself.
            assert (bet["edge"] > 0) == (bet["ev_per_dollar"] > 0)
            assert bet["side"] in ("higher", "lower")
            assert bet["factors"], "every bet must explain itself"
            assert bet["factors"][0]["name"] == "Projection"

    @pytest.mark.parametrize("league", LEAGUES)
    def test_the_recommended_side_is_the_one_the_model_favours(self, client, league):
        for bet in client.get(f"/api/board/{league}").json()["bets"]:
            assert bet["model_probability"] >= 0.5, (
                "we should never recommend the side the model thinks is a loser"
            )

    def test_distribution_quantiles_are_ordered(self, client):
        for bet in client.get("/api/board/NFL").json()["bets"]:
            d = bet["distribution"]
            assert d["p10"] <= d["p25"] <= d["p50"] <= d["p75"] <= d["p90"]


class TestFilters:
    def test_team_filter_narrows_to_that_team(self, client):
        board = client.get("/api/board/NFL").json()
        team = board["filters"]["teams"][0]
        for bet in client.get(f"/api/board/NFL?team={team}").json()["bets"]:
            assert team in (bet["team"], bet["opponent"])

    def test_market_filter_narrows_to_that_market(self, client):
        bets = client.get("/api/board/NFL?market=receptions").json()["bets"]
        assert bets and all(bet["market"] == "receptions" for bet in bets)

    def test_min_edge_filter_is_respected(self, client):
        assert all(b["edge"] >= 0.08 for b in client.get("/api/board/NFL?min_edge=0.08").json()["bets"])

    def test_search_matches_on_player_name(self, client):
        bets = client.get("/api/board/NFL?search=chase").json()["bets"]
        assert bets and all("chase" in bet["player_name"].lower() for bet in bets)

    def test_filters_offered_are_filters_that_return_something(self, client):
        board = client.get("/api/board/MLB").json()
        for team in board["filters"]["teams"][:4]:
            assert client.get(f"/api/board/MLB?team={team}").json()["bets"]

    def test_an_impossible_filter_returns_empty_not_an_error(self, client):
        response = client.get("/api/board/NFL?team=NOPE")
        assert response.status_code == 200 and response.json()["bets"] == []


class TestEntries:
    def test_prices_a_slip_built_from_the_board(self, client):
        bets = client.get("/api/board/NFL?limit=3").json()["bets"][:3]
        payload = {
            "legs": [
                {
                    "bet_id": b["id"], "player_name": b["player_name"], "market": b["market"],
                    "side": b["side"], "stat_line": b["stat_line"],
                    "probability": b["calibrated_probability"],
                    "payout_multiplier": b["payout_multiplier"],
                    "game_id": b["game_id"], "team": b["team"],
                }
                for b in bets
            ],
            "entry_type": "standard", "stake": 10,
        }
        result = client.post("/api/entry/ev", json=payload).json()
        assert result["legs"] == 3
        assert result["payout_table"]
        assert result["expected_return"] > 0

    def test_break_even_endpoint_reports_above_a_coin_flip(self, client):
        for legs in (2, 3, 4, 5):
            assert client.get(f"/api/entry/break-even?legs={legs}").json()["break_even"] > 0.54


class TestManualImport:
    def test_a_pasted_csv_is_priced(self, client):
        csv = "player,market,line,team,opponent\nJa'Marr Chase,receiving yards,72.5,CIN,BAL\n"
        board = client.post("/api/board/NFL/import", json={"text": csv}).json()
        assert board["source"] == "import" and board["bets"]

    def test_an_empty_paste_is_rejected_with_a_useful_message(self, client):
        response = client.post("/api/board/NFL/import", json={"text": ""})
        assert response.status_code == 400

    def test_unparseable_input_is_rejected_not_crashed(self, client):
        assert client.post("/api/board/NFL/import", json={"text": "nonsense"}).status_code == 400


class TestSettings:
    def test_secrets_are_never_returned_to_the_browser(self, client):
        client.put("/api/settings", json={"underdog_token": "super-secret"})
        settings = client.get("/api/settings").json()
        assert settings["underdog_token"] == ""
        assert settings["underdog_token_set"] is True

    def test_a_blank_secret_does_not_wipe_the_stored_one(self, client):
        client.put("/api/settings", json={"underdog_token": "keep-me"})
        client.put("/api/settings", json={"underdog_token": "", "bankroll": 500})
        assert client.get("/api/settings").json()["underdog_token_set"] is True

    def test_payout_changes_reprice_the_whole_board(self, client):
        client.put("/api/settings", json={"reference_entry_legs": 2})
        two = client.get("/api/board/NFL?limit=1").json()["bets"][0]["break_even_probability"]
        client.put("/api/settings", json={"reference_entry_legs": 5})
        five = client.get("/api/board/NFL?limit=1").json()["bets"][0]["break_even_probability"]
        assert two != five
        client.put("/api/settings", json={"reference_entry_legs": 3})

    def test_every_provider_reports_its_health(self, client):
        statuses = client.post("/api/settings/test-connections").json()
        assert {s["provider"] for s in statuses} == {
            "underdog", "mlb_statsapi", "nflverse", "cfbd", "weather", "market",
        }
        assert all(s["ok"] for s in statuses), [s for s in statuses if not s["ok"]]


class TestTrackRecord:
    def test_recording_a_slate_is_what_publishes_it_for_grading(self, client, clean_db):
        """Reading a board is not publishing it -- recording is an explicit act."""
        before = client.get("/api/track-record").json()["total_picks"]
        client.get("/api/board/MLB")
        assert client.get("/api/track-record").json()["total_picks"] == before

        client.post("/api/board/MLB/snapshot")
        assert client.get("/api/track-record").json()["total_picks"] > before

    def test_grading_settles_picks_and_scores_them(self, client, clean_db):
        from app.db import session_scope

        client.post("/api/board/MLB/snapshot")
        with session_scope() as session:
            pending = (
                session.query(ProjectionRow)
                .filter(ProjectionRow.graded_at.is_(None))
                .limit(30).all()
            )
            keys = [(row.player_key, row.market, row.stat_line, row.side) for row in pending]

        assert keys, "expected ungraded picks to grade"
        results = {(key, market): line + 5.0 for key, market, line, _ in keys}
        with session_scope() as session:
            graded = grade_from_results(session, results)
            assert graded > 0

            record = build_track_record(session)
            assert record.graded_picks >= graded
            assert 0.0 <= record.hit_rate <= 1.0
            assert 0.0 <= record.brier_score <= 1.0
            assert record.calibration
            assert record.by_market

    def test_a_pick_is_graded_against_its_own_side(self):
        from app.grading.grader import grade_projection

        higher = ProjectionRow(stat_line=5.5, side="higher")
        assert grade_projection(higher, 7.0).won is True
        lower = ProjectionRow(stat_line=5.5, side="lower")
        assert grade_projection(lower, 7.0).won is False
        assert grade_projection(ProjectionRow(stat_line=5.5, side="lower"), 2.0).won is True

    def test_an_exact_tie_is_a_push_not_a_loss(self):
        from app.grading.grader import grade_projection

        row = grade_projection(ProjectionRow(stat_line=6.0, side="higher"), 6.0)
        assert row.push is True and row.won is None


def test_health_endpoint(client):
    assert client.get("/api/health").json()["status"] == "ok"
