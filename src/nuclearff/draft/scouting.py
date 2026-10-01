"""Pre-draft manager scouting report from historical draft tendencies (issue #107).

:func:`nuclearff.sleeper.draft.draft_order_stats` summarizes *where* each
manager picks in the order. :func:`manager_position_profile` summarizes *what*
each manager takes and *when*: per manager, per position, per round bucket, how
often that manager drafted that position in that round range across every
persisted season. It reads only the multi-season ``sleeper_draft_picks`` table,
so it can be built once at draft start with no new fetching; live in-draft
pattern detection (#100) can layer on top of it.

Round buckets are fixed: rounds 1-3 (``"1-3"``), 4-6 (``"4-6"``) and 7 onward
(``"7+"``). Keeper picks are counted like any other pick.

Manager identity is ``sleeper_standings.display_name`` resolved per
``(league_id, roster_id)``, the same posture as
:func:`nuclearff.sleeper.draft.draft_order_stats`. Each draft counts once as a
"season", so rows from several leagues in one database pool under a shared
username; callers wanting one league's tendencies pass only that league's
picks (e.g. via :func:`nuclearff.duckdb_io.read_table_for_league`).
"""

from __future__ import annotations

import polars as pl

SMALL_SAMPLE_SEASONS = 3
"""A manager with fewer drafted seasons than this is flagged ``small_sample``."""

_SCHEMA = {
    "manager": pl.String,
    "position": pl.String,
    "round_bucket": pl.String,
    "picks": pl.UInt32,
    "seasons_with_pick": pl.UInt32,
    "seasons_drafted": pl.UInt32,
    "season_rate": pl.Float64,
    "small_sample": pl.Boolean,
}

_BUCKET_ORDER = ["1-3", "4-6", "7+"]


def _round_bucket() -> pl.Expr:
    return (
        pl.when(pl.col("round") <= 3)
        .then(pl.lit("1-3"))
        .when(pl.col("round") <= 6)
        .then(pl.lit("4-6"))
        .otherwise(pl.lit("7+"))
    )


def manager_position_profile(
    draft_picks: pl.DataFrame, standings: pl.DataFrame
) -> pl.DataFrame:
    """Per-manager positional draft tendency by round bucket.

    Args:
        draft_picks: ``sleeper_draft_picks`` rows -- needs ``league_id``,
            ``draft_id``, ``round``, ``roster_id``, ``position``.
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        One row per ``(manager, position, round_bucket)`` the manager has ever
        drafted, ordered by manager, position, then bucket: ``picks`` (total
        picks of that position in that bucket), ``seasons_with_pick`` (drafts in
        which at least one such pick was made), ``seasons_drafted`` (the
        manager's drafts, counted over every pick regardless of position),
        ``season_rate`` (``seasons_with_pick / seasons_drafted``, so 5 of 6
        drafts is ``0.833``) and ``small_sample`` (``True`` when
        ``seasons_drafted`` is below :data:`SMALL_SAMPLE_SEASONS`). A
        combination never drafted has no row, i.e. a rate of zero. Picks with
        no ``position`` or ``round`` add no row, and a roster with no
        ``display_name`` is omitted.
    """
    if draft_picks.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    names = standings.select("league_id", "roster_id", "display_name")
    named = (
        draft_picks.select("league_id", "draft_id", "round", "roster_id", "position")
        .join(names, on=["league_id", "roster_id"], how="inner")
        .filter(pl.col("display_name").is_not_null())
        .rename({"display_name": "manager"})
    )

    seasons = named.group_by("manager").agg(
        pl.struct("league_id", "draft_id").n_unique().alias("seasons_drafted")
    )

    profile = (
        named.filter(pl.col("position").is_not_null() & pl.col("round").is_not_null())
        .with_columns(_round_bucket().alias("round_bucket"))
        .group_by("manager", "position", "round_bucket")
        .agg(
            pl.len().alias("picks"),
            pl.struct("league_id", "draft_id").n_unique().alias("seasons_with_pick"),
        )
        .join(seasons, on="manager", how="left")
        .with_columns(
            (pl.col("seasons_with_pick") / pl.col("seasons_drafted")).alias(
                "season_rate"
            ),
            (pl.col("seasons_drafted") < SMALL_SAMPLE_SEASONS).alias("small_sample"),
            pl.col("round_bucket")
            .replace_strict(
                {bucket: index for index, bucket in enumerate(_BUCKET_ORDER)},
                return_dtype=pl.Int8,
            )
            .alias("_bucket_index"),
        )
        .sort("manager", "position", "_bucket_index")
    )
    return profile.select(list(_SCHEMA.keys()))
