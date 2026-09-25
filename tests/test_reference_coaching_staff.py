"""Unit tests for nuclearff.reference.coaching_staff.

No network. `load_coaching_staff`'s tests against the real shipped CSV prove
that file itself passes its own validation; `_validate`'s own rules are
tested directly against small synthetic DataFrames so each failure mode is
isolated (matching tests/test_nflverse_schedules.py's synthetic-frame
convention) without needing to hand-corrupt the real, cited data file.
"""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.reference.coaching_staff import (
    _SCHEMA_OVERRIDES,
    _validate,
    join_coaching_staff,
    load_coaching_staff,
)

# --- load_coaching_staff against the real shipped CSV --------------------------


def test_load_coaching_staff_returns_every_curated_row_by_default():
    result = load_coaching_staff()

    assert result.height == 5
    assert set(result["season"].to_list()) == {2022, 2023, 2024, 2025, 2026}


def test_load_coaching_staff_filters_to_requested_seasons():
    result = load_coaching_staff(seasons=[2025, 2026])

    assert result.height == 2
    assert set(result["season"].to_list()) == {2025, 2026}


def test_load_coaching_staff_empty_seasons_request_is_not_an_error():
    """A team/season not yet backfilled returns empty, not a raise -- this
    table only covers Tampa Bay so far."""
    result = load_coaching_staff(seasons=[1999])

    assert result.height == 0


def test_load_coaching_staff_2025_coordinator_is_josh_grizzard():
    """Pins the one row the epic's own motivating text under-specified (it
    names only "Grizzard") to the real, independently verified full name."""
    row = load_coaching_staff(seasons=[2025]).row(0, named=True)

    assert row["name"] == "Josh Grizzard"
    assert row["is_play_caller"] is True
    assert row["source_url"].startswith("http")


def test_load_coaching_staff_every_row_cites_a_real_looking_source():
    result = load_coaching_staff()

    assert all(url.startswith("http") for url in result["source_url"].to_list())


# --- _validate -------------------------------------------------------------------


def _valid_row(**overrides) -> dict:
    row = {
        "season": 2026,
        "team": "TB",
        "role": "OC",
        "name": "Zac Robinson",
        "is_play_caller": True,
        "start_week": 1,
        "end_week": None,
        "source_url": "https://www.buccaneers.com/news/zac-robinson-hired-bucs-offensive-coordinator",
        "notes": None,
    }
    row.update(overrides)
    return row


def _frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=_SCHEMA_OVERRIDES)


def test_validate_accepts_a_well_formed_table():
    _validate(_frame(_valid_row()))  # must not raise


def test_validate_rejects_a_missing_column():
    df = _frame(_valid_row()).drop("source_url")

    with pytest.raises(ValueError, match="missing expected column"):
        _validate(df)


def test_validate_rejects_a_null_required_field():
    with pytest.raises(ValueError, match="null 'name'"):
        _validate(_frame(_valid_row(name=None)))


def test_validate_allows_a_null_end_week():
    """None means "through the end of the season," not missing data."""
    _validate(_frame(_valid_row(end_week=None)))  # must not raise


def test_validate_rejects_a_placeholder_source_url():
    with pytest.raises(ValueError, match="source_url that doesn't start with"):
        _validate(_frame(_valid_row(source_url="TODO")))


def test_validate_rejects_end_week_before_start_week():
    with pytest.raises(ValueError, match="end_week before start_week"):
        _validate(_frame(_valid_row(start_week=10, end_week=5)))


def test_validate_rejects_overlapping_ranges_for_the_same_season_team_role():
    df = _frame(
        _valid_row(name="Coach A", start_week=1, end_week=10),
        _valid_row(name="Coach B", start_week=8, end_week=None),
    )

    with pytest.raises(ValueError, match="overlapping week ranges"):
        _validate(df)


def test_validate_allows_a_real_mid_season_handoff():
    """A genuine, non-overlapping mid-season change (the CSV schema's own
    stated purpose) must not be rejected."""
    df = _frame(
        _valid_row(name="Coach A", start_week=1, end_week=7),
        _valid_row(name="Coach B", start_week=8, end_week=None),
    )

    _validate(df)  # must not raise


