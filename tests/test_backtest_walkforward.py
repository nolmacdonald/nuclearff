"""Unit tests for walk-forward fold generation, baselines, and orchestration.

Every fixture here is a small, hand-built synthetic Polars DataFrame -
`tests/conftest.py`'s autouse `_no_network` fixture would fail any test that
tried to reach the network, and none of this module's functions do.
"""

from __future__ import annotations

import math

import polars as pl
import pytest

from nuclearff.backtest import walkforward
from nuclearff.backtest.walkforward import (
    baseline_prior_year,
    baseline_recency_weighted,
    evaluate_fold,
    run_walk_forward_backtest,
    season_folds,
)
from nuclearff.config.models import RecencyWeights


class TestSeasonFolds:
    def test_matches_plan_worked_example(self):
        """The plan's own worked example, verbatim."""
        result = season_folds([2022, 2023, 2024, 2025])

        assert result == [
            ([2022], 2023),
            ([2022, 2023], 2024),
            ([2022, 2023, 2024], 2025),
        ]

    def test_single_season_produces_no_folds(self):
        assert season_folds([2022]) == []

    def test_min_train_seasons_excludes_earliest_folds(self):
        """min_train_seasons=2 drops the first fold (only 1 prior season)."""
        result = season_folds([2022, 2023, 2024, 2025], min_train_seasons=2)

        assert result == [
            ([2022, 2023], 2024),
            ([2022, 2023, 2024], 2025),
        ]

    def test_sorts_and_deduplicates_input(self):
        """Unsorted input with a duplicate season still produces the exact
        same folds as the sorted, deduplicated worked example - the caller
        is not required to pre-sort or pre-dedupe.
        """
        result = season_folds([2025, 2023, 2022, 2024, 2023])

        assert result == [
            ([2022], 2023),
            ([2022, 2023], 2024),
            ([2022, 2023, 2024], 2025),
        ]

    def test_leakage_guard_every_train_season_precedes_test_season(self):
        """The single most important test in this module: for every
        generated fold, across several differently-shaped season lists,
        every training season must be strictly less than the fold's
        test_season. This is meant to catch a violation of the plan's
        explicit leakage-guard requirement if the construction in
        season_folds were ever changed to break it.
        """
        season_lists = [
            [2022, 2023, 2024, 2025],
            [2018, 2019, 2020, 2021, 2022, 2023],
            [2020, 2022, 2025],  # non-consecutive years
            [2023, 2020, 2022],  # unsorted
        ]

        for seasons in season_lists:
            for train_seasons, test_season in season_folds(seasons):
                assert all(train < test_season for train in train_seasons), (
                    f"leakage: a train season >= test_season {test_season} "
                    f"in {train_seasons} (input seasons={seasons})"
                )


def _seasons_df() -> pl.DataFrame:
    """Three seasons, five players, some players missing from some seasons."""
    return pl.DataFrame(
        {
            "player_id": [
                "p1",
                "p2",
                "p3",
                "p4",
                "p5",  # 2022
                "p1",
                "p2",
                "p3",
                "p4",
                "p5",  # 2023
                "p1",
                "p2",
                "p3",  # 2024 - p4, p5 missing (e.g. retired)
            ],
            "season": [2022] * 5 + [2023] * 5 + [2024] * 3,
            "fantasy_points": [
                200.0,
                180.0,
                150.0,
                120.0,
                90.0,
                210.0,
                170.0,
                160.0,
                110.0,
                95.0,
                220.0,
                175.0,
                155.0,
            ],
        }
    )


class TestBaselinePriorYear:
    def test_prediction_equals_prior_season_exactly(self):
        result = baseline_prior_year(
            _seasons_df(), test_season=2023, value_column="fantasy_points"
        )
        predicted = dict(
            zip(result["player_id"], result["fantasy_points_predicted"], strict=True)
        )

        assert predicted == {
            "p1": 200.0,
            "p2": 180.0,
            "p3": 150.0,
            "p4": 120.0,
            "p5": 90.0,
        }

    def test_player_absent_from_prior_season_is_absent_from_output(self):
        """p4 and p5 have no 2024 row, so predicting 2025 from 2024 cannot
        include them.
        """
        result = baseline_prior_year(
            _seasons_df(), test_season=2025, value_column="fantasy_points"
        )

        assert set(result["player_id"]) == {"p1", "p2", "p3"}

    def test_missing_required_column_raises(self):
        broken = _seasons_df().drop("season")
        with pytest.raises(ValueError, match="baseline_prior_year"):
            baseline_prior_year(broken, test_season=2023, value_column="fantasy_points")


