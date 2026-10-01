"""Unit tests for nuclearff.chopped.finishes (issue #229)."""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.chopped.finishes import FINISHES_COLUMNS, weekly_finishes
from nuclearff.report.chopped import render_weekly_finishes


def _week(week, scores, chopped, *, season=2025, league_id="L"):
    """Survival rows for one week from ``{owner: points}``."""
    ordered = sorted(scores.items(), key=lambda item: -item[1])
    rank, rows = 0, []
    for i, (owner, points) in enumerate(ordered):
        if i == 0 or points != ordered[i - 1][1]:
            rank = i + 1
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "week": week,
                "owner_id": owner,
                "manager": owner.upper(),
                "points": points,
                "rank": rank,
                "alive_count": len(scores),
                "chopped": owner == chopped,
            }
        )
    return rows


EIGHT = {o: float(100 - i * 10) for i, o in enumerate("abcdefgh")}  # a best, h worst


def test_counts_top_and_bottom_three():
    survival = pl.DataFrame(_week(1, EIGHT, chopped="h"))
    finishes = {
        r["owner_id"]: r for r in weekly_finishes(survival).iter_rows(named=True)
    }
    assert [finishes[o]["top3_weeks"] for o in "abcd"] == [1, 1, 1, 0]
    assert [finishes[o]["bottom3_weeks"] for o in "efgh"] == [0, 1, 1, 1]
    # f and g survived a bottom-3 week; h was chopped.
    assert [finishes[o]["close_calls"] for o in "fgh"] == [1, 1, 0]
    assert finishes["a"]["top3_rate"] == pytest.approx(1.0)


def test_weeks_below_the_minimum_field_are_not_counted():
    six = {o: EIGHT[o] for o in "abcdef"}
    survival = pl.DataFrame(_week(1, EIGHT, "h") + _week(2, six, "f"))
    finishes = {
        r["owner_id"]: r for r in weekly_finishes(survival).iter_rows(named=True)
    }
    assert finishes["a"]["weeks_counted"] == 1
    assert finishes["a"]["top3_weeks"] == 1
    assert finishes["f"]["close_calls"] == 1  # week 2 (chop) isn't counted
    counted = weekly_finishes(survival, min_alive=6)
    assert counted.filter(pl.col("owner_id") == "a")["weeks_counted"].item() == 2


def test_ties_count_every_tied_roster():
    tied = {**EIGHT, "d": 80.0, "e": 50.0}  # d ties c at 3rd, e ties f at 3rd-last
    survival = pl.DataFrame(_week(1, tied, chopped="h"))
    finishes = {
        r["owner_id"]: r for r in weekly_finishes(survival).iter_rows(named=True)
    }
    assert finishes["c"]["top3_weeks"] == finishes["d"]["top3_weeks"] == 1
    assert finishes["e"]["bottom3_weeks"] == finishes["f"]["bottom3_weeks"] == 1


def test_manager_with_no_counted_week_gets_zeros():
    six = {o: EIGHT[o] for o in "abcdef"}
    survival = pl.DataFrame(_week(1, six, "f"))
    finishes = weekly_finishes(survival)
    assert finishes.height == 6
    assert finishes["weeks_counted"].to_list() == [0] * 6
    assert finishes["top3_rate"].null_count() == 6


def test_columns_career_and_order():
    survival = pl.DataFrame(
        _week(1, EIGHT, "h") + _week(1, EIGHT, "h", season=2026, league_id="M")
    )
    per_season = weekly_finishes(survival)
    assert per_season.columns == ["league_id", "season", *FINISHES_COLUMNS]
    career = weekly_finishes(survival, career=True)
    assert career.columns == list(FINISHES_COLUMNS)
    assert career["owner_id"].to_list()[0] == "a"
    assert career.filter(pl.col("owner_id") == "a")["top3_weeks"].item() == 2


def test_render_weekly_finishes_writes_a_png(tmp_path):
    survival = pl.DataFrame(_week(1, EIGHT, chopped="h"))
    out_path = render_weekly_finishes(weekly_finishes(survival), tmp_path / "f.png")
    assert out_path.stat().st_size > 0
