"""League history archive: manager franchise pages (issue #112, epic #116).

Pure aggregation of already-existing per-manager outputs — no new
per-manager statistic is computed here, only assembled:
``sleeper_standings`` (career record, seasons played), this epic's own
:func:`nuclearff.archive.champions.championship_history` (#108) and
records-book functions (#109), :func:`nuclearff.sleeper.trades
.manager_trade_counts` (#41), and :func:`nuclearff.sleeper.draft
.draft_order_stats` (#85).

Manager identity is ``sleeper_standings.display_name``, the same
cross-season-identity posture every joined input already accepts.
"""

from __future__ import annotations

import polars as pl

_SCHEMA = {
    "manager": pl.String,
    "seasons_played": pl.UInt32,
    "career_wins": pl.Int64,
    "career_losses": pl.Int64,
    "career_ties": pl.Int64,
    "championships": pl.UInt32,
    "runner_ups": pl.UInt32,
    "all_time_trades": pl.UInt32,
    "avg_draft_position": pl.Float64,
    "times_first_pick": pl.UInt32,
    "times_last_pick": pl.UInt32,
    "records_held": pl.List(pl.String),
}

_HOLDERS_SCHEMA = {"manager": pl.String, "records_held": pl.List(pl.String)}


def _record_holders(
    score_extremes: pl.DataFrame,
    margin_extremes: pl.DataFrame,
    win_streaks: pl.DataFrame,
    loss_streaks: pl.DataFrame,
) -> pl.DataFrame:
    """Flatten every records-book output down to (manager, record name) rows.

    Each of #109's functions produces its own record book independently —
    this is the only place that reduces them to "who holds what," so a
    franchise page can list a manager's records without re-deriving any of
    #109's logic.
    """
    holders: list[dict[str, str]] = []

    def _take(df: pl.DataFrame, manager_col: str, record_name: str) -> None:
        if df.height == 0:
            return
        manager = df.row(0, named=True)[manager_col]
        if manager is not None:
            holders.append({"manager": manager, "record": record_name})

    _take(
        score_extremes.filter(pl.col("kind") == "highest"),
        "manager",
        "highest_single_week_score",
    )
    _take(
        score_extremes.filter(pl.col("kind") == "lowest"),
        "manager",
        "lowest_single_week_score",
    )
    _take(
        margin_extremes.filter(pl.col("kind") == "biggest_blowout"),
        "winner",
        "biggest_blowout",
    )
    _take(
        margin_extremes.filter(pl.col("kind") == "closest_margin"),
        "winner",
        "closest_margin_win",
    )
    _take(
        win_streaks.sort("length", descending=True).head(1),
        "manager",
        "longest_win_streak",
    )
    _take(
        loss_streaks.sort("length", descending=True).head(1),
        "manager",
        "longest_loss_streak",
    )

    if not holders:
        return pl.DataFrame(schema=_HOLDERS_SCHEMA)

    flat = pl.DataFrame(holders, schema={"manager": pl.String, "record": pl.String})
    return flat.group_by("manager").agg(pl.col("record").alias("records_held"))


def franchise_profile(
    standings: pl.DataFrame,
    championships: pl.DataFrame,
    trade_counts: pl.DataFrame,
    draft_stats: pl.DataFrame,
    score_extremes: pl.DataFrame,
    margin_extremes: pl.DataFrame,
    win_streaks: pl.DataFrame,
    loss_streaks: pl.DataFrame,
) -> pl.DataFrame:
    """One row per manager: career record, championships, trades, draft tendencies,
    and records held.

    Args:
        standings: ``sleeper_standings`` rows.
        championships: :func:`nuclearff.archive.champions.championship_history`
            output.
        trade_counts: :func:`nuclearff.sleeper.trades.manager_trade_counts`
            output.
        draft_stats: :func:`nuclearff.sleeper.draft.draft_order_stats` output.
        score_extremes: :func:`nuclearff.archive.records.score_extremes`
            output.
        margin_extremes: :func:`nuclearff.archive.records.margin_extremes`
            output.
        win_streaks: :func:`nuclearff.archive.records.longest_streak` output
            called with ``streak_type="win"``.
        loss_streaks: Same, called with ``streak_type="loss"``.

    Returns:
        One row per manager with a ``sleeper_standings`` row:
        ``seasons_played``, ``career_wins``/``career_losses``/
        ``career_ties`` (summed across every season), ``championships``/
        ``runner_ups`` (``0`` if never resolved, not missing),
        ``all_time_trades`` (``0`` if the manager never traded — same
        density caveat :func:`nuclearff.sleeper.trades.manager_trade_counts`
        already documents), ``avg_draft_position``/``times_first_pick``/
        ``times_last_pick`` (left ``NULL`` for a manager absent from
        ``draft_stats``, since ``0`` would misrepresent a real average),
        ``records_held`` (list of record names, empty list — not ``NULL`` —
        for a manager who holds none).
    """
    base = (
        standings.filter(pl.col("display_name").is_not_null())
        .group_by("display_name")
        .agg(
            pl.col("league_id").n_unique().cast(pl.UInt32).alias("seasons_played"),
            pl.col("wins").sum().alias("career_wins"),
            pl.col("losses").sum().alias("career_losses"),
            pl.col("ties").sum().alias("career_ties"),
        )
        .rename({"display_name": "manager"})
    )
    if base.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    champion_counts = (
        championships.filter(pl.col("champion_display_name").is_not_null())
        .group_by("champion_display_name")
        .agg(pl.len().cast(pl.UInt32).alias("championships"))
        .rename({"champion_display_name": "manager"})
    )
    runner_up_counts = (
        championships.filter(pl.col("runner_up_display_name").is_not_null())
        .group_by("runner_up_display_name")
        .agg(pl.len().cast(pl.UInt32).alias("runner_ups"))
        .rename({"runner_up_display_name": "manager"})
    )
    trades = trade_counts.select("manager", pl.col("trades").alias("all_time_trades"))
    draft = draft_stats.select(
        "manager", "avg_draft_position", "times_first_pick", "times_last_pick"
    )
    records = _record_holders(
        score_extremes, margin_extremes, win_streaks, loss_streaks
    )

    profile = (
        base.join(champion_counts, on="manager", how="left")
        .join(runner_up_counts, on="manager", how="left")
        .join(trades, on="manager", how="left")
        .join(draft, on="manager", how="left")
        .join(records, on="manager", how="left")
        .with_columns(
            pl.col("championships").fill_null(0),
            pl.col("runner_ups").fill_null(0),
            pl.col("all_time_trades").fill_null(0),
            pl.col("records_held").fill_null([]),
        )
        .sort("manager")
    )
    return profile.select(list(_SCHEMA.keys()))
