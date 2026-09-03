"""Discrete draft tiers from a continuous value column, via k-means clustering.

A VORP/VOLS ranking is continuous, but draft boards are worked in tiers: a
handful of players cluster together in value with a real gap to the next
group, and that gap matters more for in-draft decisions than a one-rank
difference within a cluster. :func:`assign_tiers` groups a value column
(typically :func:`nuclearff.valuation.vorp.vorp`'s ``vorp`` column) into
those clusters with :class:`sklearn.cluster.KMeans`, per the technical
plan's "valuation/tiers.py" sketch ("Tier via k-means (sklearn) ... auto-k
via gap heuristic").

Tier numbering is the opposite of what raw k-means hands back: k-means
cluster *labels* (``0``, ``1``, ``2``, ...) are arbitrary — they encode which
cluster a point was assigned to, not how good that cluster is. This module's
whole job is remapping those labels to ``1..k`` ordered by descending
cluster mean, so **tier 1 is always the highest-value group**. Getting this
backwards (reporting raw labels) is the single easiest way to invert this
module's output, and is explicitly guarded against in
``tests/test_valuation_tiers.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
import polars as pl
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

logger = logging.getLogger(__name__)

_N_INIT = 10
"""KMeans restarts per fit. Fixed (not "auto") so results are stable across
scikit-learn versions that change the "auto" heuristic's default resolution.
"""


def _kmeans_tiers(arr: np.ndarray, k: int, random_state: int) -> list[int]:
    """Fit k-means and remap arbitrary cluster labels to tiers 1..k by value.

    Args:
        arr: Column vector of shape ``(n, 1)`` to cluster.
        k: Number of clusters. ``k <= 1`` short-circuits to a single tier
            without fitting a model.
        random_state: Seed for k-means' centroid initialization.

    Returns:
        Tier numbers, one per row of ``arr``, in ``arr``'s original order.
        Tier 1 is the cluster with the highest mean value.
    """
    n = arr.shape[0]
    if k <= 1:
        return [1] * n

    model = KMeans(n_clusters=k, random_state=random_state, n_init=_N_INIT)
    labels = model.fit_predict(arr)

    # KMeans labels are arbitrary cluster IDs, not ordered by value - rank
    # the labels themselves by each cluster's mean, highest first, then map
    # every point through that ranking. This is the step that must not be
    # skipped: returning `labels` (or `labels + 1`) directly would report
    # tiers in whatever order k-means happened to initialize its centroids.
    cluster_means = {
        int(label): float(arr[labels == label].mean()) for label in set(labels.tolist())
    }
    ordered_labels = sorted(
        cluster_means, key=lambda label: cluster_means[label], reverse=True
    )
    label_to_tier = {label: tier for tier, label in enumerate(ordered_labels, start=1)}

    return [label_to_tier[int(label)] for label in labels.tolist()]


def _select_k(arr: np.ndarray, max_k: int, random_state: int, n_distinct: int) -> int:
    """Choose a cluster count by silhouette score over ``k=2..upper``.

    Silhouette score (mean, over all points, of ``(b - a) / max(a, b)``
    where ``a`` is a point's mean distance to its own cluster and ``b`` its
    mean distance to the nearest other cluster) is bounded in ``[-1, 1]`` and
    directly answers "how well-separated are these clusters", which is
    exactly the question a WR tier boundary is supposed to represent — unlike
    raw inertia, it does not mechanically improve as ``k`` grows, so it does
    not need a separate elbow to read off; the best score is the chosen
    ``k``. Ties keep the smaller ``k`` (the first candidate reaching the
    best score wins), preferring fewer, more decisive tiers.

    ``k`` is only tried over ``2..min(max_k, n - 1, n_distinct)``: silhouette
    is undefined below 2 clusters or above ``n - 1`` clusters, and a ``k``
    beyond the number of distinct values in ``arr`` cannot produce more than
    ``n_distinct`` non-degenerate clusters. If that upper bound is below 2,
    there is no meaningful multi-cluster split to search and ``k=1`` is
    returned directly.

    Args:
        arr: Column vector of shape ``(n, 1)`` to cluster.
        max_k: Largest ``k`` to consider.
        random_state: Seed for k-means' centroid initialization.
        n_distinct: Number of distinct values in ``arr``.

    Returns:
        The chosen number of clusters.
    """
    n = arr.shape[0]
    upper = min(max_k, n - 1, n_distinct)
    if upper < 2:
        return 1

    best_k = 2
    best_score = -1.0
    for candidate_k in range(2, upper + 1):
        model = KMeans(
            n_clusters=candidate_k, random_state=random_state, n_init=_N_INIT
        )
        labels = model.fit_predict(arr)
        if len(set(labels.tolist())) < 2:
            # Degenerate fit (e.g. an empty cluster) - not a usable candidate.
            continue
        score = silhouette_score(arr, labels)
        if score > best_score:
            best_score = score
            best_k = candidate_k

    return best_k


def assign_tiers(
    values: Sequence[float] | pl.Series,
    *,
    k: int | None = None,
    max_k: int = 8,
    random_state: int = 2026,
) -> list[int]:
    """Group ``values`` into discrete tiers, tier 1 = highest value.

    Clusters ``values`` (e.g. a ``vorp`` column) with k-means and remaps the
    resulting clusters so tier numbers increase as value decreases - tier 1
    is always the best cluster, regardless of the arbitrary label k-means
    itself assigned it. See the module docstring for why this remapping is
    the critical step here.

    If ``k`` is given, exactly that many clusters are used (clamped to
    ``[1, len(values)]`` so an oversized ``k`` cannot ask for more clusters
    than there are points). If ``k`` is ``None``, a cluster count is chosen
    automatically by silhouette score over ``k=2..max_k`` - see
    :func:`_select_k`.

    Degenerate input degrades gracefully rather than raising: an empty
    ``values`` returns an empty list; fewer than 2 distinct values (nothing
    for k-means to meaningfully separate) puts every point in tier 1;
    likewise if fewer points are available than the smallest ``k`` under
    consideration.

    Args:
        values: The values to tier, e.g. a ``vorp`` column. Order is
            preserved in the output.
        k: Exact number of tiers to use, or ``None`` to select automatically.
        max_k: Largest cluster count considered during automatic selection.
            Ignored when ``k`` is given.
        random_state: Seed for k-means' centroid initialization, so tiering
            is reproducible run to run.

    Returns:
        Tier numbers, one per element of ``values``, same order. Tier 1 is
        the highest-value group; higher tier numbers are lower value.
    """
    arr = np.asarray(list(values), dtype=float).reshape(-1, 1)
    n = arr.shape[0]

    if n == 0:
        return []

    n_distinct = len({round(v, 12) for v in arr.flatten().tolist()})
    if n_distinct < 2 or n < 2:
        return [1] * n

    if k is not None:
        chosen_k = max(1, min(k, n))
    else:
        chosen_k = _select_k(arr, max_k, random_state, n_distinct)

    return _kmeans_tiers(arr, chosen_k, random_state)
