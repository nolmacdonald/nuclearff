"""Unit tests for nuclearff.sleeper.trades: trade-network data prep.

Builds a minimal ``sleeper_transactions`` table directly rather than through
the full fetch-and-write path already covered by
``tests/test_sleeper_transactions.py``, since ``load_trades`` only reads a
handful of its columns. Uses this league's real manager names (see that
file's ``ROSTER_NAMES``) and a real 2-team trade shape, plus one synthetic
3-team trade (this league's real history has none yet) to exercise N-way
explosion, matching issue #41's acceptance criteria.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import polars as pl
import pytest

from nuclearff.sleeper.trades import (
    cumulative_trade_counts,
    load_trades,
    manager_trade_counts,
    pairwise_trade_matrix,
    top_manager_pairs,
    total_trades_by_season,
    trades_by_season,
)

LEAGUE_ID = "1240509989819273216"

_CREATE_SQL = """
CREATE TABLE sleeper_transactions (
    transaction_id VARCHAR,
    league_id VARCHAR,
    season INTEGER,
    week INTEGER,
    type VARCHAR,
    status VARCHAR,
    created_at TIMESTAMP,
    roster_display_names JSON
)
"""

_ROWS = [
    # A real 2-team trade shape (see test_sleeper_transactions.py's TRADE fixture).
    (
        "1291599084301348864",
        LEAGUE_ID,
        2025,
        9,
        "trade",
        "complete",
        datetime(2025, 11, 4, tzinfo=UTC),
        json.dumps(["nolmacdonald", "Donkeysride"]),
    ),
    # A synthetic 3-team trade (this league's real history has none yet) to
    # exercise N-way explosion: C(3, 2) = 3 pairwise edges from one transaction.
    (
        "9999999999999999999",
        LEAGUE_ID,
        2026,
        3,
        "trade",
        "complete",
        datetime(2026, 9, 20, tzinfo=UTC),
        json.dumps(["nolmacdonald", "hyoga10", "Donkeysride"]),
    ),
    # A failed trade must not count -- it never happened.
    (
        "8888888888888888888",
        LEAGUE_ID,
        2025,
        10,
        "trade",
        "failed",
        datetime(2025, 11, 10, tzinfo=UTC),
        json.dumps(["nolmacdonald", "hyoga10"]),
    ),
    # A waiver must not count as a trade.
    (
        "7777777777777777777",
        LEAGUE_ID,
        2025,
        1,
        "waiver",
        "complete",
        datetime(2025, 9, 1, tzinfo=UTC),
        json.dumps(["hyoga10"]),
    ),
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "nuclearff.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute(_CREATE_SQL)
        conn.executemany(
            "INSERT INTO sleeper_transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", _ROWS
        )
    return path


# --- load_trades ---------------------------------------------------------------


def test_load_trades_explodes_a_two_team_trade_into_one_edge(db_path):
    edges = load_trades(db_path)

    two_team = edges.filter(pl.col("transaction_id") == "1291599084301348864")
    assert two_team.height == 1
    assert two_team["manager_a"][0] == "Donkeysride"
    assert two_team["manager_b"][0] == "nolmacdonald"


def test_load_trades_explodes_a_three_team_trade_into_three_edges(db_path):
    edges = load_trades(db_path)

    three_team = edges.filter(pl.col("transaction_id") == "9999999999999999999")
    assert three_team.height == 3
    pairs = set(zip(three_team["manager_a"], three_team["manager_b"], strict=True))
    assert pairs == {
        ("Donkeysride", "hyoga10"),
        ("Donkeysride", "nolmacdonald"),
        ("hyoga10", "nolmacdonald"),
    }


def test_load_trades_excludes_failed_trades_and_non_trade_types(db_path):
    edges = load_trades(db_path)
    ids = edges["transaction_id"].to_list()

    assert "8888888888888888888" not in ids
    assert "7777777777777777777" not in ids


def test_load_trades_filters_by_league_ids(db_path):
    edges = load_trades(db_path, league_ids=["some-other-league"])

    assert edges.height == 0


def test_load_trades_returns_empty_frame_for_no_trades(tmp_path):
    path = tmp_path / "empty.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute(_CREATE_SQL)

    edges = load_trades(path)

    assert edges.height == 0
    assert manager_trade_counts(edges).height == 0
    assert pairwise_trade_matrix(edges).height == 0
    assert top_manager_pairs(edges).height == 0
    assert trades_by_season(edges).height == 0
    assert total_trades_by_season(edges).height == 0
    assert cumulative_trade_counts(edges).height == 0


# --- manager_trade_counts -------------------------------------------------------


def test_manager_trade_counts_counts_distinct_trades_not_edges(db_path):
    """nolmacdonald is in both the 2-team and 3-team trade -- 2 trades, not 3 edges."""
    edges = load_trades(db_path)
    counts = manager_trade_counts(edges)
    by_manager = dict(zip(counts["manager"], counts["trades"], strict=True))

    assert by_manager["nolmacdonald"] == 2
    assert by_manager["Donkeysride"] == 2
    assert by_manager["hyoga10"] == 1


def test_manager_trade_counts_unique_partners_and_most_frequent(db_path):
    edges = load_trades(db_path)
    counts = manager_trade_counts(edges)
    nolan = counts.filter(pl.col("manager") == "nolmacdonald").to_dicts()[0]

    assert nolan["unique_partners"] == 2  # Donkeysride, hyoga10
    assert nolan["most_frequent_partner"] == "Donkeysride"
    assert nolan["trades_with_partner"] == 2


def test_manager_trade_counts_breaks_ties_deterministically(db_path):
    """hyoga10 has exactly 1 trade with each of two partners -- must not be flaky."""
    edges = load_trades(db_path)
    counts = manager_trade_counts(edges)
    hyoga = counts.filter(pl.col("manager") == "hyoga10").to_dicts()[0]

    assert (
        hyoga["most_frequent_partner"] == "Donkeysride"
    )  # alphabetically first of the tie


# --- pairwise_trade_matrix -------------------------------------------------------


def test_pairwise_trade_matrix_is_symmetric_and_dense(db_path):
    edges = load_trades(db_path)
    matrix = pairwise_trade_matrix(edges)
    by_manager = {row["manager"]: row for row in matrix.to_dicts()}

    assert by_manager["nolmacdonald"]["Donkeysride"] == 2
    assert by_manager["Donkeysride"]["nolmacdonald"] == 2
    assert by_manager["nolmacdonald"]["nolmacdonald"] == 0
    assert by_manager["nolmacdonald"]["hyoga10"] == 1


# --- top_manager_pairs -------------------------------------------------------


def test_top_manager_pairs_ranks_by_trade_count_descending(db_path):
    edges = load_trades(db_path)
    top = top_manager_pairs(edges)

    assert top.row(0, named=True) == {
        "manager_a": "Donkeysride",
        "manager_b": "nolmacdonald",
        "trades": 2,
    }


def test_top_manager_pairs_each_pair_appears_once(db_path):
    """Issue #47's acceptance criterion: A-B, never also B-A."""
    edges = load_trades(db_path)
    top = top_manager_pairs(edges)

    pairs = list(zip(top["manager_a"], top["manager_b"], strict=True))
    reversed_pairs = {(b, a) for a, b in pairs}

    assert len(pairs) == len(set(pairs))
    assert not reversed_pairs & set(pairs)


