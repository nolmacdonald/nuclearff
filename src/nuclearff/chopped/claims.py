"""Waiver claim activity per manager (issue #225).

Who bid on the most players, who the fewest, and how many claims failed,
and why? :func:`waiver_claims` is the shared per-claim table (one row per
claimed player, with an ``outcome``) that the bid-outcomes analysis (#227)
builds on; :func:`claim_activity` rolls it up per manager.

**Outcomes** come from ``status`` and Sleeper's ``metadata.notes`` text:

- ``won``: ``status == "complete"``.
- ``outbid``: "This player was claimed by another owner." -- a lost bidding
  contest.
- ``roster_full``: "Unfortunately, your roster will have too many players
  after this transaction." -- usually a queued claim with no drop that
  failed after an earlier claim in the same run filled the roster. It never
  competed.
- ``over_budget``: "You are over the budget for this transaction." -- never
  competed either.
- ``other``: any other failed note. Kept and counted, never dropped.

Matching on English note text is fragile, so the exact strings are
constants here and anything new lands in ``other`` with a logged warning.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import polars as pl

from nuclearff.chopped.common import (
    eliminations,
    require_chopped_leagues,
    roster_owners,
)

logger = logging.getLogger(__name__)

OUTBID_NOTE = "This player was claimed by another owner."
ROSTER_FULL_NOTE = (
    "Unfortunately, your roster will have too many players after this transaction."
)
OVER_BUDGET_NOTE = "You are over the budget for this transaction."

OUTCOMES = ("won", "outbid", "roster_full", "over_budget", "other")
"""Every ``outcome`` :func:`waiver_claims` assigns."""

CLAIM_COLUMNS = (
    "league_id",
    "season",
    "week",
    "transaction_id",
    "roster_id",
    "owner_id",
    "manager",
    "player_id",
    "bid",
    "status",
    "outcome",
)
"""Columns of :func:`waiver_claims`'s output."""

ACTIVITY_COLUMNS = (
    "owner_id",
    "manager",
    "claims_placed",
    "players_bid_on",
    "claims_won",
    "claims_failed",
    "outbid",
    "roster_full",
    "over_budget",
    "other_failed",
    "free_agent_adds",
    "weeks_alive",
    "claims_per_week_alive",
    "avg_bid",
)
"""Columns of :func:`claim_activity`'s output after its grouping keys."""


def _json(value: Any, default: Any) -> Any:
    """Decode a JSON text column value, or return ``default``."""
    if isinstance(value, str) and value:
        return json.loads(value)
    return default if value is None else value


def _outcome(status: str, note: str | None) -> str:
    if status == "complete":
        return "won"
    if note == OUTBID_NOTE:
        return "outbid"
    if note == ROSTER_FULL_NOTE:
        return "roster_full"
    if note == OVER_BUDGET_NOTE:
        return "over_budget"
    logger.warning("Unrecognized failed waiver claim note: %r", note)
    return "other"


def waiver_claims(
    transactions: pl.DataFrame, leagues: pl.DataFrame, standings: pl.DataFrame
) -> pl.DataFrame:
    """One row per claimed player per waiver claim, with its outcome.

    Args:
        transactions: ``sleeper_transactions`` rows -- ``transaction_id``,
            ``league_id``, ``week``, ``type``, ``status``, ``roster_ids``,
            ``adds``, ``settings`` and ``metadata`` (JSON text).
        leagues: ``sleeper_leagues`` rows (only Chopped leagues are used).
        standings: ``sleeper_standings`` rows (owner ids and names).

    Returns:
        Columns :data:`CLAIM_COLUMNS`. A claim adding several players gets
        one row per player, all with the same ``bid``. ``bid`` is Sleeper's
        ``settings.waiver_bid`` -- kept for losing claims too, which carry
        the real amount bid (verified on the real league: 212 of 422 failed
        2025 claims are nonzero, and no losing bid ever exceeds the winner).
        ``chopped`` transactions (a chopped roster's whole roster dropped)
        are not claims and are excluded.
    """
    info = require_chopped_leagues(leagues).select("league_id", "season")
    waivers = transactions.filter(
        (pl.col("type") == "waiver")
        & pl.col("league_id").cast(pl.String).is_in(info["league_id"].to_list())
    )
    rows = []
    for row in waivers.iter_rows(named=True):
        rosters = _json(row["roster_ids"], [])
        note = _json(row.get("metadata"), {}).get("notes")
        bid = _json(row["settings"], {}).get("waiver_bid")
        for player_id in _json(row["adds"], {}) or {}:
            rows.append(
                {
                    "league_id": str(row["league_id"]),
                    "week": row["week"],
                    "transaction_id": row["transaction_id"],
                    "roster_id": rosters[0] if rosters else None,
                    "player_id": str(player_id),
                    "bid": bid if isinstance(bid, int) else None,
                    "status": row["status"],
                    "outcome": _outcome(row["status"], note),
                }
            )
    schema = {
        "league_id": pl.String,
        "week": pl.Int64,
        "transaction_id": pl.String,
        "roster_id": pl.Int64,
        "player_id": pl.String,
        "bid": pl.Int64,
        "status": pl.String,
        "outcome": pl.String,
    }
    return (
        pl.DataFrame(rows, schema=schema)
        .join(info, on="league_id")
        .join(roster_owners(standings), on=["league_id", "roster_id"], how="left")
        .select(CLAIM_COLUMNS)
        .sort(["season", "league_id", "week", "transaction_id", "player_id"])
    )


