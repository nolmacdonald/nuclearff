"""Unit tests for nuclearff.archive.owner_continuity (issue #153)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.archive.owner_continuity import DEFAULT_MIN_OVERLAP, owner_transitions

LEAGUES = pl.DataFrame(
    {
        "league_id": ["L2024", "L2025", "L2026"],
        "previous_league_id": [None, "L2024", "L2025"],
    },
    schema={"league_id": pl.String, "previous_league_id": pl.String},
)


def _standings(rows: list[tuple]) -> pl.DataFrame:
    # (league_id, season, roster_id, owner_id, display_name)
    return pl.DataFrame(
        rows,
        schema={
            "league_id": pl.String,
            "season": pl.Int64,
            "roster_id": pl.Int64,
            "owner_id": pl.String,
            "display_name": pl.String,
        },
        orient="row",
    )


def _players(rows: dict[tuple[str, int], list[str]]) -> pl.DataFrame:
    return pl.DataFrame(
        [(lid, rid, pid) for (lid, rid), pids in rows.items() for pid in pids],
        schema={"league_id": pl.String, "roster_id": pl.Int64, "player_id": pl.String},
        orient="row",
    )


STANDINGS = _standings(
    [
        # Roster 1: same owner every season.
        ("L2024", 2024, 1, "uA", "Alice"),
        ("L2025", 2025, 1, "uA", "Alice"),
        ("L2026", 2026, 1, "uA", "Alice"),
        # Roster 2: Bob hands the team to Cara in 2025, who keeps it in 2026.
        ("L2024", 2024, 2, "uB", "Bob"),
        ("L2025", 2025, 2, "uC", "Cara"),
        ("L2026", 2026, 2, "uC", "Cara"),
        # Roster 3: Dan -> Eve, but the roster is rebuilt from scratch.
        ("L2024", 2024, 3, "uD", "Dan"),
        ("L2025", 2025, 3, "uE", "Eve"),
        # Roster 4 only exists from 2025 (expansion).
        ("L2025", 2025, 4, "uF", "Fay"),
    ]
)

PLAYERS = _players(
    {
        ("L2024", 1): ["p1", "p2", "p3"],
        ("L2025", 1): ["p1", "p2", "p3"],
        ("L2024", 2): ["a", "b", "c", "d", "e"],
        ("L2025", 2): ["a", "b", "c", "d", "z"],
        ("L2026", 2): ["a", "b", "c", "d", "z"],
        ("L2024", 3): ["m", "n", "o"],
        ("L2025", 3): ["x", "y", "w"],
        ("L2025", 4): ["q"],
    }
)


def test_a_high_overlap_handoff_is_flagged_with_the_real_fraction():
    result = owner_transitions(PLAYERS, STANDINGS, LEAGUES)

    row = result.filter(pl.col("roster_id") == 2).row(0, named=True)
    assert row["league_id"] == "L2025"
    assert (row["old_owner_id"], row["new_owner_id"]) == ("uB", "uC")
    assert (row["old_display_name"], row["new_display_name"]) == ("Bob", "Cara")
    assert row["transition_season"] == 2025
    assert (row["old_player_count"], row["players_retained"]) == (5, 4)
    assert row["player_overlap_fraction"] == pytest.approx(0.8)
    assert row["probable_handoff"] is True


def test_a_full_roster_reset_is_reported_but_not_flagged():
    result = owner_transitions(PLAYERS, STANDINGS, LEAGUES)

    row = result.filter(pl.col("roster_id") == 3).row(0, named=True)
    assert row["player_overlap_fraction"] == 0.0
    assert row["probable_handoff"] is False


def test_unchanged_owners_and_new_rosters_produce_no_rows():
    result = owner_transitions(PLAYERS, STANDINGS, LEAGUES)

    assert sorted(result["roster_id"].to_list()) == [2, 3]


def test_the_threshold_is_adjustable_and_inclusive():
    strict = owner_transitions(PLAYERS, STANDINGS, LEAGUES, min_overlap=0.9)
    exact = owner_transitions(PLAYERS, STANDINGS, LEAGUES, min_overlap=0.8)

    assert DEFAULT_MIN_OVERLAP == 0.5
    assert strict.filter(pl.col("roster_id") == 2)["probable_handoff"].to_list() == [
        False
    ]
    assert exact.filter(pl.col("roster_id") == 2)["probable_handoff"].to_list() == [
        True
    ]


def test_an_old_roster_with_no_persisted_players_has_null_overlap_and_is_unflagged():
    players = PLAYERS.filter(
        ~((pl.col("league_id") == "L2024") & (pl.col("roster_id") == 2))
    )

    row = (
        owner_transitions(players, STANDINGS, LEAGUES)
        .filter(pl.col("roster_id") == 2)
        .row(0, named=True)
    )

    assert row["player_overlap_fraction"] is None
    assert row["probable_handoff"] is False


def test_a_vacated_or_unowned_roster_is_not_a_change():
    standings = _standings(
        [
            ("L2024", 2024, 1, "uA", "Alice"),
            ("L2025", 2025, 1, None, None),
            ("L2024", 2024, 2, None, None),
            ("L2025", 2025, 2, "uB", "Bob"),
        ]
    )

    assert owner_transitions(PLAYERS, standings, LEAGUES).height == 0


def test_seasons_are_linked_by_previous_league_id_not_by_order():
    unlinked = LEAGUES.with_columns(
        pl.lit(None, dtype=pl.String).alias("previous_league_id")
    )

    assert owner_transitions(PLAYERS, STANDINGS, unlinked).height == 0


def test_rows_are_ordered_by_season_then_roster():
    standings = _standings(
        [
            ("L2024", 2024, 5, "u1", "A"),
            ("L2025", 2025, 5, "u2", "B"),
            ("L2025", 2025, 2, "u3", "C"),
            ("L2026", 2026, 2, "u4", "D"),
            ("L2024", 2024, 2, "u9", "Z"),
        ]
    )

    result = owner_transitions(PLAYERS, standings, LEAGUES)

    assert list(zip(result["transition_season"], result["roster_id"], strict=True)) == [
        (2025, 2),
        (2025, 5),
        (2026, 2),
    ]


def test_empty_result_keeps_the_schema():
    result = owner_transitions(PLAYERS, STANDINGS.head(0), LEAGUES)

    assert result.height == 0
    assert "player_overlap_fraction" in result.columns


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_min_overlap_outside_unit_interval_is_rejected(value):
    with pytest.raises(ValueError, match="min_overlap"):
        owner_transitions(PLAYERS, STANDINGS, LEAGUES, min_overlap=value)
