"""Unit tests for nuclearff.archive.champions: season-level history data prep."""

from __future__ import annotations

import polars as pl

from nuclearff.archive.champions import championship_history


def _standings(rows: list[dict[str, object]]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "roster_id": pl.Int64,
        "display_name": pl.String,
        "final_rank": pl.Int64,
        "regular_season_rank": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def test_champion_and_runner_up_from_final_rank():
    """Regular-season leader can differ from the eventual champion."""
    standings = _standings(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "roster_id": 1,
                "display_name": "Alice",
                "final_rank": 2,
                "regular_season_rank": 1,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "roster_id": 2,
                "display_name": "Bob",
                "final_rank": 1,
                "regular_season_rank": 2,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "roster_id": 3,
                "display_name": "Carol",
                "final_rank": 3,
                "regular_season_rank": 3,
            },
        ]
    )

    history = {
        row["league_id"]: row
        for row in championship_history(standings).iter_rows(named=True)
    }

    row = history["L1"]
    assert row["season"] == 2025
    assert row["champion_display_name"] == "Bob"
    assert row["champion_roster_id"] == 2
    assert row["runner_up_display_name"] == "Alice"
    assert row["regular_season_leader_display_name"] == "Alice"
    assert row["regular_season_leader_roster_id"] == 1


def test_unresolved_bracket_leaves_champion_null_not_omitted():
    """A season with no placement yet still gets a row -- not silently dropped."""
    standings = _standings(
        [
            {
                "league_id": "L2",
                "season": 2026,
                "roster_id": 1,
                "display_name": "Alice",
                "final_rank": None,
                "regular_season_rank": 1,
            },
            {
                "league_id": "L2",
                "season": 2026,
                "roster_id": 2,
                "display_name": "Bob",
                "final_rank": None,
                "regular_season_rank": 2,
            },
        ]
    )

    history = championship_history(standings)

    assert history.height == 1
    row = history.row(0, named=True)
    assert row["league_id"] == "L2"
    assert row["champion_roster_id"] is None
    assert row["champion_display_name"] is None
    assert row["runner_up_roster_id"] is None
    # regular-season rank is always assigned, unlike final_rank.
    assert row["regular_season_leader_display_name"] == "Alice"


def test_multiple_seasons_each_get_their_own_row_sorted_by_season():
    standings = _standings(
        [
            {
                "league_id": "L2026",
                "season": 2026,
                "roster_id": 1,
                "display_name": "Dave",
                "final_rank": 1,
                "regular_season_rank": 1,
            },
            {
                "league_id": "L2025",
                "season": 2025,
                "roster_id": 1,
                "display_name": "Erin",
                "final_rank": 1,
                "regular_season_rank": 1,
            },
        ]
    )

    history = championship_history(standings)

    assert history["season"].to_list() == [2025, 2026]
    assert history["champion_display_name"].to_list() == ["Erin", "Dave"]


def test_missing_display_name_surfaces_as_null_not_dropped():
    """A roster whose owner has no resolvable display_name (data inconsistency)."""
    standings = _standings(
        [
            {
                "league_id": "L3",
                "season": 2024,
                "roster_id": 1,
                "display_name": None,
                "final_rank": 1,
                "regular_season_rank": 1,
            },
        ]
    )

    history = championship_history(standings)

    assert history.height == 1
    row = history.row(0, named=True)
    assert row["champion_roster_id"] == 1
    assert row["champion_display_name"] is None


def test_empty_input_returns_empty_frame_with_expected_columns():
    empty = _standings([])

    history = championship_history(empty)

    assert history.height == 0
    assert history.columns == [
        "league_id",
        "season",
        "champion_roster_id",
        "champion_display_name",
        "runner_up_roster_id",
        "runner_up_display_name",
        "regular_season_leader_roster_id",
        "regular_season_leader_display_name",
    ]


def test_tied_final_rank_picks_deterministically_by_roster_id():
    """Two rosters tied at final_rank == 1 is a data anomaly, not expected in
    real data -- the pick should still be reproducible rather than arbitrary."""
    standings = _standings(
        [
            {
                "league_id": "L4",
                "season": 2023,
                "roster_id": 5,
                "display_name": "Zoe",
                "final_rank": 1,
                "regular_season_rank": 1,
            },
            {
                "league_id": "L4",
                "season": 2023,
                "roster_id": 2,
                "display_name": "Yusuf",
                "final_rank": 1,
                "regular_season_rank": 2,
            },
        ]
    )

    history = championship_history(standings)

    assert history.row(0, named=True)["champion_roster_id"] == 2
