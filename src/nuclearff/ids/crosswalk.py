"""Resolve Sleeper player IDs to nflverse ``gsis_id`` via the ff_playerids crosswalk.

Sleeper's own player objects already carry ``gsis_id`` for most players (see
``brain/notes/sleeper-player-object.md`` in the project's brain); this fills
the gap for the rest using DynastyProcess's ``ff_playerids`` table
(:func:`nuclearff.nflverse.load_ff_playerids`), joined on ``sleeper_id``.

A crosswalk row is only trusted when its ``sleeper_id`` is unambiguous — see
:func:`ambiguous_sleeper_ids`. Nothing here performs name-based fuzzy
matching: a player who cannot be resolved through an exact ID match stays
unresolved and is reported, never silently guessed.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from nuclearff.duckdb_io import read_table, replace_table
from nuclearff.exceptions import StorageError

TABLE_NAME = "player_id_map"
"""Table written by :func:`write_player_id_map`."""

SLEEPER_PLAYERS_TABLE = "sleeper_players"
"""Source table written by :func:`nuclearff.sleeper.players.write_players_table`."""

_MAP_COLUMNS = (
    "player_id",
    "full_name",
    "position",
    "team",
    "gsis_id",
    "gsis_id_source",
)

_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    player_id VARCHAR PRIMARY KEY,
    full_name VARCHAR,
    position VARCHAR,
    team VARCHAR,
    gsis_id VARCHAR,
    gsis_id_source VARCHAR
)
"""
"""Schema for :data:`TABLE_NAME`, in the same order as :data:`_MAP_COLUMNS`."""


def ambiguous_sleeper_ids(ff_ids: pl.DataFrame) -> pl.DataFrame:
    """Return crosswalk rows whose ``sleeper_id`` maps to more than one player.

    These are never used to fill a ``gsis_id``: picking one of several
    candidates would be a fuzzy match wearing an exact-match costume.

    Args:
        ff_ids: The raw ff_playerids crosswalk
            (:func:`nuclearff.nflverse.load_ff_playerids`).

    Returns:
        Rows of ``ff_ids`` whose ``sleeper_id`` repeats, sorted by
        ``sleeper_id``. Empty when every ``sleeper_id`` is unique.
    """
    with_id = ff_ids.filter(pl.col("sleeper_id").is_not_null())
    dupes = (
        with_id.group_by("sleeper_id")
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select("sleeper_id")
    )
    return with_id.join(dupes, on="sleeper_id", how="inner").sort("sleeper_id")


def resolve_missing_gsis_ids(
    players: pl.DataFrame, ff_ids: pl.DataFrame
) -> pl.DataFrame:
    """Fill missing Sleeper ``gsis_id`` values from the ff_playerids crosswalk.

    Args:
        players: Sleeper player rows, as read by :func:`read_sleeper_players`,
            with at least ``player_id``, ``full_name``, ``position``, ``team``,
            and ``gsis_id``.
        ff_ids: The raw ff_playerids crosswalk.

    Returns:
        ``players`` with ``gsis_id`` filled in where possible and a new
        ``gsis_id_source`` column: ``"sleeper"`` when Sleeper already had it,
        ``"ff_playerids"`` when the crosswalk supplied it, or null when
        neither source resolves the player.
    """
    ambiguous = ambiguous_sleeper_ids(ff_ids).select("sleeper_id").unique()

    candidates = (
        ff_ids.filter(pl.col("sleeper_id").is_not_null())
        .join(ambiguous, on="sleeper_id", how="anti")
        .select(
            pl.col("sleeper_id").cast(pl.Utf8).alias("player_id"),
            pl.col("gsis_id").alias("_crosswalk_gsis_id"),
        )
        .filter(pl.col("_crosswalk_gsis_id").is_not_null())
    )

    resolved = players.join(candidates, on="player_id", how="left")
    return resolved.with_columns(
        pl.when(pl.col("gsis_id").is_not_null())
        .then(pl.lit("sleeper"))
        .when(pl.col("_crosswalk_gsis_id").is_not_null())
        .then(pl.lit("ff_playerids"))
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias("gsis_id_source"),
        pl.coalesce([pl.col("gsis_id"), pl.col("_crosswalk_gsis_id")]).alias("gsis_id"),
    ).drop("_crosswalk_gsis_id")


def read_sleeper_players(db_path: str | Path) -> pl.DataFrame:
    """Read the Sleeper player table written by ``nuclearff sleeper fetch-players``.

    Args:
        db_path: Path to the DuckDB database file.

    Returns:
        The :data:`SLEEPER_PLAYERS_TABLE` table as a DataFrame.

    Raises:
        StorageError: If the table has not been populated yet.
    """
    try:
        return read_table(db_path, SLEEPER_PLAYERS_TABLE)
    except StorageError as exc:
        raise StorageError(
            SLEEPER_PLAYERS_TABLE,
            str(db_path),
            "run `nuclearff sleeper fetch-players` first",
        ) from exc


def write_player_id_map(resolved: pl.DataFrame, db_path: str | Path) -> int:
    """Write resolved Sleeper-to-``gsis_id`` mappings to DuckDB.

    Replaces :data:`TABLE_NAME` wholesale, matching the write pattern used
    throughout :mod:`nuclearff.duckdb_io`: this is a full snapshot, not an
    incremental feed.

    Args:
        resolved: Output of :func:`resolve_missing_gsis_ids`.
        db_path: Path to the DuckDB database file.

    Returns:
        The number of rows written.
    """
    rows = resolved.select(list(_MAP_COLUMNS)).rows()
    return replace_table(db_path, TABLE_NAME, _CREATE_TABLE_SQL, _MAP_COLUMNS, rows)
