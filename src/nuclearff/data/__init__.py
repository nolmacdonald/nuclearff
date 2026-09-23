"""Provider-neutral cloud data platform: config, keys, manifests, object store.

See the cloud data platform epic (GitHub Issue 182) for the full design and
implementation sequence. This package currently covers Issues 183 and 184
(storage contracts/configuration and the object-store adapter) — the
Parquet, publisher, cache, and DuckDB-repository layers described in the
epic are not implemented yet.
"""

from nuclearff.data.config import StorageConfig, StorageProvider
from nuclearff.data.datasets import PLAYER_WEEK_SCHEMA
from nuclearff.data.keys import (
    format_partitions,
    history_manifest_key,
    latest_manifest_key,
    object_key,
    release_manifest_key,
    run_id,
    validate_key,
)
from nuclearff.data.manifest import DataManifest, ManifestObject, manifest_json_schema
from nuclearff.data.object_store import ObjectStore
from nuclearff.data.parquet import ParquetArtifact, write_parquet_artifact
from nuclearff.data.schema import DatasetSchema, validate_frame

__all__ = [
    "PLAYER_WEEK_SCHEMA",
    "DataManifest",
    "DatasetSchema",
    "ManifestObject",
    "ObjectStore",
    "ParquetArtifact",
    "StorageConfig",
    "StorageProvider",
    "format_partitions",
    "history_manifest_key",
    "latest_manifest_key",
    "manifest_json_schema",
    "object_key",
    "release_manifest_key",
    "run_id",
    "validate_frame",
    "validate_key",
    "write_parquet_artifact",
]
