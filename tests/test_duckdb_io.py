"""Unit tests for the shared DuckDB read/write helpers."""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nuclearff.duckdb_io import (
    merge_table,
    read_table,
    read_table_for_league,
    replace_table,
)
from nuclearff.exceptions import StorageError

_CREATE_SQL = "CREATE TABLE {table} (id VARCHAR PRIMARY KEY, name VARCHAR)"
_MERGE_CREATE_SQL = (
    "CREATE TABLE IF NOT EXISTS {table} "
    "(league_id VARCHAR, week INTEGER, points DOUBLE)"
)


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


def test_merge_table_leaves_a_different_keys_rows_untouched(tmp_path):
    """The exact bug class this exists to fix: writing league B's rows must
    not erase league A's rows already in the shared table."""
    db_path = tmp_path / "test.duckdb"
    columns = ("league_id", "week", "points")

    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        columns,
        [["A", 1, 10.0]],
        key_column="league_id",
        key_values=["A"],
    )
    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        columns,
        [["B", 1, 20.0]],
        key_column="league_id",
        key_values=["B"],
    )

    frame = read_table(db_path, "matchups").sort("league_id")
    assert frame.rows() == [("A", 1, 10.0), ("B", 1, 20.0)]


def test_merge_table_replaces_only_the_keys_it_owns(tmp_path):
    """A refetch of one key's rows must still fully replace that key's own
    prior rows (not just append), the same "replace, don't accumulate
    duplicates" contract `replace_table` has for a single key."""
    db_path = tmp_path / "test.duckdb"
    columns = ("league_id", "week", "points")

    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        columns,
        [["A", 1, 10.0], ["A", 2, 12.0]],
        key_column="league_id",
        key_values=["A"],
    )
    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        columns,
        [["A", 1, 99.0]],
        key_column="league_id",
        key_values=["A"],
    )

    frame = read_table(db_path, "matchups")
    assert frame.rows() == [("A", 1, 99.0)]


def test_merge_table_can_clear_a_keys_rows_to_empty(tmp_path):
    """A key that now legitimately has zero rows (e.g. a season with no
    matchups yet) must still have its stale rows cleared, not left stale,
    when that key is explicitly named in key_values."""
    db_path = tmp_path / "test.duckdb"
    columns = ("league_id", "week", "points")

    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        columns,
        [["A", 1, 10.0]],
        key_column="league_id",
        key_values=["A"],
    )
    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        columns,
        [],
        key_column="league_id",
        key_values=["A"],
    )

    assert read_table(db_path, "matchups").is_empty()


def test_merge_table_creates_the_table_on_first_call(tmp_path):
    db_path = tmp_path / "test.duckdb"

    count = merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        ("league_id", "week", "points"),
        [["A", 1, 10.0]],
        key_column="league_id",
        key_values=["A"],
    )

    assert count == 1
    assert read_table(db_path, "matchups").rows() == [("A", 1, 10.0)]


def test_merge_table_rejects_a_bad_key_column(tmp_path):
    with pytest.raises(ValueError, match="plain identifier"):
        merge_table(
            tmp_path / "test.duckdb",
            "matchups",
            _MERGE_CREATE_SQL,
            ("league_id", "week", "points"),
            [],
            key_column="league_id; DROP TABLE matchups;",
            key_values=["A"],
        )


# --- read_table_for_league / connection reuse (issue #149) ------------------


def test_read_table_for_league_filters_in_sql_not_after(tmp_path):
    db_path = tmp_path / "test.duckdb"
    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        ("league_id", "week", "points"),
        [["A", 1, 10.0], ["B", 1, 20.0], ["C", 1, 30.0]],
        key_column="league_id",
        key_values=["A", "B", "C"],
    )

    frame = read_table_for_league(db_path, "matchups", ["A", "C"]).sort("league_id")

    assert frame["league_id"].to_list() == ["A", "C"]


def test_read_table_for_league_empty_ids_returns_no_rows_with_the_real_schema(
    tmp_path,
):
    """An explicitly empty selection is zero rows, not 'everything' (the
    behavior omitting a filter entirely would have)."""
    db_path = tmp_path / "test.duckdb"
    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        ("league_id", "week", "points"),
        [["A", 1, 10.0]],
        key_column="league_id",
        key_values=["A"],
    )

    frame = read_table_for_league(db_path, "matchups", [])

    assert frame.is_empty()
    assert frame.columns == ["league_id", "week", "points"]


def test_read_table_for_league_missing_database_raises_storage_error(tmp_path):
    with pytest.raises(StorageError, match="does not exist"):
        read_table_for_league(tmp_path / "never-created.duckdb", "widgets", ["A"])


def test_read_table_for_league_rejects_a_bad_league_column(tmp_path):
    with pytest.raises(ValueError, match="plain identifier"):
        read_table_for_league(
            tmp_path / "test.duckdb",
            "matchups",
            ["A"],
            league_column="league_id; DROP TABLE matchups;",
        )


def test_read_table_and_read_table_for_league_accept_a_shared_connection(tmp_path):
    """A caller reading several tables for one command can pass one already-open
    connection instead of each read opening (and closing) its own."""
    db_path = tmp_path / "test.duckdb"
    merge_table(
        db_path,
        "matchups",
        _MERGE_CREATE_SQL,
        ("league_id", "week", "points"),
        [["A", 1, 10.0]],
        key_column="league_id",
        key_values=["A"],
    )
    replace_table(db_path, "widgets", _CREATE_SQL, ("id", "name"), [["1", "a"]])

    with duckdb.connect(str(db_path), read_only=True) as conn:
        matchups = read_table_for_league(db_path, "matchups", ["A"], connection=conn)
        widgets = read_table(db_path, "widgets", connection=conn)

    assert matchups.rows() == [("A", 1, 10.0)]
    assert widgets.rows() == [("1", "a")]


def test_merge_table_executes_multiple_semicolon_separated_statements(tmp_path):
    """create_table_sql may append a trailing CREATE INDEX -- both statements
    must run, not just the first (issue #149's secondary-index fix relies on
    this)."""
    db_path = tmp_path / "test.duckdb"
    create_sql = (
        "CREATE TABLE IF NOT EXISTS {table} (league_id VARCHAR, week INTEGER);"
        "CREATE INDEX IF NOT EXISTS idx_{table}_league_id ON {table} (league_id)"
    )

    merge_table(
        db_path,
        "matchups",
        create_sql,
        ("league_id", "week"),
        [["A", 1]],
        key_column="league_id",
        key_values=["A"],
    )

    with duckdb.connect(str(db_path)) as conn:
        indexes = conn.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'matchups'"
        ).fetchall()
    assert ("idx_matchups_league_id",) in indexes


def test_read_table_infers_a_nullable_float_column_past_the_first_100_rows(tmp_path):
    """GitHub Issue 79 regression: a real `sleeper_matchups.custom_points`-shaped
    column (`None` for every one of the first 100+ rows, a real float only much
    later) must not make Polars' sampled schema inference guess `Null` and then
    raise `ComputeError` on the first real value."""
    db_path = tmp_path / "test.duckdb"
    create_sql = "CREATE TABLE {table} (id INTEGER, points DOUBLE)"
    rows = [[i, None] for i in range(150)] + [[150, 146.539993]]

    replace_table(db_path, "matchups", create_sql, ("id", "points"), rows)
    frame = read_table(db_path, "matchups")

    assert frame.schema["points"] == pl.Float64
    assert frame.filter(pl.col("id") == 150)["points"].item() == 146.539993
