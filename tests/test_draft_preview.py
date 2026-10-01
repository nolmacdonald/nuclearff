"""Unit tests for nuclearff.draft.preview (issue #97).

Players use ``skew=0`` (an exact normal weekly distribution) so the expected
roster mean, median and spread are hand-computable.
"""

from __future__ import annotations

import math

import pytest

from nuclearff.config.models import SimulationConfig
from nuclearff.draft.preview import CORRELATION_ASSUMPTION, preview_roster

CFG = SimulationConfig(n_simulations=20_000, games=17)

# 17 games: season mean = 17 * mean_ppg; season sd = sqrt(17) * sd_ppg.
ROSTER = [
    {"player_id": "a", "mean_ppg": 20.0, "sd_ppg": 6.0},
    {"player_id": "b", "mean_ppg": 14.0, "sd_ppg": 5.0},
    {"player_id": "c", "mean_ppg": 10.0, "sd_ppg": 4.0},
]
Z90 = 1.2815515655446004


def test_aggregate_is_consistent_with_the_individual_distributions():
    row = preview_roster(ROSTER, CFG, seed=7).row(0, named=True)

    expected_mean = 17 * (20.0 + 14.0 + 10.0)
    assert row["mean"] == pytest.approx(expected_mean, rel=0.01)
    assert row["median"] == pytest.approx(expected_mean, rel=0.01)
    assert row["floor"] < row["median"] < row["ceiling"]
    assert row["players"] == 3
    assert row["players_unprojected"] == 0
    assert row["assumption"] == CORRELATION_ASSUMPTION


def test_spread_matches_independent_players_not_perfect_correlation():
    row = preview_roster(ROSTER, CFG, seed=7).row(0, named=True)

    independent_sd = math.sqrt(17) * math.sqrt(6.0**2 + 5.0**2 + 4.0**2)
    correlated_sd = math.sqrt(17) * (6.0 + 5.0 + 4.0)
    half_band = (row["ceiling"] - row["floor"]) / 2

    assert half_band == pytest.approx(Z90 * independent_sd, rel=0.03)
    assert half_band < 0.8 * Z90 * correlated_sd


def test_identical_players_do_not_share_a_random_stream():
    twins = [
        {"player_id": "x", "mean_ppg": 15.0, "sd_ppg": 5.0},
        {"player_id": "y", "mean_ppg": 15.0, "sd_ppg": 5.0},
    ]

    row = preview_roster(twins, CFG, seed=3).row(0, named=True)

    half_band = (row["ceiling"] - row["floor"]) / 2
    expected = Z90 * math.sqrt(17) * 5.0 * math.sqrt(2)
    assert half_band == pytest.approx(expected, rel=0.03)


def test_same_seed_is_reproducible_and_different_seeds_differ():
    first = preview_roster(ROSTER, CFG, seed=11)
    again = preview_roster(ROSTER, CFG, seed=11)
    other = preview_roster(ROSTER, CFG, seed=12)

    assert first.equals(again)
    assert not first.equals(other)


def test_unprojected_picks_are_counted_and_left_out():
    roster = [*ROSTER, {"player_id": "d"}, {"player_id": "e", "mean_ppg": 9.0}]

    row = preview_roster(roster, CFG, seed=7).row(0, named=True)

    assert row["players"] == 3
    assert row["players_unprojected"] == 2
    assert row["mean"] == pytest.approx(17 * 44.0, rel=0.01)


def test_skew_shifts_the_distribution_shape_not_the_mean():
    skewed = [{**p, "skew": 4.0} for p in ROSTER]

    row = preview_roster(skewed, CFG, seed=7).row(0, named=True)

    assert row["mean"] == pytest.approx(17 * 44.0, rel=0.01)
    assert row["median"] < row["mean"]


def test_no_projected_players_returns_an_empty_frame():
    assert preview_roster([], CFG).height == 0
    assert preview_roster([{"player_id": "d"}], CFG).height == 0


def test_a_nonpositive_sd_is_rejected():
    with pytest.raises(ValueError, match="sd_ppg"):
        preview_roster([{"player_id": "a", "mean_ppg": 10.0, "sd_ppg": 0.0}], CFG)
