"""Unit tests for nuclearff.archive.rivalries: head-to-head records (issue #110)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.archive.rivalries import (
    PHASE_PLAYOFF,
    PHASE_REGULAR_SEASON,
    head_to_head,
    head_to_head_by_phase,
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


STANDINGS = _standings(
    [
        {"league_id": "L1", "roster_id": 1, "display_name": "Alice"},
        {"league_id": "L1", "roster_id": 2, "display_name": "Bob"},
    ]
)


def test_head_to_head_tracks_wins_and_avoids_double_counting():
    matchups = _matchups(
        [
            # Week 1: Alice beats Bob by 20
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 120.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 100.0,
            },
            # Week 2: Bob beats Alice by 5
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "matchup_id": 2,
                "points": 95.0,
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

    result = head_to_head(matchups, STANDINGS)

    assert result.height == 1
    row = result.row(0, named=True)
    assert row["manager_a"] == "Alice"
    assert row["manager_b"] == "Bob"
    assert row["games"] == 2
    assert row["wins_a"] == 1
    assert row["wins_b"] == 1
    assert row["ties"] == 0
    assert row["avg_margin"] == pytest.approx(12.5)


def test_head_to_head_finds_the_pairs_biggest_blowout():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 110.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "matchup_id": 2,
                "points": 200.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 2,
                "matchup_id": 2,
                "points": 50.0,
            },
        ]
    )

    row = head_to_head(matchups, STANDINGS).row(0, named=True)

    assert row["biggest_blowout_margin"] == pytest.approx(150.0)
    assert row["biggest_blowout_winner"] == "Alice"
    assert row["biggest_blowout_week"] == 2


def test_head_to_head_counts_ties():
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

    row = head_to_head(matchups, STANDINGS).row(0, named=True)

    assert row["ties"] == 1
    assert row["wins_a"] == 0
    assert row["wins_b"] == 0
    assert row["biggest_blowout_winner"] is None
    assert row["biggest_blowout_margin"] == 0.0


def test_head_to_head_excludes_a_roster_with_no_resolvable_manager():
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
                "roster_id": 99,
                "matchup_id": 1,
                "points": 90.0,
            },
        ]
    )

    assert head_to_head(matchups, STANDINGS).height == 0


def test_head_to_head_empty_input():
    assert head_to_head(_matchups([]), STANDINGS).height == 0


def test_head_to_head_spans_multiple_seasons():
    matchups = _matchups(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2024,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 90.0,
            },
            {
                "league_id": "L2",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 80.0,
            },
            {
                "league_id": "L2",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 70.0,
            },
        ]
    )
    standings = pl.concat(
        [
            STANDINGS,
            _standings(
                [
                    {"league_id": "L2", "roster_id": 1, "display_name": "Alice"},
                    {"league_id": "L2", "roster_id": 2, "display_name": "Bob"},
                ]
            ),
        ]
    )

    row = head_to_head(matchups, standings).row(0, named=True)

    assert row["games"] == 2
    assert row["wins_a"] == 2


# --- head_to_head_by_phase (issue #137) -------------------------------------


def _league_configs(rows: list[dict]) -> pl.DataFrame:
    schema = {"league_id": pl.String, "playoff_week_start": pl.Int64}
    return pl.DataFrame(rows, schema=schema)


def _phase_matchups() -> pl.DataFrame:
    """Alice vs. Bob: 2 regular-season meetings (weeks 1-2), 1 playoff (week 15)."""
    return _matchups(
        [
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 1,
                "matchup_id": 1,
                "points": 120.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 1,
                "roster_id": 2,
                "matchup_id": 1,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 1,
                "matchup_id": 2,
                "points": 95.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 2,
                "roster_id": 2,
                "matchup_id": 2,
                "points": 100.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 15,
                "roster_id": 1,
                "matchup_id": 3,
                "points": 150.0,
            },
            {
                "league_id": "L1",
                "season": 2025,
                "week": 15,
                "roster_id": 2,
                "matchup_id": 3,
                "points": 90.0,
            },
        ]
    )


def test_head_to_head_by_phase_splits_regular_season_and_playoff():
    """Week >= playoff_week_start is playoff; everything before is regular season."""
    league_configs = _league_configs([{"league_id": "L1", "playoff_week_start": 15}])

    result = head_to_head_by_phase(_phase_matchups(), STANDINGS, league_configs)

    assert set(result["phase"].to_list()) == {PHASE_REGULAR_SEASON, PHASE_PLAYOFF}

    regular = result.filter(pl.col("phase") == PHASE_REGULAR_SEASON).row(0, named=True)
    playoff = result.filter(pl.col("phase") == PHASE_PLAYOFF).row(0, named=True)

    assert regular["games"] == 2
    assert regular["wins_a"] == 1
    assert regular["wins_b"] == 1

    assert playoff["games"] == 1
    assert playoff["wins_a"] == 1
    assert playoff["biggest_blowout_winner"] == "Alice"


def test_head_to_head_by_phase_games_sum_to_combined_total():
    """The real acceptance criterion from #137: phases sum to head_to_head's total."""
    matchups = _phase_matchups()
    league_configs = _league_configs([{"league_id": "L1", "playoff_week_start": 15}])

    combined = head_to_head(matchups, STANDINGS).row(0, named=True)
    split = head_to_head_by_phase(matchups, STANDINGS, league_configs)

    assert split["games"].sum() == combined["games"]


def test_head_to_head_by_phase_excludes_seasons_with_no_resolvable_boundary():
    """A season missing from league_configs is excluded from both phases."""
    matchups = _phase_matchups()
    empty_league_configs = _league_configs([])

    result = head_to_head_by_phase(matchups, STANDINGS, empty_league_configs)

    assert result.height == 0


def test_head_to_head_by_phase_excludes_a_season_with_a_null_playoff_week_start():
    """A 'Chopped' league (no bracket) has a null playoff_week_start -- excluded."""
    matchups = _phase_matchups()
    league_configs = _league_configs([{"league_id": "L1", "playoff_week_start": None}])

    result = head_to_head_by_phase(matchups, STANDINGS, league_configs)

    assert result.height == 0


def test_head_to_head_by_phase_empty_input():
    league_configs = _league_configs([{"league_id": "L1", "playoff_week_start": 15}])

    assert head_to_head_by_phase(_matchups([]), STANDINGS, league_configs).height == 0
