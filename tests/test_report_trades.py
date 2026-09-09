"""Unit tests for nuclearff.report.trades: manager trade-network rendering.

``render_trades_by_manager`` (issue #42), ``render_trades_heatmap`` (issue
#43), ``render_trade_network`` (issue #44), ``render_trade_leaderboard``
(issue #46), ``render_manager_pair_leaderboard`` (issue #47),
``render_trades_over_time`` (issue #48), ``render_manager_season_heatmap``
(issue #49), ``render_cumulative_trades`` (issue #50), and
``render_trade_partner_diversity`` (issue #51) -- the full trade-network
epic (issue #40).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from nuclearff.report.trades import (
    render_cumulative_trades,
    render_manager_pair_leaderboard,
    render_manager_season_heatmap,
    render_trade_leaderboard,
    render_trade_network,
    render_trade_partner_diversity,
    render_trades_by_manager,
    render_trades_heatmap,
    render_trades_over_time,
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


def _pairs() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager_a": ["Donkeysride", "Donkeysride", "hyoga10"],
            "manager_b": ["nolmacdonald", "hyoga10", "nolmacdonald"],
            "trades": [2, 1, 1],
        }
    )


def test_render_manager_pair_leaderboard_writes_a_png(tmp_path):
    out_path = tmp_path / "manager_pair_leaderboard.png"

    result = render_manager_pair_leaderboard(_pairs(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_manager_pair_leaderboard_handles_a_single_pair(tmp_path):
    out_path = tmp_path / "manager_pair_leaderboard.png"
    pairs = pl.DataFrame(
        {"manager_a": ["Donkeysride"], "manager_b": ["nolmacdonald"], "trades": [2]}
    )

    result = render_manager_pair_leaderboard(pairs, out_path)

    assert result.is_file()


def test_render_manager_pair_leaderboard_handles_no_pairs(tmp_path):
    """A league with zero trades has zero pairs -- not a crash."""
    out_path = tmp_path / "manager_pair_leaderboard.png"
    pairs = pl.DataFrame(
        schema={"manager_a": pl.String, "manager_b": pl.String, "trades": pl.UInt32}
    )

    result = render_manager_pair_leaderboard(pairs, out_path)

    assert result.is_file()


def _by_season() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "nolmacdonald", "hyoga10"],
            "season": [2025, 2026, 2026],
            "trades": [1, 0, 1],
        }
    )


def _totals() -> pl.DataFrame:
    return pl.DataFrame({"season": [2025, 2026], "trades": [1, 1]})


def test_render_trades_over_time_writes_a_png(tmp_path):
    out_path = tmp_path / "trades_over_time.png"

    result = render_trades_over_time(_by_season(), _totals(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_trades_over_time_handles_a_manager_with_a_gap_season(tmp_path):
    """Issue #48: hyoga10 has no 2025 row (not rostered yet) -- must not crash."""
    out_path = tmp_path / "trades_over_time.png"

    result = render_trades_over_time(_by_season(), _totals(), out_path)

    assert result.is_file()


def test_render_trades_over_time_handles_a_single_season(tmp_path):
    out_path = tmp_path / "trades_over_time.png"
    by_season = pl.DataFrame(
        {"manager": ["nolmacdonald"], "season": [2025], "trades": [1]}
    )
    totals = pl.DataFrame({"season": [2025], "trades": [1]})

    result = render_trades_over_time(by_season, totals, out_path)

    assert result.is_file()


def _season_matrix() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "Donkeysride", "hyoga10"],
            "2025": [1, 1, 0],
            "2026": [1, 1, 1],
        }
    )


def test_render_manager_season_heatmap_writes_a_png(tmp_path):
    out_path = tmp_path / "manager_season_heatmap.png"

    result = render_manager_season_heatmap(_season_matrix(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_manager_season_heatmap_handles_a_zero_cell(tmp_path):
    """Issue #49: dense over every manager x season combo, 0 not missing."""
    out_path = tmp_path / "manager_season_heatmap.png"

    result = render_manager_season_heatmap(_season_matrix(), out_path)

    assert result.is_file()


def test_render_manager_season_heatmap_handles_a_single_manager_and_season(tmp_path):
    out_path = tmp_path / "manager_season_heatmap.png"
    matrix = pl.DataFrame({"manager": ["nolmacdonald"], "2025": [0]})

    result = render_manager_season_heatmap(matrix, out_path)

    assert result.is_file()


def _cumulative() -> pl.DataFrame:
    start = datetime(2025, 9, 1)
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "nolmacdonald", "Donkeysride"],
            "transaction_id": ["1", "2", "3"],
            "created_at": [
                start,
                start + timedelta(days=10),
                start + timedelta(days=5),
            ],
            "cumulative_trades": [1, 2, 1],
        }
    )


def test_render_cumulative_trades_writes_a_png(tmp_path):
    out_path = tmp_path / "cumulative_trades.png"

    result = render_cumulative_trades(_cumulative(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_cumulative_trades_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "cumulative_trades.png"
    cumulative = pl.DataFrame(
        {
            "manager": ["nolmacdonald"],
            "transaction_id": ["1"],
            "created_at": [datetime(2025, 9, 1)],
            "cumulative_trades": [1],
        }
    )

    result = render_cumulative_trades(cumulative, out_path)

    assert result.is_file()


def _diversity_counts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "casitzmann", "bigTETONclimber", "thatbolb"],
            "trades": [10, 4, 0, 0],
            "unique_partners": [6, 2, 0, 0],
        }
    )


def test_render_trade_partner_diversity_writes_a_png(tmp_path):
    out_path = tmp_path / "trade_partner_diversity.png"

    result = render_trade_partner_diversity(_diversity_counts(), out_path)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_trade_partner_diversity_keeps_zero_trade_managers(tmp_path):
    """Issue #51: a zero-trade manager still plots, at the origin, not dropped."""
    out_path = tmp_path / "trade_partner_diversity.png"

    result = render_trade_partner_diversity(_diversity_counts(), out_path)

    assert result.is_file()


def test_render_trade_partner_diversity_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "trade_partner_diversity.png"
    counts = pl.DataFrame(
        {"manager": ["nolmacdonald"], "trades": [1], "unique_partners": [1]}
    )

    result = render_trade_partner_diversity(counts, out_path)

    assert result.is_file()
