"""Unit tests for categorizing roster composition and persisting it to DuckDB.

The populated-roster shape mirrors a real completed season, captured live
this session (roster 1, 17 players, 2 on IR) -- matching this repo's
convention of grounding schemas in real data. All HTTP is mocked with
``responses``.
"""

from __future__ import annotations

import duckdb
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.roster_players import (
    TABLE_NAME,
    fetch_and_write_roster_players,
    roster_player_rows,
)
from tests.conftest import TEST_BASE_URL

LEAGUE_ID = "1240509989819273216"

PRE_DRAFT_ROSTER = {
    "roster_id": 1,
    "owner_id": "723569662083825664",
    "players": [],
    # A real pre-draft league fills every empty starting slot with "0".
    "starters": ["0", "0", "0", "0", "0", "0", "0", "0", "0"],
    "reserve": [],
    "taxi": [],
}

POPULATED_ROSTER = {
    "roster_id": 1,
    "owner_id": "723569662083825664",
    "players": ["3294", "9226", "9221", "9488", "6801", "12476", "12492", "9999"],
    "starters": ["3294", "9226", "9221", "9488", "6801"],
    "reserve": ["12476", "12492"],
    "taxi": None,
}

TAXI_ROSTER = {
    "roster_id": 2,
    "owner_id": "u2",
    "players": ["1000", "2000"],
    "starters": ["1000"],
    "reserve": [],
    "taxi": ["2000"],
}


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


# --- roster_player_rows -------------------------------------------------


def test_roster_player_rows_filters_the_zero_filler():
    """Pre-draft leagues fill every empty starting slot with "0" -- never a row."""
    rows = roster_player_rows(LEAGUE_ID, 2026, [PRE_DRAFT_ROSTER])

    assert rows == []


def test_roster_player_rows_categorizes_a_real_roster():
    rows = {
        row["player_id"]: row["slot"]
        for row in roster_player_rows(LEAGUE_ID, 2025, [POPULATED_ROSTER])
    }

    assert rows["3294"] == "starter"
    assert rows["9226"] == "starter"
    assert rows["12476"] == "reserve"
    assert rows["12492"] == "reserve"
    assert rows["9999"] == "bench"


def test_roster_player_rows_a_starter_is_never_also_bench():
    rows = roster_player_rows(LEAGUE_ID, 2025, [POPULATED_ROSTER])

    starters = {row["player_id"] for row in rows if row["slot"] == "starter"}
    bench = {row["player_id"] for row in rows if row["slot"] == "bench"}
    assert starters.isdisjoint(bench)


def test_roster_player_rows_handles_a_populated_taxi_squad():
    rows = {
        row["player_id"]: row["slot"]
        for row in roster_player_rows(LEAGUE_ID, 2025, [TAXI_ROSTER])
    }

    assert rows["2000"] == "taxi"
    assert rows["1000"] == "starter"


def test_roster_player_rows_covers_the_right_player_count():
    """Verified live: a real completed roster carries 17 rostered players."""
    real_shaped_roster = dict(POPULATED_ROSTER)
    real_shaped_roster["players"] = [str(i) for i in range(17)]
    real_shaped_roster["starters"] = [str(i) for i in range(9)]
    real_shaped_roster["reserve"] = ["15", "16"]

    rows = roster_player_rows(LEAGUE_ID, 2025, [real_shaped_roster])

    assert len(rows) == 17


# --- fetch_and_write_roster_players ---------------------------------------------


@responses.activate
def test_fetch_and_write_roster_players_round_trips_through_duckdb(client, tmp_path):
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters",
        json=[POPULATED_ROSTER, TAXI_ROSTER],
    )
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_roster_players(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert count == 10  # 8 from POPULATED_ROSTER + 2 from TAXI_ROSTER
    with duckdb.connect(str(db_path)) as conn:
        (total,) = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()
        (slot,) = conn.execute(
            f"SELECT slot FROM {TABLE_NAME} WHERE player_id = '2000'"
        ).fetchone()
    assert total == 10
    assert slot == "taxi"


@responses.activate
def test_fetch_and_write_roster_players_skips_a_failed_season_without_aborting(
    client, tmp_path
):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", status=404)
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_roster_players(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert count == 0


@responses.activate
def test_fetch_and_write_roster_players_replaces_rather_than_appends(client, tmp_path):
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=[POPULATED_ROSTER]
    )
    db_path = tmp_path / "nuclearff.duckdb"
    fetch_and_write_roster_players(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=[TAXI_ROSTER])
    count = fetch_and_write_roster_players(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert count == 2
    with duckdb.connect(str(db_path)) as conn:
        (total,) = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()
    assert total == 2