def claim_activity(
    claims: pl.DataFrame,
    transactions: pl.DataFrame,
    leagues: pl.DataFrame,
    chopped_rosters: pl.DataFrame,
    standings: pl.DataFrame,
    *,
    career: bool = False,
) -> pl.DataFrame:
    """Claims placed, players bid on and failed claims by reason, per manager.

    Args:
        claims: :func:`waiver_claims`'s output.
        transactions: ``sleeper_transactions`` rows (for free-agent adds).
        leagues: ``sleeper_leagues`` rows.
        chopped_rosters: ``sleeper_chopped_rosters`` rows (weeks alive).
        standings: ``sleeper_standings`` rows (owner ids and names).
        career: Combine every season per owner instead of one row per season.

    Returns:
        Per season: ``league_id``, ``season``, then :data:`ACTIVITY_COLUMNS`;
        career: :data:`ACTIVITY_COLUMNS`. ``claims_placed`` counts claims
        (transactions), ``players_bid_on`` distinct players. ``weeks_alive``
        runs to the manager's chop week, or the league's last processed week,
        so ``claims_per_week_alive`` doesn't make an early chop look like the
        least active manager. Every manager on a roster gets a row, claims or
        not. Sorted by ``claims_placed``, most first.
    """
    info = require_chopped_leagues(leagues)
    league_ids = info["league_id"].to_list()
    keys = (
        ["owner_id", "manager"]
        if career
        else ["league_id", "season", "owner_id", "manager"]
    )

    last_week = (
        claims.group_by("league_id")
        .agg(pl.col("week").max().alias("_last_claim"))
        .join(info.select("league_id", "last_chopped_leg"), on="league_id", how="right")
        .select(
            "league_id",
            pl.max_horizontal(
                "last_chopped_leg", pl.col("_last_claim").fill_null(0)
            ).alias("_last_week"),
        )
    )
    managers = (
        eliminations(chopped_rosters, league_ids)
        .join(last_week, on="league_id")
        .join(info.select("league_id", "season"), on="league_id")
        .join(roster_owners(standings), on=["league_id", "roster_id"], how="left")
        .filter(pl.col("owner_id").is_not_null())
        .with_columns(pl.coalesce("eliminated_leg", "_last_week").alias("_weeks_alive"))
    )
    free_agents = (
        transactions.filter(
            (pl.col("type") == "free_agent")
            & (pl.col("status") == "complete")
            & pl.col("league_id").cast(pl.String).is_in(league_ids)
        )
        .select(
            pl.col("league_id").cast(pl.String),
            pl.col("roster_ids")
            .str.json_decode(pl.List(pl.Int64))
            .list.first()
            .alias("roster_id"),
        )
        .group_by(["league_id", "roster_id"])
        .agg(pl.len().alias("_fa"))
    )
    by_roster = (
        managers.join(free_agents, on=["league_id", "roster_id"], how="left")
        .group_by(keys)
        .agg(
            pl.col("_weeks_alive").sum().alias("weeks_alive"),
            pl.col("_fa").sum().alias("free_agent_adds"),
        )
    )

    outcome = pl.col("outcome")
    per_claim = claims.filter(pl.col("owner_id").is_not_null())
    stats = per_claim.group_by(keys).agg(
        pl.col("transaction_id").n_unique().alias("claims_placed"),
        pl.col("player_id").n_unique().alias("players_bid_on"),
        pl.col("transaction_id")
        .filter(outcome == "won")
        .n_unique()
        .alias("claims_won"),
        pl.col("transaction_id")
        .filter(outcome != "won")
        .n_unique()
        .alias("claims_failed"),
        *[
            pl.col("transaction_id").filter(outcome == name).n_unique().alias(alias)
            for name, alias in (
                ("outbid", "outbid"),
                ("roster_full", "roster_full"),
                ("over_budget", "over_budget"),
                ("other", "other_failed"),
            )
        ],
        pl.col("bid").mean().alias("avg_bid"),
    )
    counts = [
        "claims_placed",
        "players_bid_on",
        "claims_won",
        "claims_failed",
        "outbid",
        "roster_full",
        "over_budget",
        "other_failed",
        "free_agent_adds",
        "weeks_alive",
    ]
    activity = (
        by_roster.join(stats, on=keys, how="left")
        .with_columns(pl.col(counts).fill_null(0).cast(pl.Int64))
        .with_columns(
            pl.when(pl.col("weeks_alive") > 0)
            .then(pl.col("claims_placed") / pl.col("weeks_alive"))
            .alias("claims_per_week_alive")
        )
    )
    columns = list(ACTIVITY_COLUMNS) if career else [*keys[:2], *ACTIVITY_COLUMNS]
    return activity.select(columns).sort(
        ["claims_placed", "manager"], descending=[True, False]
    )
