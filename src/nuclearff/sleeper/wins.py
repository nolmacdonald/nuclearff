"""Cumulative wins over time: data prep from persisted matchups and standings.

Turns ``sleeper_matchups`` (``nuclearff sleeper fetch-league --matchups``,
issue #20) and ``sleeper_standings`` (``--standings``, issue #21) into a
per-manager, per-game cumulative win count spanning the league's full
multi-season history (GitHub Issue 79). No new Sleeper fetching happens
here -- this module only reads what's already persisted.

**Real gap this module fills:** neither table has a per-week result.
``sleeper_matchups`` stores raw ``points`` only; ``sleeper_standings`` has
only the season-end ``wins``/``losses``/``ties`` aggregate. Per-week outcome
is derived by :func:`weekly_results`, comparing ``points`` between the two
rosters sharing a ``(league_id, week, matchup_id)`` group. A bye week (a
lone roster, or a ``null`` ``matchup_id`` -- both real per
:mod:`nuclearff.sleeper.matchups`'s own module docstring) or any group that
isn't exactly two rosters is skipped, not guessed at.

:func:`paired_weekly_matchups` does the same pairing but keeps the raw
points/margin instead of collapsing straight to win/loss/tie -- the
league-history archive epic (#116) needs that richer shape for score/margin
records (#109) and head-to-head rivalries (#110), so :func:`weekly_results`
is now built on top of it rather than duplicating the join.

Manager identity is ``sleeper_standings.display_name``, joined by
``(league_id, roster_id)`` -- the same "display name is the cross-season
identity, not a stable id" posture :mod:`nuclearff.sleeper.trades` already
documents and accepts: true so far for this league, not guaranteed by
Sleeper.

Cumulative wins increments only on an outright win. A tie neither adds nor
subtracts, matching Sleeper's own separate ``ties`` counter, which already
treats a tie as distinct from a win -- this module does not invent a
half-win convention Sleeper itself doesn't use.
"""

from __future__ import annotations

import polars as pl

_PAIRED_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "roster_id": pl.Int64,
    "points": pl.Float64,
    "opponent_roster_id": pl.Int64,
    "opponent_points": pl.Float64,
    "margin": pl.Float64,
}

_RESULTS_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "roster_id": pl.Int64,
    "result": pl.String,
}

_CUMULATIVE_SCHEMA = {
    "manager": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "game_number": pl.UInt32,
    "cumulative_wins": pl.UInt32,
}


def paired_weekly_matchups(matchups: pl.DataFrame) -> pl.DataFrame:
    """Join each roster's weekly points against its real opponent's.

    The shared pairing step behind :func:`weekly_results` and every
    league-history archive feature that needs more than win/loss/tie out of
    a matchup (#109's score/margin records, #110's head-to-head rivalries)
    — factored out so none of them re-derive this join independently.

    Args:
        matchups: ``sleeper_matchups`` rows -- ``league_id``, ``season``,
            ``week``, ``roster_id``, ``matchup_id``, ``points``.

    Returns:
        One row per (roster, week) that had a real two-roster matchup:
        ``league_id``, ``season``, ``week``, ``roster_id``, ``points``,
        ``opponent_roster_id``, ``opponent_points``, ``margin`` (``points -
        opponent_points`` -- positive is a win, negative a loss, zero a
        tie). A bye week, or any ``(league_id, week, matchup_id)`` group
        that isn't exactly two rosters, contributes no rows -- same
        exclusion :func:`weekly_results` has always applied.
    """
    if matchups.height == 0:
        return pl.DataFrame(schema=_PAIRED_SCHEMA)

    group_keys = ["league_id", "week", "matchup_id"]
    paired_keys = (
        matchups.filter(pl.col("matchup_id").is_not_null())
        .group_by(group_keys)
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") == 2)
        .drop("_n")
    )
    paired = matchups.join(paired_keys, on=group_keys, how="inner")

    opponents = paired.select(
        *group_keys,
        pl.col("roster_id").alias("opponent_roster_id"),
        pl.col("points").alias("opponent_points"),
    )
    matched = paired.join(opponents, on=group_keys, how="inner").filter(
        pl.col("roster_id") != pl.col("opponent_roster_id")
    )

    return matched.select(
        "league_id",
        "season",
        "week",
        "roster_id",
        "points",
        "opponent_roster_id",
        "opponent_points",
        (pl.col("points") - pl.col("opponent_points")).alias("margin"),
    )


def weekly_results(matchups: pl.DataFrame) -> pl.DataFrame:
    """Derive each roster's per-week win/loss/tie from raw matchup points.

    Args:
        matchups: ``sleeper_matchups`` rows, e.g.
            :func:`nuclearff.duckdb_io.read_table`'s output for that table --
            ``league_id``, ``season``, ``week``, ``roster_id``,
            ``matchup_id``, ``points``.

    Returns:
        One row per (roster, week) that had a real two-roster matchup:
        ``league_id``, ``season``, ``week``, ``roster_id``, ``result``
        (``"win"``, ``"loss"``, or ``"tie"``). A bye week, or any
        ``(league_id, week, matchup_id)`` group that isn't exactly two
        rosters, contributes no rows.
    """
    paired = paired_weekly_matchups(matchups)
    if paired.height == 0:
        return pl.DataFrame(schema=_RESULTS_SCHEMA)

    return paired.select(
        "league_id",
        "season",
        "week",
        "roster_id",
        result=pl.when(pl.col("margin") > 0)
        .then(pl.lit("win"))
        .when(pl.col("margin") < 0)
        .then(pl.lit("loss"))
        .otherwise(pl.lit("tie")),
    )


def cumulative_wins(results: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """Per-manager running win total, in each manager's own chronological order.

    Args:
        results: Output of :func:`weekly_results`.
        standings: ``sleeper_standings`` rows -- needs ``league_id``,
            ``roster_id``, ``display_name``.

    Returns:
        One row per (manager, game), sorted by ``season``/``week`` within
        each manager: ``manager``, ``season``, ``week``, ``game_number``
        (1, 2, 3, ... that manager's *own* real game count -- not a
        league-wide week index, so a manager who joined partway through the
        league's history still starts at game 1), and ``cumulative_wins``
        (monotonically non-decreasing by construction). A roster with no
        resolvable ``display_name`` contributes no rows.
    """
    if results.height == 0:
        return pl.DataFrame(schema=_CUMULATIVE_SCHEMA)

    names = standings.select("league_id", "roster_id", "display_name")
    joined = (
        results.join(names, on=["league_id", "roster_id"], how="inner")
        .filter(pl.col("display_name").is_not_null())
        .rename({"display_name": "manager"})
        .sort(["manager", "season", "week"])
    )

    return joined.with_columns(
        pl.col("week").cum_count().over("manager").alias("game_number"),
        (pl.col("result") == "win")
        .cast(pl.UInt32)
        .cum_sum()
        .over("manager")
        .alias("cumulative_wins"),
    ).select("manager", "season", "week", "game_number", "cumulative_wins")
