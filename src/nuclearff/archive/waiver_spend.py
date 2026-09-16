"""FAAB waiver spend analytics (issue #132). Standalone — not part of either
open epic, though it reads data both rely on.

Turns ``sleeper_transactions`` (issue #20) into average FAAB spend per
position for each season's real first waiver-wire week. No new Sleeper
fetching — ``settings.waiver_bid`` and ``adds`` are already persisted per
transaction.

**"First waiver week" is ``MIN(week)``, not the earliest ``created_at``.**
Sleeper processes waivers in a weekly batch, not continuously, so ``week``
is the real grouping unit — a single waiver run can span more than one
calendar day if Sleeper's processing is delayed. Computed over every
``type == "waiver"`` transaction regardless of outcome, so a league whose
first week's claims all failed still resolves the right week (just
contributes no dollar rows for it).

**Position accuracy**: ``sleeper_players`` is a single current snapshot,
not point-in-time (the same gap that caused a real team-column bug fixed
2026-09-10). Position is resolved from ``sleeper_projections`` (real
per-week) first, falling back to the ``sleeper_players`` snapshot only when
no projection row exists for that ``(season, week, player_id)`` — expected
whenever this project's own projection fetch coverage doesn't reach that
week, or the player is outside ``DEFAULT_POSITIONS`` (projections are only
fetched for QB/RB/WR/TE by default, so a K/DEF/DL waiver add always falls
back). ``fallback_claims`` in the output surfaces how many of each row's
``num_claims`` used the fallback, rather than silently trusting it.

**Multi-add claims are excluded, not guessed at.** Sleeper's ``adds`` field
is a dict, so a single waiver transaction with one ``waiver_bid`` could in
principle add more than one player. Splitting the bid or attributing it to
only one player would misrepresent a real dollar figure either way — a
claim adding anything other than exactly one player contributes no row.
"""

from __future__ import annotations

import json

import polars as pl

_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "position": pl.String,
    "avg_bid": pl.Float64,
    "min_bid": pl.Int64,
    "max_bid": pl.Int64,
    "num_claims": pl.UInt32,
    "fallback_claims": pl.UInt32,
}


def first_waiver_week_spend_by_position(
    transactions: pl.DataFrame, players: pl.DataFrame, projections: pl.DataFrame
) -> pl.DataFrame:
    """Average/min/max FAAB bid per position, for each season's first waiver week.

    Args:
        transactions: ``sleeper_transactions`` rows — needs ``league_id``,
            ``season``, ``week``, ``type``, ``status``, ``settings``
            (JSON), ``adds`` (JSON).
        players: ``sleeper_players`` rows — ``player_id``, ``position``
            (current-snapshot fallback source).
        projections: ``sleeper_projections`` rows — ``season``, ``week``,
            ``player_id``, ``position`` (preferred, real per-week source).

    Returns:
        One row per ``(league_id, season, position)`` with at least one
        qualifying claim: ``avg_bid``/``min_bid``/``max_bid``,
        ``num_claims``, ``fallback_claims`` (how many of ``num_claims``
        resolved position from the ``players`` snapshot rather than real
        per-week ``projections``). A priority-waiver season (no real
        ``waiver_bid``) contributes no rows, not a fabricated ``$0``. Only
        ``status == "complete"`` claims count — a failed claim never
        actually spent anything.
    """
    waivers = transactions.filter(pl.col("type") == "waiver")
    if waivers.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    first_weeks = dict(
        waivers.group_by("league_id")
        .agg(pl.col("week").min().alias("first_week"))
        .iter_rows()
    )

    players_by_id = dict(players.select("player_id", "position").iter_rows())
    projections_by_key = {
        (row["season"], row["week"], row["player_id"]): row["position"]
        for row in projections.select(
            "season", "week", "player_id", "position"
        ).iter_rows(named=True)
    }

    claims: list[dict[str, object]] = []
    for row in waivers.iter_rows(named=True):
        if row["week"] != first_weeks.get(row["league_id"]):
            continue
        if row["status"] != "complete":
            continue
        settings = json.loads(row["settings"] or "{}")
        bid = settings.get("waiver_bid")
        if bid is None:
            continue

        adds = json.loads(row["adds"] or "{}")
        if len(adds) != 1:
            continue
        (player_id,) = adds.keys()

        position = projections_by_key.get((row["season"], row["week"], player_id))
        used_fallback = position is None
        if position is None:
            position = players_by_id.get(player_id)
        if position is None:
            continue

        claims.append(
            {
                "league_id": row["league_id"],
                "season": row["season"],
                "position": position,
                "bid": bid,
                "used_fallback": used_fallback,
            }
        )

    if not claims:
        return pl.DataFrame(schema=_SCHEMA)

    flat = pl.DataFrame(claims)
    return (
        flat.group_by(["league_id", "season", "position"])
        .agg(
            pl.col("bid").mean().alias("avg_bid"),
            pl.col("bid").min().alias("min_bid"),
            pl.col("bid").max().alias("max_bid"),
            pl.len().cast(pl.UInt32).alias("num_claims"),
            pl.col("used_fallback").sum().cast(pl.UInt32).alias("fallback_claims"),
        )
        .sort(["season", "league_id", "position"])
    )
