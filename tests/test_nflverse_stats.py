"""Unit tests for the nflreadpy receiving-stats wrapper functions.

These never touch the network: every function in
`nuclearff.nflverse.stats` is a thin wrapper (delegate, filter, validate),
so the tests verify delegation and filtering with monkeypatch against small
synthetic Polars DataFrames shaped like the real nflreadpy schemas observed
during live investigation (see the module docstring in `stats.py`).
"""

from __future__ import annotations

import polars as pl
import pytest

import nuclearff.nflverse.stats as stats


def _weekly_stats_df() -> pl.DataFrame:
    """A small stand-in for `nflreadpy.load_player_stats(summary_level="week")`."""
    return pl.DataFrame(
        {
            "player_id": ["00-01", "00-02", "00-03", "00-04"],
            "position": ["WR", "RB", "QB", "TE"],
            "season": [2024, 2024, 2024, 2024],
            "week": [1, 1, 1, 1],
            "targets": [8, 3, 0, 5],
            "receptions": [6, 2, 0, 4],
            "receiving_yards": [80, 15, 0, 40],
        }
    )


def test_load_weekly_receiving_delegates_with_week_summary_level(monkeypatch):
    """The wrapper calls load_player_stats with summary_level='week'."""
    calls: list[dict] = []
    expected = _weekly_stats_df()
    monkeypatch.setattr(
        stats.nflreadpy,
        "load_player_stats",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    result = stats.load_weekly_receiving([2024])

    assert calls == [{"seasons": [2024], "summary_level": "week"}]
    assert sorted(result["position"].to_list()) == ["RB", "TE", "WR"]
    assert "QB" not in result["position"].to_list()


def test_load_weekly_receiving_keeps_only_pass_catching_positions(monkeypatch):
    """Non-WR/RB/TE rows (QB here) are dropped from the result."""
    monkeypatch.setattr(
        stats.nflreadpy, "load_player_stats", lambda **kwargs: _weekly_stats_df()
    )

    result = stats.load_weekly_receiving([2024])

    assert result.height == 3
    assert set(result["position"].to_list()) == {"WR", "RB", "TE"}


def test_load_weekly_receiving_raises_on_missing_column(monkeypatch):
    """A schema drift missing a required column fails loudly, not silently."""
    broken = _weekly_stats_df().drop("position")
    monkeypatch.setattr(stats.nflreadpy, "load_player_stats", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="load_weekly_receiving.*position"):
        stats.load_weekly_receiving([2024])


def test_load_weekly_skill_stats_keeps_qb_unlike_receiving_loader(monkeypatch):
    """The skill-position loader keeps QB rows that load_weekly_receiving drops."""
    calls: list[dict] = []
    expected = _weekly_stats_df()
    monkeypatch.setattr(
        stats.nflreadpy,
        "load_player_stats",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    result = stats.load_weekly_skill_stats([2024])

    assert calls == [{"seasons": [2024], "summary_level": "week"}]
    assert set(result["position"].to_list()) == {"WR", "RB", "QB", "TE"}
    assert result.height == 4


def test_load_weekly_skill_stats_raises_on_missing_column(monkeypatch):
    """A schema drift missing a required column fails loudly, not silently."""
    broken = _weekly_stats_df().drop("position")
    monkeypatch.setattr(stats.nflreadpy, "load_player_stats", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="load_weekly_skill_stats.*position"):
        stats.load_weekly_skill_stats([2024])


def test_load_seasonal_skill_stats_keeps_qb(monkeypatch):
    """The skill-position seasonal loader keeps QB rows too."""
    monkeypatch.setattr(
        stats.nflreadpy, "load_player_stats", lambda **kwargs: _seasonal_stats_df()
    )

    result = stats.load_seasonal_skill_stats([2023, 2024])

    assert set(result["position"].to_list()) == {"WR", "QB", "RB"}


def _seasonal_stats_df() -> pl.DataFrame:
    """A stand-in for `nflreadpy.load_player_stats(summary_level="reg")`.

    Season-summary rows observed live to carry `recent_team` and `games`
    rather than the per-game `team`/`week`/`game_id` columns.
    """
    return pl.DataFrame(
        {
            "player_id": ["00-01", "00-02", "00-03"],
            "position": ["WR", "QB", "RB"],
            "season": [2024, 2024, 2024],
            "recent_team": ["KC", "KC", "SF"],
            "games": [17, 17, 15],
            "receiving_yards": [1200, 0, 400],
        }
    )


def test_load_seasonal_receiving_delegates_with_reg_summary_level(monkeypatch):
    """The wrapper calls load_player_stats with summary_level='reg'."""
    calls: list[dict] = []
    expected = _seasonal_stats_df()
    monkeypatch.setattr(
        stats.nflreadpy,
        "load_player_stats",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    result = stats.load_seasonal_receiving([2023, 2024])

    assert calls == [{"seasons": [2023, 2024], "summary_level": "reg"}]
    assert set(result["position"].to_list()) == {"WR", "RB"}


def _ngs_receiving_df() -> pl.DataFrame:
    """A stand-in for `nflreadpy.load_nextgen_stats(stat_type="receiving")`.

    Observed live: `player_position` only ever holds `WR`/`TE` — NGS does
    not publish receiving charting for RB — and `week == 0` is the
    season-aggregate row.
    """
    return pl.DataFrame(
        {
            "season": [2024, 2024],
            "season_type": ["REG", "REG"],
            "week": [0, 1],
            "player_gsis_id": ["00-01", "00-01"],
            "player_position": ["WR", "WR"],
            "targets": [140, 8],
            "avg_separation": [2.9, 3.1],
            "avg_cushion": [5.5, 5.7],
        }
    )


def test_load_ngs_receiving_delegates_with_receiving_stat_type(monkeypatch):
    """The wrapper calls load_nextgen_stats with stat_type='receiving' and
    applies no filtering of its own — NGS receiving data is already scoped
    to pass-catchers.
    """
    calls: list[dict] = []
    expected = _ngs_receiving_df()
    monkeypatch.setattr(
        stats.nflreadpy,
        "load_nextgen_stats",
        lambda **kwargs: calls.append(kwargs) or expected,
    )

    result = stats.load_ngs_receiving([2024])

    assert calls == [{"seasons": [2024], "stat_type": "receiving"}]
    assert result is expected


def test_load_ngs_receiving_raises_on_missing_column(monkeypatch):
    """A schema drift missing a required column fails loudly."""
    broken = _ngs_receiving_df().drop("targets")
    monkeypatch.setattr(stats.nflreadpy, "load_nextgen_stats", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="load_ngs_receiving.*targets"):
        stats.load_ngs_receiving([2024])


def _snap_counts_df() -> pl.DataFrame:
    """A stand-in for `nflreadpy.load_snap_counts`, two games for one WR."""
    return pl.DataFrame(
        {
            "season": [2021, 2021, 2021, 2021, 2021],
            "pfr_player_id": ["WWWW01", "WWWW01", "RRRR01", "TTTT01", "QQQQ01"],
            "player": [
                "Wide Receiver A",
                "Wide Receiver A",
                "Running Back B",
                "Tight End C",
                "Quarterback D",
            ],
            "position": ["WR", "WR", "RB", "TE", "QB"],
            "team": ["TEN", "TEN", "TEN", "TEN", "TEN"],
            "offense_snaps": [52.0, 48.0, 30.0, 40.0, 65.0],
        }
    )


def test_load_routes_sums_offense_snaps_as_a_labeled_proxy(monkeypatch):
    """Covered seasons get a season-summed offense_snaps proxy, clearly
    tagged, restricted to pass-catching positions, and QB is dropped.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        stats.nflreadpy,
        "load_snap_counts",
        lambda **kwargs: calls.append(kwargs) or _snap_counts_df(),
    )

    result = stats.load_routes([2021])

    assert calls == [{"seasons": [2021]}]
    assert set(result["source"].to_list()) == {"offense_snaps_proxy"}
    assert set(result["position"].to_list()) == {"WR", "RB", "TE"}

    wr_row = result.filter(pl.col("pfr_player_id") == "WWWW01")
    assert wr_row["routes_run"].to_list() == [100.0]


def test_load_routes_marks_uncovered_seasons_unavailable_without_a_call(monkeypatch):
    """A season before snap-count coverage gets an 'unavailable' placeholder
    row instead of a fabricated number, and load_snap_counts is never
    called for it.
    """

    def _unexpected_call(**kwargs: object) -> pl.DataFrame:
        raise AssertionError("load_snap_counts should not be called")

    monkeypatch.setattr(stats.nflreadpy, "load_snap_counts", _unexpected_call)

    result = stats.load_routes([2005])

    assert result.height == 1
    row = result.row(0, named=True)
    assert row["season"] == 2005
    assert row["source"] == "unavailable"
    assert row["routes_run"] is None
    assert row["pfr_player_id"] is None


def test_load_routes_mixes_covered_and_uncovered_seasons(monkeypatch):
    """A request spanning the coverage boundary only calls load_snap_counts
    for the covered season and still returns an unavailable row for the
    uncovered one.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        stats.nflreadpy,
        "load_snap_counts",
        lambda **kwargs: calls.append(kwargs) or _snap_counts_df(),
    )

    result = stats.load_routes([2005, 2021])

    assert calls == [{"seasons": [2021]}]
    sources = result["source"].to_list()
    assert sources.count("unavailable") == 1
    assert sources.count("offense_snaps_proxy") == 3
    assert 2005 in result["season"].to_list()


def test_load_routes_raises_on_missing_snap_count_column(monkeypatch):
    """A schema drift missing a required snap-count column fails loudly."""
    broken = _snap_counts_df().drop("offense_snaps")
    monkeypatch.setattr(stats.nflreadpy, "load_snap_counts", lambda **kwargs: broken)

    with pytest.raises(ValueError, match="load_routes.*offense_snaps"):
        stats.load_routes([2021])


def test_load_routes_with_no_seasons_returns_empty_frame_without_a_call():
    """An empty season list produces an empty, correctly-shaped frame."""
    result = stats.load_routes([])

    assert result.height == 0
    assert set(result.columns) == {
        "season",
        "pfr_player_id",
        "player",
        "position",
        "team",
        "routes_run",
        "source",
    }


def _players_df() -> pl.DataFrame:
    """A stand-in for `nflreadpy.load_players()`."""
    return pl.DataFrame(
        {
            "gsis_id": ["00-01", "00-02"],
            "display_name": ["Wide Receiver A", "Running Back B"],
            "position": ["WR", "RB"],
        }
    )


def test_load_players_delegates_to_nflreadpy(monkeypatch):
    """The wrapper returns exactly what nflreadpy.load_players returns."""
    expected = _players_df()
    monkeypatch.setattr(stats.nflreadpy, "load_players", lambda: expected)

    result = stats.load_players()

    assert result is expected


def test_load_players_raises_on_missing_column(monkeypatch):
    """A schema drift missing a required column fails loudly."""
    broken = _players_df().drop("display_name")
    monkeypatch.setattr(stats.nflreadpy, "load_players", lambda: broken)

    with pytest.raises(ValueError, match="load_players.*display_name"):
        stats.load_players()
