"""Weekly top-3 and bottom-3 finishes per manager (issue #229).

How many weeks did each manager finish in the league's top 3 (good luck) or
bottom 3 (bad luck)? In a Chopped league a bottom-3 week that isn't the
lowest is a **close call**: a slightly worse score would have ended the
season. Built on :func:`nuclearff.chopped.survival.weekly_survival`, ranking
only rosters still alive each week.

**Minimum field size.** A week counts only with at least ``min_alive`` (7)
rosters alive, so the top 3 and bottom 3 can't overlap and at least one
roster sits between them. With 6 left every roster is in one group; with 3
left every roster is in both. For a 16-team season that keeps weeks 1-10.

**Ties** at 3rd, or at 3rd from last, count every tied roster.

Top-3 weeks reflect skill as much as luck; the labels follow the question
as asked (top 3 = good luck, bottom 3 = bad luck).
"""

from __future__ import annotations

import polars as pl

FINISHES_COLUMNS = (
    "owner_id",
    "manager",
    "weeks_counted",
    "top3_weeks",
    "bottom3_weeks",
    "close_calls",
    "top3_rate",
    "bottom3_rate",
)
"""Columns of :func:`weekly_finishes`'s output after its grouping keys."""

_N = 3


def weekly_finishes(
    survival: pl.DataFrame, *, career: bool = False, min_alive: int = 7
) -> pl.DataFrame:
    """Top-3 and bottom-3 weekly finishes per manager.

    Args:
        survival: :func:`~nuclearff.chopped.survival.weekly_survival`'s
            output.
        career: Combine every season per owner instead of one row per season.
        min_alive: Fewest rosters alive for a week to count (see the module
            docstring).

    Returns:
        Per season: ``league_id``, ``season``, then :data:`FINISHES_COLUMNS`;
        career: :data:`FINISHES_COLUMNS`. ``weeks_counted`` is the manager's
        weeks alive that met ``min_alive`` (the chop week included);
        ``close_calls`` are bottom-3 weeks the manager survived; the rates
        are per week counted, null with none counted. A manager with no
        counted week (chopped once fewer than ``min_alive`` remained, or not
        at all) still gets a row of zeros. Sorted by ``top3_weeks -
        bottom3_weeks``, best first.
    """
    keys = (
        ["owner_id", "manager"]
        if career
        else ["league_id", "season", "owner_id", "manager"]
    )
    rows = survival.filter(pl.col("owner_id").is_not_null())
    week = ["league_id", "week"]
    counted = rows.filter(pl.col("alive_count") >= min_alive).with_columns(
        # rank is 1 = highest with ties sharing the better rank; a separate
        # rank from the bottom makes a tie at 3rd-from-last count everyone.
        pl.col("points")
        .rank(method="min", descending=False)
        .over(week)
        .alias("_from_bottom")
    )
    bottom = pl.col("_from_bottom") <= _N
    stats = counted.group_by(keys).agg(
        pl.len().alias("weeks_counted"),
        (pl.col("rank") <= _N).sum().alias("top3_weeks"),
        bottom.sum().alias("bottom3_weeks"),
        (bottom & ~pl.col("chopped")).sum().alias("close_calls"),
    )
    finishes = (
        rows.select(keys)
        .unique()
        .join(stats, on=keys, how="left")
        .with_columns(
            pl.col("weeks_counted", "top3_weeks", "bottom3_weeks", "close_calls")
            .fill_null(0)
            .cast(pl.Int64)
        )
        .with_columns(
            pl.when(pl.col("weeks_counted") > 0)
            .then(pl.col(f"{name}_weeks") / pl.col("weeks_counted"))
            .alias(f"{name}_rate")
            for name in ("top3", "bottom3")
        )
        .with_columns((pl.col("top3_weeks") - pl.col("bottom3_weeks")).alias("_net"))
    )
    columns = list(FINISHES_COLUMNS) if career else [*keys[:2], *FINISHES_COLUMNS]
    return finishes.sort(
        ["_net", "top3_weeks", "manager"], descending=[True, True, False]
    ).select(columns)
