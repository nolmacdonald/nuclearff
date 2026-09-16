"""Unit tests for nuclearff.archive.franchise: manager franchise pages (issue #112)."""

from __future__ import annotations

import polars as pl

from nuclearff.archive.franchise import franchise_profile


def _standings(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "roster_id": pl.Int64,
        "display_name": pl.String,
        "wins": pl.Int64,
        "losses": pl.Int64,
        "ties": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def _championships(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "champion_display_name": pl.String,
        "runner_up_display_name": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _trade_counts(rows: list[dict]) -> pl.DataFrame:
    schema = {"manager": pl.String, "trades": pl.UInt32}
    return pl.DataFrame(rows, schema=schema)


def _draft_stats(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "manager": pl.String,
        "avg_draft_position": pl.Float64,
        "times_first_pick": pl.UInt32,
        "times_last_pick": pl.UInt32,
    }
    return pl.DataFrame(rows, schema=schema)


def _score_extremes(rows: list[dict]) -> pl.DataFrame:
    schema = {"kind": pl.String, "manager": pl.String}
    return pl.DataFrame(rows, schema=schema)


def _margin_extremes(rows: list[dict]) -> pl.DataFrame:
    schema = {"kind": pl.String, "winner": pl.String}
    return pl.DataFrame(rows, schema=schema)


def _streaks(rows: list[dict]) -> pl.DataFrame:
    schema = {"manager": pl.String, "length": pl.UInt32}
    return pl.DataFrame(rows, schema=schema)


EMPTY_CHAMPIONSHIPS = _championships([])
EMPTY_TRADES = _trade_counts([])
EMPTY_DRAFT = _draft_stats([])
EMPTY_SCORE = _score_extremes([])
EMPTY_MARGIN = _margin_extremes([])
EMPTY_STREAKS = _streaks([])


def test_franchise_profile_assembles_career_record_across_seasons():
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 10,
                "losses": 4,
                "ties": 0,
            },
            {
                "league_id": "L2",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 8,
                "losses": 6,
                "ties": 1,
            },
        ]
    )

    profile = franchise_profile(
        standings,
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        EMPTY_STREAKS,
    )

    row = profile.row(0, named=True)
    assert row["manager"] == "Alice"
    assert row["seasons_played"] == 2
    assert row["career_wins"] == 18
    assert row["career_losses"] == 10
    assert row["career_ties"] == 1


def test_franchise_profile_counts_championships_and_runner_ups():
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 10,
                "losses": 4,
                "ties": 0,
            },
        ]
    )
    championships = _championships(
        [
            {
                "league_id": "L1",
                "season": 2024,
                "champion_display_name": "Alice",
                "runner_up_display_name": "Bob",
            },
            {
                "league_id": "L2",
                "season": 2025,
                "champion_display_name": "Bob",
                "runner_up_display_name": "Alice",
            },
        ]
    )

    profile = franchise_profile(
        standings,
        championships,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        EMPTY_STREAKS,
    )

    row = profile.row(0, named=True)
    assert row["championships"] == 1
    assert row["runner_ups"] == 1


def test_franchise_profile_zero_fills_trades_and_championships_not_null():
    """A manager who never won and never traded gets 0, not NULL."""
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 5,
                "losses": 9,
                "ties": 0,
            },
        ]
    )

    profile = franchise_profile(
        standings,
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        EMPTY_STREAKS,
    )

    row = profile.row(0, named=True)
    assert row["championships"] == 0
    assert row["runner_ups"] == 0
    assert row["all_time_trades"] == 0
    assert row["records_held"] == []


def test_franchise_profile_missing_draft_stats_stays_null_not_zero():
    """A manager absent from draft_stats has no real average -- don't fabricate 0."""
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 5,
                "losses": 9,
                "ties": 0,
            },
        ]
    )

    profile = franchise_profile(
        standings,
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        EMPTY_STREAKS,
    )

    row = profile.row(0, named=True)
    assert row["avg_draft_position"] is None
    assert row["times_first_pick"] is None


def test_franchise_profile_collects_records_held():
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 5,
                "losses": 9,
                "ties": 0,
            },
        ]
    )
    score_extremes = _score_extremes(
        [
            {"kind": "highest", "manager": "Alice"},
            {"kind": "lowest", "manager": "Bob"},
        ]
    )
    margin_extremes = _margin_extremes(
        [
            {"kind": "biggest_blowout", "winner": "Alice"},
            {"kind": "closest_margin", "winner": "Bob"},
        ]
    )
    win_streaks = _streaks(
        [{"manager": "Alice", "length": 5}, {"manager": "Bob", "length": 2}]
    )

    profile = franchise_profile(
        standings,
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        score_extremes,
        margin_extremes,
        win_streaks,
        EMPTY_STREAKS,
    )

    held = set(profile.row(0, named=True)["records_held"])
    assert held == {
        "highest_single_week_score",
        "biggest_blowout",
        "longest_win_streak",
    }


def test_franchise_profile_collects_a_loss_streak_record_too():
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Alice",
                "wins": 5,
                "losses": 9,
                "ties": 0,
            },
        ]
    )
    loss_streaks = _streaks([{"manager": "Alice", "length": 4}])

    profile = franchise_profile(
        standings,
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        loss_streaks,
    )

    assert profile.row(0, named=True)["records_held"] == ["longest_loss_streak"]


def test_franchise_profile_multiple_managers_sorted_alphabetically():
    standings = _standings(
        [
            {
                "league_id": "L1",
                "roster_id": 1,
                "display_name": "Zoe",
                "wins": 1,
                "losses": 1,
                "ties": 0,
            },
            {
                "league_id": "L1",
                "roster_id": 2,
                "display_name": "Alice",
                "wins": 1,
                "losses": 1,
                "ties": 0,
            },
        ]
    )

    profile = franchise_profile(
        standings,
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        EMPTY_STREAKS,
    )

    assert profile["manager"].to_list() == ["Alice", "Zoe"]


def test_franchise_profile_empty_standings():
    profile = franchise_profile(
        _standings([]),
        EMPTY_CHAMPIONSHIPS,
        EMPTY_TRADES,
        EMPTY_DRAFT,
        EMPTY_SCORE,
        EMPTY_MARGIN,
        EMPTY_STREAKS,
        EMPTY_STREAKS,
    )

    assert profile.height == 0
