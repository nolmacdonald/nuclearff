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
    "processed_at",
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
        Columns :data:`CLAIM_COLUMNS`. ``processed_at`` is when Sleeper
        processed the claim (``status_updated_at``); Sleeper can run waivers
        more than once in a week, so it separates those runs. A claim adding
        several players gets
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
                    "processed_at": row.get("status_updated_at"),
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
        "processed_at": pl.Datetime("us"),
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


# -------------------------------------------------------------------------------------
# BID OUTCOMES (#227)
# -------------------------------------------------------------------------------------

OUTCOME_COLUMNS = (
    "owner_id",
    "manager",
    "bids_decided",
    "bids_won",
    "pct_bids_won",
    "won_contested",
    "won_uncontested",
    "outbid",
    "runner_up_losses",
    "tied_losses",
    "tied_losses_at_zero",
    "avg_margin_lost_by",
    "non_competing",
)
"""Columns of :func:`bid_outcomes`'s output after its grouping keys."""


def bid_contests(claims: pl.DataFrame) -> pl.DataFrame:
    """Each bid in a contest for a player, with the contest's winning bid.

    A contest is one player in one waiver run. Sleeper can run waivers more
    than once in a week (real: player 12529 won by two managers on Nov 8 and
    Nov 12 of 2025's week 10), so a run is the day a claim was processed
    (``processed_at``), falling back to the week when that's missing. Only
    claims decided by
    bidding count (``won`` and ``outbid``). If a manager claimed the same
    player more than once in a week, one bid is kept: their winning claim if
    any (the others lost to their own claim, not to a rival), else their
    highest.

    Args:
        claims: :func:`waiver_claims`'s output.

    Returns:
        ``league_id``, ``season``, ``week``, ``run`` (the processing day),
        ``player_id``, ``owner_id``,
        ``manager``, ``bid``, ``outcome``, ``winning_bid``,
        ``top_losing_bid`` and ``contested`` (someone else bid too). An
        ``outbid`` claim with no ``won`` claim in its contest has a null
        ``winning_bid`` (see :func:`orphan_losses`).
    """
    contest = ["league_id", "_run", "player_id"]
    bids = (
        claims.with_columns(
            pl.coalesce(
                pl.col("processed_at").dt.date().cast(pl.String),
                pl.col("week").cast(pl.String),
            ).alias("_run")
        )
        .filter(
            pl.col("outcome").is_in(["won", "outbid"])
            & pl.col("owner_id").is_not_null()
        )
        .with_columns(pl.col("bid").fill_null(0))
        # Keep one bid per manager per contest: the winning claim if there is
        # one, else the highest. A manager's second claim on a player they
        # won comes back "claimed by another owner" -- lost to themselves --
        # which isn't a lost contest (real: Ziltoid11, 2025 week 9).
        .sort([pl.col("outcome") == "won", pl.col("bid")], descending=True)
        .unique([*contest, "owner_id"], keep="first", maintain_order=True)
    )
    won = pl.col("outcome") == "won"
    return (
        bids.with_columns(
            pl.col("bid").filter(won).max().over(contest).alias("winning_bid"),
            pl.col("bid").filter(~won).max().over(contest).alias("top_losing_bid"),
            (pl.len().over(contest) > 1).alias("contested"),
        )
        .rename({"_run": "run"})
        .select(
            "league_id",
            "season",
            "week",
            "run",
            "player_id",
            "owner_id",
            "manager",
            "bid",
            "outcome",
            "winning_bid",
            "top_losing_bid",
            "contested",
        )
        .sort(["season", "league_id", "week", "player_id", "bid"])
    )


def orphan_losses(contests: pl.DataFrame) -> pl.DataFrame:
    """``outbid`` claims whose contest has no winning claim.

    Not seen in the real league (every outbid claim's player was won by
    someone that same week), but reported rather than dropped if it happens.

    Args:
        contests: :func:`bid_contests`'s output.

    Returns:
        The ``outbid`` rows with a null ``winning_bid``.
    """
    return contests.filter(
        (pl.col("outcome") == "outbid") & pl.col("winning_bid").is_null()
    )


