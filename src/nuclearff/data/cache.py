"""Verified, content-addressed local cache for downloaded object-store data.

Guide §12 (GitHub Issue 182): before querying a curated dataset with DuckDB,
its Parquet bytes must already be sitting on local disk, verified against
the manifest's own SHA-256 -- never trusted by filename alone.
:func:`ensure_cached` is the guide's own algorithm (check cache -> re-hash
what's already there -> download if missing/invalid -> verify -> atomic
rename), keyed by SHA-256 so two releases that happen to reference an
identical object only ever download it once.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from nuclearff.data.keys import latest_manifest_key
from nuclearff.data.manifest import DataManifest, ManifestObject
from nuclearff.exceptions import ObjectStoreError
from nuclearff.provenance import sha256_file

logger = logging.getLogger(__name__)


class ObjectStoreLike(Protocol):
    """The subset of :class:`~nuclearff.data.object_store.ObjectStore` this
    module needs. A real ``ObjectStore`` satisfies this structurally with no
    changes; tests can substitute a small fake instead of a real boto3
    client -- the same posture the guide itself recommends for this layer.
    """

    def download_file(self, key: str, destination: Path) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...


def cache_path(cache_dir: Path, sha256: str) -> Path:
    """Resolve the on-disk path a given digest's cached object would live at.

    Args:
        cache_dir: The verified cache's root directory.
        sha256: The object's lowercase hex SHA-256 digest.

    Returns:
        ``<cache_dir>/sha256/<first two hex chars>/<digest>.parquet``, guide
        §12's own layout.
    """
    return cache_dir / "sha256" / sha256[:2] / f"{sha256}.parquet"


def _is_valid(path: Path, *, sha256: str, size_bytes: int) -> bool:
    """Check whether an already-downloaded file matches its expected digest/size.

    Args:
        path: Candidate cached file.
        sha256: Expected digest.
        size_bytes: Expected size.

    Returns:
        Whether ``path`` exists and both its size and hash match. Size is
        checked first since it's free, before paying for a full re-hash --
        guide §12's "hash an existing file before trusting it," with a cheap
        short-circuit added.
    """
    if not path.is_file():
        return False
    if path.stat().st_size != size_bytes:
        return False
    return sha256_file(path) == sha256


def ensure_cached(store: ObjectStoreLike, cache_dir: Path, obj: ManifestObject) -> Path:
    """Return a verified local path for ``obj``, downloading only if needed.

    An already-cached file is re-hashed before being trusted, not assumed
    valid just because it exists. A missing or corrupt entry is downloaded
    to a ``.part`` path by :meth:`ObjectStore.download_file` (itself
    atomic), then re-verified -- a caller can never receive a path to data
    that doesn't match the manifest.

    Args:
        store: An object store to download from, if needed.
        cache_dir: The verified cache's root directory.
        obj: The manifest object to materialize locally.

    Returns:
        A local path guaranteed to match ``obj.sha256`` and ``obj.size_bytes``.

    Raises:
        ObjectStoreError: If the freshly downloaded object still doesn't
            match its expected digest or size -- a corrupt upload or a
            manifest/object mismatch, not something to silently accept. The
            bad file is removed rather than left in the cache.
    """
    destination = cache_path(cache_dir, obj.sha256)
    if _is_valid(destination, sha256=obj.sha256, size_bytes=obj.size_bytes):
        return destination

    logger.info("Cache miss for %s (dataset=%s); downloading", obj.sha256, obj.dataset)
    store.download_file(obj.key, destination)

    if not _is_valid(destination, sha256=obj.sha256, size_bytes=obj.size_bytes):
        destination.unlink(missing_ok=True)
        raise ObjectStoreError(
            obj.key,
            "downloaded object does not match the manifest (expected "
            f"sha256={obj.sha256}, size_bytes={obj.size_bytes})",
        )

    return destination


def resolve_latest_manifest(store: ObjectStoreLike, prefix: str) -> DataManifest:
    """Fetch and parse the current release's manifest from the object store.

    Args:
        store: An object store to read from.
        prefix: Environment prefix (:attr:`nuclearff.data.config.StorageConfig.prefix`).

    Returns:
        The parsed, current :class:`~nuclearff.data.manifest.DataManifest`.
    """
    payload = store.get_bytes(latest_manifest_key(prefix))
    return DataManifest.model_validate_json(payload)


def materialize_manifest(
    store: ObjectStoreLike, cache_dir: Path, manifest: DataManifest
) -> dict[str, Path]:
    """Ensure every object a manifest references is cached locally.

    Args:
        store: An object store to download from, if needed.
        cache_dir: The verified cache's root directory.
        manifest: The release to materialize.

    Returns:
        Each object's key mapped to its verified local path, in manifest order.
    """
    return {obj.key: ensure_cached(store, cache_dir, obj) for obj in manifest.objects}


def prune_cache(cache_dir: Path, keep_digests: set[str]) -> int:
    """Delete cached objects whose digest is not in ``keep_digests``.

    A cache cleanup *mechanism*, not a retention *policy* -- deciding how
    many past releases' digests belong in ``keep_digests`` (keep the last N,
    keep anything referenced by a retained history manifest, ...) is GitHub
    Issue 191's job. This is the primitive that policy calls.

    Args:
        cache_dir: The verified cache's root directory.
        keep_digests: SHA-256 digests to keep.

    Returns:
        The number of cached files removed. ``0`` if the cache has no
        ``sha256/`` directory yet, rather than raising.
    """
    sha256_root = cache_dir / "sha256"
    if not sha256_root.is_dir():
        return 0

    removed = 0
    for path in sha256_root.glob("*/*.parquet"):
        if path.stem not in keep_digests:
            path.unlink()
            removed += 1

    return removed
