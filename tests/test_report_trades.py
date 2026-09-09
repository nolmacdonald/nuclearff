"""Unit tests for nuclearff.report.trades: manager trade-network rendering.

``render_trades_by_manager`` (issue #42), ``render_trades_heatmap`` (issue
#43), ``render_trade_network`` (issue #44), and ``render_trade_leaderboard``
(issue #46) exist so far; more render functions land here as issues #47-#51
merge.
"""

from __future__ import annotations

import polars as pl

from nuclearff.report.trades import (
    render_trade_leaderboard,
    render_trade_network,
    render_trades_by_manager,
    render_trades_heatmap,
)


def _counts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "Donkeysride", "hyoga10"],
            "trades": [2, 2, 0],
        }
    )


def _leaderboard_counts() -> pl.DataFrame:
    """A densified `manager_trade_counts` frame, like the CLI builds for #46."""
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "Donkeysride", "hyoga10"],
            "trades": [2, 2, 0],
            "unique_partners": [1, 1, 0],
            "most_frequent_partner": ["Donkeysride", "nolmacdonald", None],
            "trades_with_partner": [2, 2, 0],
        }
    )


def _matrix() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["Donkeysride", "hyoga10", "nolmacdonald"],
            "Donkeysride": [0, 0, 2],
            "hyoga10": [0, 0, 0],
            "nolmacdonald": [2, 0, 0],
        }
    )


def test_render_trades_by_manager_writes_a_png(tmp_path):
    out_path = tmp_path / "trades_by_manager.png"

    result = render_trades_by_manager(_counts(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_trades_by_manager_handles_a_zero_trade_manager(tmp_path):
    """Issue #42's acceptance criterion: a 0-trade manager must not crash the chart."""
    out_path = tmp_path / "trades_by_manager.png"

    result = render_trades_by_manager(_counts(), out_path)

    assert result.is_file()


def test_render_trades_by_manager_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "trades_by_manager.png"
    counts = pl.DataFrame({"manager": ["nolmacdonald"], "trades": [5]})

    result = render_trades_by_manager(counts, out_path)

    assert result.is_file()


def test_render_trades_heatmap_writes_a_png(tmp_path):
    out_path = tmp_path / "trades_heatmap.png"

    result = render_trades_heatmap(_matrix(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_trades_heatmap_handles_a_zero_trade_manager(tmp_path):
    """Issue #43: dense over every manager, not just observed pairs."""
    out_path = tmp_path / "trades_heatmap.png"

    result = render_trades_heatmap(_matrix(), out_path)

    assert result.is_file()


def test_render_trades_heatmap_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "trades_heatmap.png"
    matrix = pl.DataFrame({"manager": ["nolmacdonald"], "nolmacdonald": [0]})

    result = render_trades_heatmap(matrix, out_path)

    assert result.is_file()


def test_render_trade_network_writes_a_png(tmp_path):
    out_path = tmp_path / "trade_network.png"

    result = render_trade_network(_counts(), _matrix(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_trade_network_handles_a_zero_trade_manager(tmp_path):
    """Issue #44: a 0-trade manager must still appear, as an isolated node."""
    out_path = tmp_path / "trade_network.png"

    result = render_trade_network(_counts(), _matrix(), out_path)

    assert result.is_file()


def test_render_trade_network_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "trade_network.png"
    counts = pl.DataFrame({"manager": ["nolmacdonald"], "trades": [0]})
    matrix = pl.DataFrame({"manager": ["nolmacdonald"], "nolmacdonald": [0]})

    result = render_trade_network(counts, matrix, out_path)

    assert result.is_file()


def test_render_trade_leaderboard_writes_a_png(tmp_path):
    out_path = tmp_path / "trade_leaderboard.png"

    result = render_trade_leaderboard(_leaderboard_counts(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_trade_leaderboard_handles_a_zero_trade_manager(tmp_path):
    """Issue #46: a 0-trade manager still gets a row, not an error."""
    out_path = tmp_path / "trade_leaderboard.png"

    result = render_trade_leaderboard(_leaderboard_counts(), out_path)

    assert result.is_file()


def test_render_trade_leaderboard_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "trade_leaderboard.png"
    counts = pl.DataFrame(
        {
            "manager": ["nolmacdonald"],
            "trades": [0],
            "unique_partners": [0],
            "most_frequent_partner": [None],
            "trades_with_partner": [0],
        }
    )

    result = render_trade_leaderboard(counts, out_path)

    assert result.is_file()
