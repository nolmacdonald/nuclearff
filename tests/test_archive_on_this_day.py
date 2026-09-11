"""Unit tests for nuclearff.archive.on_this_day: transaction-based callbacks."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from nuclearff.archive.on_this_day import transactions_on_this_day


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
