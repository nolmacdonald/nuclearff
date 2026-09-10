"""Fetch weekly transactions across a league's history and persist them to DuckDB.

``SleeperClient.get_transactions`` has existed since the client was first
built, but nothing calls it: no CLI command, no snapshot capture, no DuckDB
table. This module wires it up, combined with
:func:`nuclearff.sleeper.leagues.walk_league_chain` for multi-season history,
following the same plain-SQL DuckDB pattern as
:mod:`nuclearff.sleeper.standings` and :mod:`nuclearff.sleeper.matchups`.

Sleeper's own ``type`` (``"trade"`` / ``"waiver"`` / ``"free_agent"``) and
``status`` (``"complete"`` / ``"failed"``) fields already categorize a
transaction — confirmed live against a real league — so this module trusts
and stores them rather than deriving a category itself.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nuclearff.duckdb_io import merge_table
from nuclearff.exceptions import SleeperAPIError
from nuclearff.sleeper.client import SleeperClient
from nuclearff.sleeper.matchups import DEFAULT_MAX_WEEK
from nuclearff.sleeper.standings import roster_display_names

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_transactions"
"""Default table name for transactions, used by :func:`fetch_and_write_transactions`."""

PLAYERS_TABLE_NAME = "sleeper_transaction_players"
"""Default table name for the unnested add/drop rows."""

_TRANSACTION_COLUMNS = (
    "transaction_id",
    "league_id",
    "season",
    "week",
    "type",
    "status",
    "creator",
    "creator_display_name",
    "roster_ids",
    "roster_display_names",
    "consenter_ids",
    "consenter_display_names",
    "created_at",
    "status_updated_at",
    "adds",
    "drops",
    "draft_picks",
    "waiver_budget",
    "settings",
    "metadata",
)

_TRANSACTION_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    transaction_id VARCHAR PRIMARY KEY,
    league_id VARCHAR,
    season INTEGER,
    week INTEGER,
    type VARCHAR,
    status VARCHAR,
    creator VARCHAR,
    creator_display_name VARCHAR,
    roster_ids JSON,
    roster_display_names JSON,
    consenter_ids JSON,
    consenter_display_names JSON,
    created_at TIMESTAMP,
    status_updated_at TIMESTAMP,
    adds JSON,
    drops JSON,
    draft_picks JSON,
    waiver_budget JSON,
    settings JSON,
    metadata JSON
)
"""

_PLAYER_COLUMNS = (
    "transaction_id",
    "league_id",
    "season",
    "week",
    "player_id",
    "roster_id",
    "direction",
)

_PLAYER_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    transaction_id VARCHAR,
    league_id VARCHAR,
    season INTEGER,
    week INTEGER,
    player_id VARCHAR,
    roster_id INTEGER,
    direction VARCHAR,
    PRIMARY KEY (transaction_id, player_id, direction)
)
"""


def _epoch_ms_to_datetime(value: Any) -> datetime | None:
    """Convert a Sleeper epoch-millisecond timestamp to a UTC ``datetime``.

    Args:
        value: The raw ``created``/``status_updated`` field.

    Returns:
        The converted timestamp, or ``None`` if ``value`` isn't a number.
    """
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _user_display_names(users: list[dict[str, Any]]) -> dict[str, str | None]:
    """Map each Sleeper user id to its display name.

    Args:
        users: Raw user objects, as returned by
            :meth:`SleeperClient.get_users`.

    Returns:
        ``user_id`` mapped to ``display_name``.
    """
    return {
        user["user_id"]: user.get("display_name")
        for user in users
        if isinstance(user, dict) and isinstance(user.get("user_id"), str)
    }


def transaction_rows(
    league_id: str,
    season: int | None,
    week: int,
    transactions: list[dict[str, Any]],
    roster_names: dict[int, str | None],
    user_names: dict[str, str | None],
) -> list[dict[str, Any]]:
    """Build :data:`TABLE_NAME` rows for one week's raw transactions.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        week: The week (Sleeper calls this the transaction "round").
        transactions: Raw transaction objects, as returned by
            :meth:`SleeperClient.get_transactions`.
        roster_names: ``roster_id`` -> display name, from
            :func:`nuclearff.sleeper.standings.roster_display_names`.
        user_names: ``user_id`` -> display name, from
            :func:`_user_display_names`.

    Returns:
        One row per transaction.
    """
    rows: list[dict[str, Any]] = []
    for tx in transactions:
        roster_ids = tx.get("roster_ids") or []
        consenter_ids = tx.get("consenter_ids") or []
        creator = tx.get("creator")
        rows.append(
            {
                "transaction_id": tx.get("transaction_id"),
                "league_id": league_id,
                "season": season,
                "week": week,
                "type": tx.get("type"),
                "status": tx.get("status"),
                "creator": creator,
                "creator_display_name": (
                    user_names.get(creator) if isinstance(creator, str) else None
                ),
                "roster_ids": json.dumps(roster_ids),
                "roster_display_names": json.dumps(
                    [roster_names.get(rid) for rid in roster_ids]
                ),
                "consenter_ids": json.dumps(consenter_ids),
                "consenter_display_names": json.dumps(
                    [roster_names.get(rid) for rid in consenter_ids]
                ),
                "created_at": _epoch_ms_to_datetime(tx.get("created")),
                "status_updated_at": _epoch_ms_to_datetime(tx.get("status_updated")),
                "adds": json.dumps(tx.get("adds") or {}),
                "drops": json.dumps(tx.get("drops") or {}),
                "draft_picks": json.dumps(tx.get("draft_picks") or []),
                "waiver_budget": json.dumps(tx.get("waiver_budget") or []),
                "settings": json.dumps(tx.get("settings") or {}),
                "metadata": json.dumps(tx.get("metadata") or {}),
            }
        )
    return rows


def transaction_player_rows(
    league_id: str, season: int | None, week: int, transactions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Unnest each transaction's ``adds``/``drops`` into per-player rows.

    Args:
        league_id: The season's Sleeper league identifier.
        season: The season year.
        week: The week these transactions are for.
        transactions: Raw transaction objects.

    Returns:
        One row per (transaction, player, direction).
    """
    rows: list[dict[str, Any]] = []
    for tx in transactions:
        transaction_id = tx.get("transaction_id")
        for direction in ("adds", "drops"):
            for player_id, roster_id in (tx.get(direction) or {}).items():
                rows.append(
                    {
                        "transaction_id": transaction_id,
                        "league_id": league_id,
                        "season": season,
                        "week": week,
                        "player_id": player_id,
                        "roster_id": roster_id,
                        "direction": direction[:-1],  # "adds" -> "add"
                    }
                )
    return rows


