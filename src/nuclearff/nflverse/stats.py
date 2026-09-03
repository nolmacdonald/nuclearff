"""Thin wrappers around ``nflreadpy`` for receiving statistics.

Every function here delegates straight to the matching ``nflreadpy`` loader
and does the minimum extra work the plan asks for (filtering to pass-catching
positions, or gluing together season coverage for :func:`load_routes`) —
never a network call outside of that one delegated call, and no reshaping
beyond what is documented on the function itself.

nflreadpy is young (pinned at 0.1.5 for this module; its own README calls its
lifecycle "experimental") and returns whatever columns the current nflverse
release happens to publish. Every function below validates the columns it
depends on via :func:`_require_columns` and raises ``ValueError`` naming the
gap rather than letting a schema drift silently corrupt a downstream join.

Cache configuration is the caller's responsibility (see
:func:`nuclearff.nflverse.loader.configure_cache`) — nothing here touches it.

Routes-run finding
-------------------
The original technical plan named ``nflreadpy.load_ftn_charting`` as "the
real routes-run source" for 2022+, with ``load_snap_counts``/
``load_participation`` as pre-2022 fallbacks. Live introspection against the
installed nflreadpy 0.1.5 found this is not accurate: ``load_ftn_charting``
returns **play-level** charting (pre-snap personnel, motion, play-action,
pass-rush counts — 29 columns per the official data dictionary at
https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html) with no
player identifier and no per-player route count at all, for any season.
``load_participation`` is also play-level: its one ``route`` column charts
only the *targeted* receiver on a play (roughly 63% null in a spot check of
the 2021 season) and the table has no pass/rush indicator of its own, so
deriving a real per-player routes count from it would require an additional
join against ``load_pbp`` — out of scope for a thin wrapper. See
:func:`load_routes` for what this module does instead.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import nflreadpy
import polars as pl

logger = logging.getLogger(__name__)

PASS_CATCHING_POSITIONS = ("WR", "RB", "TE")
"""Positions kept by every receiving loader in this module.

Observed live in ``load_player_stats``: the full ``position`` column also
carries non-pass-catching values (``QB``, offensive-line and defensive
positions, ``FB``, ``K``, ``P``, etc., plus ``None`` for some rows) that this
project has no use for.
"""

SKILL_POSITIONS = (*PASS_CATCHING_POSITIONS, "QB")
"""Positions kept by the skill-position loaders below.

QB was originally excluded project-wide (this tool's Milestone A/B scope was
WR-only) — added here for the auction/keeper valuation work, which needs
:class:`~nuclearff.config.league.LeagueConfig`'s now-generalized replacement
rank to have a real QB points column to rank against. ``load_player_stats``
already carries QB passing columns (``passing_yards``, ``passing_tds``,
``interceptions``, ...) consumed by
:class:`~nuclearff.scoring.engine.ScoringEngine`; this constant only widens
the position filter, not the schema.
"""

SNAP_COUNTS_COVERAGE_START = 2012
"""First season nflreadpy's PFR-sourced ``load_snap_counts`` covers."""

NGS_RECEIVING_COVERAGE_START = 2016
"""First season nflreadpy's Next Gen Stats receiving data covers."""

_PLAYER_STATS_REQUIRED_COLUMNS = ("player_id", "position", "season")

_NGS_RECEIVING_REQUIRED_COLUMNS = ("player_gsis_id", "season", "week", "targets")

_SNAP_COUNTS_REQUIRED_COLUMNS = (
    "season",
    "pfr_player_id",
    "player",
    "position",
    "team",
    "offense_snaps",
)

_PLAYERS_REQUIRED_COLUMNS = ("gsis_id", "display_name", "position")