class TestBaselineRecencyWeighted:
    def test_delegates_to_recency_weighted_rate(self, monkeypatch):
        """Verifies the real call site - not just that the output happens
        to match what recency_weighted_rate would produce - so this test
        would fail if baseline_recency_weighted were ever changed to
        reimplement the weighting instead of delegating to it.
        """
        original = walkforward.recency_weighted_rate
        calls = []

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(walkforward, "recency_weighted_rate", spy)

        weights = RecencyWeights(weights=(0.5, 0.3, 0.2))
        result = baseline_recency_weighted(
            _seasons_df(),
            test_season=2024,
            value_column="fantasy_points",
            weights=weights,
        )

        assert len(calls) == 1
        args, _kwargs = calls[0]
        assert args[1] == "fantasy_points"
        assert args[2] == 2024
        assert args[3] is weights

        assert "fantasy_points_predicted" in result.columns
        assert "fantasy_points_blended" not in result.columns

    def test_output_matches_direct_recency_weighted_rate_call_renamed(self):
        """The prediction values themselves are exactly
        recency_weighted_rate's own output, just under the renamed column.
        """
        from nuclearff.projection.blend import recency_weighted_rate

        weights = RecencyWeights(weights=(0.5, 0.3, 0.2))
        direct = recency_weighted_rate(
            _seasons_df(), "fantasy_points", 2024, weights
        ).rename({"fantasy_points_blended": "fantasy_points_predicted"})

        via_baseline = baseline_recency_weighted(
            _seasons_df(),
            test_season=2024,
            value_column="fantasy_points",
            weights=weights,
        )

        assert via_baseline.sort("player_id").equals(direct.sort("player_id"))


class TestEvaluateFold:
    @staticmethod
    def _predicted() -> pl.DataFrame:
        return pl.DataFrame(
            {
                "player_id": ["p1", "p2", "p3", "p4", "p5", "p6"],
                "points_predicted": [50.0, 40.0, 30.0, 20.0, 10.0, 5.0],
            }
        )

    @staticmethod
    def _actual() -> pl.DataFrame:
        return pl.DataFrame(
            {
                "player_id": ["p1", "p2", "p3", "p4", "p5", "p7"],
                "points": [45.0, 25.0, 42.0, 22.0, 15.0, 100.0],
            }
        )

    def test_matches_hand_computed_metrics(self):
        """Only p1-p5 overlap (p6 has no actual, p7 has no prediction), so
        n_players_scored = 5, not 6 or 7.

        predicted rank (desc): p1=50,p2=40,p3=30,p4=20,p5=10 -> ranks 5,4,3,2,1
        actual rank (desc):    p1=45,p3=42,p2=25,p4=22,p5=15 -> p1=5,p2=3,p3=4,p4=2,p5=1
        d = [0, 1, -1, 0, 0], sum(d^2) = 2 -> rho = 1 - 6*2/(5*24) = 0.9

        abs errors: |50-45|=5, |40-25|=15, |30-42|=12, |20-22|=2, |10-15|=5
        sum=39, mae=39/5=7.8
        squared errors: 25, 225, 144, 4, 25 -> sum=423, mean=84.6, rmse=sqrt(84.6)

        top_2: predicted top2={p1,p2}; actual top2={p1,p3}; overlap={p1}=1
        precision=1/2=0.5, recall=1/min(2,5)=0.5
        """
        result = evaluate_fold(
            self._predicted(),
            self._actual(),
            predicted_column="points_predicted",
            actual_column="points",
            top_k=2,
        )

        assert result["n_players_scored"] == pytest.approx(5)
        assert result["spearman_correlation"] == pytest.approx(0.9)
        assert result["mae"] == pytest.approx(7.8)
        assert result["rmse"] == pytest.approx(math.sqrt(84.6))
        assert result["top_2_precision"] == pytest.approx(0.5)
        assert result["top_2_recall"] == pytest.approx(0.5)

    def test_unmatched_players_excluded_not_erroring(self):
        """p6 (predicted, no actual) and p7 (actual, no predicted) do not
        appear in the joined set and do not raise.
        """
        result = evaluate_fold(
            self._predicted(),
            self._actual(),
            predicted_column="points_predicted",
            actual_column="points",
        )

        assert result["n_players_scored"] == pytest.approx(5)

    def test_missing_required_column_raises(self):
        with pytest.raises(ValueError, match="evaluate_fold"):
            evaluate_fold(
                self._predicted().drop("points_predicted"),
                self._actual(),
                predicted_column="points_predicted",
                actual_column="points",
            )


