"""Unit tests for nuclearff.archive.waiver_spend (issue #132)."""

from __future__ import annotations

import json

import polars as pl
import pytest

from nuclearff.archive.waiver_spend import first_waiver_week_spend_by_position


def _transactions(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "league_id": pl.String,
        "season": pl.Int64,
        "week": pl.Int64,
        "type": pl.String,
        "status": pl.String,
        "settings": pl.String,
        "adds": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _players(rows: list[dict]) -> pl.DataFrame:
    schema = {"player_id": pl.String, "position": pl.String}
    return pl.DataFrame(rows, schema=schema)


def _projections(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "season": pl.Int64,
        "week": pl.Int64,
        "player_id": pl.String,
        "position": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


def _waiver_row(
    league_id: str,
    season: int,
    week: int,
    player_id: str,
    bid: int | None,
    *,
    status: str = "complete",
) -> dict:
    settings = {"waiver_bid": bid} if bid is not None else {}
    return {
        "league_id": league_id,
        "season": season,
        "week": week,
        "type": "waiver",
        "status": status,
        "settings": json.dumps(settings),
        "adds": json.dumps({player_id: 1}),
    }


PLAYERS = _players([{"player_id": "p_kicker", "position": "K"}])
PROJECTIONS = _projections(
    [{"season": 2025, "week": 1, "player_id": "p_rb", "position": "RB"}]
)


def test_first_waiver_week_only_counts_the_earliest_week():
    transactions = _transactions(
        [
            _waiver_row("L1", 2025, 1, "p_rb", 10),
            _waiver_row("L1", 2025, 2, "p_rb", 50),  # later week, excluded
        ]
    )

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    assert result.height == 1
    row = result.row(0, named=True)
    assert row["avg_bid"] == pytest.approx(10.0)


def test_first_waiver_week_excludes_a_failed_claim():
    transactions = _transactions(
        [_waiver_row("L1", 2025, 1, "p_rb", 10, status="failed")]
    )

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    assert result.height == 0


def test_first_waiver_week_priority_waiver_league_produces_no_rows():
    """No real waiver_bid at all -- a priority-waiver league, not a fabricated $0."""
    transactions = _transactions([_waiver_row("L1", 2025, 1, "p_rb", None)])

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    assert result.height == 0


def test_first_waiver_week_still_resolves_when_first_weeks_claims_all_failed():
    """The real first week is still week 1, even if every week-1 claim failed."""
    transactions = _transactions(
        [
            _waiver_row("L1", 2025, 1, "p_rb", 10, status="failed"),
            _waiver_row("L1", 2025, 2, "p_rb", 50),
        ]
    )

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    assert result.height == 0  # week 2 is real, but not the "first" week -- excluded


def test_first_waiver_week_resolves_position_from_projections():
    transactions = _transactions([_waiver_row("L1", 2025, 1, "p_rb", 20)])

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    row = result.row(0, named=True)
    assert row["position"] == "RB"
    assert row["fallback_claims"] == 0


def test_first_waiver_week_falls_back_to_players_snapshot():
    transactions = _transactions([_waiver_row("L1", 2025, 1, "p_kicker", 5)])

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    row = result.row(0, named=True)
    assert row["position"] == "K"
    assert row["fallback_claims"] == 1


def test_first_waiver_week_excludes_multi_add_claims():
    row = _waiver_row("L1", 2025, 1, "p_rb", 20)
    row["adds"] = json.dumps({"p_rb": 1, "p_kicker": 1})
    transactions = _transactions([row])

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    assert result.height == 0


def test_first_waiver_week_excludes_an_unresolvable_position():
    transactions = _transactions([_waiver_row("L1", 2025, 1, "p_unknown", 20)])

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, PROJECTIONS)

    assert result.height == 0


def test_first_waiver_week_aggregates_min_max_avg_across_multiple_claims():
    transactions = _transactions(
        [
            _waiver_row("L1", 2025, 1, "p_rb", 10),
            _waiver_row("L1", 2025, 1, "p_rb2", 30),
        ]
    )
    projections = _projections(
        [
            {"season": 2025, "week": 1, "player_id": "p_rb", "position": "RB"},
            {"season": 2025, "week": 1, "player_id": "p_rb2", "position": "RB"},
        ]
    )

    row = first_waiver_week_spend_by_position(transactions, PLAYERS, projections).row(
        0, named=True
    )

    assert row["avg_bid"] == pytest.approx(20.0)
    assert row["min_bid"] == 10
    assert row["max_bid"] == 30
    assert row["num_claims"] == 2


def test_first_waiver_week_per_league_per_season_grain():
    transactions = _transactions(
        [
            _waiver_row("L1", 2024, 1, "p_rb", 10),
            _waiver_row("L2", 2025, 1, "p_rb", 40),
        ]
    )
    projections = _projections(
        [
            {"season": 2024, "week": 1, "player_id": "p_rb", "position": "RB"},
            {"season": 2025, "week": 1, "player_id": "p_rb", "position": "RB"},
        ]
    )

    result = first_waiver_week_spend_by_position(transactions, PLAYERS, projections)

    assert result.height == 2
    assert set(result["league_id"].to_list()) == {"L1", "L2"}


def test_first_waiver_week_empty_input():
    assert (
        first_waiver_week_spend_by_position(
            _transactions([]), PLAYERS, PROJECTIONS
        ).height
        == 0
    )
