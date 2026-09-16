""" "On this day" historical callbacks from persisted league data.

Built for issue #114 (league-history epic #116). Ships the transaction-based
slice only: ``sleeper_transactions.created_at`` is a real, date-anchored
timestamp (see :func:`nuclearff.sleeper.transactions._epoch_ms_to_datetime`),
usable as-is, no dependency on anything else.

Matchup-based callbacks ("on this day, the highest score in league history
was set") need a ``(season, week)`` -> real calendar date mapping, which
requires :func:`nuclearff.nflverse.schedules.load_schedules` (issue #105).
Deliberately deferred as a follow-up rather than built here, matching this
issue's own stated non-goals — do not fold that in without a separate issue.
"""

from __future__ import annotations

from datetime import date

import polars as pl


def transactions_on_this_day(transactions: pl.DataFrame, today: date) -> pl.DataFrame:
    """Return every real transaction whose real anniversary matches ``today``.

    Args:
        transactions: ``sleeper_transactions`` rows (or an equivalent
            frame), with a real ``created_at`` timestamp column.
        today: The calendar date to match month/day against — every past
            year's occurrence of that same month/day qualifies, not just an
            exact year match. A null ``created_at`` never matches.

    Returns:
        ``transactions`` filtered to rows whose ``created_at`` shares
        ``today``'s month and day, most-recent-year first.
    """
    if transactions.height == 0:
        return transactions

    matched = transactions.filter(
        (pl.col("created_at").dt.month() == today.month)
        & (pl.col("created_at").dt.day() == today.day)
    )
    return matched.sort("created_at", descending=True)
