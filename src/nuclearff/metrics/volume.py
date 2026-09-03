"""WR opportunity-volume signals: target share, air-yards share, WOPR, RACR, aDOT.

Per the technical plan's "A.1 Why volume dominates WR fantasy scoring", these are
the core, most year-over-year-stable inputs to WR valuation — a receiver's role
(how often he is targeted, how far downfield those targets are) predicts next
season's fantasy output far more reliably than last season's point total does.

nflreadpy's ``load_player_stats`` output (wrapped by
:func:`nuclearff.nflverse.stats.load_weekly_receiving` and
:func:`nuclearff.nflverse.stats.load_seasonal_receiving`) already ships
pre-computed ``target_share``, ``air_yards_share``, ``racr``, and ``wopr``
columns. Rather than trusting those blindly, or ignoring them, every function
below **recomputes the metric from its own raw inputs** (so the formula is
auditable inside this codebase, not hidden inside another library's compiled
release) and, when nflreadpy's own column is present, cross-checks the
recomputed value against it as a data-quality signal — see
:func:`_cross_check_against_nflreadpy`.

CRITICAL GRAIN WARNING
-----------------------
``target_share`` and ``air_yards_share`` are "player stat / team total stat",
and "team" is only unambiguous at the **weekly** grain
(:func:`nuclearff.nflverse.stats.load_weekly_receiving` output — one row per
player per game). A player traded mid-season has a *different* team in
different weeks. nflreadpy's seasonal summary
(:func:`nuclearff.nflverse.stats.load_seasonal_receiving`) collapses this into
one ``recent_team`` column holding only the player's **last** team of the
season, so a naive seasonal-grain team-total recompute would silently divide a
whole-season numerator by only the final team's season-total targets —
wrong for every traded player, and wrong in a way that would not show up
unless you went looking for it. :func:`target_share`, :func:`air_yards_share`,
and :func:`wopr` therefore all **require weekly-grain input** and raise
``ValueError`` (rather than guessing) if a ``week`` column is not present.
:func:`racr` and :func:`adot` are simple per-row ratios with no team grouping,
so this grain trap does not apply to them and they accept weekly or seasonal
data.

Live data-quality note (nflreadpy 0.1.5, full 2024 regular+postseason weekly
data via :func:`nuclearff.nflverse.stats.load_weekly_receiving`): recomputing
``target_share`` this way matches nflreadpy's own column almost exactly
(well under 1% of rows differ by more than 0.02 — the only source of drift
found was the rare team-week where a non-WR/RB/TE player, e.g. a fullback or
a trick-play QB target, drew a target that this project's pass-catcher-only
input does not count). ``air_yards_share``, by contrast, disagreed with
nflreadpy's own column by more than 0.02 on roughly 7% of rows, well above
:data:`_CROSS_CHECK_WARN_FRACTION` — this recompute could not fully isolate
nflreadpy's exact denominator (it is consistently *larger* than the simple
sum of this dataset's own ``receiving_air_yards`` column on the affected
rows, suggesting nflreadpy/nflfastR draws its team-total from a richer
play-by-play source that captures some targeted plays this per-player stats
table does not), so :func:`air_yards_share` reliably logs a warning on real
data. That is the cross-check doing its job, not a bug in this recompute —
see the module's ``integration_notes`` from the session that verified this
for the exact numbers.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

logger = logging.getLogger(__name__)

WOPR_TARGET_SHARE_WEIGHT = 1.5
"""Hermsmeyer's WOPR weight on target share."""

WOPR_AIR_YARDS_SHARE_WEIGHT = 0.7
"""Hermsmeyer's WOPR weight on air-yards share.

WOPR = 1.5 * target_share + 0.7 * air_yards_share, per the plan's "A.2
Efficiency metrics" section citing Josh Hermsmeyer's original definition
(fit to best predict PPR/standard fantasy points): raw target volume matters
more than air-yards concentration, but both matter.
"""

_CROSS_CHECK_TOLERANCE = 0.02
"""Loose absolute tolerance for comparing a recomputed share to nflreadpy's own."""

