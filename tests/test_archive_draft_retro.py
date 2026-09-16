"""Unit tests for nuclearff.archive.draft_retro: draft retrospectives (issue #111)."""

from __future__ import annotations

import polars as pl

from nuclearff.archive.draft_retro import grade_historical_picks


def _picks(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "draft_id": pl.String,
        "season": pl.Int64,
        "pick_no": pl.Int64,
        "round": pl.Int64,
        "roster_id": pl.Int64,
        "player_id": pl.String,
        "position": pl.String,
        "first_name": pl.String,
        "last_name": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _actuals(rows: list[dict]) -> pl.DataFrame:
    schema = {"season": pl.Int64, "player_id": pl.String, "actual_points": pl.Float64}
    return pl.DataFrame(rows, schema=schema)


def _standings(rows: list[dict]) -> pl.DataFrame:
    schema = {"league_id": pl.String, "roster_id": pl.Int64, "display_name": pl.String}
    return pl.DataFrame(rows, schema=schema)


STANDINGS = _standings(
    [
        {"league_id": "L1", "roster_id": 1, "display_name": "Alice"},
        {"league_id": "L1", "roster_id": 2, "display_name": "Bob"},
        {"league_id": "L1", "roster_id": 3, "display_name": "Carol"},
    ]
)


def _draft_of_three() -> pl.DataFrame:
    return _picks(
        [
            {
                "league_id": "L1",
                "draft_id": "D1",
                "season": 2025,
                "pick_no": 1,
                "round": 1,
                "roster_id": 1,
                "player_id": "p_early_bust",
                "position": "RB",
                "first_name": "Early",
                "last_name": "Bust",
            },
            {
                "league_id": "L1",
                "draft_id": "D1",
                "season": 2025,
                "pick_no": 2,
                "round": 1,
                "roster_id": 2,
                "player_id": "p_solid",
                "position": "WR",
                "first_name": "Solid",
                "last_name": "Pick",
            },
            {
                "league_id": "L1",
                "draft_id": "D1",
                "season": 2025,
                "pick_no": 30,
                "round": 3,
                "roster_id": 3,
                "player_id": "p_late_steal",
                "position": "WR",
                "first_name": "Late",
                "last_name": "Steal",
            },
        ]
    )


def _actuals_for_draft_of_three() -> pl.DataFrame:
    return _actuals(
        [
            {"season": 2025, "player_id": "p_early_bust", "actual_points": 20.0},
            {"season": 2025, "player_id": "p_solid", "actual_points": 150.0},
            {"season": 2025, "player_id": "p_late_steal", "actual_points": 15.0},
            {"season": 2025, "player_id": "p_late_steal", "actual_points": 15.0},
            {"season": 2025, "player_id": "p_late_steal", "actual_points": 250.0},
        ]
    )


def test_grade_historical_picks_identifies_a_steal():
    """Late pick (pick_no 30), highest real points -> biggest positive rank_delta."""
    result = grade_historical_picks(
        _draft_of_three(), _actuals_for_draft_of_three(), STANDINGS
    )

    top = result.row(0, named=True)
    assert top["player_id"] == "p_late_steal"
    assert top["season_points"] == 280.0
    assert top["actual_rank"] == 1
    assert top["expected_rank"] == 3
    assert top["rank_delta"] == 2


def test_grade_historical_picks_identifies_a_bust():
    """1st overall pick, lowest real points -> biggest negative rank_delta."""
    result = grade_historical_picks(
        _draft_of_three(), _actuals_for_draft_of_three(), STANDINGS
    )

    bottom = result.row(-1, named=True)
    assert bottom["player_id"] == "p_early_bust"
    assert bottom["expected_rank"] == 1
    assert bottom["actual_rank"] == 3
    assert bottom["rank_delta"] == -2


def test_grade_historical_picks_no_points_defaults_to_zero_not_excluded():
    """A drafted player with zero rows in season_actuals is a real bust, not a gap."""
    picks = _picks(
        [
            {
                "league_id": "L1",
                "draft_id": "D1",
                "season": 2025,
                "pick_no": 1,
                "round": 1,
                "roster_id": 1,
                "player_id": "p_never_played",
                "position": "RB",
                "first_name": "Never",
                "last_name": "Played",
            },
            {
                "league_id": "L1",
                "draft_id": "D1",
                "season": 2025,
                "pick_no": 2,
                "round": 1,
                "roster_id": 2,
                "player_id": "p_solid",
                "position": "WR",
                "first_name": "Solid",
                "last_name": "Pick",
            },
        ]
    )
    actuals = _actuals(
        [{"season": 2025, "player_id": "p_solid", "actual_points": 100.0}]
    )

    result = grade_historical_picks(picks, actuals, STANDINGS)

    row = result.filter(pl.col("player_id") == "p_never_played").row(0, named=True)
    assert row["season_points"] == 0.0
    assert row["actual_rank"] == 2


def test_grade_historical_picks_ranks_within_each_draft_separately():
    picks = pl.concat(
        [
            _draft_of_three(),
            _picks(
                [
                    {
                        "league_id": "L2",
                        "draft_id": "D2",
                        "season": 2024,
                        "pick_no": 1,
                        "round": 1,
                        "roster_id": 1,
                        "player_id": "p_other_league",
                        "position": "QB",
                        "first_name": "Other",
                        "last_name": "League",
                    },
                ]
            ),
        ]
    )
    actuals = pl.concat(
        [
            _actuals_for_draft_of_three(),
            _actuals(
                [{"season": 2024, "player_id": "p_other_league", "actual_points": 5.0}]
            ),
        ]
    )
    standings = pl.concat(
        [
            STANDINGS,
            _standings([{"league_id": "L2", "roster_id": 1, "display_name": "Dave"}]),
        ]
    )

    result = grade_historical_picks(picks, actuals, standings)

    other = result.filter(pl.col("player_id") == "p_other_league").row(0, named=True)
    assert other["expected_rank"] == 1
    assert other["actual_rank"] == 1
    assert other["rank_delta"] == 0


def test_grade_historical_picks_keeps_a_pick_with_no_resolvable_manager():
    """Grading is about the player, not the manager -- an unresolvable
    display_name doesn't drop the pick, unlike manager-grain aggregates
    elsewhere in this project."""
    picks = _picks(
        [
            {
                "league_id": "L1",
                "draft_id": "D1",
                "season": 2025,
                "pick_no": 1,
                "round": 1,
                "roster_id": 999,
                "player_id": "p_orphan",
                "position": "RB",
                "first_name": "No",
                "last_name": "Roster",
            },
        ]
    )
    actuals = _actuals(
        [{"season": 2025, "player_id": "p_orphan", "actual_points": 50.0}]
    )

    result = grade_historical_picks(picks, actuals, STANDINGS)

    assert result.height == 1
    assert result.row(0, named=True)["manager"] is None


def test_grade_historical_picks_player_name_combines_first_and_last():
    result = grade_historical_picks(
        _draft_of_three(), _actuals_for_draft_of_three(), STANDINGS
    )

    names = set(result["player_name"].to_list())
    assert "Early Bust" in names
    assert "Late Steal" in names


def test_grade_historical_picks_empty_input():
    assert grade_historical_picks(_picks([]), _actuals([]), STANDINGS).height == 0