def bid_outcomes(
    claims: pl.DataFrame, *, career: bool = False, include_non_competing: bool = False
) -> pl.DataFrame:
    """How often each manager wins the players they bid on, and how narrowly they lose.

    Args:
        claims: :func:`waiver_claims`'s output.
        career: Combine every season per owner instead of one row per season.
        include_non_competing: Count ``roster_full``/``over_budget``/``other``
            claims as bids not won in ``pct_bids_won``. By default only claims
            decided by bidding count: those never entered a contest.

    Returns:
        Per season: ``league_id``, ``season``, then :data:`OUTCOME_COLUMNS`;
        career: :data:`OUTCOME_COLUMNS`.

        - ``pct_bids_won`` = ``bids_won / bids_decided``;
          ``won_contested``/``won_uncontested``: whether anyone else bid.
        - ``runner_up_losses``: ``outbid`` claims that were the **highest
          losing bid** in their contest (the second-highest bidder); tied
          losers all count.
        - ``tied_losses``: ``outbid`` claims that **equaled the winning bid**
          -- lost on Sleeper's tiebreak (waiver order), not on price.
          ``tied_losses_at_zero`` is the subset where both bids were $0.
        - ``avg_margin_lost_by``: winning bid minus the manager's bid,
          averaged over runner-up losses.
        - ``non_competing``: roster-full, over-budget and other failed claims.

        Sorted by ``runner_up_losses`` (the unluckiest bidders first).
    """
    keys = (
        ["owner_id", "manager"]
        if career
        else ["league_id", "season", "owner_id", "manager"]
    )
    contests = bid_contests(claims)
    orphans = orphan_losses(contests)
    if orphans.height:
        logger.warning(
            "%d outbid claim(s) have no winning claim in the same week", orphans.height
        )

    won = pl.col("outcome") == "won"
    lost = pl.col("outcome") == "outbid"
    runner_up = lost & (pl.col("bid") == pl.col("top_losing_bid"))
    tied = lost & (pl.col("bid") == pl.col("winning_bid"))
    stats = contests.group_by(keys).agg(
        pl.len().alias("bids_decided"),
        won.sum().alias("bids_won"),
        (won & pl.col("contested")).sum().alias("won_contested"),
        (won & ~pl.col("contested")).sum().alias("won_uncontested"),
        lost.sum().alias("outbid"),
        runner_up.sum().alias("runner_up_losses"),
        tied.sum().alias("tied_losses"),
        (tied & (pl.col("bid") == 0)).sum().alias("tied_losses_at_zero"),
        (pl.col("winning_bid") - pl.col("bid"))
        .filter(runner_up)
        .mean()
        .alias("avg_margin_lost_by"),
    )
    non_competing = (
        claims.filter(
            pl.col("outcome").is_in(["roster_full", "over_budget", "other"])
            & pl.col("owner_id").is_not_null()
        )
        .group_by(keys)
        .agg(pl.col("transaction_id").n_unique().alias("non_competing"))
    )
    counts = [
        "bids_decided",
        "bids_won",
        "won_contested",
        "won_uncontested",
        "outbid",
        "runner_up_losses",
        "tied_losses",
        "tied_losses_at_zero",
        "non_competing",
    ]
    managers = claims.filter(pl.col("owner_id").is_not_null()).select(keys).unique()
    outcomes = (
        managers.join(stats, on=keys, how="left")
        .join(non_competing, on=keys, how="left")
        .with_columns(pl.col(counts).fill_null(0).cast(pl.Int64))
    )
    denominator = (
        pl.col("bids_decided") + pl.col("non_competing")
        if include_non_competing
        else pl.col("bids_decided")
    )
    outcomes = outcomes.with_columns(
        pl.when(denominator > 0)
        .then(pl.col("bids_won") / denominator)
        .alias("pct_bids_won")
    )
    columns = list(OUTCOME_COLUMNS) if career else [*keys[:2], *OUTCOME_COLUMNS]
    return outcomes.select(columns).sort(
        ["runner_up_losses", "tied_losses", "manager"], descending=[True, True, False]
    )
