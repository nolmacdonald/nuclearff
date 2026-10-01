"""Unit tests for nuclearff.chopped.faab (issue #221)."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.chopped.faab import (
    FAAB_COLUMNS,
    SPEND_COLUMNS,
    faab_by_week,
    faab_check,
    league_burndown,
    spend_checkpoints,
)
from nuclearff.exceptions import ChoppedLeagueError
from nuclearff.report.chopped import (
    render_faab_remaining,
    render_league_burndown,
    render_spend_leaderboard,
)

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


# --- spend_checkpoints / league_burndown (#224) -----------------------------------


def _spend(status="complete", **kwargs) -> pl.DataFrame:
    leagues = LEAGUES.with_columns(pl.lit(status).alias("status"))
    return spend_checkpoints(_faab(), leagues, **kwargs)


def _spent(spend, owner, through_week, total=False):
    return spend.filter(
        (pl.col("owner_id") == owner)
        & (pl.col("through_week") == through_week)
        & (pl.col("is_season_total") == total)
    ).row(0, named=True)


def test_spend_counts_winning_bids_not_trades():
    spend = _spend(checkpoints=(1, 2))
    assert spend.columns == list(SPEND_COLUMNS)
    assert _spent(spend, "u1", 1)["spent"] == 100
    # Week 2's $25 trade isn't spending.
    assert _spent(spend, "u1", 2)["spent"] == 100
    assert _spent(spend, "u1", 3, total=True)["spent"] == 100
    assert _spent(spend, "u1", 1)["pct_of_budget"] == pytest.approx(0.1)


def test_spend_rank_and_chopped_week():
    spend = _spend(checkpoints=(2,))
    assert _spent(spend, "u1", 2)["rank"] == 1
    assert _spent(spend, "u2", 2)["rank"] == 2
    assert _spent(spend, "u3", 2)["chopped_week"] == 2
    assert _spent(spend, "u3", 2)["spent"] == 0


def test_checkpoint_past_the_last_week():
    # Finished season: week 16 equals the season total.
    done = _spent(_spend(checkpoints=(16,)), "u1", 16)
    assert (done["reached"], done["spent"]) == (True, 100)
    # Season in progress: week 16 hasn't happened.
    live = _spent(_spend("in_season", checkpoints=(16,)), "u1", 16)
    assert (live["reached"], live["spent"], live["rank"]) == (False, None, None)


def test_alive_only_drops_chopped_managers():
    spend = _spend(checkpoints=(2,), alive_only=True)
    assert "u3" not in spend.filter(pl.col("through_week") == 2)["owner_id"].to_list()


def test_league_burndown_bands_sum_to_the_starting_budget():
    burndown = league_burndown(_faab(), LEAGUES)
    assert burndown["week"].to_list() == [0, 1, 2, 3]
    assert set(burndown["total"]) == {3000}
    week2 = burndown.filter(pl.col("week") == 2).row(0, named=True)
    # Roster 3 was chopped in week 2 holding $1,025 (it received $25).
    assert week2["lost_to_chop"] == 1025
    assert week2["spent"] == 150
    assert week2["held_by_alive"] == 875 + 950


def test_spend_and_burndown_renders_write_pngs(tmp_path):
    assert render_spend_leaderboard(_spend(), tmp_path / "s.png").stat().st_size
    burndown = league_burndown(_faab(), LEAGUES)
    assert render_league_burndown(burndown, tmp_path / "b.png").stat().st_size