_CROSS_CHECK_WARN_FRACTION = 0.05
"""Fraction of compared rows allowed to exceed tolerance before logging a warning.

A "small fraction" per the project's data-quality posture: a real, systemic
methodology change in nflreadpy should move a large share of rows, not a
handful. See the module docstring for what this threshold actually caught
(and did not catch) against real 2024 data.
"""

_WEEKLY_GRAIN_COLUMNS = ("season", "week", "team")


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing a column this function needs.

    Args:
        df: The DataFrame passed to ``fn_name``.
        required: Column names ``fn_name`` depends on.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: input DataFrame is missing expected column(s) "
            f"{missing!r} (got {df.columns!r})."
        )


def _require_weekly_grain(df: pl.DataFrame, fn_name: str) -> None:
    """Reject seasonal-grain input rather than silently mis-computing a team share.

    See the module docstring's "CRITICAL GRAIN WARNING" for why: a
    (season, team) or (player) total is ambiguous for a traded player unless
    "team" is scoped to a single week.

    Args:
        df: The DataFrame passed to ``fn_name``.
        fn_name: Name of the calling function, included in the error message.

    Raises:
        ValueError: If ``df`` has no ``week`` column.
    """
    if "week" not in df.columns:
        raise ValueError(
            f"{fn_name} requires weekly-grain input (a 'week' column) because "
            f"team totals are only unambiguous at the (season, week, team) "
            f"grain - a player traded mid-season has a different team in "
            f"different weeks, and nflreadpy's seasonal summary collapses "
            f"this into a single 'recent_team' column that silently produces "
            f"the wrong number for that player. Pass "
            f"nuclearff.nflverse.stats.load_weekly_receiving() output (or "
            f"data shaped like it) instead of seasonal data."
        )


def _cross_check_against_nflreadpy(
    recomputed: pl.Series, published: pl.Series, metric_name: str
) -> None:
    """Warn if a recomputed share disagrees with nflreadpy's own column too often.

    Never raises: a disagreement is a signal to go investigate nflreadpy's
    current methodology, not proof this recompute is wrong.

    Args:
        recomputed: This project's recomputed values.
        published: nflreadpy's own column of the same name, before it was
            overwritten by the recompute.
        metric_name: Name of the metric, for the log message.
    """
    diff = (recomputed - published).abs().drop_nulls()
    if diff.len() == 0:
        return

    n_bad = int((diff > _CROSS_CHECK_TOLERANCE).sum())
    fraction = n_bad / diff.len()
    if fraction > _CROSS_CHECK_WARN_FRACTION:
        logger.warning(
            "%s: recomputed values disagree with nflreadpy's own %r column by "
            "more than %.2f on %d/%d rows (%.1f%%). A real disagreement this "
            "large usually means something subtle changed in nflreadpy's own "
            "methodology - worth investigating rather than assuming this "
            "recompute is wrong.",
            metric_name,
            metric_name,
            _CROSS_CHECK_TOLERANCE,
            n_bad,
            diff.len(),
            fraction * 100,
        )


def target_share(df: pl.DataFrame) -> pl.DataFrame:
    """Add/overwrite a ``target_share`` column: player targets / team targets.

    Computed as ``targets / sum(targets)`` within each (season, week, team)
    group of ``df`` itself. **Requires weekly-grain input** — see the module
    docstring's "CRITICAL GRAIN WARNING"; this raises ``ValueError`` rather
    than silently mis-computing a team total if ``df`` has no ``week``
    column.

    If ``df`` already carries nflreadpy's own ``target_share`` column, the
    recomputed value cross-checks against it (see
    :func:`_cross_check_against_nflreadpy`) before overwriting it. Note the
    denominator here is the sum of ``targets`` within the *rows actually
    present in* ``df`` — if ``df`` is restricted to pass-catching positions
    (as :func:`nuclearff.nflverse.stats.load_weekly_receiving` is), a rare
    team-week where a non-WR/RB/TE player drew a target (a fullback, or a QB
    on a trick play — both real, observed live in 2024) will differ slightly
    from nflreadpy's own team-wide denominator. This was confirmed live to
    be a small effect (well under the warn threshold): see the module
    docstring.

    A team-week where the target total is zero or negative yields null, not
    a divide error.

    Args:
        df: Weekly receiving data with ``season``, ``week``, ``team``, and
            ``targets`` columns, e.g.
            :func:`nuclearff.nflverse.stats.load_weekly_receiving` output.

    Returns:
        ``df`` with ``target_share`` added or overwritten.

    Raises:
        ValueError: If ``df`` has no ``week`` column, or is missing
            ``season``, ``team``, or ``targets``.
    """
    _require_weekly_grain(df, "target_share")
    _require_columns(df, (*_WEEKLY_GRAIN_COLUMNS, "targets"), "target_share")

    published = df["target_share"] if "target_share" in df.columns else None

    team_targets = pl.col("targets").sum().over(list(_WEEKLY_GRAIN_COLUMNS))
    result = df.with_columns(
        pl.when(team_targets <= 0)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("targets") / team_targets)
        .alias("target_share")
    )

    if published is not None:
        _cross_check_against_nflreadpy(
            result["target_share"], published, "target_share"
        )

    return result


