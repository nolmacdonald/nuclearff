"""Convert VORP into auction dollars, with and without keeper inflation.

The standard VBD-to-dollars method, as sketched in the brain's
``research/auction-keeper-valuation.md``:

1. The league's whole budget is ``teams x budget_per_team``.
2. Every rosterable slot must be fillable, so ``$1 x rosterable_slots`` is
   reserved off the top — nobody can be priced such that a team cannot fill
   its roster.
3. What remains (the *spendable pool*) is distributed across the players who
   actually clear replacement level, proportional to each one's share of the
   total VORP in the draftable pool.

Only the top ``rosterable_slots`` players by VORP are priced above the
minimum bid: a league of 10 teams x 14 spots drafts 140 players, so the
141st-best player is, by construction, a $1 replacement-level body no matter
how his VORP compares to the 140th.

Keeper inflation
----------------
In a keeper league, kept players are held at a price below their market
value, and the dollars that *would* have bought them stay in the pool
chasing a smaller player population. Every remaining dollar of market value
therefore costs more than $1 at the real auction. This module computes both
the no-keeper baseline and the keeper-adjusted value so the difference — the
inflation effect — is visible rather than buried in one final number.

**Keeper cost is not available from Sleeper and this module never invents
it.** Confirmed live against the real league (2026-09-02): Sleeper exposes
``draft.settings.budget`` and an ``is_keeper`` flag on past picks, but no
keeper *price*, and this league's own draft description is empty (no
machine-readable escalator rule). Worse, every prior season of this league
was a snake draft — there are no historical auction prices to escalate from
either. Keeper costs must therefore be supplied by the caller as an explicit
mapping; :func:`keeper_adjusted_values` requires one rather than defaulting
to a guess.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import polars as pl

from nuclearff.exceptions import ConfigError

logger = logging.getLogger(__name__)

MIN_BID = 1
"""Dollars every rosterable player costs at minimum, reserved off the top."""


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing a column ``fn_name`` needs.

    Args:
        df: The DataFrame passed to ``fn_name``.
        required: Column names ``fn_name`` depends on.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: input DataFrame is missing expected column(s) "
            f"{missing!r} (got {df.columns!r})."
        )


def budget_from_draft(draft: dict) -> int:
    """Read the per-team auction budget from a Sleeper draft object.

    Confirmed live (2026-09-02) against the real auction league: the budget
    lives at ``draft["settings"]["budget"]`` and read ``200``. Note that the
    league object's ``settings.waiver_budget`` is FAAB, **not** the auction
    budget — do not substitute it.

    Args:
        draft: A raw Sleeper draft object, as returned by
            :meth:`nuclearff.sleeper.client.SleeperClient.get_draft`.

    Returns:
        The per-team auction budget in dollars.

    Raises:
        ConfigError: If the draft is not an auction, or carries no ``budget``
            setting. The plan's instruction for this case is to surface the
            gap and ask, never to assume a conventional $200.
    """
    draft_type = draft.get("type")
    if draft_type != "auction":
        raise ConfigError(
            f"budget_from_draft: draft type is {draft_type!r}, not 'auction'; "
            f"there is no auction budget to read."
        )

    settings = draft.get("settings")
    budget = settings.get("budget") if isinstance(settings, dict) else None
    if not isinstance(budget, (int, float)) or budget <= 0:
        raise ConfigError(
            "budget_from_draft: this auction draft has no usable "
            "`settings.budget`. Do not assume a conventional $200 — confirm "
            "the real per-team budget with the league before pricing anything."
        )
    return int(budget)


def auction_values(
    valuations: pl.DataFrame,
    *,
    teams: int,
    budget_per_team: int,
    roster_spots: int,
    vorp_column: str = "vorp",
    value_column: str = "auction_value",
) -> pl.DataFrame:
    """Distribute the league's spendable budget across the draftable pool by VORP.

    Args:
        valuations: One row per player, with ``player_id`` and
            ``vorp_column``. Rows with a null VORP are treated as
            replacement-level (minimum bid), not dropped.
        teams: Number of teams in the league.
        budget_per_team: Per-team auction budget, e.g. from
            :func:`budget_from_draft`.
        roster_spots: Roster spots per team, including bench. The draftable
            pool is ``teams * roster_spots`` players.
        vorp_column: Name of the VORP column to price against.
        value_column: Name of the dollar column to write.

    Returns:
        ``valuations`` with ``value_column`` added (floored at :data:`MIN_BID`)
        and an ``in_draft_pool`` boolean marking the top
        ``teams * roster_spots`` players by VORP.

        Dollars sum to exactly ``teams * budget_per_team`` **across the draft
        pool** — verified live: a 10-team, 14-spot, $200 league sums to
        $2,000.00 over its 140 pooled players. Players outside the pool are
        priced at :data:`MIN_BID` as well (they are real $1 waiver bodies),
        so the sum over *every* row exceeds the league budget by $1 per
        out-of-pool player. That is intended, not a rounding bug: filter on
        ``in_draft_pool`` before checking budget conservation.

    Raises:
        ValueError: If a required column is missing, or any of ``teams``,
            ``budget_per_team``, or ``roster_spots`` is not positive.
    """
    _require_columns(valuations, ("player_id", vorp_column), "auction_values")
    if min(teams, budget_per_team, roster_spots) <= 0:
        raise ValueError(
            f"auction_values: teams ({teams}), budget_per_team "
            f"({budget_per_team}), and roster_spots ({roster_spots}) must all "
            f"be positive."
        )

    rosterable_slots = teams * roster_spots
    total_budget_pool = teams * budget_per_team
    spendable_pool = total_budget_pool - rosterable_slots * MIN_BID

    if spendable_pool <= 0:
        logger.warning(
            "auction_values: the $%d minimum bid on %d rosterable slots "
            "consumes the entire $%d league budget - every player prices at "
            "the $%d minimum and VORP cannot differentiate anyone.",
            MIN_BID,
            rosterable_slots,
            total_budget_pool,
            MIN_BID,
        )

    ranked = valuations.with_columns(
        pl.col(vorp_column)
        .rank("ordinal", descending=True)
        .over(pl.lit(1))
        .alias("_vorp_rank")
    ).with_columns((pl.col("_vorp_rank") <= rosterable_slots).alias("in_draft_pool"))

    pool_vorp_total = (
        ranked.filter(pl.col("in_draft_pool"))
        .select(pl.col(vorp_column).clip(lower_bound=0.0).sum())
        .item()
    )

    if not pool_vorp_total or pool_vorp_total <= 0:
        logger.warning(
            "auction_values: total positive VORP across the draft pool is "
            "%r - cannot distribute the spendable pool proportionally, so "
            "every player prices at the $%d minimum.",
            pool_vorp_total,
            MIN_BID,
        )
        return ranked.with_columns(pl.lit(float(MIN_BID)).alias(value_column)).drop(
            "_vorp_rank"
        )

    return ranked.with_columns(
        (
            MIN_BID
            + pl.when(pl.col("in_draft_pool"))
            .then(pl.col(vorp_column).clip(lower_bound=0.0))
            .otherwise(0.0)
            / pool_vorp_total
            * max(spendable_pool, 0)
        )
        .fill_null(float(MIN_BID))
        .alias(value_column)
    ).drop("_vorp_rank")


def keeper_inflation_multiplier(
    valuations: pl.DataFrame,
    keeper_costs: Mapping[str, float],
    *,
    teams: int,
    budget_per_team: int,
    value_column: str = "auction_value",
) -> float:
    """Return the factor every non-kept dollar of market value inflates by.

    ``(total league $ remaining after keepers) / (market value of all
    non-kept players in the draft pool)`` — the standard formula from the
    ESPN/RotoWire keeper-inflation writeups cited in the brain's research
    file. A result of ``1.5`` means $1 of baseline market value should be
    expected to cost $1.50 at the real auction.

    Args:
        valuations: Output of :func:`auction_values`, with ``player_id``,
            ``value_column``, and ``in_draft_pool``.
        keeper_costs: ``player_id`` to the dollar price that player is kept
            at. Must be supplied — Sleeper does not expose keeper prices (see
            the module docstring).
        teams: Number of teams in the league.
        budget_per_team: Per-team auction budget.
        value_column: Name of the baseline dollar column.

    Returns:
        The inflation multiplier. Returns ``1.0`` (no inflation) when there
        are no keepers.

    Raises:
        ValueError: If a required column is missing, or the keepers' total
            cost meets or exceeds the entire league budget.
    """
    _require_columns(
        valuations,
        ("player_id", value_column, "in_draft_pool"),
        "keeper_inflation_multiplier",
    )
    if not keeper_costs:
        return 1.0

    total_budget_pool = teams * budget_per_team
    kept_total = float(sum(keeper_costs.values()))
    remaining_budget = total_budget_pool - kept_total

    if remaining_budget <= 0:
        raise ValueError(
            f"keeper_inflation_multiplier: keeper costs total ${kept_total:,.0f}, "
            f"which meets or exceeds the entire ${total_budget_pool:,.0f} league "
            f"budget; there is nothing left to inflate."
        )

    kept_ids = list(keeper_costs)
    non_kept_market_value = (
        valuations.filter(
            pl.col("in_draft_pool") & ~pl.col("player_id").is_in(kept_ids)
        )
        .select(pl.col(value_column).sum())
        .item()
    )

    if not non_kept_market_value or non_kept_market_value <= 0:
        logger.warning(
            "keeper_inflation_multiplier: non-kept market value is %r - "
            "returning a multiplier of 1.0 rather than dividing by zero.",
            non_kept_market_value,
        )
        return 1.0

    multiplier = remaining_budget / float(non_kept_market_value)
    # Note: printf-style %-formatting has no thousands-separator flag — `%,.0f`
    # raises `ValueError: unsupported format character ','` inside logging.
    logger.info(
        "Keeper inflation: $%.0f remaining across $%.0f of non-kept market "
        "value -> %.2fx",
        remaining_budget,
        non_kept_market_value,
        multiplier,
    )
    return multiplier


def keeper_adjusted_values(
    valuations: pl.DataFrame,
    keeper_costs: Mapping[str, float],
    *,
    teams: int,
    budget_per_team: int,
    value_column: str = "auction_value",
    adjusted_column: str = "auction_value_keeper_adjusted",
) -> pl.DataFrame:
    """Apply keeper inflation to every non-kept player's auction value.

    Kept players keep their contracted price in ``adjusted_column`` (what
    their owner actually pays, not what they are worth); everyone else in the
    draft pool is scaled by :func:`keeper_inflation_multiplier`. Both the
    baseline and the adjusted number survive in the output so the inflation
    effect stays visible.

    Args:
        valuations: Output of :func:`auction_values`.
        keeper_costs: ``player_id`` to kept price. Empty means no keepers, in
            which case the adjusted column equals the baseline.
        teams: Number of teams in the league.
        budget_per_team: Per-team auction budget.
        value_column: Name of the baseline dollar column to read.
        adjusted_column: Name of the inflated dollar column to write.

    Returns:
        ``valuations`` with ``adjusted_column`` and an ``is_keeper`` boolean
        added.

    Raises:
        ValueError: See :func:`keeper_inflation_multiplier`.
    """
    multiplier = keeper_inflation_multiplier(
        valuations,
        keeper_costs,
        teams=teams,
        budget_per_team=budget_per_team,
        value_column=value_column,
    )

    kept_ids = list(keeper_costs)
    cost_frame = (
        pl.DataFrame(
            {
                "player_id": kept_ids,
                "_keeper_cost": [float(keeper_costs[pid]) for pid in kept_ids],
            }
        )
        if kept_ids
        else pl.DataFrame(
            {"player_id": [], "_keeper_cost": []},
            schema={"player_id": pl.Utf8, "_keeper_cost": pl.Float64},
        )
    )

    joined = valuations.join(cost_frame, on="player_id", how="left")

    return joined.with_columns(
        pl.col("_keeper_cost").is_not_null().alias("is_keeper"),
        pl.when(pl.col("_keeper_cost").is_not_null())
        .then(pl.col("_keeper_cost"))
        .otherwise(pl.col(value_column) * multiplier)
        .alias(adjusted_column),
    ).drop("_keeper_cost")
