"""Persist each roster's real fantasy team name per season (issue #130, epic #116).

``SleeperClient.get_rosters`` already carries ``roster.metadata.team_name``
per season, but nothing persists it — ``sleeper/standings.py
::roster_display_names`` only resolves the owner's Sleeper **account
username** (``users[].display_name``), which is a different field that
almost never changes. A new table, not a column bolted onto
``sleeper_standings``, avoids a schema migration on that already-shipped
table (``CREATE TABLE IF NOT EXISTS`` can't add a column to a table that
already exists on a real database file).

Reuses the already-called ``get_rosters`` endpoint — no new Sleeper API
surface, matching this issue's own non-goal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import merge_table
from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_roster_names"
"""Default table name, used by :func:`fetch_and_write_team_names`."""

_COLUMNS = ("league_id", "season", "roster_id", "owner_id", "team_name")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    league_id VARCHAR,
    season INTEGER,
    roster_id INTEGER,
    owner_id VARCHAR,
    team_name VARCHAR,
    PRIMARY KEY (league_id, roster_id)
)
"""


def roster_team_names(rosters: list[dict[str, Any]]) -> dict[int, str | None]:
    """Map each roster to its real fantasy team name, if one is set.

    Args:
        rosters: Raw roster objects, as returned by
            :meth:`SleeperClient.get_rosters`.

    Returns:
        ``roster_id`` mapped to ``roster.metadata.team_name``, or ``None``
        if unset (Sleeper's default display — the owner's username, not a
        real custom name — falls back to ``None`` here, not treated as a
        distinct name) or blank.
    """
    result: dict[int, str | None] = {}
    for roster in rosters:
        roster_id = roster.get("roster_id")
        if not isinstance(roster_id, int):
            continue
        metadata = roster.get("metadata")
        team_name = metadata.get("team_name") if isinstance(metadata, dict) else None
        result[roster_id] = team_name or None
    return result


def team_name_rows(
    league_id: str, season: int | None, rosters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build :data:`TABLE_NAME` rows for one season.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        rosters: Raw roster objects.

    Returns:
        One row per roster with an integer ``roster_id``: ``league_id``,
        ``season``, ``roster_id``, ``owner_id``, ``team_name`` (``None``
        for a roster with no custom name set).
    """
    names = roster_team_names(rosters)
    rows: list[dict[str, Any]] = []
    for roster in rosters:
        roster_id = roster.get("roster_id")
        if not isinstance(roster_id, int):
            continue
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "roster_id": roster_id,
                "owner_id": roster.get("owner_id"),
                "team_name": names.get(roster_id),
            }
        )
    return rows


def fetch_and_write_team_names(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
) -> int:
    """Fetch rosters for every league in a chain and persist real team names.

    A rosters-fetch failure for one season is logged and degrades to no
    rows for that season, matching :func:`nuclearff.sleeper.standings
    .fetch_and_write_standings`'s posture, rather than aborting the whole
    chain. Only replaces rows for the ``league_id``\\ s in ``leagues`` (via
    :func:`nuclearff.duckdb_io.merge_table`) — every other league already in
    this table is left untouched.

    Args:
        client: A configured Sleeper client.
        leagues: Raw league payloads for every season to cover, as returned
            by :func:`nuclearff.sleeper.leagues.walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table.

    Returns:
        The number of rows written.
    """
    rows: list[dict[str, Any]] = []
    for league in leagues:
        league_id = str(league["league_id"])
        season = int(league["season"]) if league.get("season") else None
        try:
            rosters = client.get_rosters(league_id)
        except SleeperAPIError as exc:
            logger.warning("Could not fetch rosters for league %s: %s", league_id, exc)
            rosters = []
        rows.extend(team_name_rows(league_id, season, rosters))

    league_ids = [str(league["league_id"]) for league in leagues]
    count = merge_table(
        db_path,
        table_name,
        _CREATE_TABLE_SQL,
        _COLUMNS,
        [[row[column] for column in _COLUMNS] for row in rows],
        key_column="league_id",
        key_values=league_ids,
    )
    logger.info("Wrote %d roster name row(s) to %s", count, db_path)
    return count
