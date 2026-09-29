"""Unit tests for nuclearff.chopped.claims (issue #225)."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.chopped.claims import (
    ACTIVITY_COLUMNS,
    CLAIM_COLUMNS,
    OUTBID_NOTE,
    OVER_BUDGET_NOTE,
    ROSTER_FULL_NOTE,
    claim_activity,
    waiver_claims,
)
from nuclearff.report.chopped import render_claim_activity

LEAGUE_ID = "chop1"
WON_NOTE = "Your waiver claim was processed successfully!"


def _tx(tid, week, type_, status, roster, *, bid=None, note=None, adds=("p1",)):
    return {
        "transaction_id": tid,
        "league_id": LEAGUE_ID,
        "season": 2025,
        "week": week,
        "type": type_,
        "status": status,
        "roster_ids": json.dumps([roster]),
        "adds": json.dumps(dict.fromkeys(adds, roster)) if adds else None,
        "settings": json.dumps({"waiver_bid": bid}) if bid is not None else None,
        "metadata": json.dumps({"notes": note}) if note else None,
    }


TRANSACTIONS = pl.DataFrame(
    [
        _tx("t1", 1, "waiver", "complete", 1, bid=50, note=WON_NOTE),
        _tx("t2", 1, "waiver", "failed", 2, bid=40, note=OUTBID_NOTE),
        _tx(
            "t3", 1, "waiver", "failed", 2, bid=10, note=ROSTER_FULL_NOTE, adds=("p2",)
        ),
        _tx(
            "t4", 2, "waiver", "failed", 2, bid=900, note=OVER_BUDGET_NOTE, adds=("p3",)
        ),
        _tx("t5", 2, "waiver", "failed", 1, bid=5, note="Something new", adds=("p4",)),
        # One claim adding two players.
        _tx("t6", 2, "waiver", "complete", 1, bid=20, note=WON_NOTE, adds=("p5", "p6")),
        _tx("t7", 2, "free_agent", "complete", 2, adds=("p7",)),
        _tx("t8", 2, "chopped", "complete", 3, adds=None),
    ]
)

LEAGUES = pl.DataFrame(
    [
        {
            "league_id": LEAGUE_ID,
            "season": 2025,
            "total_rosters": 3,
            "status": "complete",
            "settings": json.dumps(
                {"type": 3, "last_chopped_leg": 2, "waiver_budget": 1000}
            ),
        }
    ]
)

CHOPPED_ROSTERS = pl.DataFrame(
    [
        {"league_id": LEAGUE_ID, "roster_id": 1, "eliminated_leg": None},
        {"league_id": LEAGUE_ID, "roster_id": 2, "eliminated_leg": None},
        {"league_id": LEAGUE_ID, "roster_id": 3, "eliminated_leg": 1},
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


def _claims() -> pl.DataFrame:
    return waiver_claims(TRANSACTIONS, LEAGUES, STANDINGS)


def _activity(**kwargs) -> dict[str, dict]:
    frame = claim_activity(
        _claims(), TRANSACTIONS, LEAGUES, CHOPPED_ROSTERS, STANDINGS, **kwargs
    )
    return {row["owner_id"]: row for row in frame.iter_rows(named=True)}


def test_one_row_per_claimed_player_with_outcome(caplog):
    with caplog.at_level("WARNING", logger="nuclearff.chopped.claims"):
        claims = _claims()
    assert claims.columns == list(CLAIM_COLUMNS)
    outcomes = dict(claims.select("transaction_id", "outcome").unique().iter_rows())
    assert outcomes == {
        "t1": "won",
        "t2": "outbid",
        "t3": "roster_full",
        "t4": "over_budget",
        "t5": "other",
        "t6": "won",
    }
    assert "Something new" in caplog.text


def test_multi_add_claim_gives_one_row_per_player_with_the_same_bid():
    t6 = _claims().filter(pl.col("transaction_id") == "t6")
    assert sorted(t6["player_id"]) == ["p5", "p6"]
    assert set(t6["bid"]) == {20}


def test_losing_bid_amounts_are_kept_and_chopped_transactions_excluded():
    claims = _claims()
    assert claims.filter(pl.col("transaction_id") == "t2")["bid"].item() == 40
    assert "t8" not in claims["transaction_id"].to_list()
    assert "t7" not in claims["transaction_id"].to_list()


def test_activity_counts_and_failure_reasons():
    activity = _activity()
    one, two = activity["u1"], activity["u2"]
    assert (one["claims_placed"], one["players_bid_on"]) == (3, 4)
    assert (one["claims_won"], one["claims_failed"], one["other_failed"]) == (2, 1, 1)
    assert (two["claims_placed"], two["claims_won"]) == (3, 0)
    assert (two["outbid"], two["roster_full"], two["over_budget"]) == (1, 1, 1)
    assert two["free_agent_adds"] == 1
    assert two["avg_bid"] == pytest.approx((40 + 10 + 900) / 3)


def test_every_manager_gets_a_row_and_weeks_alive():
    activity = _activity()
    assert activity["u3"]["claims_placed"] == 0
    assert activity["u3"]["weeks_alive"] == 1  # chopped in week 1
    assert activity["u1"]["weeks_alive"] == 2
    assert activity["u1"]["claims_per_week_alive"] == pytest.approx(1.5)


def test_columns_career_and_order():
    frame = claim_activity(_claims(), TRANSACTIONS, LEAGUES, CHOPPED_ROSTERS, STANDINGS)
    assert frame.columns == ["league_id", "season", *ACTIVITY_COLUMNS]
    assert frame["owner_id"].to_list()[-1] == "u3"
    career = claim_activity(
        _claims(), TRANSACTIONS, LEAGUES, CHOPPED_ROSTERS, STANDINGS, career=True
    )
    assert career.columns == list(ACTIVITY_COLUMNS)


def test_render_claim_activity_writes_a_png(tmp_path):
    frame = claim_activity(_claims(), TRANSACTIONS, LEAGUES, CHOPPED_ROSTERS, STANDINGS)
    assert render_claim_activity(frame, tmp_path / "c.png").stat().st_size > 0
