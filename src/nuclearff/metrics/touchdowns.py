"""Expected-TD opportunity modeling and TD-luck regression.

Per the technical plan's "A.3 Red zone and touchdown modeling", touchdowns
are the highest-variance, least-predictable component of WR fantasy
scoring -- the plan cites year-over-year TD stability of roughly 0.28,
versus roughly 0.65 for receiving yards. Rather than trusting a player's
raw TD count at face value, :func:`expected_tds` regresses it toward an
opportunity-driven expected-TDs estimate (targets, red-zone looks, air
yards) by joining ffverse's own maintained expected-fantasy-opportunity
model (:func:`nuclearff.nflverse.stats.load_ff_opportunity`) onto a
receiving frame. A player who outscored their expected TDs was TD-lucky --
a *negative*-regression risk for next season; one who underscored is a
*positive*-regression buy candidate. The plan's own concrete 2026 example:
Justin Jefferson posted a ~28.5% target share but only 2 receiving TDs in
2025 -- a textbook positive-TD-regression case, not a fade.

This module does not reimplement ffverse's expected-opportunity model --
doing that from raw play-by-play would be a much larger undertaking than
this issue's scope, and ffverse's community-maintained model (built on real
red-zone/air-yards play-by-play) is the appropriate thing to wrap, the same
way this project already wraps nflreadpy's other loaders rather than
reimplementing them.

CRITICAL GRAIN WARNING
-----------------------
:func:`nuclearff.nflverse.stats.load_ff_opportunity` is **always weekly
grain** -- one row per player per game -- no seasonal aggregate is
available directly (confirmed live; see that function's docstring).
:func:`expected_tds` therefore has to aggregate ``opportunity`` up to
whatever grain ``df`` is at before joining:

- If ``df`` is weekly (it has a ``week`` column), ``opportunity`` is
  grouped by ``(season, week, player_id)`` and summed -- defensive, in case
  of a stray duplicate row, though in practice each such key already has at
  most one real (non-null-``player_id``) row.
- If ``df`` is seasonal (no ``week`` column), ``opportunity`` is grouped by
  ``(season, player_id)`` and its ``rec_touchdown_exp`` is **summed across
  every week of that season** before joining -- a real season total, not
  one week's value and not an average.

Getting this wrong -- joining raw weekly opportunity rows onto a seasonal
frame, or averaging instead of summing -- is the same class of grain
mistake the CRITICAL GRAIN WARNING in :mod:`nuclearff.metrics.volume`
exists to prevent, even though this specific case is an additive sum
rather than a share, so that module's "a team total is only unambiguous
per week" trade complication does not carry over unchanged:
``rec_touchdown_exp`` is a per-player value with no team-total denominator
to get wrong, it just needs the right group-by keys before the sum. The
discipline is the same even though the mechanics differ.

Two dtype quirks of ``opportunity`` (confirmed live -- see
:func:`nuclearff.nflverse.stats.load_ff_opportunity`'s docstring) are cast
away before the join: its ``season`` column comes back as a String (not
the ``Int32`` :func:`nuclearff.nflverse.stats.load_seasonal_receiving` /
:func:`nuclearff.nflverse.stats.load_weekly_receiving` return), and its
``week`` column comes back as Float64 (not their ``Int32``). Both are cast
to match ``df``'s own dtype before the join, rather than left to silently
join zero rows.

SIGN CONVENTION -- read this before using ``td_regression``
--------------------------------------------------------------
``td_regression = df["receiving_tds"] - expected_tds``.

- **Positive** ``td_regression``: the player scored *more* actual TDs than
  their opportunity predicted. They were TD-lucky -- a **negative**-
  regression risk (expect fewer TDs next time, all else equal).
- **Negative** ``td_regression``: the player scored *fewer* actual TDs than
  expected. They were TD-unlucky -- a **positive**-regression buy
  candidate, exactly like the plan's Jefferson example (2 actual TDs
  against a target-share-implied expectation well above that).

This is the single easiest thing to get backwards in this module, and
getting it backwards would silently invert every downstream regression
call built on it -- see ``TestSignConvention`` in the test suite for the
two hand-computed cases (one of each sign) that pin this down.

MINIMUM-SAMPLE CAVEAT
-----------------------
Per the plan's "A.3", an expected-TDs estimate is only meaningful behind a
real sample of red-zone looks/targets -- a 2-target rookie's
``td_regression`` is noise, not signal. This function does **not**
implement its own sample gate; that is
:class:`nuclearff.config.models.SampleThresholds`'s job elsewhere in the
pipeline (e.g. :func:`nuclearff.projection.blend.recency_weighted_rate`'s
existing ``min_games`` gate, when this feeds into a multi-season blend). Do
not treat a small-sample ``td_regression`` from this function alone as
meaningful signal without that downstream gate.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

logger = logging.getLogger(__name__)

_RECEIVING_REQUIRED_COLUMNS = ("player_id", "season", "receiving_tds")

_OPPORTUNITY_REQUIRED_COLUMNS = ("player_id", "season", "rec_touchdown_exp")


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


def expected_tds(df: pl.DataFrame, opportunity: pl.DataFrame) -> pl.DataFrame:
    """Add ``expected_tds`` and ``td_regression`` from ffverse's expected-TD model.

    Joins ``opportunity`` (ffverse's expected-fantasy-opportunity model,
    e.g. :func:`nuclearff.nflverse.stats.load_ff_opportunity` output) onto
    ``df`` by player (and season, and week if ``df`` is weekly-grain).
    ``opportunity`` is always weekly grain, so it is aggregated up to
    ``df``'s own grain first -- summed to a season total per player if
    ``df`` is seasonal. **See the module docstring's CRITICAL GRAIN
    WARNING** for exactly what that means and why it matters.

    Adds two columns:

    - ``expected_tds``: ``opportunity``'s ``rec_touchdown_exp``, aggregated
      to ``df``'s grain.
    - ``td_regression``: ``df["receiving_tds"] - expected_tds``. **See the
      module docstring's SIGN CONVENTION** -- positive means the player
      outscored their expected TDs (TD-luck-inflated, a *negative*-
      regression risk); negative means the opposite (a *positive*-
      regression buy candidate).

    A row in ``df`` whose player has no matching row in ``opportunity``
    (traded away, not tracked by ffverse's model, or simply outside the
    seasons ``opportunity`` covers) gets null ``expected_tds`` /
    ``td_regression`` -- it is **not** dropped from the output and this
    does **not** raise. How many rows this affected is logged as a
    warning.

    This function applies no minimum-sample gate of its own -- see the
    module docstring's MINIMUM-SAMPLE CAVEAT before treating a small-sample
    ``td_regression`` as meaningful.

    Args:
        df: A receiving frame -- weekly grain (has a ``week`` column) or
            seasonal grain (does not) -- with at least ``player_id``,
            ``season``, and ``receiving_tds`` columns, e.g.
            :func:`nuclearff.nflverse.stats.load_weekly_receiving` or
            :func:`nuclearff.nflverse.stats.load_seasonal_receiving`
            output.
        opportunity: Weekly-grain expected-opportunity data, e.g.
            :func:`nuclearff.nflverse.stats.load_ff_opportunity` output,
            with at least ``player_id``, ``season``, ``rec_touchdown_exp``
            (and ``week``, if ``df`` is weekly-grain) columns. Rows with a
            null ``player_id`` are dropped before aggregating (see that
            function's docstring for why real duplicate-key rows there are
            always null-``player_id`` placeholders).

    Returns:
        ``df`` with ``expected_tds`` and ``td_regression`` added.

    Raises:
        ValueError: If ``df`` is missing ``player_id``, ``season``, or
            ``receiving_tds``; if ``opportunity`` is missing ``player_id``,
            ``season``, or ``rec_touchdown_exp``; or if ``df`` is
            weekly-grain and ``opportunity`` has no ``week`` column.
    """
    _require_columns(df, _RECEIVING_REQUIRED_COLUMNS, "expected_tds")
    _require_columns(opportunity, _OPPORTUNITY_REQUIRED_COLUMNS, "expected_tds")

    weekly_grain = "week" in df.columns
    if weekly_grain:
        _require_columns(opportunity, ("week",), "expected_tds")

    group_keys = (
        ["season", "player_id", "week"] if weekly_grain else ["season", "player_id"]
    )

    # opportunity's season/week dtypes are confirmed-live quirks (String and
    # Float64 respectively, per load_ff_opportunity's docstring) - cast to
    # df's own dtype so the join keys actually match rather than silently
    # joining zero rows.
    prepared_opportunity = opportunity.filter(
        pl.col("player_id").is_not_null()
    ).with_columns(pl.col("season").cast(df.schema["season"]))
    if weekly_grain:
        prepared_opportunity = prepared_opportunity.with_columns(
            pl.col("week").cast(df.schema["week"])
        )

    opportunity_agg = prepared_opportunity.group_by(group_keys).agg(
        pl.col("rec_touchdown_exp").sum().alias("expected_tds")
    )

    result = df.join(opportunity_agg, on=group_keys, how="left")

    n_missing = result["expected_tds"].null_count()
    if n_missing:
        logger.warning(
            "expected_tds: %d of %d row(s) in `df` had no matching "
            "opportunity data - expected_tds/td_regression left null for "
            "those rows rather than dropped or erroring.",
            n_missing,
            result.height,
        )

    return result.with_columns(
        (pl.col("receiving_tds") - pl.col("expected_tds")).alias("td_regression")
    )
