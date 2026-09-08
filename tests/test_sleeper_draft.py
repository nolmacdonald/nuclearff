"""Unit tests for flattening draft picks and persisting them to DuckDB.

The draft/pick shapes mirror this project's real, currently in-progress 2026
startup draft (``draft_id=1367225133646778368``), captured live -- including
its real ``settings.reversal_round: 3`` behavior (round 3 continues round
2's ``draft_slot`` direction instead of reversing), confirmed against real
picks 20 (round 2, ``draft_slot`` 1) and 21 (round 3, ``draft_slot`` 10). All
HTTP is mocked with ``responses``.
"""

from __future__ import annotations

import duckdb
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.draft import (
    TABLE_NAME,
    draft_pick_rows,
    fetch_and_write_draft_picks,
)
from tests.conftest import TEST_BASE_URL

DRAFT_ID = "1367225133646778368"
LEAGUE_ID = "1367225133634191360"

DRAFT = {
    "draft_id": DRAFT_ID,
    "league_id": LEAGUE_ID,
    "season": "2026",
    "type": "snake",
    "settings": {"teams": 10, "rounds": 15, "reversal_round": 3},
}

# A handful of real picks from this project's real, in-progress draft --
# round 1 (plain snake), and rounds 2/3, whose real draft_slot values confirm
# settings.reversal_round: 3 keeps round 3 in the *same* column direction as
# round 2 rather than reversing back to round 1's.
PICKS = [
    {
        "draft_id": DRAFT_ID,
        "pick_no": 1,
        "round": 1,
        "draft_slot": 1,
        "roster_id": 6,
        "picked_by": "469921263066804224",
        "player_id": "9221",
        "is_keeper": None,
        "metadata": {
            "position": "RB",
            "first_name": "Jahmyr",
            "last_name": "Gibbs",
            "team": "DET",
        },
    },
    {
        "draft_id": DRAFT_ID,
        "pick_no": 20,
        "round": 2,
        "draft_slot": 1,
        "roster_id": 6,
        "picked_by": "469921263066804224",
        "player_id": "4988",
        "is_keeper": None,
        "metadata": {
            "position": "RB",
            "first_name": "Bucky",
            "last_name": "Irving",
            "team": "TB",
        },
    },
    {
        "draft_id": DRAFT_ID,
        "pick_no": 21,
        "round": 3,
        "draft_slot": 10,
        "roster_id": 10,
        "picked_by": "996141190049394688",
        "player_id": "11604",
        "is_keeper": None,
        "metadata": {
            "position": "TE",
            "first_name": "Brock",
            "last_name": "Bowers",
            "team": "LV",
        },
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


# --- draft_pick_rows ---------------------------------------------------


def test_draft_pick_rows_flattens_metadata_and_league_fields():
    rows = {row["pick_no"]: row for row in draft_pick_rows(DRAFT, PICKS)}

    assert rows[1]["position"] == "RB"
    assert rows[1]["last_name"] == "Gibbs"
    assert rows[1]["league_id"] == LEAGUE_ID
    assert rows[1]["season"] == 2026


def test_draft_pick_rows_keeps_the_real_draft_slot_through_a_reversal_round():
    """draft_slot alone (no computed direction) reflects reversal_round: 3."""
    rows = {row["pick_no"]: row for row in draft_pick_rows(DRAFT, PICKS)}

    assert rows[1]["draft_slot"] == 1  # round 1, slot 1
    assert rows[20]["draft_slot"] == 1  # round 2 ends back at slot 1
    assert rows[21]["draft_slot"] == 10  # round 3 continues at slot 10, not 1


def test_draft_pick_rows_skips_picks_without_a_pick_no():
    rows = draft_pick_rows(DRAFT, [{"metadata": {}}])

    assert rows == []


def test_draft_pick_rows_handles_missing_metadata():
    rows = draft_pick_rows(DRAFT, [{"pick_no": 1, "round": 1, "draft_slot": 1}])

    assert rows[0]["position"] is None
    assert rows[0]["last_name"] is None


# --- fetch_and_write_draft_picks ----------------------------------------


@responses.activate
def test_fetch_and_write_draft_picks_round_trips_through_duckdb(client, tmp_path):
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=PICKS)
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_draft_picks(client, DRAFT_ID, db_path)

    assert count == len(PICKS)
    with duckdb.connect(str(db_path)) as conn:
        (draft_slot,) = conn.execute(
            f"SELECT draft_slot FROM {TABLE_NAME} WHERE pick_no = 21"
        ).fetchone()
    assert draft_slot == 10


@responses.activate
def test_fetch_and_write_draft_picks_before_any_pick_is_made(client, tmp_path):
    """An empty picks list (draft not started) is valid data, not an error."""
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    db_path = tmp_path / "nuclearff.duckdb"

    count = fetch_and_write_draft_picks(client, DRAFT_ID, db_path)

    assert count == 0
