"""Unit tests for the DuckDB query layer over cached Parquet.

Issue 188's own "Done when": query tests cover season/week filters and
schema transitions, and a dashboard-shaped query uses only manifest-selected
objects -- all exercised directly below.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest

from nuclearff.data.datasets import PLAYER_WEEK_SCHEMA
from nuclearff.data.manifest import DataManifest, ManifestObject
from nuclearff.data.parquet import write_parquet_artifact
from nuclearff.data.repository import (
    connect_analytics,
    dataset_paths,
    load_player_week,
    read_parquet_paths,
)


@pytest.fixture
def connection():
    with connect_analytics() as conn:
        yield conn


def _player_week_frame(season: int, week: int, player_id: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [season],
            "week": [week],
            "player_id": [player_id],
            "player_name": [f"Player {player_id}"],
            "team": ["SEA"],
        }
    )


# --- connect_analytics ---------------------------------------------------


def test_connect_analytics_sets_the_configured_thread_count():
    with connect_analytics(threads=4) as conn:
        (value,) = conn.execute("SELECT current_setting('threads')").fetchone()

    assert int(value) == 4


def test_connect_analytics_defaults_to_two_threads():
    with connect_analytics() as conn:
        (value,) = conn.execute("SELECT current_setting('threads')").fetchone()

    assert int(value) == 2


# --- dataset_paths -----------------------------------------------------


def test_dataset_paths_selects_only_the_requested_dataset(tmp_path: Path):
    player_week_obj = ManifestObject(
        dataset="player_week",
        key="prod/releases/RUN/curated/player_week/part-000.parquet",
        content_type="application/vnd.apache.parquet",
        size_bytes=1,
        sha256="a" * 64,
        row_count=1,
        schema_version=1,
    )
    matchups_obj = ManifestObject(
        dataset="matchups",
        key="prod/releases/RUN/curated/matchups/part-000.parquet",
        content_type="application/vnd.apache.parquet",
        size_bytes=1,
        sha256="b" * 64,
        row_count=1,
        schema_version=1,
    )
    manifest = DataManifest(
        schema_version=1,
        run_id="RUN",
        created_at_utc="2026-09-22T00:00:00Z",  # type: ignore[arg-type]
        objects=[player_week_obj, matchups_obj],
    )
    materialized = {
        player_week_obj.key: tmp_path / "player_week.parquet",
        matchups_obj.key: tmp_path / "matchups.parquet",
    }

    selected = dataset_paths(manifest, materialized, "player_week")

    assert selected == [tmp_path / "player_week.parquet"]


# --- read_parquet_paths -------------------------------------------------


def test_read_parquet_paths_requires_at_least_one_path(connection):
    with pytest.raises(ValueError, match="At least one Parquet path"):
        read_parquet_paths(connection, [], columns=["a"])


def test_read_parquet_paths_selects_only_the_requested_columns(
    tmp_path: Path, connection
):
    destination = tmp_path / "part-000.parquet"
    write_parquet_artifact(
        _player_week_frame(2026, 1, "1001"), destination, schema=PLAYER_WEEK_SCHEMA
    )

    frame = read_parquet_paths(connection, [destination], columns=["player_id", "team"])

    assert frame.columns == ["player_id", "team"]
    assert frame["player_id"].to_list() == ["1001"]


def test_read_parquet_paths_pushes_a_where_filter_into_sql(tmp_path: Path, connection):
    destination = tmp_path / "part-000.parquet"
    frame = pl.concat(
        [_player_week_frame(2026, 1, "1001"), _player_week_frame(2026, 2, "1002")]
    )
    write_parquet_artifact(frame, destination, schema=PLAYER_WEEK_SCHEMA)

    result = read_parquet_paths(
        connection,
        [destination],
        columns=["player_id", "week"],
        where_sql="week = ?",
        params=[2],
    )

    assert result["player_id"].to_list() == ["1002"]


def test_read_parquet_paths_without_union_by_name_rejects_a_schema_mismatch(
    tmp_path: Path, connection
):
    """Schema transition, part 1: without union_by_name, a column absent
    from one file is a hard error, not silently dropped."""
    old_path = tmp_path / "old.parquet"
    new_path = tmp_path / "new.parquet"
    _player_week_frame(2025, 1, "1001").write_parquet(old_path)
    _player_week_frame(2026, 1, "1002").with_columns(
        pl.Series("target_share", [0.2])
    ).write_parquet(new_path)

    with pytest.raises(duckdb.Error):
        read_parquet_paths(
            connection, [old_path, new_path], columns=["player_id", "target_share"]
        )


def test_read_parquet_paths_with_union_by_name_fills_a_missing_column_with_null(
    tmp_path: Path, connection
):
    """Schema transition, part 2: with union_by_name, the older file's
    missing column reads as null rather than erroring."""
    old_path = tmp_path / "old.parquet"
    new_path = tmp_path / "new.parquet"
    _player_week_frame(2025, 1, "1001").write_parquet(old_path)
    _player_week_frame(2026, 1, "1002").with_columns(
        pl.Series("target_share", [0.2])
    ).write_parquet(new_path)

    result = read_parquet_paths(
        connection,
        [old_path, new_path],
        columns=["player_id", "target_share"],
        union_by_name=True,
    ).sort("player_id")

    assert result["player_id"].to_list() == ["1001", "1002"]
    assert result["target_share"].to_list() == [None, 0.2]


# --- load_player_week ---------------------------------------------------


def test_load_player_week_filters_by_season_and_through_week(
    tmp_path: Path, connection
):
    """Issue 188's "Done when": query tests cover season/week filters."""
    destination = tmp_path / "part-000.parquet"
    frame = pl.concat(
        [
            _player_week_frame(2025, 1, "old-season"),
            _player_week_frame(2026, 1, "week-1"),
            _player_week_frame(2026, 2, "week-2"),
            _player_week_frame(2026, 3, "week-3-excluded"),
        ]
    )
    write_parquet_artifact(frame, destination, schema=PLAYER_WEEK_SCHEMA)

    result = load_player_week(connection, [destination], season=2026, through_week=2)

    assert sorted(result["player_id"].to_list()) == ["week-1", "week-2"]
    assert result.columns == ["season", "week", "player_id", "player_name", "team"]


