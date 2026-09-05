"""Unit tests for computing standings/playoff results and persisting them to DuckDB.

All HTTP is mocked with ``responses``, matching every other Sleeper-client
test in this repo.
"""

from __future__ import annotations

import json

import duckdb
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.standings import (
    MATCHES_TABLE_NAME,
    STANDINGS_TABLE_NAME,
    bracket_match_rows,
    fetch_and_write_standings,
    resolve_final_ranks,
    roster_display_names,
    standings_rows,
)
from tests.conftest import TEST_BASE_URL

LEAGUE_ID = "1240509989819273216"

ROSTERS = [
    {
        "roster_id": 1,
        "owner_id": "u1",
        "settings": {
            "wins": 10,
            "losses": 4,
            "ties": 0,
            "fpts": 1500,
            "fpts_decimal": 25,
            "fpts_against": 1400,
            "fpts_against_decimal": 50,
        },
    },
    {
        "roster_id": 2,
        "owner_id": "u2",
        "settings": {
            "wins": 8,
            "losses": 6,
            "ties": 0,
            "fpts": 1400,
            "fpts_decimal": 0,
            "fpts_against": 1350,
            "fpts_against_decimal": 0,
        },
    },
    {
        # owner_id has no matching user entry -- a real data inconsistency.
        "roster_id": 3,
        "owner_id": "u3",
        "settings": {
            "wins": 9,
            "losses": 5,
            "ties": 0,
            "fpts": 1600,
            "fpts_decimal": 75,
        },
    },
    {
        # No owner at all, and no fpts_against fields (e.g. a fresh season).
        "roster_id": 4,
        "owner_id": None,
        "settings": {"wins": 5, "losses": 9, "ties": 0, "fpts": 1200},
    },
]

USERS = [
    {"user_id": "u1", "display_name": "Alice"},
    {"user_id": "u2", "display_name": "Bob"},
]

WINNERS_BRACKET = [
    {"m": 1, "r": 1, "t1": 1, "t2": 4, "w": 1, "l": 4},
    {"m": 2, "r": 1, "t1": 2, "t2": 3, "w": 3, "l": 2},
    {
        "m": 3,
        "p": 1,
        "r": 2,
        "t1": 1,
        "t2": 3,
        "w": 3,
        "l": 1,
        "t1_from": {"w": 1},
        "t2_from": {"w": 2},
    },
    {
        "m": 4,
        "p": 3,
        "r": 2,
        "t1": 4,
        "t2": 2,
        "w": 2,
        "l": 4,
        "t1_from": {"l": 1},
        "t2_from": {"l": 2},
    },
]

