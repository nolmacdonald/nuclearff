"""Unit tests for nuclearff.valuation.tiers.assign_tiers.

The critical thing this suite guards against: k-means cluster labels are
arbitrary integers assigned by scikit-learn, not ordered by value. A naive
implementation that returns raw labels (or ``labels + 1``) would report
tier numbers in whatever order k-means happened to initialize its
centroids - sometimes the highest-value cluster would come back labeled
tier 3, not tier 1. Every test in ``TestOrdering`` below exists to catch
exactly that class of bug.
"""

from __future__ import annotations

import polars as pl

from nuclearff.valuation.tiers import assign_tiers


class TestOrdering:
    """The single easiest thing to get backwards here: tier 1 must always
    be the *highest*-value cluster, never a raw, unordered k-means label.
    """

    def test_highest_values_get_tier_1_not_lowest(self):
        """Three obvious, well-separated clusters; k=3.

        Values are deliberately NOT pre-sorted by cluster, so a correct
        implementation must be doing real value-based label remapping, not
        merely reflecting an already-sorted input back out.
        """
        # cluster ~100s, cluster ~50s, cluster ~10s, interleaved.
        values = [10, 100, 50, 98, 8, 45, 95, 5, 48]
        tiers = assign_tiers(values, k=3)

        assert len(tiers) == len(values)
        by_value = dict(zip(values, tiers, strict=True))

        for v in (100, 98, 95):
            assert by_value[v] == 1, f"value {v} should be tier 1, got {by_value[v]}"
        for v in (50, 48, 45):
            assert by_value[v] == 2, f"value {v} should be tier 2, got {by_value[v]}"
        for v in (10, 8, 5):
            assert by_value[v] == 3, f"value {v} should be tier 3, got {by_value[v]}"

    def test_preserves_input_order(self):
        """Output order matches input order, not sorted or grouped order."""
        values = [100, 98, 95, 50, 48, 45, 10, 8, 5]
        tiers = assign_tiers(values, k=3)

        assert tiers == [1, 1, 1, 2, 2, 2, 3, 3, 3]

    def test_two_clusters_highest_is_tier_1(self):
        """A simpler 2-cluster case, as a second, smaller ordering check."""
        values = [1.0, 2.0, 3.0, 50.0, 51.0, 52.0]
        tiers = assign_tiers(values, k=2)

        assert tiers[:3] == [2, 2, 2]
        assert tiers[3:] == [1, 1, 1]

    def test_accepts_polars_series(self):
        """The declared Sequence[float] | pl.Series input type both work."""
        series = pl.Series("vorp", [100.0, 98.0, 95.0, 10.0, 8.0, 5.0])
        tiers = assign_tiers(series, k=2)

        assert tiers[:3] == [1, 1, 1]
        assert tiers[3:] == [2, 2, 2]


class TestAutoKSelection:
    def test_obviously_clustered_input_finds_three_tiers(self):
        """k=None on 3 clearly-separated clusters should land on k=3, the
        same, correctly-ordered result as passing k=3 explicitly.
        """
        values = [10, 100, 50, 98, 8, 45, 95, 5, 48]
        tiers = assign_tiers(values, max_k=8)

        assert len(set(tiers)) == 3
        assert tiers == assign_tiers(values, k=3)

    def test_max_k_bounds_the_search(self):
        """A max_k of 2 cannot return more than 2 tiers even on 3-cluster data."""
        values = [10, 100, 50, 98, 8, 45, 95, 5, 48]
        tiers = assign_tiers(values, max_k=2)

        assert len(set(tiers)) <= 2


class TestDegenerateInput:
    def test_empty_input_returns_empty_list(self):
        assert assign_tiers([]) == []

    def test_single_value_returns_tier_one(self):
        assert assign_tiers([42.0]) == [1]

    def test_all_identical_values_returns_all_tier_one(self):
        """Fewer than 2 distinct values: nothing for k-means to separate."""
        tiers = assign_tiers([7.0, 7.0, 7.0, 7.0, 7.0])
        assert tiers == [1, 1, 1, 1, 1]

    def test_all_identical_values_does_not_crash_even_with_k_requested(self):
        tiers = assign_tiers([7.0, 7.0, 7.0], k=3)
        assert tiers == [1, 1, 1]

    def test_k_larger_than_number_of_points_does_not_crash(self):
        """Explicit k exceeding len(values) is clamped, not an error."""
        tiers = assign_tiers([5.0, 10.0, 1.0], k=10)

        assert len(tiers) == 3
        assert max(tiers) <= 3
        assert min(tiers) >= 1

    def test_fewer_points_than_smallest_k_tried_degrades_gracefully(self):
        """Two points total is fewer than the smallest k (2) auto-selection
        would try - silhouette needs at least one point per cluster plus
        one held out, so this degrades to a single tier rather than
        raising.
        """
        tiers = assign_tiers([5.0, 10.0])
        assert tiers == [1, 1]

    def test_explicit_k_one_returns_single_tier(self):
        tiers = assign_tiers([5.0, 10.0, 100.0], k=1)
        assert tiers == [1, 1, 1]

    def test_explicit_k_zero_or_negative_is_clamped_to_one(self):
        tiers = assign_tiers([5.0, 10.0, 100.0], k=0)
        assert tiers == [1, 1, 1]


class TestReturnShape:
    def test_returns_plain_list_of_int(self):
        tiers = assign_tiers([100.0, 50.0, 10.0], k=3)
        assert isinstance(tiers, list)
        assert all(isinstance(t, int) for t in tiers)

    def test_reproducible_across_calls_with_same_random_state(self):
        values = [10, 100, 50, 98, 8, 45, 95, 5, 48]
        first = assign_tiers(values, k=3, random_state=2026)
        second = assign_tiers(values, k=3, random_state=2026)
        assert first == second
