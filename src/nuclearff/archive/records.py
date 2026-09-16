"""League history archive: the records book (issue #109, epic #116).

``sleeper_matchups`` (issue #20) already has every real per-roster,
per-week point total ever fetched, and
:func:`nuclearff.sleeper.wins.paired_weekly_matchups`/:func:`~nuclearff.sleeper
.wins.weekly_results` already do the pairing this module needs — no new
Sleeper fetching, no new join logic duplicated here.

Manager identity is ``sleeper_standings.display_name``, the same
cross-season-identity posture every other multi-season aggregate in this
project accepts (:mod:`nuclearff.sleeper.trades`, :mod:`nuclearff.archive
.champions`): true so far for this league, not guaranteed stable by Sleeper.
"""

from __future__ import annotations

import polars as pl

from nuclearff.sleeper.wins import paired_weekly_matchups

_SCORE_SCHEMA = {
    "kind": pl.String,
    "manager": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "points": pl.Float64,
    "opponent": pl.String,
}

_MARGIN_SCHEMA = {
    "kind": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "winner": pl.String,
    "winner_points": pl.Float64,
    "loser": pl.String,
    "loser_points": pl.Float64,
    "margin": pl.Float64,
}

_STREAK_SCHEMA = {
    "manager": pl.String,
    "streak_type": pl.String,
    "length": pl.UInt32,
    "start_season": pl.Int64,
    "start_week": pl.Int64,
    "end_season": pl.Int64,
    "end_week": pl.Int64,
}


def _names(standings: pl.DataFrame) -> pl.DataFrame:
    return standings.select("league_id", "roster_id", "display_name")


