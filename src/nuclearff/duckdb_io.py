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

**Reads (issue #149).** :func:`read_table` always ran ``SELECT * FROM
{table}`` with every filtering done client-side in Polars after loading the
*entire* table -- for a shared cache holding several leagues' full
multi-season history, most callers only ever wanted one. :func:`read_table_for_league`
pushes a ``league_id IN (...)`` filter down to SQL instead. Both accept an
optional already-open ``connection`` so a caller reading several tables for
one command doesn't open a separate DuckDB connection per table.
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
            exist on every call after the first. May contain more than one
            ``;``-separated statement (e.g. a trailing ``CREATE INDEX``) --
            each is executed in order.
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
        for statement in create_table_sql.format(table=table_name).split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
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


def _collect(
    conn: duckdb.DuckDBPyConnection, query: str, params: Sequence[Any]
) -> pl.DataFrame:
    """Run ``query`` and collect the result into a Polars DataFrame.

    Args:
        conn: An open DuckDB connection.
        query: A complete, already-parameterized SQL query.
        params: Values to bind to ``query``'s ``?`` placeholders.

    Returns:
        The result rows as a DataFrame, column order preserved.
    """
    result = conn.execute(query, list(params))
    rows = result.fetchall()
    columns = [d[0] for d in result.description]

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


def _read(
    db_path: str | Path,
    table_name: str,
    query: str,
    params: Sequence[Any],
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Shared connection-handling for :func:`read_table`/:func:`read_table_for_league`.

    Args:
        db_path: Path to the DuckDB database file.
        table_name: Table being read (for the error message only).
        query: A complete, already-parameterized SQL query.
        params: Values to bind to ``query``'s ``?`` placeholders.
        connection: An already-open connection to reuse instead of opening
            (and closing) a new one -- see the module docstring.

    Returns:
        The result rows as a DataFrame.

    Raises:
        StorageError: If the database file or the table does not exist.
    """
    if connection is not None:
        try:
            return _collect(connection, query, params)
        except duckdb.Error as exc:
            raise StorageError(table_name, str(db_path), str(exc)) from exc

    db_path = Path(db_path)
    if not db_path.is_file():
        raise StorageError(table_name, str(db_path), "database file does not exist")
    try:
        with duckdb.connect(str(db_path), read_only=True) as conn:
            return _collect(conn, query, params)
    except duckdb.Error as exc:
        raise StorageError(table_name, str(db_path), str(exc)) from exc


def read_table(
    db_path: str | Path,
    table_name: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Read a DuckDB table into a Polars DataFrame.

    Args:
        db_path: Path to the DuckDB database file.
        table_name: Table to read.
        connection: An already-open connection to read through, instead of
            opening (and closing) a new one. Pass the same connection to
            every :func:`read_table`/:func:`read_table_for_league` call in
            one command/request that would otherwise each open their own --
            see the module docstring.

    Returns:
        The table's rows as a DataFrame, column order preserved.

    Raises:
        ValueError: If ``table_name`` is not a plain identifier.
        StorageError: If the database file or the table does not exist.
    """
    _check_identifier(table_name)
    return _read(
        db_path, table_name, f"SELECT * FROM {table_name}", [], connection=connection
    )


def read_table_for_league(
    db_path: str | Path,
    table_name: str,
    league_ids: Sequence[str],
    *,
    league_column: str = "league_id",
    connection: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Read only the rows for the given league(s), filtered in SQL rather than after.

    Every ``fetch_and_write_*`` module in this package writes one
    ``league_id`` (or draft chain of them) per season, and most readers only
    ever want one league's slice of a table that, in a real shared cache,
    holds several unrelated leagues' full multi-season history. Reading the
    whole table and filtering client-side in Polars (this project's
    original, and still most common, pattern) pulls every other league's
    rows into memory and off disk for nothing.

    Args:
        db_path: Path to the DuckDB database file.
        table_name: Table to read.
        league_ids: The ``league_column`` values to include. An empty
            sequence returns zero rows (with the table's real schema, not a
            guessed one) rather than every row -- unlike omitting a filter
            entirely, an explicitly empty selection is never "everything."
        league_column: The column ``league_ids`` filters on. Every table
            :mod:`nuclearff.sleeper` writes calls this ``league_id``; the
            parameter exists for the rare table that doesn't (none today).
        connection: An already-open connection to read through -- see
            :func:`read_table`.

    Returns:
        The matching rows as a DataFrame, column order preserved.

    Raises:
        ValueError: If ``table_name`` or ``league_column`` is not a plain
            identifier.
        StorageError: If the database file or the table does not exist.
    """
    _check_identifier(table_name)
    _check_identifier(league_column)

    league_ids = list(league_ids)
    if not league_ids:
        where = "FALSE"
    else:
        where = f"{league_column} IN ({', '.join(['?'] * len(league_ids))})"

    query = f"SELECT * FROM {table_name} WHERE {where}"
    return _read(db_path, table_name, query, league_ids, connection=connection)
