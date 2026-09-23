"""Unit tests for the atomic publisher.

Exercises issue 186's own "Done when" bars directly: an injected failure
never modifies `latest.json`, and a successful release can be reproduced
(read back byte-identical) and rolled back.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from nuclearff.data.datasets import PLAYER_WEEK_SCHEMA
from nuclearff.data.manifest import DataManifest, ManifestObject
from nuclearff.data.parquet import write_parquet_artifact
from nuclearff.data.publisher import (
    ReleaseArtifact,
    clear_staging,
    publish_release,
    release_artifact,
    rollback_to,
    staging_dir,
)
from nuclearff.exceptions import ObjectStoreError


def _player_week_fixture() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2026, 2026, 2026],
            "week": [1, 1, 1],
            "player_id": ["1001", "1002", "1003"],
            "player_name": ["Player One", "Player Two", "Player Three"],
            "team": ["SEA", "SF", "SEA"],
        }
    )


class _FakeStore:
    """A minimal `PublisherStoreLike`: an in-memory object store, no network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, object]] = {}
        self.put_file_calls: list[str] = []
        self.put_bytes_calls: list[str] = []
        self.corrupt_head_for: set[str] = set()
        self.fail_put_bytes_for: set[str] = set()

    def put_file(
        self, local_path: Path, key: str, *, content_type: str, sha256: str
    ) -> None:
        self.put_file_calls.append(key)
        payload = local_path.read_bytes()
        self.objects[key] = payload
        self.metadata[key] = {
            "ContentLength": len(payload),
            "Metadata": {"sha256": sha256},
        }

    def put_bytes(self, payload: bytes, key: str, *, content_type: str) -> None:
        if key in self.fail_put_bytes_for:
            raise ObjectStoreError(key, "simulated failure")
        self.put_bytes_calls.append(key)
        self.objects[key] = payload

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise ObjectStoreError(key, "not found")
        return self.objects[key]

    def head(self, key: str) -> dict[str, object]:
        if key in self.corrupt_head_for:
            return {"ContentLength": -1, "Metadata": {"sha256": "0" * 64}}
        return self.metadata[key]


def _artifact(
    tmp_path: Path, run_id: str, name: str = "part-000.parquet"
) -> ReleaseArtifact:
    parquet = write_parquet_artifact(
        _player_week_fixture(), tmp_path / name, schema=PLAYER_WEEK_SCHEMA
    )
    return release_artifact(
        parquet, key=f"prod/releases/{run_id}/curated/player_week/{name}"
    )


def _manifest(run_id: str, artifact: ReleaseArtifact) -> DataManifest:
    return DataManifest(
        schema_version=1,
        run_id=run_id,
        created_at_utc="2026-09-22T00:00:00Z",  # type: ignore[arg-type]
        objects=[
            ManifestObject(
                dataset="player_week",
                key=artifact.key,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                row_count=3,
                schema_version=1,
            )
        ],
    )


# --- staging_dir / clear_staging ----------------------------------------


def test_staging_dir_layout(tmp_path: Path):
    assert staging_dir(tmp_path, "RUN123") == tmp_path / "staging" / "RUN123"


def test_clear_staging_removes_nested_files_and_directories(tmp_path: Path):
    staging = staging_dir(tmp_path, "RUN123")
    (staging / "raw").mkdir(parents=True)
    (staging / "raw" / "league.json").write_text("{}")
    (staging / "curated").mkdir()
    (staging / "curated" / "part-000.parquet").write_bytes(b"x")

    clear_staging(staging)

    assert not staging.exists()


def test_clear_staging_on_a_missing_directory_is_a_no_op(tmp_path: Path):
    clear_staging(tmp_path / "never-existed")  # must not raise


# --- publish_release -----------------------------------------------------


def test_publish_release_uploads_verifies_and_commits_in_order(tmp_path: Path):
    run_id = "20260922T000000Z-abc1234"
    artifact = _artifact(tmp_path, run_id)
    manifest = _manifest(run_id, artifact)
    store = _FakeStore()

    publish_release(store, "prod", [artifact], manifest)

    assert store.put_file_calls == [artifact.key]
    assert store.put_bytes_calls == [
        f"prod/releases/{run_id}/manifest.json",
        f"prod/manifests/history/{run_id}.json",
        "prod/manifests/latest.json",
    ]
    latest = DataManifest.model_validate_json(
        store.objects["prod/manifests/latest.json"]
    )
    assert latest == manifest


def test_publish_release_rejects_an_artifact_from_a_different_release(tmp_path: Path):
    run_id = "20260922T000000Z-abc1234"
    other_run_artifact = _artifact(tmp_path, "some-other-run")
    manifest = _manifest(run_id, other_run_artifact)
    store = _FakeStore()

    with pytest.raises(ObjectStoreError, match="does not contain"):
        publish_release(store, "prod", [other_run_artifact], manifest)

    assert store.put_file_calls == []  # rejected before any upload was attempted


def test_publish_release_never_touches_latest_json_if_remote_verification_fails(
    tmp_path: Path,
):
    """Issue 186's "Done when": injected failures never modify latest.json."""
    run_id = "20260922T000000Z-abc1234"
    artifact = _artifact(tmp_path, run_id)
    manifest = _manifest(run_id, artifact)
    store = _FakeStore()
    store.corrupt_head_for.add(artifact.key)

    with pytest.raises(ObjectStoreError, match="remote"):
        publish_release(store, "prod", [artifact], manifest)

    assert "prod/manifests/latest.json" not in store.objects


def test_publish_release_never_touches_latest_json_if_the_history_write_fails(
    tmp_path: Path,
):
    """Issue 186's "Done when": injected failures never modify latest.json,
    even after every artifact upload already succeeded."""
    run_id = "20260922T000000Z-abc1234"
    artifact = _artifact(tmp_path, run_id)
    manifest = _manifest(run_id, artifact)
    store = _FakeStore()
    store.fail_put_bytes_for.add(f"prod/manifests/history/{run_id}.json")

    with pytest.raises(ObjectStoreError, match="simulated failure"):
        publish_release(store, "prod", [artifact], manifest)

    assert "prod/manifests/latest.json" not in store.objects


# --- rollback_to -------------------------------------------------------


def test_rollback_to_republishes_a_previous_releases_manifest_byte_for_byte(
    tmp_path: Path,
):
    """Issue 186's "Done when": successful releases can be reproduced and
    rolled back."""
    store = _FakeStore()
    run_a = "20260920T000000Z-aaaaaaa"
    run_b = "20260922T000000Z-bbbbbbb"
    artifact_a = _artifact(tmp_path, run_a, name="a.parquet")
    artifact_b = _artifact(tmp_path, run_b, name="b.parquet")
    manifest_a = _manifest(run_a, artifact_a)
    manifest_b = _manifest(run_b, artifact_b)
    publish_release(store, "prod", [artifact_a], manifest_a)
    publish_release(store, "prod", [artifact_b], manifest_b)
    assert (
        store.objects["prod/manifests/latest.json"]
        != store.objects[f"prod/manifests/history/{run_a}.json"]
    )  # sanity: B is current, not A, before rollback

    rolled_back = rollback_to(store, "prod", run_a)

    assert rolled_back == manifest_a
    assert (
        store.objects["prod/manifests/latest.json"]
        == store.objects[f"prod/manifests/history/{run_a}.json"]
    )


def test_rollback_to_an_unknown_run_id_raises(tmp_path: Path):
    store = _FakeStore()

    with pytest.raises(ObjectStoreError):
        rollback_to(store, "prod", "never-published")
