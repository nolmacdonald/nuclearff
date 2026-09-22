"""Unit tests for deterministic object-key construction and key safety."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nuclearff.data.keys import (
    format_partitions,
    history_manifest_key,
    latest_manifest_key,
    object_key,
    release_manifest_key,
    run_id,
    validate_key,
)
from nuclearff.exceptions import ObjectStoreError

# --- run_id ------------------------------------------------------------


def test_run_id_formats_a_utc_timestamp_and_short_sha():
    now = datetime(2026, 9, 22, 10, 15, 30, tzinfo=UTC)

    assert run_id(now, "a1b2c3d4e5f6") == "20260922T101530Z-a1b2c3d"


def test_run_id_does_not_pad_a_short_sha():
    now = datetime(2026, 9, 22, 10, 15, 30, tzinfo=UTC)

    assert run_id(now, "abc") == "20260922T101530Z-abc"


# --- format_partitions ---------------------------------------------------


def test_format_partitions_orders_segments_as_given():
    assert format_partitions({"season": 2026, "week": 3}) == "season=2026/week=03"


def test_format_partitions_zero_pads_only_the_week_key():
    assert format_partitions({"week": 3}) == "week=03"
    assert format_partitions({"season": 3}) == "season=3"


def test_format_partitions_trusts_an_already_string_week():
    assert format_partitions({"week": "playoffs"}) == "week=playoffs"


def test_format_partitions_empty():
    assert format_partitions({}) == ""


# --- object_key ------------------------------------------------------------


def test_object_key_matches_the_guide_raw_layout():
    key = object_key(
        "prod",
        "raw",
        "sleeper",
        "matchups",
        partitions={"season": 2026, "week": 3, "ingest_date": "2026-09-22"},
        filename="matchups.json.gz",
    )

    assert key == (
        "prod/raw/sleeper/matchups/season=2026/week=03/"
        "ingest_date=2026-09-22/matchups.json.gz"
    )


def test_object_key_matches_the_guide_curated_layout():
    key = object_key(
        "prod",
        "releases",
        "20260922T101530Z-a1b2c3d",
        "curated",
        "player_week",
        partitions={"season": 2026},
        filename="part-000.parquet",
    )

    assert key == (
        "prod/releases/20260922T101530Z-a1b2c3d/curated/player_week/"
        "season=2026/part-000.parquet"
    )


def test_object_key_without_partitions():
    key = object_key(
        "prod", "raw", "sleeper", "players", filename="latest/players.json.gz"
    )

    assert key == "prod/raw/sleeper/players/latest/players.json.gz"


# --- manifest key helpers ---------------------------------------------------


def test_release_manifest_key():
    assert (
        release_manifest_key("prod", "20260922T101530Z-a1b2c3d")
        == "prod/releases/20260922T101530Z-a1b2c3d/manifest.json"
    )


def test_history_manifest_key():
    assert (
        history_manifest_key("prod", "20260922T101530Z-a1b2c3d")
        == "prod/manifests/history/20260922T101530Z-a1b2c3d.json"
    )


def test_latest_manifest_key():
    assert latest_manifest_key("prod") == "prod/manifests/latest.json"


# --- validate_key ------------------------------------------------------------


def test_validate_key_accepts_a_key_within_its_prefix():
    validate_key("prod/manifests/latest.json", prefix="prod")


def test_validate_key_rejects_a_leading_slash():
    with pytest.raises(ObjectStoreError, match="start with '/'"):
        validate_key("/prod/manifests/latest.json", prefix="prod")


def test_validate_key_rejects_a_dot_dot_segment():
    with pytest.raises(ObjectStoreError, match=r"\.\."):
        validate_key("prod/releases/../secrets.json", prefix="prod")


def test_validate_key_rejects_a_key_outside_the_configured_prefix():
    with pytest.raises(ObjectStoreError, match="configured prefix"):
        validate_key("dev/manifests/latest.json", prefix="prod")


def test_validate_key_rejects_a_string_prefix_match_that_is_not_a_path_segment():
    """`"prodigal/..."` must not pass a `prefix="prod"` check."""
    with pytest.raises(ObjectStoreError, match="configured prefix"):
        validate_key("prodigal/manifests/latest.json", prefix="prod")
