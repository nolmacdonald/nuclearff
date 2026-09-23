"""Dataset contracts: declared shape and pre-publication data-quality checks.

Every curated dataset the cloud data platform publishes (GitHub Issue 182,
guide §10) must define its required columns, its natural key, which columns
may be null, and its expected row-count bounds *once*, declaratively, rather
than each writer hand-rolling its own checks. :class:`DatasetSchema` is that
declaration; :func:`validate_frame` is the one place every check runs,
called by :func:`nuclearff.data.parquet.write_parquet_artifact` before a
single byte is written — guide design posture: fail the workflow rather than
publish stale, empty, duplicate, or schema-invalid data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import polars as pl

from nuclearff.exceptions import DataQualityError


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    """The declared shape and quality bar for one curated dataset.

    Args:
        name: Dataset name, e.g. ``"player_week"`` — matches
            :attr:`nuclearff.data.manifest.ManifestObject.dataset`.
        columns: Every required column, mapped to its expected Polars dtype
            (either the bare class, e.g. ``pl.Int64``, or an instance —
            Polars treats both as equal). A column present in a frame but
            absent here is not itself an error (see :func:`validate_frame`)
            — this declares the required floor, not an exhaustive allow-list.
        natural_key: Column(s) that together must uniquely identify a row.
            Must be a subset of ``columns``.
        nullable: Columns (a subset of ``columns``) allowed to contain
            nulls. Every other declared column must have none.
        value_ranges: Optional inclusive ``(min, max)`` bounds for a column,
            e.g. ``{"week": (1, 22)}``.
        min_rows: Minimum acceptable row count. ``1`` by default — an empty
            dataset is refused unless a schema explicitly allows it
            (``min_rows=0``).
        max_rows: Maximum acceptable row count, or ``None`` for no upper
            bound.

    """

    name: str
    columns: Mapping[str, type[pl.DataType] | pl.DataType]
    natural_key: tuple[str, ...]
    nullable: frozenset[str] = frozenset()
    value_ranges: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    min_rows: int | None = 1
    max_rows: int | None = None

    def __post_init__(self) -> None:
        missing_key_columns = set(self.natural_key) - set(self.columns)
        if missing_key_columns:
            raise ValueError(
                f"natural_key column(s) {sorted(missing_key_columns)} for "
                f"dataset {self.name!r} are not declared in columns"
            )
        unknown_nullable = self.nullable - set(self.columns)
        if unknown_nullable:
            raise ValueError(
                f"nullable column(s) {sorted(unknown_nullable)} for dataset "
                f"{self.name!r} are not declared in columns"
            )


def validate_frame(schema: DatasetSchema, frame: pl.DataFrame) -> None:
    """Check ``frame`` against every rule in ``schema``, in a fixed order.

    Stops at the first violation rather than collecting every one — matches
    this project's existing fail-fast validation style (see
    :mod:`nuclearff.duckdb_io`'s identifier check,
    :func:`nuclearff.config.league.league_config_from_sleeper`).

    Args:
        schema: The dataset's declared contract.
        frame: The data to check, before it is written to Parquet.

    Raises:
        DataQualityError: On the first rule ``frame`` violates: a missing or
            mistyped column, a row count outside ``[min_rows, max_rows]``, a
            null in a non-nullable column, a value outside its declared
            range, or a duplicate natural key.
    """
    missing = [column for column in schema.columns if column not in frame.columns]
    if missing:
        raise DataQualityError(schema.name, f"missing required column(s): {missing}")

    for column, expected_dtype in schema.columns.items():
        actual_dtype = frame.schema[column]
        if actual_dtype != expected_dtype:
            raise DataQualityError(
                schema.name,
                f"column {column!r} has dtype {actual_dtype}, "
                f"expected {expected_dtype}",
            )

    row_count = frame.height
    if schema.min_rows is not None and row_count < schema.min_rows:
        raise DataQualityError(
            schema.name,
            f"{row_count} row(s) is below the minimum of {schema.min_rows}",
        )
    if schema.max_rows is not None and row_count > schema.max_rows:
        raise DataQualityError(
            schema.name,
            f"{row_count} row(s) exceeds the maximum of {schema.max_rows}",
        )

    non_nullable = [
        column for column in schema.columns if column not in schema.nullable
    ]
    for column in non_nullable:
        null_count = frame[column].null_count()
        if null_count:
            raise DataQualityError(
                schema.name,
                f"column {column!r} has {null_count} null value(s), not allowed",
            )

    for column, (low, high) in schema.value_ranges.items():
        out_of_range = frame.filter((pl.col(column) < low) | (pl.col(column) > high))
        if out_of_range.height:
            raise DataQualityError(
                schema.name,
                f"column {column!r} has {out_of_range.height} value(s) outside "
                f"the range [{low}, {high}]",
            )

    if schema.natural_key:
        duplicate_count = (
            frame.height - frame.unique(subset=list(schema.natural_key)).height
        )
        if duplicate_count:
            raise DataQualityError(
                schema.name,
                f"{duplicate_count} row(s) share a duplicate natural key "
                f"{schema.natural_key}",
            )
