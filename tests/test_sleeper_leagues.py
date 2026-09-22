"""Unit tests for walking and persisting a league's previous_league_id chain.

All HTTP is mocked with ``responses``, matching every other Sleeper-client
test in this repo.
"""

from __future__ import annotations

import json

import duckdb
import polars as pl
import pytest
import responses

from nuclearff.exceptions import SleeperHTTPError
from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.leagues import (
    CONFIG_TABLE_NAME,
    TABLE_NAME,
    league_chain_ids,
    league_config_rows,
    league_rows,
    league_type_name,
    walk_league_chain,
    write_league_tables,
)
from tests.conftest import LEAGUE_ID, TEST_BASE_URL

PREVIOUS_LEAGUE_ID = "1240509989819273216"
"""The real prior-season league ID linked from the committed fixture."""


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


@pytest.fixture
def previous_league_payload(league_payload):
    """A synthetic prior-season payload, same shape as the real fixture.

    Not a second captured fixture (no earlier-season payload has been
    fetched) — a copy of the real league object with only the identifying
    fields changed, the same technique
    ``tests/test_config_league.py`` uses for payload variants.
    """
    payload = dict(league_payload)
    payload["league_id"] = PREVIOUS_LEAGUE_ID
    payload["season"] = 2025
    payload["previous_league_id"] = None
    payload["status"] = "complete"
    return payload


# --- walk_league_chain -------------------------------------------------


@responses.activate
def test_walk_league_chain_follows_previous_league_id(
    client, league_payload, previous_league_payload
):
    """A two-hop chain is fully walked, most recent season first."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )

    leagues = walk_league_chain(client, LEAGUE_ID)

    assert [league["league_id"] for league in leagues] == [
        LEAGUE_ID,
        PREVIOUS_LEAGUE_ID,
    ]


@responses.activate
def test_walk_league_chain_stops_when_previous_league_id_is_absent(
    client, league_payload
):
    """A league with no previous_league_id is a one-hop chain."""
    payload = dict(league_payload)
    payload["previous_league_id"] = None
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=payload)

    leagues = walk_league_chain(client, LEAGUE_ID)

    assert len(leagues) == 1


@responses.activate
def test_walk_league_chain_treats_zero_as_chain_end(client, league_payload):
    """Sleeper's "0" sentinel for previous_league_id also terminates the walk."""
    payload = dict(league_payload)
    payload["previous_league_id"] = "0"
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=payload)

    leagues = walk_league_chain(client, LEAGUE_ID)

    assert len(leagues) == 1


@responses.activate
def test_walk_league_chain_stops_at_a_failed_hop_without_raising(
    client, league_payload
):
    """A previous_league_id that 404s stops the walk but keeps what succeeded."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", status=404)

    leagues = walk_league_chain(client, LEAGUE_ID)

    assert [league["league_id"] for league in leagues] == [LEAGUE_ID]


@responses.activate
def test_walk_league_chain_raises_if_the_starting_league_fails(client):
    """Unlike a later hop, the starting league itself is not optional."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", status=404)

    with pytest.raises(SleeperHTTPError):
        walk_league_chain(client, LEAGUE_ID)


@responses.activate
def test_walk_league_chain_stops_on_a_cycle(client, league_payload):
    """A league that points back to itself does not loop forever."""
    payload = dict(league_payload)
    payload["previous_league_id"] = LEAGUE_ID
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=payload)

    leagues = walk_league_chain(client, LEAGUE_ID)

    assert len(leagues) == 1


@responses.activate
def test_walk_league_chain_respects_max_seasons(
    client, league_payload, previous_league_payload
):
    """A cap on chain length stops the walk even if more history exists."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )

    leagues = walk_league_chain(client, LEAGUE_ID, max_seasons=1)

    assert len(leagues) == 1


# --- walk_league_chain incremental fetch (issue #180) ----------------------


@responses.activate
def test_walk_league_chain_reuses_a_cached_complete_hop(
    client, league_payload, previous_league_payload, tmp_path
):
    """A hop already cached as `status == "complete"` skips its live call."""
    db_path = tmp_path / "nuclearff.duckdb"
    write_league_tables([previous_league_payload], db_path)
    # No mock registered for PREVIOUS_LEAGUE_ID -- if `walk_league_chain`
    # tried to fetch it live, `responses` would raise a connection error.
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)

    leagues = walk_league_chain(client, LEAGUE_ID, db_path=db_path)

    assert [league["league_id"] for league in leagues] == [
        LEAGUE_ID,
        PREVIOUS_LEAGUE_ID,
    ]
    assert leagues[1]["season"] == 2025
    assert leagues[1]["status"] == "complete"
    assert leagues[1]["draft_id"] == str(previous_league_payload["draft_id"])
    assert len(responses.calls) == 1


@responses.activate
def test_walk_league_chain_reuses_the_cached_starting_league_too(
    client, previous_league_payload, tmp_path
):
    """The starting league itself is eligible for a cache hit, not just later hops."""
    db_path = tmp_path / "nuclearff.duckdb"
    write_league_tables([previous_league_payload], db_path)

    leagues = walk_league_chain(client, PREVIOUS_LEAGUE_ID, db_path=db_path)

    assert len(leagues) == 1
    assert leagues[0]["league_id"] == PREVIOUS_LEAGUE_ID
    assert len(responses.calls) == 0


@responses.activate
def test_walk_league_chain_does_not_reuse_an_incomplete_cached_hop(
    client, league_payload, previous_league_payload, tmp_path
):
    """A cached hop that isn't `"complete"` yet is still always fetched live."""
    db_path = tmp_path / "nuclearff.duckdb"
    in_progress = dict(previous_league_payload)
    in_progress["status"] = "in_season"
    write_league_tables([in_progress], db_path)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )

    leagues = walk_league_chain(client, LEAGUE_ID, db_path=db_path)

    assert len(leagues) == 2
    assert len(responses.calls) == 2


