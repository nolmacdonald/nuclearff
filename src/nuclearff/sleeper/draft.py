"""Persist a draft's picks to DuckDB.

:meth:`SleeperClient.get_draft_picks` is already fetched by
:func:`nuclearff.sleeper.snapshot.fetch_league_snapshot` and written to raw
JSON (``draft_picks.json``), but nothing turns it into a queryable table —
the same gap :mod:`nuclearff.sleeper.roster_players` closed for roster
composition. This module flattens a draft's picks, one row per pick, keeping
each pick's real ``draft_slot`` (the grid column
:mod:`nuclearff.report.draft_board` renders it at) rather than deriving pick
order from an assumed alternating snake — see that module's docstring for
why: a draft's ``settings.reversal_round`` can make a later round continue
the same direction as the round before it instead of reversing, and Sleeper
already resolves that into each pick's own ``draft_slot``, so there is
nothing to compute here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import replace_table
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_draft_picks"
"""Default table name, used by :func:`fetch_and_write_draft_picks`."""

_COLUMNS = (
    "draft_id",
    "league_id",
    "season",
    "pick_no",
    "round",
    "draft_slot",
    "roster_id",
    "picked_by",
    "player_id",
    "position",
    "first_name",
    "last_name",
    "team",
    "is_keeper",
)

_CREATE_TABLE_SQL = """
CREATE TABLE {table} (
    draft_id VARCHAR,
    league_id VARCHAR,
    season INTEGER,
    pick_no INTEGER,
    round INTEGER,
    draft_slot INTEGER,
    roster_id INTEGER,
    picked_by VARCHAR,
    player_id VARCHAR,
    position VARCHAR,
    first_name VARCHAR,
    last_name VARCHAR,
    team VARCHAR,
    is_keeper BOOLEAN,
    PRIMARY KEY (draft_id, pick_no)
)
"""


def draft_pick_rows(
    draft: dict[str, Any], picks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Flatten a draft's raw picks into :data:`TABLE_NAME` rows.

    Args:
        draft: The raw draft object, as returned by
            :meth:`SleeperClient.get_draft`. Only ``draft_id``/``league_id``/
            ``season`` are read from it; every pick already carries its own
            ``draft_id``, but a draft's ``league_id``/``season`` are not
            repeated on each pick.
        picks: Raw pick objects, as returned by
            :meth:`SleeperClient.get_draft_picks`.

    Returns:
        One row per pick with a valid ``pick_no``, keeping the player's
        ``position``/``first_name``/``last_name``/``team`` from the pick's
        own ``metadata`` (present on every real pick, so this needs no join
        to ``sleeper_players`` to be immediately useful) rather than
        re-resolving them elsewhere.
    """
    draft_id = draft.get("draft_id")
    league_id = draft.get("league_id")
    season = int(draft["season"]) if draft.get("season") else None

    rows: list[dict[str, Any]] = []
    for pick in picks:
        pick_no = pick.get("pick_no")
        if not isinstance(pick_no, int):
            continue
        metadata = pick.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "draft_id": draft_id,
                "league_id": league_id,
                "season": season,
                "pick_no": pick_no,
                "round": pick.get("round"),
                "draft_slot": pick.get("draft_slot"),
                "roster_id": pick.get("roster_id"),
                "picked_by": pick.get("picked_by"),
                "player_id": pick.get("player_id"),
                "position": metadata.get("position"),
                "first_name": metadata.get("first_name"),
                "last_name": metadata.get("last_name"),
                "team": metadata.get("team"),
                "is_keeper": pick.get("is_keeper"),
            }
        )
    return rows


def fetch_and_write_draft_picks(
    client: SleeperClient,
    draft_id: str,
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
) -> int:
    """Fetch a draft and its picks, and persist the picks to DuckDB.

    Args:
        client: A configured Sleeper client.
        draft_id: Sleeper draft identifier.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table.

    Returns:
        The number of pick rows written. ``0`` before any pick has been
        made, which is valid data (a draft that hasn't started yet), not an
        error.
    """
    draft = client.get_draft(draft_id)
    picks = client.get_draft_picks(draft_id)
    rows = draft_pick_rows(draft, picks)

    count = replace_table(
        db_path,
        table_name,
        _CREATE_TABLE_SQL,
        _COLUMNS,
        [[row[column] for column in _COLUMNS] for row in rows],
    )

    logger.info("Wrote %d draft pick row(s) to %s", count, db_path)
    return count
