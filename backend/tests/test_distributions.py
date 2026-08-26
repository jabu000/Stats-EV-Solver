"""The statistical core. If these are wrong, every price on the board is wrong."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from scipy import stats

from app.models.distributions import (
    compound_yardage_distribution, count_summary, log5, negative_binomial_params,
    poisson_binomial_pmf, prob_at_least_one, prob_count_at_least,
    prob_over_from_samples, shrink,
)


class TestNegativeBinomial:
    @pytest.mark.parametrize(("mean", "variance"), [(2.0, 2.5), (6.0, 7.2), (18.0, 25.0)])
    def test_round_trips_mean_and_variance(self, mean, variance):
        n, p = negative_binomial_params(mean, variance)
        dist = stats.nbinom(n, p)
        assert dist.mean() == pytest.approx(mean, rel=1e-6)
        assert dist.var() == pytest.approx(variance, rel=1e-6)

    def test_cannot_be_underdispersed(self):
        """A negative binomial has variance > mean; asking for less must not explode."""
        n, p = negative_binomial_params(5.0, 1.0)
        assert stats.nbinom(n, p).var() >= 5.0

    def test_probability_decreases_as_line_rises(self):
        probs = [prob_count_at_least(6.0, line) for line in (3.5, 4.5, 5.5, 6.5, 7.5)]
        assert all(a > b for a, b in zip(probs, probs[1:]))

    def test_threshold_rounds_up_to_the_next_whole_count(self):
        """A 5.5 line means "6 or more"; 5.1 and 5.9 must resolve the same way."""
        assert prob_count_at_least(6.0, 5.1) == prob_count_at_least(6.0, 5.9)

    def test_summary_quantiles_are_ordered(self):
        summary = count_summary(6.0)
        assert summary.p10 <= summary.p25 <= summary.p50 <= summary.p75 <= summary.p90


class TestPoissonBinomial:
    def test_matches_brute_force_enumeration(self):
        probs = [0.31, 0.28, 0.30, 0.26, 0.19]
        pmf = poisson_binomial_pmf(probs)

        brute = np.zeros(len(probs) + 1)
        for bits in itertools.product([0, 1], repeat=len(probs)):
            joint = np.prod([p if b else 1 - p for p, b in zip(probs, bits)])
            brute[sum(bits)] += joint

        assert np.allclose(pmf, brute, atol=1e-12)

    def test_pmf_sums_to_one(self):
        assert poisson_binomial_pmf([0.2, 0.5, 0.9, 0.05]).sum() == pytest.approx(1.0)

    def test_at_least_one_is_the_complement_of_none(self):
        probs = [0.25, 0.3, 0.28]
        assert prob_at_least_one(probs) == pytest.approx(1 - poisson_binomial_pmf(probs)[0])

    def test_empty_input_is_impossible_not_certain(self):
        assert prob_at_least_one([]) < 1e-5


class TestLog5:
    def test_average_against_average_returns_league_average(self):
        assert log5(0.22, 0.22, 0.22) == pytest.approx(0.22, abs=1e-9)

    def test_two_above_average_inputs_exceed_both(self):
        result = log5(0.32, 0.27, 0.22)
        assert result > 0.32 and result > 0.27

    def test_two_below_average_inputs_fall_below_both(self):
        result = log5(0.15, 0.18, 0.22)
        assert result < 0.15 and result < 0.18

    def test_is_symmetric_in_its_two_rates(self):
        assert log5(0.30, 0.18, 0.22) == pytest.approx(log5(0.18, 0.30, 0.22))

    def test_stays_a_probability_at_the_extremes(self):
        assert 0.0 < log5(0.99, 0.99, 0.05) < 1.0
        assert 0.0 < log5(0.01, 0.01, 0.95) < 1.0


class TestShrinkage:
    def test_no_sample_returns_the_prior(self):
        assert shrink(0.40, 0.22, 0, 250) == pytest.approx(0.22)

    def test_large_sample_approaches_the_observation(self):
        assert shrink(0.40, 0.22, 1_000_000, 250) == pytest.approx(0.40, abs=1e-3)

    def test_is_monotone_in_sample_size(self):
        values = [shrink(0.40, 0.22, n, 250) for n in (0, 50, 200, 800, 5000)]
        assert all(a < b for a, b in zip(values, values[1:]))


class TestCompoundYardage:
    def test_mean_tracks_events_times_yards_per_event(self):
        samples = compound_yardage_distribution(6.0, 1.25, 12.0, 1.0, samples=40000)
        assert samples.mean() == pytest.approx(72.0, rel=0.05)

    def test_is_right_skewed_so_the_median_sits_below_the_mean(self):
        """The whole reason not to use a normal: real yardage has a long right tail."""
        samples = compound_yardage_distribution(5.0, 1.3, 12.0, 1.0, samples=40000)
        assert np.median(samples) < samples.mean()

    def test_skew_shrinks_as_volume_rises(self):
        low = compound_yardage_distribution(2.0, 1.3, 11.0, 1.0, samples=40000)
        high = compound_yardage_distribution(9.0, 1.3, 11.0, 1.0, samples=40000)
        assert np.median(low) / low.mean() < np.median(high) / high.mean()

    def test_never_produces_negative_yardage(self):
        samples = compound_yardage_distribution(4.0, 1.3, 9.0, 1.2, samples=20000)
        assert samples.min() >= 0.0

    def test_probability_decreases_as_line_rises(self):
        samples = compound_yardage_distribution(6.0, 1.25, 12.0, samples=40000)
        probs = [prob_over_from_samples(samples, line) for line in (30.5, 60.5, 90.5, 120.5)]
        assert all(a > b for a, b in zip(probs, probs[1:]))

    def test_zero_inflation_raises_the_chance_of_a_blank(self):
        without = compound_yardage_distribution(5.0, 1.3, 11.0, zero_inflation=0.0, samples=40000)
        with_zeros = compound_yardage_distribution(5.0, 1.3, 11.0, zero_inflation=0.15, samples=40000)
        assert (with_zeros == 0).mean() > (without == 0).mean()

    def test_is_deterministic_for_a_given_seed(self):
        """A refresh must not jitter the board."""
        a = compound_yardage_distribution(5.0, 1.3, 11.0, seed=7, samples=5000)
        b = compound_yardage_distribution(5.0, 1.3, 11.0, seed=7, samples=5000)
        assert np.array_equal(a, b)


def test_all_probabilities_stay_strictly_inside_zero_and_one():
    """Kelly and log-scoring blow up at exactly 0 or 1, so nothing may return them."""
    assert 0.0 < prob_count_at_least(0.001, 20.5) < 1.0
    assert 0.0 < prob_count_at_least(50.0, 0.5) < 1.0
    assert 0.0 < prob_at_least_one([1.0, 1.0, 1.0]) < 1.0
    assert not math.isnan(prob_at_least_one([0.0]))
