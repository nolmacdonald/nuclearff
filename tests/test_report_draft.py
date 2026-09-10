"""Unit tests for nuclearff.report.draft (GitHub Issue 85)."""

from __future__ import annotations

import polars as pl

from nuclearff.report.draft import render_draft_order_table


def _stats() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "hyoga10"],
            "seasons_drafted": [6, 5],
            "avg_draft_position": [5.5, 7.2],
            "times_first_pick": [1, 0],
            "times_last_pick": [0, 2],
        }
    )


def test_render_draft_order_table_writes_a_png(tmp_path):
    out_path = tmp_path / "draft_order.png"

    result = render_draft_order_table(_stats(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_draft_order_table_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "draft_order.png"
    stats = pl.DataFrame(
        {
            "manager": ["nolmacdonald"],
            "seasons_drafted": [1],
            "avg_draft_position": [1.0],
            "times_first_pick": [1],
            "times_last_pick": [0],
        }
    )

    result = render_draft_order_table(stats, out_path)

    assert result.is_file()
