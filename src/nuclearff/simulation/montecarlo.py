"""Monte-Carlo projection distributions: a mean and a spread, turned into a range.

Per the technical plan's "A.7 Uncertainty: distributions, not point estimates" —
a single projected PPG number hides the range that actually decides drafts.
Weekly fantasy scoring is right-skewed (a great game can go much further above
the mean than a bad game can go below it, since points are bounded near zero
but not above), so a plain normal distribution understates both the upside and
the downside. This module simulates weekly outcomes from
``scipy.stats.skewnorm`` instead, sums them into full simulated seasons, and
reduces those seasons to the numbers a draft decision actually uses: floor,
median, ceiling, and (optionally) the probability of clearing some outcome
bar such as a top-12 finish.

This module does not decide what "top-12" means for a given league/season —
that is a valuation-layer concern (comparing simulated season totals against
other players' projections). :func:`summarize_distribution` only takes a
numeric threshold and reports what fraction of simulated seasons cleared it.

Parameterizing skewnorm by mean/sd, not loc/scale
--------------------------------------------------
``scipy.stats.skewnorm(a, loc, scale)``'s own ``loc``/``scale`` parameters do
**not** equal the distribution's actual mean and standard deviation once ``a``
(the skew shape parameter) is nonzero — only at ``a=0`` does skewnorm reduce
to a plain normal where ``loc``/``scale`` are literally the mean/sd. For any
other ``a``, skewnorm's true mean and standard deviation are:

* ``E[X] = loc + scale * delta * sqrt(2/pi)``, where ``delta = a / sqrt(1+a**2)``
* ``sd[X] = scale * sqrt(1 - 2*delta**2/pi)``

Both are affine in ``loc``/``scale``: writing ``X = loc + scale * Z`` for the
standard (``loc=0, scale=1``) form ``Z ~ skewnorm(a)``, ``E[X] = loc + scale *
E[Z]`` and ``sd[X] = scale * sd[Z]``. So the correction is: get the standard
form's own mean/sd via ``skewnorm(a).stats(moments="mv")`` (scipy's own
closed-form moment formulas, not reimplemented here), then solve

* ``scale = sd_ppg / sd[Z]``
* ``loc = mean_ppg - scale * E[Z]``

for the ``loc``/``scale`` that make the distribution's *actual* mean and sd
equal ``mean_ppg``/``sd_ppg`` regardless of ``skew``. At ``skew=0``,
``E[Z]=0`` and ``sd[Z]=1`` exactly, so this collapses to ``scale=sd_ppg,
loc=mean_ppg`` — an exact normal distribution, verified empirically in this
module's tests (not just algebraically) since this is exactly the kind of
correction that can look right and silently be off by a constant factor.
See :func:`_skewnorm_loc_scale`.

Reproducibility
----------------
Every function that draws random samples takes an explicit ``seed`` and uses
``numpy.random.default_rng(seed)`` — the modern numpy ``Generator`` API, not
the legacy global ``np.random.seed()`` state, which is not safely composable
across calls. This matters beyond good practice: :mod:`nuclearff.provenance`
records a ``seed`` field in its run manifest precisely so a published ranking
can be exactly reproduced later. A simulation function that is not actually
deterministic given the same seed would silently break that guarantee, so
every function here is byte-identical across two calls given the same seed.

Memoization (issue #151)
-------------------------
:func:`simulate_player_season` isn't on any pipeline/CLI path today, but is
explicitly slated to back interactive dashboard cards (10,000 draws by
default, per player, per render). :func:`_simulate_player_season_cached`
memoizes it with :func:`functools.lru_cache` -- **only** when ``seed`` is a
real integer. ``seed=None`` means "draw fresh entropy," a real, intentional
per-call outcome documented above; caching that call would freeze it to
whatever the first caller happened to draw and silently hand every later
caller the same "random" result forever, exactly the kind of bug this
module's whole determinism posture exists to prevent. The public function
dispatches to the cache only for an explicit seed and always returns a
copy of the cached array, since a caller mutating the array they got back
would otherwise corrupt every future cache hit for the same arguments.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
from scipy.stats import skewnorm

from nuclearff.config.models import SimulationConfig

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = 1024
"""Bounds the memoization cache's memory rather than leaving it unbounded --
the same "no unbounded, unevicted cache" concern already raised elsewhere in
the 2026-09-21 hosting-cost audit (nuclearff_dashboard issue #13)."""


def _skewnorm_loc_scale(
    mean_ppg: float, sd_ppg: float, skew: float
) -> tuple[float, float]:
    """Solve for the skewnorm ``loc``/``scale`` that hit a target mean and sd.

    See the module docstring's "Parameterizing skewnorm by mean/sd, not
    loc/scale" for the derivation. Uses scipy's own closed-form moment
    formulas for the standard (``loc=0, scale=1``) form via
    ``skewnorm(a).stats(moments="mv")`` rather than reimplementing them.

    Args:
        mean_ppg: Target mean of the resulting distribution.
        sd_ppg: Target standard deviation of the resulting distribution.
            Must be positive (the caller validates this).
        skew: skewnorm shape parameter ``a``. Zero is a symmetric (normal)
            distribution; positive skews right, negative skews left.

    Returns:
        The ``(loc, scale)`` pair such that
        ``skewnorm(a=skew, loc=loc, scale=scale)`` has mean ``mean_ppg`` and
        standard deviation ``sd_ppg``.
    """
    standard_mean, standard_var = skewnorm(skew).stats(moments="mv")
    scale = sd_ppg / np.sqrt(float(standard_var))
    loc = mean_ppg - scale * float(standard_mean)
    return float(loc), float(scale)


def _simulate_player_season(
    mean_ppg: float,
    sd_ppg: float,
    skew: float,
    games: int,
    n_simulations: int,
    seed: int | None,
) -> np.ndarray:
    """Uncached simulation body, shared by the cached and direct paths.

    Args:
        mean_ppg: Target mean weekly fantasy points.
        sd_ppg: Target standard deviation of weekly fantasy points. Must be
            positive; validated by the caller.
        skew: skewnorm shape parameter.
        games: Number of independent weekly draws summed into each season.
        n_simulations: Number of simulated seasons to draw.
        seed: Seed for `numpy.random.default_rng`; `None` draws fresh
            entropy.

    Returns:
        A 1-D array of `n_simulations` simulated season-total fantasy-point
        values.
    """
    loc, scale = _skewnorm_loc_scale(mean_ppg, sd_ppg, skew)
    rng = np.random.default_rng(seed)
    weekly_points = skewnorm.rvs(
        a=skew,
        loc=loc,
        scale=scale,
        size=(n_simulations, games),
        random_state=rng,
    )
    return np.asarray(weekly_points).sum(axis=1)


@lru_cache(maxsize=_CACHE_MAXSIZE)
def _simulate_player_season_cached(
    mean_ppg: float,
    sd_ppg: float,
    skew: float,
    games: int,
    n_simulations: int,
    seed: int,
) -> np.ndarray:
    """:func:`_simulate_player_season`, memoized -- only ever called with a
    real integer ``seed``, never ``None``. See the module docstring's
    "Memoization" note for why that split matters."""
    return _simulate_player_season(mean_ppg, sd_ppg, skew, games, n_simulations, seed)


def simulate_player_season(
    mean_ppg: float,
    sd_ppg: float,
    skew: float = 0.0,
    *,
    games: int = 17,
    n_simulations: int = 10_000,
    seed: int | None = None,
) -> np.ndarray:
    """Simulate full seasons of weekly fantasy points for one player.

    Each simulated week draws from ``scipy.stats.skewnorm(a=skew, loc=...,
    scale=...)``, parameterized (see :func:`_skewnorm_loc_scale`) so the
    distribution's actual mean and standard deviation equal ``mean_ppg`` and
    ``sd_ppg`` regardless of ``skew`` — skewnorm's raw ``loc``/``scale``
    parameters do not equal the target mean/sd once ``skew`` is nonzero. A
    season total is the sum of ``games`` independent weekly draws.

    **Memoized when ``seed`` is given** (issue #151): a repeat call with the
    exact same arguments returns a cached result instead of redrawing 10,000
    (by default) random seasons. A call with ``seed=None`` is never cached —
    see the module docstring — and always draws fresh entropy, matching this
    function's behavior before memoization was added.

    Args:
        mean_ppg: Target mean weekly fantasy points.
        sd_ppg: Target standard deviation of weekly fantasy points. Must be
            positive.
        skew: skewnorm shape parameter. ``0.0`` (the default) is an exact
            normal distribution; positive values right-skew weekly outcomes
            (a long boom tail), matching the plan's "A.7 Uncertainty" note
            that WR weeks are right-skewed.
        games: Number of independent weekly draws summed into each simulated
            season.
        n_simulations: Number of simulated seasons to draw.
        seed: Seed for `numpy.random.default_rng`. The same seed produces
            byte-identical output across calls; `None` draws fresh entropy
            each call and is never cached.

    Returns:
        A 1-D array of `n_simulations` simulated season-total fantasy-point
        values. Always a fresh array, never a reference a caller could
        mutate to corrupt a future cache hit.

    Raises:
        ValueError: If `sd_ppg` is not positive. A season with zero variance
            is deterministic, not something to simulate — this is treated as
            a caller mistake rather than a degenerate distribution.
    """
    if sd_ppg <= 0:
        raise ValueError(
            f"sd_ppg must be positive, got {sd_ppg!r} - a season with zero "
            "or negative variance is deterministic, not something to "
            "simulate."
        )

    if seed is None:
        return _simulate_player_season(
            mean_ppg, sd_ppg, skew, games, n_simulations, seed
        )
    return _simulate_player_season_cached(
        mean_ppg, sd_ppg, skew, games, n_simulations, seed
    ).copy()


def summarize_distribution(
    samples: np.ndarray,
    config: SimulationConfig,
    *,
    top_n_threshold: float | None = None,
) -> dict[str, float]:
    """Reduce simulated season totals to floor/median/ceiling/mean.

    Args:
        samples: Simulated season totals, e.g. `simulate_player_season`
            output.
        config: Percentiles for the floor/ceiling read-off. See
            `nuclearff.config.models.SimulationConfig`.
        top_n_threshold: A season-total bar to report the probability of
            clearing (e.g. the season total corresponding to a top-12 finish
            among comparable players, computed elsewhere and passed in here
            — this function does not know what "top-12" means, it only takes
            a numeric bar). Omitted by default.

    Returns:
        A dict with `"floor"` (the `config.floor_percentile` quantile of
        `samples`), `"median"` (the 50th percentile), `"ceiling"` (the
        `config.ceiling_percentile` quantile), and `"mean"` (the sample
        mean). When `top_n_threshold` is given, also includes
        `"p_exceeds_threshold"`: the fraction of `samples` at or above it.
        The key is absent entirely (not `None`) when `top_n_threshold` is
        omitted.
    """
    summary = {
        "floor": float(np.quantile(samples, config.floor_percentile)),
        "median": float(np.quantile(samples, 0.5)),
        "ceiling": float(np.quantile(samples, config.ceiling_percentile)),
        "mean": float(np.mean(samples)),
    }
    if top_n_threshold is not None:
        summary["p_exceeds_threshold"] = float(np.mean(samples >= top_n_threshold))
    return summary


def simulate_from_config(
    mean_ppg: float,
    sd_ppg: float,
    skew: float,
    config: SimulationConfig,
    *,
    seed: int | None = None,
) -> np.ndarray:
    """Simulate a player's season using the knobs already in a `SimulationConfig`.

    Convenience wrapper around `simulate_player_season` for a caller who
    already holds a `SimulationConfig` and would otherwise have to unpack its
    `games`/`n_simulations` fields by hand on every call.

    Args:
        mean_ppg: Target mean weekly fantasy points.
        sd_ppg: Target standard deviation of weekly fantasy points. Must be
            positive.
        skew: skewnorm shape parameter, as in `simulate_player_season`.
        config: Supplies `games` and `n_simulations`.
        seed: Seed for `numpy.random.default_rng`, as in
            `simulate_player_season`.

    Returns:
        A 1-D array of `config.n_simulations` simulated season-total
        fantasy-point values.

    Raises:
        ValueError: If `sd_ppg` is not positive.
    """
    return simulate_player_season(
        mean_ppg,
        sd_ppg,
        skew,
        games=config.games,
        n_simulations=config.n_simulations,
        seed=seed,
    )
