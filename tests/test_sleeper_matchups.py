"""Unit tests for fetching and persisting weekly matchups.

All HTTP is mocked with ``responses``, matching every other Sleeper-client
test in this repo.
"""

from __future__ import annotations

import json

import duckdb
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.matchups import (
    TABLE_NAME,
    fetch_and_write_matchups,
    matchup_rows,
)
from tests.conftest import TEST_BASE_URL

LEAGUE_ID = "1240509989819273216"

MATCHUPS_WEEK_1 = [
    {
        "roster_id": 1,
        "matchup_id": 4,
        "points": 123.62,
        "custom_points": None,
        "players": ["1", "2"],
        "starters": ["1"],
        "starters_points": [7.82],
        "players_points": {"1": 7.82, "2": 0.0},
    },
    {
        "roster_id": 2,
        "matchup_id": 4,
        "points": 110.0,
        "custom_points": None,
        "players": ["3"],
        "starters": ["3"],
        "starters_points": [15.0],
        "players_points": {"3": 15.0},
    },
]


@pytest.fixture
def client(tmp_path):
    """A client pointed at a fake base URL with throttling and backoff disabled."""
    with SleeperClient(
        cache_dir=tmp_path / "cache",
        base_url=TEST_BASE_URL,
        min_interval=0.0,
        backoff_factor=0.0,
    ) as sleeper:
        yield sleeper


# --- matchup_rows -----------------------------------------------------------


def test_matchup_rows_flattens_one_roster_per_row():
    rows = matchup_rows(LEAGUE_ID, 2025, 1, MATCHUPS_WEEK_1)

    assert len(rows) == 2
    assert rows[0]["roster_id"] == 1
    assert rows[0]["week"] == 1
    assert rows[0]["league_id"] == LEAGUE_ID


def test_matchup_rows_encodes_nested_fields_as_json():
    rows = matchup_rows(LEAGUE_ID, 2025, 1, MATCHUPS_WEEK_1)

    assert json.loads(rows[0]["players"]) == ["1", "2"]
    assert json.loads(rows[0]["players_points"]) == {"1": 7.82, "2": 0.0}


# --- fetch_and_write_matchups ------------------------------------------------


@responses.activate
def test_fetch_and_write_matchups_stops_after_the_last_played_week(client, tmp_path):
    """Weeks with no matchups yet are skipped, not written as empty rows."""
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/1", json=MATCHUPS_WEEK_1
    )
    for week in range(2, 19):
        responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/{week}", json=[])
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_matchups(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert count == 2
    with duckdb.connect(str(db_path)) as conn:
        weeks = {
            row[0] for row in conn.execute(f"SELECT week FROM {TABLE_NAME}").fetchall()
        }
    assert weeks == {1}


@responses.activate
def test_fetch_and_write_matchups_skips_a_failed_week_without_aborting(
    client, tmp_path
):
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/1", json=MATCHUPS_WEEK_1
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/2", status=500)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/2", status=500)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/2", status=500)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/2", status=500)
    for week in range(3, 19):
        responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/{week}", json=[])
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_matchups(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path, max_week=18
    )

    assert count == 2


@responses.activate
def test_fetch_and_write_matchups_respects_max_week(client, tmp_path):
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/1", json=MATCHUPS_WEEK_1
    )
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_matchups(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path, max_week=1
    )

    assert count == 2
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_and_write_matchups_does_not_erase_another_leagues_rows(client, tmp_path):
    """Real bug fixed 2026-09-10: fetching a second, unrelated league used
    to silently erase every row from a previously-fetched, different
    league -- confirmed live across three real leagues on the same
    account, since the old write always dropped the whole shared table
    first."""
    other_league_id = "9999999999"
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/1", json=MATCHUPS_WEEK_1
    )
    for week in range(2, 19):
        responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/matchups/{week}", json=[])
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{other_league_id}/matchups/1",
        json=[MATCHUPS_WEEK_1[0]],
    )
    for week in range(2, 19):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{other_league_id}/matchups/{week}", json=[]
        )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_matchups(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )
    fetch_and_write_matchups(
        client, [{"league_id": other_league_id, "season": 2025}], db_path
    )

    with duckdb.connect(str(db_path)) as conn:
        league_ids = {
            row[0]
            for row in conn.execute(f"SELECT league_id FROM {TABLE_NAME}").fetchall()
        }
    assert league_ids == {LEAGUE_ID, other_league_id}
