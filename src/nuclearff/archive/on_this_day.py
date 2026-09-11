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

:func:`transaction_summary_rows` (issue #118) turns
:func:`transactions_on_this_day`'s raw rows into the display-ready shape
``nuclearff.report.on_this_day.render_on_this_day_table`` renders.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

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


def _parties(row: dict[str, Any]) -> str:
    names = [n for n in json.loads(row["roster_display_names"] or "[]") if n]
    return ", ".join(names) if names else "—"


def _move_summary(row: dict[str, Any], player_names: dict[str, str]) -> str:
    adds = json.loads(row["adds"] or "{}")
    drops = json.loads(row["drops"] or "{}")
    draft_picks = json.loads(row["draft_picks"] or "[]")

    def names(player_ids: Any) -> str:
        return ", ".join(player_names.get(pid, pid) for pid in player_ids)

    parts = []
    if adds:
        parts.append(f"added {names(adds)}")
    if drops:
        parts.append(f"dropped {names(drops)}")
    if draft_picks:
        noun = "pick" if len(draft_picks) == 1 else "picks"
        parts.append(f"{len(draft_picks)} draft {noun}")
    return "; ".join(parts) if parts else "—"


def transaction_summary_rows(
    matches: pl.DataFrame, players: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Turn :func:`transactions_on_this_day`'s output into display-ready rows.

    Args:
        matches: Rows as returned by :func:`transactions_on_this_day`.
        players: ``sleeper_players`` rows (``player_id``, ``full_name``), used
            to resolve the player IDs in ``adds``/``drops`` to real names. A
            player ID absent from ``players`` (or ``players`` omitted
            entirely) falls back to the raw ID rather than failing —
            matches this project's existing "missing lookup degrades, does
            not crash" posture (e.g. ``report/user_leagues.py``'s ``"FA"``
            fallback).

    Returns:
        ``year``, ``type``, ``parties`` (comma-joined manager names, or
        ``"—"`` if none resolved), ``summary`` (a human-readable
        added/dropped/traded description), same row order as ``matches``.
    """
    if matches.height == 0:
        return pl.DataFrame(
            schema={
                "year": pl.Int64,
                "type": pl.Utf8,
                "parties": pl.Utf8,
                "summary": pl.Utf8,
            }
        )

    player_names: dict[str, str] = {}
    if players is not None and players.height > 0:
        player_names = {
            row["player_id"]: row["full_name"]
            for row in players.select("player_id", "full_name").to_dicts()
            if row["full_name"]
        }

    rows = []
    for row in matches.to_dicts():
        created_at = row["created_at"]
        rows.append(
            {
                "year": created_at.year if created_at is not None else None,
                "type": row.get("type") or "—",
                "parties": _parties(row),
                "summary": _move_summary(row, player_names),
            }
        )
    return pl.DataFrame(rows)
