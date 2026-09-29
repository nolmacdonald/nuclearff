"""Shared loaders for the Chopped league analytics (epic #223).

Every Chopped metric needs the same context: which leagues in the database
are Chopped-format, their FAAB budget and last processed chop, each
manager's name keyed by owner id, and the week each roster was chopped.
These helpers read that context from tables nuclearff already persists:

- ``sleeper_leagues``: ``settings`` (raw JSON) carries ``type``,
  ``last_chopped_leg`` and ``waiver_budget``.
- ``sleeper_standings``: ``owner_id`` and ``display_name`` per roster, per
  season.
- ``sleeper_chopped_rosters``: ``eliminated_leg`` per roster, written by
  :func:`nuclearff.sleeper.standings.fetch_and_write_standings` for
  Chopped leagues only.

**Managers are keyed by ``owner_id``**, not display name or roster id: a
roster id is only meaningful within one season, and a display name can
change between seasons (a real case in NUCLEARFF REDRAFT split one owner
into two "managers"). :func:`manager_names` labels each owner with their
latest-season display name.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from nuclearff.exceptions import ChoppedLeagueError
from nuclearff.sleeper.standings import is_chopped_league

_LEAGUES_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "total_rosters": pl.Int64,
    "waiver_budget": pl.Int64,
    "last_chopped_leg": pl.Int64,
}


def _settings(value: Any) -> dict[str, Any]:
    """Decode a ``sleeper_leagues.settings`` value (JSON text or a dict)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return {}


def chopped_leagues(leagues: pl.DataFrame) -> pl.DataFrame:
    """The Chopped-format leagues in ``sleeper_leagues``, with their settings.

    Args:
        leagues: ``sleeper_leagues`` rows -- needs ``league_id``, ``season``,
            ``total_rosters`` and ``settings`` (raw JSON).

    Returns:
        One row per Chopped league (:func:`~nuclearff.sleeper.standings.
        is_chopped_league`): ``league_id``, ``season``, ``total_rosters``,
        ``waiver_budget`` (the starting FAAB budget, from the league's own
        settings, never assumed) and ``last_chopped_leg`` (the latest week
        whose chop has been processed). Sorted by season.
    """
    rows = []
    for row in leagues.iter_rows(named=True):
        settings = _settings(row.get("settings"))
        if not is_chopped_league({"settings": settings}):
            continue
        rows.append(
            {
                "league_id": str(row["league_id"]),
                "season": row.get("season"),
                "total_rosters": row.get("total_rosters"),
                "waiver_budget": settings.get("waiver_budget"),
                "last_chopped_leg": settings.get("last_chopped_leg"),
            }
        )
    return pl.DataFrame(rows, schema=_LEAGUES_SCHEMA).sort("season")


def require_chopped_leagues(leagues: pl.DataFrame) -> pl.DataFrame:
    """:func:`chopped_leagues`, raising if there are none.

    Args:
        leagues: ``sleeper_leagues`` rows.

    Returns:
        The Chopped leagues (at least one row).

    Raises:
        ChoppedLeagueError: If no league in ``leagues`` is Chopped-format.
    """
    found = chopped_leagues(leagues)
    if found.height == 0:
        raise ChoppedLeagueError(
            "No Chopped-format league found (settings.type == 3 with "
            "last_chopped_leg). Fetch one with `nuclearff sleeper fetch-league "
            "--league-id <id> --standings --matchups --transactions`."
        )
    return found


def manager_names(standings: pl.DataFrame) -> dict[str, str]:
    """Each owner's display name from their latest season.

    Args:
        standings: ``sleeper_standings`` rows -- needs ``season``,
            ``owner_id`` and ``display_name``.

    Returns:
        ``owner_id`` mapped to the display name of that owner's most recent
        season with a name. Owners with no name in any season are absent.
    """
    named = standings.filter(
        pl.col("owner_id").is_not_null() & pl.col("display_name").is_not_null()
    ).sort("season")
    return dict(named.select("owner_id", "display_name").iter_rows())


def roster_owners(standings: pl.DataFrame) -> pl.DataFrame:
    """Each roster's owner id and manager name, per league.

    Args:
        standings: ``sleeper_standings`` rows -- needs ``league_id``,
            ``season``, ``roster_id``, ``owner_id`` and ``display_name``.

    Returns:
        ``league_id``, ``roster_id``, ``owner_id``, ``manager`` (from
        :func:`manager_names`). A roster with no owner that season (Sleeper
        drops a departed manager from past rosters) has null ``owner_id``
        and ``manager``.
    """
    names = manager_names(standings)
    return standings.select(
        pl.col("league_id").cast(pl.String),
        pl.col("roster_id").cast(pl.Int64),
        pl.col("owner_id"),
        pl.col("owner_id").replace_strict(names, default=None).alias("manager"),
    )


def eliminations(chopped_rosters: pl.DataFrame, league_ids: list[str]) -> pl.DataFrame:
    """Each roster's elimination leg, for the given Chopped leagues.

    Args:
        chopped_rosters: ``sleeper_chopped_rosters`` rows -- needs
            ``league_id``, ``roster_id`` and ``eliminated_leg``.
        league_ids: Chopped leagues that must be covered.

    Returns:
        ``league_id``, ``roster_id``, ``eliminated_leg`` (null while alive,
        and for the winner).

    Raises:
        ChoppedLeagueError: If a league in ``league_ids`` has no rows. Who
            was alive in a week can't be told from matchups alone (Sleeper
            keeps a 0.0-point row for every chopped roster), so the analytics
            refuse to guess.
    """
    frame = chopped_rosters.select(
        pl.col("league_id").cast(pl.String),
        pl.col("roster_id").cast(pl.Int64),
        pl.col("eliminated_leg").cast(pl.Int64),
    ).filter(pl.col("league_id").is_in(league_ids))
    missing = sorted(set(league_ids) - set(frame["league_id"].to_list()))
    if missing:
        raise ChoppedLeagueError(
            f"No sleeper_chopped_rosters rows for league(s) {missing}; refetch with "
            "`nuclearff sleeper fetch-league --league-id <id> --standings`."
        )
    return frame


def alive_in_week(eliminated_leg: pl.Expr, week: pl.Expr) -> pl.Expr:
    """Whether a roster was still alive (not yet chopped) in a week.

    A roster chopped in week ``w`` was alive for weeks 1 through ``w``: it
    played week ``w`` and scored lowest.

    Args:
        eliminated_leg: The roster's elimination leg (null if never chopped).
        week: The week.

    Returns:
        A boolean expression.
    """
    return eliminated_leg.is_null() | (eliminated_leg >= week)