def test_top_manager_pairs_respects_n(db_path):
    edges = load_trades(db_path)

    assert top_manager_pairs(edges, n=1).height == 1


# --- trades_by_season -------------------------------------------------------


def test_trades_by_season_counts_distinct_trades_per_season(db_path):
    edges = load_trades(db_path)
    by_season = trades_by_season(edges)
    row = by_season.filter(
        (pl.col("manager") == "nolmacdonald") & (pl.col("season") == 2025)
    ).to_dicts()[0]

    assert row["trades"] == 1


# --- total_trades_by_season -------------------------------------------------------


def test_total_trades_by_season_counts_each_trade_once(db_path):
    """The 3-team trade must not multi-count as 2 (or 3) league-wide trades."""
    edges = load_trades(db_path)
    totals = total_trades_by_season(edges)
    by_season = dict(zip(totals["season"], totals["trades"], strict=True))

    assert by_season[2025] == 1
    assert by_season[2026] == 1


# --- cumulative_trade_counts -------------------------------------------------------


def test_cumulative_trade_counts_is_monotonically_non_decreasing(db_path):
    edges = load_trades(db_path)
    cumulative = cumulative_trade_counts(edges)

    for manager in cumulative["manager"].unique():
        values = (
            cumulative.filter(pl.col("manager") == manager)
            .sort("created_at")["cumulative_trades"]
            .to_list()
        )
        assert values == sorted(values)


def test_cumulative_trade_counts_reaches_the_final_total(db_path):
    edges = load_trades(db_path)
    cumulative = cumulative_trade_counts(edges)
    nolan = cumulative.filter(pl.col("manager") == "nolmacdonald").sort("created_at")

    assert nolan["cumulative_trades"].to_list() == [1, 2]
