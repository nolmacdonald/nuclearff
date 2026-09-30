"""Unit tests for nuclearff.chopped.claims (issues #225, #227)."""

from __future__ import annotations

import json
from datetime import datetime

import polars as pl
import pytest

from nuclearff.chopped.claims import (
    ACTIVITY_COLUMNS,
    CLAIM_COLUMNS,
    OUTBID_NOTE,
    OUTCOME_COLUMNS,
    OVER_BUDGET_NOTE,
    ROSTER_FULL_NOTE,
    bid_contests,
    bid_outcomes,
    claim_activity,
    orphan_losses,
    waiver_claims,
)
from nuclearff.report.chopped import render_bid_outcomes, render_claim_activity

LEAGUE_ID = "chop1"
WON_NOTE = "Your waiver claim was processed successfully!"


def _tx(
    tid, week, type_, status, roster, *, bid=None, note=None, adds=("p1",), day=None
):
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
        "status_updated_at": datetime(2025, 9, day) if day else None,
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


# -------------------------------------------------------------------------------------
# BID OUTCOMES (#227)
# -------------------------------------------------------------------------------------


def _bid(tid, week, roster, player, bid, outcome, *, day=None):
    note = {
        "won": WON_NOTE,
        "outbid": OUTBID_NOTE,
        "roster_full": ROSTER_FULL_NOTE,
    }[outcome]
    status = "complete" if outcome == "won" else "failed"
    return _tx(
        tid, week, "waiver", status, roster, bid=bid, note=note, adds=(player,), day=day
    )


CONTESTS = pl.DataFrame(
    [
        # Contest a: m1 wins at 50, m2 is the runner-up (40), m3 bid 10.
        _bid("a1", 1, 1, "a", 50, "won"),
        _bid("a2", 1, 2, "a", 40, "outbid"),
        _bid("a3", 1, 3, "a", 10, "outbid"),
        # Contest b: m2 ties m1's $0 bid and loses on waiver order.
        _bid("b1", 1, 1, "b", 0, "won"),
        _bid("b2", 1, 2, "b", 0, "outbid"),
        # Contest c: m3 ties m1's 25 bid, above $0.
        _bid("c1", 2, 1, "c", 25, "won"),
        _bid("c2", 2, 3, "c", 25, "outbid"),
        # Contest d: uncontested win. m2's second claim loses to their own.
        _bid("d1", 2, 2, "d", 100, "won"),
        _bid("d2", 2, 2, "d", 100, "outbid"),
        # Contest e: m2 and m3 tie for second, both behind m1.
        _bid("e1", 3, 1, "e", 90, "won"),
        _bid("e2", 3, 2, "e", 60, "outbid"),
        _bid("e3", 3, 3, "e", 60, "outbid"),
        # Contest f: two waiver runs in one week, won by different managers.
        _bid("f1", 4, 1, "f", 5, "won", day=1),
        _bid("f2", 4, 2, "f", 7, "won", day=3),
        _bid("f3", 4, 3, "f", 6, "outbid", day=3),
        # Not a bidding contest.
        _bid("g1", 4, 3, "g", 10, "roster_full"),
        _bid("g2", 4, 1, "h", 10, "roster_full"),
    ]
)

ORPHAN = pl.DataFrame([_bid("z1", 5, 1, "z", 20, "outbid")])


def _outcomes(transactions=CONTESTS, **kwargs) -> dict[str, dict]:
    claims = waiver_claims(transactions, LEAGUES, STANDINGS)
    frame = bid_outcomes(claims, **kwargs)
    return {row["owner_id"]: row for row in frame.iter_rows(named=True)}


def test_every_contest_has_exactly_one_winning_claim():
    contests = bid_contests(waiver_claims(CONTESTS, LEAGUES, STANDINGS))
    winners = contests.filter(pl.col("outcome") == "won").group_by("run", "player_id")
    assert set(winners.len()["len"]) == {1}
    assert orphan_losses(contests).height == 0