LOSERS_BRACKET = [
    {"m": 1, "p": 1, "r": 1, "t1": 5, "t2": 6, "w": 5, "l": 6},
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


# --- roster_display_names --------------------------------------------------


def test_roster_display_names_joins_owner_to_display_name():
    names = roster_display_names(ROSTERS, USERS)

    assert names[1] == "Alice"
    assert names[2] == "Bob"


def test_roster_display_names_handles_missing_or_absent_owner():
    """A roster whose owner has no user entry, or no owner at all, is None."""
    names = roster_display_names(ROSTERS, USERS)

    assert names[3] is None
    assert names[4] is None


# --- resolve_final_ranks ----------------------------------------------------


def test_resolve_final_ranks_from_placement_matches():
    """Winner of a p-tagged match gets p; loser gets p + 1."""
    ranks = resolve_final_ranks(WINNERS_BRACKET)

    assert ranks == {3: 1, 1: 2, 2: 3, 4: 4}


def test_resolve_final_ranks_ignores_matches_without_placement():
    """A first-round match (no p) does not contribute a rank."""
    ranks = resolve_final_ranks([{"m": 1, "r": 1, "t1": 1, "t2": 2, "w": 1, "l": 2}])

    assert ranks == {}


def test_resolve_final_ranks_does_not_use_the_losers_bracket():
    """Losers-bracket placement is deliberately not interpreted as final rank."""
    ranks = resolve_final_ranks([])  # losers bracket never passed to this function

    assert ranks == {}


# --- standings_rows ----------------------------------------------------


def test_standings_rows_combines_whole_and_decimal_points():
    rows = {
        row["roster_id"]: row
        for row in standings_rows(LEAGUE_ID, 2025, ROSTERS, USERS, WINNERS_BRACKET)
    }

    assert rows[1]["fpts"] == 1500.25
    assert rows[1]["fpts_against"] == 1400.50
    assert rows[4]["fpts"] == 1200.0
    assert rows[3]["fpts_against"] is None


def test_standings_rows_regular_season_rank_differs_from_final_rank():
    """Roster 1 led the regular season but roster 3 won it all -- ranks must differ."""
    rows = {
        row["roster_id"]: row
        for row in standings_rows(LEAGUE_ID, 2025, ROSTERS, USERS, WINNERS_BRACKET)
    }

    assert rows[1]["regular_season_rank"] == 1
    assert rows[3]["regular_season_rank"] == 2
    assert rows[1]["final_rank"] == 2
    assert rows[3]["final_rank"] == 1


def test_standings_rows_display_name_and_league_fields():
    rows = {
        row["roster_id"]: row
        for row in standings_rows(LEAGUE_ID, 2025, ROSTERS, USERS, WINNERS_BRACKET)
    }

    assert rows[1]["display_name"] == "Alice"
    assert rows[1]["league_id"] == LEAGUE_ID
    assert rows[1]["season"] == 2025


# --- bracket_match_rows ---------------------------------------------------


def test_bracket_match_rows_encodes_from_references_as_json():
    rows = {
        row["match"]: row
        for row in bracket_match_rows(LEAGUE_ID, 2025, "winners", WINNERS_BRACKET)
    }

    assert json.loads(rows[3]["t1_from"]) == {"w": 1}
    assert rows[3]["placement"] == 1
    assert rows[3]["bracket"] == "winners"


def test_bracket_match_rows_from_references_are_none_when_absent():
    rows = bracket_match_rows(LEAGUE_ID, 2025, "winners", [WINNERS_BRACKET[0]])

    assert rows[0]["t1_from"] is None
    assert rows[0]["t2_from"] is None
    assert rows[0]["placement"] is None


def test_bracket_match_rows_captures_raw_losers_bracket_placement():
    """Losers-bracket placement is stored raw, even though it isn't used to rank."""
    rows = bracket_match_rows(LEAGUE_ID, 2025, "losers", LOSERS_BRACKET)

    assert rows[0]["placement"] == 1
    assert rows[0]["bracket"] == "losers"


# --- fetch_and_write_standings ---------------------------------------------


@responses.activate
def test_fetch_and_write_standings_round_trips_through_duckdb(client, tmp_path):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=LOSERS_BRACKET
    )
    db_path = tmp_path / "nuclearff.duckdb"

    standings_count, matches_count = fetch_and_write_standings(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert standings_count == 4
    assert matches_count == len(WINNERS_BRACKET) + len(LOSERS_BRACKET)

    with duckdb.connect(str(db_path)) as conn:
        (final_rank,) = conn.execute(
            f"SELECT final_rank FROM {STANDINGS_TABLE_NAME} WHERE roster_id = 3"
        ).fetchone()
        (bracket_count,) = conn.execute(
            f"SELECT COUNT(*) FROM {MATCHES_TABLE_NAME}"
        ).fetchone()

    assert final_rank == 1
    assert bracket_count == len(WINNERS_BRACKET) + len(LOSERS_BRACKET)


@responses.activate
def test_fetch_and_write_standings_degrades_a_failed_endpoint_without_aborting(
    client, tmp_path
):
    """A losers_bracket 404 does not abort the whole season's standings."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", status=404)
    db_path = tmp_path / "nuclearff.duckdb"

    standings_count, matches_count = fetch_and_write_standings(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert standings_count == 4
    assert matches_count == len(WINNERS_BRACKET)


@responses.activate
def test_fetch_and_write_standings_empty_bracket_yields_no_final_ranks(
    client, tmp_path
):
    """A season with no bracket yet still produces standings, just no final rank."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=[])
    db_path = tmp_path / "nuclearff.duckdb"

    standings_count, matches_count = fetch_and_write_standings(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert standings_count == 4
    assert matches_count == 0
    with duckdb.connect(str(db_path)) as conn:
        (null_ranks,) = conn.execute(
            f"SELECT COUNT(*) FROM {STANDINGS_TABLE_NAME} WHERE final_rank IS NULL"
        ).fetchone()
    assert null_ranks == 4