class TestRunWalkForwardBacktest:
    def test_end_to_end_plumbing_across_two_models_and_two_folds(self):
        """seasons=[2022,2023,2024] -> 2 folds (test 2023, test 2024).
        2 models -> 4 output rows total, one per (model, fold), with sane
        (non-null, finite) metric values. This does not assert one model
        beats the other - see the task spec: that is a real modeling
        question for later, this only proves the plumbing is correct.
        """
        seasons_df = _seasons_df()

        def mean_model(train_df: pl.DataFrame, test_season: int) -> pl.DataFrame:
            """Trivially predicts every player's training-window mean."""
            mean_value = train_df["fantasy_points"].mean()
            players = train_df["player_id"].unique()
            return pl.DataFrame(
                {
                    "player_id": players,
                    "fantasy_points_predicted": [mean_value] * len(players),
                }
            )

        models = {
            "prior_year": lambda train_df, test_season: baseline_prior_year(
                train_df, test_season, "fantasy_points"
            ),
            "mean": mean_model,
        }

        result = run_walk_forward_backtest(
            seasons_df, [2022, 2023, 2024], "fantasy_points", models, top_k=2
        )

        assert result.height == 4  # 2 folds * 2 models
        assert set(result["model_name"]) == {"prior_year", "mean"}
        assert set(result["test_season"]) == {2023, 2024}
        assert (
            result.filter(
                (pl.col("model_name") == "prior_year") & (pl.col("test_season") == 2023)
            ).height
            == 1
        )

        expected_columns = {
            "model_name",
            "test_season",
            "spearman_correlation",
            "mae",
            "rmse",
            "top_2_precision",
            "top_2_recall",
            "n_players_scored",
        }
        assert expected_columns.issubset(set(result.columns))

        # Every fold here has 3-5 fully overlapping players (never <2), so
        # mae/rmse are always real finite numbers regardless of which model
        # produced the predictions.
        for column in ("mae", "rmse"):
            assert result[column].is_not_nan().all()

        # spearman_correlation is well-defined for "prior_year" (its
        # predictions vary by player), but the trivial "mean" model predicts
        # the *same* value for every player - a constant input, which is
        # correctly nan per backtest.metrics.spearman_correlation's own
        # documented contract, not a plumbing bug.
        prior_year_rows = result.filter(pl.col("model_name") == "prior_year")
        assert prior_year_rows["spearman_correlation"].is_not_nan().all()

        mean_rows = result.filter(pl.col("model_name") == "mean")
        assert mean_rows["spearman_correlation"].is_nan().all()

        assert (result["n_players_scored"] >= 3).all()

    def test_empty_models_produces_no_rows(self):
        result = run_walk_forward_backtest(
            _seasons_df(), [2022, 2023, 2024], "fantasy_points", {}
        )
        assert result.height == 0

    def test_missing_required_column_raises(self):
        broken = _seasons_df().drop("season")
        with pytest.raises(ValueError, match="run_walk_forward_backtest"):
            run_walk_forward_backtest(broken, [2022, 2023], "fantasy_points", {})
