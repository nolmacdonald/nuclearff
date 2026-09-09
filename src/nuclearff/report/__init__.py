"""Report generation: styled position tables and a markdown draft report."""

from nuclearff.report.bracket import render_bracket_tree, render_playoff_brackets
from nuclearff.report.build import write_report
from nuclearff.report.draft_board import render_draft_board
from nuclearff.report.tables import render_position_table, top_n_by_position
from nuclearff.report.trades import (
    render_manager_pair_leaderboard,
    render_manager_season_heatmap,
    render_trade_leaderboard,
    render_trade_network,
    render_trades_by_manager,
    render_trades_heatmap,
    render_trades_over_time,
)

__all__ = [
    "render_bracket_tree",
    "render_draft_board",
    "render_manager_pair_leaderboard",
    "render_manager_season_heatmap",
    "render_playoff_brackets",
    "render_position_table",
    "render_trade_leaderboard",
    "render_trade_network",
    "render_trades_by_manager",
    "render_trades_heatmap",
    "render_trades_over_time",
    "top_n_by_position",
    "write_report",
]
