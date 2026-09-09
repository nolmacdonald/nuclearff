"""Report generation: styled position tables and a markdown draft report."""

from nuclearff.report.bracket import render_bracket_tree, render_playoff_brackets
from nuclearff.report.build import write_report
from nuclearff.report.draft_board import render_draft_board
from nuclearff.report.tables import render_position_table, top_n_by_position
from nuclearff.report.trades import (
    render_chord_diagram,
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
from nuclearff.report.user_leagues import (
    render_user_leagues_table,
    summarize_league_types,
)

__all__ = [
    "render_bracket_tree",
    "render_chord_diagram",
    "render_cumulative_trades",
    "render_draft_board",
    "render_manager_pair_leaderboard",
    "render_manager_season_heatmap",
    "render_playoff_brackets",
    "render_position_table",
    "render_trade_leaderboard",
    "render_trade_network",
    "render_trade_partner_diversity",
    "render_trades_by_manager",
    "render_trades_heatmap",
    "render_trades_over_time",
    "render_user_leagues_table",
    "summarize_league_types",
    "top_n_by_position",
    "write_report",
]
