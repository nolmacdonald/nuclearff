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

import polars as pl

from nuclearff.duckdb_io import merge_table
from nuclearff.exceptions import SleeperAPIError
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
CREATE TABLE IF NOT EXISTS {table} (
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
    draft: dict[str, Any] | None = None,
) -> int:
    """Fetch a draft and its picks, and persist the picks to DuckDB.

    Only replaces rows for this ``draft_id`` (via
    :func:`nuclearff.duckdb_io.merge_table`), not this draft's whole
    ``league_id`` -- a league can have more than one draft in a season (see
    :func:`fetch_and_write_all_drafts`'s docstring), so scoping by
    ``league_id`` here would erase a sibling draft's picks. Every other
    draft_id/league_id already in :data:`TABLE_NAME` is left untouched. See
    :func:`~nuclearff.sleeper.matchups.fetch_and_write_matchups`'s
    docstring for the cross-league data-loss bug this guards against.

    Args:
        client: A configured Sleeper client.
        draft_id: Sleeper draft identifier.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table.
        draft: The raw draft object, if a caller already fetched it for its
            own purposes (e.g. ``report draft-board`` needs ``settings``/
            ``draft_order`` regardless). Passing it here avoids fetching
            the same draft live a second time; omit it to fetch it fresh.

    Returns:
        The number of pick rows written. ``0`` before any pick has been
        made, which is valid data (a draft that hasn't started yet), not an
        error.
    """
    if draft is None:
        draft = client.get_draft(draft_id)
    picks = client.get_draft_picks(draft_id)
    rows = draft_pick_rows(draft, picks)

    count = merge_table(
        db_path,
        table_name,
        _CREATE_TABLE_SQL,
        _COLUMNS,
        [[row[column] for column in _COLUMNS] for row in rows],
        key_column="draft_id",
        key_values=[draft_id],
    )

    logger.info("Wrote %d draft pick row(s) to %s", count, db_path)
    return count


def fetch_and_write_all_drafts(
    client: SleeperClient,
    leagues: list[dict[str, Any]],
    db_path: str | Path,
    *,
    table_name: str = TABLE_NAME,
) -> int:
    """Fetch every draft for every league in a chain and persist them all.

    :func:`fetch_and_write_draft_picks` scopes its write to a single
    ``draft_id`` -- calling it once per season in a loop would leave every
    *other* season's drafts for this league untouched, but still miss the
    point: this function collects every season's picks first and writes
    them in one call, the same multi-season accumulation shape
    :func:`nuclearff.sleeper.matchups.fetch_and_write_matchups` already
    uses, then replaces by ``league_id`` (via
    :func:`nuclearff.duckdb_io.merge_table`) rather than ``draft_id`` --
    covering every draft a league has at once. Only replaces rows for
    league_ids successfully queried this call; a league whose own
    :meth:`~nuclearff.sleeper.client.SleeperClient.get_league_drafts` call
    failed keeps its previously-written rows untouched rather than being
    silently erased on a transient refetch failure. Every *other* league
    already in :data:`TABLE_NAME` is also left untouched. See
    :func:`~nuclearff.sleeper.matchups.fetch_and_write_matchups`'s
    docstring for the cross-league data-loss bug this guards against.

    A league can have more than one draft in a season (Sleeper's own
    :meth:`SleeperClient.get_league_drafts` returns "every draft associated
    with a league", not just one) -- every draft found is fetched and
    included, not just the first. A season/league whose drafts fail to
    fetch is logged and skipped, matching
    :func:`fetch_and_write_matchups`'s posture of not aborting the whole
    walk over one bad hop.

    Args:
        client: A configured Sleeper client.
        leagues: Raw league payloads for every season to cover, as returned
            by :func:`nuclearff.sleeper.leagues.walk_league_chain`.
        db_path: Path to the DuckDB database file, created if absent.
        table_name: Destination table.

    Returns:
        The number of pick rows written, across every season and draft.
    """
    rows: list[dict[str, Any]] = []
    fetched_league_ids: list[str] = []

    for league in leagues:
        league_id = str(league["league_id"])
        try:
            drafts = client.get_league_drafts(league_id)
        except SleeperAPIError as exc:
            logger.warning("Could not fetch drafts for league %s: %s", league_id, exc)
            continue

        fetched_league_ids.append(league_id)

        for draft_summary in drafts:
            draft_id = draft_summary.get("draft_id")
            if not draft_id:
                continue
            try:
                draft = client.get_draft(draft_id)
                picks = client.get_draft_picks(draft_id)
            except SleeperAPIError as exc:
                logger.warning(
                    "Could not fetch draft %s for league %s: %s",
                    draft_id,
                    league_id,
                    exc,
                )
                continue
            rows.extend(draft_pick_rows(draft, picks))

    count = merge_table(
        db_path,
        table_name,
        _CREATE_TABLE_SQL,
        _COLUMNS,
        [[row[column] for column in _COLUMNS] for row in rows],
        key_column="league_id",
        key_values=fetched_league_ids,
    )

    logger.info(
        "Wrote %d draft pick row(s) across %d league(s) to %s",
        count,
        len(leagues),
        db_path,
    )
    return count


