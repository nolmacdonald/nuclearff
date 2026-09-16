"""Unit tests for nuclearff.archive.on_this_day: transaction-based callbacks."""

from __future__ import annotations

import json
from datetime import date, datetime

import polars as pl

from nuclearff.archive.on_this_day import (
    _ordinal,
    _pick_label,
    transaction_summary_rows,
    transactions_on_this_day,
)


def _transactions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "transaction_id": ["t1", "t2", "t3", "t4"],
            "league_id": ["l1", "l1", "l2", "l1"],
            "type": ["trade", "waiver", "waiver", "trade"],
            "created_at": [
                datetime(2021, 9, 10, 12, 0, 0),
                datetime(2023, 9, 10, 18, 30, 0),
                datetime(2022, 9, 11, 9, 0, 0),
                None,
            ],
            "roster_ids": [
                json.dumps([1, 2]),
                json.dumps([]),
                json.dumps([]),
                json.dumps([1, 2]),
            ],
            "roster_display_names": [
                json.dumps(["Nolan", "Mike"]),
                json.dumps(["Nolan"]),
                json.dumps([None]),
                json.dumps(["Mike"]),
            ],
            "adds": [
                # Nolan (roster 1) traded p9 to Mike (roster 2).
                json.dumps({"p9": 2}),
                json.dumps({"p1": 1}),
                json.dumps({"p2": 2}),
                json.dumps({}),
            ],
            "drops": [
                json.dumps({"p9": 1}),
                json.dumps({}),
                json.dumps({}),
                json.dumps({}),
            ],
            "draft_picks": [
                # A 2025 1st, originally roster 1's own pick, given to
                # roster 2 -- matches the real Sleeper traded-pick shape.
                json.dumps(
                    [
                        {
                            "season": "2025",
                            "round": 1,
                            "roster_id": 1,
                            "owner_id": 2,
                            "previous_owner_id": 1,
                        }
                    ]
                ),
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
            ],
            "settings": [
                json.dumps({}),
                json.dumps({}),
                json.dumps({"waiver_bid": 7}),
                json.dumps({}),
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
    return pl.DataFrame(
        {"player_id": ["p1", "p9"], "full_name": ["Player One", "Player Nine"]}
    )


def test_transaction_summary_rows_resolves_player_names_for_waiver():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, _players())

    t2 = result.filter(pl.col("summary").str.contains("Player One")).to_dicts()[0]
    assert t2["summary"] == "added Player One"
    assert t2["parties"] == "Nolan"


def test_transaction_summary_rows_falls_back_to_raw_id_without_players_table():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, players=None)

    t2 = result.filter(pl.col("type") == "waiver").to_dicts()[0]
    assert t2["summary"] == "added p1"


def test_transaction_summary_rows_handles_empty_matches():
    empty = _transactions().clear()

    result = transaction_summary_rows(empty, _players())

    assert result.height == 0


def test_transaction_summary_rows_adds_faab_note_for_faab_league():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 11))

    result = transaction_summary_rows(matches, waiver_budgets={"l2": 100})

    row = result.to_dicts()[0]
    assert row["summary"] == "added p2 $7 ($100)"


def test_transaction_summary_rows_omits_faab_note_without_a_budget():
    """A priority-waiver league (no real waiver_budget) gets no note at all."""
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 11))

    result = transaction_summary_rows(matches, waiver_budgets=None)

    row = result.to_dicts()[0]
    assert row["summary"] == "added p2"


def test_transaction_summary_rows_omits_faab_note_for_trade_players():
    """A trade never gets a FAAB note, even in a FAAB league."""
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, _players(), waiver_budgets={"l1": 100})

    trade_rows = result.filter(pl.col("type") == "trade")
    assert all("$" not in s for s in trade_rows["summary"].to_list())


def test_transaction_summary_rows_splits_a_trade_into_one_row_per_party():
    """Issue: a trade must show what *each* party received/gave up, not
    every party's name comma-joined onto one combined-summary row."""
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, _players())
    trade_rows = result.filter(pl.col("type") == "trade").to_dicts()

    assert len(trade_rows) == 2
    by_party = {r["parties"]: r["summary"] for r in trade_rows}
    assert by_party["Mike"] == "received Player Nine, '25 1st"
    assert by_party["Nolan"] == "gave up Player Nine, '25 1st"


def test_transaction_summary_rows_shares_group_id_across_trade_parties():
    matches = transactions_on_this_day(_transactions(), date(2026, 9, 10))

    result = transaction_summary_rows(matches, _players())
    trade_rows = result.filter(pl.col("type") == "trade")

    assert trade_rows["group_id"].n_unique() == 1
    assert trade_rows["group_id"][0] == "t1"


def test_transaction_summary_rows_handles_a_pickless_all_player_trade():
    transactions = pl.DataFrame(
        {
            "transaction_id": ["t5"],
            "league_id": ["l1"],
            "type": ["trade"],
            "created_at": [datetime(2024, 3, 1)],
            "roster_ids": [json.dumps([1, 2])],
            "roster_display_names": [json.dumps(["Nolan", "Mike"])],
            "adds": [json.dumps({"p1": 2})],
            "drops": [json.dumps({"p1": 1})],
            "draft_picks": [json.dumps([])],
            "settings": [json.dumps({})],
        }
    )
    matches = transactions_on_this_day(transactions, date(2024, 3, 1))

    result = transaction_summary_rows(matches, _players())

    by_party = {r["parties"]: r["summary"] for r in result.to_dicts()}
    assert by_party == {"Mike": "received Player One", "Nolan": "gave up Player One"}


def test_ordinal_covers_teen_exception_and_normal_suffixes():
    assert _ordinal(1) == "1st"
    assert _ordinal(2) == "2nd"
    assert _ordinal(3) == "3rd"
    assert _ordinal(4) == "4th"
    assert _ordinal(11) == "11th"
    assert _ordinal(12) == "12th"
    assert _ordinal(13) == "13th"
    assert _ordinal(21) == "21st"


def test_pick_label_formats_as_apostrophe_year_and_ordinal_round():
    assert _pick_label({"season": "2026", "round": 1}) == "'26 1st"
    assert _pick_label({"season": "2028", "round": 3}) == "'28 3rd"
