"""Unit tests for nuclearff.sleeper.projections.

All HTTP is mocked with ``responses``, matching every other Sleeper-client
test in this repo.
"""

from __future__ import annotations

import json

import duckdb
import pytest
import responses

from nuclearff.config.league import ScoringSettings
from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.projections import (
    TABLE_NAME,
    fetch_and_write_projections,
    projection_rows,
    score_projection,
    unscored_projection_keys,
)
from tests.conftest import TEST_BASE_URL

PROJECTIONS_WEEK_1 = [
    {
        "player_id": "4046",
        "team": "NE",
        "opponent": "LV",
        "position": "QB",
        "company": "rotowire",
        "updated_at": 1759765502229,
        "stats": {
            "pass_yd": 253.84,
            "pass_td": 1.8,
            "pass_int": 0.84,
            "rush_yd": 11.38,
        },
    },
    {
        "player_id": "9509",
        "team": "CIN",
        "opponent": "CLE",
        "position": "WR",
        "company": "rotowire",
        "updated_at": 1759765502229,
        "stats": {"rec": 4.8, "rec_yd": 62.3, "rec_td": 0.4},
    },
]


@pytest.fixture
def client(tmp_path):
    with SleeperClient(
        cache_dir=tmp_path / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as sleeper:
        yield sleeper


# --- projection_rows ---------------------------------------------------------


def test_projection_rows_flattens_one_row_per_player():
    rows = projection_rows(2025, 1, PROJECTIONS_WEEK_1)

    assert len(rows) == 2
    assert rows[0]["player_id"] == "4046"
    assert rows[0]["season"] == 2025
    assert rows[0]["week"] == 1


def test_projection_rows_encodes_stats_as_json():
    rows = projection_rows(2025, 1, PROJECTIONS_WEEK_1)

    assert json.loads(rows[0]["stats"])["pass_yd"] == 253.84


def test_projection_rows_skips_an_entry_with_no_player_id():
    entries = [*PROJECTIONS_WEEK_1, {"team": "SEA", "stats": {}}]

    rows = projection_rows(2025, 1, entries)

    assert len(rows) == 2


# --- score_projection ---------------------------------------------------------


def test_score_projection_applies_league_scoring_directly():
    scoring = ScoringSettings(values={"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0})

    points = score_projection({"rec": 4.8, "rec_yd": 62.3, "rec_td": 0.4}, scoring)

    assert points == pytest.approx(4.8 * 1.0 + 62.3 * 0.1 + 0.4 * 6.0)


def test_score_projection_ignores_a_stat_key_the_league_does_not_score():
    scoring = ScoringSettings(values={"rec_yd": 0.1})

    points = score_projection({"rec_yd": 62.3, "pass_yd": 253.84}, scoring)

    assert points == pytest.approx(6.23)


def test_score_projection_treats_a_missing_stat_as_zero():
    scoring = ScoringSettings(values={"pass_td": 4.0})

    points = score_projection({}, scoring)

    assert points == 0.0


# --- unscored_projection_keys -------------------------------------------------


def test_unscored_projection_keys_flags_threshold_bonuses():
    scoring = ScoringSettings(
        values={"rec": 1.0, "bonus_rec_yd_100": 3.0, "bonus_rec_wr": 0.5}
    )

    unscored = unscored_projection_keys(scoring)

    assert unscored == ["bonus_rec_wr", "bonus_rec_yd_100"]


def test_unscored_projection_keys_ignores_a_zero_valued_key():
    scoring = ScoringSettings(values={"bonus_rec_yd_100": 0.0})

    assert unscored_projection_keys(scoring) == []


# --- fetch_and_write_projections ----------------------------------------------


@responses.activate
def test_fetch_and_write_projections_writes_rows(client, tmp_path):
    responses.get(
        f"{TEST_BASE_URL}/projections/nfl/2025/1",
        json=PROJECTIONS_WEEK_1,
    )
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_projections(client, 2025, 1, db_path)

    assert count == 2
    with duckdb.connect(str(db_path)) as conn:
        weeks = {
            row[0] for row in conn.execute(f"SELECT week FROM {TABLE_NAME}").fetchall()
        }
    assert weeks == {1}


@responses.activate
def test_fetch_and_write_projections_does_not_erase_another_week(client, tmp_path):
    """The exact bug class GitHub Issue 85 found for sleeper_draft_picks:
    a per-week write must not silently erase an earlier week already
    written."""
    responses.get(
        f"{TEST_BASE_URL}/projections/nfl/2025/1",
        json=PROJECTIONS_WEEK_1,
    )
    responses.get(
        f"{TEST_BASE_URL}/projections/nfl/2025/2",
        json=[PROJECTIONS_WEEK_1[0]],
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_projections(client, 2025, 1, db_path)
    count = fetch_and_write_projections(client, 2025, 2, db_path)

    assert count == 3
    with duckdb.connect(str(db_path)) as conn:
        weeks = sorted(
            row[0] for row in conn.execute(f"SELECT week FROM {TABLE_NAME}").fetchall()
        )
    assert weeks == [1, 1, 2]


@responses.activate
def test_fetch_and_write_projections_replaces_a_refetched_week(client, tmp_path):
    responses.get(
        f"{TEST_BASE_URL}/projections/nfl/2025/1",
        json=PROJECTIONS_WEEK_1,
    )
    responses.get(
        f"{TEST_BASE_URL}/projections/nfl/2025/1",
        json=[PROJECTIONS_WEEK_1[0]],
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_projections(client, 2025, 1, db_path)
    count = fetch_and_write_projections(client, 2025, 1, db_path)

    assert count == 1