_FF_OPPORTUNITY_REQUIRED_COLUMNS = (
    "season",
    "week",
    "game_id",
    "player_id",
    "rec_touchdown",
    "rec_touchdown_exp",
    "rec_touchdown_diff",
)


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing an expected column.

    Args:
        df: The DataFrame an nflreadpy loader returned.
        required: Column names the caller of ``fn_name`` depends on.
        fn_name: Name of the wrapper function that needed these columns,
            included in the error message.

    Raises:
        ValueError: If any column in ``required`` is absent from ``df``.
    """
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"{fn_name}: nflreadpy returned a DataFrame missing expected "
            f"column(s) {missing!r} (got {df.columns!r}). nflreadpy is an "
            f"experimental, actively-evolving library — its schema may have "
            f"changed; re-verify against the current version before "
            f"widening this check."
        )


def load_weekly_receiving(seasons: list[int]) -> pl.DataFrame:
    """Load weekly player stats, filtered to pass-catching positions.

    Delegates to ``nflreadpy.load_player_stats(seasons=seasons,
    summary_level="week")``. Observed live (nflreadpy 0.1.5, 2024 season):
    150 columns, one row per player per game, including ``targets``,
    ``receptions``, ``receiving_yards``, ``receiving_air_yards``, ``racr``,
    ``target_share``, ``air_yards_share``, ``wopr``, and
    ``fantasy_points_ppr``.

    Args:
        seasons: Seasons to load.

    Returns:
        One row per pass-catcher per game, restricted to
        :data:`PASS_CATCHING_POSITIONS`.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse weekly player stats for seasons %s", seasons)
    df = nflreadpy.load_player_stats(seasons=seasons, summary_level="week")
    _require_columns(df, _PLAYER_STATS_REQUIRED_COLUMNS, "load_weekly_receiving")
    return df.filter(pl.col("position").is_in(PASS_CATCHING_POSITIONS))


def load_seasonal_receiving(seasons: list[int]) -> pl.DataFrame:
    """Load season-summary player stats, filtered to pass-catching positions.

    Delegates to ``nflreadpy.load_player_stats(seasons=seasons,
    summary_level="reg")``. Observed live (nflreadpy 0.1.5, 2024 season): one
    row per player per season, same receiving columns as
    :func:`load_weekly_receiving` plus ``games``, but with ``recent_team`` in
    place of the per-game ``team``/``opponent_team``/``week``/``game_id``
    columns.

    Args:
        seasons: Seasons to load.

    Returns:
        One row per pass-catcher per season, restricted to
        :data:`PASS_CATCHING_POSITIONS`.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse seasonal player stats for seasons %s", seasons)
    df = nflreadpy.load_player_stats(seasons=seasons, summary_level="reg")
    _require_columns(df, _PLAYER_STATS_REQUIRED_COLUMNS, "load_seasonal_receiving")
    return df.filter(pl.col("position").is_in(PASS_CATCHING_POSITIONS))


def load_weekly_skill_stats(seasons: list[int]) -> pl.DataFrame:
    """Load weekly player stats, filtered to skill positions (QB/RB/WR/TE).

    Delegates to ``nflreadpy.load_player_stats(seasons=seasons,
    summary_level="week")``, same as :func:`load_weekly_receiving`, but kept
    to :data:`SKILL_POSITIONS` instead of :data:`PASS_CATCHING_POSITIONS` —
    this is the loader to use when a QB points column is needed (e.g. for
    :class:`~nuclearff.valuation.vorp` at ``position="QB"``).

    Args:
        seasons: Seasons to load.

    Returns:
        One row per skill-position player per game, restricted to
        :data:`SKILL_POSITIONS`.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse weekly player stats for seasons %s", seasons)
    df = nflreadpy.load_player_stats(seasons=seasons, summary_level="week")
    _require_columns(df, _PLAYER_STATS_REQUIRED_COLUMNS, "load_weekly_skill_stats")
    return df.filter(pl.col("position").is_in(SKILL_POSITIONS))


