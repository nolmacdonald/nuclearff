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
import polars as pl
import pytest
import responses

from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.draft import (
    TABLE_NAME,
    draft_order_stats,
    draft_pick_rows,
    fetch_and_write_all_drafts,
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


@responses.activate
def test_fetch_and_write_draft_picks_does_not_erase_a_sibling_draft(client, tmp_path):
    """A league can have more than one draft in a season (see
    fetch_and_write_all_drafts's docstring) -- writing one draft's picks
    must not erase a *different* draft's picks that happen to share the
    same league_id, which is why this scopes its replace by draft_id, not
    league_id."""
    sibling_draft_id = "sibling-draft"
    sibling_draft = {**DRAFT, "draft_id": sibling_draft_id}
    sibling_picks = [{**PICKS[0], "draft_id": sibling_draft_id}]
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=PICKS)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{sibling_draft_id}", json=sibling_draft)
    responses.get(
        f"{TEST_BASE_URL}/v1/draft/{sibling_draft_id}/picks", json=sibling_picks
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_draft_picks(client, DRAFT_ID, db_path)
    fetch_and_write_draft_picks(client, sibling_draft_id, db_path)

    with duckdb.connect(str(db_path)) as conn:
        draft_ids = {
            row[0]
            for row in conn.execute(f"SELECT draft_id FROM {TABLE_NAME}").fetchall()
        }
    assert draft_ids == {DRAFT_ID, sibling_draft_id}


# --- fetch_and_write_all_drafts (GitHub Issue 85) -----------------------

_OTHER_DRAFT_ID = "999"
_OTHER_LEAGUE_ID = "888"
_OTHER_DRAFT = {
    "draft_id": _OTHER_DRAFT_ID,
    "league_id": _OTHER_LEAGUE_ID,
    "season": "2025",
    "type": "snake",
}
_OTHER_PICKS = [
    {
        "draft_id": _OTHER_DRAFT_ID,
        "pick_no": 1,
        "round": 1,
        "draft_slot": 3,
        "roster_id": 1,
        "picked_by": "u1",
        "player_id": "1",
        "metadata": {},
    },
]


@responses.activate
def test_fetch_and_write_all_drafts_does_not_erase_an_earlier_season(client, tmp_path):
    """The bug fetch_and_write_draft_picks would have if called in a loop."""
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[{"draft_id": DRAFT_ID}]
    )
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=PICKS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_OTHER_LEAGUE_ID}/drafts",
        json=[{"draft_id": _OTHER_DRAFT_ID}],
    )
    responses.get(f"{TEST_BASE_URL}/v1/draft/{_OTHER_DRAFT_ID}", json=_OTHER_DRAFT)
    responses.get(
        f"{TEST_BASE_URL}/v1/draft/{_OTHER_DRAFT_ID}/picks", json=_OTHER_PICKS
    )
    db_path = tmp_path / "nuclearff.duckdb"
    leagues = [
        {"league_id": LEAGUE_ID, "season": 2026},
        {"league_id": _OTHER_LEAGUE_ID, "season": 2025},
    ]

    count = fetch_and_write_all_drafts(client, leagues, db_path)

    assert count == len(PICKS) + len(_OTHER_PICKS)
    with duckdb.connect(str(db_path)) as conn:
        seasons = conn.execute(f"SELECT DISTINCT season FROM {TABLE_NAME}").fetchall()
    assert {s for (s,) in seasons} == {2026, 2025}


@responses.activate
def test_fetch_and_write_all_drafts_skips_a_league_whose_drafts_fail(client, tmp_path):
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[{"draft_id": DRAFT_ID}]
    )
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=PICKS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{_OTHER_LEAGUE_ID}/drafts", status=500)
    db_path = tmp_path / "nuclearff.duckdb"
    leagues = [
        {"league_id": LEAGUE_ID, "season": 2026},
        {"league_id": _OTHER_LEAGUE_ID, "season": 2025},
    ]

    count = fetch_and_write_all_drafts(client, leagues, db_path)

    assert count == len(PICKS)


