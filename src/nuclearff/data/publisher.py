"""Atomic publication: upload a release's artifacts, then commit the manifest.

Guide §11 (GitHub Issue 182): object stores have no multi-object
transaction, so a release is modeled as immutable artifacts uploaded and
verified first, followed by a manifest-pointer update as the single commit
point. :func:`publish_release` is that two-phase sequence: every artifact is
uploaded and its remote copy verified (guide steps 6-8) before the manifest
is written to its three locations, ``latest.json`` last (steps 9-11) -- a
failure anywhere before the final ``latest.json`` write leaves the current
release untouched, since nothing before it can partially commit.

This module does not fetch source data or write Parquet -- that's
:mod:`nuclearff.data.parquet` (GitHub Issue 185) and the not-yet-built
fetch/normalize step. It only orchestrates already-built local artifacts
into one published release.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nuclearff.data.keys import (
    history_manifest_key,
    latest_manifest_key,
    release_manifest_key,
)
from nuclearff.data.manifest import DataManifest
from nuclearff.data.parquet import ParquetArtifact
from nuclearff.exceptions import ObjectStoreError

MANIFEST_CONTENT_TYPE = "application/json"


class PublisherStoreLike(Protocol):
    """The subset of :class:`~nuclearff.data.object_store.ObjectStore` this
    module needs. See :class:`nuclearff.data.cache.ObjectStoreLike` for the
    same reasoning -- a real ``ObjectStore`` satisfies this structurally,
    tests use a small fake instead.
    """

    def put_file(
        self, local_path: Path, key: str, *, content_type: str, sha256: str
    ) -> None: ...

    def put_bytes(self, payload: bytes, key: str, *, content_type: str) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def head(self, key: str) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    """One immutable object a release uploads.

    Args:
        local_path: The already-written local file (typically a
            :class:`~nuclearff.data.parquet.ParquetArtifact`'s ``path``).
        key: Its destination object-store key.
        content_type: MIME type to record on the object.
        sha256: Expected digest of ``local_path``'s bytes.
        size_bytes: Expected size of ``local_path``.

    """

    local_path: Path
    key: str
    content_type: str
    sha256: str
    size_bytes: int


def release_artifact(
    artifact: ParquetArtifact,
    *,
    key: str,
    content_type: str = "application/vnd.apache.parquet",
) -> ReleaseArtifact:
    """Build a :class:`ReleaseArtifact` from an already-written Parquet artifact.

    Args:
        artifact: The output of
            :func:`nuclearff.data.parquet.write_parquet_artifact`.
        key: Its destination object-store key.
        content_type: MIME type to record on the object.

    Returns:
        A release artifact ready for :func:`publish_release`.
    """
    return ReleaseArtifact(
        local_path=artifact.path,
        key=key,
        content_type=content_type,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
    )


def staging_dir(root: Path, run_id: str) -> Path:
    """Resolve one release's local staging directory.

    Args:
        root: The data root (typically
            :attr:`~nuclearff.config.models.PathsConfig.data`).
        run_id: The release identifier.

    Returns:
        ``<root>/staging/<run_id>``, guide §11's own layout.
    """
    return root / "staging" / run_id


def clear_staging(path: Path) -> None:
    """Remove a release's local staging directory after a successful publish.

    Args:
        path: A directory returned by :func:`staging_dir`. Removing a
            directory that doesn't exist (or never existed) is not an
            error.
    """
    if not path.is_dir():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def _verify_remote_artifact(
    store: PublisherStoreLike, artifact: ReleaseArtifact
) -> None:
    """Confirm an uploaded object's remote size and digest match what was sent.

    Args:
        store: The object store the artifact was just uploaded to.
        artifact: The artifact to verify.

    Raises:
        ObjectStoreError: If the remote object's size or ``sha256`` metadata
            doesn't match what :func:`publish_release` uploaded.
    """
    head = store.head(artifact.key)
    remote_size = head.get("ContentLength")
    if remote_size != artifact.size_bytes:
        raise ObjectStoreError(
            artifact.key,
            f"remote size {remote_size} does not match expected {artifact.size_bytes}",
        )
    metadata = head.get("Metadata")
    remote_sha256 = metadata.get("sha256") if isinstance(metadata, dict) else None
    if remote_sha256 != artifact.sha256:
        raise ObjectStoreError(
            artifact.key,
            f"remote sha256 metadata {remote_sha256!r} does not match "
            f"expected {artifact.sha256!r}",
        )


def _verify_latest_manifest(
    store: PublisherStoreLike, prefix: str, *, expected_run_id: str
) -> None:
    """Read `latest.json` back and confirm it points at the expected release.

    Args:
        store: The object store to read from.
        prefix: Environment prefix.
        expected_run_id: The ``run_id`` that should now be current.

    Raises:
        ObjectStoreError: If `latest.json` cannot be read back, or reads
            back with a different ``run_id`` than expected.
    """
    payload = store.get_bytes(latest_manifest_key(prefix))
    manifest = DataManifest.model_validate_json(payload)
    if manifest.run_id != expected_run_id:
        raise ObjectStoreError(
            latest_manifest_key(prefix),
            f"latest.json reads back run_id={manifest.run_id!r}, "
            f"expected {expected_run_id!r}",
        )


def publish_release(
    store: PublisherStoreLike,
    prefix: str,
    artifacts: list[ReleaseArtifact],
    manifest: DataManifest,
) -> None:
    """Upload every artifact, verify it, then commit the manifest last.

    Guide steps 6-12: every artifact is uploaded and its remote copy
    verified before anything manifest-related is written. The manifest is
    then written to its release and history locations, and only then to
    ``latest.json`` -- the single commit point -- which is immediately read
    back to confirm it. A failure at any point before the ``latest.json``
    write leaves the previous release fully intact, since nothing written
    before it is ever consulted by a reader (see
    :mod:`nuclearff.data.cache`, which only ever resolves `latest.json`).

    Args:
        store: The object store to publish to.
        prefix: Environment prefix.
        artifacts: Every object this release uploads. Each artifact's
            ``key`` must contain ``manifest.run_id`` — a caller bug
            (an artifact built for a different release) is rejected before
            any upload happens, not discovered partway through.
        manifest: The release manifest. Written verbatim (the exact same
            serialized bytes) to all three manifest locations, satisfying
            guide §8's invariant that `latest.json` is byte-for-byte
            identical to its `manifests/history/` copy.

    Raises:
        ObjectStoreError: If an artifact's key doesn't belong to this
            release, an upload's remote verification fails, or the final
            `latest.json` readback doesn't match.
    """
    for artifact in artifacts:
        if manifest.run_id not in artifact.key:
            raise ObjectStoreError(
                artifact.key,
                f"artifact key does not contain this release's run_id "
                f"{manifest.run_id!r}",
            )

    for artifact in artifacts:
        store.put_file(
            artifact.local_path,
            artifact.key,
            content_type=artifact.content_type,
            sha256=artifact.sha256,
        )
        _verify_remote_artifact(store, artifact)

    manifest_bytes = manifest.model_dump_json().encode("utf-8")
    store.put_bytes(
        manifest_bytes,
        release_manifest_key(prefix, manifest.run_id),
        content_type=MANIFEST_CONTENT_TYPE,
    )
    store.put_bytes(
        manifest_bytes,
        history_manifest_key(prefix, manifest.run_id),
        content_type=MANIFEST_CONTENT_TYPE,
    )

    # Commit point: every immutable object and both durable manifest copies
    # are already verified: only `latest.json` is left to write.
    store.put_bytes(
        manifest_bytes, latest_manifest_key(prefix), content_type=MANIFEST_CONTENT_TYPE
    )
    _verify_latest_manifest(store, prefix, expected_run_id=manifest.run_id)


def rollback_to(store: PublisherStoreLike, prefix: str, run_id: str) -> DataManifest:
    """Point `latest.json` back at a previously published release.

    Guide §20's recovery procedure for "bad transformation discovered":
    republish a previously validated immutable manifest as the current
    release. Republishes the history copy's exact bytes rather than
    re-serializing the parsed model, so `latest.json` stays byte-for-byte
    identical to its `manifests/history/` copy — the same invariant
    :func:`publish_release` maintains. Does not touch or re-upload any
    Parquet object; those are already immutable and untouched by this call.

    Args:
        store: The object store to roll back.
        prefix: Environment prefix.
        run_id: The release to make current again. Must already have a
            `manifests/history/<run_id>.json` entry.

    Returns:
        The manifest that is now current.

    Raises:
        ObjectStoreError: If the history manifest for ``run_id`` cannot be
            read, or the post-rollback `latest.json` readback doesn't match.
    """
    payload = store.get_bytes(history_manifest_key(prefix, run_id))
    store.put_bytes(
        payload, latest_manifest_key(prefix), content_type=MANIFEST_CONTENT_TYPE
    )
    _verify_latest_manifest(store, prefix, expected_run_id=run_id)
    return DataManifest.model_validate_json(payload)
