"""Walk a league's ``previous_league_id`` chain and persist history to DuckDB.

``nuclearff sleeper fetch-league`` captures a single league's current-season
payload. A league's ``previous_league_id`` links back to the same league in
its prior season, but nothing walks that chain — there is no durable,
queryable record of a league's history the way the Sleeper player map already
has one (:mod:`nuclearff.sleeper.players`). This module fetches every hop in
that chain and writes both the raw Sleeper payloads and their parsed
:class:`~nuclearff.config.league.LeagueConfig` fields to DuckDB via
:mod:`nuclearff.duckdb_io`, following the same plain-SQL pattern.

A hop that fails to fetch stops the walk (its own ``previous_league_id`` is
needed to keep going); a hop that fetches but fails to type into
``LeagueConfig`` keeps its raw row and simply has no config row. Neither
aborts the whole walk — see :func:`walk_league_chain` and
:func:`league_config_rows`.

**Incremental fetch (issue #180, a follow-up to #148).** A completed
season's league object cannot change, so :func:`walk_league_chain` accepts
an optional ``db_path``: when given, a hop already cached in
:data:`TABLE_NAME` with ``status == "complete"`` is reconstructed from that
row instead of an extra live :meth:`SleeperClient.get_league` call. The raw
table doesn't carry ``draft_id`` (it's only parsed onto
:data:`CONFIG_TABLE_NAME`), so the reconstruction joins against that table
for it — see :func:`_load_cached_hops` and :func:`_reconstruct_hop`.
``db_path=None`` (the default) preserves the always-live behavior exactly,
so no existing caller is affected unless it opts in.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nuclearff.config.league import league_config_from_sleeper
from nuclearff.duckdb_io import merge_table, read_table
from nuclearff.exceptions import ConfigError, SleeperAPIError, StorageError
from nuclearff.sleeper.client import SleeperClient

logger = logging.getLogger(__name__)

TABLE_NAME = "sleeper_leagues"
"""Default table name for raw league payloads, used by :func:`write_league_tables`."""

CONFIG_TABLE_NAME = "sleeper_league_configs"
"""Default table name for parsed ``LeagueConfig`` rows.

Used by :func:`write_league_tables`.
"""

DEFAULT_MAX_SEASONS = 20
"""Default cap on chain length, guarding against an unexpectedly long walk."""

_LEAGUE_TYPE_NAMES = {0: "redraft", 1: "keeper", 2: "dynasty", 3: "chopped"}
"""Sleeper's numeric ``settings.type`` mapped to a readable label.

An unrecognized value falls back to ``"unknown"`` rather than raising —
Sleeper's own docs only document 0/1/2, but nothing guarantees that stays
exhaustive. ``3`` ("Chopped": lowest scorer eliminated weekly, no playoff
bracket) was confirmed live against a real league
(``1262207133378695168``) while investigating GitHub Issue 34 — see
:mod:`nuclearff.sleeper.standings` for how that league type's final
standing is derived without a bracket.
"""


def league_type_name(league: dict[str, Any]) -> str:
    """Resolve a raw league payload's ``settings.type`` to a readable label.

    Deliberately trusts ``settings.type`` alone, not
    :func:`nuclearff.sleeper.standings.is_chopped_league`'s stricter
    ``type == 3 and "last_chopped_leg" in settings`` check -- that check
    exists to confirm a Chopped league has actually started eliminating
    rosters before running Chopped-specific standings logic, not to answer
    "what kind of league is this." Real data shows why the distinction
    matters: of a real account's 4 real ``type == 3`` leagues, only 3 carry
    ``last_chopped_leg`` -- the 4th just hasn't chopped anyone yet, but it is
    still, declaratively, a Chopped league.

    Args:
        league: A raw league payload, as returned by
            :meth:`SleeperClient.get_league` or :meth:`SleeperClient.
            get_user_leagues`.

    Returns:
        One of ``"redraft"``, ``"keeper"``, ``"dynasty"``, ``"chopped"``, or
        ``"unknown"`` for any other value -- Sleeper's own docs only
        document 0/1/2, and nothing guarantees that stays exhaustive.
    """
    settings = league.get("settings")
    if not isinstance(settings, dict):
        return "unknown"
    return _LEAGUE_TYPE_NAMES.get(settings.get("type"), "unknown")


_LEAGUE_COLUMNS = (
    "league_id",
    "previous_league_id",
    "season",
    "name",
    "status",
    "league_type",
    "total_rosters",
    "settings",
    "scoring_settings",
    "roster_positions",
    "fetched_at",
)
"""Columns for :data:`TABLE_NAME`, matching :data:`_LEAGUE_CREATE_TABLE_SQL`'s order."""