@responses.activate
def test_walk_league_chain_without_db_path_always_fetches_live(
    client, league_payload, previous_league_payload, tmp_path
):
    """`db_path=None` (the default) preserves the original always-live behavior."""
    db_path = tmp_path / "nuclearff.duckdb"
    write_league_tables([previous_league_payload], db_path)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)
    responses.get(
        f"{TEST_BASE_URL}/v1/league/{PREVIOUS_LEAGUE_ID}", json=previous_league_payload
    )

    leagues = walk_league_chain(client, LEAGUE_ID)

    assert len(leagues) == 2
    assert len(responses.calls) == 2


@responses.activate
def test_walk_league_chain_cached_hop_without_a_parsed_config_gets_no_draft_id(
    client, league_payload, previous_league_payload, tmp_path
):
    """A cached hop with a raw row but no `sleeper_league_configs` row (its
    config failed to parse) reconstructs with `draft_id=None` rather than
    raising."""
    db_path = tmp_path / "nuclearff.duckdb"
    unparseable = dict(previous_league_payload)
    del unparseable["season"]
    raw_count, config_count = write_league_tables([unparseable], db_path)
    assert raw_count == 1
    assert config_count == 0
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)

    leagues = walk_league_chain(client, LEAGUE_ID, db_path=db_path)

    assert leagues[1]["league_id"] == PREVIOUS_LEAGUE_ID
    assert leagues[1]["draft_id"] is None
    assert len(responses.calls) == 1


# --- league_type_name -----------------------------------------------------


def test_league_type_name_trusts_type_alone_not_last_chopped_leg():
    """Issue #77: a real type==3 league can lack `last_chopped_leg` (hasn't
    chopped anyone yet) and must still resolve to "chopped", unlike the
    stricter `is_chopped_league` check used for standings resolution."""
    payload = {"league_id": "1", "settings": {"type": 3}}

    assert league_type_name(payload) == "chopped"


def test_league_type_name_handles_missing_settings():
    assert league_type_name({"league_id": "1"}) == "unknown"


# --- league_rows ---------------------------------------------------------


def test_league_rows_flattens_the_real_fixture(league_payload):
    """Raw payload fields land in the row, JSON columns encode verbatim."""
    row = league_rows([league_payload])[0]

    assert row["league_id"] == LEAGUE_ID
    assert row["previous_league_id"] == PREVIOUS_LEAGUE_ID
    assert row["season"] == int(league_payload["season"])
    assert row["league_type"] == "redraft"
    assert json.loads(row["scoring_settings"])["rec"] == 1.0
    assert json.loads(row["roster_positions"]) == league_payload["roster_positions"]


@pytest.mark.parametrize(
    ("type_code", "name"),
    [(0, "redraft"), (1, "keeper"), (2, "dynasty"), (3, "chopped"), (99, "unknown")],
)
def test_league_rows_maps_league_type_names(type_code, name):
    """Sleeper's numeric league type becomes a readable label."""
    payload = {"league_id": "1", "season": 2026, "settings": {"type": type_code}}

    row = league_rows([payload])[0]

    assert row["league_type"] == name


def test_league_rows_handles_a_missing_previous_league_id():
    """A league with no settings/previous_league_id at all does not raise."""
    payload = {"league_id": "1", "season": 2026}

    row = league_rows([payload])[0]

    assert row["previous_league_id"] is None
    assert row["league_type"] == "unknown"


# --- league_config_rows ---------------------------------------------------


def test_league_config_rows_skips_unparseable_payloads(league_payload):
    """A hop that fails to type into LeagueConfig is skipped, not fatal."""
    unparseable = {"league_id": "bad-league"}

    rows = league_config_rows([league_payload, unparseable])

    assert [row["league_id"] for row in rows] == [LEAGUE_ID]


