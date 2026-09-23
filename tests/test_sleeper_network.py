"""Unit tests for crawling the Sleeper network reachable from a seed user.

All HTTP is mocked with ``responses``, matching every other Sleeper-client
test in this repo (see ``test_sleeper_leagues.py``).
"""

from __future__ import annotations

import logging

import pytest
import responses

from nuclearff.exceptions import SleeperHTTPError
from nuclearff.sleeper import SleeperClient
from nuclearff.sleeper.network import crawl_user_network
from tests.conftest import TEST_BASE_URL


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


def _user(user_id: str, display_name: str) -> dict:
    return {"user_id": user_id, "display_name": display_name}


def _league(league_id: str, name: str, total_rosters: int = 3) -> dict:
    return {
        "league_id": league_id,
        "name": name,
        "season": "2026",
        "sport": "nfl",
        "status": "in_season",
        "total_rosters": total_rosters,
    }


def _mock_seed(user_id: str = "U1", display_name: str = "seed") -> None:
    responses.get(
        f"{TEST_BASE_URL}/v1/user/{display_name}", json=_user(user_id, display_name)
    )


def _mock_user_leagues(user_id: str, leagues: list[dict]) -> None:
    responses.get(f"{TEST_BASE_URL}/v1/user/{user_id}/leagues/nfl/2026", json=leagues)


def _mock_league_users(league_id: str, users: list[dict]) -> None:
    responses.get(f"{TEST_BASE_URL}/v1/league/{league_id}/users", json=users)


# --- hop 0 only (max_hops=0) -------------------------------------------------


@responses.activate
def test_crawl_at_max_hops_zero_only_fetches_the_seeds_own_leagues(client):
    """max_hops=0 never calls get_users -- an unregistered URL would raise."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One")])

    network = crawl_user_network(client, "seed", 2026, max_hops=0)

    assert network.seed_user_id == "U1"
    assert network.leagues["league_id"].to_list() == ["L1"]
    assert network.users["user_id"].to_list() == ["U1"]
    assert network.memberships.height == 1


@responses.activate
def test_crawl_at_max_hops_zero_handles_a_user_with_no_leagues(client):
    """A seed with zero leagues that season produces empty frames, not a raise."""
    _mock_seed()
    _mock_user_leagues("U1", [])

    network = crawl_user_network(client, "seed", 2026, max_hops=0)

    assert network.leagues.is_empty()
    assert network.memberships.is_empty()
    assert network.users["user_id"].to_list() == ["U1"]
    assert network.users["league_count"].to_list() == [0]


# --- max_hops=1 (default): the full motivating example -----------------------


@responses.activate
def test_crawl_default_hop_discovers_co_members_and_their_leagues(client):
    """Issue #209's own example: seed's leagues -> co-members -> their leagues."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=3)])
    _mock_league_users(
        "L1", [_user("U1", "seed"), _user("U2", "alice"), _user("U3", "bob")]
    )
    _mock_user_leagues(
        "U2",
        [_league("L1", "League One", total_rosters=3), _league("L2", "League Two")],
    )
    _mock_user_leagues(
        "U3",
        [_league("L1", "League One", total_rosters=3), _league("L3", "League Three")],
    )

    network = crawl_user_network(client, "seed", 2026)

    assert set(network.users["user_id"].to_list()) == {"U1", "U2", "U3"}
    assert set(network.leagues["league_id"].to_list()) == {"L1", "L2", "L3"}
    assert network.memberships.height == 5


@responses.activate
def test_crawl_leagues_frame_reports_known_member_count(client):
    """L1's full membership was fetched (hop 0), so its count is exact; L2/L3
    are each known only through the one co-member who reported them."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=3)])
    _mock_league_users(
        "L1", [_user("U1", "seed"), _user("U2", "alice"), _user("U3", "bob")]
    )
    _mock_user_leagues(
        "U2",
        [_league("L1", "League One", total_rosters=3), _league("L2", "League Two")],
    )
    _mock_user_leagues(
        "U3",
        [_league("L1", "League One", total_rosters=3), _league("L3", "League Three")],
    )

    network = crawl_user_network(client, "seed", 2026)

    counts = dict(
        zip(
            network.leagues["league_id"].to_list(),
            network.leagues["known_member_count"].to_list(),
            strict=True,
        )
    )
    assert counts == {"L1": 3, "L2": 1, "L3": 1}
    hops = dict(
        zip(
            network.leagues["league_id"].to_list(),
            network.leagues["hop"].to_list(),
            strict=True,
        )
    )
    assert hops == {"L1": 0, "L2": 1, "L3": 1}


@responses.activate
def test_crawl_users_frame_reports_shared_league_count(client):
    """shared_league_count is overlap with the seed's own (hop-0) leagues only."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=3)])
    _mock_league_users(
        "L1", [_user("U1", "seed"), _user("U2", "alice"), _user("U3", "bob")]
    )
    _mock_user_leagues(
        "U2",
        [_league("L1", "League One", total_rosters=3), _league("L2", "League Two")],
    )
    _mock_user_leagues(
        "U3",
        [_league("L1", "League One", total_rosters=3), _league("L3", "League Three")],
    )

    network = crawl_user_network(client, "seed", 2026)

    rows = {
        row["user_id"]: (row["league_count"], row["shared_league_count"])
        for row in network.users.iter_rows(named=True)
    }
    assert rows == {"U1": (1, 1), "U2": (2, 1), "U3": (2, 1)}