_LEAGUE_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    league_id VARCHAR PRIMARY KEY,
    previous_league_id VARCHAR,
    season INTEGER,
    name VARCHAR,
    status VARCHAR,
    league_type VARCHAR,
    total_rosters INTEGER,
    settings JSON,
    scoring_settings JSON,
    roster_positions JSON,
    fetched_at TIMESTAMP
)
"""
"""Schema for :data:`TABLE_NAME`. ``settings``/``scoring_settings``/``roster_positions``
carry the raw Sleeper payload verbatim, the same posture
:class:`nuclearff.config.league.ScoringSettings`/``RosterSlots`` take for tolerating
an unfixed key set — see that module's docstring."""

_CONFIG_COLUMNS = (
    "league_id",
    "season",
    "name",
    "num_teams",
    "league_type",
    "best_ball",
    "is_full_ppr",
    "has_te_premium",
    "draft_id",
    "previous_league_id",
    "playoff_week_start",
    "roster_qb",
    "roster_rb",
    "roster_wr",
    "roster_te",
    "roster_flex",
    "roster_superflex",
    "roster_bench",
    "roster_ir",
)
"""Columns for :data:`CONFIG_TABLE_NAME`, in the same order as
:data:`_CONFIG_CREATE_TABLE_SQL`. Scoring is deliberately not flattened here —
Sleeper's scoring-key set is not fixed (see
:class:`nuclearff.config.league.ScoringSettings`), and the raw
``scoring_settings`` JSON already lives in :data:`TABLE_NAME`, joinable by
``league_id``."""

