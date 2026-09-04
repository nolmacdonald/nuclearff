"""Unit tests for expected-TD opportunity modeling and TD-luck regression.

Every fixture here is a small, hand-built synthetic Polars DataFrame with
realistic column names/dtypes (matching the shapes documented in
`nuclearff.nflverse.stats`), never live network - `tests/conftest.py`'s
autouse `_no_network` fixture would fail any test that tried.

`load_ff_opportunity`'s own thin-delegation test lives here too (rather than
in `tests/test_nflverse_stats.py`), matching this issue's file scope.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

import nuclearff.nflverse.stats as stats
from nuclearff.metrics.touchdowns import expected_tds


def _weekly_receiving_df() -> pl.DataFrame:
    """Two players, one week each - shaped like `load_weekly_receiving` output."""
    return pl.DataFrame(
        {
            "player_id": ["p_over", "p_under"],
            "season": [2024, 2024],
            "week": [1, 1],
            "receiving_tds": [5, 2],
        }
    )


def _weekly_opportunity_df() -> pl.DataFrame:
    """Matching opportunity rows - `season` String, `week` Float64 (live-confirmed
    `load_ff_opportunity` dtype quirks), keyed by the same gsis_id-format
    `player_id`.
    """
    return pl.DataFrame(
        {
            "player_id": ["p_over", "p_under"],
            "season": ["2024", "2024"],
            "week": [1.0, 1.0],
            "rec_touchdown_exp": [3.0, 5.0],
        }
    )


class TestSignConvention:
    """Pin down the one thing this module is easiest to get backwards."""

    def test_overperformance_yields_positive_td_regression(self):
        """5 actual TDs vs 3.0 expected -> td_regression = +2.0 (TD-lucky,
        a negative-regression risk).
        """
        result = expected_tds(_weekly_receiving_df(), _weekly_opportunity_df())
        row = result.filter(pl.col("player_id") == "p_over").row(0, named=True)

        assert row["expected_tds"] == pytest.approx(3.0)
        assert row["td_regression"] == pytest.approx(2.0)

    def test_underperformance_yields_negative_td_regression(self):
        """2 actual TDs vs 5.0 expected -> td_regression = -3.0 (TD-unlucky,
        a positive-regression buy candidate, per the plan's Jefferson
        example).
        """
        result = expected_tds(_weekly_receiving_df(), _weekly_opportunity_df())
        row = result.filter(pl.col("player_id") == "p_under").row(0, named=True)

        assert row["expected_tds"] == pytest.approx(5.0)
        assert row["td_regression"] == pytest.approx(-3.0)


class TestSeasonalGrainSumsWeeklyOpportunity:
    def test_sums_weekly_expected_tds_to_a_season_total(self):
        """A seasonal `df` joined against weekly opportunity data must sum
        the weekly rec_touchdown_exp values to a season total, not use one
        week's value and not average them. Weekly values (0.1, 0.2, 0.9) are
        chosen so sum (1.2), average (0.4), and "last week's value" (0.9)
        are all different numbers - only the sum is correct.
        """
        seasonal_df = pl.DataFrame(
            {
                "player_id": ["p1"],
                "season": [2024],
                "receiving_tds": [4],
            }
        )
        multi_week_opportunity = pl.DataFrame(
            {
                "player_id": ["p1", "p1", "p1"],
                "season": ["2024", "2024", "2024"],
                "week": [1.0, 2.0, 3.0],
                "rec_touchdown_exp": [0.1, 0.2, 0.9],
            }
        )

        result = expected_tds(seasonal_df, multi_week_opportunity)
        row = result.row(0, named=True)

        assert row["expected_tds"] == pytest.approx(1.2)
        assert row["td_regression"] == pytest.approx(4 - 1.2)

    def test_seasonal_join_does_not_require_a_week_column_in_df(self):
        """A seasonal df has no 'week' column at all, even though opportunity
        (always weekly-grain) does - confirm this is not mistaken for a
        missing-required-column error on df's side.
        """
        seasonal_df = pl.DataFrame(
            {
                "player_id": ["p_over"],
                "season": [2024],
                "receiving_tds": [4],
            }
        )
        assert "week" not in seasonal_df.columns

        result = expected_tds(seasonal_df, _weekly_opportunity_df())
        assert "expected_tds" in result.columns
        assert result.row(0, named=True)["expected_tds"] == pytest.approx(3.0)


class TestMissingOpportunityData:
    def test_missing_player_gets_null_not_dropped_and_is_logged(self, caplog):
        """A player in df with no matching opportunity row gets null
        expected_tds/td_regression, stays in the output, and is logged.
        """
        df = pl.DataFrame(
            {
                "player_id": ["p_matched", "p_missing"],
                "season": [2024, 2024],
                "week": [1, 1],
                "receiving_tds": [5, 3],
            }
        )
        opportunity = pl.DataFrame(
            {
                "player_id": ["p_matched"],
                "season": ["2024"],
                "week": [1.0],
                "rec_touchdown_exp": [3.0],
            }
        )

        with caplog.at_level(logging.WARNING, logger="nuclearff.metrics.touchdowns"):
            result = expected_tds(df, opportunity)

        assert result.height == 2
        missing_row = result.filter(pl.col("player_id") == "p_missing").row(
            0, named=True
        )
        assert missing_row["expected_tds"] is None
        assert missing_row["td_regression"] is None
        assert any("expected_tds" in r.message for r in caplog.records)

    def test_no_missing_rows_does_not_warn(self, caplog):
        """No log noise when every row in df finds a match."""
        with caplog.at_level(logging.WARNING, logger="nuclearff.metrics.touchdowns"):
            expected_tds(_weekly_receiving_df(), _weekly_opportunity_df())

        assert caplog.records == []


class TestRequiredColumns:
    def test_missing_required_df_column_raises(self):
        broken = _weekly_receiving_df().drop("receiving_tds")
        with pytest.raises(ValueError, match="expected_tds.*receiving_tds"):
            expected_tds(broken, _weekly_opportunity_df())

    def test_missing_required_opportunity_column_raises(self):
        broken = _weekly_opportunity_df().drop("rec_touchdown_exp")
        with pytest.raises(ValueError, match="expected_tds.*rec_touchdown_exp"):
            expected_tds(_weekly_receiving_df(), broken)

    def test_weekly_df_against_opportunity_missing_week_raises(self):
        """df is weekly-grain but opportunity has no 'week' column at all -
        this must raise rather than silently joining on (season, player_id)
        only and fanning out or mismatching weeks.
        """
        broken = _weekly_opportunity_df().drop("week")
        with pytest.raises(ValueError, match="expected_tds.*week"):
            expected_tds(_weekly_receiving_df(), broken)


def _ff_opportunity_stand_in() -> pl.DataFrame:
    """A stand-in for `nflreadpy.load_ff_opportunity(stat_type="weekly")`,
    shaped like the live-confirmed real schema (season String, week
    Float64, player_id in gsis_id format).
    """
    return pl.DataFrame(
        {
            "season": ["2024"],
            "week": [1.0],
            "game_id": ["2024_01_KC_BAL"],
            "player_id": ["00-0036322"],
            "rec_touchdown": [1.0],
            "rec_touchdown_exp": [0.4],
            "rec_touchdown_diff": [0.6],
        }
    )


class TestLoadFfOpportunity:
    """Thin-delegation tests, matching tests/test_nflverse_loader.py's pattern."""

    def test_delegates_with_weekly_stat_type(self, monkeypatch):
        calls: list[dict] = []
        expected = _ff_opportunity_stand_in()
        monkeypatch.setattr(
            stats.nflreadpy,
            "load_ff_opportunity",
            lambda **kwargs: calls.append(kwargs) or expected,
        )

        result = stats.load_ff_opportunity([2024])

        assert calls == [{"seasons": [2024], "stat_type": "weekly"}]
        assert result is expected

    def test_raises_on_missing_column(self, monkeypatch):
        broken = _ff_opportunity_stand_in().drop("rec_touchdown_exp")
        monkeypatch.setattr(
            stats.nflreadpy, "load_ff_opportunity", lambda **kwargs: broken
        )

        with pytest.raises(ValueError, match="load_ff_opportunity.*rec_touchdown_exp"):
            stats.load_ff_opportunity([2024])
