"""QB/offense metrics: fantasy-point breakdown, volume vs. efficiency, neutral-
situation pass rate and pace, charted play-design rates, and defense EPA
allowed per dropback.

Built for GitHub Issue 214 (epic #213): the reusable feature layer behind
"what changed in a QB's box score, and how much of it is real." Every
aggregation function here groups by ``(season, ...)`` and deliberately takes
no "week range" argument — pass a full season of play-by-play for a
season-grain table, or pre-filter to whatever weeks matter first (e.g.
``pbp.filter(pl.col("week").is_between(1, 8))``) for a week-range table, same
posture as :func:`nuclearff.matchups.dvp.points_allowed_by_position`: the
caller controls the grain by controlling the input, not by the function
interpreting a week-range parameter.

Data sources, verified live this session (nflreadpy 0.1.5, 2025 season)
--------------------------------------------------------------------------
- Fantasy-point components: ``nflreadpy.load_player_stats`` — confirmed
  ``passing_yards``, ``passing_tds``, ``passing_interceptions``,
  ``passing_2pt_conversions``, ``completions``, ``attempts``,
  ``rushing_yards``, ``rushing_tds``, ``rushing_2pt_conversions``,
  ``receiving_yards``, ``receiving_tds`` (a real, if rare, QB stat — a
  broken-play throwback), ``rushing_fumbles_lost``, ``sack_fumbles_lost``,
  ``receiving_fumbles_lost``, and the composites/thresholds
  :class:`~nuclearff.scoring.engine.ScoringEngine` already maps for every
  other position.
- EPA/CPOE/success/dropbacks/neutral-situation/pace inputs:
  ``nflreadpy.load_pbp`` — confirmed ``qb_dropback``, ``epa``, ``cpoe``,
  ``success``, ``pass_oe``, ``qtr``, ``score_differential``, ``play_type``,
  ``posteam``, ``defteam``, ``passer_player_id``/``passer_player_name``,
  ``drive`` and ``game_seconds_remaining`` (for :func:`pace` — confirmed
  ``play_id`` is monotonically increasing within a game, so sorting by it
  gives real play order).
- Play-action/RPO/screen/motion/blitz/out-of-pocket/drop/catchable-ball/
  interception-worthy rates: ``nflreadpy.load_ftn_charting`` joined onto
  ``load_pbp`` — confirmed all ten fields exist under the exact names the
  epic named (``is_play_action``, ``is_rpo``, ``is_screen_pass``,
  ``is_motion``, ``n_blitzers``, ``n_pass_rushers``, ``is_qb_out_of_pocket``,
  ``is_drop``, ``is_catchable_ball``, ``is_interception_worthy``). **The join
  key types do not match without a cast**: ``pbp.play_id`` is ``Float64``,
  ``ftn.nflverse_play_id`` is ``Int32``. Confirmed live for the full 2025
  season: with the cast, every one of 20,886 real dropbacks league-wide
  found exactly one charted match (100% coverage, not a lossy join), and the
  resulting rates for Baker Mayfield (16.8% play-action, 3.4% charted drop
  rate, 4.28 average pass rushers faced, ...) all land in the realistic
  real-NFL range.

Not in scope here (see epic #213's other sub-issues)
-------------------------------------------------------
Personnel/formation (``load_participation``) is not available for the
current season at all — confirmed live, ``load_participation(seasons=[2026])``
raises ``ValueError: Season must be between 2016 and 2025``. Next Gen Stats
(time to throw, aggressiveness, air yards) are pre-aggregated by NGS itself
and need no derivation logic; they belong to #217's report as a direct load.
PFF's data and its ID crosswalk are #216's job. Coaching staff/play-caller
lookup is #215's job (:mod:`nuclearff.reference.coaching_staff`).
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from nuclearff.config.league import ScoringSettings
from nuclearff.scoring.engine import ScoringEngine

_PASSING_SCORING_KEYS = (
    "pass_yd",
    "pass_td",
    "pass_int",
    "pass_2pt",
    "pass_cmp",
    "pass_att",
    "bonus_pass_yd_300",
    "bonus_pass_yd_400",
    "bonus_pass_cmp_25",
)
"""Sleeper scoring keys bucketed into ``fantasy_point_breakdown``'s
``passing_points`` column."""

_RUSHING_SCORING_KEYS = (
    "rush_yd",
    "rush_td",
    "rush_fd",
    "rush_2pt",
    "rec",
    "rec_yd",
    "rec_td",
    "rec_fd",
    "rec_2pt",
    "bonus_rec_yd_100",
    "bonus_rec_yd_200",
    "bonus_rush_yd_100",
    "bonus_rush_yd_200",
    "bonus_rush_att_20",
    "bonus_rush_rec_yd_100",
    "bonus_rush_rec_yd_200",
    "bonus_rec_wr",
    "bonus_rec_te",
    "bonus_rec_rb",
)
"""Every non-passing offensive-production scoring key, bucketed into
``fantasy_point_breakdown``'s ``rushing_points`` column. Named "rushing" per
the epic's own language, even though it also covers a QB's (rare, real)
receiving stats — not worth a fourth near-always-zero bucket."""

_TURNOVER_SCORING_KEYS = ("fum_lost", "fum")
"""Sleeper scoring keys bucketed into ``fantasy_point_breakdown``'s
``turnover_points`` column."""

_NEUTRAL_QUARTERS = (1, 2, 3)
"""Quarters counted as "neutral situation" -- excludes the 4th quarter and OT
entirely, where trailing/leading teams' play-calling skews hardest toward
pass-heavy comebacks or run-heavy clock-killing. A modeling choice (per
issue #214's own review notes), not an nflverse-defined constant."""

_NEUTRAL_MAX_SCORE_DIFFERENTIAL = 8.0
"""Max |score differential| (in points) counted as "neutral situation" -- one
possession, accounting for a 2-point conversion. A modeling choice, not an
nflverse-defined constant; the standard threshold used by public neutral
pass-rate/PROE work (e.g. rbsdm.com)."""

_MAX_PLAUSIBLE_SNAP_INTERVAL_SECONDS = 40.0
"""Longest gap between two of a team's consecutive same-drive plays counted
as real offensive tempo, for :func:`pace`. 40 seconds is the NFL's own base
play clock; a longer gap reflects a stoppage (injury, replay review, a
penalty discussion) rather than the offense's own chosen pace. A modeling
choice, not an nflverse-defined constant."""

_FTN_RATE_COLUMNS = (
    "is_play_action",
    "is_rpo",
    "is_screen_pass",
    "is_motion",
    "n_blitzers",
    "n_pass_rushers",
    "is_qb_out_of_pocket",
    "is_drop",
    "is_catchable_ball",
    "is_interception_worthy",
)
"""FTN charting fields exposed by :func:`ftn_charting_rates`, in the exact
names confirmed live against ``load_ftn_charting`` (see the module
docstring)."""

_VOLUME_EFFICIENCY_REQUIRED_COLUMNS = (
    "season",
    "passer_player_id",
    "passer_player_name",
    "qb_dropback",
    "epa",
    "success",
    "cpoe",
    "pass_oe",
)

_NEUTRAL_PASS_RATE_REQUIRED_COLUMNS = (
    "season",
    "posteam",
    "qtr",
    "score_differential",
    "play_type",
)

_DEFENSE_EPA_REQUIRED_COLUMNS = ("season", "defteam", "qb_dropback", "epa")

_PACE_REQUIRED_COLUMNS = (
    "season",
    "posteam",
    "qtr",
    "score_differential",
    "play_type",
    "game_id",
    "drive",
    "play_id",
    "game_seconds_remaining",
)

_FTN_PBP_REQUIRED_COLUMNS = (
    "season",
    "game_id",
    "play_id",
    "qb_dropback",
    "passer_player_id",
    "passer_player_name",
)

_FTN_CHARTING_REQUIRED_COLUMNS = (
    "nflverse_game_id",
    "nflverse_play_id",
    *_FTN_RATE_COLUMNS,
)


def _require_columns(df: pl.DataFrame, required: Sequence[str], fn_name: str) -> None:
    """Fail early and clearly if ``df`` is missing a column ``fn_name`` needs.

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


def fantasy_point_breakdown(df: pl.DataFrame, scoring: ScoringSettings) -> pl.DataFrame:
    """Split actual fantasy points into passing / rushing / turnover components.

    Reuses :meth:`~nuclearff.scoring.engine.ScoringEngine.score_frame_by_group`
    rather than approximating a split against nflreadpy's own generic
    ``fantasy_points``/``fantasy_points_ppr`` columns — those reflect a fixed
    scoring format, not this league's real rules (the same "never trust a
    generic points column" posture :mod:`nuclearff.scoring.engine`'s own
    module docstring already takes). Because ``passing_points``,
    ``rushing_points``, and ``turnover_points`` are built from an exhaustive
    partition of every scoring key :class:`ScoringEngine` handles (see
    :data:`_PASSING_SCORING_KEYS`, :data:`_RUSHING_SCORING_KEYS`,
    :data:`_TURNOVER_SCORING_KEYS` — a test pins this against the engine's
    own handled-key set), they sum to *exactly* this same call's
    ``fantasy_points`` column, for any row, not approximately.

    Args:
        df: Stat rows using nflverse column names, e.g.
            ``nflreadpy.load_player_stats`` output at weekly or seasonal
            grain — no grain trap here (unlike
            :mod:`nuclearff.metrics.volume`'s team-share functions): every
            term is a per-row computation with no team-total denominator.
        scoring: The league's real scoring rules.

    Returns:
        ``df`` with ``passing_points``, ``rushing_points``,
        ``turnover_points``, and ``fantasy_points`` (the true total, from
        every handled scoring key — identical to
        ``ScoringEngine(scoring).score_frame(df)``'s own column) added.
    """
    engine = ScoringEngine(scoring)
    return engine.score_frame_by_group(
        df,
        {
            "passing_points": _PASSING_SCORING_KEYS,
            "rushing_points": _RUSHING_SCORING_KEYS,
            "turnover_points": _TURNOVER_SCORING_KEYS,
        },
    )


def volume_efficiency_split(pbp: pl.DataFrame) -> pl.DataFrame:
    """Split a passer's play-by-play into volume (dropbacks) vs. efficiency.

    Args:
        pbp: Play-by-play rows, e.g. ``nflreadpy.load_pbp`` output, covering
            whichever season/week-range the caller wants one row per passer
            for (see the module docstring).

    Returns:
        One row per ``(season, passer_player_id, passer_player_name)``:
        ``dropbacks`` (volume, and the sample size behind every rate column
        here), ``epa_per_dropback``, ``success_rate``, ``cpoe`` (null when
        every dropback in the window was a non-pass-attempt, e.g. all
        scrambles/sacks — nflreadpy's own ``cpoe`` is only defined on a real
        pass attempt), and ``pass_oe`` (pass-rate over expected — an
        nflreadpy-computed, situation-level column reported here at the
        passer's own dropbacks, not recomputed). Sorted by season, most
        dropbacks first.

    Raises:
        ValueError: If ``pbp`` is missing a required column.
    """
    _require_columns(
        pbp, _VOLUME_EFFICIENCY_REQUIRED_COLUMNS, "volume_efficiency_split"
    )

    dropbacks = pbp.filter(pl.col("qb_dropback") == 1)
    return (
        dropbacks.group_by(["season", "passer_player_id", "passer_player_name"])
        .agg(
            pl.len().alias("dropbacks"),
            pl.col("epa").mean().alias("epa_per_dropback"),
            pl.col("success").mean().alias("success_rate"),
            pl.col("cpoe").mean().alias("cpoe"),
            pl.col("pass_oe").mean().alias("pass_oe"),
        )
        .sort(["season", "dropbacks"], descending=[False, True])
    )


def _neutral_situation_plays(pbp: pl.DataFrame) -> pl.DataFrame:
    """Real offensive plays (run or pass) in a neutral game situation.

    Shared by :func:`neutral_pass_rate` and :func:`pace` — both are
    "neutral-situation" metrics in the epic's own framing, and must agree on
    exactly which plays that means. See :data:`_NEUTRAL_QUARTERS` and
    :data:`_NEUTRAL_MAX_SCORE_DIFFERENTIAL` for the exact thresholds and why
    they're a modeling choice, not an nflverse-defined term.

    Args:
        pbp: Play-by-play rows.

    Returns:
        ``pbp`` filtered to real run/pass plays in a neutral situation.
    """
    return pbp.filter(
        pl.col("qtr").is_in(_NEUTRAL_QUARTERS)
        & (pl.col("score_differential").abs() <= _NEUTRAL_MAX_SCORE_DIFFERENTIAL)
        & pl.col("play_type").is_in(["pass", "run"])
    )


def neutral_pass_rate(pbp: pl.DataFrame) -> pl.DataFrame:
    """A team's pass rate in neutral game situations, by season.

    This is deliberately a **team**-level metric (grouped by ``posteam``,
    not passer): neutral pass rate reflects play-calling/scheme, the epic's
    own motivating question #3 ("what changed in the offense"), not which QB
    happened to be on the field for a given snap.

    Args:
        pbp: Play-by-play rows covering whichever season/week-range the
            caller wants one row per team for.

    Returns:
        One row per ``(season, posteam)``: ``neutral_plays`` (the sample
        size — real offensive plays, run or pass, in a neutral situation)
        and ``neutral_pass_rate`` (the fraction of those that were a pass).

    Raises:
        ValueError: If ``pbp`` is missing a required column.
    """
    _require_columns(pbp, _NEUTRAL_PASS_RATE_REQUIRED_COLUMNS, "neutral_pass_rate")

    neutral = _neutral_situation_plays(pbp)
    return (
        neutral.group_by(["season", "posteam"])
        .agg(
            pl.len().alias("neutral_plays"),
            (pl.col("play_type") == "pass").mean().alias("neutral_pass_rate"),
        )
        .sort(["season", "posteam"])
    )


def pace(pbp: pl.DataFrame) -> pl.DataFrame:
    """A team's seconds per offensive play, in neutral game situations, by season.

    Computed as the real game-clock time between two of a team's
    consecutive plays *within the same drive* (``game_seconds_remaining``
    diffed after sorting by ``play_id``, confirmed live to be monotonically
    increasing within a game — see the module docstring), restricted to
    neutral situations (:func:`_neutral_situation_plays` — the same
    definition :func:`neutral_pass_rate` uses) and to plausible snap-to-snap
    gaps (see :data:`_MAX_PLAUSIBLE_SNAP_INTERVAL_SECONDS`): a longer gap is
    a clock stoppage, not the offense choosing to go slow. Lower is faster
    (hurry-up); higher is slower (deliberate, clock-milking).

    Args:
        pbp: Play-by-play rows covering whichever season/week-range the
            caller wants one row per team for.

    Returns:
        One row per ``(season, posteam)``: ``snap_intervals`` (the sample
        size — real, plausible consecutive-play gaps observed) and
        ``pace_seconds_per_play``. A team season with zero plausible
        consecutive-play gaps at all (e.g. every neutral-situation drive was
        a single play) contributes no row, rather than an undefined average.

    Raises:
        ValueError: If ``pbp`` is missing a required column.
    """
    _require_columns(pbp, _PACE_REQUIRED_COLUMNS, "pace")

    neutral = _neutral_situation_plays(pbp)
    gaps = (
        neutral.sort(["season", "posteam", "game_id", "drive", "play_id"])
        .with_columns(
            (
                pl.col("game_seconds_remaining").shift(1)
                - pl.col("game_seconds_remaining")
            )
            .over(["game_id", "posteam", "drive"])
            .alias("_seconds_since_previous_play")
        )
        .filter(
            pl.col("_seconds_since_previous_play").is_not_null()
            & (pl.col("_seconds_since_previous_play") > 0)
            & (
                pl.col("_seconds_since_previous_play")
                <= _MAX_PLAUSIBLE_SNAP_INTERVAL_SECONDS
            )
        )
    )
    return (
        gaps.group_by(["season", "posteam"])
        .agg(
            pl.len().alias("snap_intervals"),
            pl.col("_seconds_since_previous_play")
            .mean()
            .alias("pace_seconds_per_play"),
        )
        .sort(["season", "posteam"])
    )


def ftn_charting_rates(pbp: pl.DataFrame, ftn_charting: pl.DataFrame) -> pl.DataFrame:
    """Per-passer charted play-design rates: play-action, RPO, screen, motion,
    pass-rush pressure, out-of-pocket, drops, catchable balls, and
    interception-worthy throws.

    Joins ``pbp`` (restricted to real dropbacks, the same grain
    :func:`volume_efficiency_split` uses) onto FTN's own charting via
    ``(game_id, play_id)`` — confirmed live this session to need a dtype
    cast (``pbp.play_id`` is ``Float64``, ``ftn_charting.nflverse_play_id``
    is ``Int32``) and, with that cast, to match every real dropback league-
    wide (see the module docstring). An inner join: a dropback FTN hasn't
    charted (real for a season/week FTN charting doesn't cover) simply isn't
    counted here rather than appearing as an all-null row.

    Args:
        pbp: Play-by-play rows covering whichever season/week-range the
            caller wants one row per passer for.
        ftn_charting: ``nflreadpy.load_ftn_charting`` output (or a subset of
            its rows) — needs ``nflverse_game_id``, ``nflverse_play_id``, and
            every column in :data:`_FTN_RATE_COLUMNS`.

    Returns:
        One row per ``(season, passer_player_id, passer_player_name)``:
        ``charted_dropbacks`` (the sample size), ``play_action_rate``,
        ``rpo_rate``, ``screen_rate``, ``motion_rate``, ``avg_blitzers``,
        ``avg_pass_rushers``, ``out_of_pocket_rate``, ``drop_rate``,
        ``catchable_ball_rate``, ``interception_worthy_rate``. Sorted by
        season, most charted dropbacks first.

    Raises:
        ValueError: If either input is missing a required column.
    """
    _require_columns(pbp, _FTN_PBP_REQUIRED_COLUMNS, "ftn_charting_rates")
    _require_columns(ftn_charting, _FTN_CHARTING_REQUIRED_COLUMNS, "ftn_charting_rates")

    dropbacks = pbp.filter(pl.col("qb_dropback") == 1).with_columns(
        pl.col("play_id").cast(pl.Int32)
    )
    joined = dropbacks.join(
        ftn_charting,
        left_on=["game_id", "play_id"],
        right_on=["nflverse_game_id", "nflverse_play_id"],
        how="inner",
    )

    return (
        joined.group_by(["season", "passer_player_id", "passer_player_name"])
        .agg(
            pl.len().alias("charted_dropbacks"),
            pl.col("is_play_action").mean().alias("play_action_rate"),
            pl.col("is_rpo").mean().alias("rpo_rate"),
            pl.col("is_screen_pass").mean().alias("screen_rate"),
            pl.col("is_motion").mean().alias("motion_rate"),
            pl.col("n_blitzers").mean().alias("avg_blitzers"),
            pl.col("n_pass_rushers").mean().alias("avg_pass_rushers"),
            pl.col("is_qb_out_of_pocket").mean().alias("out_of_pocket_rate"),
            pl.col("is_drop").mean().alias("drop_rate"),
            pl.col("is_catchable_ball").mean().alias("catchable_ball_rate"),
            pl.col("is_interception_worthy").mean().alias("interception_worthy_rate"),
        )
        .sort(["season", "charted_dropbacks"], descending=[False, True])
    )


def defense_epa_per_dropback(pbp: pl.DataFrame) -> pl.DataFrame:
    """Real EPA allowed per opposing dropback, by defense and season.

    Opponent context for a QB change report's acceptance criterion ("each
    defense's EPA allowed per dropback, season to date") — a QB's own
    efficiency numbers mean less without knowing whether the defenses they
    faced were strong or weak.

    Args:
        pbp: Play-by-play rows covering whichever season/week-range the
            caller wants one row per defense for.

    Returns:
        One row per ``(season, defteam)``: ``dropbacks_faced`` (the sample
        size) and ``epa_allowed_per_dropback``, sorted worst defense
        (highest EPA allowed) first within each season.

    Raises:
        ValueError: If ``pbp`` is missing a required column.
    """
    _require_columns(pbp, _DEFENSE_EPA_REQUIRED_COLUMNS, "defense_epa_per_dropback")

    dropbacks = pbp.filter(pl.col("qb_dropback") == 1)
    return (
        dropbacks.group_by(["season", "defteam"])
        .agg(
            pl.len().alias("dropbacks_faced"),
            pl.col("epa").mean().alias("epa_allowed_per_dropback"),
        )
        .sort(["season", "epa_allowed_per_dropback"], descending=[False, True])
    )
