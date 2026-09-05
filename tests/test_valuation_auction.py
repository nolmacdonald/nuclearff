"""Unit tests for nuclearff.valuation.auction: VORP -> dollars, keeper inflation.

Budget conservation is the property that matters here and it is checked
exactly, not approximately: a 10-team, 14-spot, $200 league must distribute
exactly $2,000 across its 140-player draft pool. The real league's confirmed
settings (`draft.settings.budget == 200`, 10 teams, 14 roster spots) are used
as the canonical fixture so these numbers mean something.
"""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.exceptions import ConfigError
from nuclearff.valuation.auction import (
    MIN_BID,
    auction_values,
    budget_from_draft,
    keeper_adjusted_values,
    keeper_inflation_multiplier,
)

TEAMS = 10
BUDGET = 200
ROSTER_SPOTS = 14
POOL_SIZE = TEAMS * ROSTER_SPOTS  # 140


def _valuations(n: int = 200) -> pl.DataFrame:
    """`n` players with linearly descending VORP, best first."""
    return pl.DataFrame(
        {
            "player_id": [f"p{i}" for i in range(n)],
            "vorp": [float(n - i) for i in range(n)],
        }
    )


def _priced(n: int = 200) -> pl.DataFrame:
    return auction_values(
        _valuations(n),
        teams=TEAMS,
        budget_per_team=BUDGET,
        roster_spots=ROSTER_SPOTS,
    )


# --- budget_from_draft ----------------------------------------------------


def test_budget_from_draft_reads_the_confirmed_field():
    """The real league's budget lives at draft.settings.budget (confirmed live)."""
    draft = {"type": "auction", "settings": {"budget": 200, "teams": 10}}

    assert budget_from_draft(draft) == 200


def test_budget_from_draft_rejects_a_snake_draft():
    """Every prior season of the real league was a snake draft with no budget."""
    draft = {"type": "snake", "settings": {"rounds": 15}}

    with pytest.raises(ConfigError, match="not 'auction'"):
        budget_from_draft(draft)


def test_budget_from_draft_refuses_to_assume_a_conventional_200():
    """A missing budget is surfaced, never silently defaulted (plan requirement)."""
    with pytest.raises(ConfigError, match="Do not assume"):
        budget_from_draft({"type": "auction", "settings": {}})

    with pytest.raises(ConfigError, match="Do not assume"):
        budget_from_draft({"type": "auction", "settings": {"budget": 0}})


def test_budget_from_draft_ignores_waiver_budget():
    """league.settings.waiver_budget is FAAB and must never stand in for it."""
    with pytest.raises(ConfigError):
        budget_from_draft({"type": "auction", "settings": {"waiver_budget": 100}})


# --- auction_values -------------------------------------------------------


def test_draft_pool_is_exactly_teams_times_roster_spots():
    result = _priced()

    assert result.filter(pl.col("in_draft_pool")).height == POOL_SIZE


def test_dollars_sum_exactly_to_the_league_budget_across_the_pool():
    """The property that makes these numbers real money: $2,000, to the cent."""
    result = _priced()
    pool_total = result.filter(pl.col("in_draft_pool"))["auction_value"].sum()

    assert pool_total == pytest.approx(TEAMS * BUDGET)


def test_players_outside_the_pool_are_minimum_bid_bodies():
    result = _priced()
    outside = result.filter(~pl.col("in_draft_pool"))

    assert outside.height == 200 - POOL_SIZE
    assert outside["auction_value"].to_list() == [float(MIN_BID)] * outside.height


def test_value_is_monotonic_in_vorp():
    """A better player is never cheaper than a worse one."""
    result = _priced().sort("vorp", descending=True)
    values = result["auction_value"].to_list()

    assert values == sorted(values, reverse=True)


def test_no_player_is_priced_below_the_minimum_bid():
    result = _priced()

    assert result["auction_value"].min() >= float(MIN_BID)


def test_negative_vorp_players_do_not_claw_back_dollars():
    """Clipping at zero means replacement-level players cost $1, not a credit."""
    frame = pl.DataFrame(
        {
            "player_id": ["a", "b", "c"],
            "vorp": [50.0, 0.0, -30.0],
        }
    )

    result = auction_values(frame, teams=1, budget_per_team=20, roster_spots=3)
    by_id = dict(zip(result["player_id"], result["auction_value"], strict=True))

    assert by_id["c"] == pytest.approx(float(MIN_BID))
    assert by_id["b"] == pytest.approx(float(MIN_BID))
    assert by_id["a"] > float(MIN_BID)


def test_null_vorp_is_treated_as_replacement_level():
    frame = pl.DataFrame(
        {"player_id": ["a", "b"], "vorp": [10.0, None]},
    )

    result = auction_values(frame, teams=1, budget_per_team=10, roster_spots=2)
    by_id = dict(zip(result["player_id"], result["auction_value"], strict=True))

    assert by_id["b"] == pytest.approx(float(MIN_BID))


