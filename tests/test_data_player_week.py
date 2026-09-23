"""Unit tests for fetching and building the ``player_week`` dataset.

``build_player_week``'s fixture below is shaped after real
``nflreadpy.load_player_stats(summary_level="week")`` output (confirmed
directly against the 2024 season): ``Int32`` season/week, string player
identity columns, and one null-``player_id`` team-aggregate row per team per
week. ``fetch_player_week`` itself is exercised only through an injected
fake loader -- never a live network call, matching this project's existing
``SleeperClient`` injection tests.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from nuclearff.data.player_week import (
    build_player_week,
    current_season,
    fetch_player_week,
)
from nuclearff.exceptions import DataQualityError


def _raw_fixture() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": pl.Series([2024, 2024, 2024, 2024], dtype=pl.Int32),
            "week": pl.Series([1, 1, 19, 1], dtype=pl.Int32),
            "player_id": ["00-0023459", "00-0023853", "00-0023459", None],
            "player_name": ["A.Rodgers", "M.Prater", "A.Rodgers", None],
            "player_display_name": [
                "Aaron Rodgers",
                "Matt Prater",
                "Aaron Rodgers",
                None,
            ],
            "position": ["QB", "K", "QB", None],
            "team": ["NYJ", "ARI", "NYJ", "BAL"],
            "season_type": ["REG", "REG", "POST", "REG"],
            "fantasy_points": [12.3, 8.0, 20.1, 0.0],
        }
    )


# --- fetch_player_week ---------------------------------------------------


def test_fetch_player_week_calls_the_injected_loader_with_seasons():
    captured: list[list[int]] = []

    def _loader(seasons):
        captured.append(list(seasons))
        return _raw_fixture()

    result = fetch_player_week([2024], loader=_loader)

    assert captured == [[2024]]
    assert result.height == 4


# --- build_player_week -----------------------------------------------------


def test_build_player_week_selects_and_casts_schema_columns():
    built = build_player_week(_raw_fixture())

    assert built.columns == ["season", "week", "player_id", "player_name", "team"]
    assert built.schema["season"] == pl.Int64
    assert built.schema["week"] == pl.Int64


def test_build_player_week_drops_null_player_id_team_aggregate_rows():
    built = build_player_week(_raw_fixture())

    assert built.height == 3
    assert None not in built["player_id"].to_list()


def test_build_player_week_keeps_postseason_weeks_by_default():
    built = build_player_week(_raw_fixture())

    assert 19 in built["week"].to_list()


def test_build_player_week_through_week_filters_inclusive():
    built = build_player_week(_raw_fixture(), through_week=1)

    assert built["week"].to_list() == [1, 1]


def test_build_player_week_rejects_a_duplicate_natural_key():
    raw = _raw_fixture()
    duplicated = pl.concat([raw, raw.head(1)])

    with pytest.raises(DataQualityError):
        build_player_week(duplicated)


# --- current_season ---------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected_season"),
    [
        (date(2026, 9, 1), 2026),  # regular season kickoff
        (date(2026, 12, 31), 2026),  # still mid-season
        (date(2027, 1, 15), 2026),  # playoffs, still last year's season
        (date(2027, 2, 8), 2026),  # Super Bowl week, still last year's season
        (date(2027, 2, 28), 2026),  # last day of the cutover month
        (date(2027, 3, 1), 2027),  # cutover: next season, no games yet
        (date(2027, 8, 31), 2027),  # offseason, next season already named
    ],
)
def test_current_season_resolves_by_month(today, expected_season):
    assert current_season(today) == expected_season