def test_a_managers_claim_that_lost_to_their_own_is_not_a_bid_lost():
    contests = bid_contests(waiver_claims(CONTESTS, LEAGUES, STANDINGS))
    d = contests.filter(pl.col("player_id") == "d")
    assert d["outcome"].to_list() == ["won"]
    assert not d["contested"].item()


def test_win_rate_and_contested_wins():
    out = _outcomes()
    one = out["u1"]
    assert (one["bids_decided"], one["bids_won"]) == (5, 5)
    assert one["pct_bids_won"] == 1.0
    assert (one["won_contested"], one["won_uncontested"]) == (4, 1)
    two = out["u2"]
    # a, b, e lost; d and f2 won. f2 is contested by m3.
    assert (two["bids_decided"], two["bids_won"], two["outbid"]) == (5, 2, 3)
    assert two["pct_bids_won"] == pytest.approx(0.4)
    assert (two["won_contested"], two["won_uncontested"]) == (1, 1)


def test_runner_up_losses_count_the_highest_losing_bids_including_ties():
    out = _outcomes()
    # m2: runner-up in a (40), b (0, only loser) and e (60, tied with m3).
    assert out["u2"]["runner_up_losses"] == 3
    # m3: not the runner-up in a (10 < 40), but is in c (25), e (60), f (6).
    assert out["u3"]["runner_up_losses"] == 3


def test_tied_losses_split_zero_from_above_zero():
    out = _outcomes()
    assert (out["u2"]["tied_losses"], out["u2"]["tied_losses_at_zero"]) == (1, 1)
    assert (out["u3"]["tied_losses"], out["u3"]["tied_losses_at_zero"]) == (1, 0)
    assert out["u1"]["tied_losses"] == 0


def test_average_margin_is_winning_bid_minus_bid_over_runner_up_losses():
    out = _outcomes()
    # m2: a (50-40=10), b (0), e (90-60=30).
    assert out["u2"]["avg_margin_lost_by"] == pytest.approx(40 / 3)
    assert out["u1"]["avg_margin_lost_by"] is None


def test_waiver_runs_on_different_days_are_separate_contests():
    contests = bid_contests(waiver_claims(CONTESTS, LEAGUES, STANDINGS))
    f = contests.filter(pl.col("player_id") == "f")
    assert f.height == 3
    day_one = f.filter(pl.col("run") == "2025-09-01")
    assert day_one["owner_id"].to_list() == ["u1"]
    assert not day_one["contested"].item()
    day_three = f.filter(pl.col("run") == "2025-09-03")
    assert day_three["winning_bid"].unique().to_list() == [7]


def test_non_competing_claims_stay_out_of_the_denominator_unless_asked():
    default = _outcomes()["u1"]
    with_all = _outcomes(include_non_competing=True)["u1"]
    assert default["non_competing"] == 1
    assert default["bids_decided"] == 5
    assert default["pct_bids_won"] == 1.0
    assert with_all["pct_bids_won"] == pytest.approx(5 / 6)


def test_outbid_claim_without_a_winner_is_reported_not_dropped(caplog):
    claims = waiver_claims(ORPHAN, LEAGUES, STANDINGS)
    assert orphan_losses(bid_contests(claims)).height == 1
    with caplog.at_level("WARNING", logger="nuclearff.chopped.claims"):
        out = bid_outcomes(claims)
    assert "no winning claim" in caplog.text
    assert out.filter(pl.col("owner_id") == "u1")["outbid"].item() == 1


def test_outcome_columns_career_and_order():
    claims = waiver_claims(CONTESTS, LEAGUES, STANDINGS)
    frame = bid_outcomes(claims)
    assert frame.columns == ["league_id", "season", *OUTCOME_COLUMNS]
    assert frame["runner_up_losses"].to_list() == [3, 3, 0]  # unluckiest first
    assert frame["owner_id"][-1] == "u1"
    assert bid_outcomes(claims, career=True).columns == list(OUTCOME_COLUMNS)


def test_render_bid_outcomes_writes_a_png(tmp_path):
    frame = bid_outcomes(waiver_claims(CONTESTS, LEAGUES, STANDINGS))
    assert render_bid_outcomes(frame, tmp_path / "b.png").stat().st_size > 0
