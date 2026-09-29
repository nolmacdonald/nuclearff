"""FAAB remaining per team after each week (issue #221).

In a Chopped league every week's chopped roster hits waivers, so the FAAB
each surviving team has left decides who can win those players. This
rebuilds each roster's balance week by week from ``sleeper_transactions``:

- **Starting budget** is the league's own ``settings.waiver_budget``
  (:func:`~nuclearff.chopped.common.chopped_leagues`), never assumed. The
  real league's is $1,000, not the "$10" in its name.
- **Spending** is every winning waiver claim's ``settings.waiver_bid``
  (``type == "waiver"``, ``status == "complete"``), counted in full even for
  a claim that adds more than one player: the whole bid leaves the balance.
  Failed claims and free-agent adds cost nothing.
- **FAAB traded** between teams comes from each completed trade's
  ``waiver_budget`` list (``amount``, ``sender``, ``receiver`` roster ids).
- **Grouped by week** (Sleeper runs waivers in a weekly batch), carrying each
  balance forward through weeks with no activity.
- **A chopped roster's rows stop at its elimination week**; that row's
  ``remaining`` is the FAAB it had when chopped.

:func:`faab_check` compares the rebuilt total against Sleeper's own
``waiver_budget_used``, so a mismatch is reported rather than hidden.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from nuclearff.chopped.common import (
    eliminations,
    require_chopped_leagues,
    roster_owners,
)

FAAB_COLUMNS = (
    "league_id",
    "season",
    "week",
    "roster_id",
    "owner_id",
    "manager",
    "spent_this_week",
    "received_via_trade",
    "sent_via_trade",
    "remaining",
    "chopped",
)
"""Columns of :func:`faab_by_week`'s output."""


def _json(value: Any, default: Any) -> Any:
    """Decode a JSON text column value, or return ``default``."""
    if isinstance(value, str) and value:
        return json.loads(value)
    return default if value is None else value


def _movements(transactions: pl.DataFrame, league_ids: list[str]) -> pl.DataFrame:
    """One row per FAAB movement: a winning bid, or one side of a trade."""
    moves: list[dict[str, Any]] = []
    relevant = transactions.filter(
        pl.col("league_id").cast(pl.String).is_in(league_ids)
        & (pl.col("status") == "complete")
        & pl.col("type").is_in(["waiver", "trade"])
    )
    for row in relevant.iter_rows(named=True):
        base = {"league_id": str(row["league_id"]), "week": row["week"]}
        if row["type"] == "waiver":
            bid = _json(row["settings"], {}).get("waiver_bid")
            rosters = _json(row["roster_ids"], [])
            if isinstance(bid, int) and bid and rosters:
                moves.append({**base, "roster_id": rosters[0], "spent": bid})
            continue
        for transfer in _json(row["waiver_budget"], []) or []:
            amount = transfer.get("amount")
            if not isinstance(amount, int) or not amount:
                continue
            moves.append({**base, "roster_id": transfer["sender"], "sent": amount})
            moves.append(
                {**base, "roster_id": transfer["receiver"], "received": amount}
            )
    schema = {
        "league_id": pl.String,
        "week": pl.Int64,
        "roster_id": pl.Int64,
        "spent": pl.Int64,
        "sent": pl.Int64,
        "received": pl.Int64,
    }
    return pl.DataFrame(moves, schema=schema).fill_null(0)


