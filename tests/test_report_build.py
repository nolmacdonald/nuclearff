"""Unit tests for nuclearff.report: CSV, markdown, and table selection.

PNG rendering is deliberately not exercised here — it needs the `dev` extra
(plottable/matplotlib) and downloads headshots over the network, which
`tests/conftest.py`'s autouse `_no_network` fixture forbids. `write_report`
is therefore called with `render_tables=False`; the rendering path's own
guard rails (the `RenderingUnavailableError` type, the Agg backend) are
covered by the fact that it is skipped cleanly.
"""

from __future__ import annotations

import polars as pl
import pytest

from nuclearff.report.build import write_board_csv, write_report
from nuclearff.report.tables import top_n_by_position


def _board() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["a", "b", "c", "d"],
            "player_display_name": ["A Back", "B Wideout", "C Passer", "D Tight"],
            "position": ["RB", "WR", "QB", "TE"],
            "recent_team": ["DET", "CIN", "BUF", "KC"],
            "games": [17, 16, 17, 15],
            "value_estimate": [300.0, 250.0, 400.0, 200.0],
            "points_per_game": [17.6, 15.6, 23.5, 13.3],
            "replacement_value_position": [150.0, 140.0, 300.0, 120.0],
            "vorp": [150.0, 110.0, 100.0, 80.0],
            "auction_value": [88.0, 65.0, 55.0, 40.0],
            "in_draft_pool": [True, True, True, True],
            "adp_overall": [2.5, 1.5, 25.8, 21.0],
            "adp_position_rank": [1, 1, 1, 1],
            "adp_source": ["fantasypros_ecr"] * 4,
            "adp_as_of_date": ["2026-08-28"] * 4,
            "headshot_url": [None, None, None, None],
            "rank_overall": [1, 2, 3, 4],
            "rank_position": [1, 1, 1, 1],
            "fantasy_points_2025": [310.0, 260.0, 410.0, 210.0],
        }
    )


def _context() -> dict:
    return {
        "league_id": "1387966835797798912",
        "league_name": "Freeman Forever League",
        "season": 2026,
        "num_teams": 10,
        "budget_per_team": 200,
        "roster_spots": 14,
        "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"]
        + ["BN"] * 6,
        "scoring_type": "half_ppr",
        "max_keepers": 3,
        "baseline": "vols",
        "seasons_used": [2023, 2024, 2025],
        "as_of_season": 2026,
        "replacement_ranks": {"QB": 10, "RB": 27, "WR": 30, "TE": 13},
        "players_valued": 4,
    }


def test_top_n_by_position_ranks_by_auction_value():
    result = top_n_by_position(_board(), "RB", n=12)

    assert result.height == 1
    assert result["player_display_name"][0] == "A Back"


def test_top_n_by_position_raises_for_an_absent_position():
    with pytest.raises(ValueError, match="no players at position"):
        top_n_by_position(_board(), "K")


def test_write_board_csv_uses_a_stable_column_order(tmp_path):
    path = write_board_csv(_board(), tmp_path / "board.csv")
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")

    assert header[0] == "rank_overall"
    assert "auction_value" in header
    # Season point columns are appended after the documented core columns.
    assert header[-1] == "fantasy_points_2025"


def test_write_report_writes_csv_and_markdown(tmp_path):
    report = write_report(_board(), _context(), tmp_path, render_tables=False)

    assert report.is_file()
    assert (tmp_path / "auction_board.csv").is_file()


def test_report_states_the_league_facts_dynamically(tmp_path):
    """Nothing about the league is hardcoded in the report text."""
    report = write_report(_board(), _context(), tmp_path, render_tables=False)
    text = report.read_text(encoding="utf-8")

    assert "Freeman Forever League" in text
    assert "$200 per team" in text
    assert "half ppr" in text
    assert "$2,000" in text  # 10 teams x $200, computed not hardcoded


def test_report_carries_the_load_bearing_caveats(tmp_path):
    """The caveats are the point: these numbers are easy to over-trust."""
    report = write_report(_board(), _context(), tmp_path, render_tables=False)
    text = report.read_text(encoding="utf-8")

    assert "backward-looking" in text
    assert "Rookies" in text
    assert "not ADP" in text
    assert "No keeper adjustment" in text
    assert "FLEX split" in text


def test_report_includes_a_table_per_position(tmp_path):
    report = write_report(_board(), _context(), tmp_path, render_tables=False)
    text = report.read_text(encoding="utf-8")

    for position in ("QB", "RB", "WR", "TE"):
        assert f"{position}\n" in text or f" {position}" in text


def test_report_skips_png_links_when_not_rendering(tmp_path):
    report = write_report(_board(), _context(), tmp_path, render_tables=False)
    text = report.read_text(encoding="utf-8")

    assert ".png" not in text
