"""Unit tests for league snapshot capture and anomaly detection."""

from __future__ import annotations

import copy
import json

import pytest
import responses

from nuclearff.sleeper import (
    SleeperClient,
    detect_anomalies,
    fetch_league_snapshot,
    write_snapshot,
)
from tests.conftest import LEAGUE_ID, TEST_BASE_URL

DRAFT_ID = "1367225133646778368"


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


def _register_league(league, draft, users, rosters, state):
    """Register every endpoint a full snapshot touches."""
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}", json=league)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/users", json=users)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/rosters", json=rosters)
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/drafts", json=[draft])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}", json=draft)
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/draft/{DRAFT_ID}/traded_picks", json=[])
    responses.get(f"{TEST_BASE_URL}/v1/state/nfl", json=state)


def test_league_fixture_matches_the_documented_format(league_payload):
    """Guard the facts every downstream weight depends on."""
    scoring = league_payload["scoring_settings"]

    assert scoring["rec"] == 1.0, "full PPR"
    assert scoring["rec_td"] == 6.0
    assert scoring.get("bonus_rec_te", 0.0) == 0.0, "no TE premium"
    assert scoring.get("bonus_rec_wr", 0.0) == 0.0
    assert scoring.get("bonus_rec_yd_100", 0.0) == 0.0
    assert league_payload["settings"]["num_teams"] == 10
    assert league_payload["roster_positions"].count("WR") == 2
    assert league_payload["roster_positions"].count("FLEX") == 3


def test_draft_object_resolves_the_round_count(league_payload, draft_payload):
    """The draft object contradicts the league's stale draft_rounds field."""
    assert league_payload["settings"]["draft_rounds"] == 3
    assert draft_payload["settings"]["rounds"] == 15
    assert draft_payload["type"] == "snake"

    anomalies = {a.code: a for a in detect_anomalies(league_payload, draft_payload)}

    assert anomalies["draft_rounds_mismatch"].severity == "warning"
    assert "15" in anomalies["draft_rounds_mismatch"].action


def test_keeper_setting_is_flagged_in_a_redraft(league_payload, draft_payload):
    """type=0 with max_keepers=1 is contradictory and needs confirmation."""
    codes = {a.code for a in detect_anomalies(league_payload, draft_payload)}

    assert "keepers_in_redraft" in codes


def test_median_scoring_is_noted(league_payload, draft_payload):
    """league_average_match changes how floor and ceiling are weighted."""
    anomalies = {a.code: a for a in detect_anomalies(league_payload, draft_payload)}

    assert anomalies["median_scoring"].severity == "info"
    assert "floor" in anomalies["median_scoring"].action


def test_no_kicker_or_defense_is_noted(league_payload, draft_payload):
    """The league rosters no kickers or defenses, so those keys are inert."""
    codes = {a.code for a in detect_anomalies(league_payload, draft_payload)}

    assert "no_kicker_or_defense" in codes


def test_missing_draft_object_flags_the_round_count(league_payload):
    """Without the draft object, the low round count is only a suspicion."""
    codes = {a.code for a in detect_anomalies(league_payload, None)}

    assert "draft_rounds_below_roster_size" in codes
    assert "draft_rounds_mismatch" not in codes


def test_agreeing_round_counts_produce_an_informational_note(league_payload):
    """When league and draft agree, the setting is confirmed rather than flagged."""
    league = copy.deepcopy(league_payload)
    league["settings"]["draft_rounds"] = 15

    anomalies = {
        a.code: a for a in detect_anomalies(league, {"settings": {"rounds": 15}})
    }

    assert anomalies["draft_rounds_confirmed"].severity == "info"
    assert "draft_rounds_mismatch" not in anomalies


def test_best_ball_prefers_vorp(league_payload):
    """A best-ball league changes the recommended replacement baseline."""
    league = copy.deepcopy(league_payload)
    league["settings"]["best_ball"] = 1

    anomalies = {a.code: a for a in detect_anomalies(league, None)}

    assert "VORP" in anomalies["best_ball"].action


def test_kicker_slot_suppresses_the_note(league_payload):
    """A league that rosters kickers gets no inert-scoring note."""
    league = copy.deepcopy(league_payload)
    league["roster_positions"] = [*league["roster_positions"], "K"]

    codes = {a.code for a in detect_anomalies(league, None)}

    assert "no_kicker_or_defense" not in codes


def test_anomalies_are_ordered_warnings_first(league_payload, draft_payload):
    """Warnings come before informational notes so output reads top-down."""
    severities = [a.severity for a in detect_anomalies(league_payload, draft_payload)]

    assert severities == sorted(severities, key=lambda s: s != "warning")


def test_malformed_league_does_not_crash_detection():
    """A payload missing every expected key yields no anomalies, not an error."""
    assert detect_anomalies({}, None) == []
    assert detect_anomalies({"settings": "not-a-mapping"}, None) == []


@responses.activate
def test_fetch_snapshot_captures_every_endpoint(
    client, league_payload, draft_payload, users_payload, rosters_payload, state_payload
):
    """A full capture records each endpoint it retrieved."""
    _register_league(
        league_payload, draft_payload, users_payload, rosters_payload, state_payload
    )

    snapshot = fetch_league_snapshot(client, LEAGUE_ID)

    assert snapshot.league_name == "NUCLEARFF REDRAFT"
    assert snapshot.draft is not None
    assert snapshot.state == state_payload
    assert snapshot.metadata.missing == []
    assert len(snapshot.metadata.endpoints) == 9
    assert len(snapshot.warnings) == 2


@responses.activate
def test_optional_endpoint_failure_is_recorded_not_raised(
    client, league_payload, draft_payload, users_payload, rosters_payload, state_payload
):
    """A partially available league still produces a usable snapshot."""
    _register_league(
        league_payload, draft_payload, users_payload, rosters_payload, state_payload
    )
    responses.replace(
        responses.GET, f"{TEST_BASE_URL}/v1/league/{LEAGUE_ID}/traded_picks", status=404
    )

    snapshot = fetch_league_snapshot(client, LEAGUE_ID)

    assert snapshot.traded_picks == []
    assert f"/v1/league/{LEAGUE_ID}/traded_picks" in snapshot.metadata.missing


@responses.activate
def test_write_snapshot_is_immutable(
    client,
    tmp_path,
    league_payload,
    draft_payload,
    users_payload,
    rosters_payload,
    state_payload,
):
    """Raw snapshots are written once; a repeat write refuses to clobber."""
    _register_league(
        league_payload, draft_payload, users_payload, rosters_payload, state_payload
    )
    snapshot = fetch_league_snapshot(client, LEAGUE_ID)

    target = write_snapshot(snapshot, tmp_path)

    assert (target / "league.json").is_file()
    assert (target / "draft.json").is_file()
    assert json.loads((target / "league.json").read_text()) == league_payload

    written = json.loads((target / "snapshot.json").read_text())
    assert written["metadata"]["league_id"] == LEAGUE_ID
    assert len(written["anomalies"]) == len(snapshot.anomalies)

    with pytest.raises(FileExistsError):
        write_snapshot(snapshot, tmp_path)
