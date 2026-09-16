"""Unit tests for nuclearff.archive.name_history: team name changes (issue #130)."""

from __future__ import annotations

import polars as pl

from nuclearff.archive.name_history import team_name_changes


def _roster_names(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "roster_id": pl.Int64,
        "owner_id": pl.String,
        "team_name": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _standings(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "owner_id": pl.String,
        "display_name": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def test_team_name_changes_counts_real_transitions_only():
    roster_names = _roster_names(
        [
            {
                "league_id": "L1",
                "season": 2023,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "A",
            },
            {
                "league_id": "L2",
                "season": 2024,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "B",
            },
            {
                "league_id": "L3",
                "season": 2025,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "A",
            },
        ]
    )
    standings = _standings(
        [{"league_id": "L3", "season": 2025, "owner_id": "u1", "display_name": "Alice"}]
    )

    result = team_name_changes(roster_names, standings)

    row = result.row(0, named=True)
    assert row["owner_id"] == "u1"
    assert row["manager"] == "Alice"
    assert row["name_sequence"] == ["A", "B", "A"]
    assert row["change_count"] == 2


def test_team_name_changes_does_not_inflate_via_distinct_name_count():
    """A, A, A, B, B is one real change, not "2 distinct names used"."""
    roster_names = _roster_names(
        [
            {
                "league_id": f"L{i}",
                "season": 2020 + i,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": name,
            }
            for i, name in enumerate(["A", "A", "A", "B", "B"])
        ]
    )
    standings = _standings([])

    row = team_name_changes(roster_names, standings).row(0, named=True)

    assert row["change_count"] == 1


def test_team_name_changes_a_null_season_does_not_break_the_comparison():
    roster_names = _roster_names(
        [
            {
                "league_id": "L1",
                "season": 2023,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "A",
            },
            {
                "league_id": "L2",
                "season": 2024,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": None,
            },
            {
                "league_id": "L3",
                "season": 2025,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "A",
            },
        ]
    )
    standings = _standings([])

    row = team_name_changes(roster_names, standings).row(0, named=True)

    assert row["name_sequence"] == ["A", None, "A"]
    assert row["change_count"] == 0


def test_team_name_changes_manager_who_never_set_a_custom_name():
    roster_names = _roster_names(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "roster_id": 2,
                "owner_id": "u2",
                "team_name": None,
            },
            {
                "league_id": "L2",
                "season": 2025,
                "roster_id": 2,
                "owner_id": "u2",
                "team_name": None,
            },
        ]
    )
    standings = _standings([])

    row = team_name_changes(roster_names, standings).row(0, named=True)

    assert row["name_sequence"] == [None, None]
    assert row["change_count"] == 0


def test_team_name_changes_leaderboard_sorted_by_change_count_descending():
    roster_names = _roster_names(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "A",
            },
            {
                "league_id": "L2",
                "season": 2025,
                "roster_id": 1,
                "owner_id": "u1",
                "team_name": "B",
            },
            {
                "league_id": "L1",
                "season": 2024,
                "roster_id": 2,
                "owner_id": "u2",
                "team_name": "X",
            },
            {
                "league_id": "L2",
                "season": 2025,
                "roster_id": 2,
                "owner_id": "u2",
                "team_name": "X",
            },
        ]
    )
    standings = _standings([])

    result = team_name_changes(roster_names, standings)

    assert result["owner_id"].to_list() == ["u1", "u2"]
    assert result["change_count"].to_list() == [1, 0]


def test_team_name_changes_excludes_a_roster_with_no_owner():
    roster_names = _roster_names(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "roster_id": 1,
                "owner_id": None,
                "team_name": "A",
            },
        ]
    )
    standings = _standings([])

    assert team_name_changes(roster_names, standings).height == 0


def test_team_name_changes_empty_input():
    assert team_name_changes(_roster_names([]), _standings([])).height == 0
