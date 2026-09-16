"""League history archive: head-to-head rivalries (issue #110, epic #116).

``sleeper/wins.py::paired_weekly_matchups`` (added for #109) already pairs
each week's two rosters with real points and margin — this module joins
that against manager identity and collapses it to one row per manager pair
across every persisted season. No new Sleeper fetching.

**Regular-season/playoff split (issue #137).** #110 originally shipped only
the combined all-time record, deliberately deferred pending a real
playoff-week boundary — ``LeagueConfig.playoff_week_start`` (added for
#137, closing the same gap the draft-companion epic's #106 and the season
awards of #113 also hit). :func:`head_to_head` still returns the combined
record, unchanged; :func:`head_to_head_by_phase` adds the split, keyed off
that boundary, as a sibling rather than a replacement — the combined number
is still real and useful on its own.

Manager identity is ``sleeper_standings.display_name``, the same
cross-season-identity posture every other multi-season aggregate in this
project accepts.
"""

from __future__ import annotations

import polars as pl

from nuclearff.sleeper.wins import paired_weekly_matchups

_SCHEMA = {
    "manager_a": pl.String,
    "manager_b": pl.String,
    "games": pl.UInt32,
    "wins_a": pl.UInt32,
    "wins_b": pl.UInt32,
    "ties": pl.UInt32,
    "avg_margin": pl.Float64,
    "biggest_blowout_margin": pl.Float64,
    "biggest_blowout_winner": pl.String,
    "biggest_blowout_season": pl.Int64,
    "biggest_blowout_week": pl.Int64,
}

_PHASE_SCHEMA = {"phase": pl.String, **_SCHEMA}

PHASE_REGULAR_SEASON = "regular_season"
PHASE_PLAYOFF = "playoff"


