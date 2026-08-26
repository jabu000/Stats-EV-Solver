"""Payout maths. The break-even number is the difference between a winning and a losing tool."""

from __future__ import annotations

import pytest

from app.domain import Market, Side
from app.pricing.edge import ReferenceEntry, best_side, price_leg, probability_for_side
from app.pricing.entry import (
    PayoutStructure, break_even_probability, find_correlations, kelly_fraction, price_entry,
)
from app.schemas import EntryLeg


def leg(probability: float, bet_id="b", multiplier=1.0, game_id=None, team=None,
        market=Market.RECEPTIONS) -> EntryLeg:
    return EntryLeg(
        bet_id=bet_id, player_name="P", market=market, side=Side.HIGHER,
        stat_line=4.5, probability=probability, payout_multiplier=multiplier,
        game_id=game_id, team=team,
    )


class TestBreakEven:
    @pytest.mark.parametrize("legs", [2, 3, 4, 5])
    def test_standard_matches_the_closed_form(self, legs):
        structure = PayoutStructure()
        expected = structure.standard[legs] ** (-1.0 / legs)
        assert break_even_probability(structure, "standard", legs) == pytest.approx(expected, abs=1e-6)

    def test_is_well_above_a_coin_flip(self):
        """The central point: a 53% leg is not value, it is a slow loss."""
        for legs in (2, 3, 4, 5):
            assert break_even_probability(PayoutStructure(), "standard", legs) > 0.54

    def test_a_payout_boost_lowers_the_bar(self):
        structure = PayoutStructure()
        plain = break_even_probability(structure, "standard", 3, leg_multiplier=1.0)
        boosted = break_even_probability(structure, "standard", 3, leg_multiplier=1.5)
        assert boosted < plain

    def test_insured_entries_solve_by_bisection_and_stay_a_probability(self):
        for legs in (3, 4, 5):
            value = break_even_probability(PayoutStructure(), "insured", legs)
            assert 0.0 < value < 1.0

    def test_break_even_probability_actually_breaks_even(self):
        """Round-trip: pricing an entry of all-break-even legs must return the stake."""
        structure = PayoutStructure()
        for entry_type in ("standard", "insured"):
            for legs in (3, 4, 5):
                p = break_even_probability(structure, entry_type, legs)
                result = price_entry(
                    [leg(p, f"b{i}") for i in range(legs)],
                    structure=structure, entry_type=entry_type, stake=1.0,
                )
                assert result.expected_return == pytest.approx(1.0, abs=1e-3)


class TestEntryPricing:
    def test_matches_a_hand_computed_standard_entry(self):
        result = price_entry([leg(0.60, f"b{i}") for i in range(3)], entry_type="standard", stake=10)
        assert result.expected_return == pytest.approx(10 * 6.0 * 0.6**3, abs=1e-6)

    def test_a_53_percent_leg_is_negative_ev(self):
        result = price_entry([leg(0.53, f"b{i}") for i in range(3)], entry_type="standard", stake=10)
        assert result.ev_percent < 0
        assert result.kelly_stake == 0.0

    def test_payout_branch_probabilities_sum_to_one_for_insured(self):
        result = price_entry([leg(0.60, f"b{i}") for i in range(5)], entry_type="insured", stake=10)
        # Only paying branches are listed, so they sum to less than one, never more.
        assert 0 < sum(o.probability for o in result.payout_table) <= 1.0 + 1e-9

    def test_leg_boosts_multiply_the_entry_payout(self):
        plain = price_entry([leg(0.60, f"b{i}") for i in range(3)], stake=10)
        boosted = price_entry(
            [leg(0.60, "b0", multiplier=1.5)] + [leg(0.60, f"b{i}") for i in range(1, 3)], stake=10
        )
        assert boosted.expected_return == pytest.approx(plain.expected_return * 1.5, rel=1e-9)

    def test_empty_slip_is_handled_not_crashed(self):
        result = price_entry([], stake=10)
        assert result.legs == 0 and result.notes

    def test_unlisted_entry_size_still_prices(self):
        assert price_entry([leg(0.6, f"b{i}") for i in range(7)], stake=10).expected_return > 0


class TestKelly:
    def test_no_edge_means_no_stake(self):
        assert kelly_fraction(1 / 3, 3.0) == pytest.approx(0.0, abs=1e-9)

    def test_negative_edge_never_recommends_a_bet(self):
        assert kelly_fraction(0.20, 3.0) == 0.0

    def test_grows_with_the_edge(self):
        values = [kelly_fraction(p, 3.0) for p in (0.34, 0.40, 0.50, 0.60)]
        assert all(a < b for a, b in zip(values, values[1:]))

    def test_never_exceeds_the_whole_bankroll(self):
        assert kelly_fraction(0.999, 50.0) <= 1.0


class TestLegEdge:
    def test_ev_identity_holds_against_a_simulated_entry(self):
        """EV per dollar is p/b - 1; verify against an actually-priced entry."""
        reference = ReferenceEntry("standard", 3)
        for probability in (0.50, 0.58, 0.65):
            priced = price_leg(probability, 1.0, 1.0, reference)
            legs = [leg(probability, "a")] + [leg(priced.break_even, f"b{i}") for i in range(2)]
            simulated = price_entry(legs, entry_type="standard", stake=1.0).expected_return
            assert 1 + priced.ev_per_dollar == pytest.approx(simulated, abs=1e-3)

    def test_low_confidence_is_shrunk_toward_break_even_when_ranking(self):
        reference = ReferenceEntry("standard", 3)
        confident = price_leg(0.62, 1.0, 1.0, reference, "value")
        shaky = price_leg(0.62, 1.0, 0.2, reference, "value")
        assert shaky.score < confident.score
        assert shaky.edge == confident.edge  # the edge itself is unchanged

    def test_most_likely_demotes_negative_ev_chalk(self):
        reference = ReferenceEntry("standard", 3)
        chalk = price_leg(0.54, 1.0, 0.9, reference, "likely")
        value = price_leg(0.60, 1.0, 0.9, reference, "likely")
        assert chalk.ev_per_dollar < 0 < value.ev_per_dollar
        assert chalk.score < value.score

    def test_side_selection_and_complement(self):
        assert best_side(0.63) is Side.HIGHER
        assert best_side(0.41) is Side.LOWER
        assert probability_for_side(0.41, Side.LOWER) == pytest.approx(0.59)


class TestCorrelation:
    def test_flags_a_quarterback_receiver_stack(self):
        warnings = find_correlations([
            leg(0.55, "q", game_id="g1", team="KC", market=Market.PASSING_YARDS),
            leg(0.55, "w", game_id="g1", team="KC", market=Market.RECEIVING_YARDS),
        ])
        assert any(w.kind == "stack" and w.severity == "warn" for w in warnings)

    def test_flags_opposing_pitcher_and_hitter(self):
        warnings = find_correlations([
            leg(0.55, "p", game_id="g2", team="NYY", market=Market.STRIKEOUTS),
            leg(0.55, "h", game_id="g2", team="BOS", market=Market.HITS_1_PLUS),
        ])
        assert any(w.kind == "opposing" for w in warnings)

    def test_independent_legs_are_not_flagged(self):
        assert find_correlations([
            leg(0.55, "a", game_id="g1"), leg(0.55, "b", game_id="g2"),
        ]) == []