_CONFIG_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    league_id VARCHAR PRIMARY KEY,
    season INTEGER,
    name VARCHAR,
    num_teams INTEGER,
    league_type INTEGER,
    best_ball BOOLEAN,
    is_full_ppr BOOLEAN,
    has_te_premium BOOLEAN,
    draft_id VARCHAR,
    previous_league_id VARCHAR,
    playoff_week_start INTEGER,
    roster_qb INTEGER,
    roster_rb INTEGER,
    roster_wr INTEGER,
    roster_te INTEGER,
    roster_flex INTEGER,
    roster_superflex INTEGER,
    roster_bench INTEGER,
    roster_ir INTEGER
)
"""
"""Schema for :data:`CONFIG_TABLE_NAME`."""


def _chain_previous_league_id(league_json: dict[str, Any]) -> str | None:
    """Return the next hop's league ID, or ``None`` if the chain ends here.

    Sleeper represents "no prior season" inconsistently across leagues:
    the key can be missing, ``null``, an empty string, or the string
    ``"0"``. All four are treated as chain termination.

    Args:
        league_json: A raw Sleeper league object.

    Returns:
        The prior season's league ID, or ``None`` if there is none.
    """
    raw = league_json.get("previous_league_id")
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text and text != "0" else None


def _load_cached_hops(
    db_path: str | Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None]]:
    """Load cached league rows and draft ids for incremental chain walking.

    Args:
        db_path: Path to the DuckDB database file.

    Returns:
        ``(cached_rows, draft_ids)`` — ``cached_rows`` maps ``league_id`` to
        its cached :data:`TABLE_NAME` row, ``draft_ids`` maps ``league_id``
        to its cached :data:`CONFIG_TABLE_NAME` ``draft_id`` (absent if that
        league has no parsed config row). Both are empty on a first-ever run
        (no database file or table yet).
    """
    try:
        raw = read_table(db_path, TABLE_NAME)
    except StorageError:
        return {}, {}
    cached_rows = {row["league_id"]: row for row in raw.to_dicts()}

    try:
        configs = read_table(db_path, CONFIG_TABLE_NAME)
    except StorageError:
        return cached_rows, {}
    draft_ids = {row["league_id"]: row.get("draft_id") for row in configs.to_dicts()}

    return cached_rows, draft_ids


def _reconstruct_hop(row: dict[str, Any], draft_id: str | None) -> dict[str, Any]:
    """Rebuild a live-shaped league payload from a cached :data:`TABLE_NAME` row.

    Args:
        row: A cached :data:`TABLE_NAME` row, as returned by
            :func:`nuclearff.duckdb_io.read_table` (JSON columns as text).
        draft_id: The league's cached ``draft_id``, from
            :data:`CONFIG_TABLE_NAME` — not stored on the raw row itself
            (see the module docstring).

    Returns:
        A dict shaped like :meth:`SleeperClient.get_league`'s return value —
        enough for every downstream consumer of a chain hop
        (:func:`write_league_tables`,
        :func:`nuclearff.config.league.league_config_from_sleeper`, and
        every ``fetch_and_write_*`` function, which only read
        ``league_id``/``season``/``status`` off a hop).
    """
    return {
        "league_id": row["league_id"],
        "previous_league_id": row.get("previous_league_id"),
        "season": row.get("season"),
        "name": row.get("name"),
        "status": row.get("status"),
        "total_rosters": row.get("total_rosters"),
        "settings": json.loads(row.get("settings") or "{}"),
        "scoring_settings": json.loads(row.get("scoring_settings") or "{}"),
        "roster_positions": json.loads(row.get("roster_positions") or "[]"),
        "draft_id": draft_id,
    }


def _cached_hop(
    hop_id: str,
    cached_rows: dict[str, dict[str, Any]],
    draft_ids: dict[str, str | None],
) -> dict[str, Any] | None:
    """Return a reconstructed payload for ``hop_id`` if it's safely reusable.

    Args:
        hop_id: The league ID this hop needs.
        cached_rows: From :func:`_load_cached_hops`.
        draft_ids: From :func:`_load_cached_hops`.

    Returns:
        A reconstructed payload (:func:`_reconstruct_hop`) if ``hop_id`` is
        cached with ``status == "complete"`` — a finished season's payload
        cannot change — otherwise ``None``, meaning the caller must fetch it
        live.
    """
    row = cached_rows.get(hop_id)
    if row is None or row.get("status") != "complete":
        return None
    return _reconstruct_hop(row, draft_ids.get(hop_id))


def walk_league_chain(
    client: SleeperClient,
    league_id: str,
    *,
    max_seasons: int = DEFAULT_MAX_SEASONS,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch a league and every prior season reachable via ``previous_league_id``.

    Args:
        client: A configured Sleeper client.
        league_id: The starting (most recent) league identifier.
        max_seasons: Maximum number of hops to fetch, including the starting
            league. Guards against an unexpectedly long or malformed chain.
        db_path: Path to the DuckDB database file to consult for already-
            cached, completed hops (see the module docstring's "Incremental
            fetch" note). Omit (or pass ``None``, the default) to always
            fetch every hop live, unchanged from this function's original
            behavior.

    Returns:
        Raw league payloads, most recent season first, in fetch order. Always
        has at least one entry. A payload reused from cache instead of fetched
        live is reconstructed (see :func:`_reconstruct_hop`), not the exact
        object Sleeper would return, but equivalent for every field this
        project reads off a league payload.

    Raises:
        SleeperAPIError: If the starting league itself cannot be fetched and
            is not already cached as complete. A failure on a later hop is
            logged and stops the walk instead of raising — see the module
            docstring.
    """
    cached_rows, draft_ids = (
        _load_cached_hops(db_path) if db_path is not None else ({}, {})
    )

    first = _cached_hop(league_id, cached_rows, draft_ids)
    leagues: list[dict[str, Any]] = [
        first if first is not None else client.get_league(league_id)
    ]
    seen = {league_id}

    while len(leagues) < max_seasons:
        next_id = _chain_previous_league_id(leagues[-1])
        if next_id is None:
            break
        if next_id in seen:
            logger.warning(
                "Sleeper league chain from %s revisits %s; stopping walk",
                league_id,
                next_id,
            )
            break

        cached = _cached_hop(next_id, cached_rows, draft_ids)
        if cached is not None:
            leagues.append(cached)
            seen.add(next_id)
            continue

        try:
            next_league = client.get_league(next_id)
        except SleeperAPIError as exc:
            logger.warning(
                "Could not fetch league %s (previous_league_id of %s): %s",
                next_id,
                leagues[-1]["league_id"],
                exc,
            )
            break

        leagues.append(next_league)
        seen.add(next_id)

    if len(leagues) >= max_seasons:
        logger.info(
            "Sleeper league chain from %s hit max_seasons=%d; stopping walk",
            league_id,
            max_seasons,
        )

    return leagues


