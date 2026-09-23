"""Unit tests for validated Parquet artifact writing.

Issue 185's "Done when": DuckDB reads every fixture, and a duplicate or
schema-invalid fixture fails loudly -- both exercised directly below against
:data:`nuclearff.data.datasets.PLAYER_WEEK_SCHEMA`, guide §10's own worked
example.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest

from nuclearff.data.datasets import PLAYER_WEEK_SCHEMA
from nuclearff.data.parquet import write_parquet_artifact
from nuclearff.exceptions import DataQualityError
from nuclearff.provenance import sha256_file


def _player_week_fixture() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2026, 2026, 2026],
            "week": [1, 1, 1],
            "player_id": ["1001", "1002", "1003"],
            "player_name": ["Player One", "Player Two", "Player Three"],
            "team": ["SEA", "SF", "SEA"],
        }
    )


def test_write_parquet_artifact_writes_a_valid_fixture(tmp_path: Path):
    destination = tmp_path / "player_week" / "part-000.parquet"

    artifact = write_parquet_artifact(
        _player_week_fixture(), destination, schema=PLAYER_WEEK_SCHEMA
    )

    assert artifact.path == destination
    assert destination.is_file()
    assert artifact.row_count == 3
    assert artifact.size_bytes == destination.stat().st_size
    assert artifact.sha256 == sha256_file(destination)


def test_write_parquet_artifact_leaves_no_temp_file_behind(tmp_path: Path):
    destination = tmp_path / "part-000.parquet"

    write_parquet_artifact(
        _player_week_fixture(), destination, schema=PLAYER_WEEK_SCHEMA
    )

    assert not destination.with_suffix(".parquet.part").exists()


def test_write_parquet_artifact_is_readable_by_duckdb(tmp_path: Path):
    """Issue 185's "Done when": DuckDB reads every fixture."""
    destination = tmp_path / "part-000.parquet"
    write_parquet_artifact(
        _player_week_fixture(), destination, schema=PLAYER_WEEK_SCHEMA
    )

    with duckdb.connect() as conn:
        rows = conn.execute(
            "SELECT season, week, player_id, player_name, team "
            "FROM read_parquet(?) ORDER BY player_id",
            [str(destination)],
        ).fetchall()

    assert rows == [
        (2026, 1, "1001", "Player One", "SEA"),
        (2026, 1, "1002", "Player Two", "SF"),
        (2026, 1, "1003", "Player Three", "SEA"),
    ]


def test_write_parquet_artifact_rejects_a_duplicate_natural_key_fixture(tmp_path: Path):
    """Issue 185's "Done when": a duplicate fixture fails loudly."""
    destination = tmp_path / "part-000.parquet"
    duplicated = pl.concat([_player_week_fixture(), _player_week_fixture().head(1)])

    with pytest.raises(DataQualityError, match="duplicate natural key"):
        write_parquet_artifact(duplicated, destination, schema=PLAYER_WEEK_SCHEMA)

    assert not destination.exists()


def test_write_parquet_artifact_rejects_a_schema_invalid_fixture(tmp_path: Path):
    """Issue 185's "Done when": a schema-invalid fixture fails loudly."""
    destination = tmp_path / "part-000.parquet"
    missing_team = _player_week_fixture().drop("team")

    with pytest.raises(DataQualityError, match="missing required column"):
        write_parquet_artifact(missing_team, destination, schema=PLAYER_WEEK_SCHEMA)

    assert not destination.exists()


def test_write_parquet_artifact_creates_parent_directories(tmp_path: Path):
    destination = tmp_path / "nested" / "deeper" / "part-000.parquet"

    write_parquet_artifact(
        _player_week_fixture(), destination, schema=PLAYER_WEEK_SCHEMA
    )

    assert destination.is_file()
