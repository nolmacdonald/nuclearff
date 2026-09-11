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
``nuclearff.report.on_this_day.render_on_this_day_table`` renders, including
a real FAAB cost note on a waiver add when the league actually uses FAAB
(``$<bid> ($<budget>)``, e.g. ``$7 ($100)``) via :func:`_faab_note`, and a
real trade split into one row per party (:func:`_trade_rows`) — "who got
what," not every party's name comma-joined onto one combined-summary row.
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


def _faab_note(row: dict[str, Any], waiver_budget: int | None) -> str:
    """``" $<bid> ($<budget>)"`` for a real FAAB waiver claim, else ``""``.

    Only ``type == "waiver"`` transactions carry a ``waiver_bid`` at all
    (Sleeper has no bid concept for a free-agent pickup or a trade), and a
    league with no real FAAB budget set (``waiver_budget`` falsy — a
    priority-waiver league, e.g. ``waiver_type`` other than FAAB) has no
    budget to show a bid "out of" — "if applicable depending on league," not
    unconditional. A real ``$0`` bid in a real FAAB league is still shown:
    it means the manager spent nothing, which is a real fact, not a missing
    one.
    """
    if row.get("type") != "waiver" or not waiver_budget:
        return ""
    settings = json.loads(row.get("settings") or "{}")
    bid = settings.get("waiver_bid")
    if bid is None:
        return ""
    return f" ${bid} (${waiver_budget})"


def _move_summary(
    row: dict[str, Any], player_names: dict[str, str], waiver_budget: int | None
) -> str:
    adds = json.loads(row["adds"] or "{}")
    drops = json.loads(row["drops"] or "{}")
    draft_picks = json.loads(row["draft_picks"] or "[]")

    def names(player_ids: Any) -> str:
        return ", ".join(player_names.get(pid, pid) for pid in player_ids)

    parts = []
    if adds:
        parts.append(f"added {names(adds)}{_faab_note(row, waiver_budget)}")
    if drops:
        parts.append(f"dropped {names(drops)}")
    if draft_picks:
        noun = "pick" if len(draft_picks) == 1 else "picks"
        parts.append(f"{len(draft_picks)} draft {noun}")
    return "; ".join(parts) if parts else "—"


def _pick_label(pick: dict[str, Any]) -> str:
    """``"<season> round <round> pick"`` from a raw traded-pick object.

    A pick's own ``roster_id`` (whose original draft slot it is) can differ
    from both trading parties when a previously-acquired future pick gets
    re-traded again — real for this project's own league history. Not
    resolved to a manager name here: that needs a roster_id -> manager
    lookup for the *whole* league, not just this transaction's own
    ``roster_ids``, which is out of scope for this real-but-partial label
    rather than guessing at an attribution this function can't verify.
    """
    return f"{pick.get('season')} round {pick.get('round')} pick"


def _trade_party_summary(
    received_players: list[str],
    given_players: list[str],
    received_picks: list[dict[str, Any]],
    given_picks: list[dict[str, Any]],
    player_names: dict[str, str],
) -> str:
    def names(player_ids: list[str]) -> str:
        return ", ".join(player_names.get(pid, pid) for pid in player_ids)

    def with_picks(items: list[str], picks: list[dict[str, Any]]) -> list[str]:
        if picks:
            items = [*items, ", ".join(_pick_label(p) for p in picks)]
        return items

    parts = []
    received_names = [names(received_players)] if received_players else []
    received = with_picks(received_names, received_picks)
    if received:
        parts.append(f"received {', '.join(received)}")
    given = with_picks([names(given_players)] if given_players else [], given_picks)
    if given:
        parts.append(f"gave up {', '.join(given)}")
    return "; ".join(parts) if parts else "—"


