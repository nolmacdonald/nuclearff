"""Unit tests for the release manifest model and its JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nuclearff.data.manifest import (
    MANIFEST_VERSION,
    DataManifest,
    ManifestObject,
    manifest_json_schema,
)

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "manifest-v1.schema.json"

_VALID_SHA256 = "e7bf4d9c" + "0" * 56


def _object(dataset: str = "player_week", **overrides: object) -> ManifestObject:
    fields = {
        "dataset": dataset,
        "key": f"prod/releases/RUN/curated/{dataset}/season=2026/part-000.parquet",
        "content_type": "application/vnd.apache.parquet",
        "size_bytes": 482137,
        "sha256": _VALID_SHA256,
        "row_count": 1940,
        "schema_version": 1,
        "partitions": {"season": "2026"},
    }
    fields.update(overrides)
    return ManifestObject(**fields)  # type: ignore[arg-type]


def _manifest(objects: list[ManifestObject] | None = None) -> DataManifest:
    return DataManifest(
        schema_version=1,
        run_id="20260922T101530Z-a1b2c3d",
        created_at_utc="2026-09-22T10:15:30Z",  # type: ignore[arg-type]
        git_sha="a1b2c3d4e5f6",
        league_id="1367225133634191360",
        season=2026,
        objects=objects or [],
    )


# --- ManifestObject ---------------------------------------------------------


def test_manifest_object_rejects_a_malformed_sha256():
    with pytest.raises(ValidationError):
        _object(sha256="not-a-real-digest")


def test_manifest_object_rejects_a_negative_row_count():
    with pytest.raises(ValidationError):
        _object(row_count=-1)


# --- DataManifest ------------------------------------------------------------


def test_data_manifest_defaults_manifest_version():
    manifest = _manifest()

    assert manifest.manifest_version == MANIFEST_VERSION


def test_data_manifest_round_trips_through_json():
    manifest = _manifest([_object()])

    restored = DataManifest.model_validate_json(manifest.model_dump_json())

    assert restored == manifest


def test_data_manifest_rejects_duplicate_dataset_partition_combination():
    duplicate = _object(
        key="prod/releases/RUN/curated/player_week/season=2026/part-001.parquet"
    )

    with pytest.raises(ValidationError, match="duplicate dataset/partition"):
        _manifest([_object(), duplicate])


def test_data_manifest_allows_the_same_dataset_at_different_partitions():
    other_week = _object(partitions={"season": "2026", "week": "02"})
    manifest = _manifest(
        [_object(partitions={"season": "2026", "week": "01"}), other_week]
    )

    assert len(manifest.objects) == 2


# --- JSON Schema -------------------------------------------------------------


def test_manifest_schema_file_matches_current_model_generation():
    """Drift guard: `schemas/manifest-v1.schema.json` must be regenerated
    (via `manifest_json_schema()`) whenever `DataManifest`/`ManifestObject`
    change, rather than hand-edited out of sync."""
    checked_in = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert checked_in == manifest_json_schema()
