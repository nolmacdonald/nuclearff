"""Blend a few recent seasons of real stats into a forward-looking projection.

Per the plan's "A.6 Projection blending", a defensible projection is not last
year's raw total: it is built from recency-weighted *rate* stats (so games
missed do not distort the signal), an age curve (WR production is flat
through the late 20s and then declines gradually, not a cliff), a
games-played projection (an injury-shortened season still needs a games
estimate), and optional hand-authored context deltas (a QB/OC change, added
or removed target competition) layered on top.

Every knob here is one of the existing, already-validated configuration
models from :mod:`nuclearff.config.models` --
:class:`~nuclearff.config.models.RecencyWeights`,
:class:`~nuclearff.config.models.AgeCurveConfig`, and
:class:`~nuclearff.config.models.SampleThresholds` (bundled together in
:class:`~nuclearff.config.models.ModelConfig`) -- rather than a second,
duplicate set of parameters defined in this module. Every function below
takes its config object as a parameter instead of re-deriving its own
defaults.

Age from ``load_players()`` -- a real, live-confirmed data shape
------------------------------------------------------------------
Age is not a column on :func:`nuclearff.nflverse.stats.load_seasonal_receiving`
output at all. It has to be joined in from
:func:`nuclearff.nflverse.stats.load_players`, which carries a real
``birth_date`` column (a ``"YYYY-MM-DD"`` string, e.g. Justin Jefferson's is
``"1999-06-16"``) keyed by ``gsis_id`` -- the same column format that
:func:`nuclearff.nflverse.stats.load_seasonal_receiving`'s ``player_id``
column uses. :func:`apply_age_curve` bridges through that ID column and
excludes/flags anything it cannot resolve rather than guessing, the same
"bridge through an ID, never fuzzy-match" posture
:func:`nuclearff.metrics.efficiency.join_routes` already established for the
analogous ``pfr_id``/``gsis_id`` bridge.

Minimum-sample guard is partial, by design
--------------------------------------------
The plan's "A.8 Regression and normalization" calls for shrinking a
small-sample rate toward a *positional prior* rather than trusting it at full
weight. No positional-prior data source exists yet, so that shrinkage is out
of scope here. What :func:`recency_weighted_rate` does implement is a partial
version of the same guard: a player-season below a games threshold
(:data:`~nuclearff.config.models.SampleThresholds.min_games`) is excluded
from the blend entirely, rather than either trusting a 2-game small-sample
season at full weight or shrinking it toward a prior that does not exist yet.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import polars as pl

from nuclearff.config.models import (
    AgeCurveConfig,
    ModelConfig,
    RecencyWeights,
    SampleThresholds,
)

logger = logging.getLogger(__name__)

_AGE_REFERENCE_MONTH = 9
_AGE_REFERENCE_DAY = 1
"""Age is computed as of September 1st of the projected season.

