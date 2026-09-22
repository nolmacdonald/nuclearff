"""Unit tests for Monte-Carlo projection distributions.

No network, per `tests/conftest.py`'s autouse `_no_network` fixture — every
sample here comes from `numpy`/`scipy` random draws or hand-built arrays.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import skew as empirical_skew
from scipy.stats import skewnorm

from nuclearff.config.models import SimulationConfig
from nuclearff.simulation.montecarlo import (
    simulate_from_config,
    simulate_player_season,
    summarize_distribution,
)

MEAN_PPG = 12.5
SD_PPG = 4.0


class TestSimulatePlayerSeasonCorrectness:
    """The empirical mean/sd/skew checks the task calls "the single most
    important correctness check" - if the skewnorm mean/sd correction is
    wrong, everything downstream looks plausible and is silently wrong.
    """

    def test_skew_zero_reduces_to_exact_normal_at_weekly_level(self):
        """At skew=0.0, the per-week distribution's empirical mean/sd must
        equal mean_ppg/sd_ppg within a large-sample tolerance.

        Uses games=1 to reconstruct the per-game distribution directly
        (rather than season_total / games, which for games > 1 shrinks the
        empirical standard deviation by sqrt(games) since it is averaging
        `games` independent draws - that would be testing a property of
        summation, not the skewnorm parameterization this test targets).
        With games=1 a "season total" *is* a single weekly draw, so its
        distribution is exactly `skewnorm(a=0, loc=mean_ppg, scale=sd_ppg)`,
        i.e. `norm(mean_ppg, sd_ppg)`.
        """
        samples = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=0.0, games=1, n_simulations=100_000, seed=1
        )

        assert samples.mean() == pytest.approx(MEAN_PPG, abs=0.1)
        assert samples.std(ddof=1) == pytest.approx(SD_PPG, abs=0.1)

    def test_season_total_aggregates_independent_weekly_draws(self):
        """A season total is the sum of `games` independent weekly draws:
        its mean scales by `games` and its sd scales by sqrt(games).
        """
        games = 17
        samples = simulate_player_season(
            MEAN_PPG,
            SD_PPG,
            skew=0.0,
            games=games,
            n_simulations=100_000,
            seed=2,
        )

        assert samples.mean() == pytest.approx(MEAN_PPG * games, abs=0.5)
        assert samples.std(ddof=1) == pytest.approx(SD_PPG * np.sqrt(games), abs=0.5)

    def test_nonzero_skew_shifts_empirical_skewness_in_expected_direction(self):
        """skew=5 (clearly nonzero, right-skewing) must produce a positively
        skewed weekly sample, without needing exact skewness matching. Mean
        and sd should still land close to target - the moment correction
        must hold for nonzero skew too, not just skew=0.
        """
        samples = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=5.0, games=1, n_simulations=100_000, seed=3
        )

        assert samples.mean() == pytest.approx(MEAN_PPG, abs=0.1)
        assert samples.std(ddof=1) == pytest.approx(SD_PPG, abs=0.1)
        assert empirical_skew(samples) > 0.5

    def test_negative_skew_shifts_empirical_skewness_left(self):
        """A negative skew parameter should skew the sample left of zero."""
        samples = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=-5.0, games=1, n_simulations=100_000, seed=4
        )

        assert empirical_skew(samples) < -0.5


class TestSimulatePlayerSeasonValidation:
    def test_zero_sd_raises_value_error(self):
        with pytest.raises(ValueError, match="sd_ppg"):
            simulate_player_season(MEAN_PPG, 0.0, seed=1)

    def test_negative_sd_raises_value_error(self):
        with pytest.raises(ValueError, match="sd_ppg"):
            simulate_player_season(MEAN_PPG, -1.0, seed=1)


class TestSimulatePlayerSeasonDeterminism:
    def test_same_seed_is_byte_identical(self):
        first = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.5, games=17, n_simulations=500, seed=42
        )
        second = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.5, games=17, n_simulations=500, seed=42
        )

        assert np.array_equal(first, second)

    def test_different_seed_differs(self):
        first = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.5, games=17, n_simulations=500, seed=42
        )
        second = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.5, games=17, n_simulations=500, seed=43
        )

        assert not np.array_equal(first, second)

    def test_returns_requested_number_of_simulations(self):
        samples = simulate_player_season(MEAN_PPG, SD_PPG, n_simulations=250, seed=1)
        assert samples.shape == (250,)


class TestSimulatePlayerSeasonMemoization:
    """Issue #151: a real seed is memoized; ``seed=None`` never is."""

    def test_repeated_call_with_a_real_seed_does_not_redraw(self, monkeypatch):
        calls = []
        original_rvs = skewnorm.rvs

        def counting_rvs(*args, **kwargs):
            calls.append(1)
            return original_rvs(*args, **kwargs)

        monkeypatch.setattr(
            "nuclearff.simulation.montecarlo.skewnorm.rvs", counting_rvs
        )

        first = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.0, games=17, n_simulations=50, seed=99
        )
        second = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.0, games=17, n_simulations=50, seed=99
        )

        assert len(calls) == 1  # the second call was a cache hit, not a redraw
        assert np.array_equal(first, second)

    def test_the_returned_array_is_never_a_shared_reference(self):
        """A caller mutating what they got back must not corrupt a future
        cache hit for the same arguments."""
        first = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.0, games=17, n_simulations=50, seed=123
        )
        first[0] = -999999.0

        second = simulate_player_season(
            MEAN_PPG, SD_PPG, skew=1.0, games=17, n_simulations=50, seed=123
        )

        assert second[0] != -999999.0

    def test_seed_none_is_never_cached_and_keeps_drawing_fresh_entropy(
        self, monkeypatch
    ):
        calls = []
        original_rvs = skewnorm.rvs

        def counting_rvs(*args, **kwargs):
            calls.append(1)
            return original_rvs(*args, **kwargs)

        monkeypatch.setattr(
            "nuclearff.simulation.montecarlo.skewnorm.rvs", counting_rvs
        )

        simulate_player_season(MEAN_PPG, SD_PPG, n_simulations=50, seed=None)
        simulate_player_season(MEAN_PPG, SD_PPG, n_simulations=50, seed=None)

        assert len(calls) == 2  # neither call was a cache hit


