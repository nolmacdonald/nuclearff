"""Unit tests for nuclearff.archive.on_this_day: transaction-based callbacks."""

from __future__ import annotations

import json
from datetime import date, datetime

import polars as pl

from nuclearff.archive.on_this_day import (
    transaction_summary_rows,
    transactions_on_this_day,
)


def _transactions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "transaction_id": ["t1", "t2", "t3", "t4"],
            "type": ["trade", "trade", "waiver", "trade"],
            "created_at": [
                datetime(2021, 9, 10, 12, 0, 0),
                datetime(2023, 9, 10, 18, 30, 0),
                datetime(2022, 9, 11, 9, 0, 0),
                None,
            ],
            "roster_display_names": [
                json.dumps(["Nolan", "Mike"]),
                json.dumps(["Nolan"]),
                json.dumps([None]),
                json.dumps(["Mike"]),
            ],
            "adds": [
                json.dumps({}),
                json.dumps({"p1": 1}),
                json.dumps({"p2": 2}),
                json.dumps({}),
            ],
            "drops": [
                json.dumps({}),
                json.dumps({}),
                json.dumps({}),
                json.dumps({}),
            ],
            "draft_picks": [
                json.dumps([{"season": "2025", "round": 1}]),
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
            ],
        }
    )


def test_matches_same_month_and_day_across_years():
    result = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    assert result["transaction_id"].to_list() == ["t2", "t1"]


def test_no_match_returns_empty_frame():
    result = transactions_on_this_day(_transactions(), date(2026, 1, 1))

    assert result.height == 0


def test_null_created_at_never_matches():
    result = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    assert "t4" not in result["transaction_id"].to_list()


def test_empty_input_returns_empty_frame():
    empty = _transactions().clear()

    result = transactions_on_this_day(empty, date(2026, 9, 10))

    assert result.height == 0


def _players() -> pl.DataFrame:
    return pl.DataFrame({"player_id": ["p1"], "full_name": ["Player One"]})


def test_transaction_summary_rows_resolves_player_names():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, _players())

    t2 = result.filter(pl.col("year") == 2023).to_dicts()[0]
    assert t2["summary"] == "added Player One"
    assert t2["parties"] == "Nolan"


def test_transaction_summary_rows_falls_back_to_raw_id_without_players_table():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, players=None)

    t2 = result.filter(pl.col("year") == 2023).to_dicts()[0]
    assert t2["summary"] == "added p1"


def test_transaction_summary_rows_includes_draft_picks():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, _players())

    t1 = result.filter(pl.col("year") == 2021).to_dicts()[0]
    assert "1 draft pick" in t1["summary"]


def test_transaction_summary_rows_handles_empty_matches():
    empty = _transactions().clear()

    result = transaction_summary_rows(empty, _players())

    assert result.height == 0