@responses.activate
def test_fetch_and_write_all_drafts_does_not_erase_a_different_calls_league(
    client, tmp_path
):
    """Real bug fixed 2026-09-10: calling this once per league (the CLI's
    real usage pattern -- one `sleeper fetch-league --drafts` invocation
    per league_id, not one combined call across leagues) used to silently
    erase the previous call's league entirely, confirmed live across three
    real leagues on the same account."""
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[{"draft_id": DRAFT_ID}]
    )
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=DRAFT)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=PICKS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{_OTHER_LEAGUE_ID}/drafts",
        json=[{"draft_id": _OTHER_DRAFT_ID}],
    )
    responses.get(f"{TEST_BASE_URL}/v1/draft/{_OTHER_DRAFT_ID}", json=_OTHER_DRAFT)
    responses.get(
        f"{TEST_BASE_URL}/v1/draft/{_OTHER_DRAFT_ID}/picks", json=_OTHER_PICKS
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_all_drafts(
        client, [{"league_id": LEAGUE_ID, "season": 2026}], db_path
    )
    fetch_and_write_all_drafts(
        client, [{"league_id": _OTHER_LEAGUE_ID, "season": 2025}], db_path
    )

    with duckdb.connect(str(db_path)) as conn:
        league_ids = {
            row[0]
            for row in conn.execute(f"SELECT league_id FROM {TABLE_NAME}").fetchall()
        }
    assert league_ids == {LEAGUE_ID, _OTHER_LEAGUE_ID}


# --- draft_order_stats (GitHub Issue 85) ---------------------------------


def _picks(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "draft_id": pl.String,
        "round": pl.Int64,
        "draft_slot": pl.Int64,
        "roster_id": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def _standings(rows: list[dict]) -> pl.DataFrame:
    schema = {"league_id": pl.String, "roster_id": pl.Int64, "display_name": pl.String}
    return pl.DataFrame(rows, schema=schema)


def test_draft_order_stats_computes_avg_first_and_last():
    """A manager who drafted 1st once (of 4 real teams) and last once."""
    picks = _picks(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "draft_id": "D1",
                "round": 1,
                "draft_slot": 1,
                "roster_id": 1,
            },
            {
                "league_id": "L2",
                "season": 2025,
                "draft_id": "D2",
                "round": 1,
                "draft_slot": 4,
                "roster_id": 1,
            },
            # Other real rosters in each draft, needed so draft_slot's real
            # per-season max (the "last pick" comparison) isn't just this
            # one manager's own value.
            {
                "league_id": "L1",
                "season": 2024,
                "draft_id": "D1",
                "round": 1,
                "draft_slot": 4,
                "roster_id": 2,
            },
            {
                "league_id": "L2",
                "season": 2025,
                "draft_id": "D2",
                "round": 1,
                "draft_slot": 1,
                "roster_id": 2,
            },
        ]
    )
    standings = _standings(
        [
            {"league_id": "L1", "roster_id": 1, "display_name": "nolmacdonald"},
            {"league_id": "L2", "roster_id": 1, "display_name": "nolmacdonald"},
            {"league_id": "L1", "roster_id": 2, "display_name": "hyoga10"},
            {"league_id": "L2", "roster_id": 2, "display_name": "hyoga10"},
        ]
    )

    stats = draft_order_stats(picks, standings).filter(
        pl.col("manager") == "nolmacdonald"
    )

    assert stats["seasons_drafted"].item() == 2
    assert stats["avg_draft_position"].item() == 2.5
    assert stats["times_first_pick"].item() == 1
    assert stats["times_last_pick"].item() == 1


def test_draft_order_stats_ignores_rounds_after_the_first():
    """A roster's draft_slot is constant across rounds -- reading only round 1
    must not double-count a manager's seasons."""
    picks = _picks(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "draft_id": "D1",
                "round": 1,
                "draft_slot": 1,
                "roster_id": 1,
            },
            {
                "league_id": "L1",
                "season": 2024,
                "draft_id": "D1",
                "round": 2,
                "draft_slot": 1,
                "roster_id": 1,
            },
        ]
    )
    standings = _standings([{"league_id": "L1", "roster_id": 1, "display_name": "a"}])

    stats = draft_order_stats(picks, standings)

    assert stats["seasons_drafted"].item() == 1


def test_draft_order_stats_skips_an_unresolvable_roster():
    picks = _picks(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "draft_id": "D1",
                "round": 1,
                "draft_slot": 1,
                "roster_id": 99,
            },
        ]
    )
    standings = _standings([{"league_id": "L1", "roster_id": 1, "display_name": "a"}])

    assert draft_order_stats(picks, standings).height == 0


def test_draft_order_stats_handles_no_picks():
    assert draft_order_stats(_picks([]), _standings([])).height == 0