def test_league_config_rows_captures_roster_and_scoring_flags(league_payload):
    """Scalar LeagueConfig fields and roster counts land as typed columns."""
    row = league_config_rows([league_payload])[0]

    assert row["is_full_ppr"] is True
    assert row["has_te_premium"] is False
    assert row["roster_wr"] == 2
    assert row["roster_flex"] == 3
    assert row["roster_bench"] == 6
    assert row["previous_league_id"] == PREVIOUS_LEAGUE_ID
    assert row["playoff_week_start"] == 15


# --- write_league_tables ---------------------------------------------------


def test_write_league_tables_round_trips_through_duckdb(
    league_payload, previous_league_payload, tmp_path
):
    """Both tables round-trip through DuckDB against real fetched-shape data."""
    db_path = tmp_path / "nuclearff.duckdb"

    raw_count, config_count = write_league_tables(
        [league_payload, previous_league_payload], db_path
    )

    assert raw_count == 2
    assert config_count == 2
    assert db_path.is_file()

    with duckdb.connect(str(db_path)) as conn:
        raw_ids = {
            row[0]
            for row in conn.execute(f"SELECT league_id FROM {TABLE_NAME}").fetchall()
        }
        config_ids = {
            row[0]
            for row in conn.execute(
                f"SELECT league_id FROM {CONFIG_TABLE_NAME}"
            ).fetchall()
        }

    assert raw_ids == {LEAGUE_ID, PREVIOUS_LEAGUE_ID}
    assert config_ids == {LEAGUE_ID, PREVIOUS_LEAGUE_ID}


def test_write_league_tables_config_count_can_trail_raw_count(league_payload, tmp_path):
    """A hop with a raw row but no parseable config is reflected in the counts."""
    unparseable = {"league_id": "bad-league", "season": 2020}
    db_path = tmp_path / "nuclearff.duckdb"

    raw_count, config_count = write_league_tables(
        [league_payload, unparseable], db_path
    )

    assert raw_count == 2
    assert config_count == 1


def test_write_league_tables_refetching_one_league_replaces_only_its_own_rows(
    league_payload, tmp_path
):
    """Real bug fixed 2026-09-10: writing a second, unrelated league's chain
    used to silently erase every row from a previously-written, different
    league's chain -- confirmed live across three real leagues on the same
    account. A re-fetch must only ever replace that same league_id's own
    rows."""
    db_path = tmp_path / "nuclearff.duckdb"
    other_league_id = "some-other-league"
    write_league_tables([{"league_id": other_league_id, "season": 2000}], db_path)

    raw_count, config_count = write_league_tables([league_payload], db_path)

    assert raw_count == 1
    assert config_count == 1
    with duckdb.connect(str(db_path)) as conn:
        raw_ids = {
            row[0]
            for row in conn.execute(f"SELECT league_id FROM {TABLE_NAME}").fetchall()
        }

    # The other league's row survives untouched; this league's own row is
    # present exactly once (not duplicated by the second call).
    assert raw_ids == {other_league_id, LEAGUE_ID}


def test_write_league_tables_refetching_the_same_league_does_not_duplicate(
    league_payload, tmp_path
):
    """A second write for the *same* league_id still replaces (not appends
    to) that league_id's own row -- the fix for cross-league data loss must
    not turn this into an accumulating duplicate feed either."""
    db_path = tmp_path / "nuclearff.duckdb"
    write_league_tables([league_payload], db_path)

    write_league_tables([league_payload], db_path)

    with duckdb.connect(str(db_path)) as conn:
        (total,) = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()

    assert total == 1


def _leagues_frame(chain: dict[str, str | None]) -> pl.DataFrame:
    """``{league_id: previous_league_id}`` -> a minimal TABLE_NAME-shaped frame."""
    return pl.DataFrame(
        {
            "league_id": list(chain.keys()),
            "previous_league_id": list(chain.values()),
        }
    )


def test_league_chain_ids_walks_multiple_seasons():
    leagues = _leagues_frame({"2026id": "2025id", "2025id": "2024id", "2024id": None})

    result = league_chain_ids(leagues, "2026id")

    assert set(result) == {"2026id", "2025id", "2024id"}


def test_league_chain_ids_stops_at_missing_history():
    """A league_id missing from `leagues` (history never fetched) returns itself."""
    leagues = _leagues_frame({"other_league": None})

    result = league_chain_ids(leagues, "2026id")

    assert result == ["2026id"]


def test_league_chain_ids_does_not_loop_on_a_cycle():
    """A malformed/cyclic previous_league_id chain must not infinite-loop."""
    leagues = _leagues_frame({"a": "b", "b": "a"})

    result = league_chain_ids(leagues, "a")

    assert set(result) == {"a", "b"}
