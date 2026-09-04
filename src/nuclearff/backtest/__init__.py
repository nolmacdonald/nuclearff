"""Walk-forward backtest harness: fold generation, baselines, and evaluation metrics."""

from nuclearff.backtest.metrics import (
    brier_score,
    mae,
    rmse,
    spearman_correlation,
    tier_accuracy,
    top_k_precision_recall,
)
from nuclearff.backtest.walkforward import (
    baseline_prior_year,
    baseline_recency_weighted,
    evaluate_fold,
    run_walk_forward_backtest,
    season_folds,
)

__all__ = [
    "baseline_prior_year",
    "baseline_recency_weighted",
    "brier_score",
    "evaluate_fold",
    "mae",
    "rmse",
    "run_walk_forward_backtest",
    "season_folds",
    "spearman_correlation",
    "tier_accuracy",
    "top_k_precision_recall",
]