class TestSummarizeDistribution:
    """Uses a fixed, hand-built array (not simulated) so every percentile is
    known in advance and directly assertable - `np.quantile([1..10], q)`
    under numpy's default linear interpolation.
    """

    def _known_samples(self) -> np.ndarray:
        return np.arange(1.0, 11.0)  # [1, 2, ..., 10]

    def test_floor_median_ceiling_match_hand_computation(self):
        config = SimulationConfig()  # floor_percentile=0.10, ceiling=0.90
        summary = summarize_distribution(self._known_samples(), config)

        assert summary["floor"] == pytest.approx(1.9)
        assert summary["median"] == pytest.approx(5.5)
        assert summary["ceiling"] == pytest.approx(9.1)
        assert summary["mean"] == pytest.approx(5.5)

    def test_custom_percentiles_are_honored(self):
        config = SimulationConfig(floor_percentile=0.25, ceiling_percentile=0.75)
        summary = summarize_distribution(self._known_samples(), config)

        assert summary["floor"] == pytest.approx(
            np.quantile(np.arange(1.0, 11.0), 0.25)
        )
        assert summary["ceiling"] == pytest.approx(
            np.quantile(np.arange(1.0, 11.0), 0.75)
        )

    def test_p_exceeds_threshold_matches_hand_count(self):
        config = SimulationConfig()
        summary = summarize_distribution(
            self._known_samples(), config, top_n_threshold=7.0
        )

        # 7, 8, 9, 10 clear the bar: 4 of 10.
        assert summary["p_exceeds_threshold"] == pytest.approx(0.4)

    def test_threshold_exactly_equal_to_a_sample_counts_as_exceeding(self):
        config = SimulationConfig()
        summary = summarize_distribution(
            self._known_samples(), config, top_n_threshold=10.0
        )

        assert summary["p_exceeds_threshold"] == pytest.approx(0.1)

    def test_omitting_threshold_omits_the_key_entirely(self):
        config = SimulationConfig()
        summary = summarize_distribution(self._known_samples(), config)

        assert "p_exceeds_threshold" not in summary

    def test_threshold_above_every_sample_gives_zero_probability(self):
        config = SimulationConfig()
        summary = summarize_distribution(
            self._known_samples(), config, top_n_threshold=100.0
        )

        assert summary["p_exceeds_threshold"] == pytest.approx(0.0)


class TestSimulateFromConfig:
    def test_matches_direct_simulate_player_season_call(self):
        config = SimulationConfig(n_simulations=500, games=17)

        via_config = simulate_from_config(MEAN_PPG, SD_PPG, 1.5, config, seed=7)
        direct = simulate_player_season(
            MEAN_PPG,
            SD_PPG,
            1.5,
            games=config.games,
            n_simulations=config.n_simulations,
            seed=7,
        )

        assert np.array_equal(via_config, direct)

    def test_uses_configs_games_and_n_simulations(self):
        config = SimulationConfig(n_simulations=321, games=9)
        samples = simulate_from_config(MEAN_PPG, SD_PPG, 0.0, config, seed=1)

        assert samples.shape == (321,)

    def test_propagates_sd_validation_error(self):
        config = SimulationConfig()
        with pytest.raises(ValueError, match="sd_ppg"):
            simulate_from_config(MEAN_PPG, 0.0, 0.0, config, seed=1)
