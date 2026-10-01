"""Unit tests for nuclearff.archive.scoring_profile (issue #154)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.archive.scoring_profile import scoring_profile

_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "roster_id": pl.Int64,
    "matchup_id": pl.Int64,
    "points": pl.Float64,
}


def _matchups(rows: list[tuple]) -> pl.DataFrame:
    # (week, roster_id, matchup_id, points) in league L1, season 2025.
    return pl.DataFrame(
        [("L1", 2025, *row) for row in rows], schema=_SCHEMA, orient="row"
    )


STANDINGS = pl.DataFrame(
    [("L1", 1, "Alice"), ("L1", 2, "Bob"), ("L1", 3, "Cara")],
    schema={"league_id": pl.String, "roster_id": pl.Int64, "display_name": pl.String},
    orient="row",
)

# Alice and Bob play each other every week; Cara is on a bye all four weeks.
#   week 1: Alice 120 beats Bob 100 (margin 20)
#   week 2: Alice 80 loses to Bob 95 (margin 15)
#   week 3: Alice 100 ties Bob 100
#   week 4: Alice 130 beats Bob 90 (margin 40)
_FULL_SEASON = _matchups(
    [
        (1, 1, 1, 120.0),
        (1, 2, 1, 100.0),
        (1, 3, None, 90.0),
        (2, 1, 1, 80.0),
        (2, 2, 1, 95.0),
        (2, 3, None, 85.0),
        (3, 1, 1, 100.0),
        (3, 2, 1, 100.0),
        (3, 3, None, 60.0),
        (4, 1, 1, 130.0),
        (4, 2, 1, 90.0),
        (4, 3, None, 75.0),
    ]
)


def _row(frame: pl.DataFrame, manager: str) -> dict:
    return frame.filter(pl.col("manager") == manager).row(0, named=True)


def test_averages_and_margins_for_a_full_season():
    alice = _row(scoring_profile(_FULL_SEASON, STANDINGS), "Alice")

    assert alice["games_played"] == 4
    assert alice["avg_points_for"] == pytest.approx((120 + 80 + 100 + 130) / 4)
    assert alice["avg_points_against"] == pytest.approx((100 + 95 + 100 + 90) / 4)
    # Wins by 20 and 40; one loss by 15; the tie contributes to no margin stat.
    assert alice["avg_margin_won_by"] == pytest.approx(30.0)
    assert alice["avg_margin_lost_by"] == pytest.approx(15.0)
    assert alice["largest_blowout_win"] == 40.0
    assert alice["largest_blowout_loss"] == 15.0
    assert alice["closest_win"] == 20.0
    assert alice["closest_loss"] == 15.0


def test_losing_side_reports_positive_magnitudes():
    bob = _row(scoring_profile(_FULL_SEASON, STANDINGS), "Bob")

    assert bob["avg_margin_won_by"] == pytest.approx(15.0)
    assert bob["avg_margin_lost_by"] == pytest.approx(30.0)
    assert bob["largest_blowout_loss"] == 40.0
    assert bob["closest_loss"] == 20.0


def test_bye_weeks_count_for_points_but_not_margins():
    cara = _row(scoring_profile(_FULL_SEASON, STANDINGS), "Cara")

    assert cara["games_played"] == 4
    assert cara["avg_points_for"] == pytest.approx((90 + 85 + 60 + 75) / 4)
    assert cara["avg_points_against"] is None
    for column in (
        "avg_margin_won_by",
        "avg_margin_lost_by",
        "largest_blowout_win",
        "largest_blowout_loss",
        "closest_win",
        "closest_loss",
    ):
        assert cara[column] is None


def test_ties_are_excluded_from_every_margin_statistic():
    tied_only = _matchups([(1, 1, 1, 100.0), (1, 2, 1, 100.0)])

    alice = _row(scoring_profile(tied_only, STANDINGS), "Alice")

    assert alice["games_played"] == 1
    assert alice["avg_points_for"] == 100.0
    assert alice["avg_points_against"] == 100.0
    assert alice["avg_margin_won_by"] is None
    assert alice["avg_margin_lost_by"] is None


def test_one_week_season_equals_that_weeks_values():
    one_week = _matchups([(1, 1, 1, 120.0), (1, 2, 1, 100.0)])

    alice = _row(scoring_profile(one_week, STANDINGS), "Alice")

    assert (alice["avg_points_for"], alice["avg_margin_won_by"]) == (120.0, 20.0)
    assert alice["largest_blowout_win"] == alice["closest_win"] == 20.0


def test_unplayed_weeks_are_ignored():
    future = _matchups(
        [(1, 1, 1, 120.0), (1, 2, 1, 100.0), (2, 1, 1, 0.0), (2, 2, 1, 0.0)]
    )

    alice = _row(scoring_profile(future, STANDINGS), "Alice")

    assert alice["games_played"] == 1
    assert alice["avg_points_for"] == 120.0


def test_seasons_are_separate_rows_ordered_by_season_then_points():
    later = _FULL_SEASON.with_columns(pl.lit(2026).cast(pl.Int64).alias("season"))
    both = pl.concat([later, _FULL_SEASON])

    result = scoring_profile(both, STANDINGS)

    assert result["season"].to_list() == [2025] * 3 + [2026] * 3
    assert result.filter(pl.col("season") == 2025)["manager"].to_list() == [
        "Alice",
        "Bob",
        "Cara",
    ]


def test_roster_without_a_display_name_is_omitted():
    standings = STANDINGS.with_columns(
        pl.when(pl.col("roster_id") == 2)
        .then(None)
        .otherwise(pl.col("display_name"))
        .alias("display_name")
    )

    result = scoring_profile(_FULL_SEASON, standings)

    assert sorted(result["manager"].to_list()) == ["Alice", "Cara"]


def test_empty_matchups_return_the_empty_schema():
    result = scoring_profile(pl.DataFrame(schema=_SCHEMA), STANDINGS)

    assert result.height == 0
    assert "avg_margin_won_by" in result.columns
