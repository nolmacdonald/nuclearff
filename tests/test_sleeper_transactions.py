"""Unit tests for fetching and persisting weekly transactions.

Sample payloads below are real transactions captured from the live Sleeper
API this session (a waiver claim that failed, one that completed, and a
real trade), not synthetic fixtures -- matching this repo's convention of
grounding schemas in real data. All HTTP is mocked with ``responses``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.transactions import (
    PLAYERS_TABLE_NAME,
    TABLE_NAME,
    fetch_and_write_transactions,
    transaction_player_rows,
    transaction_rows,
)
from tests.conftest import TEST_BASE_URL

LEAGUE_ID = "1240509989819273216"

WAIVER_FAILED = {
    "status": "failed",
    "type": "waiver",
    "metadata": {"notes": "This player was claimed by another owner."},
    "created": 1757480252125,
    "settings": {"seq": 19, "waiver_bid": 0},
    "leg": 1,
    "draft_picks": [],
    "creator": "451502081426059264",
    "transaction_id": "1271367961197711360",
    "adds": {"9754": 5},
    "consenter_ids": [5],
    "drops": None,
    "roster_ids": [5],
    "status_updated": 1757488445297,
    "waiver_budget": [],
}

WAIVER_COMPLETE = {
    "status": "complete",
    "type": "waiver",
    "metadata": {"notes": "Your waiver claim was processed successfully!"},
    "created": 1757473191611,
    "settings": {"seq": 5, "waiver_bid": 16},
    "leg": 1,
    "draft_picks": [],
    "creator": "332632476830679040",
    "transaction_id": "1271338347251388416",
    "adds": {"12490": 3},
    "consenter_ids": [3],
    "drops": {"12474": 3},
    "roster_ids": [3],
    "status_updated": 1757488445297,
    "waiver_budget": [],
}

TRADE = {
    "status": "complete",
    "type": "trade",
    "metadata": None,
    "created": 1762303727750,
    "settings": {"expires_at": 1762476527},
    "leg": 9,
    "draft_picks": [],
    "creator": "332632476830679040",
    "transaction_id": "1291599084301348864",
    "adds": {"7525": 6, "7526": 6, "9509": 3},
    "consenter_ids": [3, 6],
    "drops": {"7525": 3, "7526": 3, "9509": 6},
    "roster_ids": [3, 6],
    "status_updated": 1762457470827,
    "waiver_budget": [],
}

ROSTER_NAMES = {3: "nolmacdonald", 5: "hyoga10", 6: "Donkeysride"}
USER_NAMES = {
    "332632476830679040": "nolmacdonald",
    "451502081426059264": "aperry151",
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


# --- transaction_rows -------------------------------------------------


def test_transaction_rows_captures_type_and_status():
    rows = {
        row["transaction_id"]: row
        for row in transaction_rows(
            LEAGUE_ID, 2025, 1, [WAIVER_FAILED, TRADE], ROSTER_NAMES, USER_NAMES
        )
    }

    assert rows["1271367961197711360"]["type"] == "waiver"
    assert rows["1271367961197711360"]["status"] == "failed"
    assert rows["1291599084301348864"]["type"] == "trade"
    assert rows["1291599084301348864"]["status"] == "complete"


def test_transaction_rows_converts_epoch_milliseconds_to_utc():
    row = transaction_rows(LEAGUE_ID, 2025, 1, [WAIVER_FAILED], {}, {})[0]

    assert row["created_at"] == datetime.fromtimestamp(1757480252125 / 1000, tz=UTC)
    assert row["status_updated_at"] == datetime.fromtimestamp(
        1757488445297 / 1000, tz=UTC
    )


def test_transaction_rows_resolves_creator_and_roster_display_names():
    row = transaction_rows(LEAGUE_ID, 2025, 1, [TRADE], ROSTER_NAMES, USER_NAMES)[0]

    assert row["creator_display_name"] == "nolmacdonald"
    assert json.loads(row["roster_display_names"]) == ["nolmacdonald", "Donkeysride"]
    assert json.loads(row["consenter_display_names"]) == [
        "nolmacdonald",
        "Donkeysride",
    ]


def test_transaction_rows_handles_null_drops_and_missing_metadata():
    """A failed waiver has drops=None; a trade in real data had metadata=None."""
    rows = transaction_rows(LEAGUE_ID, 2025, 1, [WAIVER_FAILED, TRADE], {}, {})

    assert json.loads(rows[0]["drops"]) == {}
    assert json.loads(rows[1]["metadata"]) == {}


# --- transaction_player_rows -------------------------------------------------


def test_transaction_player_rows_unnests_a_real_trade():
    """A real trade: 3 players added, 3 dropped, across two rosters."""
    rows = transaction_player_rows(LEAGUE_ID, 2025, 9, [TRADE])

    adds = [row for row in rows if row["direction"] == "add"]
    drops = [row for row in rows if row["direction"] == "drop"]
    assert len(adds) == 3
    assert len(drops) == 3
    assert {row["player_id"] for row in adds} == {"7525", "7526", "9509"}
    assert {row["roster_id"] for row in adds} == {6, 3}


def test_transaction_player_rows_skips_null_drops():
    rows = transaction_player_rows(LEAGUE_ID, 2025, 1, [WAIVER_FAILED])

    assert len(rows) == 1
    assert rows[0]["direction"] == "add"
    assert rows[0]["player_id"] == "9754"


# --- fetch_and_write_transactions ---------------------------------------------


@responses.activate
def test_fetch_and_write_transactions_round_trips_through_duckdb(client, tmp_path):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=[])
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/1",
        json=[WAIVER_FAILED, WAIVER_COMPLETE],
    )
    for week in range(2, 19):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/{week}", json=[]
        )
    db_path = tmp_path / "nuclearff.duckdb"

    transaction_count, player_count = fetch_and_write_transactions(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert transaction_count == 2
    assert player_count == 3  # 1 add (failed) + 1 add + 1 drop (complete)

    with duckdb.connect(str(db_path)) as conn:
        (count,) = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()
        (player_row_count,) = conn.execute(
            f"SELECT COUNT(*) FROM {PLAYERS_TABLE_NAME}"
        ).fetchone()
    assert count == 2
    assert player_row_count == 3


@responses.activate
def test_fetch_and_write_transactions_does_not_erase_another_leagues_rows(
    client, tmp_path
):
    """Real bug fixed 2026-09-10: fetching a second, unrelated league used
    to silently erase every row from a previously-fetched, different
    league -- confirmed live across three real leagues on the same
    account, since the old write always dropped the whole shared table
    first."""
    other_league_id = "9999999999"
    other_waiver = {**WAIVER_COMPLETE, "transaction_id": "other-league-txn"}
    for lid, waiver in ((LEAGUE_ID, WAIVER_COMPLETE), (other_league_id, other_waiver)):
        responses.get(f"{TEST_BASE_URL}/v1/league/{lid}/rosters", json=[])
        responses.get(f"{TEST_BASE_URL}/v1/league/{lid}/users", json=[])
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{lid}/transactions/1",
            json=[waiver],
        )
        for week in range(2, 19):
            responses.get(
                f"{TEST_BASE_URL}/v1/league/{lid}/transactions/{week}", json=[]
            )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_transactions(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )
    fetch_and_write_transactions(
        client, [{"league_id": other_league_id, "season": 2025}], db_path
    )

    with duckdb.connect(str(db_path)) as conn:
        league_ids = {
            row[0]
            for row in conn.execute(f"SELECT league_id FROM {TABLE_NAME}").fetchall()
        }
    assert league_ids == {LEAGUE_ID, other_league_id}


@responses.activate
def test_fetch_and_write_transactions_skips_a_failed_week_without_aborting(
    client, tmp_path
):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=[])
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/1", json=[WAIVER_FAILED]
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/2", status=500)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/2", status=500)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/2", status=500)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/2", status=500)
    for week in range(3, 19):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/{week}", json=[]
        )
    db_path = tmp_path / "nuclearff.duckdb"

    transaction_count, _ = fetch_and_write_transactions(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path, max_week=18
    )

    assert transaction_count == 1


@responses.activate
def test_fetch_and_write_transactions_respects_max_week(client, tmp_path):
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=[])
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/1", json=[WAIVER_FAILED]
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_transactions(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path, max_week=1
    )

    assert len(responses.calls) == 3  # rosters + users + week 1 only


@responses.activate
def test_fetch_and_write_transactions_degrades_rosters_users_failure(client, tmp_path):
    """A failed rosters/users fetch still processes transactions, with no names."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", status=404)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", status=404)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/1", json=[WAIVER_FAILED]
    )
    for week in range(2, 19):
        responses.get(
            f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/transactions/{week}", json=[]
        )
    db_path = tmp_path / "nuclearff.duckdb"

    transaction_count, _ = fetch_and_write_transactions(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    assert transaction_count == 1
    with duckdb.connect(str(db_path)) as conn:
        (creator_name,) = conn.execute(
            f"SELECT creator_display_name FROM {TABLE_NAME}"
        ).fetchone()
    assert creator_name is None
