"""Unit tests for nuclearff.archive.records: the records book (issue #109)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.archive.records import longest_streak, margin_extremes, score_extremes
from nuclearff.sleeper.wins import weekly_results


def _matchups(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "week": pl.Int64,
        "roster_id": pl.Int64,
        "matchup_id": pl.Int64,
        "points": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def _standings(rows: list[dict]) -> pl.DataFrame:
    schema = {"league_id": pl.String, "roster_id": pl.Int64, "display_name": pl.String}
    return pl.DataFrame(rows, schema=schema)


def _results(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "week": pl.Int64,
        "roster_id": pl.Int64,
        "result": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


STANDINGS = _standings(
    [
        {"league_id": "L1", "roster_id": 1, "display_name": "Alice"},
        {"league_id": "L1", "roster_id": 2, "display_name": "Bob"},
    ]
)


# --- score_extremes ----------------------------------------------------------


def test_score_extremes_finds_highest_and_lowest_with_opponent_context():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 200.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 4,
                "points": 50.0,
            },
        ]
    )

    result = {
        row["kind"]: row
        for row in score_extremes(matchups, STANDINGS).iter_rows(named=True)
    }

    assert result["highest"]["manager"] == "Alice"
    assert result["highest"]["points"] == 200.0
    assert result["highest"]["opponent"] == "Bob"
    assert result["lowest"]["manager"] == "Bob"
    assert result["lowest"]["opponent"] == "Alice"


def test_score_extremes_includes_a_bye_week_with_no_opponent():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": None,
                "points": 300.0,
            },
        ]
    )

    result = score_extremes(matchups, STANDINGS)

    assert result.height == 2
    highest = result.filter(pl.col("kind") == "highest").row(0, named=True)
    assert highest["points"] == 300.0
    assert highest["opponent"] is None


def test_score_extremes_empty_input():
    assert score_extremes(_matchups([]), STANDINGS).height == 0


def test_score_extremes_no_resolvable_manager():
    """A roster with no matching standings row (data inconsistency) contributes
    nothing."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 99,
                "matchup_id": None,
                "points": 100.0,
            },
        ]
    )

    assert score_extremes(matchups, STANDINGS).height == 0


# --- margin_extremes ----------------------------------------------------------


def test_margin_extremes_finds_blowout_and_nailbiter():
    matchups = _matchups(
        [
            # Blowout: 200 - 50 = 150
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 200.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 50.0,
            },
            # Nail-biter: 101 - 100 = 1
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "matchup_id": 2,
                "points": 101.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 2,
                "matchup_id": 2,
                "points": 100.0,
            },
        ]
    )

    result = {
        row["kind"]: row
        for row in margin_extremes(matchups, STANDINGS).iter_rows(named=True)
    }

    assert result["biggest_blowout"]["winner"] == "Alice"
    assert result["biggest_blowout"]["margin"] == pytest.approx(150.0)
    assert result["closest_margin"]["margin"] == pytest.approx(1.0)
    assert result["closest_margin"]["week"] == 2


def test_margin_extremes_a_tie_never_qualifies():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 100.0,
            },
        ]
    )

    assert margin_extremes(matchups, STANDINGS).height == 0


def test_margin_extremes_empty_input():
    assert margin_extremes(_matchups([]), STANDINGS).height == 0


def test_margin_extremes_no_resolvable_manager():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 98,
                "matchup_id": 1,
                "points": 200.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 99,
                "matchup_id": 1,
                "points": 50.0,
            },
        ]
    )

    assert margin_extremes(matchups, STANDINGS).height == 0


# --- longest_streak -----------------------------------------------------------


def test_longest_streak_rejects_an_invalid_streak_type():
    with pytest.raises(ValueError, match="streak_type"):
        longest_streak(_results([]), STANDINGS, "draw")


def test_longest_streak_finds_the_longest_run():
    results = _results(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 3,
                "roster_id": 1,
                "result": "loss",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 4,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 5,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 6,
                "roster_id": 1,
                "result": "win",
            },
        ]
    )

    streaks = longest_streak(results, STANDINGS, "win")

    assert streaks.height == 1
    row = streaks.row(0, named=True)
    assert row["manager"] == "Alice"
    assert row["length"] == 3
    assert row["start_week"] == 4
    assert row["end_week"] == 6


def test_longest_streak_a_tie_breaks_the_streak():
    """A tie is neither a win nor a loss -- it must interrupt a win streak."""
    results = _results(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 3,
                "roster_id": 1,
                "result": "tie",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 4,
                "roster_id": 1,
                "result": "win",
            },
        ]
    )

    streaks = longest_streak(results, STANDINGS, "win")

    assert streaks.row(0, named=True)["length"] == 2


def test_longest_streak_ties_between_equal_runs_keep_the_earliest():
    results = _results(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 3,
                "roster_id": 1,
                "result": "loss",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 4,
                "roster_id": 1,
                "result": "win",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 5,
                "roster_id": 1,
                "result": "win",
            },
        ]
    )

    row = longest_streak(results, STANDINGS, "win").row(0, named=True)

    assert row["start_week"] == 1
    assert row["end_week"] == 2


def test_longest_streak_empty_input():
    assert longest_streak(_results([]), STANDINGS, "win").height == 0


def test_longest_streak_no_runs_of_the_requested_type():
    """A manager who has only ties has no win streak to report."""
    results = _results(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "result": "tie",
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "result": "tie",
            },
        ]
    )

    assert longest_streak(results, STANDINGS, "win").height == 0


def test_longest_streak_real_shaped_multi_manager_history():
    """End-to-end through weekly_results, mirroring how a real caller would use this."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": w,
                "roster_id": 1,
                "matchup_id": w,
                "points": 120.0 if w != 3 else 80.0,
            }
            for w in range(1, 5)
        ]
        + [
            {
                "league_id": "L1",
                "season": 2025,
                "week": w,
                "roster_id": 2,
                "matchup_id": w,
                "points": 100.0 if w != 3 else 130.0,
            }
            for w in range(1, 5)
        ]
    )
    results = weekly_results(matchups)

    win_streaks = {
        row["manager"]: row["length"]
        for row in longest_streak(results, STANDINGS, "win").iter_rows(named=True)
    }

    assert win_streaks["Alice"] == 2  # weeks 1-2, broken by the week-3 loss
    assert win_streaks["Bob"] == 1  # only week 3
