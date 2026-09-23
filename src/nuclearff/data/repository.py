"""DuckDB query layer over locally cached, manifest-selected Parquet files.

Guide §13 (GitHub Issue 182): once a dataset's objects are verified onto
local disk (:mod:`nuclearff.data.cache`), querying them is an in-memory
DuckDB connection reading Parquet directly -- no persistent local database
file, unlike :mod:`nuclearff.duckdb_io`'s mutable per-league cache tables,
a different, unrelated system. :func:`dataset_paths` is the seam between
the two layers: it selects only the local paths a manifest actually
declares for one dataset, so a query never reads a stray file that happens
to be sitting in the cache directory for an unrelated reason.

Results come back via ``fetchall()`` + ``pl.DataFrame(..., orient="row")``,
not DuckDB's ``.fetch_arrow_table()`` / ``pl.from_arrow`` -- the same
choice :mod:`nuclearff.duckdb_io` already made and documents: the Arrow
path requires ``pyarrow``, which neither ``duckdb`` nor ``polars`` declares
as a dependency, for what is, so far, a handful of typed columns.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import polars as pl

from nuclearff.data.manifest import DataManifest

DEFAULT_THREADS = 2
"""Default DuckDB thread count for an analytics connection, guide §13's own value."""


def connect_analytics(*, threads: int = DEFAULT_THREADS) -> duckdb.DuckDBPyConnection:
    """Open a fresh in-memory DuckDB connection for querying cached Parquet.

    Args:
        threads: DuckDB's own ``threads`` setting.

    Returns:
        A new connection. Supports the context-manager protocol
        (``with connect_analytics() as conn:``) for a caller that wants it
        closed automatically; a long-lived caller (e.g. a future Streamlit
        `@st.cache_resource`, GitHub Issue 190) is responsible for closing
        or replacing it itself.
    """
    connection = duckdb.connect(":memory:")
    connection.execute(f"SET threads = {int(threads)}")
    return connection


def dataset_paths(
    manifest: DataManifest, materialized: dict[str, Path], dataset: str
) -> list[Path]:
    """Select one dataset's local paths from an already-materialized manifest.

    The seam between :func:`nuclearff.data.cache.materialize_manifest`
    (which caches *everything* a manifest references) and a query function
    below (which must only ever read the one dataset it was asked for).

    Args:
        manifest: The release manifest, for each object's declared
            ``dataset``.
        materialized: Object key -> local path, from
            :func:`nuclearff.data.cache.materialize_manifest`.
        dataset: The dataset name to select, e.g. ``"player_week"``.

    Returns:
        Local paths for every object in ``manifest`` whose ``dataset``
        matches, in manifest order. Empty if the manifest has none.
    """
    return [materialized[obj.key] for obj in manifest.objects if obj.dataset == dataset]


def read_parquet_paths(
    connection: duckdb.DuckDBPyConnection,
    paths: Sequence[Path],
    *,
    columns: Sequence[str],
    where_sql: str | None = None,
    params: Sequence[object] | None = None,
    union_by_name: bool = False,
) -> pl.DataFrame:
    """Query an explicit set of Parquet files, selecting only named columns.

    The shared primitive every dataset-specific query function (e.g.
    :func:`load_player_week`) is built on. Guide §13's guidelines, applied
    directly: only the requested columns are ever selected (never
    ``SELECT *``); filters are pushed into SQL, not applied after loading;
    ``hive_partitioning`` is on so DuckDB can prune by the ``season=.../
    week=...`` partition segments in ``paths``; a column absent from
    ``paths`` raises DuckDB's own binder error rather than silently
    returning fewer columns than asked for.

    Args:
        connection: An open DuckDB connection (:func:`connect_analytics`).
        paths: Local Parquet file paths to read — typically
            :func:`dataset_paths`'s output, never a whole cache directory.
        columns: Columns to select, in order.
        where_sql: An optional ``WHERE`` clause body (no leading ``WHERE``),
            using ``?`` placeholders bound positionally from ``params``.
        params: Values for ``where_sql``'s placeholders, in order.
        union_by_name: Align columns by name across files with different
            schemas (a column absent from one file reads as ``NULL`` for
            its rows) instead of requiring identical schemas. Guide §13:
            "use only during controlled additive schema transitions" — not
            the default, since it silently tolerates a real schema drift
            that might otherwise deserve to be caught.

    Returns:
        The query result as a Polars DataFrame.

    Raises:
        ValueError: If ``paths`` is empty.
    """
    if not paths:
        raise ValueError("At least one Parquet path is required")

    column_list = ", ".join(columns)
    union_flag = "true" if union_by_name else "false"
    query = (
        f"SELECT {column_list} FROM read_parquet(?, hive_partitioning = true, "
        f"union_by_name = {union_flag})"
    )
    if where_sql:
        query += f" WHERE {where_sql}"

    bind_params: list[object] = [[str(path) for path in paths]]
    if params:
        bind_params.extend(params)

    result = connection.execute(query, bind_params)
    rows = result.fetchall()
    result_columns = [description[0] for description in result.description]
    # infer_schema_length=None: scan every row, not just a sample, before
    # inferring a column's type -- see duckdb_io._collect for the sparse-
    # leading-rows bug this guards against, which applies here too.
    return pl.DataFrame(
        rows, schema=result_columns, orient="row", infer_schema_length=None
    )


_PLAYER_WEEK_COLUMNS = ("season", "week", "player_id", "player_name", "team")


def load_player_week(
    connection: duckdb.DuckDBPyConnection,
    paths: Sequence[Path],
    *,
    season: int,
    through_week: int,
) -> pl.DataFrame:
    """Load ``player_week`` rows for one season, through a given week.

    Guide §13's own worked example, reproduced exactly (including its
    ``season = ? AND week <= ?`` filter shape) over
    :data:`nuclearff.data.datasets.PLAYER_WEEK_SCHEMA`'s columns.

    Args:
        connection: An open DuckDB connection.
        paths: Local ``player_week`` Parquet paths (typically
            ``dataset_paths(manifest, materialized, "player_week")``).
        season: Season to filter to.
        through_week: Maximum week to include (inclusive).

    Returns:
        Matching rows as a Polars DataFrame.
    """
    return read_parquet_paths(
        connection,
        paths,
        columns=_PLAYER_WEEK_COLUMNS,
        where_sql="season = ? AND week <= ?",
        params=[season, through_week],
    )
