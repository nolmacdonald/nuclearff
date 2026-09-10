"""Shared helpers for reading and writing DuckDB tables via plain SQL.

Every write here replaces its table wholesale rather than upserting: the
tables this project stores in DuckDB are full snapshots (a Sleeper player map,
a resolved ID crosswalk), not incremental feeds.

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
