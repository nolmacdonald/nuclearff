"""Normalize and store the Sleeper NFL player map for identity mapping.

The full player map (:meth:`SleeperClient.get_players`) is the input to mapping
Sleeper roster and draft data onto external NFL data sources such as
``nflreadpy``. Sleeper's player objects already carry several cross-platform
identifiers, notably ``gsis_id`` (the nflverse primary key), so a normalized
subset of columns stored in DuckDB is enough to start joining against nflverse
without waiting on a separate ID crosswalk.

Rows are written through :mod:`nuclearff.duckdb_io`, which uses plain
parameterized SQL rather than a Polars/pandas DataFrame handoff — see that
module for why.

**Unchanged-fetch short-circuit (issue #149).** ``replace_table`` always
``DROP``s and rebuilds the whole table (~11k rows), even when Sleeper's
player map hasn't actually changed since the last fetch — a real, wasted
cost once this runs on a schedule rather than by hand. :func:`write_players_table`
compares a content hash of the incoming rows against the hash recorded for
the previous write (in a tiny sibling metadata table) and skips the
rewrite entirely when they match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import duckdb

from nuclearff.duckdb_io import replace_table

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_identifier(name: str) -> None:
    """Validate that ``name`` is safe to interpolate as a SQL identifier.

    Duplicated from :mod:`nuclearff.duckdb_io` (same regex, same reasoning)
    rather than importing a private helper across modules: unlike every
    other write in this package, the meta-table lookup below runs its own
    raw SQL ahead of :func:`nuclearff.duckdb_io.replace_table`, which
    normally provides this guard.

    Args:
        name: Candidate table name.

    Raises:
        ValueError: If ``name`` is not a plain identifier.
    """
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(f"table_name must be a plain identifier, got {name!r}")


logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_players"
"""Default table name used by :func:`write_players_table`."""

_META_TABLE_NAME = "sleeper_players_meta"
"""Tiny sibling table recording the last-written content hash per table name,
used by :func:`write_players_table` to skip a no-op rewrite."""

_META_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    table_name VARCHAR PRIMARY KEY,
    content_hash VARCHAR
)
"""

_COLUMNS = (
    "player_id",
    "sport",
    "first_name",
    "last_name",
    "full_name",
    "search_full_name",
    "position",
    "fantasy_positions",
    "team",
    "status",
    "active",
    "injury_status",
    "age",
    "years_exp",
    "college",
    "gsis_id",
    "espn_id",
    "yahoo_id",
    "sportradar_id",
    "rotowire_id",
    "fantasy_data_id",
    "stats_id",
    "swish_id",
    "pandascore_id",
)
"""Columns kept from each Sleeper player object.

A deliberate subset of the several dozen fields Sleeper returns: enough to
identify a player, resolve their fantasy-relevant role, and join to nflverse
(``gsis_id``) or other platforms, without carrying broadcast/scouting metadata
(birth city, high school, hashtag, etc.) this project has no use for.
"""

_ID_COLUMNS = frozenset(
    {
        "gsis_id",
        "espn_id",
        "yahoo_id",
        "sportradar_id",
        "rotowire_id",
        "fantasy_data_id",
        "stats_id",
        "swish_id",
        "pandascore_id",
    }
)
"""Columns stored as VARCHAR even though Sleeper sometimes types them as ints.

Cross-platform IDs are identifiers, not quantities, and Sleeper is not
consistent about whether it types them as strings or numbers. Storing them as
text avoids a type-mismatch error on whichever player breaks that assumption
first.
"""

