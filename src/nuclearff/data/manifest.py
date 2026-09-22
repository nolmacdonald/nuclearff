"""The release manifest: the commit point for one published data release.

A publish (GitHub Issue 186, not yet implemented) uploads a set of immutable
Parquet objects and then, last, a manifest describing them — the manifest is
what makes that set of objects one coherent, queryable release (guide design
principle #3). :class:`DataManifest` is that model.

**Not the same thing as** :class:`nuclearff.provenance.RunManifest`, despite
the similar name and the shared ``run_id`` concept. ``RunManifest`` is a
*local* record of what code/config/inputs produced one pipeline run (git
commit, config hash, source hashes) — nothing publishes or reads it back
across machines. ``DataManifest`` is the opposite: a *published*, remote
pointer to a set of Parquet objects in object storage, read by every
consumer (the dashboard, a future report) to know what data exists and where.
A future pipeline run could reasonably produce both — a ``RunManifest`` for
its own provenance, and a ``DataManifest`` if it published data — but they
answer different questions and are kept as separate types rather than one
overloaded ``Manifest``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MANIFEST_VERSION = 1
"""Current manifest schema version, written to every :class:`DataManifest`."""


class ManifestObject(BaseModel):
    """One published object within a release.

    Args:
        dataset: Logical dataset name, e.g. ``"player_week"``.
        key: The object's provider-neutral key (see
            :mod:`nuclearff.data.keys`) — never a full ``r2://`` or ``s3://``
            URL (guide design principle #4).
        content_type: MIME type, e.g. ``"application/vnd.apache.parquet"``.
        size_bytes: Uploaded object size, matching the store's
            ``ContentLength`` after upload.
        sha256: Lowercase hex SHA-256 digest of the exact uploaded bytes.
        row_count: Row count of the underlying data, computed after the
            final transform.
        schema_version: The schema version this object's data conforms to.
        partitions: Hive-style partition values for this object, e.g.
            ``{"season": "2026", "week": "03"}`` — matches
            :func:`nuclearff.data.keys.format_partitions`'s input shape.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    key: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0)
    schema_version: int
    partitions: dict[str, str] = Field(default_factory=dict)


class DataManifest(BaseModel):
    """An immutable, published release: a coherent set of dataset objects.

    See the module docstring for how this differs from
    :class:`nuclearff.provenance.RunManifest`.

    Args:
        manifest_version: Format version of this manifest shape itself.
            Defaults to :data:`MANIFEST_VERSION`.
        schema_version: Data schema version this release's objects conform
            to (an object may declare a different, per-dataset
            ``schema_version`` on itself if a migration is in progress).
        run_id: This release's identifier (:func:`nuclearff.data.keys.run_id`).
        created_at_utc: When this manifest was assembled.
        git_sha: The commit SHA the publishing workflow ran at, if known.
        league_id: The Sleeper league this release covers, if scoped to one.
        season: The season this release covers, if scoped to one.
        objects: Every object this release published.

    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: int = MANIFEST_VERSION
    schema_version: int
    run_id: str
    created_at_utc: datetime
    git_sha: str | None = None
    league_id: str | None = None
    season: int | None = None
    objects: list[ManifestObject] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_no_duplicate_partitions(self) -> DataManifest:
        """Reject two objects publishing the same dataset/partition combination.

        Guide §8's required invariant: "Duplicate dataset/partition
        combinations are rejected."
        """
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for obj in self.objects:
            fingerprint = (obj.dataset, tuple(sorted(obj.partitions.items())))
            if fingerprint in seen:
                raise ValueError(
                    "duplicate dataset/partition combination in manifest: "
                    f"dataset={obj.dataset!r} partitions={obj.partitions!r}"
                )
            seen.add(fingerprint)
        return self


def manifest_json_schema() -> dict[str, Any]:
    """Return :class:`DataManifest`'s JSON Schema, sorted for deterministic output.

    Backs ``schemas/manifest-v1.schema.json`` — see
    ``tests/test_data_manifest.py`` for the drift guard that keeps the
    checked-in file matching this function's current output.

    Returns:
        The schema as a plain, JSON-serializable dict with every nested
        mapping's keys sorted.
    """

    def _sort(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _sort(value[k]) for k in sorted(value, key=str)}
        if isinstance(value, list):
            return [_sort(item) for item in value]
        return value

    return _sort(DataManifest.model_json_schema())
