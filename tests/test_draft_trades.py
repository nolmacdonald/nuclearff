"""Unit tests for nuclearff.draft.trades (issue #104).

The league is the committed Sleeper fixture. Its replacement ranks are deeper
than the small pools below, so each position's replacement level falls back to
its worst-ranked player (VORP 0.0), which keeps every expected value
hand-computable.
"""

from __future__ import annotations

import logging

import polars as pl
import pytest

from nuclearff.config.league import league_config_from_sleeper
from nuclearff.draft.trades import FAIR_TOLERANCE, grade_pick_trade


def _projections() -> pl.DataFrame:
    # WR VORP: 50, 40, 30, 20, 10, 0.  RB VORP: 30, 20, 10, 0.
    # Board by VORP (pick 1..10): 50, 40, 30, 30, 20, 20, 10, 10, 0, 0.
    wr = [("wr1", 100.0), ("wr2", 90.0), ("wr3", 80.0), ("wr4", 70.0)]
    wr += [("wr5", 60.0), ("wr6", 50.0)]
    rb = [("rb1", 95.0), ("rb2", 85.0), ("rb3", 75.0), ("rb4", 65.0)]
    return pl.DataFrame(
        [(pid, "WR" if pid.startswith("wr") else "RB", pts) for pid, pts in wr + rb],
        schema={
            "player_id": pl.String,
            "position": pl.String,
            "proj_points": pl.Float64,
        },
        orient="row",
    )


@pytest.fixture
def cfg(league_payload):
    return league_config_from_sleeper(league_payload)


def test_early_pick_for_two_late_picks_is_unfavorable(cfg):
    grade = grade_pick_trade([1], [7, 8], _projections(), cfg)

    assert grade.given == {1: 50.0}
    assert grade.received == {7: 10.0, 8: 10.0}
    assert grade.differential == pytest.approx(-30.0)
    assert grade.label == "unfavorable"


def test_the_reverse_trade_is_favorable(cfg):
    grade = grade_pick_trade([7, 8], [1], _projections(), cfg)

    assert grade.differential == pytest.approx(30.0)
    assert grade.label == "favorable"


def test_equal_value_picks_are_fair(cfg):
    grade = grade_pick_trade([3], [4], _projections(), cfg)

    assert grade.differential == 0.0
    assert grade.label == "fair"


def test_the_fair_band_is_adjustable(cfg):
    # Gives 50, receives 30 + 10 = 40: a 20% gap against the larger side.
    default = grade_pick_trade([1], [3, 7], _projections(), cfg)
    wide = grade_pick_trade([1], [3, 7], _projections(), cfg, tolerance=0.25)

    assert FAIR_TOLERANCE == 0.10
    assert default.label == "unfavorable"
    assert wide.label == "fair"
    assert wide.tolerance == 0.25


def test_two_worthless_sides_are_fair(cfg):
    assert grade_pick_trade([9], [10], _projections(), cfg).label == "fair"


def test_drafted_players_leave_the_board_and_shift_pick_numbers(cfg):
    grade = grade_pick_trade([2], [4], _projections(), cfg, drafted_player_ids={"wr1"})

    # Board without wr1: 40, 30, 30, 20, ... so pick 2 is rank 1, pick 4 rank 3.
    assert grade.given == {2: 40.0}
    assert grade.received == {4: 30.0}


def test_a_pick_already_made_is_rejected(cfg):
    with pytest.raises(ValueError, match="already been made"):
        grade_pick_trade([1], [4], _projections(), cfg, drafted_player_ids={"wr1"})


def test_picks_below_replacement_are_floored_and_a_deep_pick_warns(cfg, caplog):
    deep = pl.DataFrame(
        {
            "player_id": [f"wr{i}" for i in range(1, 41)] + ["rb1", "rb2"],
            "position": ["WR"] * 40 + ["RB", "RB"],
            "proj_points": [float(x) for x in range(100, 60, -1)] + [80.0, 70.0],
        }
    )

    with caplog.at_level(logging.WARNING):
        grade = grade_pick_trade([1], [42, 50], deep, cfg)

    # Last of 42 is wr40 at VORP -5, floored to 0.0; pick 50 falls back to it.
    assert grade.received == {42: 0.0, 50: 0.0}
    assert grade.given[1] == pytest.approx(34.0)
    assert "only 42 valued" in caplog.text


@pytest.mark.parametrize(
    ("given", "received", "kwargs", "message"),
    [
        ([], [2], {}, "both sides"),
        ([1], [], {}, "both sides"),
        ([0], [2], {}, "1 or greater"),
        ([2], [2], {}, "cannot be on both"),
        ([1], [2], {"tolerance": -0.1}, "tolerance"),
    ],
)
def test_invalid_inputs_are_rejected(cfg, given, received, kwargs, message):
    with pytest.raises(ValueError, match=message):
        grade_pick_trade(given, received, _projections(), cfg, **kwargs)


def test_projections_without_a_skill_position_are_rejected(cfg):
    kickers = pl.DataFrame(
        {"player_id": ["k1"], "position": ["K"], "proj_points": [100.0]}
    )

    with pytest.raises(ValueError, match="no players at any"):
        grade_pick_trade([1], [2], kickers, cfg)


def test_missing_projection_column_is_rejected(cfg):
    with pytest.raises(ValueError, match="grade_pick_trade"):
        grade_pick_trade([1], [2], _projections().drop("proj_points"), cfg)
