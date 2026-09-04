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
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import replace_table

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_players"
"""Default table name used by :func:`write_players_table`."""

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


def write_players_table(
    players: dict[str, Any],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
) -> int:
    """Write the Sleeper player map to a DuckDB table, replacing it wholesale.

    The player map is a full snapshot rather than an incremental feed, so each
    call replaces the table instead of upserting into it. Sleeper asks callers
    to fetch the underlying payload at most once a day; this function only
    controls how it is stored, not how often it is fetched (see
    :meth:`SleeperClient.get_players`).

    Args:
        players: Mapping of Sleeper player ID to player object.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table name.

    Returns:
        The number of rows written.

    Raises:
        ValueError: If ``table_name`` is not a plain identifier.
    """
    rows = player_rows(players)
    row_values = [[row[column] for column in _COLUMNS] for row in rows]

    count = replace_table(db_path, table_name, _CREATE_TABLE_SQL, _COLUMNS, row_values)

    logger.info("Wrote %d Sleeper players to %s (table %s)", count, db_path, table_name)
    return count