@responses.activate
def test_crawl_does_not_re_expand_the_seed_when_it_reappears_as_a_co_member(client):
    """The seed shows up in its own league's member list -- must not be
    treated as a newly discovered co-member or double counted."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=2)])
    _mock_league_users("L1", [_user("U1", "seed"), _user("U2", "alice")])
    _mock_user_leagues("U2", [_league("L1", "League One", total_rosters=2)])

    network = crawl_user_network(client, "seed", 2026)

    assert network.users["user_id"].to_list().count("U1") == 1


# --- robustness: a failure on one branch does not abort the whole crawl -----


@responses.activate
def test_crawl_raises_if_the_seed_users_own_leagues_cannot_be_fetched(client):
    _mock_seed()
    responses.get(f"{TEST_BASE_URL}/v1/user/U1/leagues/nfl/2026", status=500)

    with pytest.raises(SleeperHTTPError):
        crawl_user_network(client, "seed", 2026)


@responses.activate
def test_crawl_skips_a_co_member_whose_leagues_fail_to_fetch(client, caplog):
    """A failed co-member expansion is logged and skipped, not a raise --
    the other co-member is still discovered."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=3)])
    _mock_league_users(
        "L1", [_user("U1", "seed"), _user("U2", "alice"), _user("U3", "bob")]
    )
    responses.get(f"{TEST_BASE_URL}/v1/user/U2/leagues/nfl/2026", status=500)
    _mock_user_leagues("U3", [_league("L1", "League One", total_rosters=3)])

    with caplog.at_level(logging.WARNING):
        network = crawl_user_network(client, "seed", 2026)

    assert set(network.users["user_id"].to_list()) == {"U1", "U3"}
    assert "U2" in caplog.text


@responses.activate
def test_crawl_skips_a_league_whose_members_fail_to_fetch(client, caplog):
    """A failed get_users call for one newly found league is logged and
    skipped -- other newly found leagues still expand normally."""
    _mock_seed()
    _mock_user_leagues(
        "U1",
        [
            _league("L1", "League One", total_rosters=2),
            _league("L2", "League Two", total_rosters=2),
        ],
    )
    responses.get(f"{TEST_BASE_URL}/v1/league/L1/users", status=500)
    _mock_league_users("L2", [_user("U1", "seed"), _user("U4", "carl")])
    _mock_user_leagues("U4", [_league("L2", "League Two", total_rosters=2)])

    with caplog.at_level(logging.WARNING):
        network = crawl_user_network(client, "seed", 2026)

    assert set(network.users["user_id"].to_list()) == {"U1", "U4"}
    assert "L1" in caplog.text


# --- malformed upstream data is skipped, not a raise ------------------------


@responses.activate
def test_crawl_skips_a_league_entry_with_no_league_id(client):
    """A malformed league entry (real risk: a third-party API) is skipped
    rather than crashing the crawl or polluting the output frames."""
    _mock_seed()
    _mock_user_leagues(
        "U1", [{"name": "No id here"}, _league("L1", "League One", total_rosters=1)]
    )

    network = crawl_user_network(client, "seed", 2026, max_hops=0)

    assert network.leagues["league_id"].to_list() == ["L1"]


@responses.activate
def test_crawl_skips_a_league_member_entry_with_no_user_id(client):
    """A malformed member entry is skipped when discovering co-members."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=2)])
    _mock_league_users("L1", [{"display_name": "no id here"}, _user("U1", "seed")])

    network = crawl_user_network(client, "seed", 2026)

    assert network.users["user_id"].to_list() == ["U1"]


# --- max_users guard ----------------------------------------------------------


@responses.activate
def test_crawl_respects_max_users_and_logs_a_warning(client, caplog):
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=5)])
    _mock_league_users(
        "L1",
        [
            _user("U1", "seed"),
            _user("U2", "b"),
            _user("U3", "c"),
            _user("U4", "d"),
            _user("U5", "e"),
        ],
    )
    # Only enough room is registered for whichever single co-member the
    # (deterministic, sorted) trim keeps -- U2, the alphabetically first.
    _mock_user_leagues("U2", [_league("L1", "League One", total_rosters=5)])

    with caplog.at_level(logging.WARNING):
        network = crawl_user_network(client, "seed", 2026, max_users=2)

    assert network.users.height <= 2
    assert "max_users" in caplog.text


# --- max_hops beyond 1: stops early once a hop finds nothing new ------------


@responses.activate
def test_crawl_stops_before_max_hops_once_no_new_leagues_are_found(client):
    """hop 1's co-member has no leagues beyond what's already known, so a
    hop-2 fetch never happens -- an unregistered URL there would raise."""
    _mock_seed()
    _mock_user_leagues("U1", [_league("L1", "League One", total_rosters=2)])
    _mock_league_users("L1", [_user("U1", "seed"), _user("U2", "alice")])
    _mock_user_leagues("U2", [_league("L1", "League One", total_rosters=2)])

    network = crawl_user_network(client, "seed", 2026, max_hops=5)

    assert set(network.users["user_id"].to_list()) == {"U1", "U2"}
