"""Unit tests for nuclearff.sleeper.wins (GitHub Issue 79)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.sleeper.wins import (
    cumulative_wins,
    drop_unplayed_weeks,
    paired_weekly_matchups,
    weekly_results,
)


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


# --- paired_weekly_matchups --------------------------------------------------


def test_paired_weekly_matchups_keeps_points_and_margin():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 123.62,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 4,
                "points": 110.0,
            },
        ]
    )

    paired = {
        row["roster_id"]: row
        for row in paired_weekly_matchups(matchups).iter_rows(named=True)
    }

    assert paired[1]["opponent_roster_id"] == 2
    assert paired[1]["opponent_points"] == 110.0
    assert paired[1]["margin"] == pytest.approx(13.62)
    assert paired[2]["margin"] == pytest.approx(-13.62)


def test_paired_weekly_matchups_skips_a_bye_week():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": None,
                "points": 123.62,
            },
        ]
    )

    assert paired_weekly_matchups(matchups).height == 0


def test_paired_weekly_matchups_handles_no_matchups():
    assert paired_weekly_matchups(_matchups([])).height == 0


def test_paired_weekly_matchups_drops_a_not_yet_played_week():
    """Issue #171: a future week where Sleeper's placeholder has every roster
    at 0.0 must not pair up into a phantom 0-0 tie."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2026,
                "week": 15,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 0.0,
            },
            {
                "league_id": "L1",
                "season": 2026,
                "week": 15,
                "roster_id": 2,
                "matchup_id": 4,
                "points": 0.0,
            },
        ]
    )

    assert paired_weekly_matchups(matchups).height == 0


# --- drop_unplayed_weeks -----------------------------------------------------


def test_drop_unplayed_weeks_removes_a_week_where_everyone_scored_zero():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2026,
                "week": 3,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 0.0,
            },
            {
                "league_id": "L1",
                "season": 2026,
                "week": 3,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 0.0,
            },
        ]
    )

    assert drop_unplayed_weeks(matchups).height == 0


def test_drop_unplayed_weeks_keeps_a_real_zero_for_zero_tie():
    """A tie with real (nonzero) points on the board elsewhere in the same
    week must not be swept up with the unplayed weeks."""
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

    assert drop_unplayed_weeks(matchups).height == 2


def test_drop_unplayed_weeks_keeps_other_weeks_in_the_same_league():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2026,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 120.0,
            },
            {
                "league_id": "L1",
                "season": 2026,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 90.0,
            },
            {
                "league_id": "L1",
                "season": 2026,
                "week": 3,
                "roster_id": 1,
                "matchup_id": 2,
                "points": 0.0,
            },
            {
                "league_id": "L1",
                "season": 2026,
                "week": 3,
                "roster_id": 2,
                "matchup_id": 2,
                "points": 0.0,
            },
        ]
    )

    remaining = drop_unplayed_weeks(matchups)

    assert remaining["week"].to_list() == [1, 1]


def test_drop_unplayed_weeks_handles_no_matchups():
    assert drop_unplayed_weeks(_matchups([])).height == 0


def test_weekly_results_is_derived_from_the_same_pairing_as_margins():
    """weekly_results' win/loss/tie must agree with paired_weekly_matchups'
    margin sign."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 4,
                "points": 100.0,
            },
        ]
    )

    margin = paired_weekly_matchups(matchups)["margin"].to_list()
    result = weekly_results(matchups)["result"].to_list()

    assert margin == [0.0, 0.0]
    assert result == ["tie", "tie"]


# --- weekly_results ---------------------------------------------------------


def test_weekly_results_resolves_a_normal_matchup():
    """Shaped like the real fixture (MATCHUPS_WEEK_1 in test_sleeper_matchups.py)."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 123.62,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 4,
                "points": 110.0,
            },
        ]
    )

    results = weekly_results(matchups).sort("roster_id")

    assert results["result"].to_list() == ["win", "loss"]


def test_weekly_results_handles_a_tie():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 4,
                "points": 100.0,
            },
        ]
    )

    results = weekly_results(matchups)

    assert set(results["result"].to_list()) == {"tie"}


def test_weekly_results_skips_a_bye_week():
    """A lone roster with a null matchup_id -- real per matchups.py's own docstring."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": None,
                "points": 123.62,
            },
        ]
    )

    assert weekly_results(matchups).height == 0


def test_weekly_results_skips_an_incomplete_group():
    """A matchup_id shared by only one roster (a data glitch, not a real bye)
    is skipped too."""
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 4,
                "points": 100.0,
            },
        ]
    )

    assert weekly_results(matchups).height == 0


def test_weekly_results_handles_no_matchups():
    assert weekly_results(_matchups([])).height == 0


# --- cumulative_wins ---------------------------------------------------------


def test_cumulative_wins_is_monotonically_non_decreasing():
    results = pl.DataFrame(
        {
            "league_id": ["L1", "L1", "L1"],
            "season": [2025, 2025, 2025],
            "week": [1, 2, 3],
            "roster_id": [1, 1, 1],
            "result": ["win", "loss", "win"],
        }
    )
    standings = _standings(
        [{"league_id": "L1", "roster_id": 1, "display_name": "nolmacdonald"}]
    )

    cumulative = cumulative_wins(results, standings)

    assert cumulative["cumulative_wins"].to_list() == [1, 1, 2]
    assert cumulative["game_number"].to_list() == [1, 2, 3]


def test_cumulative_wins_a_tie_does_not_increment():
    results = pl.DataFrame(
        {
            "league_id": ["L1", "L1"],
            "season": [2025, 2025],
            "week": [1, 2],
            "roster_id": [1, 1],
            "result": ["win", "tie"],
        }
    )
    standings = _standings(
        [{"league_id": "L1", "roster_id": 1, "display_name": "nolmacdonald"}]
    )

    cumulative = cumulative_wins(results, standings)

    assert cumulative["cumulative_wins"].to_list() == [1, 1]


def test_cumulative_wins_a_manager_joining_partway_through_starts_at_game_one():
    """A manager's game_number is their own, not a league-wide week index."""
    results = pl.DataFrame(
        {
            "league_id": ["L1", "L2", "L2"],
            "season": [2024, 2025, 2025],
            "week": [10, 1, 2],
            "roster_id": [1, 5, 5],
            "result": ["win", "loss", "win"],
        }
    )
    standings = _standings(
        [
            {"league_id": "L1", "roster_id": 1, "display_name": "early_joiner"},
            {"league_id": "L2", "roster_id": 5, "display_name": "late_joiner"},
        ]
    )

    cumulative = cumulative_wins(results, standings).filter(
        pl.col("manager") == "late_joiner"
    )

    assert cumulative["game_number"].to_list() == [1, 2]


def test_cumulative_wins_skips_an_unresolvable_roster():
    results = pl.DataFrame(
        {
            "league_id": ["L1"],
            "season": [2025],
            "week": [1],
            "roster_id": [99],
            "result": ["win"],
        }
    )
    standings = _standings(
        [{"league_id": "L1", "roster_id": 1, "display_name": "someone"}]
    )

    assert cumulative_wins(results, standings).height == 0


def test_cumulative_wins_handles_no_results():
    assert cumulative_wins(weekly_results(_matchups([])), _standings([])).height == 0