_DRAFT_ORDER_SCHEMA = {
    "manager": pl.String,
    "seasons_drafted": pl.UInt32,
    "avg_draft_position": pl.Float64,
    "times_first_pick": pl.UInt32,
    "times_last_pick": pl.UInt32,
}


def draft_order_stats(picks: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """Per-manager draft-order history: average position, times 1st, times last.

    Uses each season's round-1 picks only -- a roster's ``draft_slot`` is
    constant across every round of one draft (Sleeper's own resolved
    value, not derived from an assumed alternating snake -- see this
    module's docstring), so round 1 alone already gives the season's full
    draft order per manager, with no risk of double-counting a manager's
    seasons by reading every round.

    "Last pick" is season-relative, not a fixed number: a league's team
    count can change season to season, so a season's own real maximum
    ``draft_slot`` (not a global constant) is what "last" is compared
    against.

    Args:
        picks: ``sleeper_draft_picks`` rows, e.g.
            :func:`fetch_and_write_all_drafts`'s output read back via
            :func:`nuclearff.duckdb_io.read_table`.
        standings: ``sleeper_standings`` rows -- needs ``league_id``,
            ``roster_id``, ``display_name``.

    Returns:
        One row per manager: ``manager``, ``seasons_drafted``,
        ``avg_draft_position``, ``times_first_pick``, ``times_last_pick``.
        A roster with no resolvable ``display_name`` contributes no row. A
        manager who was only in the league for some of its seasons has
        stats computed only over the seasons they actually drafted in.
    """
    if picks.height == 0:
        return pl.DataFrame(schema=_DRAFT_ORDER_SCHEMA)

    round_one = picks.filter(pl.col("round") == 1).select(
        "league_id", "season", "draft_id", "roster_id", "draft_slot"
    )
    season_size = round_one.group_by(["league_id", "draft_id"]).agg(
        pl.col("draft_slot").max().alias("last_slot")
    )
    positioned = round_one.join(season_size, on=["league_id", "draft_id"])

    names = standings.select("league_id", "roster_id", "display_name")
    joined = (
        positioned.join(names, on=["league_id", "roster_id"], how="inner")
        .filter(pl.col("display_name").is_not_null())
        .rename({"display_name": "manager"})
    )

    return (
        joined.group_by("manager")
        .agg(
            pl.len().alias("seasons_drafted"),
            pl.col("draft_slot").mean().alias("avg_draft_position"),
            (pl.col("draft_slot") == 1).sum().alias("times_first_pick"),
            (pl.col("draft_slot") == pl.col("last_slot"))
            .sum()
            .alias("times_last_pick"),
        )
        .sort("avg_draft_position")
    )