def faab_by_week(
    transactions: pl.DataFrame,
    leagues: pl.DataFrame,
    chopped_rosters: pl.DataFrame,
    standings: pl.DataFrame,
) -> pl.DataFrame:
    """Each roster's FAAB after every week, for every Chopped league.

    Args:
        transactions: ``sleeper_transactions`` rows -- ``league_id``,
            ``week``, ``type``, ``status``, ``roster_ids``, ``settings`` and
            ``waiver_budget`` (JSON text).
        leagues: ``sleeper_leagues`` rows (budget, ``last_chopped_leg``).
        chopped_rosters: ``sleeper_chopped_rosters`` rows (elimination legs).
        standings: ``sleeper_standings`` rows (owner ids and names).

    Returns:
        Columns :data:`FAAB_COLUMNS`, one row per roster per week from week 1
        to the later of ``last_chopped_leg`` and the last week with FAAB
        activity, stopping at the roster's elimination week. ``chopped`` is
        true on that last row. ``remaining = budget - cumulative(spent +
        sent - received)``. Sorted by season, week, roster.

    Raises:
        ChoppedLeagueError: If there is no Chopped league, or one has no
            ``sleeper_chopped_rosters`` rows.
    """
    league_info = require_chopped_leagues(leagues)
    league_ids = league_info["league_id"].to_list()
    eliminated = eliminations(chopped_rosters, league_ids)
    moves = _movements(transactions, league_ids)

    weekly = moves.group_by(["league_id", "week", "roster_id"]).agg(
        pl.col("spent").sum().alias("spent_this_week"),
        pl.col("received").sum().alias("received_via_trade"),
        pl.col("sent").sum().alias("sent_via_trade"),
    )
    last_activity = moves.group_by("league_id").agg(pl.col("week").max().alias("_last"))
    spans = (
        league_info.join(last_activity, on="league_id", how="left")
        .with_columns(
            pl.max_horizontal(pl.col("last_chopped_leg"), pl.col("_last").fill_null(0))
            .clip(lower_bound=1)
            .alias("_final_week")
        )
        .join(eliminated, on="league_id")
        .with_columns(
            pl.min_horizontal(
                pl.col("_final_week"), pl.col("eliminated_leg").fill_null(10_000)
            ).alias("_through")
        )
        .with_columns(pl.int_ranges(1, pl.col("_through") + 1).alias("week"))
        .explode("week", empty_as_null=True)
    )
    frame = (
        spans.join(weekly, on=["league_id", "week", "roster_id"], how="left")
        .with_columns(
            pl.col("spent_this_week", "received_via_trade", "sent_via_trade")
            .fill_null(0)
            .cast(pl.Int64),
            (pl.col("eliminated_leg") == pl.col("week"))
            .fill_null(False)
            .alias("chopped"),
        )
        .sort(["league_id", "roster_id", "week"])
        .with_columns(
            (
                pl.col("waiver_budget")
                - (
                    pl.col("spent_this_week")
                    + pl.col("sent_via_trade")
                    - pl.col("received_via_trade")
                )
                .cum_sum()
                .over(["league_id", "roster_id"])
            ).alias("remaining")
        )
        .join(roster_owners(standings), on=["league_id", "roster_id"], how="left")
    )
    return frame.select(FAAB_COLUMNS).sort(["season", "league_id", "week", "roster_id"])


def faab_check(
    faab: pl.DataFrame, leagues: pl.DataFrame, chopped_rosters: pl.DataFrame
) -> pl.DataFrame:
    """Rosters whose rebuilt FAAB disagrees with Sleeper's ``waiver_budget_used``.

    Sleeper's ``waiver_budget_used`` is the roster's net FAAB out (winning
    bids plus FAAB traded away, minus FAAB received) as of the fetch, so it
    should equal the starting budget minus the roster's latest
    ``remaining``.

    Args:
        faab: :func:`faab_by_week`'s output.
        leagues: ``sleeper_leagues`` rows (for the starting budget).
        chopped_rosters: ``sleeper_chopped_rosters`` rows.

    Returns:
        ``league_id``, ``season``, ``roster_id``, ``manager``, ``rebuilt_used``,
        ``waiver_budget_used``, one row per mismatch; empty when every roster
        agrees. Rosters without a ``waiver_budget_used`` value are skipped.
    """
    budgets = require_chopped_leagues(leagues).select("league_id", "waiver_budget")
    latest = (
        faab.sort("week")
        .group_by(["league_id", "season", "roster_id", "manager"])
        .agg(pl.col("remaining").last())
        .join(budgets, on="league_id")
        .with_columns(
            (pl.col("waiver_budget") - pl.col("remaining")).alias("rebuilt_used")
        )
    )
    sleeper = chopped_rosters.select(
        pl.col("league_id").cast(pl.String),
        pl.col("roster_id").cast(pl.Int64),
        pl.col("waiver_budget_used").cast(pl.Int64),
    ).filter(pl.col("waiver_budget_used").is_not_null())
    return (
        latest.join(sleeper, on=["league_id", "roster_id"])
        .filter(pl.col("rebuilt_used") != pl.col("waiver_budget_used"))
        .select(
            "league_id",
            "season",
            "roster_id",
            "manager",
            "rebuilt_used",
            "waiver_budget_used",
        )
        .sort(["season", "league_id", "roster_id"])
    )
