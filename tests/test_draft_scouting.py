"""Unit tests for nuclearff.draft.scouting (issue #107)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.draft.scouting import SMALL_SAMPLE_SEASONS, manager_position_profile

_PICK_SCHEMA = {
    "league_id": pl.String,
    "draft_id": pl.String,
    "round": pl.Int64,
    "roster_id": pl.Int64,
    "position": pl.String,
}


def _picks(rows: list[tuple]) -> pl.DataFrame:
    # (season_tag, round, roster_id, position); each season is its own league
    # and draft, as in Sleeper.
    return pl.DataFrame(
        [(f"L{tag}", f"D{tag}", rnd, rid, pos) for tag, rnd, rid, pos in rows],
        schema=_PICK_SCHEMA,
        orient="row",
    )


def _standings(seasons: list[str], names: dict[int, str | None]) -> pl.DataFrame:
    return pl.DataFrame(
        [(f"L{s}", rid, name) for s in seasons for rid, name in names.items()],
        schema={
            "league_id": pl.String,
            "roster_id": pl.Int64,
            "display_name": pl.String,
        },
        orient="row",
    )


def _row(frame: pl.DataFrame, manager: str, position: str, bucket: str) -> dict:
    rows = frame.filter(
        (pl.col("manager") == manager)
        & (pl.col("position") == position)
        & (pl.col("round_bucket") == bucket)
    )
    assert rows.height == 1
    return rows.row(0, named=True)


def test_a_consistent_pattern_shows_a_full_season_rate():
    seasons = ["2022", "2023", "2024", "2025"]
    picks = _picks(
        [(s, 1, 1, "RB") for s in seasons] + [(s, 4, 1, "WR") for s in seasons]
    )

    profile = manager_position_profile(picks, _standings(seasons, {1: "Alice"}))

    rb = _row(profile, "Alice", "RB", "1-3")
    assert (rb["picks"], rb["seasons_with_pick"], rb["seasons_drafted"]) == (4, 4, 4)
    assert rb["season_rate"] == 1.0
    assert rb["small_sample"] is False


def test_rate_is_over_all_drafted_seasons_not_just_seasons_with_the_position():
    seasons = ["2022", "2023", "2024", "2025"]
    picks = _picks(
        [
            ("2022", 2, 1, "QB"),
            ("2023", 3, 1, "QB"),
            ("2024", 2, 1, "RB"),
            ("2025", 2, 1, "RB"),
        ]
    )

    profile = manager_position_profile(picks, _standings(seasons, {1: "Alice"}))

    qb = _row(profile, "Alice", "QB", "1-3")
    assert qb["seasons_with_pick"] == 2
    assert qb["seasons_drafted"] == 4
    assert qb["season_rate"] == pytest.approx(0.5)


def test_several_picks_in_one_season_count_once_toward_the_season_rate():
    picks = _picks([("2025", 1, 1, "RB"), ("2025", 2, 1, "RB"), ("2025", 3, 1, "RB")])

    profile = manager_position_profile(picks, _standings(["2025"], {1: "Alice"}))

    rb = _row(profile, "Alice", "RB", "1-3")
    assert rb["picks"] == 3
    assert rb["seasons_with_pick"] == 1
    assert rb["season_rate"] == 1.0


def test_one_season_of_history_still_produces_a_flagged_profile():
    picks = _picks([("2025", 1, 1, "RB")])

    profile = manager_position_profile(picks, _standings(["2025"], {1: "Alice"}))

    row = _row(profile, "Alice", "RB", "1-3")
    assert row["seasons_drafted"] == 1
    assert row["small_sample"] is True
    assert SMALL_SAMPLE_SEASONS > 1


def test_round_bucket_edges():
    picks = _picks(
        [
            ("2025", 3, 1, "A"),
            ("2025", 4, 1, "B"),
            ("2025", 6, 1, "C"),
            ("2025", 7, 1, "D"),
        ]
    )

    profile = manager_position_profile(picks, _standings(["2025"], {1: "Alice"}))

    buckets = dict(zip(profile["position"], profile["round_bucket"], strict=True))
    assert buckets == {"A": "1-3", "B": "4-6", "C": "4-6", "D": "7+"}


def test_managers_are_profiled_separately_and_sorted():
    picks = _picks([("2025", 1, 1, "RB"), ("2025", 1, 2, "QB"), ("2025", 8, 2, "TE")])

    profile = manager_position_profile(
        picks, _standings(["2025"], {1: "Alice", 2: "Bob"})
    )

    assert profile["manager"].to_list() == ["Alice", "Bob", "Bob"]
    assert profile.filter(pl.col("manager") == "Bob")["round_bucket"].to_list() == [
        "1-3",
        "7+",
    ]


def test_picks_without_position_or_round_add_no_row_but_still_count_the_season():
    picks = pl.DataFrame(
        [
            ("L2025", "D2025", None, 1, "RB"),
            ("L2025", "D2025", 2, 1, None),
            ("L2025", "D2025", 5, 1, "WR"),
        ],
        schema=_PICK_SCHEMA,
        orient="row",
    )

    profile = manager_position_profile(picks, _standings(["2025"], {1: "Alice"}))

    assert profile["position"].to_list() == ["WR"]
    assert profile["seasons_drafted"].to_list() == [1]


def test_a_roster_without_a_display_name_is_omitted():
    picks = _picks([("2025", 1, 1, "RB"), ("2025", 1, 2, "QB")])

    profile = manager_position_profile(
        picks, _standings(["2025"], {1: "Alice", 2: None})
    )

    assert profile["manager"].unique().to_list() == ["Alice"]


def test_populated_result_has_the_same_schema_as_the_empty_one():
    picks = _picks([("2025", 1, 1, "RB")])
    standings = _standings(["2025"], {1: "Alice"})

    populated = manager_position_profile(picks, standings)
    empty = manager_position_profile(pl.DataFrame(schema=_PICK_SCHEMA), standings)

    assert populated.schema == empty.schema


def test_empty_picks_return_the_empty_schema():
    profile = manager_position_profile(
        pl.DataFrame(schema=_PICK_SCHEMA), _standings(["2025"], {1: "Alice"})
    )

    assert profile.height == 0
    assert "season_rate" in profile.columns