def league_chain_ids(leagues: pl.DataFrame, league_id: str) -> list[str]:
    """Resolve every ``league_id`` in ``league_id``'s chain from persisted rows.

    Unlike :func:`walk_league_chain`, this is a pure lookup against
    :data:`TABLE_NAME` rows an earlier ``sleeper fetch-league --history`` run
    already wrote — no live Sleeper calls. Built for issue #118: the same
    DuckDB cache can hold multiple, unrelated leagues (real for this
    project's own dev/demo cache), so a report meant to cover one franchise's
    full multi-season history needs to scope itself to that franchise's real
    chain rather than either a single season or every league in the cache —
    the same class of cross-league mixing the 2026-09-10 ``merge_table`` fix
    addressed for writes, here on the read side.

    Args:
        leagues: :data:`TABLE_NAME` rows (``league_id``,
            ``previous_league_id``).
        league_id: The season to start from.

    Returns:
        Every ``league_id`` in the chain, including ``league_id`` itself. A
        ``league_id`` absent from ``leagues`` (e.g. history was never
        fetched) returns just itself, not an empty list — callers can still
        scope to the one season they know about.
    """
    previous_by_id = {
        row["league_id"]: row.get("previous_league_id") for row in leagues.to_dicts()
    }

    chain = {league_id}
    current = league_id
    while True:
        previous = previous_by_id.get(current)
        if not previous or previous in chain:
            break
        chain.add(previous)
        current = previous
    return list(chain)


def league_rows(
    leagues: list[dict[str, Any]], *, fetched_at: datetime | None = None
) -> list[dict[str, Any]]:
    """Flatten raw league payloads into :data:`TABLE_NAME` rows.

    Args:
        leagues: Raw league payloads, as returned by :func:`walk_league_chain`.
        fetched_at: Timestamp to record for every row. Defaults to now (UTC).

    Returns:
        One row per league, keyed by :data:`_LEAGUE_COLUMNS`.
    """
    if fetched_at is None:
        fetched_at = datetime.now(tz=UTC)

    rows: list[dict[str, Any]] = []
    for league in leagues:
        settings = league.get("settings")
        if not isinstance(settings, dict):
            settings = {}

        previous_league_id = league.get("previous_league_id")
        rows.append(
            {
                "league_id": str(league["league_id"]),
                "previous_league_id": (
                    str(previous_league_id) if previous_league_id else None
                ),
                "season": int(league["season"]) if league.get("season") else None,
                "name": league.get("name"),
                "status": league.get("status"),
                "league_type": league_type_name(league),
                "total_rosters": league.get("total_rosters"),
                "settings": json.dumps(settings),
                "scoring_settings": json.dumps(league.get("scoring_settings") or {}),
                "roster_positions": json.dumps(league.get("roster_positions") or []),
                "fetched_at": fetched_at,
            }
        )
    return rows


