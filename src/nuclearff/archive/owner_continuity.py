"""League history archive: owner handoffs detected by roster continuity (issue #153).

The archive treats ``sleeper_standings.owner_id`` as a stable manager identity.
That breaks when a roster passes to a different Sleeper account without an
expansion or re-draft: ``owner_id`` (and ``display_name``) change for the same
``roster_id`` while the players mostly stay. :func:`owner_transitions` catalogs
every season boundary where the owner of a ``roster_id`` changed and how much of
the roster carried over, so a username that vanished from the current league can
be traced to the roster it used to control.

Seasons are linked through ``sleeper_league_configs.previous_league_id`` (a
Sleeper league gets a new ``league_id`` each season), so a boundary is always
between two adjacent seasons of one league lineage.

Overlap is the fraction of the old roster's players still on the roster in the
first season under the new owner. A transition is a ``probable_handoff`` when
that fraction is at least ``min_overlap`` (default :data:`DEFAULT_MIN_OVERLAP`,
a starting point that has not been calibrated against real league data). A
low-overlap change (expansion, re-draft) is reported with
``probable_handoff == False`` rather than guessed at. A flag is a probability
signal, not an assertion of identity: two unrelated owners can both hold a lot
of the same good players.

Limitation: the persisted tables keep only the primary ``owner_id``, not
``roster.co_owners``, so a co-owner being promoted to primary owner looks the
same as a stranger taking over. Nothing here distinguishes them.

This module only builds the catalog. It does not merge identities in any other
archive aggregate.
"""

from __future__ import annotations

import polars as pl

DEFAULT_MIN_OVERLAP = 0.5
"""Retained-roster fraction at or above which a change is a probable handoff."""

_SCHEMA = {
    "league_id": pl.String,
    "roster_id": pl.Int64,
    "old_owner_id": pl.String,
    "old_display_name": pl.String,
    "new_owner_id": pl.String,
    "new_display_name": pl.String,
    "transition_season": pl.Int64,
    "old_player_count": pl.UInt32,
    "players_retained": pl.UInt32,
    "player_overlap_fraction": pl.Float64,
    "probable_handoff": pl.Boolean,
}


def owner_transitions(
    roster_players: pl.DataFrame,
    standings: pl.DataFrame,
    leagues: pl.DataFrame,
    *,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> pl.DataFrame:
    """Every owner change on a fixed ``roster_id``, with roster carry-over.

    Args:
        roster_players: ``sleeper_roster_players`` rows -- ``league_id``,
            ``roster_id``, ``player_id``.
        standings: ``sleeper_standings`` rows -- ``league_id``, ``season``,
            ``roster_id``, ``owner_id``, ``display_name``.
        leagues: ``sleeper_league_configs`` rows -- ``league_id`` and
            ``previous_league_id``, linking each season to the one before it.
        min_overlap: Fraction in ``[0, 1]`` of the old roster that must be
            retained for a change to be a probable handoff.

    Returns:
        One row per ``(league_id, roster_id)`` whose non-null ``owner_id``
        differs from the previous season's, ordered by season then roster:
        ``league_id`` (the new season's), ``roster_id``, ``old_owner_id``,
        ``old_display_name``, ``new_owner_id``, ``new_display_name``,
        ``transition_season``, ``old_player_count``, ``players_retained``,
        ``player_overlap_fraction`` (``players_retained / old_player_count``;
        null when the old roster has no persisted players) and
        ``probable_handoff`` (overlap at least ``min_overlap``; ``False`` when
        overlap is null). A roster whose owner never changes, or that exists in
        only one of two adjacent seasons, yields no row.

    Raises:
        ValueError: If ``min_overlap`` is outside ``[0, 1]``.
    """
    if not 0.0 <= min_overlap <= 1.0:
        raise ValueError("owner_transitions: min_overlap must be between 0 and 1.")

    links = leagues.filter(pl.col("previous_league_id").is_not_null()).select(
        "league_id", "previous_league_id"
    )
    owners = standings.select(
        "league_id", "season", "roster_id", "owner_id", "display_name"
    )
    new_side = owners.join(links, on="league_id", how="inner").rename(
        {"owner_id": "new_owner_id", "display_name": "new_display_name"}
    )
    old_side = owners.select(
        pl.col("league_id").alias("previous_league_id"),
        "roster_id",
        pl.col("owner_id").alias("old_owner_id"),
        pl.col("display_name").alias("old_display_name"),
    )
    changed = new_side.join(
        old_side, on=["previous_league_id", "roster_id"], how="inner"
    ).filter(
        pl.col("old_owner_id").is_not_null()
        & pl.col("new_owner_id").is_not_null()
        & (pl.col("old_owner_id") != pl.col("new_owner_id"))
    )
    if changed.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    rosters = roster_players.group_by("league_id", "roster_id").agg(
        pl.col("player_id").unique().alias("players")
    )
    old_players = rosters.rename(
        {"league_id": "previous_league_id", "players": "old_players"}
    )
    new_players = rosters.rename({"players": "new_players"})

    return (
        changed.join(old_players, on=["previous_league_id", "roster_id"], how="left")
        .join(new_players, on=["league_id", "roster_id"], how="left")
        .with_columns(
            pl.col("old_players").list.len().alias("old_player_count"),
            pl.col("old_players")
            .list.set_intersection(pl.col("new_players"))
            .list.len()
            .alias("players_retained"),
            pl.col("season").alias("transition_season"),
        )
        .with_columns(
            pl.when(pl.col("old_player_count") > 0)
            .then(pl.col("players_retained") / pl.col("old_player_count"))
            .otherwise(None)
            .alias("player_overlap_fraction")
        )
        .with_columns(
            (pl.col("player_overlap_fraction") >= min_overlap)
            .fill_null(False)
            .alias("probable_handoff")
        )
        .sort("transition_season", "roster_id")
        .select(list(_SCHEMA.keys()))
    )
