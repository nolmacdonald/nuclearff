"""Weekly survival table: chop line, margin, Z-score and percentile (issue #226).

The shared weekly table the luck index (#228) and the top-3/bottom-3 counts
(#229) build on, the same role :mod:`nuclearff.sleeper.trades` played for
the trade network epic.

**The chop line** is the lowest score among rosters still alive that week:
the score of the roster that got chopped. Every roster's ``margin`` is its
distance above that line.

**Only rosters still alive count.** Sleeper keeps a matchup row for every
chopped roster in every later week, at ``0.0`` points, so a plain
``min(points)`` would put the chop line at 0. Alive is read from
``sleeper_chopped_rosters.eliminated_leg`` (:func:`~nuclearff.chopped.
common.alive_in_week`).

**Only weeks whose chop has been processed count**: weeks 1 through the
league's ``last_chopped_leg``. A completed season's matchups run past the
final chop (weeks 16-18 of the real 2025 season), where chopped rosters
score again; an in-progress week's scores aren't final.

Every roster has its own ``matchup_id`` in a Chopped league, so
:func:`nuclearff.sleeper.wins.paired_weekly_matchups` doesn't apply; this
reads ``sleeper_matchups`` directly.
"""

from __future__ import annotations

import logging

import polars as pl

from nuclearff.chopped.common import (
    alive_in_week,
    eliminations,
    require_chopped_leagues,
    roster_owners,
)
from nuclearff.exceptions import ChoppedLeagueError

logger = logging.getLogger(__name__)

SURVIVAL_COLUMNS = (
    "league_id",
    "season",
    "week",
    "roster_id",
    "owner_id",
    "manager",
    "points",
    "alive_count",
    "chop_line",
    "chopped",
    "rank",
    "percentile",
    "margin",
    "margin_pct",
    "week_mean",
    "week_sd",
    "z_chop",
    "gap_to_safety",
)
"""Columns of :func:`weekly_survival`'s output, in order."""


