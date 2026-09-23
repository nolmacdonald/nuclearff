"""Unit tests for the verified, content-addressed local object cache."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nuclearff.data.cache import (
    cache_path,
    ensure_cached,
    materialize_manifest,
    prune_cache,
    resolve_latest_manifest,
)
from nuclearff.data.manifest import DataManifest, ManifestObject
from nuclearff.exceptions import ObjectStoreError


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _FakeStore:
    """A minimal `ObjectStoreLike`: serves fixed bytes per key, no network."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.download_calls: list[str] = []
        self.get_bytes_calls: list[str] = []

    def download_file(self, key: str, destination: Path) -> None:
        self.download_calls.append(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects[key])

    def get_bytes(self, key: str) -> bytes:
        self.get_bytes_calls.append(key)
        return self.objects[key]


class _RaisingStore:
    """A store whose `download_file` fails the test if ever called."""

    def download_file(self, key: str, destination: Path) -> None:
        raise AssertionError(f"download_file should not have been called for {key}")

    def get_bytes(self, key: str) -> bytes:
        raise AssertionError("get_bytes should not have been called")


def _manifest_object(
    payload: bytes,
    *,
    key: str = "prod/releases/RUN/x.parquet",
    partitions: dict[str, str] | None = None,
) -> ManifestObject:
    return ManifestObject(
        dataset="player_week",
        key=key,
        content_type="application/vnd.apache.parquet",
        size_bytes=len(payload),
        sha256=_digest(payload),
        row_count=1,
        schema_version=1,
        partitions=partitions or {},
    )


# --- cache_path --------------------------------------------------------


def test_cache_path_uses_the_first_two_digest_characters_as_a_subdirectory(
    tmp_path: Path,
):
    digest = "e7bf4d9c" + "0" * 56

    path = cache_path(tmp_path, digest)

    assert path == tmp_path / "sha256" / "e7" / f"{digest}.parquet"


# --- ensure_cached -------------------------------------------------------


def test_ensure_cached_downloads_on_a_cache_miss(tmp_path: Path):
    payload = b"parquet-bytes"
    obj = _manifest_object(payload)
    store = _FakeStore({obj.key: payload})

    path = ensure_cached(store, tmp_path, obj)

    assert path.read_bytes() == payload
    assert store.download_calls == [obj.key]


def test_ensure_cached_skips_the_download_on_a_valid_cache_hit(tmp_path: Path):
    payload = b"already-cached"
    obj = _manifest_object(payload)
    destination = cache_path(tmp_path, obj.sha256)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(payload)

    path = ensure_cached(_RaisingStore(), tmp_path, obj)

    assert path == destination


def test_ensure_cached_redownloads_a_corrupted_cache_entry(tmp_path: Path):
    payload = b"correct-bytes"
    obj = _manifest_object(payload)
    destination = cache_path(tmp_path, obj.sha256)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"wrong-bytes-same-name")  # wrong content, right path
    store = _FakeStore({obj.key: payload})

    path = ensure_cached(store, tmp_path, obj)

    assert path.read_bytes() == payload
    assert store.download_calls == [obj.key]


def test_ensure_cached_raises_and_cleans_up_if_the_download_still_does_not_match(
    tmp_path: Path,
):
    payload = b"expected-bytes"
    obj = _manifest_object(payload)
    store = _FakeStore({obj.key: b"actually-downloaded-something-else"})

    with pytest.raises(ObjectStoreError, match=obj.sha256):
        ensure_cached(store, tmp_path, obj)

    assert not cache_path(tmp_path, obj.sha256).exists()


def test_ensure_cached_downloads_two_different_releases_sharing_a_digest_only_once(
    tmp_path: Path,
):
    """Guide §12 / issue 187's "Done when": identical objects across
    releases are downloaded once."""
    payload = b"shared-across-releases"
    release_a = _manifest_object(payload, key="prod/releases/A/x.parquet")
    release_b = _manifest_object(payload, key="prod/releases/B/x.parquet")
    store = _FakeStore({release_a.key: payload, release_b.key: payload})

    path_a = ensure_cached(store, tmp_path, release_a)
    path_b = ensure_cached(store, tmp_path, release_b)

    assert path_a == path_b
    assert store.download_calls == [release_a.key]  # not called again for release_b


# --- resolve_latest_manifest -------------------------------------------


def test_resolve_latest_manifest_reads_and_parses_latest_json():
    manifest = DataManifest(
        schema_version=1,
        run_id="20260922T000000Z-abc1234",
        created_at_utc="2026-09-22T00:00:00Z",  # type: ignore[arg-type]
    )
    store = _FakeStore(
        {"prod/manifests/latest.json": manifest.model_dump_json().encode()}
    )

    resolved = resolve_latest_manifest(store, "prod")

    assert resolved == manifest
    assert store.get_bytes_calls == ["prod/manifests/latest.json"]


# --- materialize_manifest -------------------------------------------------


def test_materialize_manifest_ensures_every_object_is_cached(tmp_path: Path):
    payload_a = b"object-a"
    payload_b = b"object-b"
    obj_a = _manifest_object(
        payload_a, key="prod/releases/RUN/a.parquet", partitions={"part": "000"}
    )
    obj_b = _manifest_object(
        payload_b, key="prod/releases/RUN/b.parquet", partitions={"part": "001"}
    )
    manifest = DataManifest(
        schema_version=1,
        run_id="20260922T000000Z-abc1234",
        created_at_utc="2026-09-22T00:00:00Z",  # type: ignore[arg-type]
        objects=[obj_a, obj_b],
    )
    store = _FakeStore({obj_a.key: payload_a, obj_b.key: payload_b})

    paths = materialize_manifest(store, tmp_path, manifest)

    assert set(paths) == {obj_a.key, obj_b.key}
    assert paths[obj_a.key].read_bytes() == payload_a
    assert paths[obj_b.key].read_bytes() == payload_b


# --- prune_cache -----------------------------------------------------------


def test_prune_cache_removes_unreferenced_digests_and_keeps_the_rest(tmp_path: Path):
    keep_digest = "a" * 64
    drop_digest = "b" * 64
    keep_path = cache_path(tmp_path, keep_digest)
    drop_path = cache_path(tmp_path, drop_digest)
    for path in (keep_path, drop_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    removed = prune_cache(tmp_path, {keep_digest})

    assert removed == 1
    assert keep_path.exists()
    assert not drop_path.exists()


def test_prune_cache_on_a_missing_cache_dir_returns_zero(tmp_path: Path):
    assert prune_cache(tmp_path / "never-created", set()) == 0
