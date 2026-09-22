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
    AVATARS_TABLE_NAME,
    MATCHES_TABLE_NAME,
    STANDINGS_TABLE_NAME,
    bracket_match_rows,
    fetch_and_write_standings,
    is_chopped_league,
    resolve_chopped_final_ranks,
    resolve_final_ranks,
    roster_display_names,
    standings_rows,
    user_avatar_rows,
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
    {"user_id": "u1", "display_name": "Alice", "avatar": "avatar-abc"},
    {"user_id": "u2", "display_name": "Bob", "avatar": None},
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

# A real completed Sleeper "Chopped" league (16 teams, lowest scorer
# eliminated weekly, no playoff bracket) -- 1262207133378695168, captured
# live while investigating GitHub Issue 34. `winners_bracket`/`losers_bracket`
# return `null` for this league type, not `[]`; the real final standing lives
# on `roster.settings.eliminated` instead (the leg/week a roster was
# chopped, absent for the eventual winner).
CHOPPED_LEAGUE_ID = "1262207133378695168"

CHOPPED_LEAGUE = {
    "league_id": CHOPPED_LEAGUE_ID,
    "season": 2025,
    "settings": {"type": 3, "last_chopped_leg": 15, "num_teams": 16},
}

_CHOPPED_ELIMINATIONS = {
    14: None,  # never eliminated -- the real league's actual winner
    2: 15,
    13: 14,
    15: 13,
    7: 12,
    12: 11,
    9: 10,
    6: 9,
    8: 8,
    5: 7,
    16: 6,
    1: 5,
    4: 4,
    3: 3,
    10: 2,
    11: 1,
}

CHOPPED_ROSTERS = [
    {
        "roster_id": roster_id,
        "owner_id": f"u{roster_id}",
        "settings": (
            {"wins": 0, "losses": 0, "ties": 0}
            if eliminated is None
            else {"wins": 0, "losses": 0, "ties": 0, "eliminated": eliminated}
        ),
    }
    for roster_id, eliminated in _CHOPPED_ELIMINATIONS.items()
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


# --- user_avatar_rows (issue #150) -------------------------------------------


def test_user_avatar_rows_builds_one_row_per_user():
    users = [
        {"user_id": "u1", "display_name": "Alice", "avatar": "abc123"},
        {"user_id": "u2", "display_name": "Bob", "avatar": None},
    ]

    rows = {row["owner_id"]: row for row in user_avatar_rows(LEAGUE_ID, 2025, users)}

    assert rows["u1"]["avatar"] == "abc123"
    assert rows["u2"]["avatar"] is None
    assert rows["u1"]["league_id"] == LEAGUE_ID
    assert rows["u1"]["season"] == 2025


def test_user_avatar_rows_skips_a_user_with_no_real_user_id():
    users = [{"user_id": None, "display_name": "ghost", "avatar": "x"}, "not-a-dict"]

    rows = user_avatar_rows(LEAGUE_ID, 2025, users)

    assert rows == []


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


# --- is_chopped_league / resolve_chopped_final_ranks ------------------------


def test_is_chopped_league_detects_the_real_chopped_league():
    assert is_chopped_league(CHOPPED_LEAGUE) is True


def test_is_chopped_league_false_for_a_normal_league():
    assert is_chopped_league({"settings": {"type": 0}}) is False


def test_is_chopped_league_requires_both_signals():
    """type == 3 alone, with no last_chopped_leg, is not enough to be sure."""
    assert is_chopped_league({"settings": {"type": 3}}) is False


def test_is_chopped_league_handles_missing_or_malformed_settings():
    assert is_chopped_league({}) is False
    assert is_chopped_league({"settings": None}) is False


def test_resolve_chopped_final_ranks_orders_by_elimination_leg():
    """Confirmed against all 16 rosters of the real league: no gaps or repeats."""
    ranks = resolve_chopped_final_ranks(CHOPPED_ROSTERS)

    assert ranks[14] == 1  # never eliminated -- the real winner
    assert ranks[2] == 2  # eliminated leg 15, chopped last (runner-up)
    assert ranks[11] == 16  # eliminated leg 1, chopped first
    assert sorted(ranks.values()) == list(range(1, 17))


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


def test_standings_rows_falls_back_to_chopped_ranks_when_the_bracket_is_empty():
    """A Chopped league's empty winners_bracket falls back to `eliminated`."""
    rows = {
        row["roster_id"]: row
        for row in standings_rows(
            CHOPPED_LEAGUE_ID,
            2025,
            CHOPPED_ROSTERS,
            [],
            [],
            league=CHOPPED_LEAGUE,
        )
    }

    assert rows[14]["final_rank"] == 1
    assert rows[2]["final_rank"] == 2
    assert rows[11]["final_rank"] == 16


def test_standings_rows_does_not_misapply_the_chopped_fallback_to_a_normal_league():
    """A normal league's genuinely empty bracket (season in progress) stays NULL."""
    normal_league = {"league_id": LEAGUE_ID, "season": 2025, "settings": {"type": 0}}
    rows = {
        row["roster_id"]: row
        for row in standings_rows(
            LEAGUE_ID, 2025, ROSTERS, USERS, [], league=normal_league
        )
    }

    assert all(row["final_rank"] is None for row in rows.values())


def test_standings_rows_without_a_league_argument_never_applies_the_fallback():
    """Omitting `league` keeps `final_rank` NULL even for a Chopped season."""
    rows = {
        row["roster_id"]: row
        for row in standings_rows(CHOPPED_LEAGUE_ID, 2025, CHOPPED_ROSTERS, [], [])
    }

    assert all(row["final_rank"] is None for row in rows.values())


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
def test_fetch_and_write_standings_also_writes_avatars_from_the_same_users_call(
    client, tmp_path
):
    """No new Sleeper call -- avatars come from the same get_users response
    already fetched for display names (issue #150)."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=LOSERS_BRACKET
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_standings(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = dict(
            conn.execute(
                f"SELECT owner_id, avatar FROM {AVATARS_TABLE_NAME}"
            ).fetchall()
        )

    assert rows == {"u1": "avatar-abc", "u2": None}


# --- incremental fetch (issue #148) -----------------------------------------


@responses.activate
def test_fetch_and_write_standings_skips_a_cached_complete_league_entirely(
    client, tmp_path
):
    """A complete season with cached standings + matches makes no live calls
    on a second run -- not rosters, users, or either bracket."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=LOSERS_BRACKET
    )
    db_path = tmp_path / "nuclearff.duckdb"
    league = {"league_id": LEAGUE_ID, "season": 2025, "status": "complete"}

    fetch_and_write_standings(client, [league], db_path)
    assert len(responses.calls) == 4

    responses.calls.reset()
    standings_count, matches_count = fetch_and_write_standings(
        client, [league], db_path
    )

    assert standings_count == 4
    assert matches_count == len(WINNERS_BRACKET) + len(LOSERS_BRACKET)
    assert len(responses.calls) == 0
    with duckdb.connect(str(db_path)) as conn:
        (avatar_count,) = conn.execute(
            f"SELECT COUNT(*) FROM {AVATARS_TABLE_NAME}"
        ).fetchone()
    assert avatar_count == len(USERS)  # carried forward, not lost by the skip


