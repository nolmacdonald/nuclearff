"""Survival luck index per manager (issue #228).

Did a manager survive because they kept scoring well above the chop line, or
because someone else kept scoring even lower? Four measures, all built on
:func:`nuclearff.chopped.survival.weekly_survival` and computed over **weeks
survived** (alive and not chopped that week):

1. **Cumulative Luck Index**: the sum of weekly ``margin`` above the chop
   line. High means comfortable, skill-based survival; near zero means the
   manager kept hovering just above the lowest score. A raw sum grows with
   every week survived, so ``avg_margin`` (per week survived) is the fair
   comparison, and ``field_relative_margin`` (each week's margin minus that
   week's median survivor margin) can go negative: a thinner cushion than a
   typical survivor had.
2. **Nail-biter ratio**: weeks survived within ``nail_biter_pct`` (5%) of the
   chop line, over weeks survived. Higher = more survival luck.
3. **Razor-thin Z weeks**: weeks with ``0 < z_chop <= razor_z`` (0.3), and
   their share. Higher = more survival luck.
4. **Percentile-rank stability**: the coefficient of variation (sample
   standard deviation / mean) of weekly ``percentile``. High CV together
   with long survival is coincidence luck: weekly finishes swing widely but
   the worst weeks landed on weeks someone else scored even lower. The
   percentile, not the raw rank, since the field shrinks from 16 to 2.

**Late-week guard.** Measures 3 and 4 skip weeks with fewer than
``min_alive`` (5) rosters alive: with 2 alive, a survivor's ``z_chop`` is
always exactly 2.0 (population standard deviation), and a percentile over
3 rosters says little. :func:`guard_excluded_weeks` lists the weeks skipped.
Measures 1 and 2 use every week.
"""

from __future__ import annotations

import polars as pl

LUCK_COLUMNS = (
    "owner_id",
    "manager",
    "weeks_survived",
    "chopped_week",
    "cumulative_margin",
    "avg_margin",
    "field_relative_margin",
    "nail_biter_weeks",
    "nail_biter_ratio",
    "guarded_weeks",
    "razor_thin_weeks",
    "razor_thin_ratio",
    "mean_z",
    "percentile_cv",
)
"""Columns of :func:`survival_luck`'s output after its grouping keys."""


def survival_luck(
    survival: pl.DataFrame,
    *,
    career: bool = False,
    nail_biter_pct: float = 0.05,
    razor_z: float = 0.3,
    min_alive: int = 5,
    min_weeks: int = 4,
) -> pl.DataFrame:
    """The four survival luck measures, per manager.

    Args:
        survival: :func:`~nuclearff.chopped.survival.weekly_survival`'s
            output.
        career: Combine every season per owner instead of one row per
            season.
        nail_biter_pct: A week survived within this fraction of the chop
            line counts as a nail-biter.
        razor_z: A week survived with ``0 < z_chop <= razor_z`` counts as
            razor-thin.
        min_alive: Weeks with fewer rosters alive are skipped by the Z-score
            and percentile measures (see the module docstring).
        min_weeks: Fewest guarded weeks needed to report ``percentile_cv``;
            otherwise it is null.

    Returns:
        Per season: ``league_id``, ``season``, then :data:`LUCK_COLUMNS`.
        Career: :data:`LUCK_COLUMNS` only, with ``chopped_week`` from the
        latest season. Every manager who appears in ``survival`` gets a row,
        even one chopped in week 1 (``weeks_survived == 0``). Ratios are
        null when their denominator is 0. ``chopped_week`` is null for a
        manager never chopped. Sorted by ``avg_margin`` descending.
    """
    keys = (
        ["owner_id", "manager"]
        if career
        else ["league_id", "season", "owner_id", "manager"]
    )

    rows = survival.filter(pl.col("owner_id").is_not_null()).with_columns(
        (pl.col("alive_count") >= min_alive).alias("_guarded")
    )
    survived = rows.filter(~pl.col("chopped")).with_columns(
        (
            pl.col("margin") - pl.col("margin").median().over(["league_id", "week"])
        ).alias("_relative")
    )
    guarded = pl.col("_guarded")
    stats = survived.group_by(keys).agg(
        pl.len().alias("weeks_survived"),
        pl.col("margin").sum().alias("cumulative_margin"),
        pl.col("margin").mean().alias("avg_margin"),
        pl.col("_relative").sum().alias("field_relative_margin"),
        ((pl.col("margin_pct") > 0) & (pl.col("margin_pct") <= nail_biter_pct))
        .sum()
        .alias("nail_biter_weeks"),
        guarded.sum().alias("guarded_weeks"),
        (guarded & (pl.col("z_chop") > 0) & (pl.col("z_chop") <= razor_z))
        .sum()
        .alias("razor_thin_weeks"),
        pl.col("z_chop").filter(guarded).mean().alias("mean_z"),
        pl.col("percentile").filter(guarded).std(ddof=1).alias("_pct_sd"),
        pl.col("percentile").filter(guarded).mean().alias("_pct_mean"),
    )
    # The chop week per season (null if alive or the winner); for a career,
    # the owner's most recent season's, so a manager still alive this season
    # isn't shown with last season's chop.
    season_keys = ["league_id", "season", "owner_id", "manager"]
    chopped_week = (
        rows.group_by(season_keys)
        .agg(pl.col("week").filter(pl.col("chopped")).first().alias("chopped_week"))
        .sort("season")
        .group_by(keys)
        .agg(pl.col("chopped_week").last())
    )
    managers = rows.select(keys).unique()

    luck = (
        managers.join(stats, on=keys, how="left")
        .join(chopped_week, on=keys, how="left")
        .with_columns(
            pl.col(
                "weeks_survived",
                "nail_biter_weeks",
                "guarded_weeks",
                "razor_thin_weeks",
            )
            .fill_null(0)
            .cast(pl.Int64),
            pl.col("cumulative_margin", "field_relative_margin").fill_null(0.0),
        )
        .with_columns(
            _ratio("nail_biter_weeks", "weeks_survived").alias("nail_biter_ratio"),
            _ratio("razor_thin_weeks", "guarded_weeks").alias("razor_thin_ratio"),
            pl.when(pl.col("guarded_weeks") >= min_weeks)
            .then(pl.col("_pct_sd") / pl.col("_pct_mean"))
            .alias("percentile_cv"),
        )
    )
    columns = list(LUCK_COLUMNS) if career else [*keys[:2], *LUCK_COLUMNS]
    return luck.select(columns).sort(
        ["avg_margin", "manager"], descending=[True, False], nulls_last=True
    )


def _ratio(numerator: str, denominator: str) -> pl.Expr:
    """``numerator / denominator``, null when the denominator is 0."""
    return (
        pl.when(pl.col(denominator) > 0)
        .then(pl.col(numerator) / pl.col(denominator))
        .otherwise(None)
    )


def guard_excluded_weeks(survival: pl.DataFrame, *, min_alive: int = 5) -> pl.DataFrame:
    """The weeks the late-week guard leaves out of the Z-score and CV measures.

    Args:
        survival: :func:`~nuclearff.chopped.survival.weekly_survival`'s
            output.
        min_alive: The same threshold passed to :func:`survival_luck`.

    Returns:
        ``league_id``, ``season``, ``week``, ``alive_count``, one row per
        week with fewer than ``min_alive`` rosters alive.
    """
    return (
        survival.filter(pl.col("alive_count") < min_alive)
        .select("league_id", "season", "week", "alive_count")
        .unique()
        .sort(["season", "league_id", "week"])
    )
