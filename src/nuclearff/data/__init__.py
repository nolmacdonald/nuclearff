"""Provider-neutral cloud data platform: config, keys, manifests, object store.

See the cloud data platform epic (GitHub Issue 182) for the full design and
implementation sequence. This package currently covers Issues 183 and 184
(storage contracts/configuration and the object-store adapter) — the
Parquet, publisher, cache, and DuckDB-repository layers described in the
epic are not implemented yet.
"""

from nuclearff.data.cache import (
    cache_path,
    ensure_cached,
    materialize_manifest,
    prune_cache,
    resolve_latest_manifest,
)
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
from nuclearff.data.publisher import (
    ReleaseArtifact,
    clear_staging,
    publish_release,
    release_artifact,
    rollback_to,
    staging_dir,
)
from nuclearff.data.repository import (
    connect_analytics,
    dataset_paths,
    load_player_week,
    read_parquet_paths,
)
from nuclearff.data.schema import DatasetSchema, validate_frame

__all__ = [
    "PLAYER_WEEK_SCHEMA",
    "DataManifest",
    "DatasetSchema",
    "ManifestObject",
    "ObjectStore",
    "ParquetArtifact",
    "ReleaseArtifact",
    "StorageConfig",
    "StorageProvider",
    "cache_path",
    "clear_staging",
    "connect_analytics",
    "dataset_paths",
    "ensure_cached",
    "format_partitions",
    "history_manifest_key",
    "latest_manifest_key",
    "load_player_week",
    "manifest_json_schema",
    "materialize_manifest",
    "object_key",
    "prune_cache",
    "publish_release",
    "read_parquet_paths",
    "release_artifact",
    "release_manifest_key",
    "resolve_latest_manifest",
    "rollback_to",
    "run_id",
    "staging_dir",
    "validate_frame",
    "validate_key",
    "write_parquet_artifact",
]