def score_extremes(matchups: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """The single highest- and lowest-scoring real weeks in league history.

    Includes bye weeks (a real score, just no opponent to report) — a
    single-week score record is about the points, not the matchup outcome.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        Two rows (``kind == "highest"``/``"lowest"``): ``manager``,
        ``season``, ``week``, ``points``, ``opponent`` (``None`` for a bye
        week). Empty if ``matchups`` has no rows with a resolvable manager.
    """
    if matchups.height == 0:
        return pl.DataFrame(schema=_SCORE_SCHEMA)

    names = _names(standings)
    opponents = paired_weekly_matchups(matchups).select(
        "league_id", "season", "week", "roster_id", "opponent_roster_id"
    )

    joined = (
        matchups.filter(pl.col("points").is_not_null())
        .join(names, on=["league_id", "roster_id"], how="inner")
        .filter(pl.col("display_name").is_not_null())
        .rename({"display_name": "manager"})
        .join(opponents, on=["league_id", "season", "week", "roster_id"], how="left")
        .join(
            names.rename(
                {"roster_id": "opponent_roster_id", "display_name": "opponent"}
            ),
            on=["league_id", "opponent_roster_id"],
            how="left",
        )
    )
    if joined.height == 0:
        return pl.DataFrame(schema=_SCORE_SCHEMA)

    highest = (
        joined.sort("points", descending=True)
        .head(1)
        .with_columns(kind=pl.lit("highest"))
    )
    lowest = joined.sort("points").head(1).with_columns(kind=pl.lit("lowest"))

    return pl.concat([highest, lowest]).select(list(_SCORE_SCHEMA.keys()))


def margin_extremes(matchups: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """The biggest blowout and the closest ("nail-biter") margin in league history.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        Two rows (``kind == "biggest_blowout"``/``"closest_margin"``):
        ``season``, ``week``, ``winner``/``winner_points``,
        ``loser``/``loser_points``, ``margin`` (always positive). A tied
        matchup (``margin == 0``) never qualifies as a "closest margin" win —
        it has no winner or loser to report. Real ties are recoverable from
        :func:`nuclearff.sleeper.wins.weekly_results` if wanted separately.
    """
    paired = paired_weekly_matchups(matchups)
    # `paired` has one row per side of every matchup (a win at +margin and
    # its mirrored loss at -margin) -- keeping only margin > 0 picks the
    # winner's row once per real matchup instead of double-counting it.
    decisive = paired.filter(pl.col("margin") > 0)
    if decisive.height == 0:
        return pl.DataFrame(schema=_MARGIN_SCHEMA)

    names = _names(standings)
    joined = (
        decisive.join(names, on=["league_id", "roster_id"], how="inner")
        .rename({"display_name": "winner", "points": "winner_points"})
        .join(
            names.rename({"roster_id": "opponent_roster_id", "display_name": "loser"}),
            on=["league_id", "opponent_roster_id"],
            how="inner",
        )
        .rename({"opponent_points": "loser_points"})
        .filter(pl.col("winner").is_not_null() & pl.col("loser").is_not_null())
    )
    if joined.height == 0:
        return pl.DataFrame(schema=_MARGIN_SCHEMA)

    blowout = (
        joined.sort("margin", descending=True)
        .head(1)
        .with_columns(kind=pl.lit("biggest_blowout"))
    )
    nailbiter = (
        joined.sort("margin").head(1).with_columns(kind=pl.lit("closest_margin"))
    )

    return pl.concat([blowout, nailbiter]).select(list(_MARGIN_SCHEMA.keys()))


def longest_streak(
    results: pl.DataFrame, standings: pl.DataFrame, streak_type: str
) -> pl.DataFrame:
    """Each manager's longest consecutive win or loss streak.

    **A tie breaks a streak.** It's neither a win nor a loss, so it can't
    extend a "consecutive wins" or "consecutive losses" count — the next
    streak starts fresh on the following game. A stated choice, not an
    implicit default (per issue #109's own acceptance criteria).

    Args:
        results: Output of :func:`nuclearff.sleeper.wins.weekly_results`.
        standings: ``sleeper_standings`` rows, for ``display_name``.
        streak_type: ``"win"`` or ``"loss"``.

    Returns:
        One row per manager who has at least one streak of that type,
        longest first: ``manager``, ``streak_type``, ``length``,
        ``start_season``/``start_week``, ``end_season``/``end_week``. A tie
        for a manager's own longest streak keeps the chronologically
        earliest one (deterministic, not an arbitrary pick).

    Raises:
        ValueError: If ``streak_type`` isn't ``"win"`` or ``"loss"``.
    """
    if streak_type not in {"win", "loss"}:
        raise ValueError(f"streak_type must be 'win' or 'loss', got {streak_type!r}")

    names = _names(standings)
    joined = (
        results.join(names, on=["league_id", "roster_id"], how="inner")
        .filter(pl.col("display_name").is_not_null())
        .rename({"display_name": "manager"})
        .sort(["manager", "season", "week"])
    )
    if joined.height == 0:
        return pl.DataFrame(schema=_STREAK_SCHEMA)

    joined = joined.with_columns(
        (pl.col("result") != pl.col("result").shift(1).over("manager"))
        .fill_null(True)
        .cast(pl.UInt32)
        .cum_sum()
        .over("manager")
        .alias("_run_id")
    )

    runs = (
        joined.filter(pl.col("result") == streak_type)
        .group_by(["manager", "_run_id"], maintain_order=True)
        .agg(
            pl.len().alias("length"),
            pl.col("season").first().alias("start_season"),
            pl.col("week").first().alias("start_week"),
            pl.col("season").last().alias("end_season"),
            pl.col("week").last().alias("end_week"),
        )
    )
    if runs.height == 0:
        return pl.DataFrame(schema=_STREAK_SCHEMA)

    longest = (
        runs.sort(["manager", "length"], descending=[False, True])
        .group_by("manager", maintain_order=True)
        .first()
        .with_columns(streak_type=pl.lit(streak_type))
    )
    return longest.select(list(_STREAK_SCHEMA.keys())).sort("length", descending=True)