def load_seasonal_skill_stats(seasons: list[int]) -> pl.DataFrame:
    """Load season-summary player stats, filtered to skill positions (QB/RB/WR/TE).

    Delegates to ``nflreadpy.load_player_stats(seasons=seasons,
    summary_level="reg")``, same as :func:`load_seasonal_receiving`, but kept
    to :data:`SKILL_POSITIONS` instead of :data:`PASS_CATCHING_POSITIONS`.

    Args:
        seasons: Seasons to load.

    Returns:
        One row per skill-position player per season, restricted to
        :data:`SKILL_POSITIONS`.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse seasonal player stats for seasons %s", seasons)
    df = nflreadpy.load_player_stats(seasons=seasons, summary_level="reg")
    _require_columns(df, _PLAYER_STATS_REQUIRED_COLUMNS, "load_seasonal_skill_stats")
    return df.filter(pl.col("position").is_in(SKILL_POSITIONS))


def load_ngs_receiving(seasons: list[int]) -> pl.DataFrame:
    """Load Next Gen Stats receiving data.

    Delegates to ``nflreadpy.load_nextgen_stats(seasons=seasons,
    stat_type="receiving")``. NGS coverage starts in
    :data:`NGS_RECEIVING_COVERAGE_START` (2016); seasons before that return
    no rows (nflreadpy's own behavior — not filtered here).

    Observed live (nflreadpy 0.1.5, 2024 season): 23 columns keyed by
    ``player_gsis_id``, ``season``, and ``week`` (``week == 0`` holds the
    season-aggregate row, per NGS convention), including ``avg_cushion``,
    ``avg_separation``, ``avg_intended_air_yards``,
    ``percent_share_of_intended_air_yards``, ``avg_yac``,
    ``avg_expected_yac``, and ``avg_yac_above_expectation``. No filtering is
    applied here beyond what nflreadpy itself returns: the observed
    ``player_position`` values were only ``WR`` and ``TE`` — NGS does not
    publish receiving charting for ``RB`` at all, so a caller expecting RB
    rows from this function will not find any.

    Args:
        seasons: Seasons to load.

    Returns:
        The Next Gen Stats receiving DataFrame, unfiltered.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse NGS receiving stats for seasons %s", seasons)
    df = nflreadpy.load_nextgen_stats(seasons=seasons, stat_type="receiving")
    _require_columns(df, _NGS_RECEIVING_REQUIRED_COLUMNS, "load_ngs_receiving")
    return df


def load_routes(seasons: list[int]) -> pl.DataFrame:
    """Approximate routes-run per player-season, honestly labeled by source.

    See the module docstring for the live investigation that ruled out
    ``load_ftn_charting`` and ``load_participation`` as real per-player
    routes-run sources. What is actually used:

    - For seasons >= :data:`SNAP_COUNTS_COVERAGE_START` (2012),
      ``nflreadpy.load_snap_counts`` gives a real, directly observed,
      player-level ``offense_snaps`` count per game (PFR-sourced). It is
      **not** routes run — it includes running-play snaps and any snap where
      the player stayed in to block — so it systematically overstates true
      routes run, by an amount that varies with each team's run/pass mix.
      It is the least-bad real approximation available here, summed to a
      season total and returned with ``source == "offense_snaps_proxy"``.
    - For seasons before that, no nflreadpy source (real or approximate)
      exists at all. Each such requested season contributes a single
      placeholder row with every player-identifying column ``null`` and
      ``source == "unavailable"``, rather than a fabricated per-player
      number.

    Never treat an ``"offense_snaps_proxy"`` row as equivalent to real
    charted routes-run data (e.g. when computing YPRR) without accounting
    for that overcount; the plan's own caution that "routes-run/YPRR
    coverage from public sources is the weakest data link" held up under
    live investigation.

    Args:
        seasons: Seasons to cover.

    Returns:
        Columns ``season``, ``pfr_player_id``, ``player``, ``position``,
        ``team``, ``routes_run``, ``source``. Rows are restricted to
        :data:`PASS_CATCHING_POSITIONS` wherever player-level data exists.

    Raises:
        ValueError: If nflreadpy's snap-count schema is missing an expected
            column.
    """
    covered = sorted(
        season for season in seasons if season >= SNAP_COUNTS_COVERAGE_START
    )
    uncovered = sorted(
        season for season in seasons if season < SNAP_COUNTS_COVERAGE_START
    )

    frames: list[pl.DataFrame] = []

    if covered:
        logger.info(
            "Fetching nflverse snap counts for seasons %s (routes-run proxy, "
            "not real routes-run data)",
            covered,
        )
        snaps = nflreadpy.load_snap_counts(seasons=covered)
        _require_columns(snaps, _SNAP_COUNTS_REQUIRED_COLUMNS, "load_routes")
        proxy = (
            snaps.filter(pl.col("position").is_in(PASS_CATCHING_POSITIONS))
            .group_by(["season", "pfr_player_id", "player", "position", "team"])
            .agg(pl.col("offense_snaps").sum().alias("routes_run"))
            .with_columns(pl.lit("offense_snaps_proxy").alias("source"))
        )
        frames.append(proxy)

    if uncovered:
        logger.warning(
            "No routes-run data or reasonable fallback exists in nflreadpy "
            "for season(s) %s (snap counts start %d); returning "
            "'unavailable' placeholder rows instead of a fabricated number.",
            uncovered,
            SNAP_COUNTS_COVERAGE_START,
        )
        frames.append(
            pl.DataFrame(
                {
                    "season": uncovered,
                    "pfr_player_id": [None] * len(uncovered),
                    "player": [None] * len(uncovered),
                    "position": [None] * len(uncovered),
                    "team": [None] * len(uncovered),
                    "routes_run": [None] * len(uncovered),
                    "source": ["unavailable"] * len(uncovered),
                }
            )
        )

    if not frames:
        return pl.DataFrame(
            schema={
                "season": pl.Int64,
                "pfr_player_id": pl.String,
                "player": pl.String,
                "position": pl.String,
                "team": pl.String,
                "routes_run": pl.Float64,
                "source": pl.String,
            }
        )

    return pl.concat(frames, how="diagonal_relaxed")


def load_players() -> pl.DataFrame:
    """Load the nflverse player bio/ID table.

    Delegates to ``nflreadpy.load_players()``. Observed live (nflreadpy
    0.1.5): 39 columns, one row per player, including ``gsis_id``,
    ``display_name``, ``position``, ``position_group``, ``birth_date``,
    ``college_name``, ``rookie_season``, ``last_season``, ``latest_team``,
    ``status``, and cross-platform IDs (``espn_id``, ``pfr_id``, ``pff_id``,
    ``otc_id``, ``esb_id``, ``nfl_id``, ``smart_id``).

    Returns:
        One row per known nflverse player.

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse player bio/ID table")
    df = nflreadpy.load_players()
    _require_columns(df, _PLAYERS_REQUIRED_COLUMNS, "load_players")
    return df


def load_ff_opportunity(seasons: list[int]) -> pl.DataFrame:
    """Load ffverse's expected-fantasy-opportunity model, weekly grain.

    Delegates to ``nflreadpy.load_ff_opportunity(seasons=seasons,
    stat_type="weekly")`` — ffverse's own community-maintained expected-
    opportunity model (targets, red-zone looks, and air yards distilled into
    an expected-production estimate per player per game), not something this
    project reimplements from raw play-by-play. Observed live (nflreadpy
    0.1.5, 2024 season): 159 columns, one row per player per game, including
    ``receptions``/``receptions_exp``, ``rec_yards_gained``/``_exp``,
    ``rec_fantasy_points``/``_exp``, and ``rec_touchdown``/``_exp``/``_diff``
    (``rec_touchdown_diff`` is already computed as ``rec_touchdown -
    rec_touchdown_exp`` — confirmed live to match exactly, to the last
    decimal, across every non-null row of the 2024 season), plus
    ``_team``-suffixed team-total versions of several of these.

    ``player_id`` is in the same gsis_id format used by
    :func:`load_weekly_receiving` / :func:`load_seasonal_receiving` /
    :func:`load_players` (confirmed live, e.g. ``"00-0035228"``) — unlike
    :func:`load_routes`'s ``pfr_player_id``, no ID bridge is needed to join
    this onto those. The nominal key is ``(game_id, player_id)``, but this is
    **not fully unique**: confirmed live, a small number of duplicate
    ``(game_id, player_id)`` pairs exist (158 of 6,005 2024 weekly rows), and
    every single one of them has a **null** ``player_id`` (an
    untracked/unresolved-player placeholder row, also null on every other
    identifying column) — every row with a real, non-null ``player_id`` is
    unique per ``(game_id, player_id)``.

    Two dtype quirks confirmed live that a caller joining this onto
    :func:`load_weekly_receiving` / :func:`load_seasonal_receiving` output
    must handle rather than assume away: this loader's ``season`` comes back
    as a **String** (e.g. ``"2024"``), not the ``Int32`` those two loaders
    return, and its ``week`` comes back as **Float64** (e.g. ``1.0``), not
    their ``Int32``. Cast both to match before joining on them — see
    :func:`nuclearff.metrics.touchdowns.expected_tds`, which does exactly
    that.

    No seasonal aggregate is available directly: ``stat_type`` only accepts
    ``"weekly"``, ``"pbp_pass"``, or ``"pbp_rush"`` (confirmed live via
    ``nflreadpy.load_ff_opportunity``'s own signature) — there is no
    ``stat_type="season"`` or equivalent. A season total has to be summed
    from these weekly rows by the caller; see
    :func:`nuclearff.metrics.touchdowns.expected_tds` for where that
    happens.

    Args:
        seasons: Seasons to load.

    Returns:
        One row per player per game (weekly grain), unfiltered by position
        (QBs and other non-pass-catchers are present, with ``rec_touchdown``
        columns mostly zero for them).

    Raises:
        ValueError: If the returned DataFrame is missing an expected column.
    """
    logger.info("Fetching nflverse ff_opportunity weekly data for seasons %s", seasons)
    df = nflreadpy.load_ff_opportunity(seasons=seasons, stat_type="weekly")
    _require_columns(df, _FF_OPPORTUNITY_REQUIRED_COLUMNS, "load_ff_opportunity")
    return df
