"""Pure evaluation metrics for the walk-forward backtest harness.

Per the technical plan's "B.10 Backtesting and model selection", a projection
model earns its weights by beating simple baselines on a fixed battery of
metrics, not by intuition: **Spearman rank correlation**, MAE/RMSE on the raw
value, top-k precision/recall, tier accuracy, and calibration (Brier score)
for a `P(top-12)`-style probability.

Every function here takes plain ``predicted``/``actual`` sequences (or, for
:func:`top_k_precision_recall`, ranked id sequences) and returns a score -
none of them touch a DataFrame, a season, or a model.
:mod:`nuclearff.backtest.walkforward` is the layer that turns a Polars frame
of predictions/actuals into these sequences and calls into this module;
keeping the two separate means these scoring functions are testable against
tiny hand-computed cases with zero DataFrame machinery involved.

Degenerate input (live-verified against scipy 1.17.1)
--------------------------------------------------------
``scipy.stats.spearmanr`` already returns ``nan`` for both a single-point
input and a constant-valued input (rather than raising), but it also emits a
runtime ``ConstantInputWarning`` for the constant case that this project does
not want leaking into normal logs. :func:`spearman_correlation` therefore
detects both degenerate cases itself and returns ``float("nan")`` directly,
without ever calling into scipy for them - explicit and warning-free, not
just "whatever scipy happens to do."
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def _require_same_length(predicted: Sequence, actual: Sequence, fn_name: str) -> None:
    """Fail early and clearly if two paired sequences do not line up.

    Args:
        predicted: The predicted-value sequence.
        actual: The actual-value sequence.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If ``predicted`` and ``actual`` differ in length.
    """
    if len(predicted) != len(actual):
        raise ValueError(
            f"{fn_name}: predicted and actual must be the same length, got "
            f"{len(predicted)} and {len(actual)}."
        )


def _safe_ratio(numerator: int, denominator: int) -> float:
    """``numerator / denominator``, or ``0.0`` rather than a divide error.

    Args:
        numerator: Ratio numerator.
        denominator: Ratio denominator.

    Returns:
        The ratio, or ``0.0`` if ``denominator`` is not positive (there is
        nothing to score, so there is nothing to credit or penalize).
    """
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def spearman_correlation(predicted: Sequence[float], actual: Sequence[float]) -> float:
    """Spearman rank correlation between ``predicted`` and ``actual``.

    Degenerate cases return ``float("nan")`` rather than raising or trusting
    a nonsensical value - see the module docstring for what was live-verified
    against scipy 1.17.1's own behavior in each case:

    - Fewer than 2 points: no rank correlation is defined. scipy itself
      already returns ``nan`` here (confirmed live), but this function checks
      explicitly rather than depending on that.
    - Either sequence is constant (every value identical): rank correlation
      is undefined (zero variance in the ranks). scipy returns ``nan`` here
      too, but also emits a ``ConstantInputWarning`` - this function detects
      the constant case itself and returns ``nan`` directly, without calling
      scipy, so no warning is ever emitted for an expected, handled case.

    Args:
        predicted: Predicted values.
        actual: Actual values, same length and player order as ``predicted``.

    Returns:
        The Spearman correlation coefficient in ``[-1.0, 1.0]``, or
        ``float("nan")`` if fewer than 2 points are given or either sequence
        is constant.

    Raises:
        ValueError: If ``predicted`` and ``actual`` differ in length.
    """
    _require_same_length(predicted, actual, "spearman_correlation")

    if len(predicted) < 2:
        return float("nan")
    if len(set(predicted)) == 1 or len(set(actual)) == 1:
        return float("nan")

    statistic, _ = spearmanr(predicted, actual)
    return float(statistic)


def mae(predicted: Sequence[float], actual: Sequence[float]) -> float:
    """Mean absolute error between ``predicted`` and ``actual``.

    Args:
        predicted: Predicted values.
        actual: Actual values, same length and player order as ``predicted``.

    Returns:
        ``mean(abs(predicted_i - actual_i))``, or ``float("nan")`` if both
        sequences are empty.

    Raises:
        ValueError: If ``predicted`` and ``actual`` differ in length.
    """
    _require_same_length(predicted, actual, "mae")

    if not predicted:
        return float("nan")

    errors = [abs(p - a) for p, a in zip(predicted, actual, strict=True)]
    return sum(errors) / len(errors)


def rmse(predicted: Sequence[float], actual: Sequence[float]) -> float:
    """Root mean squared error between ``predicted`` and ``actual``.

    Args:
        predicted: Predicted values.
        actual: Actual values, same length and player order as ``predicted``.

    Returns:
        ``sqrt(mean((predicted_i - actual_i) ** 2))``, or ``float("nan")`` if
        both sequences are empty.

    Raises:
        ValueError: If ``predicted`` and ``actual`` differ in length.
    """
    _require_same_length(predicted, actual, "rmse")

    if not predicted:
        return float("nan")

    squared_errors = [(p - a) ** 2 for p, a in zip(predicted, actual, strict=True)]
    return math.sqrt(sum(squared_errors) / len(squared_errors))


def top_k_precision_recall(
    predicted_ids: Sequence[str], actual_ids: Sequence[str], k: int
) -> dict[str, float]:
    """Precision/recall of the top-``k`` predicted ids against the top-``k`` actual ids.

    ``predicted_ids``/``actual_ids`` are assumed already ranked best-first by
    predicted vs. actual value - this function only slices the first ``k`` of
    each and compares the resulting sets. It does no ranking of its own.

    ``k`` larger than either list is handled gracefully rather than raising:
    Python's own slicing already clamps ``predicted_ids[:k]``/``actual_ids[:k]``
    to the full list when ``k`` exceeds its length, so a caller passing e.g.
    ``top_k_precision_recall(ids, ids, k=1000)`` against a 20-player list
    simply compares the two full 20-player lists rather than erroring.

    Args:
        predicted_ids: Player ids ranked best-first by predicted value.
        actual_ids: Player ids ranked best-first by actual value.
        k: Size of the top slice to compare. Must be positive.

    Returns:
        ``{"precision": overlap / k, "recall": overlap / min(k, len(actual_ids))}``
        where ``overlap`` is the size of the intersection of
        ``predicted_ids[:k]`` and ``actual_ids[:k]``. Either ratio is ``0.0``
        (not a divide error) if its denominator is not positive - e.g.
        ``actual_ids`` is empty.

    Raises:
        ValueError: If ``k`` is not positive.
    """
    if k <= 0:
        raise ValueError(f"top_k_precision_recall: k must be positive, got {k}")

    top_predicted = set(predicted_ids[:k])
    top_actual = set(actual_ids[:k])
    overlap = len(top_predicted & top_actual)

    return {
        "precision": _safe_ratio(overlap, k),
        "recall": _safe_ratio(overlap, min(k, len(actual_ids))),
    }


def tier_accuracy(
    predicted_tiers: Sequence[int], actual_tiers: Sequence[int]
) -> dict[str, float]:
    """Fraction of players whose predicted tier matches (or nearly matches) actual.

    ``predicted_tiers``/``actual_tiers`` are same-length sequences of one
    tier per player, in the same player order (a shared, implicit id order -
    this function itself is id-agnostic and only compares positions).

    Args:
        predicted_tiers: Predicted tier per player.
        actual_tiers: Actual tier per player, same order as ``predicted_tiers``.

    Returns:
        A dict with two keys: ``exact_match_rate`` (fraction of players where
        the predicted and actual tier match exactly) and ``within_one_rate``
        (fraction where they differ by at most 1). Both are ``float("nan")``
        if the sequences are empty.

    Raises:
        ValueError: If ``predicted_tiers`` and ``actual_tiers`` differ in
            length.
    """
    _require_same_length(predicted_tiers, actual_tiers, "tier_accuracy")

    n = len(predicted_tiers)
    if n == 0:
        return {"exact_match_rate": float("nan"), "within_one_rate": float("nan")}

    exact = 0
    within_one = 0
    for predicted, actual in zip(predicted_tiers, actual_tiers, strict=True):
        if predicted == actual:
            exact += 1
        if abs(predicted - actual) <= 1:
            within_one += 1

    return {"exact_match_rate": exact / n, "within_one_rate": within_one / n}


def brier_score(
    predicted_probabilities: Sequence[float], actual_outcomes: Sequence[bool]
) -> float:
    """Brier score: mean squared error of a predicted probability vs. a binary outcome.

    Intended for calibrating a predicted ``P(top-12)``-style probability
    against whether a player actually finished top-12, per the plan's "B.10
    Backtesting and model selection".

    Every probability is validated to lie in ``[0, 1]`` before scoring - a
    probability outside that range means the caller has a bug upstream (e.g.
    passing a raw score instead of a probability), and computing a score
    against it would be meaningless rather than merely imprecise.

    Args:
        predicted_probabilities: Predicted ``P(outcome)`` per player, each in
            ``[0, 1]``.
        actual_outcomes: Whether the outcome actually happened per player,
            same order as ``predicted_probabilities``.

    Returns:
        ``mean((p_i - outcome_i) ** 2)`` over the sequence, or
        ``float("nan")`` if both sequences are empty.

    Raises:
        ValueError: If ``predicted_probabilities`` and ``actual_outcomes``
            differ in length, or any probability is outside ``[0, 1]``.
    """
    _require_same_length(predicted_probabilities, actual_outcomes, "brier_score")

    for probability in predicted_probabilities:
        if not 0.0 <= probability <= 1.0:
            raise ValueError(
                f"brier_score: predicted probability {probability!r} is "
                f"outside [0, 1] - this means the caller has a bug upstream "
                f"(e.g. passing a raw score instead of a probability)."
            )

    if not predicted_probabilities:
        return float("nan")

    squared_errors = [
        (probability - float(outcome)) ** 2
        for probability, outcome in zip(
            predicted_probabilities, actual_outcomes, strict=True
        )
    ]
    return sum(squared_errors) / len(squared_errors)
