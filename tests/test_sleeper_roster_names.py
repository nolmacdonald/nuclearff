"""Unit tests for nuclearff.sleeper.roster_names (issue #130)."""

from __future__ import annotations

import duckdb
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.roster_names import (
    TABLE_NAME,
    fetch_and_write_team_names,
    roster_team_names,
    team_name_rows,
)
from tests.conftest import TEST_BASE_URL

LEAGUE_ID = "1240509989819273216"

ROSTERS = [
    {
        "roster_id": 1,
        "owner_id": "u1",
        "metadata": {"team_name": "The Bad Newz Bears"},
    },
    {
        "roster_id": 2,
        "owner_id": "u2",
        "metadata": {"team_name": ""},
    },
    {
        "roster_id": 3,
        "owner_id": "u3",
        "metadata": None,
    },
    {
        # No metadata key at all -- a roster that's never touched its name.
        "roster_id": 4,
        "owner_id": "u4",
    },
    {
        # No roster_id -- a data glitch, skipped.
        "owner_id": "u5",
        "metadata": {"team_name": "Ghost Team"},
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


# --- roster_team_names -------------------------------------------------------


def test_roster_team_names_resolves_a_real_custom_name():
    names = roster_team_names(ROSTERS)

    assert names[1] == "The Bad Newz Bears"


def test_roster_team_names_blank_string_is_none():
    names = roster_team_names(ROSTERS)

    assert names[2] is None


def test_roster_team_names_null_metadata_is_none():
    names = roster_team_names(ROSTERS)

    assert names[3] is None


def test_roster_team_names_missing_metadata_key_is_none():
    names = roster_team_names(ROSTERS)

    assert names[4] is None


def test_roster_team_names_skips_a_roster_with_no_roster_id():
    names = roster_team_names(ROSTERS)

    assert len(names) == 4


# --- team_name_rows -----------------------------------------------------------


def test_team_name_rows_shapes_one_row_per_roster():
    rows = {row["roster_id"]: row for row in team_name_rows(LEAGUE_ID, 2025, ROSTERS)}

    assert rows[1]["team_name"] == "The Bad Newz Bears"
    assert rows[1]["league_id"] == LEAGUE_ID
    assert rows[1]["season"] == 2025
    assert rows[1]["owner_id"] == "u1"
    assert rows[3]["team_name"] is None
    assert len(rows) == 4


# --- fetch_and_write_team_names -----------------------------------------------


@responses.activate
def test_fetch_and_write_team_names_round_trips_through_duckdb(client, tmp_path):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_team_names(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert count == 4
    with duckdb.connect(str(db_path)) as conn:
        (team_name,) = conn.execute(
            f"SELECT team_name FROM {TABLE_NAME} WHERE roster_id = 1"
        ).fetchone()
    assert team_name == "The Bad Newz Bears"


@responses.activate
def test_fetch_and_write_team_names_does_not_erase_another_leagues_rows(
    client, tmp_path
):
    other_league_id = "9999999999"
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{other_league_id}/rosters", json=ROSTERS)
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_team_names(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )
    fetch_and_write_team_names(
        client, [{"league_id": other_league_id, "season": 2025}], db_path
    )

    with duckdb.connect(str(db_path)) as conn:
        league_ids = {
            row[0]
            for row in conn.execute(f"SELECT league_id FROM {TABLE_NAME}").fetchall()
        }
    assert league_ids == {LEAGUE_ID, other_league_id}


@responses.activate
def test_fetch_and_write_team_names_degrades_a_failed_fetch_without_aborting(
    client, tmp_path, caplog
):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", status=404)
    db_path = tmp_path / "nuclearff.duckdb"

    with caplog.at_level("WARNING", logger="nuclearff.sleeper.roster_names"):
        count = fetch_and_write_team_names(
            client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
        )

    assert count == 0
    assert "Could not fetch rosters" in caplog.text
