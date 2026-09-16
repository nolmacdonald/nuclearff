"""Smoke tests for nuclearff.report.on_this_day: PNG table rendering."""

from __future__ import annotations

from datetime import date

import polars as pl

from nuclearff.report.on_this_day import render_on_this_day_table


def _summary_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "year": [2023, 2021],
            "type": ["trade", "waiver"],
            "parties": ["Nolan, Mike", "Nolan"],
            "summary": ["added Player A; dropped Player B", "added Player C"],
            "group_id": ["t1", "t2"],
        }
    )


def test_render_on_this_day_table_writes_a_png(tmp_path):
    out_path = tmp_path / "on_this_day.png"

    result = render_on_this_day_table(_summary_rows(), date(2026, 9, 10), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_on_this_day_table_handles_empty_result(tmp_path):
    out_path = tmp_path / "on_this_day.png"
    empty = _summary_rows().clear()

    result = render_on_this_day_table(empty, date(2026, 1, 1), out_path)

    assert result.is_file()
    assert result.stat().st_size > 0


def test_render_on_this_day_table_handles_a_single_match(tmp_path):
    out_path = tmp_path / "on_this_day.png"
    one_row = _summary_rows().head(1)

    result = render_on_this_day_table(one_row, date(2026, 9, 10), out_path)

    assert result.is_file()


def test_render_on_this_day_table_handles_long_summary_and_type_text(tmp_path):
    """Regression test: a real 51-char summary and a real "free_agent" type
    (vs. "waiver"/"trade") used to wrap and overlap the row below before the
    column widths were sized to the longest string actually present."""
    out_path = tmp_path / "on_this_day.png"
    rows = pl.DataFrame(
        {
            "year": [2024],
            "type": ["free_agent"],
            "parties": ["nawfeastdallas"],
            "summary": ["added Jacory Croskey-Merritt; dropped Brandin Cooks Extended"],
            "group_id": ["t1"],
        }
    )

    result = render_on_this_day_table(rows, date(2026, 5, 6), out_path)

    assert result.is_file()
    assert result.stat().st_size > 0


def test_render_on_this_day_table_handles_a_long_trade_summary(tmp_path):
    """Regression test: an unbounded width for a real multi-player,
    multi-pick trade summary used to starve every other column's real
    share of the same fixed axes width down to an overlapping sliver."""
    out_path = tmp_path / "on_this_day.png"
    rows = pl.DataFrame(
        {
            "year": [2026, 2026],
            "type": ["trade", "trade"],
            "parties": ["nolmacdonald", "jwhitney0220"],
            "summary": [
                "received Troy Franklin, Tyler Warren, J.K. Dobbins, Zach "
                "Charbonnet; gave up 2 draft picks",
                "received 2 draft picks; gave up Troy Franklin, Tyler "
                "Warren, J.K. Dobbins, Zach Charbonnet",
            ],
            "group_id": ["t1", "t1"],
        }
    )

    result = render_on_this_day_table(rows, date(2026, 4, 27), out_path)

    assert result.is_file()
    assert result.stat().st_size > 0


def test_render_on_this_day_table_groups_a_multi_party_trade(tmp_path):
    """A 3-way trade (3 rows sharing one group_id) doesn't crash the
    grouping/blanking logic -- not just the common 2-party case."""
    out_path = tmp_path / "on_this_day.png"
    rows = pl.DataFrame(
        {
            "year": [2026, 2026, 2026],
            "type": ["trade", "trade", "trade"],
            "parties": ["A", "B", "C"],
            "summary": ["received X", "received Y", "received Z"],
            "group_id": ["t1", "t1", "t1"],
        }
    )

    result = render_on_this_day_table(rows, date(2026, 4, 27), out_path)

    assert result.is_file()