def air_yards_share(df: pl.DataFrame) -> pl.DataFrame:
    """Add/overwrite an ``air_yards_share`` column: player air yards / team air yards.

    Computed as ``receiving_air_yards / sum(receiving_air_yards)`` within
    each (season, week, team) group of ``df`` itself. **Requires
    weekly-grain input** — see :func:`target_share` and the module
    docstring's "CRITICAL GRAIN WARNING"; raises ``ValueError`` if ``df`` has
    no ``week`` column.

    A team-week where total air yards is zero or negative yields null for
    every player in that group, not a divide error or a nonsensical share.
    This guard is defensive: a live check against the full 2024
    regular+postseason (570 team-weeks) found zero team-weeks with a
    non-positive air-yards total, but an *individual* player-week can
    legitimately have negative ``receiving_air_yards`` (confirmed live: 745
    of 5,444 2024 WR/RB/TE weekly rows, from targets thrown behind the line
    of scrimmage) — one bad week from a small handful of low-usage players
    on an otherwise normal team is exactly the scenario this guard exists
    for even though it was not observed this season.

    If ``df`` already carries nflreadpy's own ``air_yards_share`` column,
    the recomputed value cross-checks against it. **This cross-check is
    expected to warn on real data** — see the module docstring for the live
    finding (~7% of 2024 rows disagree by more than 0.02) and what it does
    and does not mean.

    Args:
        df: Weekly receiving data with ``season``, ``week``, ``team``, and
            ``receiving_air_yards`` columns, e.g.
            :func:`nuclearff.nflverse.stats.load_weekly_receiving` output.

    Returns:
        ``df`` with ``air_yards_share`` added or overwritten.

    Raises:
        ValueError: If ``df`` has no ``week`` column, or is missing
            ``season``, ``team``, or ``receiving_air_yards``.
    """
    _require_weekly_grain(df, "air_yards_share")
    _require_columns(
        df, (*_WEEKLY_GRAIN_COLUMNS, "receiving_air_yards"), "air_yards_share"
    )

    published = df["air_yards_share"] if "air_yards_share" in df.columns else None

    team_air_yards = (
        pl.col("receiving_air_yards").sum().over(list(_WEEKLY_GRAIN_COLUMNS))
    )
    result = df.with_columns(
        pl.when(team_air_yards <= 0)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("receiving_air_yards") / team_air_yards)
        .alias("air_yards_share")
    )

    if published is not None:
        _cross_check_against_nflreadpy(
            result["air_yards_share"], published, "air_yards_share"
        )

    return result