@responses.activate
def test_fetch_and_write_standings_refetches_a_league_that_is_not_yet_complete(
    client, tmp_path
):
    """A season without ``status == "complete"`` is always fetched live,
    even if it already has cached rows -- its record/bracket can still
    change."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=LOSERS_BRACKET
    )
    db_path = tmp_path / "nuclearff.duckdb"
    league = {"league_id": LEAGUE_ID, "season": 2025, "status": "in_season"}

    fetch_and_write_standings(client, [league], db_path)

    responses.calls.reset()
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=LOSERS_BRACKET
    )
    standings_count, _ = fetch_and_write_standings(client, [league], db_path)

    assert standings_count == 4
    assert len(responses.calls) == 4


@responses.activate
def test_fetch_and_write_standings_does_not_erase_another_leagues_rows(
    client, tmp_path
):
    """Real bug fixed 2026-09-10: fetching a second, unrelated league used
    to silently erase every row from a previously-fetched, different
    league -- confirmed live across three real leagues on the same
    account, since the old write always dropped the whole shared table
    first."""
    other_league_id = "9999999999"
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/winners_bracket", json=WINNERS_BRACKET
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/losers_bracket", json=LOSERS_BRACKET
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{other_league_id}/rosters", json=ROSTERS)
    responses.get(f"{TEST_BASE_URL}/v1/league/{other_league_id}/users", json=USERS)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{other_league_id}/winners_bracket", json=[]
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{other_league_id}/losers_bracket", json=[]
    )
    db_path = tmp_path / "nuclearff.duckdb"

    fetch_and_write_standings(
        client, [{"league_id": LEAGUE_ID, "season": 2025}], db_path
    )
    fetch_and_write_standings(
        client, [{"league_id": other_league_id, "season": 2025}], db_path
    )

    with duckdb.connect(str(db_path)) as conn:
        league_ids = {
            row[0]
            for row in conn.execute(
                f"SELECT league_id FROM {STANDINGS_TABLE_NAME}"
            ).fetchall()
        }
    assert league_ids == {LEAGUE_ID, other_league_id}


@responses.activate
def test_fetch_and_write_standings_handles_a_chopped_league(client, tmp_path, caplog):
    """A Chopped league's null bracket response quietly yields a real final_rank."""
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{CHOPPED_LEAGUE_ID}/rosters", json=CHOPPED_ROSTERS
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/{CHOPPED_LEAGUE_ID}/users", json=[])
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{CHOPPED_LEAGUE_ID}/winners_bracket", json=None
    )
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{CHOPPED_LEAGUE_ID}/losers_bracket", json=None
    )
    db_path = tmp_path / "nuclearff.duckdb"

    with caplog.at_level("WARNING", logger="nuclearff.sleeper.standings"):
        standings_count, matches_count = fetch_and_write_standings(
            client, [CHOPPED_LEAGUE], db_path
        )

    assert standings_count == 16
    assert matches_count == 0
    assert not caplog.records  # no misleading "could not fetch" warning

    with duckdb.connect(str(db_path)) as conn:
        ranks = dict(
            conn.execute(
                f"SELECT roster_id, final_rank FROM {STANDINGS_TABLE_NAME}"
            ).fetchall()
        )
    assert ranks[14] == 1
    assert ranks[11] == 16


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
