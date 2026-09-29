"""Unit tests for nuclearff.chopped.faab (issue #221)."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.chopped.faab import FAAB_COLUMNS, faab_by_week, faab_check
from nuclearff.exceptions import ChoppedLeagueError
from nuclearff.report.chopped import render_faab_remaining

LEAGUE_ID = "chop1"


def _tx(week, type_, status, roster_ids, *, bid=None, faab=None, adds=None):
    return {
        "league_id": LEAGUE_ID,
        "season": 2025,
        "week": week,
        "type": type_,
        "status": status,
        "roster_ids": json.dumps(roster_ids),
        "settings": json.dumps({"waiver_bid": bid}) if bid is not None else None,
        "waiver_budget": json.dumps(faab or []),
        "adds": json.dumps(adds or {}),
    }


TRANSACTIONS = pl.DataFrame(
    [
        _tx(1, "waiver", "complete", [1], bid=100),
        _tx(1, "waiver", "failed", [2], bid=90),  # lost: costs nothing
        _tx(1, "free_agent", "complete", [3]),  # no bid
        # One claim adding two players: the whole bid counts once.
        _tx(2, "waiver", "complete", [2], bid=50, adds={"p1": 2, "p2": 2}),
        _tx(
            2,
            "trade",
            "complete",
            [1, 3],
            faab=[{"amount": 25, "sender": 1, "receiver": 3}],
        ),
        _tx(3, "waiver", "complete", [1], bid=0),  # a $0 win
    ]
)

LEAGUES = pl.DataFrame(
    [
        {
            "league_id": LEAGUE_ID,
            "season": 2025,
            "total_rosters": 3,
            "settings": json.dumps(
                {"type": 3, "last_chopped_leg": 3, "waiver_budget": 1000}
            ),
        }
    ]
)


def _chopped_rosters(used=None) -> pl.DataFrame:
    used = used or {1: 125, 2: 50, 3: -25}
    return pl.DataFrame(
        [
            {
                "league_id": LEAGUE_ID,
                "season": 2025,
                "roster_id": roster_id,
                "owner_id": f"u{roster_id}",
                "eliminated_leg": leg,
                "waiver_budget_used": used[roster_id],
            }
            for roster_id, leg in {1: None, 2: None, 3: 2}.items()
        ],
        schema_overrides={"eliminated_leg": pl.Int64},
    )


STANDINGS = pl.DataFrame(
    [
        {
            "league_id": LEAGUE_ID,
            "season": 2025,
            "roster_id": r,
            "owner_id": f"u{r}",
            "display_name": f"manager{r}",
        }
        for r in (1, 2, 3)
    ]
)


def _faab() -> pl.DataFrame:
    return faab_by_week(TRANSACTIONS, LEAGUES, _chopped_rosters(), STANDINGS)


def _balance(faab: pl.DataFrame, roster_id: int) -> list[int]:
    return (
        faab.filter(pl.col("roster_id") == roster_id)
        .sort("week")["remaining"]
        .to_list()
    )


def test_columns_and_budget_from_league_settings():
    faab = _faab()
    assert faab.columns == list(FAAB_COLUMNS)
    assert _balance(faab, 2)[0] == 1000  # the failed claim cost nothing


def test_winning_bids_and_trades_move_the_balance():
    faab = _faab()
    # Roster 1: -100 (week 1), -25 sent (week 2), $0 win (week 3).
    assert _balance(faab, 1) == [900, 875, 875]
    # Roster 2's two-player claim counts its bid once.
    assert _balance(faab, 2) == [1000, 950, 950]


def test_trade_columns():
    week2 = _faab().filter(pl.col("week") == 2)
    one = week2.filter(pl.col("roster_id") == 1).row(0, named=True)
    three = week2.filter(pl.col("roster_id") == 3).row(0, named=True)
    assert (one["sent_via_trade"], one["received_via_trade"]) == (25, 0)
    assert (three["received_via_trade"], three["remaining"]) == (25, 1025)


def test_chopped_roster_stops_at_its_elimination_week():
    faab = _faab().filter(pl.col("roster_id") == 3).sort("week")
    assert faab["week"].to_list() == [1, 2]
    assert faab["chopped"].to_list() == [False, True]


def test_balances_carry_forward_through_quiet_weeks():
    faab = _faab()
    assert faab.filter(pl.col("roster_id") == 2)["week"].to_list() == [1, 2, 3]


def test_manager_names_from_owner_ids():
    assert set(_faab()["manager"]) == {"manager1", "manager2", "manager3"}


def test_faab_check_agrees_with_sleeper():
    assert faab_check(_faab(), LEAGUES, _chopped_rosters()).height == 0


def test_faab_check_reports_a_mismatch():
    mismatched = _chopped_rosters({1: 200, 2: 50, 3: -25})
    report = faab_check(_faab(), LEAGUES, mismatched)
    assert report.select("roster_id", "rebuilt_used", "waiver_budget_used").rows() == [
        (1, 125, 200)
    ]


def test_a_normal_league_raises():
    normal = LEAGUES.with_columns(pl.lit(json.dumps({"type": 0})).alias("settings"))
    with pytest.raises(ChoppedLeagueError):
        faab_by_week(TRANSACTIONS, normal, _chopped_rosters(), STANDINGS)


def test_render_faab_remaining_writes_a_png(tmp_path):
    out_path = render_faab_remaining(_faab(), tmp_path / "faab.png")
    assert out_path.stat().st_size > 0