The real NFL season kickoff date moves around a bit year to year, but
"start of September" is a simple, defensible, and easily-restated convention
for "age at the start of the season" -- exact kickoff-day precision does not
change which side of an integer age a player falls on for all but a
vanishingly small number of players born in early September.
"""


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


def _resolve_min_games(
    min_games: int | None, sample_threshold: SampleThresholds | None
) -> int | None:
    """Resolve the effective minimum-games gate from either optional input.

    Args:
        min_games: An explicit games threshold, if the caller passed one.
        sample_threshold: A :class:`SampleThresholds` to pull ``min_games``
            from, if the caller passed one.

    Returns:
        ``min_games`` if given (it takes precedence as the more specific,
        explicit override); otherwise ``sample_threshold.min_games`` if a
        threshold config was given; otherwise ``None`` (no gate at all).
    """
    if min_games is not None:
        return min_games
    if sample_threshold is not None:
        return sample_threshold.min_games
    return None


def recency_weighted_rate(
    seasons: pl.DataFrame,
    value_column: str,
    as_of_season: int,
    weights: RecencyWeights,
    *,
    min_games: int | None = None,
    sample_threshold: SampleThresholds | None = None,
) -> pl.DataFrame:
    """Recency-weight ``value_column`` across the seasons before ``as_of_season``.

    Multi-season input (one row per player per season -- e.g. several
    :func:`nuclearff.nflverse.stats.load_seasonal_receiving` calls
    concatenated) is reduced to one weighted average per player, using
    ``weights.weights`` newest-season-first: ``weights.weights[0]`` applies
    to ``as_of_season - 1``, ``weights.weights[1]`` to ``as_of_season - 2``,
    and so on -- exactly the convention documented on
    :class:`~nuclearff.config.models.RecencyWeights` itself. Only rows whose
    ``season`` falls in that lookback window are considered; anything outside
    it (including ``as_of_season`` itself, and anything older than the
    weights cover) is ignored.

    Renormalization (deliberate design choice): a player with fewer available
    seasons than ``len(weights.weights)`` -- a young player, or one missing a
    year to injury -- has their weights renormalized over just the seasons
    they actually have, rather than treating a missing prior season as a
    zero. Concretely, this is a weighted average
    ``sum(weight_i * value_i) / sum(weight_i)`` taken only over the seasons
    actually present for that player: a season the player does not have
    simply contributes neither a numerator nor a denominator term, so the
    remaining seasons' weights implicitly absorb its share. Treating a
    missing season as a zero-value row instead (i.e. dividing by a fixed 1.0
    regardless of coverage) would understate a young player's rate for no
    real reason -- a rookie with one qualifying season should have that one
    season weighted at full strength, not diluted by two seasons of
    assumed-zero production that never happened.

    A player with **zero** seasons in the lookback window is dropped from
    the output entirely (there is nothing to blend), not given a null or
    zero row.

    Minimum-sample guard (partial -- see the module docstring): when
    ``min_games`` or ``sample_threshold`` is given, a player-season whose
    ``games`` value falls below the threshold is excluded from the blend
    entirely, as if that season did not exist for that player (which then
    flows into the same renormalization above). ``min_games`` takes
    precedence if both are given. Neither given (the default) applies no
    sample-size gate at all -- every season in the lookback window is used at
    its full weight, however few games it covers.

    Args:
        seasons: Multi-season data with at least ``player_id``, ``season``,
            and ``value_column`` columns (plus ``games`` if a sample
            threshold is requested). Typically several
            :func:`nuclearff.nflverse.stats.load_seasonal_receiving` calls
            concatenated across seasons.
        value_column: Name of the column to blend, e.g. ``"targets"``.
        as_of_season: The season being projected. The lookback window is the
            ``len(weights.weights)`` seasons immediately before this one.
        weights: Recency weights, newest-season-first.
        min_games: If given, exclude a player-season with ``games`` below
            this value from the blend. Takes precedence over
            ``sample_threshold`` if both are given.
        sample_threshold: If given (and ``min_games`` is not), exclude a
            player-season with ``games`` below
            ``sample_threshold.min_games`` from the blend.

    Returns:
        One row per ``player_id`` with a ``<value_column>_blended`` column.
        The raw per-season ``value_column`` is not overwritten in the
        input -- this returns a new, separate frame, so a caller that wants
        the raw per-season values alongside the blend can keep its own copy
        of ``seasons``.

    Raises:
        ValueError: If ``seasons`` is missing ``player_id``, ``season``,
            ``value_column``, or (when a sample-size gate is requested)
            ``games``.
    """
    _require_columns(
        seasons, ("player_id", "season", value_column), "recency_weighted_rate"
    )

    effective_min_games = _resolve_min_games(min_games, sample_threshold)
    if effective_min_games is not None:
        _require_columns(seasons, ("games",), "recency_weighted_rate")

    season_weight = {
        as_of_season - 1 - offset: weight
        for offset, weight in enumerate(weights.weights)
    }

    window = seasons.filter(pl.col("season").is_in(list(season_weight)))

    if effective_min_games is not None:
        n_before = window.height
        window = window.filter(pl.col("games") >= effective_min_games)
        n_excluded = n_before - window.height
        if n_excluded:
            logger.warning(
                "recency_weighted_rate(%r): excluded %d player-season row(s) "
                "below the %d-game minimum-sample threshold from the blend",
                value_column,
                n_excluded,
                effective_min_games,
            )

    window = window.filter(pl.col(value_column).is_not_null())

    weighted = window.with_columns(
        pl.col("season")
        .replace_strict(season_weight, return_dtype=pl.Float64)
        .alias("_weight")
    )

    return (
        weighted.group_by("player_id")
        .agg(
            (pl.col("_weight") * pl.col(value_column)).sum().alias("_weighted_sum"),
            pl.col("_weight").sum().alias("_weight_total"),
        )
        .filter(pl.col("_weight_total") > 0)
        .select(
            "player_id",
            (pl.col("_weighted_sum") / pl.col("_weight_total")).alias(
                f"{value_column}_blended"
            ),
        )
    )


def apply_age_curve(
    df: pl.DataFrame,
    players: pl.DataFrame,
    as_of_season: int,
    config: AgeCurveConfig,
    projection_column: str,
) -> pl.DataFrame:
    """Multiply ``projection_column`` by an age-based factor.

    The factor is 1.0 for a player at or below ``config.plateau_end``, else
    ``max(config.min_factor, 1.0 - config.decline_per_year * (age -
    config.plateau_end))`` -- flat through the plateau, then a gradual,
    floored linear decline, exactly the shape documented on
    :class:`~nuclearff.config.models.AgeCurveConfig` itself.

    Age is computed as of September 1st of ``as_of_season`` (see
    :data:`_AGE_REFERENCE_MONTH`/:data:`_AGE_REFERENCE_DAY` for why that
    convention was chosen) from ``players``'s real ``birth_date`` column
    (``"YYYY-MM-DD"`` string, per
    :func:`nuclearff.nflverse.stats.load_players`'s live-confirmed shape --
    see the module docstring), joined onto ``df`` by ``player_id`` (``df``)
    <-> ``gsis_id`` (``players``).

    A player missing from ``players`` entirely, or present but with a null
    (or unparseable) ``birth_date``, gets an age factor of 1.0 -- no
    adjustment -- rather than crashing or being silently dropped from the
    output. How many players this affected is logged as a warning so a
    caller notices a real players-table gap rather than trusting a silently
    neutral factor.

    Args:
        df: A frame with ``player_id`` and ``projection_column`` columns,
            one row per player -- typically :func:`recency_weighted_rate`
            output.
        players: The nflverse player bio table
            (:func:`nuclearff.nflverse.stats.load_players` output), with at
            least ``gsis_id`` and ``birth_date`` columns.
        as_of_season: The season being projected.
        config: Age-curve parameters.
        projection_column: Name of the column to multiply by the age factor.

    Returns:
        ``df`` with ``projection_column`` multiplied in place by the age
        factor, plus a new ``age_factor`` column so the adjustment itself is
        inspectable, not baked in silently.

    Raises:
        ValueError: If ``df`` is missing ``player_id`` or
            ``projection_column``, or ``players`` is missing ``gsis_id`` or
            ``birth_date``.
    """
    _require_columns(df, ("player_id", projection_column), "apply_age_curve")
    _require_columns(players, ("gsis_id", "birth_date"), "apply_age_curve")

    birth_date = pl.col("birth_date")
    if players.schema["birth_date"] != pl.Date:
        birth_date = birth_date.str.strptime(pl.Date, "%Y-%m-%d", strict=False)

    not_yet_had_birthday_by_reference = (
        birth_date.dt.month() > _AGE_REFERENCE_MONTH
    ) | (
        (birth_date.dt.month() == _AGE_REFERENCE_MONTH)
        & (birth_date.dt.day() > _AGE_REFERENCE_DAY)
    )

    # A gsis_id should be unique in load_players() output, but this bridge
    # dedupes defensively (keeping the first row) rather than letting a
    # future duplicate silently fan out the join -- the same "never let an
    # ID ambiguity multiply rows unnoticed" posture as
    # nuclearff.metrics.efficiency.join_routes.
    ages = (
        players.select(
            pl.col("gsis_id").alias("player_id"),
            (
                pl.lit(as_of_season)
                - birth_date.dt.year()
                - not_yet_had_birthday_by_reference.cast(pl.Int32)
            ).alias("_age"),
        )
        .filter(pl.col("player_id").is_not_null())
        .unique(subset="player_id", keep="first")
    )

    joined = df.join(ages, on="player_id", how="left")

    n_missing = joined["_age"].null_count()
    if n_missing:
        logger.warning(
            "apply_age_curve: %d of %d player row(s) had no age available "
            "(missing from `players`, or a null/unparseable birth_date) - "
            "these get an age factor of 1.0 (no adjustment) rather than "
            "crashing or being dropped.",
            n_missing,
            joined.height,
        )

    age_factor = (
        pl.when(pl.col("_age").is_null())
        .then(pl.lit(1.0))
        .when(pl.col("_age") <= config.plateau_end)
        .then(pl.lit(1.0))
        .otherwise(
            pl.max_horizontal(
                pl.lit(config.min_factor),
                pl.lit(1.0)
                - config.decline_per_year * (pl.col("_age") - config.plateau_end),
            )
        )
        .alias("age_factor")
    )

    return (
        joined.with_columns(age_factor)
        .with_columns(
            (pl.col(projection_column) * pl.col("age_factor")).alias(projection_column)
        )
        .drop("_age")
    )


def apply_context_deltas(
    df: pl.DataFrame,
    deltas: dict[str, float],
    projection_column: str,
    *,
    mode: str = "multiplicative",
) -> pl.DataFrame:
    """Apply hand-authored per-player context deltas to ``projection_column``.

    Per the plan's "A.6 Projection blending", context deltas are where a
    human-authored, versioned adjustment for a QB change, an
    offensive-coordinator change, or added/removed target competition gets
    layered onto a blended projection. This function only *applies* whatever
    ``{player_id: delta}`` dict it is given -- loading such a dict from a
    versioned YAML file is a later issue's concern, out of scope here.

    ``mode="multiplicative"`` multiplies ``projection_column`` by each
    player's delta (e.g. ``delta=1.15`` for a +15% bump); ``mode="additive"``
    adds it instead. A player in ``df`` but absent from ``deltas`` gets no
    adjustment: an identity delta (``1.0`` multiplicative, ``0.0``
    additive) rather than an error -- deltas are opt-in per player, and most
    players have none.

    A ``player_id`` present in ``deltas`` but **not** found in ``df`` is
    reported via a logged warning naming every such id, rather than silently
    dropped -- a typo'd ``player_id`` in a hand-written deltas file is
    exactly the mistake this is meant to surface.

    Args:
        df: A frame with ``player_id`` and ``projection_column`` columns.
        deltas: ``{player_id (gsis_id): delta}``.
        projection_column: Name of the column to adjust.
        mode: ``"multiplicative"`` or ``"additive"``.

    Returns:
        ``df`` with ``projection_column`` adjusted in place per ``deltas``.

    Raises:
        ValueError: If ``mode`` is not ``"multiplicative"`` or
            ``"additive"``, or ``df`` is missing ``player_id`` or
            ``projection_column``.
    """
    if mode not in ("multiplicative", "additive"):
        raise ValueError(
            f"apply_context_deltas: unknown mode {mode!r}; expected "
            f"'multiplicative' or 'additive'"
        )
    _require_columns(df, ("player_id", projection_column), "apply_context_deltas")

    if not deltas:
        return df

    unknown_ids = sorted(set(deltas) - set(df["player_id"].to_list()))
    if unknown_ids:
        logger.warning(
            "apply_context_deltas: %d player_id(s) in `deltas` not found in "
            "`df` - check for a typo in the hand-written deltas source: %s",
            len(unknown_ids),
            unknown_ids,
        )

    identity = 1.0 if mode == "multiplicative" else 0.0
    delta_frame = pl.DataFrame(
        {
            "player_id": list(deltas.keys()),
            "_delta": [float(value) for value in deltas.values()],
        }
    )

    joined = df.join(delta_frame, on="player_id", how="left").with_columns(
        pl.col("_delta").fill_null(identity)
    )

    if mode == "multiplicative":
        adjusted = pl.col(projection_column) * pl.col("_delta")
    else:
        adjusted = pl.col(projection_column) + pl.col("_delta")

    return joined.with_columns(adjusted.alias(projection_column)).drop("_delta")


def project_games_played(
    seasons: pl.DataFrame,
    as_of_season: int,
    weights: RecencyWeights,
    max_games: int = 17,
) -> pl.DataFrame:
    """Recency-weighted average games played, capped at ``max_games``.

    Delegates entirely to :func:`recency_weighted_rate` with
    ``value_column="games"`` rather than reimplementing the same weighting
    and renormalization logic a second time.

    Args:
        seasons: Multi-season data with ``player_id``, ``season``, and
            ``games`` columns.
        as_of_season: The season being projected.
        weights: Recency weights, newest-season-first.
        max_games: Upper bound on the projected games value (an NFL regular
            season is 17 games).

    Returns:
        One row per ``player_id`` with a ``projected_games`` column.
    """
    blended = recency_weighted_rate(seasons, "games", as_of_season, weights)
    return blended.rename({"games_blended": "projected_games"}).with_columns(
        pl.col("projected_games").clip(upper_bound=max_games)
    )


def blend_projection(
    seasons: pl.DataFrame,
    players: pl.DataFrame,
    as_of_season: int,
    rate_columns: list[str],
    model_config: ModelConfig,
    context_deltas: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Orchestrate recency weighting, the age curve, and context deltas.

    For each column in ``rate_columns``: recency-weight it with
    :func:`recency_weighted_rate` (using ``model_config.recency`` for the
    weights, and ``model_config.thresholds`` as the minimum-sample gate --
    see the module docstring on why that guard is partial), then age-adjust
    the blended value with :func:`apply_age_curve` (using
    ``model_config.age_curve``), then apply ``context_deltas`` with
    :func:`apply_context_deltas` if given (multiplicatively -- a context
    delta such as a QB change is a role-level adjustment expected to shift
    every one of a player's rate stats by roughly the same factor, not just
    one of them). :func:`project_games_played` is joined in alongside the
    rate columns as ``projected_games``.

    This does **not** run the result through
    :class:`nuclearff.scoring.engine.ScoringEngine` or produce a single
    final point total -- deciding which raw stat columns to blend, and
    turning a blended stat line into one scored projection, is a downstream
    modeling decision for a later pipeline stage, not this function's job.
    The output here is meant to stay inspectable, reusable building blocks:
    one blended (and adjusted) value per requested rate column, not a single
    composite number.

    Args:
        seasons: Multi-season data with at least ``player_id``, ``season``,
            ``games``, and every column named in ``rate_columns``.
        players: The nflverse player bio table
            (:func:`nuclearff.nflverse.stats.load_players` output), for the
            age curve's ``birth_date`` lookup.
        as_of_season: The season being projected.
        rate_columns: Names of the rate columns to blend, e.g.
            ``["targets", "receiving_yards"]``.
        model_config: Bundles the recency weights, age-curve parameters, and
            minimum-sample thresholds this function composes.
        context_deltas: Optional ``{player_id: delta}`` multiplicative
            adjustment, applied to every blended rate column. ``None``
            (the default) applies no adjustment.

    Returns:
        One row per ``player_id`` with ``<column>_blended`` (recency-
        weighted, age-adjusted, and context-adjusted if requested) for every
        column in ``rate_columns``, a single ``age_factor`` column, and
        ``projected_games``. A player missing from one component (e.g. no
        qualifying season for one particular rate column, or no games data
        at all) still appears if present in any component, with null in the
        columns it has no data for -- components are joined on the full set
        of players seen by any of them, not intersected down to players
        present everywhere.

    Raises:
        ValueError: If ``rate_columns`` is empty, or any component call
            raises (see :func:`recency_weighted_rate`,
            :func:`apply_age_curve`, :func:`apply_context_deltas`).
    """
    if not rate_columns:
        raise ValueError("blend_projection: rate_columns must not be empty")

    combined: pl.DataFrame | None = None
    for column in rate_columns:
        blended = recency_weighted_rate(
            seasons,
            column,
            as_of_season,
            model_config.recency,
            sample_threshold=model_config.thresholds,
        )
        combined = (
            blended
            if combined is None
            else combined.join(blended, on="player_id", how="full", coalesce=True)
        )

    assert combined is not None  # rate_columns is non-empty, so the loop ran

    for column in rate_columns:
        combined = apply_age_curve(
            combined, players, as_of_season, model_config.age_curve, f"{column}_blended"
        )

    if context_deltas:
        for column in rate_columns:
            combined = apply_context_deltas(
                combined, context_deltas, f"{column}_blended"
            )

    games = project_games_played(seasons, as_of_season, model_config.recency)
    combined = combined.join(games, on="player_id", how="full", coalesce=True)

    return combined
