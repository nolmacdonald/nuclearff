"""WR route-participation efficiency: routes-run join, YPRR, TPRR.

Per the technical plan's "A.2 Efficiency metrics", YPRR (yards per route
run) and TPRR (targets per route run) are the strongest stable
receiving-efficiency signals available — their denominator (routes) is large
and far less noisy than targets or catches. Both require a ``routes_run``
column, which does not exist anywhere in nflreadpy's aggregated player-stats
tables and has to be joined in from
:func:`nuclearff.nflverse.stats.load_routes` — see :func:`join_routes`.

ID crosswalk: two different ID schemes, no direct overlap
------------------------------------------------------------
:func:`nuclearff.nflverse.stats.load_routes` is keyed by ``pfr_player_id``
(Pro Football Reference format, e.g. ``"JeffJu00"``), while
:func:`nuclearff.nflverse.stats.load_weekly_receiving` /
:func:`nuclearff.nflverse.stats.load_seasonal_receiving` are keyed by
``player_id`` in gsis_id format (e.g. ``"00-0036322"``) — confirmed live
these do not overlap directly. The bridge is
:func:`nuclearff.nflverse.stats.load_players`, whose ``pfr_id`` column
matches ``load_routes``'s ``pfr_player_id`` format and whose ``gsis_id``
column matches the receiving loaders' ``player_id`` format. Confirmed live
for Justin Jefferson: ``gsis_id`` ``"00-0036322"`` <-> ``pfr_id``
``"JeffJu00"``.

Also confirmed live: ``load_players()`` really does contain at least one
genuine name collision — two different real players both named "Justin
Jefferson" (a Vikings WR born 1999, LSU, and an Alabama linebacker born
2003), with entirely different ``gsis_id``/``pfr_id`` pairs. This is real,
not a hypothetical, and it is exactly why :func:`join_routes` joins through
IDs only and never falls back to matching on ``display_name`` — matching
this project's established "no silent fuzzy matches" convention from
:mod:`nuclearff.ids.crosswalk`. (For what it's worth: despite that name
collision, a live check of the full ``load_players()`` table found the
``(pfr_id, gsis_id)`` bridge itself is clean — every ``pfr_id`` with a
non-null ``gsis_id`` maps to exactly one ``gsis_id`` and vice versa, zero
ambiguous pairs. :func:`ambiguous_player_id_pairs` and the rejection logic
in :func:`join_routes` are kept anyway, defensively, in case a future
nflverse release introduces one — same posture as
:func:`nuclearff.ids.crosswalk.ambiguous_sleeper_ids`.)

The proxy-data caveat (read before trusting a YPRR/TPRR number)
------------------------------------------------------------------
Every ``routes_run`` value reachable through :func:`join_routes` right now
comes from :func:`nuclearff.nflverse.stats.load_routes`'s
``"offense_snaps_proxy"`` source — there is currently no real per-player
routes-run data available via nflreadpy for *any* season (see that
function's module docstring for the live investigation that established
this: ``load_ftn_charting`` and ``load_participation`` were both ruled out).
Offense snaps overstate true routes run (they include run-blocking snaps and
decoy routes), so every :func:`yprr`/:func:`tprr` value this module produces
today is really "receiving production / an offense-snaps-based overestimate
of routes run", which **understates** true YPRR/TPRR by an amount that
varies with each player's run-blocking/decoy-route usage. Both functions
repeat this warning in their own docstrings and carry the ``routes_source``
column through their output so a caller can see it at the row level too.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

logger = logging.getLogger(__name__)

_RECEIVING_REQUIRED_COLUMNS = ("season", "player_id", "receiving_yards", "targets")
_ROUTES_REQUIRED_COLUMNS = ("season", "pfr_player_id", "routes_run", "source")
_PLAYERS_REQUIRED_COLUMNS = ("gsis_id", "pfr_id")


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


def ambiguous_player_id_pairs(players: pl.DataFrame) -> pl.DataFrame:
    """Return ``(pfr_id, gsis_id)`` pairs from ``players`` that point two ways.

    An unambiguous bridge row has a ``pfr_id`` that maps to exactly one
    ``gsis_id`` and a ``gsis_id`` that maps to exactly one ``pfr_id``. A row
    on either side of that failing is reported here and excluded by
    :func:`join_routes`, never guessed at — the same "skip rather than
    guess" posture as
    :func:`nuclearff.ids.crosswalk.ambiguous_sleeper_ids`, which solved the
    analogous problem for the Sleeper<->gsis_id crosswalk.

    A live check of the real ``load_players()`` table (nflreadpy 0.1.5)
    found zero such pairs — despite it containing a genuine *name*
    collision (two "Justin Jefferson"s), the ID bridge itself was clean.
    This function exists as a defensive guard for a future nflverse release
    that might introduce one, not because one is currently known to exist.

    Args:
        players: The nflverse player table
            (:func:`nuclearff.nflverse.stats.load_players` output), with at
            least ``pfr_id`` and ``gsis_id`` columns.

    Returns:
        Distinct ``(pfr_id, gsis_id)`` rows involved in an ambiguous
        mapping, in either direction. Empty when the bridge is clean.
    """
    _require_columns(players, _PLAYERS_REQUIRED_COLUMNS, "ambiguous_player_id_pairs")

    bridge = (
        players.filter(pl.col("pfr_id").is_not_null() & pl.col("gsis_id").is_not_null())
        .select("pfr_id", "gsis_id")
        .unique()
    )

    ambiguous_pfr_ids = (
        bridge.group_by("pfr_id")
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select("pfr_id")
    )
    ambiguous_gsis_ids = (
        bridge.group_by("gsis_id")
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select("gsis_id")
    )

    from_pfr = bridge.join(ambiguous_pfr_ids, on="pfr_id", how="inner")
    from_gsis = bridge.join(ambiguous_gsis_ids, on="gsis_id", how="inner")
    return pl.concat([from_pfr, from_gsis]).unique().sort("pfr_id")


def join_routes(
    receiving: pl.DataFrame, routes: pl.DataFrame, players: pl.DataFrame
) -> pl.DataFrame:
    """Join routes-run onto seasonal receiving data via the pfr_id<->gsis_id bridge.

    Adds ``routes_run`` and ``routes_source`` columns to ``receiving``. Both
    are null wherever no match exists — a player
    :func:`nuclearff.nflverse.stats.load_routes` has no row for (pre-2012
    coverage), or whose position was outside WR/RB/TE there, does **not**
    silently disappear from the output; ``receiving`` is the base of a left
    join, so every one of its rows is preserved regardless of match.

    GRAIN NOTE - seasonal only: ``load_routes`` returns one row per player
    per **season** (``offense_snaps`` already summed to a season total
    inside it). This function is meant for
    :func:`nuclearff.nflverse.stats.load_seasonal_receiving` output, not
    weekly — it raises ``ValueError`` if ``receiving`` has a ``week``
    column, rather than repeating one season-total ``routes_run`` onto every
    weekly row of a player without saying so. (Aggregating weekly receiving
    up to season grain first was considered instead of raising, but doing
    that correctly runs straight into the same traded-player team-grain trap
    documented on :func:`nuclearff.metrics.volume.target_share` — summing a
    traded player's weekly rows needs the same care nflreadpy's own
    ``recent_team`` seasonal collapse gets wrong. Raising and asking the
    caller to use seasonal data directly avoids re-solving that problem
    inside this function.)

    A traded player can appear as **two** rows in ``routes`` for the same
    season (one per team, since ``load_routes`` groups by
    ``(season, pfr_player_id, team)``); this function sums ``routes_run``
    across those rows per ``(season, pfr_player_id)`` before joining, so a
    traded player's season ``routes_run`` is their full-season total, not
    just one team's.

    Ambiguous ``(pfr_id, gsis_id)`` pairs in ``players`` (see
    :func:`ambiguous_player_id_pairs`) are excluded from the bridge and
    logged, never guessed at.

    Args:
        receiving: Seasonal receiving data
            (:func:`nuclearff.nflverse.stats.load_seasonal_receiving`
            output), with at least ``season`` and ``player_id`` (gsis_id
            format) columns.
        routes: Routes-run data
            (:func:`nuclearff.nflverse.stats.load_routes` output), with at
            least ``season``, ``pfr_player_id``, ``routes_run``, and
            ``source`` columns.
        players: The nflverse player bridge table
            (:func:`nuclearff.nflverse.stats.load_players` output), with at
            least ``pfr_id`` and ``gsis_id`` columns.

    Returns:
        ``receiving`` with ``routes_run`` and ``routes_source`` added.

    Raises:
        ValueError: If ``receiving`` has a ``week`` column (weekly-grain
            input), or any input is missing a required column.
    """
    if "week" in receiving.columns:
        raise ValueError(
            "join_routes expects seasonal-grain receiving data (no 'week' "
            "column) because nuclearff.nflverse.stats.load_routes reports "
            "one routes_run total per player per season - pass "
            "load_seasonal_receiving() output, not load_weekly_receiving() "
            "output. Repeating a season total onto every weekly row would "
            "be misleading if summed across weeks later."
        )
    _require_columns(receiving, _RECEIVING_REQUIRED_COLUMNS, "join_routes")
    _require_columns(routes, _ROUTES_REQUIRED_COLUMNS, "join_routes")
    _require_columns(players, _PLAYERS_REQUIRED_COLUMNS, "join_routes")

    ambiguous = ambiguous_player_id_pairs(players)
    if ambiguous.height:
        logger.warning(
            "join_routes: excluding %d ambiguous (pfr_id, gsis_id) pair(s) from "
            "the join rather than guessing which direction is correct: %s",
            ambiguous.height,
            ambiguous.rows(),
        )

    bridge = (
        players.filter(pl.col("pfr_id").is_not_null() & pl.col("gsis_id").is_not_null())
        .select("pfr_id", "gsis_id")
        .unique()
        .join(
            ambiguous.select("pfr_id", "gsis_id"), on=["pfr_id", "gsis_id"], how="anti"
        )
    )

    routes_by_player = (
        routes.filter(pl.col("pfr_player_id").is_not_null())
        .group_by(["season", "pfr_player_id"])
        .agg(
            pl.col("routes_run").sum().alias("routes_run"),
            pl.col("source").first().alias("routes_source"),
        )
    )

    routes_bridged = routes_by_player.join(
        bridge, left_on="pfr_player_id", right_on="pfr_id", how="left"
    ).select("season", "gsis_id", "routes_run", "routes_source")

    return receiving.join(
        routes_bridged,
        left_on=["season", "player_id"],
        right_on=["season", "gsis_id"],
        how="left",
    )


def yprr(df: pl.DataFrame, min_routes: int = 200) -> pl.DataFrame:
    """Add a ``yprr`` column: yards per route run, sample-gated.

    ``YPRR = receiving_yards / routes_run``, computed only where
    ``routes_run >= min_routes``; below that threshold the value is null
    rather than a small-sample mirage (the plan's "A.8 Regression and
    normalization" minimum-sample-thresholds guard). Requires ``df`` to
    already have a ``routes_run`` column — i.e. to have already been passed
    through :func:`join_routes` — and raises ``ValueError`` naming the fix
    if it does not, rather than silently computing garbage against a column
    that does not exist.

    PROXY-DATA WARNING: every ``routes_run`` value this project currently
    has access to comes from
    :func:`nuclearff.nflverse.stats.load_routes`'s
    ``"offense_snaps_proxy"`` source — there is **no real per-player
    routes-run data available via nflreadpy for any season** right now (see
    that function's module docstring for the live investigation that
    established this). So every ``yprr`` value this function produces today
    is really "receiving_yards / (an offense-snaps-based overestimate of
    routes run)", which **understates** true YPRR by an amount that varies
    with each player's run-blocking/decoy-route usage. Do not treat a
    ``yprr`` value from this function as directly comparable to a
    publicly-reported YPRR figure computed from real charted routes without
    accounting for that gap. The ``routes_source`` column (carried through
    unchanged from ``df``) lets a caller see this at the row level, not just
    read about it here.

    Args:
        df: Receiving data already carrying ``routes_run`` (and typically
            ``routes_source``), e.g. :func:`join_routes` output.
        min_routes: Minimum season routes run required to trust the rate.
            Below this, ``yprr`` is null rather than computed on a small
            sample.

    Returns:
        ``df`` with ``yprr`` added.

    Raises:
        ValueError: If ``df`` has no ``routes_run`` column, or is missing
            ``receiving_yards``.
    """
    if "routes_run" not in df.columns:
        raise ValueError(
            "yprr requires a 'routes_run' column - run join_routes() first "
            "to attach it from nflverse's snap-count-based routes proxy "
            "(see nuclearff.nflverse.stats.load_routes)."
        )
    _require_columns(df, ("receiving_yards",), "yprr")

    return df.with_columns(
        pl.when(pl.col("routes_run") >= min_routes)
        .then(pl.col("receiving_yards") / pl.col("routes_run"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("yprr")
    )


def tprr(df: pl.DataFrame, min_routes: int = 200) -> pl.DataFrame:
    """Add a ``tprr`` column: targets per route run, sample-gated.

    ``TPRR = targets / routes_run``, same shape as :func:`yprr`: computed
    only where ``routes_run >= min_routes``, null below that. Of the trio
    {YPRR, Y/T, TPRR}, the plan's "A.4 Stability and predictiveness" table
    calls TPRR the *most consistent year-to-year* — it isolates a
    receiver's ability to earn a target independent of what he does with
    it, a cleaner separation/role proxy than raw target share.

    That stability is about TPRR's target-earning signal, not about the
    routes-run data it is divided by: it inherits the exact same
    PROXY-DATA WARNING as :func:`yprr` — every ``routes_run`` value
    available here comes from an offense-snaps-based overestimate (see that
    function's docstring for the full explanation), which understates true
    TPRR by an amount that varies per player. Requires ``df`` to already
    have a ``routes_run`` column (i.e. to have been passed through
    :func:`join_routes`); raises ``ValueError`` naming the fix if not.

    Args:
        df: Receiving data already carrying ``routes_run`` (and typically
            ``routes_source``), e.g. :func:`join_routes` output.
        min_routes: Minimum season routes run required to trust the rate.
            Below this, ``tprr`` is null rather than computed on a small
            sample.

    Returns:
        ``df`` with ``tprr`` added.

    Raises:
        ValueError: If ``df`` has no ``routes_run`` column, or is missing
            ``targets``.
    """
    if "routes_run" not in df.columns:
        raise ValueError(
            "tprr requires a 'routes_run' column - run join_routes() first "
            "to attach it from nflverse's snap-count-based routes proxy "
            "(see nuclearff.nflverse.stats.load_routes)."
        )
    _require_columns(df, ("targets",), "tprr")

    return df.with_columns(
        pl.when(pl.col("routes_run") >= min_routes)
        .then(pl.col("targets") / pl.col("routes_run"))
        .otherwise(pl.lit(None, dtype=pl.Float64))
        .alias("tprr")
    )