_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    player_id VARCHAR PRIMARY KEY,
    sport VARCHAR,
    first_name VARCHAR,
    last_name VARCHAR,
    full_name VARCHAR,
    search_full_name VARCHAR,
    position VARCHAR,
    fantasy_positions VARCHAR[],
    team VARCHAR,
    status VARCHAR,
    active BOOLEAN,
    injury_status VARCHAR,
    age INTEGER,
    years_exp INTEGER,
    college VARCHAR,
    gsis_id VARCHAR,
    espn_id VARCHAR,
    yahoo_id VARCHAR,
    sportradar_id VARCHAR,
    rotowire_id VARCHAR,
    fantasy_data_id VARCHAR,
    stats_id VARCHAR,
    swish_id VARCHAR,
    pandascore_id VARCHAR
)
"""
"""Schema for :data:`TABLE_NAME`, in the same order as :data:`_COLUMNS`."""


def player_rows(players: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the Sleeper player map into rows of the kept columns.

    Args:
        players: Mapping of Sleeper player ID to player object, as returned by
            :meth:`nuclearff.sleeper.client.SleeperClient.get_players`.

    Returns:
        One row per player, keyed by :data:`_COLUMNS`. The dict key is
        authoritative for ``player_id``: a handful of legacy/team-defense
        entries carry a ``null`` or mismatched ``player_id`` field in the
        payload itself. ID columns are stringified so callers see the same
        types that end up in DuckDB.
    """
    rows: list[dict[str, Any]] = []
    for player_id, player in players.items():
        if not isinstance(player, dict):
            logger.warning("Skipping non-object player entry for id %s", player_id)
            continue
        row: dict[str, Any] = {}
        for column in _COLUMNS:
            value = player.get(column)
            if column in _ID_COLUMNS and value is not None:
                value = str(value)
            row[column] = value
        row["player_id"] = player_id
        rows.append(row)
    return rows


def _content_hash(row_values: list[list[Any]]) -> str:
    """A stable hash of the player rows, independent of dict iteration order.

    Args:
        row_values: Row values in :data:`_COLUMNS` order, as built by
            :func:`write_players_table`.

    Returns:
        A hex digest identifying this exact player map — an unchanged
        fetch (even one Sleeper returns in a different dict order) hashes
        identically, so it doesn't trigger a rewrite.
    """
    canonical = sorted(json.dumps(row, default=str) for row in row_values)
    return hashlib.sha256("\n".join(canonical).encode()).hexdigest()


def write_players_table(
    players: dict[str, Any],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
    meta_table_name: str = _META_TABLE_NAME,
) -> int:
    """Write the Sleeper player map to a DuckDB table, replacing it wholesale.

    The player map is a full snapshot rather than an incremental feed, so each
    call replaces the table instead of upserting into it. Sleeper asks callers
    to fetch the underlying payload at most once a day; this function only
    controls how it is stored, not how often it is fetched (see
    :meth:`SleeperClient.get_players`).

    **Skips the rewrite entirely (issue #149)** when the incoming player map
    hashes identically to the last write recorded in ``meta_table_name`` —
    Sleeper's player map doesn't change every day even though it's safe to
    poll that often, and a same-data rewrite still pays the full ``DROP`` +
    ~11k-row reinsert cost for nothing.

    Args:
        players: Mapping of Sleeper player ID to player object.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table name.
        meta_table_name: Table recording the last-written content hash.

    Returns:
        The number of rows the table now holds — either newly written, or
        (when unchanged) the row count already there.

    Raises:
        ValueError: If ``table_name`` or ``meta_table_name`` is not a plain
            identifier.
    """
    _check_identifier(table_name)
    _check_identifier(meta_table_name)

    rows = player_rows(players)
    row_values = [[row[column] for column in _COLUMNS] for row in rows]
    new_hash = _content_hash(row_values)

    db_path = Path(db_path)
    if db_path.is_file():
        with duckdb.connect(str(db_path)) as conn:
            conn.execute(_META_CREATE_TABLE_SQL.format(table=meta_table_name))
            previous = conn.execute(
                f"SELECT content_hash FROM {meta_table_name} WHERE table_name = ?",
                [table_name],
            ).fetchone()
            table_exists = (
                conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                    [table_name],
                ).fetchone()
                is not None
            )
            if table_exists and previous is not None and previous[0] == new_hash:
                count_row = conn.execute(
                    f"SELECT COUNT(*) FROM {table_name}"
                ).fetchone()
                count = count_row[0] if count_row is not None else 0
                logger.info(
                    "Sleeper player map unchanged (%d rows); skipped rewriting %s",
                    count,
                    table_name,
                )
                return count

    count = replace_table(db_path, table_name, _CREATE_TABLE_SQL, _COLUMNS, row_values)

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(_META_CREATE_TABLE_SQL.format(table=meta_table_name))
        conn.execute(
            f"INSERT INTO {meta_table_name} (table_name, content_hash) "
            "VALUES (?, ?) ON CONFLICT (table_name) DO UPDATE "
            "SET content_hash = excluded.content_hash",
            [table_name, new_hash],
        )

    logger.info("Wrote %d Sleeper players to %s (table %s)", count, db_path, table_name)
    return count
