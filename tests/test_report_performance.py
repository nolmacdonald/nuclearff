"""Unit tests for nuclearff.report.performance."""

from __future__ import annotations

import polars as pl

from nuclearff.report.performance import (
    _underperformers,
    render_season_performance_table,
    render_weekly_performance_table,
)


def _performance(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "player_name": pl.String,
        "position": pl.String,
        "team": pl.String,
        "manager": pl.String,
        "projected_points": pl.Float64,
        "actual_points": pl.Float64,
        "delta": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def _season_summary(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "player_name": pl.String,
        "position": pl.String,
        "team": pl.String,
        "manager": pl.String,
        "games": pl.UInt32,
        "avg_projected_points": pl.Float64,
        "avg_actual_points": pl.Float64,
        "avg_delta": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


# --- _underperformers -----------------------------------------------------


def test_underperformers_excludes_a_player_stuck_at_exactly_zero():
    """The real problem found rendering this against real, real-time week 1
    data: a player whose game hasn't kicked off yet reads as `0.0` actual
    points, identically to a real bust, and would otherwise swamp the list."""
    performance = _performance(
        [
            {
                "player_name": "Hasn't Played Yet",
                "position": "QB",
                "team": "XX",
                "manager": "m1",
                "projected_points": 25.0,
                "actual_points": 0.0,
                "delta": -25.0,
            },
            {
                "player_name": "Drake Maye",
                "position": "QB",
                "team": "NE",
                "manager": "m2",
                "projected_points": 22.2,
                "actual_points": 11.8,
                "delta": -10.4,
            },
        ]
    )

    result = _underperformers(performance, top_n=10)

    assert result["player_name"].to_list() == ["Drake Maye"]


def test_underperformers_respects_top_n_after_filtering():
    performance = _performance(
        [
            {
                "player_name": f"Player {i}",
                "position": "WR",
                "team": "XX",
                "manager": "m1",
                "projected_points": 10.0,
                "actual_points": 1.0,
                "delta": -9.0 - i,
            }
            for i in range(5)
        ]
    )

    result = _underperformers(performance, top_n=2)

    assert result.height == 2


def test_underperformers_never_backfills_with_an_overachiever():
    """The real bug caught rendering against real week 1 data: with fewer
    than top_n real underperformers, the list must come up short, not pad
    itself with the next-best delta regardless of sign."""
    performance = _performance(
        [
            {
                "player_name": "Real Underperformer",
                "position": "QB",
                "team": "XX",
                "manager": "m1",
                "projected_points": 20.0,
                "actual_points": 10.0,
                "delta": -10.0,
            },
            {
                "player_name": "Real Overachiever",
                "position": "WR",
                "team": "XX",
                "manager": "m2",
                "projected_points": 10.0,
                "actual_points": 20.0,
                "delta": 10.0,
            },
        ]
    )

    result = _underperformers(performance, top_n=5)

    assert result["player_name"].to_list() == ["Real Underperformer"]


def test_render_weekly_performance_table_writes_a_png(tmp_path):
    performance = _performance(
        [
            {
                "player_name": "Drake Maye",
                "position": "QB",
                "team": "NE",
                "manager": "nolmacdonald",
                "projected_points": 20.6,
                "actual_points": 9.1,
                "delta": -11.5,
            },
            {
                "player_name": "Ja'Marr Chase",
                "position": "WR",
                "team": "CIN",
                "manager": "casitzmann",
                "projected_points": 18.2,
                "actual_points": 31.4,
                "delta": 13.2,
            },
        ]
    ).sort("delta", descending=True)
    out_path = tmp_path / "performance.png"

    result = render_weekly_performance_table(performance, out_path, week=1)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_weekly_performance_table_respects_top_n(tmp_path):
    rows = [
        {
            "player_name": f"Player {i}",
            "position": "WR",
            "team": "XX",
            "manager": "nolmacdonald",
            "projected_points": 10.0,
            "actual_points": float(i),
            "delta": float(i) - 10.0,
        }
        for i in range(20)
    ]
    performance = _performance(rows).sort("delta", descending=True)
    out_path = tmp_path / "performance.png"

    result = render_weekly_performance_table(performance, out_path, week=1, top_n=3)

    assert result.is_file()


def test_render_weekly_performance_table_handles_a_single_player(tmp_path):
    performance = _performance(
        [
            {
                "player_name": "Drake Maye",
                "position": "QB",
                "team": "NE",
                "manager": "nolmacdonald",
                "projected_points": 20.6,
                "actual_points": 9.1,
                "delta": -11.5,
            }
        ]
    )
    out_path = tmp_path / "performance.png"

    result = render_weekly_performance_table(performance, out_path, week=1)

    assert result.is_file()


def test_render_season_performance_table_writes_a_png(tmp_path):
    summary = _season_summary(
        [
            {
                "player_name": "Drake Maye",
                "position": "QB",
                "team": "NE",
                "manager": "aperry151",
                "games": 16,
                "avg_projected_points": 20.6,
                "avg_actual_points": 15.1,
                "avg_delta": -5.5,
            },
            {
                "player_name": "Ja'Marr Chase",
                "position": "WR",
                "team": "CIN",
                "manager": "casitzmann",
                "games": 15,
                "avg_projected_points": 18.2,
                "avg_actual_points": 22.4,
                "avg_delta": 4.2,
            },
        ]
    ).sort("avg_delta", descending=True)
    out_path = tmp_path / "season.png"

    result = render_season_performance_table(summary, out_path, season=2025)

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_season_performance_table_respects_top_n(tmp_path):
    rows = [
        {
            "player_name": f"Player {i}",
            "position": "WR",
            "team": "XX",
            "manager": "nolmacdonald",
            "games": 10,
            "avg_projected_points": 10.0,
            "avg_actual_points": float(i),
            "avg_delta": float(i) - 10.0,
        }
        for i in range(20)
    ]
    summary = _season_summary(rows).sort("avg_delta", descending=True)
    out_path = tmp_path / "season.png"

    result = render_season_performance_table(summary, out_path, season=2025, top_n=3)

    assert result.is_file()


def test_render_season_performance_table_handles_a_single_player(tmp_path):
    summary = _season_summary(
        [
            {
                "player_name": "Drake Maye",
                "position": "QB",
                "team": "NE",
                "manager": "aperry151",
                "games": 16,
                "avg_projected_points": 20.6,
                "avg_actual_points": 15.1,
                "avg_delta": -5.5,
            }
        ]
    )
    out_path = tmp_path / "season.png"

    result = render_season_performance_table(summary, out_path, season=2025)

    assert result.is_file()
