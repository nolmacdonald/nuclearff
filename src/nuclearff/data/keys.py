"""Deterministic, safe object-key construction for the R2/S3 data platform.

Every object nuclearff ever publishes is addressed by one of the key builders
here, so the layout described in the cloud data platform guide (GitHub Issue
182, guide §7) is produced in exactly one place rather than re-assembled ad
hoc by every writer. :func:`validate_key` is the safety net every write must
pass through before it reaches the object store (see
:mod:`nuclearff.data.object_store`) — manifests must never store a provider
URL, only these provider-neutral keys (guide design principle #4).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from nuclearff.exceptions import ObjectStoreError

RELEASE_MANIFEST_NAME = "manifest.json"
LATEST_MANIFEST_NAME = "latest.json"


def run_id(now: datetime, git_sha: str) -> str:
    """Build an immutable release identifier: a UTC timestamp plus a short git SHA.

    Args:
        now: The timestamp to encode. Must be timezone-aware; callers pass
            ``datetime.now(tz=UTC)``.
        git_sha: The full (or already-short) git commit SHA for this release.

    Returns:
        ``"<%Y%m%dT%H%M%SZ>-<7-char short SHA>"``, e.g.
        ``"20260922T101530Z-a1b2c3d"``.
    """
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{git_sha[:7]}"


def format_partitions(partitions: Mapping[str, int | str]) -> str:
    """Render Hive-style partition segments in insertion order.

    Args:
        partitions: Partition column name to value, e.g. ``{"season": 2026,
            "week": 3}``. Order is preserved from the mapping.

    Returns:
        ``"season=2026/week=03"``-style path segment. An integer ``"week"``
        value is zero-padded to two digits (guide §7's explicit rule — NFL
        weeks run 1-22); every other value is rendered with plain ``str()``.
    """
    parts = []
    for key, value in partitions.items():
        if key == "week" and isinstance(value, int):
            parts.append(f"{key}={value:02d}")
        else:
            parts.append(f"{key}={value}")
    return "/".join(parts)


def object_key(
    prefix: str,
    *segments: str,
    filename: str,
    partitions: Mapping[str, int | str] | None = None,
) -> str:
    """Build a full object key from a prefix, path segments, and a filename.

    Args:
        prefix: Environment prefix (:attr:`nuclearff.data.config.StorageConfig.prefix`).
        *segments: Fixed path segments, e.g. ``"raw", "sleeper", "matchups"``.
        filename: The final path component, e.g. ``"matchups.json.gz"`` or
            ``"part-000.parquet"``.
        partitions: Optional Hive-style partitions inserted between
            ``segments`` and ``filename`` (see :func:`format_partitions`).

    Returns:
        The full key, e.g.
        ``"prod/raw/sleeper/matchups/season=2026/week=03/matchups.json.gz"``.
    """
    parts = [prefix, *segments]
    if partitions:
        parts.append(format_partitions(partitions))
    parts.append(filename)
    return "/".join(parts)


def release_manifest_key(prefix: str, run_id: str) -> str:
    """Key for one release's own immutable manifest copy.

    Args:
        prefix: Environment prefix.
        run_id: The release identifier (:func:`run_id`).

    Returns:
        ``"<prefix>/releases/<run_id>/manifest.json"``.
    """
    return f"{prefix}/releases/{run_id}/{RELEASE_MANIFEST_NAME}"


def history_manifest_key(prefix: str, run_id: str) -> str:
    """Key for one release's entry in the immutable manifest history.

    Args:
        prefix: Environment prefix.
        run_id: The release identifier (:func:`run_id`).

    Returns:
        ``"<prefix>/manifests/history/<run_id>.json"``.
    """
    return f"{prefix}/manifests/history/{run_id}.json"


def latest_manifest_key(prefix: str) -> str:
    """Key for the mutable pointer to the current release.

    Args:
        prefix: Environment prefix.

    Returns:
        ``"<prefix>/manifests/latest.json"``. The one key in this module
        that is ever overwritten in place — every other key is immutable
        once published (guide design principle #2).
    """
    return f"{prefix}/manifests/{LATEST_MANIFEST_NAME}"


def validate_key(key: str, *, prefix: str) -> None:
    """Reject an object key that could escape its configured environment.

    Every write path (see :mod:`nuclearff.data.object_store`) must call this
    before issuing a request — guide §9/§18's security controls.

    Args:
        key: The object key to validate.
        prefix: The environment prefix this key must stay under
            (:attr:`nuclearff.data.config.StorageConfig.prefix`).

    Raises:
        ObjectStoreError: If ``key`` contains a ``".."`` path segment, starts
            with ``"/"``, or does not start with ``"<prefix>/"``.
    """
    if key.startswith("/"):
        raise ObjectStoreError(key, "key must not start with '/'")
    if ".." in key.split("/"):
        raise ObjectStoreError(key, "key must not contain a '..' path segment")
    if not key.startswith(f"{prefix}/"):
        raise ObjectStoreError(key, f"key must start with configured prefix {prefix!r}")