def fetch_and_write_transactions(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    max_week: int = DEFAULT_MAX_WEEK,
    table_name: str = TABLE_NAME,
    players_table_name: str = PLAYERS_TABLE_NAME,
) -> tuple[int, int]:
    """Fetch every week's transactions for every league in a chain and persist them.

    A week that fails to fetch is logged and skipped; it does not abort the
    rest of the season or the walk, matching
    :func:`nuclearff.sleeper.matchups.fetch_and_write_matchups`'s posture.

    Only replaces rows for the league_ids in ``leagues`` (via
    :func:`nuclearff.duckdb_io.merge_table`) -- every other league already
    in these tables is left untouched. See
    :func:`~nuclearff.sleeper.matchups.fetch_and_write_matchups`'s
    docstring for the cross-league data-loss bug this guards against.

    Args:
        client: A configured Sleeper client.
        leagues: Raw league payloads for every season to cover, as returned
            by :func:`nuclearff.sleeper.leagues.walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        max_week: Maximum week number to fetch per season.
        table_name: Destination table for transactions.
        players_table_name: Destination table for unnested add/drop rows.

    Returns:
        A ``(transaction_rows_written, player_rows_written)`` tuple.
    """
    transactions_out: list[dict[str, Any]] = []
    players_out: list[dict[str, Any]] = []

    for league in leagues:
        league_id = str(league["league_id"])
        season = int(league["season"]) if league.get("season") else None

        try:
            rosters = client.get_rosters(league_id)
            users = client.get_users(league_id)
        except SleeperAPIError as exc:
            logger.warning(
                "Could not fetch rosters/users for league %s: %s", league_id, exc
            )
            rosters, users = [], []
        roster_names = roster_display_names(rosters, users)
        user_names = _user_display_names(users)

        for week in range(1, max_week + 1):
            try:
                week_transactions = client.get_transactions(league_id, week)
            except SleeperAPIError as exc:
                logger.warning(
                    "Could not fetch transactions for league %s week %d: %s",
                    league_id,
                    week,
                    exc,
                )
                continue
            if not week_transactions:
                continue
            transactions_out.extend(
                transaction_rows(
                    league_id, season, week, week_transactions, roster_names, user_names
                )
            )
            players_out.extend(
                transaction_player_rows(league_id, season, week, week_transactions)
            )

    league_ids = [str(league["league_id"]) for league in leagues]
    transaction_count = merge_table(
        db_path,
        table_name,
        _TRANSACTION_CREATE_TABLE_SQL,
        _TRANSACTION_COLUMNS,
        [[row[column] for column in _TRANSACTION_COLUMNS] for row in transactions_out],
        key_column="league_id",
        key_values=league_ids,
    )
    player_count = merge_table(
        db_path,
        players_table_name,
        _PLAYER_CREATE_TABLE_SQL,
        _PLAYER_COLUMNS,
        [[row[column] for column in _PLAYER_COLUMNS] for row in players_out],
        key_column="league_id",
        key_values=league_ids,
    )

    logger.info(
        "Wrote %d transaction(s) and %d transaction-player row(s) to %s",
        transaction_count,
        player_count,
        db_path,
    )
    return transaction_count, player_count
