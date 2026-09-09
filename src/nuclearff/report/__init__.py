"""Report generation: styled position tables and a markdown draft report."""

from nuclearff.report.bracket import render_bracket_tree, render_playoff_brackets
from nuclearff.report.build import write_report
from nuclearff.report.draft_board import render_draft_board
from nuclearff.report.tables import render_position_table, top_n_by_position
from nuclearff.report.trades import render_trades_by_manager, render_trades_heatmap

__all__ = [
    "render_bracket_tree",
    "render_draft_board",
    "render_playoff_brackets",
    "render_position_table",
    "render_trades_by_manager",
    "render_trades_heatmap",
    "top_n_by_position",
    "write_report",
]