def test_dashboard_query_uses_only_manifest_selected_objects(
    tmp_path: Path, connection
):
    """Issue 188's "Done when": dashboard-shaped queries use only
    manifest-selected objects -- ties dataset_paths (the manifest layer)
    directly to load_player_week (the query layer)."""
    player_week_path = tmp_path / "player_week.parquet"
    matchups_path = tmp_path / "matchups.parquet"
    write_parquet_artifact(
        _player_week_frame(2026, 1, "1001"), player_week_path, schema=PLAYER_WEEK_SCHEMA
    )
    # A same-shaped file under a different dataset name -- if dataset_paths
    # leaked it through, load_player_week would happily read it too.
    write_parquet_artifact(
        _player_week_frame(2026, 1, "should-not-appear"),
        matchups_path,
        schema=PLAYER_WEEK_SCHEMA,
    )

    player_week_obj = ManifestObject(
        dataset="player_week",
        key="prod/releases/RUN/curated/player_week/part-000.parquet",
        content_type="application/vnd.apache.parquet",
        size_bytes=1,
        sha256="a" * 64,
        row_count=1,
        schema_version=1,
    )
    matchups_obj = ManifestObject(
        dataset="matchups",
        key="prod/releases/RUN/curated/matchups/part-000.parquet",
        content_type="application/vnd.apache.parquet",
        size_bytes=1,
        sha256="b" * 64,
        row_count=1,
        schema_version=1,
    )
    manifest = DataManifest(
        schema_version=1,
        run_id="RUN",
        created_at_utc="2026-09-22T00:00:00Z",  # type: ignore[arg-type]
        objects=[player_week_obj, matchups_obj],
    )
    materialized = {
        player_week_obj.key: player_week_path,
        matchups_obj.key: matchups_path,
    }

    paths = dataset_paths(manifest, materialized, "player_week")
    result = load_player_week(connection, paths, season=2026, through_week=1)

    assert result["player_id"].to_list() == ["1001"]
