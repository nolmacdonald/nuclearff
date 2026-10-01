"""FAAB waiver spend analytics (issues #132, #133). Standalone — not part of
either open epic, though it reads data both rely on.

Turns ``sleeper_transactions`` (issue #20) into two views: average FAAB
spend per position for each season's real first waiver-wire week
(:func:`first_waiver_week_spend_by_position`, #132), and total FAAB spend
per manager, per season and career-wide, against how many players they
actually acquired (:func:`manager_waiver_spend`/:func:`career_waiver_spend`,
#133). No new Sleeper fetching — ``settings.waiver_bid`` and ``adds`` are
already persisted per transaction.

**Losing claims keep their real dollar amount (settled in #227).** #133
worried, from one captured failed claim with ``settings.waiver_bid == 0``
(``tests/test_sleeper_transactions.py::WAIVER_FAILED``), that Sleeper
erases the bid on a lost claim. It doesn't: on the real Chopped league,
212 of 422 failed 2025 claims carry a nonzero ``waiver_bid``, and the
``$0`` ones are real ``$0`` bids. This module still builds ``total_spent``
from winning (``status == "complete"``) claims only, since a lost claim
costs nothing, and reports ``failed_claims`` as a count; per-claim bids
for losses live in :mod:`nuclearff.chopped.claims`.

**Multi-add claims are excluded everywhere in this module, not guessed
at.** Sleeper's ``adds`` field is a dict, so a single waiver transaction
with one ``waiver_bid`` could in principle add more than one player.
Splitting the bid or attributing it to only one player would misrepresent
a real dollar figure either way — a claim adding anything other than
exactly one player contributes no row, in both #132's and #133's
functions, for consistency.

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

_MANAGER_SEASON_SCHEMA = {
    "league_id": pl.String,
    "season": pl.Int64,
    "manager": pl.String,
    "total_spent": pl.Int64,
    "players_acquired": pl.UInt32,
    "failed_claims": pl.UInt32,
    "avg_cost_per_player": pl.Float64,
}

_CAREER_SCHEMA = {
    "manager": pl.String,
    "total_spent": pl.Int64,
    "players_acquired": pl.UInt32,
    "failed_claims": pl.UInt32,
    "avg_cost_per_player": pl.Float64,
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


def _with_avg_cost(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        avg_cost_per_player=pl.when(pl.col("players_acquired") > 0)
        .then(pl.col("total_spent") / pl.col("players_acquired"))
        .otherwise(None)
    )


def manager_waiver_spend(transactions: pl.DataFrame) -> pl.DataFrame:
    """Per-manager, per-season FAAB spend against players actually acquired.

    Args:
        transactions: ``sleeper_transactions`` rows — needs ``league_id``,
            ``season``, ``type``, ``status``, ``settings`` (JSON), ``adds``
            (JSON), ``roster_display_names`` (JSON).

    Returns:
        One row per ``(league_id, season, manager)`` with at least one
        waiver claim: ``total_spent`` (sum of ``waiver_bid`` over
        ``status == "complete"`` single-add claims only — see the module
        docstring on why a losing claim's amount isn't trusted),
        ``players_acquired`` (count of those same claims),
        ``failed_claims`` (count of ``status == "failed"`` claims — a real
        "attempts" signal, no dollar figure attached), ``avg_cost_per_player``
        (``None``, not a divide-by-zero, when ``players_acquired == 0``).
        A manager with only failed claims still gets a row:
        ``total_spent == 0``, ``failed_claims > 0``, not an absent row. A
        priority-waiver season (no real ``waiver_bid``) or a multi-add claim
        contributes nothing to any column, same exclusions as
        :func:`first_waiver_week_spend_by_position`.
    """
    waivers = transactions.filter(pl.col("type") == "waiver")
    if waivers.height == 0:
        return pl.DataFrame(schema=_MANAGER_SEASON_SCHEMA)

    rows: list[dict[str, object]] = []
    for row in waivers.iter_rows(named=True):
        names = [n for n in json.loads(row["roster_display_names"] or "[]") if n]
        if len(names) != 1:
            continue
        manager = names[0]

        if row["status"] == "failed":
            rows.append(
                {
                    "league_id": row["league_id"],
                    "season": row["season"],
                    "manager": manager,
                    "spent": 0,
                    "acquired": 0,
                    "failed": 1,
                }
            )
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

        rows.append(
            {
                "league_id": row["league_id"],
                "season": row["season"],
                "manager": manager,
                "spent": bid,
                "acquired": 1,
                "failed": 0,
            }
        )

    if not rows:
        return pl.DataFrame(schema=_MANAGER_SEASON_SCHEMA)

    flat = pl.DataFrame(rows)
    summary = flat.group_by(["league_id", "season", "manager"]).agg(
        pl.col("spent").sum().alias("total_spent"),
        pl.col("acquired").sum().cast(pl.UInt32).alias("players_acquired"),
        pl.col("failed").sum().cast(pl.UInt32).alias("failed_claims"),
    )
    summary = _with_avg_cost(summary)
    return summary.select(list(_MANAGER_SEASON_SCHEMA.keys())).sort(
        ["season", "league_id", "manager"]
    )


def career_waiver_spend(season_spend: pl.DataFrame) -> pl.DataFrame:
    """Roll :func:`manager_waiver_spend`'s output up to one row per manager,
    career-wide.

    Args:
        season_spend: Output of :func:`manager_waiver_spend`, spanning every
            season to roll up.

    Returns:
        One row per manager: ``total_spent``/``players_acquired``/
        ``failed_claims`` summed across every season present,
        ``avg_cost_per_player`` recomputed from the summed totals (not
        averaged across each season's own average, which would
        under-weight a season with more acquisitions). Sorted by
        ``total_spent`` descending.
    """
    if season_spend.height == 0:
        return pl.DataFrame(schema=_CAREER_SCHEMA)

    career = season_spend.group_by("manager").agg(
        pl.col("total_spent").sum().alias("total_spent"),
        pl.col("players_acquired").sum().alias("players_acquired"),
        pl.col("failed_claims").sum().alias("failed_claims"),
    )
    career = _with_avg_cost(career)
    return career.select(list(_CAREER_SCHEMA.keys())).sort(
        "total_spent", descending=True
    )
