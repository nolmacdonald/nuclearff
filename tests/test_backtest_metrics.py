"""Unit tests for backtest evaluation metrics: rank correlation, error, top-k,
tier accuracy, and calibration.

Every case here is hand-computed (see the assertion comments), never taken
on scipy's/this module's word for it - `tests/conftest.py`'s autouse
`_no_network` fixture would fail any test that tried to reach the network,
but none of these functions touch the network anyway (pure sequence math).
"""

from __future__ import annotations

import math

import pytest

from nuclearff.backtest.metrics import (
    brier_score,
    mae,
    rmse,
    spearman_correlation,
    tier_accuracy,
    top_k_precision_recall,
)


class TestSpearmanCorrelation:
    def test_matches_hand_computation(self):
        """predicted=[1,2,3,4,5] ranks 1..5; actual=[2,1,4,3,5] ranks [2,1,4,3,5].
        d = [-1,1,-1,1,0], sum(d^2) = 4, n = 5 ->
        rho = 1 - 6*4/(5*(25-1)) = 1 - 24/120 = 0.8.
        """
        result = spearman_correlation([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
        assert result == pytest.approx(0.8)

    def test_perfect_agreement_is_one(self):
        result = spearman_correlation([1, 2, 3, 4], [10, 20, 30, 40])
        assert result == pytest.approx(1.0)

    def test_perfect_disagreement_is_negative_one(self):
        result = spearman_correlation([1, 2, 3, 4], [40, 30, 20, 10])
        assert result == pytest.approx(-1.0)

    def test_fewer_than_two_points_returns_nan(self):
        assert math.isnan(spearman_correlation([1.0], [2.0]))
        assert math.isnan(spearman_correlation([], []))

    def test_constant_input_returns_nan_without_warning(self, recwarn):
        """A constant sequence is a degenerate case handled explicitly - no
        ConstantInputWarning should leak out of this function (scipy itself
        emits one if called directly on constant input; this function
        detects the case first and never calls scipy for it).
        """
        result = spearman_correlation([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])
        assert math.isnan(result)
        assert len(recwarn) == 0

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="spearman_correlation"):
            spearman_correlation([1.0, 2.0], [1.0])


class TestMae:
    def test_matches_hand_computation(self):
        """|1-2| + |2-2| + |3-2| = 1 + 0 + 1 = 2, mean = 2/3."""
        result = mae([1.0, 2.0, 3.0], [2.0, 2.0, 2.0])
        assert result == pytest.approx(2 / 3)

    def test_empty_returns_nan(self):
        assert math.isnan(mae([], []))

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="mae"):
            mae([1.0, 2.0], [1.0])


class TestRmse:
    def test_matches_hand_computation(self):
        """squared errors [1, 0, 1], mean = 2/3, sqrt(2/3)."""
        result = rmse([1.0, 2.0, 3.0], [2.0, 2.0, 2.0])
        assert result == pytest.approx(math.sqrt(2 / 3))

    def test_empty_returns_nan(self):
        assert math.isnan(rmse([], []))

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="rmse"):
            rmse([1.0, 2.0], [1.0])


class TestTopKPrecisionRecall:
    def test_matches_known_overlap_count(self):
        """predicted top-3 = {a,b,c}; actual top-3 = {b,z,a}; overlap = {a,b} = 2.
        precision = 2/3, recall = 2/min(3, 5) = 2/3.
        """
        predicted_ids = ["a", "b", "c", "d", "e"]
        actual_ids = ["b", "z", "a", "w", "q"]
        result = top_k_precision_recall(predicted_ids, actual_ids, k=3)

        assert result["precision"] == pytest.approx(2 / 3)
        assert result["recall"] == pytest.approx(2 / 3)

    def test_k_larger_than_lists_clamps_instead_of_raising(self):
        """k=10 against 2- and 3-element lists: slicing clamps to the full
        list rather than erroring. predicted[:10] = {a,b}; actual[:10] =
        {a,b,c}; overlap = {a,b} = 2. precision = 2/10, recall = 2/min(10,3) = 2/3.
        """
        result = top_k_precision_recall(["a", "b"], ["a", "b", "c"], k=10)

        assert result["precision"] == pytest.approx(0.2)
        assert result["recall"] == pytest.approx(2 / 3)

    def test_empty_actual_ids_does_not_divide_by_zero(self):
        result = top_k_precision_recall(["a", "b"], [], k=2)
        assert result["precision"] == pytest.approx(0.0)
        assert result["recall"] == pytest.approx(0.0)

    def test_non_positive_k_raises(self):
        with pytest.raises(ValueError, match="k must be positive"):
            top_k_precision_recall(["a"], ["a"], k=0)


class TestTierAccuracy:
    def test_matches_hand_computation(self):
        """predicted=[1,2,2,3,4], actual=[1,3,2,3,2].
        exact: [T,F,T,T,F] -> 3/5 = 0.6
        within_one: |0|,|1|,|0|,|0|,|2| <=1 -> [T,T,T,T,F] -> 4/5 = 0.8
        """
        result = tier_accuracy([1, 2, 2, 3, 4], [1, 3, 2, 3, 2])

        assert result["exact_match_rate"] == pytest.approx(0.6)
        assert result["within_one_rate"] == pytest.approx(0.8)

    def test_empty_returns_nan(self):
        result = tier_accuracy([], [])
        assert math.isnan(result["exact_match_rate"])
        assert math.isnan(result["within_one_rate"])

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="tier_accuracy"):
            tier_accuracy([1, 2], [1])


class TestBrierScore:
    def test_matches_hand_computation(self):
        """(0.8-1)^2 + (0.2-0)^2 + (0.6-1)^2 = 0.04 + 0.04 + 0.16 = 0.24,
        mean = 0.08.
        """
        result = brier_score([0.8, 0.2, 0.6], [True, False, True])
        assert result == pytest.approx(0.08)

    def test_perfect_predictions_score_zero(self):
        result = brier_score([1.0, 0.0], [True, False])
        assert result == pytest.approx(0.0)

    def test_empty_returns_nan(self):
        assert math.isnan(brier_score([], []))

    def test_probability_above_one_raises(self):
        with pytest.raises(ValueError, match=r"1\.5"):
            brier_score([1.5], [True])

    def test_probability_below_zero_raises(self):
        with pytest.raises(ValueError, match=r"-0\.1"):
            brier_score([-0.1], [False])

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="brier_score"):
            brier_score([0.5, 0.5], [True])
