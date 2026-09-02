"""Unit tests for the Sleeper API client.

All HTTP is mocked with ``responses``. An unregistered request raises a
connection error, so a test that accidentally reaches the network fails.
"""

from __future__ import annotations

import json

import pytest
import responses

from nuclearff.exceptions import SleeperHTTPError, SleeperResponseError
from nuclearff.sleeper import SleeperClient
from tests.conftest import LEAGUE_ID, TEST_BASE_URL


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


@responses.activate
def test_get_league_returns_payload(client, league_payload):
    """A successful league request decodes to the league object."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league_payload)

    league = client.get_league(LEAGUE_ID)

    assert league["name"] == "NUCLEARFF REDRAFT"
    assert league["scoring_settings"]["rec"] == 1.0


@responses.activate
def test_get_users_returns_list(client, users_payload):
    """Endpoints documented as arrays are validated as arrays."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users_payload)

    assert len(client.get_users(LEAGUE_ID)) == len(users_payload)


@responses.activate
def test_empty_traded_picks_is_not_an_error(client):
    """An empty array is a valid answer, not a missing endpoint."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])

    assert client.get_traded_picks(LEAGUE_ID) == []


@responses.activate
def test_retries_rate_limit_then_succeeds(client, league_payload):
    """A 429 is retried, and the eventual success is returned."""
    url = f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}"
    responses.get(url, status=429)
    responses.get(url, status=503)
    responses.get(url, json=league_payload)

    assert client.get_league(LEAGUE_ID)["name"] == "NUCLEARFF REDRAFT"
    assert len(responses.calls) == 3


@responses.activate
def test_gives_up_after_max_retries(client):
    """Persistent server errors surface as a typed error carrying the status."""
    url = f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}"
    for _ in range(client.max_retries + 1):
        responses.get(url, status=500)

    with pytest.raises(SleeperHTTPError) as excinfo:
        client.get_league(LEAGUE_ID)

    assert excinfo.value.status_code == 500
    assert len(responses.calls) == client.max_retries + 1


@responses.activate
def test_client_errors_are_not_retried(client):
    """A 404 means the resource is absent; retrying would only waste calls."""
    url = f"{TEST_BASE_URL}/v1/draft/nope"
    responses.get(url, status=404)

    with pytest.raises(SleeperHTTPError) as excinfo:
        client.get_draft("nope")

    assert excinfo.value.status_code == 404
    assert len(responses.calls) == 1


@responses.activate
def test_null_payload_is_a_response_error(client):
    """Sleeper answers unknown league IDs with a bare null rather than a 404."""
    responses.get(
        f"{TEST_BASE_URL}/v1/league/0", body="null", content_type="application/json"
    )

    with pytest.raises(SleeperResponseError, match="expected a JSON object"):
        client.get_league("0")


@responses.activate
def test_wrong_container_type_is_a_response_error(client):
    """An object where an array is documented is a contract violation."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json={"a": 1})

    with pytest.raises(SleeperResponseError, match="expected a JSON array"):
        client.get_rosters(LEAGUE_ID)


@responses.activate
def test_array_of_non_objects_is_a_response_error(client):
    """An array of scalars would break every downstream consumer."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=[1, 2])

    with pytest.raises(SleeperResponseError, match="item 0 is int"):
        client.get_rosters(LEAGUE_ID)


@responses.activate
def test_non_json_body_is_a_response_error(client):
    """An HTML error page from a proxy is reported as a response error."""
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", body="<html>nope</html>", status=200)

    with pytest.raises(SleeperResponseError, match="not valid JSON"):
        client.get_state()


def test_trending_rejects_unknown_kind(client):
    """Only adds and drops are trending kinds."""
    with pytest.raises(ValueError, match="must be 'add' or 'drop'"):
        client.get_trending(kind="sideways")


@responses.activate
def test_players_payload_is_cached_on_disk(client):
    """The ~5MB player map is fetched once and then served from disk."""
    payload = {"4046": {"full_name": "Patrick Mahomes"}}
    responses.get(f"{TEST_BASE_URL}/v1/players/nfl", json=payload)

    first = client.get_players()
    second = client.get_players()

    assert first == second == payload
    assert len(responses.calls) == 1
    assert json.loads(client.players_cache_path.read_text()) == payload


@responses.activate
def test_stale_players_cache_is_refreshed(client):
    """Once the cache exceeds its TTL, the payload is fetched again."""
    responses.get(f"{TEST_BASE_URL}/v1/players/nfl", json={"1": {}})
    responses.get(f"{TEST_BASE_URL}/v1/players/nfl", json={"2": {}})

    client.get_players()
    client.players_ttl_hours = 0

    assert client.get_players() == {"2": {}}
    assert len(responses.calls) == 2


@responses.activate
def test_force_refresh_bypasses_a_fresh_cache(client):
    """force_refresh re-fetches even when the cache is still valid."""
    responses.get(f"{TEST_BASE_URL}/v1/players/nfl", json={"1": {}})
    responses.get(f"{TEST_BASE_URL}/v1/players/nfl", json={"2": {}})

    client.get_players()

    assert client.get_players(force_refresh=True) == {"2": {}}


def test_avatar_url():
    """Avatars come from the CDN, not the API."""
    assert SleeperClient.avatar_url("abc") == "https://sleepercdn.com/avatars/abc"
    assert SleeperClient.avatar_url("abc", thumbnail=True).endswith("/thumbs/abc")


def test_network_access_is_blocked_in_tests(tmp_path):
    """The offline guard bites: an unmocked request cannot reach the network."""
    import requests

    with SleeperClient(
        cache_dir=tmp_path, min_interval=0.0, backoff_factor=0.0, max_retries=0
    ) as live:
        with pytest.raises((SleeperHTTPError, requests.RequestException, RuntimeError)):
            live.get_state()