def wopr(df: pl.DataFrame) -> pl.DataFrame:
    """Add/overwrite a ``wopr`` column: Weighted Opportunity Rating (Hermsmeyer).

    ``WOPR = 1.5 * target_share + 0.7 * air_yards_share`` (the exact weights
    Hermsmeyer fit to best predict PPR/standard fantasy points — see the
    plan's "A.2 Efficiency metrics" and :data:`WOPR_TARGET_SHARE_WEIGHT` /
    :data:`WOPR_AIR_YARDS_SHARE_WEIGHT`). Composes :func:`target_share` and
    :func:`air_yards_share` rather than reimplementing the share math a
    third time: each is called only if its output column is not already
    present in ``df``.

    **Requires weekly-grain input**, unconditionally — see the module
    docstring's "CRITICAL GRAIN WARNING". This is checked here even when
    ``target_share``/``air_yards_share`` are already present, because those
    columns could be nflreadpy's own *seasonal* columns (nflreadpy publishes
    ``target_share``/``air_yards_share`` at both grains), which are exactly
    the silently-wrong-for-traded-players values this project's grain
    warning exists to catch. A caller who wants WOPR built on this
    project's audited recompute rather than a pre-existing column should
    call :func:`target_share`/:func:`air_yards_share` explicitly first, or
    simply pass weekly data through untouched and let this function do it.
    WOPR is a within-team share; the plan's own caveat applies unchanged —
    pair it with team pass volume before comparing across offenses.

    Args:
        df: Weekly receiving data — either already carrying ``target_share``
            and ``air_yards_share`` (this project's own, or nflreadpy's own
            weekly columns), or raw enough for :func:`target_share` /
            :func:`air_yards_share` to compute them.

    Returns:
        ``df`` with ``wopr`` added or overwritten (and ``target_share`` /
        ``air_yards_share`` added, if they were not already present).

    Raises:
        ValueError: If ``df`` has no ``week`` column.
    """
    _require_weekly_grain(df, "wopr")

    if "target_share" not in df.columns:
        df = target_share(df)
    if "air_yards_share" not in df.columns:
        df = air_yards_share(df)

    return df.with_columns(
        (
            WOPR_TARGET_SHARE_WEIGHT * pl.col("target_share")
            + WOPR_AIR_YARDS_SHARE_WEIGHT * pl.col("air_yards_share")
        ).alias("wopr")
    )


def racr(df: pl.DataFrame) -> pl.DataFrame:
    """Add/overwrite a ``racr`` column: Receiver Air Conversion Ratio.

    ``RACR = receiving_yards / receiving_air_yards``, per row — no team
    grouping, so this works on weekly **or** seasonal data (the grain trap
    documented on :func:`target_share` does not apply here). Values > 1.0
    indicate strong YAC/possession efficiency; deep threats naturally run
    lower.

    A row with zero or negative ``receiving_air_yards`` yields null, not
    ``inf`` or a crash. Negative ``receiving_air_yards`` is real, observed
    data (confirmed live: 745 of 5,444 2024 WR/RB/TE weekly rows), not a
    theoretical edge case.

    Args:
        df: Receiving data with ``receiving_yards`` and
            ``receiving_air_yards`` columns, weekly or seasonal grain.

    Returns:
        ``df`` with ``racr`` added or overwritten.

    Raises:
        ValueError: If ``df`` is missing ``receiving_yards`` or
            ``receiving_air_yards``.
    """
    _require_columns(df, ("receiving_yards", "receiving_air_yards"), "racr")

    return df.with_columns(
        pl.when(pl.col("receiving_air_yards") <= 0)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("receiving_yards") / pl.col("receiving_air_yards"))
        .alias("racr")
    )


def adot(df: pl.DataFrame) -> pl.DataFrame:
    """Add/overwrite an ``adot`` column: average depth of target.

    ``aDOT = receiving_air_yards / targets``, per row — no team grouping, so
    this works on weekly **or** seasonal data.

    A row with zero targets yields null, not a divide error. Zero-target
    rows are real and common (confirmed live: 1,058 of 5,444 2024 WR/RB/TE
    weekly rows — a game where a rostered pass-catcher was never targeted).

    Args:
        df: Receiving data with ``receiving_air_yards`` and ``targets``
            columns, weekly or seasonal grain.

    Returns:
        ``df`` with ``adot`` added or overwritten.

    Raises:
        ValueError: If ``df`` is missing ``receiving_air_yards`` or
            ``targets``.
    """
    _require_columns(df, ("receiving_air_yards", "targets"), "adot")

    return df.with_columns(
        pl.when(pl.col("targets") == 0)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("receiving_air_yards") / pl.col("targets"))
        .alias("adot")
    )
