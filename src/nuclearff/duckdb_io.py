"""Shared helpers for reading and writing DuckDB tables via plain SQL.

:func:`replace_table` replaces its table wholesale: correct for a genuine
full snapshot (the Sleeper player map, a resolved ID crosswalk) that has
exactly one real-world source, but a real, previously-undetected bug when
used for a table shared across many *independent* sources -- e.g. one row
set per Sleeper league. Every ``fetch_and_write_*`` function that took a
single league's multi-*season* chain and called :func:`replace_table` with
just that chain's rows was silently erasing every *other league's* rows
already in the table, since ``replace_table`` always ``DROP``s the whole
table first. Confirmed live (2026-09-10): fetching three real, unrelated
leagues on the same account in sequence left only the last-fetched
league's matchups/standings rows surviving in ``sleeper_matchups``/
``sleeper_standings`` -- the first two leagues' full multi-season history,
fetched and verified working earlier the same session, was gone with no
error or warning at any point. :func:`merge_table` exists to fix this: it
only replaces the rows belonging to the keys this call actually owns,
leaving every other key's rows untouched.

Rows move through parameterized SQL rather than a Polars/pandas DataFrame
handoff. DuckDB's DataFrame integrations (``.pl()``, ``CREATE TABLE ... AS
SELECT * FROM df``) convert through Arrow, which requires ``pyarrow`` even
though neither ``duckdb`` nor ``polars`` declares it as a dependency. Plain SQL
avoids that dependency for what are, so far, a handful of typed columns.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nuclearff.exceptions import StorageError

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_identifier(name: str) -> None:
    """Validate that ``name`` is safe to interpolate as a SQL identifier.

    DuckDB has no parameterized way to bind an identifier, so every table name
    that reaches an f-string in this module passes through here first.

    Args:
        name: Candidate table name.

    Raises:
        ValueError: If ``name`` is not a plain identifier.
    """
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(f"table_name must be a plain identifier, got {name!r}")


def replace_table(
    db_path: str | Path,
    table_name: str,
    create_table_sql: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> int:
    """Replace a DuckDB table wholesale with the given rows.

    Args:
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table name.
        create_table_sql: A ``CREATE TABLE {table} (...)`` statement with a
            single ``{table}`` placeholder, in the same column order as
            ``columns``.
        columns: Column names, in insertion order.
        rows: One sequence of values per row, in column order.

    Returns:
        The number of rows written.

    Raises:
        ValueError: If ``table_name`` is not a plain identifier.
    """
    _check_identifier(table_name)

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    placeholders = ", ".join(["?"] * len(columns))
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.execute(create_table_sql.format(table=table_name))
        if rows:
            conn.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)

    return len(rows)


def merge_table(
    db_path: str | Path,
    table_name: str,
    create_table_sql: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    key_column: str,
    key_values: Sequence[Any],
) -> int:
    """Replace only the rows this call owns, leaving every other row untouched.

    Unlike :func:`replace_table`, this never drops the table: it deletes
    existing rows whose ``key_column`` is in ``key_values`` (typically
    ``league_id``, and typically every key this call actually attempted to
    fetch, not just the ones that happened to yield rows -- see the module
    docstring), then inserts ``rows``. A key never passed in ``key_values``
    is never touched, however the table changes otherwise. Safe to call
    repeatedly for different, independent keys sharing one table (e.g. once
    per league) without erasing a different key's previously-written rows.

    Args:
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table name.
        create_table_sql: A ``CREATE TABLE IF NOT EXISTS {table} (...)``
            statement with a single ``{table}`` placeholder, in the same
            column order as ``columns``. Must be ``IF NOT EXISTS``, not a
            plain ``CREATE TABLE`` -- the table is expected to already
            exist on every call after the first.
        columns: Column names, in insertion order.
        rows: One sequence of values per row, in column order.
        key_column: The column identifying which existing rows this call
            owns and may replace.
        key_values: The distinct ``key_column`` values this call owns.
            Every existing row whose ``key_column`` matches one of these is
            deleted before ``rows`` is inserted; a key absent from
            ``key_values`` is left exactly as it was, even if ``rows``
            happens to contain none of its rows.

    Returns:
        The number of rows inserted by this call.

    Raises:
        ValueError: If ``table_name`` or ``key_column`` is not a plain
            identifier.
    """
    _check_identifier(table_name)
    _check_identifier(key_column)

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    placeholders = ", ".join(["?"] * len(columns))
    key_values = list(key_values)

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(create_table_sql.format(table=table_name))
        if key_values:
            delete_placeholders = ", ".join(["?"] * len(key_values))
            conn.execute(
                f"DELETE FROM {table_name} "
                f"WHERE {key_column} IN ({delete_placeholders})",
                key_values,
            )
        if rows:
            conn.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)

    return len(rows)


def read_table(db_path: str | Path, table_name: str) -> pl.DataFrame:
    """Read a DuckDB table into a Polars DataFrame.

    Args:
        db_path: Path to the DuckDB database file.
        table_name: Table to read.

    Returns:
        The table's rows as a DataFrame, column order preserved.

    Raises:
        ValueError: If ``table_name`` is not a plain identifier.
        StorageError: If the database file or the table does not exist.
    """
    _check_identifier(table_name)

    db_path = Path(db_path)
    if not db_path.is_file():
        raise StorageError(table_name, str(db_path), "database file does not exist")

    try:
        with duckdb.connect(str(db_path), read_only=True) as conn:
            result = conn.execute(f"SELECT * FROM {table_name}")
            rows = result.fetchall()
            columns = [d[0] for d in result.description]
    except duckdb.Error as exc:
        raise StorageError(table_name, str(db_path), str(exc)) from exc

    # `infer_schema_length=None` (scan every row, not just the default
    # first 100): `sleeper_matchups.custom_points` is a real column where
    # every row is `None` for far more than 100 rows before the first real
    # float ever appears -- Polars' default sampled inference decided the
    # column was `Null`-typed from that leading run, then raised
    # `ComputeError: could not append value ... of type: f64` the moment a
    # real value showed up later. `sleeper_matchups` was never read back
    # through this function before GitHub Issue 79 wired up the first
    # reader of it, so this was a real, latent bug in every table read
    # here, not something new to matchups specifically.
    return pl.DataFrame(rows, schema=columns, orient="row", infer_schema_length=None)