def league_config_rows(leagues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse raw league payloads into :data:`CONFIG_TABLE_NAME` rows.

    A payload that does not type into :class:`~nuclearff.config.league.LeagueConfig`
    is logged and skipped rather than raising — its raw row (see
    :func:`league_rows`) is kept regardless.

    Args:
        leagues: Raw league payloads, as returned by :func:`walk_league_chain`.

    Returns:
        One row per league that parsed successfully, keyed by
        :data:`_CONFIG_COLUMNS`.
    """
    rows: list[dict[str, Any]] = []
    for league in leagues:
        try:
            cfg = league_config_from_sleeper(league)
        except ConfigError as exc:
            logger.warning(
                "Could not derive a LeagueConfig for league %s: %s",
                league.get("league_id"),
                exc,
            )
            continue

        rows.append(
            {
                "league_id": cfg.league_id,
                "season": cfg.season,
                "name": cfg.name,
                "num_teams": cfg.num_teams,
                "league_type": cfg.league_type,
                "best_ball": cfg.best_ball,
                "is_full_ppr": cfg.scoring.is_full_ppr,
                "has_te_premium": cfg.scoring.has_te_premium,
                "draft_id": cfg.draft_id,
                "previous_league_id": cfg.previous_league_id,
                "playoff_week_start": cfg.playoff_week_start,
                "roster_qb": cfg.roster.qb,
                "roster_rb": cfg.roster.rb,
                "roster_wr": cfg.roster.wr,
                "roster_te": cfg.roster.te,
                "roster_flex": cfg.roster.flex,
                "roster_superflex": cfg.roster.superflex,
                "roster_bench": cfg.roster.bench,
                "roster_ir": cfg.roster.ir,
            }
        )
    return rows


def write_league_tables(
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
    config_table_name: str = CONFIG_TABLE_NAME,
    fetched_at: datetime | None = None,
) -> tuple[int, int]:
    """Write raw and parsed league history to DuckDB for this one chain.

    Each call replaces both tables' rows for exactly the league_ids in
    ``leagues`` (via :func:`nuclearff.duckdb_io.merge_table`) — a chain
    walk is a full re-fetch of *that chain*, not an incremental feed, but
    every *other* chain's league_ids already in these tables are left
    untouched. A prior version of this function called
    :func:`nuclearff.duckdb_io.replace_table`, which drops the whole table
    first: writing a second, unrelated league's chain silently erased the
    first league's rows entirely, confirmed live 2026-09-10 across three
    real, unrelated leagues on the same account. See
    :func:`~nuclearff.sleeper.matchups.fetch_and_write_matchups`'s
    docstring for the same bug class in a different table.

    Args:
        leagues: Raw league payloads, as returned by :func:`walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table for raw payloads.
        config_table_name: Destination table for parsed ``LeagueConfig`` rows.
        fetched_at: Timestamp to record for every raw row. Defaults to now (UTC).

    Returns:
        A ``(raw_rows_written, config_rows_written)`` tuple. The second is
        less than or equal to the first when a hop failed to parse (see
        :func:`league_config_rows`).

    Raises:
        ValueError: If either table name is not a plain identifier.
    """
    league_ids = [str(league["league_id"]) for league in leagues]

    raw_rows = league_rows(leagues, fetched_at=fetched_at)
    raw_values = [[row[column] for column in _LEAGUE_COLUMNS] for row in raw_rows]
    raw_count = merge_table(
        db_path,
        table_name,
        _LEAGUE_CREATE_TABLE_SQL,
        _LEAGUE_COLUMNS,
        raw_values,
        key_column="league_id",
        key_values=league_ids,
    )

    config_rows = league_config_rows(leagues)
    config_values = [[row[column] for column in _CONFIG_COLUMNS] for row in config_rows]
    config_count = merge_table(
        db_path,
        config_table_name,
        _CONFIG_CREATE_TABLE_SQL,
        _CONFIG_COLUMNS,
        config_values,
        key_column="league_id",
        key_values=league_ids,
    )

    logger.info(
        "Wrote %d Sleeper league(s) (%d with a parsed config) to %s",
        raw_count,
        config_count,
        db_path,
    )
    return raw_count, config_count