def test_all_zero_vorp_prices_everyone_at_the_minimum_and_warns(caplog):
    """No positive VORP means nothing to distribute proportionally."""
    frame = pl.DataFrame({"player_id": ["a", "b"], "vorp": [0.0, 0.0]})

    with caplog.at_level("WARNING", logger="nuclearff.valuation.auction"):
        result = auction_values(frame, teams=1, budget_per_team=10, roster_spots=2)

    assert result["auction_value"].to_list() == [float(MIN_BID)] * 2
    assert any("minimum" in r.message for r in caplog.records)


def test_missing_column_raises():
    with pytest.raises(ValueError, match="auction_values.*vorp"):
        auction_values(
            pl.DataFrame({"player_id": ["a"]}),
            teams=TEAMS,
            budget_per_team=BUDGET,
            roster_spots=ROSTER_SPOTS,
        )


def test_non_positive_league_parameters_raise():
    with pytest.raises(ValueError, match="must all"):
        auction_values(
            _valuations(5), teams=0, budget_per_team=BUDGET, roster_spots=ROSTER_SPOTS
        )


# --- keeper inflation -----------------------------------------------------


def test_no_keepers_means_no_inflation():
    assert (
        keeper_inflation_multiplier(_priced(), {}, teams=TEAMS, budget_per_team=BUDGET)
        == 1.0
    )


def test_keepers_held_below_market_inflate_everyone_else():
    """Three studs kept at $1 each leaves nearly the whole budget chasing less."""
    priced = _priced()
    cheap_keepers = {"p0": 1.0, "p1": 1.0, "p2": 1.0}

    multiplier = keeper_inflation_multiplier(
        priced, cheap_keepers, teams=TEAMS, budget_per_team=BUDGET
    )

    assert multiplier > 1.0


def test_keepers_at_full_price_do_not_inflate_much():
    """Keeping players at roughly market value is close to inflation-neutral."""
    priced = _priced()
    by_id = dict(zip(priced["player_id"], priced["auction_value"], strict=True))
    at_market = {pid: by_id[pid] for pid in ("p0", "p1", "p2")}

    multiplier = keeper_inflation_multiplier(
        priced, at_market, teams=TEAMS, budget_per_team=BUDGET
    )

    assert multiplier == pytest.approx(1.0, abs=0.05)


def test_inflation_log_line_formats_at_info_level(caplog):
    """Regression guard: `%,.0f` is invalid printf-style and blew up in logging."""
    with caplog.at_level("INFO", logger="nuclearff.valuation.auction"):
        keeper_inflation_multiplier(
            _priced(), {"p0": 1.0}, teams=TEAMS, budget_per_team=BUDGET
        )

    # .message is the raw template; .getMessage() actually applies the args,
    # which is where a bad format specifier raises.
    assert any("Keeper inflation" in r.getMessage() for r in caplog.records)


def test_keepers_consuming_the_whole_budget_raises():
    priced = _priced()

    with pytest.raises(ValueError, match="nothing left to inflate"):
        keeper_inflation_multiplier(
            priced, {"p0": 2000.0}, teams=TEAMS, budget_per_team=BUDGET
        )


def test_keeper_adjusted_values_charge_keepers_their_contract_not_market():
    priced = _priced()
    keepers = {"p0": 5.0, "p1": 7.0}

    result = keeper_adjusted_values(
        priced, keepers, teams=TEAMS, budget_per_team=BUDGET
    )
    by_id = dict(
        zip(
            result["player_id"],
            result["auction_value_keeper_adjusted"],
            strict=True,
        )
    )

    assert by_id["p0"] == pytest.approx(5.0)
    assert by_id["p1"] == pytest.approx(7.0)


def test_keeper_adjusted_values_flag_who_is_kept():
    result = keeper_adjusted_values(
        _priced(), {"p0": 5.0}, teams=TEAMS, budget_per_team=BUDGET
    )
    by_id = dict(zip(result["player_id"], result["is_keeper"], strict=True))

    assert by_id["p0"] is True
    assert by_id["p5"] is False


def test_keeper_adjusted_values_keep_the_baseline_visible():
    """Both numbers survive so the inflation effect is legible, not buried."""
    result = keeper_adjusted_values(
        _priced(), {"p0": 1.0}, teams=TEAMS, budget_per_team=BUDGET
    )

    assert "auction_value" in result.columns
    assert "auction_value_keeper_adjusted" in result.columns

    non_keeper = result.filter(pl.col("player_id") == "p50")
    assert (
        non_keeper["auction_value_keeper_adjusted"][0] > non_keeper["auction_value"][0]
    )


def test_keeper_adjusted_values_with_no_keepers_equals_baseline():
    result = keeper_adjusted_values(_priced(), {}, teams=TEAMS, budget_per_team=BUDGET)

    assert result["auction_value_keeper_adjusted"].to_list() == pytest.approx(
        result["auction_value"].to_list()
    )
