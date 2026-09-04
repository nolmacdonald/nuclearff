"""Walk-forward fold generation, baseline models, and backtest orchestration.

Per the technical plan's "B.10 Backtesting and model selection" and
recommendation #7: "the proposed 50/25/10/15 weighting [in ``ModelConfig``]
is a sensible hypothesis, but the repository should not enshrine it until
walk-forward tests show it beats simple prior-year/recency baselines." This
module builds the harness that makes that comparison possible - it does
**not** itself decide the final weights. Running this harness against real,
multi-season nflverse data and deciding whether the full model earns its
weights is separate, later work; see ``run_walk_forward_backtest``'s own
docstring for exactly where that boundary sits.

Leakage guard
----------------
The plan explicitly calls for a structural leakage guard: a fold's
``train_seasons`` must never contain ``test_season`` or anything later than
it. :func:`season_folds` makes this true **by construction** rather than by
caller discipline - ``train_seasons`` for a fold is always a strict prefix
(``ordered[:i]``) of the sorted, deduplicated season list, and ``test_season``
is the very next element (``ordered[i]``). Because the list is sorted and
deduplicated first, every element of a prefix is strictly less than the
element immediately following it; there is no code path that can produce a
fold where this does not hold. ``tests/test_backtest_walkforward.py`` asserts
this invariant directly.

Baseline vs. model output shape
-----------------------------------
:func:`baseline_prior_year`, :func:`baseline_recency_weighted`, and any real
projection a caller wires in as a ``models`` entry for
:func:`run_walk_forward_backtest` all share one output convention: a
DataFrame with a ``player_id`` column and a ``<value_column>_predicted``
column, one row per player. This is deliberate - it lets any of them be used
interchangeably as a ``model_fn`` without ``evaluate_fold`` or
``run_walk_forward_backtest`` needing to know which one produced a given
prediction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import polars as pl

from nuclearff.backtest.metrics import (
    mae,
    rmse,
    spearman_correlation,
    top_k_precision_recall,
)
from nuclearff.config.models import RecencyWeights
from nuclearff.projection.blend import recency_weighted_rate

logger = logging.getLogger(__name__)


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing a column this function needs.

    Args:
        df: The DataFrame passed to ``fn_name``.
        required: Column names ``fn_name`` depends on.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: input DataFrame is missing expected column(s) "
            f"{missing!r} (got {df.columns!r})."
        )


def season_folds(
    seasons: list[int], min_train_seasons: int = 1
) -> list[tuple[list[int], int]]:
    """Generate expanding-window (train_seasons, test_season) walk-forward folds.

    Matches the plan's own worked example exactly:
    ``season_folds([2022, 2023, 2024, 2025])`` ->
    ``[([2022], 2023), ([2022, 2023], 2024), ([2022, 2023, 2024], 2025)]``.

    ``seasons`` is sorted and deduplicated first, so the caller does not need
    to pre-sort or pre-dedupe it, and a duplicate season value in the input
    cannot produce a fold where a season appears in both ``train_seasons``
    and as ``test_season``. For each position ``i`` (starting at 1) in that
    sorted, deduplicated list, one fold is produced with
    ``train_seasons = ordered[:i]`` (every earlier season, an *expanding*
    window - not a fixed size) and ``test_season = ordered[i]`` (the very
    next season). This is the leakage guard the plan calls for, true **by
    construction**: because ``ordered`` is sorted ascending with no
    duplicates, every element of ``ordered[:i]`` is strictly less than
    ``ordered[i]`` - there is no way for a generated fold's ``train_seasons``
    to contain a season ``>= test_season``.

    A would-be fold whose training window has fewer than
    ``min_train_seasons`` seasons is skipped entirely (e.g. the very first
    season in the list has no prior season at all to train on, and produces
    no fold for it).

    Args:
        seasons: Available seasons. Need not be pre-sorted or de-duplicated.
        min_train_seasons: Minimum number of training seasons a fold must
            have to be included.

    Returns:
        One ``(train_seasons, test_season)`` tuple per qualifying fold, in
        ascending order of ``test_season``. Empty if ``seasons`` has fewer
        than 2 distinct values, or every possible fold is excluded by
        ``min_train_seasons``.
    """
    ordered = sorted(set(seasons))

    folds: list[tuple[list[int], int]] = []
    for i in range(1, len(ordered)):
        train_seasons = ordered[:i]
        if len(train_seasons) < min_train_seasons:
            continue
        folds.append((train_seasons, ordered[i]))

    return folds


def baseline_prior_year(
    seasons_df: pl.DataFrame, test_season: int, value_column: str
) -> pl.DataFrame:
    """Baseline: predict ``test_season``'s value as exactly the prior season's value.

    The simplest possible baseline - no weighting, no age curve, just "what
    did this player do last year". A player with no row in
    ``test_season - 1`` has no prediction at all: this baseline cannot
    predict a player it has never seen, so that player is simply absent from
    the output rather than given a null or zero prediction.

    Args:
        seasons_df: Multi-season data with at least ``player_id``,
            ``season``, and ``value_column`` columns.
        test_season: The season being predicted.
        value_column: Name of the column to carry forward as the prediction.

    Returns:
        One row per player who has a ``test_season - 1`` row, with
        ``player_id`` and a ``<value_column>_predicted`` column.

    Raises:
        ValueError: If ``seasons_df`` is missing ``player_id``, ``season``,
            or ``value_column``.
    """
    _require_columns(
        seasons_df, ("player_id", "season", value_column), "baseline_prior_year"
    )

    return seasons_df.filter(pl.col("season") == test_season - 1).select(
        "player_id",
        pl.col(value_column).alias(f"{value_column}_predicted"),
    )


def baseline_recency_weighted(
    seasons_df: pl.DataFrame,
    test_season: int,
    value_column: str,
    weights: RecencyWeights,
) -> pl.DataFrame:
    """Baseline: predict ``test_season``'s value via recency-weighted prior seasons.

    Delegates entirely to
    :func:`nuclearff.projection.blend.recency_weighted_rate` (called with
    ``as_of_season=test_season``) rather than reimplementing recency
    weighting a second time - this project already has exactly that logic,
    renormalization over missing seasons and all. The only work done here is
    renaming its ``<value_column>_blended`` output column to
    ``<value_column>_predicted``, so both baseline functions in this module
    share one consistent output column-naming convention regardless of which
    baseline a caller used - see the module docstring.

    Args:
        seasons_df: Multi-season data, passed straight through to
            :func:`~nuclearff.projection.blend.recency_weighted_rate` (see
            its docstring for required columns).
        test_season: The season being predicted.
        value_column: Name of the column to recency-weight.
        weights: Recency weights, newest-season-first.

    Returns:
        One row per player with a qualifying prior season, with
        ``player_id`` and a ``<value_column>_predicted`` column.

    Raises:
        ValueError: If ``seasons_df`` is missing a column
            :func:`~nuclearff.projection.blend.recency_weighted_rate`
            requires.
    """
    blended = recency_weighted_rate(seasons_df, value_column, test_season, weights)
    return blended.rename({f"{value_column}_blended": f"{value_column}_predicted"})


def evaluate_fold(
    predicted: pl.DataFrame,
    actual: pl.DataFrame,
    *,
    id_column: str = "player_id",
    predicted_column: str,
    actual_column: str,
    top_k: int = 12,
) -> dict[str, float]:
    """Score one fold's predictions against real outcomes.

    Joins ``predicted`` and ``actual`` on ``id_column`` - an **inner** join,
    since a player predicted but with no real outcome (or vice versa) cannot
    be scored either way; that player is excluded from the comparison, not
    treated as an error. Both predicted and actual values are ranked
    best-first (descending) from the joined set to build the top-``k``
    id lists that :func:`~nuclearff.backtest.metrics.top_k_precision_recall`
    expects.

    Args:
        predicted: Predictions, with at least ``id_column`` and
            ``predicted_column``. Typically :func:`baseline_prior_year`,
            :func:`baseline_recency_weighted`, or a real model's output.
        actual: Real outcomes, with at least ``id_column`` and
            ``actual_column``.
        id_column: Column both frames are joined on.
        predicted_column: Column in ``predicted`` holding the predicted
            value.
        actual_column: Column in ``actual`` holding the actual value.
        top_k: Size of the top-k slice for
            :func:`~nuclearff.backtest.metrics.top_k_precision_recall`.

    Returns:
        A flat dict merging every
        :mod:`nuclearff.backtest.metrics` result computed against the joined
        predicted/actual values: ``spearman_correlation``, ``mae``, ``rmse``,
        ``top_{top_k}_precision``, ``top_{top_k}_recall`` (namespaced by
        ``top_k`` so multiple calls with different ``top_k`` values, or
        multiple metrics, never collide in one flat dict), plus
        ``n_players_scored`` - the size of the joined set, so a caller can
        tell a fold with only a handful of overlapping players (noisy,
        should be visibly flaggable) from a well-populated one, rather than
        presenting both with the same confidence.

    Raises:
        ValueError: If ``predicted`` is missing ``id_column`` or
            ``predicted_column``, or ``actual`` is missing ``id_column`` or
            ``actual_column``.
    """
    _require_columns(predicted, (id_column, predicted_column), "evaluate_fold")
    _require_columns(actual, (id_column, actual_column), "evaluate_fold")

    joined = predicted.select(id_column, predicted_column).join(
        actual.select(id_column, actual_column), on=id_column, how="inner"
    )

    predicted_values = joined[predicted_column].to_list()
    actual_values = joined[actual_column].to_list()

    predicted_ranked = joined.sort(predicted_column, descending=True)[
        id_column
    ].to_list()
    actual_ranked = joined.sort(actual_column, descending=True)[id_column].to_list()

    top_k_result = top_k_precision_recall(predicted_ranked, actual_ranked, top_k)

    return {
        "spearman_correlation": spearman_correlation(predicted_values, actual_values),
        "mae": mae(predicted_values, actual_values),
        "rmse": rmse(predicted_values, actual_values),
        f"top_{top_k}_precision": top_k_result["precision"],
        f"top_{top_k}_recall": top_k_result["recall"],
        "n_players_scored": float(joined.height),
    }


def run_walk_forward_backtest(
    seasons_df: pl.DataFrame,
    seasons: list[int],
    value_column: str,
    models: dict[str, Callable[[pl.DataFrame, int], pl.DataFrame]],
    *,
    min_train_seasons: int = 1,
    top_k: int = 12,
) -> pl.DataFrame:
    """Orchestrate fold generation, model prediction, and scoring across every fold.

    For each ``(train_seasons, test_season)`` fold from
    :func:`season_folds` (using ``seasons`` and ``min_train_seasons``), and
    for each ``(name, model_fn)`` in ``models``: call
    ``model_fn(seasons_df.filter(season in train_seasons), test_season)`` to
    get that model's predictions, then score them with :func:`evaluate_fold`
    against ``test_season``'s real values from ``seasons_df``. Every
    ``model_fn`` is expected to return a DataFrame with ``player_id`` and
    ``<value_column>_predicted`` columns - the same shape
    :func:`baseline_prior_year` and :func:`baseline_recency_weighted`
    already produce, so either is directly usable as a ``models`` entry, and
    so is a real projection pipeline wrapped in a matching-shaped callable.

    This function is pure - no I/O. Per the plan's "B.10 Backtesting and
    model selection", the resulting table is meant to be saved under
    ``data/artifacts/backtests/`` for inspection and comparison across
    models and folds, but persisting it is a caller's decision, not this
    function's.

    Note: this function does not itself run a real historical backtest or
    make any claim about model quality - it is exercised in this project's
    own tests only against small synthetic data. Running it for real,
    against multiple real nflverse seasons, with the project's actual
    projection pipeline wired in as one of ``models``, and deciding whether
    that model earns its configured weights over these baselines, is
    separate, later work (see the plan's recommendation #7).

    Args:
        seasons_df: Multi-season data with at least ``player_id``,
            ``season``, and ``value_column`` columns - the source of both
            each fold's training slice (passed to each ``model_fn``) and its
            real ``test_season`` actuals.
        seasons: Available seasons, passed to :func:`season_folds`.
        value_column: Name of the value being predicted, e.g.
            ``"fantasy_points"``. Determines both the actuals column read
            from ``seasons_df`` and the ``<value_column>_predicted`` column
            expected from every ``model_fn``.
        models: ``{model_name: model_fn}``. Each ``model_fn`` takes
            (``seasons_df`` filtered to a fold's ``train_seasons``, that
            fold's ``test_season``) and returns a predictions DataFrame.
        min_train_seasons: Passed through to :func:`season_folds`.
        top_k: Passed through to :func:`evaluate_fold`.

    Returns:
        One row per ``(model_name, test_season)`` pair, with every
        :func:`evaluate_fold` metric as a column plus ``model_name`` and
        ``test_season`` columns. Empty (no rows) if ``models`` is empty or
        :func:`season_folds` produces no folds.

    Raises:
        ValueError: If ``seasons_df`` is missing ``player_id``, ``season``,
            or ``value_column``, or a ``model_fn``'s output does not carry
            the columns :func:`evaluate_fold` requires.
    """
    _require_columns(
        seasons_df, ("player_id", "season", value_column), "run_walk_forward_backtest"
    )

    predicted_column = f"{value_column}_predicted"
    rows: list[dict[str, float | str | int]] = []

    for train_seasons, test_season in season_folds(seasons, min_train_seasons):
        train_df = seasons_df.filter(pl.col("season").is_in(train_seasons))
        actual_df = seasons_df.filter(pl.col("season") == test_season).select(
            "player_id", value_column
        )

        for model_name, model_fn in models.items():
            predicted_df = model_fn(train_df, test_season)
            metrics = evaluate_fold(
                predicted_df,
                actual_df,
                predicted_column=predicted_column,
                actual_column=value_column,
                top_k=top_k,
            )
            rows.append(
                {"model_name": model_name, "test_season": test_season, **metrics}
            )

    return pl.DataFrame(rows)