def test_validate_allows_different_roles_to_overlap():
    """An OC and an HC covering the same weeks is not an overlap -- overlap
    is only checked within the same (season, team, role)."""
    df = _frame(
        _valid_row(role="OC", name="Coach A", start_week=1, end_week=None),
        _valid_row(role="HC", name="Coach B", start_week=1, end_week=None),
    )

    _validate(df)  # must not raise


# --- join_coaching_staff ------------------------------------------------------------


def _oc_only() -> pl.DataFrame:
    return load_coaching_staff().filter(pl.col("role") == "OC")


def test_join_coaching_staff_attaches_the_right_season_coordinator():
    df = pl.DataFrame(
        {"season": [2024, 2025, 2026], "posteam": ["TB", "TB", "TB"], "week": [3, 3, 3]}
    )

    joined = join_coaching_staff(df, _oc_only())

    assert joined["coach_name"].to_list() == [
        "Liam Coen",
        "Josh Grizzard",
        "Zac Robinson",
    ]
    assert joined["coach_is_play_caller"].to_list() == [True, True, True]


def test_join_coaching_staff_null_for_an_uncurated_team_or_season():
    df = pl.DataFrame({"season": [2026, 1999], "posteam": ["TB", "TB"], "week": [1, 1]})

    joined = join_coaching_staff(df, _oc_only())

    assert joined["coach_name"].to_list() == ["Zac Robinson", None]


def test_join_coaching_staff_respects_a_mid_season_handoff():
    coaching_staff = pl.DataFrame(
        [
            {
                "season": 2030,
                "team": "TB",
                "role": "OC",
                "name": "Coach A",
                "is_play_caller": True,
                "start_week": 1,
                "end_week": 7,
                "source_url": "https://example.com/a",
                "notes": None,
            },
            {
                "season": 2030,
                "team": "TB",
                "role": "OC",
                "name": "Coach B",
                "is_play_caller": True,
                "start_week": 8,
                "end_week": None,
                "source_url": "https://example.com/b",
                "notes": None,
            },
        ],
        schema=_SCHEMA_OVERRIDES,
    )
    df = pl.DataFrame(
        {"season": [2030, 2030], "posteam": ["TB", "TB"], "week": [5, 12]}
    )

    joined = join_coaching_staff(df, coaching_staff)

    assert joined["coach_name"].to_list() == ["Coach A", "Coach B"]


def test_join_coaching_staff_respects_a_custom_team_column():
    df = pl.DataFrame({"season": [2026], "team": ["TB"], "week": [1]})

    joined = join_coaching_staff(df, _oc_only(), team_column="team")

    assert joined["coach_name"].item() == "Zac Robinson"


def test_join_coaching_staff_raises_when_not_pre_filtered_to_one_role():
    coaching_staff = pl.DataFrame(
        [
            {
                "season": 2030,
                "team": "TB",
                "role": "OC",
                "name": "Coach A",
                "is_play_caller": True,
                "start_week": 1,
                "end_week": None,
                "source_url": "https://example.com/a",
                "notes": None,
            },
            {
                "season": 2030,
                "team": "TB",
                "role": "HC",
                "name": "Coach B",
                "is_play_caller": False,
                "start_week": 1,
                "end_week": None,
                "source_url": "https://example.com/b",
                "notes": None,
            },
        ],
        schema=_SCHEMA_OVERRIDES,
    )
    df = pl.DataFrame({"season": [2030], "posteam": ["TB"], "week": [1]})

    with pytest.raises(ValueError, match="more than one coaching_staff row"):
        join_coaching_staff(df, coaching_staff)


def test_join_coaching_staff_raises_on_missing_week_column():
    df = pl.DataFrame({"season": [2026], "posteam": ["TB"]})

    with pytest.raises(ValueError, match="'season' and 'week'"):
        join_coaching_staff(df, _oc_only())


def test_join_coaching_staff_raises_on_missing_team_column():
    df = pl.DataFrame({"season": [2026], "week": [1]})

    with pytest.raises(ValueError, match="posteam"):
        join_coaching_staff(df, _oc_only())
