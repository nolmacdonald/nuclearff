"""Writing curated Parquet artifacts, validated against a dataset's schema.

Guide §10 (GitHub Issue 182): a curated dataset is written locally as a
deterministic, zstd-compressed Parquet file before it is ever uploaded, and
must pass its :class:`~nuclearff.data.schema.DatasetSchema`'s checks first —
:func:`write_parquet_artifact` is that single choke point. The returned
:class:`ParquetArtifact` carries exactly the fields
:class:`nuclearff.data.manifest.ManifestObject` needs (``size_bytes``,
``sha256``, ``row_count``), for a future publisher (GitHub Issue 186) to
assemble a manifest from without recomputing anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from nuclearff.data.schema import DatasetSchema, validate_frame
from nuclearff.provenance import sha256_file


@dataclass(frozen=True, slots=True)
class ParquetArtifact:
    """A validated, written-to-disk curated Parquet file.

    Args:
        path: Where the file was written.
        row_count: Number of rows written.
        size_bytes: File size on disk.
        sha256: Hex digest of the file's exact bytes
            (:func:`nuclearff.provenance.sha256_file`).

    """

    path: Path
    row_count: int
    size_bytes: int
    sha256: str


def write_parquet_artifact(
    frame: pl.DataFrame, destination: Path, *, schema: DatasetSchema
) -> ParquetArtifact:
    """Validate ``frame`` against ``schema`` and write it as compressed Parquet.

    Writes to a sibling ``.part`` path first and renames it into place only
    after the write succeeds, so a reader can never observe a partially
    written file at ``destination`` — the same atomic-write pattern
    :meth:`nuclearff.data.object_store.ObjectStore.download_file` already
    uses on the read side.

    Args:
        frame: The data to write.
        destination: Local path to write to. Parent directories are created
            if absent.
        schema: The dataset's declared contract
            (:func:`nuclearff.data.schema.validate_frame` runs first).

    Returns:
        The written artifact's path, row count, size, and digest.

    Raises:
        DataQualityError: If ``frame`` fails any of ``schema``'s checks.
            Nothing is written in that case.
    """
    validate_frame(schema, frame)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    frame.write_parquet(temporary, compression="zstd", statistics=True)
    temporary.replace(destination)

    return ParquetArtifact(
        path=destination,
        row_count=frame.height,
        size_bytes=destination.stat().st_size,
        sha256=sha256_file(destination),
    )
