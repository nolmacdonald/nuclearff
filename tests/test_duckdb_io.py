"""Unit tests for the shared DuckDB read/write helpers."""

from __future__ import annotations

import duckdb
import pytest

from nuclearff.duckdb_io import read_table, replace_table
from nuclearff.exceptions import StorageError

_CREATE_SQL = "CREATE TABLE {table} (id VARCHAR PRIMARY KEY, name VARCHAR)"


def test_replace_table_then_read_table_round_trips(tmp_path):
    """Rows written by replace_table come back unchanged from read_table."""
    db_path = tmp_path / "test.duckdb"

    count = replace_table(
        db_path, "widgets", _CREATE_SQL, ("id", "name"), [["1", "a"], ["2", "b"]]
    )

    assert count == 2
    frame = read_table(db_path, "widgets")
    assert frame.sort("id").rows() == [("1", "a"), ("2", "b")]


def test_replace_table_replaces_rather_than_appends(tmp_path):
    """A second write reflects only the latest rows."""
    db_path = tmp_path / "test.duckdb"

    replace_table(db_path, "widgets", _CREATE_SQL, ("id", "name"), [["1", "a"]])
    replace_table(db_path, "widgets", _CREATE_SQL, ("id", "name"), [["2", "b"]])

    frame = read_table(db_path, "widgets")
    assert frame.rows() == [("2", "b")]


def test_replace_table_handles_empty_rows(tmp_path):
    """Writing zero rows still creates the (empty) table."""
    db_path = tmp_path / "test.duckdb"

    count = replace_table(db_path, "widgets", _CREATE_SQL, ("id", "name"), [])

    assert count == 0
    assert read_table(db_path, "widgets").is_empty()


def test_replace_table_rejects_a_bad_table_name(tmp_path):
    """A table name that is not a plain identifier is refused, not interpolated."""
    with pytest.raises(ValueError, match="plain identifier"):
        replace_table(
            tmp_path / "test.duckdb",
            "widgets; DROP TABLE widgets;",
            _CREATE_SQL,
            ("id", "name"),
            [],
        )


def test_read_table_missing_database_raises_storage_error(tmp_path):
    """A database file that was never created is a StorageError, not a crash."""
    with pytest.raises(StorageError, match="does not exist"):
        read_table(tmp_path / "never-created.duckdb", "widgets")


def test_read_table_missing_table_raises_storage_error(tmp_path):
    """An existing database without the requested table is a StorageError."""
    db_path = tmp_path / "test.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE other (id VARCHAR)")

    with pytest.raises(StorageError):
        read_table(db_path, "widgets")


def test_read_table_rejects_a_bad_table_name(tmp_path):
    """Reading also validates the table name before it reaches SQL."""
    with pytest.raises(ValueError, match="plain identifier"):
        read_table(tmp_path / "test.duckdb", "widgets; DROP TABLE widgets;")
