"""Manager trade network: data prep from persisted Sleeper trade transactions.

Turns ``sleeper_transactions`` (written by ``nuclearff sleeper fetch-league
--transactions``, issue #20) rows where ``type == "trade"`` into a manager-pair
edge list and a handful of per-manager aggregates, shared by every
trade-visualization issue (#42-#51). No new Sleeper fetching happens here —
this module only reads what's already persisted.

A trade is one row in ``sleeper_transactions`` with a ``roster_ids`` array of
the rosters involved. Sleeper's schema allows more than two rosters per
trade, so :func:`load_trades` explodes an N-team trade into one edge per
unordered manager pair (``C(n, 2)`` edges) rather than assuming exactly two
parties. That means a manager in a single 3-team trade appears in 2 edges,
not 1 — every aggregate below that counts *trades* rather than *edges*
(:func:`manager_trade_counts`, :func:`trades_by_season`,
:func:`cumulative_trade_counts`) de-duplicates back down to distinct
``transaction_id`` values per manager via :func:`_manager_transactions`
before counting, so an N-way trade is never double-counted as multiple
trades for the same manager.

Manager identity is the ``roster_display_names`` string already resolved
(per season, at capture time) by :mod:`nuclearff.sleeper.transactions` —
aggregating one manager's trades across several seasons relies on that
display name staying stable across them, true so far for this league but
not guaranteed by Sleeper.

Density over the *full* manager roster (including a manager who never
traded) is deliberately out of scope here: that roster lives in
``sleeper_standings``, a different table, not ``sleeper_transactions``.
Callers that need a zero-trade manager to still appear (the heatmap,
network graph, and season-heatmap visualizations do) join this module's
output against that roster themselves.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import combinations
from pathlib import Path

import polars as pl

from nuclearff.duckdb_io import read_table

TABLE_NAME = "sleeper_transactions"
"""Source table this module reads from — see :mod:`nuclearff.sleeper.transactions`."""

_EDGES_SCHEMA = {
    "transaction_id": pl.String,
    "season": pl.Int64,
    "week": pl.Int64,
    "created_at": pl.Datetime,
    "manager_a": pl.String,
    "manager_b": pl.String,
}


def load_trades(
    db_path: str | Path, league_ids: Sequence[str] | None = None
) -> pl.DataFrame:
    """Explode persisted trade transactions into one row per manager pair per trade.

    Only ``status == "complete"`` trades count — a vetoed/failed trade never
    happened.

    Args:
        db_path: Path to the DuckDB database file written by
            ``nuclearff sleeper fetch-league --transactions``.
        league_ids: Restrict to these seasons' Sleeper league ids
            (``sleeper_transactions`` stores one ``league_id`` per season,
            since Sleeper mints a new one each year). ``None`` includes
            every trade in the table — the manager trade network is a
            multi-season view by design.

    Returns:
        One row per (trade, manager pair): ``transaction_id``, ``season``,
        ``week``, ``created_at``, and ``manager_a``/``manager_b``
        (alphabetically ordered, so a pair is never double-counted as both
        A-B and B-A). A trade where fewer than two rosters resolve to a
        known manager name contributes no rows.
    """
    transactions = read_table(db_path, TABLE_NAME).filter(
        (pl.col("type") == "trade") & (pl.col("status") == "complete")
    )
    if league_ids is not None:
        transactions = transactions.filter(pl.col("league_id").is_in(list(league_ids)))

    rows: list[dict[str, object]] = []
    for row in transactions.iter_rows(named=True):
        names = sorted(
            {n for n in json.loads(row["roster_display_names"]) if n is not None}
        )
        for manager_a, manager_b in combinations(names, 2):
            rows.append(
                {
                    "transaction_id": row["transaction_id"],
                    "season": row["season"],
                    "week": row["week"],
                    "created_at": row["created_at"],
                    "manager_a": manager_a,
                    "manager_b": manager_b,
                }
            )

    return pl.DataFrame(rows, schema=_EDGES_SCHEMA)


def _manager_transactions(edges: pl.DataFrame) -> pl.DataFrame:
    """One row per (manager, transaction), collapsing an N-way trade's multiple edges.

    :func:`load_trades` explodes each trade into one edge per manager pair,
    so a manager in a 3-way trade appears in 2 edges for what is really 1
    trade. Every aggregate that counts *trades* rather than *edges* needs
    this de-duplicated view first.

    Every caller already guards ``edges.height == 0`` before reaching here
    (each returns its own typed empty schema), so this assumes a non-empty
    frame.

    Args:
        edges: Output of :func:`load_trades`, non-empty.

    Returns:
        One row per manager per distinct trade they were party to:
        ``manager``, ``transaction_id``, ``season``, ``week``, ``created_at``.
    """
    long = pl.concat(
        [
            edges.select(
                pl.col("manager_a").alias("manager"),
                "transaction_id",
                "season",
                "week",
                "created_at",
            ),
            edges.select(
                pl.col("manager_b").alias("manager"),
                "transaction_id",
                "season",
                "week",
                "created_at",
            ),
        ]
    )
    return long.unique(subset=["manager", "transaction_id"])


def manager_trade_counts(edges: pl.DataFrame) -> pl.DataFrame:
    """Per-manager trade totals, unique partners, and most frequent partner.

    Args:
        edges: Output of :func:`load_trades`.

    Returns:
        One row per manager who appears in ``edges``: ``manager``, ``trades``
        (distinct transaction count — not edge count, see the module
        docstring), ``unique_partners``, ``most_frequent_partner``,
        ``trades_with_partner`` (trade count with that partner, ties broken
        alphabetically by partner name for determinism). A manager with no
        trades never appears — see the module docstring on manager-roster
        density.
    """
    counts_schema = {
        "manager": pl.String,
        "trades": pl.UInt32,
        "unique_partners": pl.UInt32,
        "most_frequent_partner": pl.String,
        "trades_with_partner": pl.UInt32,
    }
    if edges.height == 0:
        return pl.DataFrame(schema=counts_schema)

    trades = (
        _manager_transactions(edges)
        .group_by("manager")
        .agg(pl.col("transaction_id").n_unique().alias("trades"))
    )

    pairs = pl.concat(
        [
            edges.select(
                pl.col("manager_a").alias("manager"),
                pl.col("manager_b").alias("partner"),
                "transaction_id",
            ),
            edges.select(
                pl.col("manager_b").alias("manager"),
                pl.col("manager_a").alias("partner"),
                "transaction_id",
            ),
        ]
    )
    partner_counts = (
        pairs.group_by(["manager", "partner"])
        .agg(pl.col("transaction_id").n_unique().alias("trades_with_partner"))
        .sort(
            ["manager", "trades_with_partner", "partner"],
            descending=[False, True, False],
        )
    )
    unique_partners = pairs.group_by("manager").agg(
        pl.col("partner").n_unique().alias("unique_partners")
    )
    most_frequent = partner_counts.group_by("manager", maintain_order=True).first()

    return (
        trades.join(unique_partners, on="manager", how="left")
        .join(
            most_frequent.select("manager", "partner", "trades_with_partner").rename(
                {"partner": "most_frequent_partner"}
            ),
            on="manager",
            how="left",
        )
        .sort("trades", descending=True)
    )


def pairwise_trade_matrix(edges: pl.DataFrame) -> pl.DataFrame:
    """A symmetric manager x manager trade-count matrix.

    Dense over every manager who appears in ``edges`` (not the full league
    roster — see the module docstring), including pairs that never traded
    (filled with ``0``), with a ``0`` diagonal.

    Args:
        edges: Output of :func:`load_trades`.

    Returns:
        A DataFrame with one ``manager`` column plus one column per manager,
        values are trade counts between that row/column pair.
    """
    if edges.height == 0:
        return pl.DataFrame({"manager": []}, schema={"manager": pl.String})

    managers = sorted(set(edges["manager_a"]) | set(edges["manager_b"]))
    pair_counts = edges.group_by(["manager_a", "manager_b"]).agg(
        pl.col("transaction_id").n_unique().alias("trades")
    )
    counts: dict[tuple[str, str], int] = {
        (row["manager_a"], row["manager_b"]): row["trades"]
        for row in pair_counts.iter_rows(named=True)
    }

    rows = []
    for row_manager in managers:
        row: dict[str, object] = {"manager": row_manager}
        for col_manager in managers:
            if row_manager == col_manager:
                row[col_manager] = 0
            else:
                key = (
                    (row_manager, col_manager)
                    if row_manager < col_manager
                    else (col_manager, row_manager)
                )
                row[col_manager] = counts.get(key, 0)
        rows.append(row)

    return pl.DataFrame(rows)


def trades_by_season(edges: pl.DataFrame) -> pl.DataFrame:
    """Per-manager, per-season trade counts.

    Args:
        edges: Output of :func:`load_trades`.

    Returns:
        One row per (manager, season) that had at least one trade:
        ``manager``, ``season``, ``trades`` (distinct transaction count).
        Dense only over observed (manager, season) pairs — a season/manager
        combination with no trades is simply absent, not a ``0`` row;
        callers building a dense heatmap fill the gaps themselves.
    """
    schema = {"manager": pl.String, "season": pl.Int64, "trades": pl.UInt32}
    if edges.height == 0:
        return pl.DataFrame(schema=schema)

    return (
        _manager_transactions(edges)
        .group_by(["manager", "season"])
        .agg(pl.col("transaction_id").n_unique().alias("trades"))
        .sort(["manager", "season"])
    )


def cumulative_trade_counts(edges: pl.DataFrame) -> pl.DataFrame:
    """Per-manager running trade total over time.

    Args:
        edges: Output of :func:`load_trades`.

    Returns:
        One row per (manager, trade), ordered by ``created_at`` within each
        manager: ``manager``, ``transaction_id``, ``created_at``,
        ``cumulative_trades`` (1, 2, 3, ... — monotonically non-decreasing
        per manager by construction).
    """
    schema = {
        "manager": pl.String,
        "transaction_id": pl.String,
        "created_at": pl.Datetime,
        "cumulative_trades": pl.UInt32,
    }
    if edges.height == 0:
        return pl.DataFrame(schema=schema)

    manager_transactions = _manager_transactions(edges).sort(["manager", "created_at"])
    return manager_transactions.with_columns(
        pl.col("transaction_id").cum_count().over("manager").alias("cumulative_trades")
    ).select("manager", "transaction_id", "created_at", "cumulative_trades")