def weekly_survival(
    matchups: pl.DataFrame,
    chopped_rosters: pl.DataFrame,
    leagues: pl.DataFrame,
    standings: pl.DataFrame,
    *,
    strict: bool = True,
) -> pl.DataFrame:
    """One row per alive roster per processed week, for every Chopped league.

    Args:
        matchups: ``sleeper_matchups`` rows -- ``league_id``, ``season``,
            ``week``, ``roster_id``, ``points``.
        chopped_rosters: ``sleeper_chopped_rosters`` rows.
        leagues: ``sleeper_leagues`` rows (for Chopped detection and
            ``last_chopped_leg``).
        standings: ``sleeper_standings`` rows (for owner ids and names).
        strict: Raise if a week's chopped roster wasn't its lowest scorer,
            or two alive rosters tied for lowest (see
            :func:`chop_line_problems`). ``False`` logs a warning instead.

    Returns:
        Columns :data:`SURVIVAL_COLUMNS`:

        - ``alive_count``: rosters alive that week.
        - ``chop_line``: lowest ``points`` among them.
        - ``chopped``: this roster was eliminated this week.
        - ``rank`` (1 = highest score, ties share the best rank) and
          ``percentile`` = ``1 - (rank - 1) / alive_count`` (1.0 = top,
          never 0).
        - ``margin`` = ``points - chop_line`` (0 for the chopped roster) and
          ``margin_pct`` = ``margin / chop_line``.
        - ``week_mean``/``week_sd``: mean and **population** standard
          deviation of the alive rosters' points.
        - ``z_chop`` = ``margin / week_sd``, the distance above the chop line
          in units of that week's spread (null when ``week_sd`` is 0).
        - ``gap_to_safety``: for the chopped roster only, how far below the
          second-lowest score it finished; null for everyone else.

        Sorted by season, week, then ``rank``.

    Raises:
        ChoppedLeagueError: If there is no Chopped league, a Chopped league
            has no ``sleeper_chopped_rosters`` rows, or (with ``strict``) a
            week fails :func:`chop_line_problems`.
    """
    league_info = require_chopped_leagues(leagues)
    league_ids = league_info["league_id"].to_list()
    eliminated = eliminations(chopped_rosters, league_ids)

    weeks = (
        matchups.select(
            pl.col("league_id").cast(pl.String),
            pl.col("week").cast(pl.Int64),
            pl.col("roster_id").cast(pl.Int64),
            pl.col("points").cast(pl.Float64),
        )
        .join(
            league_info.select("league_id", "season", "last_chopped_leg"),
            on="league_id",
        )
        .filter((pl.col("week") >= 1) & (pl.col("week") <= pl.col("last_chopped_leg")))
        .join(eliminated, on=["league_id", "roster_id"], how="left")
        .filter(alive_in_week(pl.col("eliminated_leg"), pl.col("week")))
    )

    group = ["league_id", "week"]
    second_lowest = weeks.group_by(group).agg(
        pl.col("points").sort().slice(1, 1).first().alias("second_lowest")
    )
    frame = (
        weeks.join(second_lowest, on=group, how="left")
        .with_columns(
            pl.len().over(group).alias("alive_count"),
            pl.col("points").min().over(group).alias("chop_line"),
            (pl.col("eliminated_leg") == pl.col("week"))
            .fill_null(False)
            .alias("chopped"),
            pl.col("points")
            .rank(method="min", descending=True)
            .over(group)
            .cast(pl.Int64)
            .alias("rank"),
            pl.col("points").mean().over(group).alias("week_mean"),
            pl.col("points").std(ddof=0).over(group).alias("week_sd"),
        )
        .with_columns(
            (1 - (pl.col("rank") - 1) / pl.col("alive_count")).alias("percentile"),
            (pl.col("points") - pl.col("chop_line")).alias("margin"),
        )
        .with_columns(
            pl.when(pl.col("chop_line") > 0)
            .then(pl.col("margin") / pl.col("chop_line"))
            .alias("margin_pct"),
            pl.when(pl.col("week_sd") > 0)
            .then(pl.col("margin") / pl.col("week_sd"))
            .alias("z_chop"),
            pl.when(pl.col("chopped"))
            .then(pl.col("second_lowest") - pl.col("points"))
            .alias("gap_to_safety"),
            pl.col("alive_count").cast(pl.Int64),
        )
        .join(roster_owners(standings), on=["league_id", "roster_id"], how="left")
        .select(SURVIVAL_COLUMNS)
        .sort(["season", "league_id", "week", "rank", "roster_id"])
    )

    problems = chop_line_problems(frame)
    if problems.height:
        message = "; ".join(
            f"{row['league_id']} week {row['week']}: {row['problem']}"
            for row in problems.iter_rows(named=True)
        )
        if strict:
            raise ChoppedLeagueError(f"Chop line doesn't match Sleeper: {message}")
        logger.warning("Chop line doesn't match Sleeper: %s", message)
    return frame


def chop_line_problems(survival: pl.DataFrame) -> pl.DataFrame:
    """Weeks where the computed chop line disagrees with Sleeper's chop.

    Each processed week should have exactly one chopped roster, and it
    should be the one alive roster with the lowest score.

    Args:
        survival: :func:`weekly_survival`'s output.

    Returns:
        ``league_id``, ``season``, ``week``, ``problem``, one row per
        problem found; empty when every week checks out. Problems:
        no chopped roster, more than one chopped roster, the chopped roster
        not the lowest scorer, or several alive rosters tied for lowest
        (Sleeper's tiebreak isn't known, so a tie is flagged rather than
        trusted).
    """
    weekly = survival.group_by(["league_id", "season", "week"]).agg(
        pl.col("chopped").sum().alias("n_chopped"),
        (pl.col("points") == pl.col("chop_line")).sum().alias("n_at_line"),
        pl.col("margin").filter(pl.col("chopped")).max().alias("chopped_margin"),
    )
    problems = pl.concat(
        [
            weekly.filter(pl.col("n_chopped") == 0).with_columns(
                problem=pl.lit("no roster was chopped")
            ),
            weekly.filter(pl.col("n_chopped") > 1).with_columns(
                problem=pl.lit("more than one roster was chopped")
            ),
            weekly.filter(pl.col("chopped_margin") > 0).with_columns(
                problem=pl.lit("the chopped roster wasn't the lowest scorer")
            ),
            weekly.filter(pl.col("n_at_line") > 1).with_columns(
                problem=pl.lit("alive rosters tied for the lowest score")
            ),
        ]
    )
    return problems.select("league_id", "season", "week", "problem").sort(
        ["season", "league_id", "week"]
    )