def _named_pairs(matchups: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """Pair weekly matchups and resolve both sides to a real manager name.

    Shared by :func:`head_to_head` and :func:`head_to_head_by_phase` so the
    two can't drift on how a roster resolves to a manager, or how a
    self-matchup/unresolvable-manager row gets excluded.

    Returns:
        One row per real matchup side, resolved to ``manager``/``opponent``
        (both directions present), with every column
        :func:`~nuclearff.sleeper.wins.paired_weekly_matchups` carries
        through (``league_id``, ``season``, ``week``, ``margin``, ...).
    """
    paired = paired_weekly_matchups(matchups)
    if paired.height == 0:
        return paired

    names = standings.select("league_id", "roster_id", "display_name")
    joined = (
        paired.join(names, on=["league_id", "roster_id"], how="inner")
        .rename({"display_name": "manager"})
        .join(
            names.rename(
                {"roster_id": "opponent_roster_id", "display_name": "opponent"}
            ),
            on=["league_id", "opponent_roster_id"],
            how="inner",
        )
        .filter(
            pl.col("manager").is_not_null()
            & pl.col("opponent").is_not_null()
            & (pl.col("manager") != pl.col("opponent"))
        )
    )
    return joined


def _canonicalize(named_pairs: pl.DataFrame) -> pl.DataFrame:
    """Collapse each matchup's two rows (one per side) to one canonical row.

    Args:
        named_pairs: :func:`_named_pairs` output.

    Returns:
        One row per real matchup, ``manager_a`` < ``manager_b``
        alphabetically so a pair is never double-counted in both
        directions, with an ``abs_margin`` column added.
    """
    canonical = named_pairs.filter(pl.col("manager") < pl.col("opponent")).rename(
        {
            "manager": "manager_a",
            "opponent": "manager_b",
            "points": "manager_a_points",
            "opponent_points": "manager_b_points",
        }
    )
    return canonical.with_columns(abs_margin=pl.col("margin").abs())


def _summarize(canonical: pl.DataFrame) -> pl.DataFrame:
    """Reduce canonical matchup rows to one head-to-head record per pair.

    Args:
        canonical: :func:`_canonicalize` output (optionally pre-filtered,
            e.g. to one phase).

    Returns:
        Rows keyed by :data:`_SCHEMA`, sorted by ``games`` descending. Empty
        (but correctly typed) if ``canonical`` is empty.
    """
    if canonical.height == 0:
        return pl.DataFrame(schema=_SCHEMA)

    summary = canonical.group_by(["manager_a", "manager_b"]).agg(
        pl.len().cast(pl.UInt32).alias("games"),
        (pl.col("margin") > 0).sum().cast(pl.UInt32).alias("wins_a"),
        (pl.col("margin") < 0).sum().cast(pl.UInt32).alias("wins_b"),
        (pl.col("margin") == 0).sum().cast(pl.UInt32).alias("ties"),
        pl.col("abs_margin").mean().alias("avg_margin"),
    )

    blowouts = (
        canonical.sort("abs_margin", descending=True)
        .group_by(["manager_a", "manager_b"], maintain_order=True)
        .first()
        .select(
            "manager_a",
            "manager_b",
            "season",
            "week",
            "margin",
            biggest_blowout_margin=pl.col("abs_margin"),
            biggest_blowout_winner=pl.when(pl.col("margin") > 0)
            .then(pl.col("manager_a"))
            .when(pl.col("margin") < 0)
            .then(pl.col("manager_b"))
            .otherwise(pl.lit(None)),
        )
        .rename({"season": "biggest_blowout_season", "week": "biggest_blowout_week"})
    )

    result = summary.join(blowouts, on=["manager_a", "manager_b"], how="inner")
    return result.select(list(_SCHEMA.keys())).sort("games", descending=True)


def head_to_head_by_phase(
    matchups: pl.DataFrame, standings: pl.DataFrame, league_configs: pl.DataFrame
) -> pl.DataFrame:
    """Head-to-head record for every manager pair, split by season phase (issue #137).

    The regular-season and playoff splits are computed exactly like
    :func:`head_to_head`, just pre-filtered by week before summarizing —
    same join, same de-duplication, same blowout definition.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows, for ``display_name``.
        league_configs: ``sleeper_league_configs`` rows (one per
            ``(league_id, season)``), for ``playoff_week_start``.

    Returns:
        The same columns as :func:`head_to_head`, plus a ``phase`` column
        (:data:`PHASE_REGULAR_SEASON` or :data:`PHASE_PLAYOFF`) — one pair
        of rows per manager pair, not a replacement for the combined
        record. A pair's ``regular_season`` + ``playoff`` ``games`` sum to
        the same total :func:`head_to_head` reports for that pair,
        *provided* every one of their meetings falls in a season with a
        resolvable ``playoff_week_start`` (see below).

        A ``(league_id, season)`` with no resolvable ``playoff_week_start``
        (missing from ``league_configs``, or null — e.g. a "Chopped"
        league with no bracket) is excluded from **both** phases rather
        than guessed into one — matching this project's general "don't
        guess a playoff boundary" posture. That means the cross-check
        above only holds when every season in ``matchups`` resolves; a
        mixed history (some seasons resolvable, some not) will show a
        combined total larger than the two phases' sum, by design.
    """
    named = _named_pairs(matchups, standings)
    if named.height == 0:
        return pl.DataFrame(schema=_PHASE_SCHEMA)

    boundaries = league_configs.select("league_id", "playoff_week_start").filter(
        pl.col("playoff_week_start").is_not_null()
    )
    phased = named.join(boundaries, on="league_id", how="inner").with_columns(
        pl.when(pl.col("week") >= pl.col("playoff_week_start"))
        .then(pl.lit(PHASE_PLAYOFF))
        .otherwise(pl.lit(PHASE_REGULAR_SEASON))
        .alias("phase")
    )
    if phased.height == 0:
        return pl.DataFrame(schema=_PHASE_SCHEMA)

    canonical = _canonicalize(phased)

    results = [
        _summarize(canonical.filter(pl.col("phase") == phase)).with_columns(
            pl.lit(phase).alias("phase")
        )
        for phase in (PHASE_REGULAR_SEASON, PHASE_PLAYOFF)
    ]
    combined = pl.concat(results, how="vertical")
    return combined.select(list(_PHASE_SCHEMA.keys())).sort(
        ["games", "phase"], descending=[True, False]
    )


def head_to_head(matchups: pl.DataFrame, standings: pl.DataFrame) -> pl.DataFrame:
    """All-time combined head-to-head record for every manager pair that has met.

    Args:
        matchups: ``sleeper_matchups`` rows.
        standings: ``sleeper_standings`` rows, for ``display_name``.

    Returns:
        One row per manager pair (``manager_a`` < ``manager_b``
        alphabetically, so a pair is never double-counted in both
        directions): ``games``, ``wins_a``/``wins_b``/``ties``,
        ``avg_margin`` (mean absolute point differential across their
        meetings — a competitiveness measure, not signed toward either
        manager), and the pair's own ``biggest_blowout_margin`` /
        ``biggest_blowout_winner`` / ``biggest_blowout_season`` /
        ``biggest_blowout_week`` (``biggest_blowout_winner`` is ``None``
        only if every meeting between this pair has been a tie).

        A roster pair that shares the same resolved ``display_name`` (data
        anomaly, not expected in real data) is excluded rather than
        collapsed into a self-matchup — same posture as excluding an
        unresolvable manager entirely. See :func:`head_to_head_by_phase`
        for the same record split into regular-season and playoff meetings.
    """
    named = _named_pairs(matchups, standings)
    if named.height == 0:
        return pl.DataFrame(schema=_SCHEMA)
    return _summarize(_canonicalize(named))