def _trade_rows(
    row: dict[str, Any], player_names: dict[str, str]
) -> list[dict[str, Any]]:
    """Explode one real trade transaction into one row per party.

    A trade moves assets between two (or more, for a real N-way trade) real
    rosters — collapsing it into a single row with every party's name
    comma-joined loses "who got what," which is the actual answer a
    "on this day" trade callback should give. Splitting it into one row per
    ``roster_id`` in ``roster_ids`` answers that directly; the shared
    ``group_id`` (the transaction id) lets
    :func:`nuclearff.report.on_this_day.render_on_this_day_table` visually
    group the resulting rows as one transaction.

    Args:
        row: A raw ``sleeper_transactions`` row (as a dict) with
            ``type == "trade"``.
        player_names: ``player_id`` -> real name, for resolving ``adds``/
            ``drops``.

    Returns:
        One dict per party, each with ``year``, ``type``, ``parties`` (that
        one party's manager name), ``summary`` (what they received/gave up),
        and ``group_id`` (this transaction's id, shared across every row
        this function returns).
    """
    roster_ids = json.loads(row.get("roster_ids") or "[]")
    display_names = json.loads(row.get("roster_display_names") or "[]")
    name_by_roster = dict(zip(roster_ids, display_names, strict=False))
    adds = json.loads(row["adds"] or "{}")
    drops = json.loads(row["drops"] or "{}")
    draft_picks = json.loads(row["draft_picks"] or "[]")
    created_at = row["created_at"]
    year = created_at.year if created_at is not None else None

    rows = []
    for roster_id in roster_ids:
        received_players = [pid for pid, rid in adds.items() if rid == roster_id]
        given_players = [pid for pid, rid in drops.items() if rid == roster_id]
        received_picks = [p for p in draft_picks if p.get("owner_id") == roster_id]
        given_picks = [
            p for p in draft_picks if p.get("previous_owner_id") == roster_id
        ]
        rows.append(
            {
                "year": year,
                "type": row.get("type") or "—",
                "parties": name_by_roster.get(roster_id) or "—",
                "summary": _trade_party_summary(
                    received_players,
                    given_players,
                    received_picks,
                    given_picks,
                    player_names,
                ),
                "group_id": row.get("transaction_id"),
            }
        )
    return rows


def transaction_summary_rows(
    matches: pl.DataFrame,
    players: pl.DataFrame | None = None,
    waiver_budgets: dict[str, int] | None = None,
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
        waiver_budgets: ``league_id`` -> that season's real total FAAB
            budget, from the raw league object's ``settings.waiver_budget``
            (``sleeper_leagues.settings``, not currently modeled on
            :class:`~nuclearff.config.league.LeagueConfig`). A ``league_id``
            absent from this mapping (or ``waiver_budgets`` omitted
            entirely) renders a waiver add with no FAAB note at all, the
            correct behavior for a priority-waiver league with no real
            budget to show.

    Returns:
        ``year``, ``type``, ``parties`` (comma-joined manager names for a
        non-trade row, or the single party's own name for a trade row —
        see below — or ``"—"`` if none resolved), ``summary`` (a
        human-readable description; for a waiver/free-agent row,
        added/dropped, with a real ``$<bid> ($<budget>)`` note on a FAAB
        add when applicable; for a trade row, what that one party
        received/gave up), and ``group_id`` (the source transaction id —
        a real trade explodes into one row per party sharing the same
        ``group_id``, which
        :func:`nuclearff.report.on_this_day.render_on_this_day_table` uses
        to visually group those rows as one transaction).
    """
    if matches.height == 0:
        return pl.DataFrame(
            schema={
                "year": pl.Int64,
                "type": pl.Utf8,
                "parties": pl.Utf8,
                "summary": pl.Utf8,
                "group_id": pl.Utf8,
            }
        )

    player_names: dict[str, str] = {}
    if players is not None and players.height > 0:
        player_names = {
            row["player_id"]: row["full_name"]
            for row in players.select("player_id", "full_name").to_dicts()
            if row["full_name"]
        }
    waiver_budgets = waiver_budgets or {}

    rows = []
    for row in matches.to_dicts():
        if row.get("type") == "trade":
            rows.extend(_trade_rows(row, player_names))
            continue
        created_at = row["created_at"]
        waiver_budget = waiver_budgets.get(row.get("league_id"))
        rows.append(
            {
                "year": created_at.year if created_at is not None else None,
                "type": row.get("type") or "—",
                "parties": _parties(row),
                "summary": _move_summary(row, player_names, waiver_budget),
                "group_id": row.get("transaction_id"),
            }
        )
    return pl.DataFrame(rows)
