"""Typed configuration models for paths, runs, and the projection model.

These are the settings nuclearff owns. League settings are a separate concern:
they are fetched from Sleeper and modelled in ``nuclearff.config`` alongside
these once the league configuration lands.

All models forbid unknown keys so a typo in a YAML file fails immediately
instead of being silently ignored.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BaselineName = Literal["vols", "vorp"]
"""Supported value-based-drafting replacement baselines."""


class _Base(BaseModel):
    """Shared pydantic settings: immutable, strict, and typo-intolerant."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PathsConfig(_Base):
    """Filesystem layout for inputs, caches, and generated artifacts.

    Every directory is expressed relative to ``root``, so pointing ``root`` at a
    temporary directory relocates the whole tree. Tests rely on this.

    Args:
        root: Base directory that all other paths resolve against.
        data: Directory for generated and third-party data, relative to ``root``.
        configs: Directory for versioned configuration, relative to ``root``.

    """

    root: Path = Path(".")
    data: Path = Path("data")
    configs: Path = Path("configs")

    def _under_data(self, name: str) -> Path:
        """Resolve ``name`` beneath the data directory.

        Args:
            name: Subdirectory name.

        Returns:
            The absolute path to that subdirectory.
        """
        base = self.data if self.data.is_absolute() else self.root / self.data
        return (base / name).resolve()

    @property
    def raw_dir(self) -> Path:
        """Immutable downloaded snapshots. Never modified in place."""
        return self._under_data("raw")

    @property
    def processed_dir(self) -> Path:
        """Normalized derivatives of raw inputs."""
        return self._under_data("processed")

    @property
    def cache_dir(self) -> Path:
        """Sleeper and nflreadpy caches, plus intermediate Parquet frames."""
        return self._under_data("cache")

    @property
    def manifests_dir(self) -> Path:
        """Per-run provenance manifests."""
        return self._under_data("manifests")

    @property
    def artifacts_dir(self) -> Path:
        """Published rankings, reports, and figures."""
        return self._under_data("artifacts")

    @property
    def leagues_dir(self) -> Path:
        """Versioned league configuration written by the Sleeper fetch command."""
        base = self.configs if self.configs.is_absolute() else self.root / self.configs
        return (base / "leagues").resolve()

    def all_dirs(self) -> tuple[Path, ...]:
        """Return every managed directory in creation order.

        Returns:
            The data subdirectories followed by the league config directory.
        """
        return (
            self.raw_dir,
            self.processed_dir,
            self.cache_dir,
            self.manifests_dir,
            self.artifacts_dir,
            self.leagues_dir,
        )

    def ensure(self) -> None:
        """Create every managed directory, including parents.

        Existing directories are left untouched.
        """
        for directory in self.all_dirs():
            directory.mkdir(parents=True, exist_ok=True)


class RecencyWeights(_Base):
    """Weights applied to the most recent seasons, newest first.

    Args:
        weights: Per-season weights ordered from most to least recent.

    Raises:
        ValueError: If the weights do not sum to 1.0 or any weight is negative.
    """

    weights: tuple[float, ...] = (0.5, 0.3, 0.2)

    @model_validator(mode="after")
    def _check_weights(self) -> RecencyWeights:
        """Validate that the weights are non-negative and sum to one."""
        if not self.weights:
            raise ValueError("recency weights must not be empty")
        if any(w < 0 for w in self.weights):
            raise ValueError(
                f"recency weights must be non-negative, got {self.weights}"
            )
        if not math.isclose(sum(self.weights), 1.0, abs_tol=1e-9):
            raise ValueError(
                f"recency weights must sum to 1.0, got {sum(self.weights)!r}"
            )
        return self

    @property
    def seasons(self) -> int:
        """Number of prior seasons the blend consumes."""
        return len(self.weights)


class WeightBlocks(_Base):
    """Composite-score block weights.

    Defaults follow the plan's stability-justified split: opportunity metrics
    dominate, touchdowns are deliberately the smallest block because their
    year-over-year correlation is weakest.

    Args:
        volume: Weight on target share, air-yards share, and WOPR.
        efficiency: Weight on YPRR, TPRR, and RACR.
        expected_td: Weight on regressed expected touchdowns.
        situation: Weight on age curve, context deltas, and games played.

    Raises:
        ValueError: If the four blocks do not sum to 1.0.
    """

    volume: float = 0.50
    efficiency: float = 0.25
    expected_td: float = 0.10
    situation: float = 0.15

    @model_validator(mode="after")
    def _check_sum(self) -> WeightBlocks:
        """Validate that the blocks form a proper convex combination."""
        total = self.volume + self.efficiency + self.expected_td + self.situation
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"weight blocks must sum to 1.0, got {total!r}")
        if min(self.volume, self.efficiency, self.expected_td, self.situation) < 0:
            raise ValueError("weight blocks must be non-negative")
        return self


class SampleThresholds(_Base):
    """Minimum sample sizes before a rate statistic is trusted at full weight.

    Below these thresholds a rate is shrunk toward the positional prior rather
    than used directly. This is the primary guard against small-sample mirages.

    Args:
        min_games: Minimum games played in a season.
        min_routes: Minimum routes run in a season.
        min_targets: Minimum targets in a season, used when routes are unavailable.

    """

    min_games: int = Field(default=8, ge=1, le=17)
    min_routes: int = Field(default=200, ge=0)
    min_targets: int = Field(default=50, ge=0)


class AgeCurveConfig(_Base):
    """Multiplicative age adjustment applied to projected rates.

    The curve is flat through ``plateau_end`` and then declines linearly,
    floored at ``min_factor``. Wide receiver decline is gradual, not a cliff.

    Args:
        peak_age: Age at which production peaks.
        plateau_end: Last age before decline begins.
        decline_per_year: Multiplicative loss per year past ``plateau_end``.
        min_factor: Lower bound on the age factor.

    """

    peak_age: int = Field(default=26, ge=18, le=40)
    plateau_end: int = Field(default=28, ge=18, le=40)
    decline_per_year: float = Field(default=0.025, ge=0.0, le=1.0)
    min_factor: float = Field(default=0.85, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_ages(self) -> AgeCurveConfig:
        """Validate that the plateau does not end before the peak."""
        if self.plateau_end < self.peak_age:
            raise ValueError(
                f"plateau_end ({self.plateau_end}) must be >= "
                f"peak_age ({self.peak_age})"
            )
        return self


class SimulationConfig(_Base):
    """Monte-Carlo settings for projection distributions.

    Args:
        n_simulations: Number of simulated seasons.
        games: Games in a simulated season.
        floor_percentile: Percentile reported as the floor.
        ceiling_percentile: Percentile reported as the ceiling.

    """

    n_simulations: int = Field(default=10_000, ge=100)
    games: int = Field(default=17, ge=1, le=25)
    floor_percentile: float = Field(default=0.10, gt=0.0, lt=1.0)
    ceiling_percentile: float = Field(default=0.90, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def _check_percentiles(self) -> SimulationConfig:
        """Validate that the floor sits below the ceiling."""
        if self.floor_percentile >= self.ceiling_percentile:
            raise ValueError(
                f"floor_percentile ({self.floor_percentile}) must be < "
                f"ceiling_percentile ({self.ceiling_percentile})"
            )
        return self


class ModelConfig(_Base):
    """Projection-model settings.

    These are a starting hypothesis, not a validated model. The plan requires
    walk-forward backtests before the weights are treated as final.

    Args:
        name: Identifier recorded in run manifests and artifacts.
        version: Model version recorded alongside outputs.
        position: Position this configuration targets.
        seasons: Historical seasons used as model input, oldest first.
        recency: Season recency weights, newest first.
        weights: Composite-score block weights.
        thresholds: Minimum-sample gates for rate statistics.
        age_curve: Age adjustment parameters.
        simulation: Monte-Carlo settings.
        baseline: Replacement baseline used for value-based drafting.
        flex_wr_rate: Assumed share of FLEX slots filled by wide receivers.

    Raises:
        ValueError: If ``seasons`` is empty or shorter than the recency weights.
    """

    name: str = "wr_default"
    version: str = "0.1.0"
    position: str = "WR"
    seasons: tuple[int, ...] = (2023, 2024, 2025)
    recency: RecencyWeights = RecencyWeights()
    weights: WeightBlocks = WeightBlocks()
    thresholds: SampleThresholds = SampleThresholds()
    age_curve: AgeCurveConfig = AgeCurveConfig()
    simulation: SimulationConfig = SimulationConfig()
    baseline: BaselineName = "vols"
    flex_wr_rate: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_seasons(self) -> ModelConfig:
        """Validate that enough seasons are supplied for the recency weights."""
        if not self.seasons:
            raise ValueError("seasons must not be empty")
        if len(self.seasons) < self.recency.seasons:
            raise ValueError(
                f"{self.recency.seasons} recency weights require at least that "
                f"many seasons, got {len(self.seasons)}"
            )
        if sorted(self.seasons) != list(self.seasons):
            raise ValueError(f"seasons must be sorted oldest first, got {self.seasons}")
        return self


class RunConfig(_Base):
    """Settings that vary per pipeline execution.

    Args:
        season: Season being projected.
        seed: Seed for every stochastic step; recorded in the run manifest.
        log_level: Logging level name for console output.

    """

    season: int = Field(default=2026, ge=1999)
    seed: int = Field(default=2026, ge=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class NuclearffConfig(_Base):
    """Top-level configuration composing paths, run settings, and the model.

    Args:
        paths: Filesystem layout.
        run: Per-execution settings.
        model: Projection-model settings.

    """

    paths: PathsConfig = PathsConfig()
    run: RunConfig = RunConfig()
    model: ModelConfig = ModelConfig()

    def canonical_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-safe dictionary of this configuration.

        Paths are rendered as POSIX strings so the representation is stable
        across operating systems, which makes it safe to hash for provenance.

        Returns:
            A dictionary with deterministic key ordering and primitive values.
        """
        return _jsonify_mapping(self.model_dump(mode="python"))


def _jsonify(value: object) -> object:
    """Recursively convert a dumped model into JSON-safe, deterministic values.

    Args:
        value: A value produced by ``model_dump``.

    Returns:
        The value with paths stringified, tuples listed, and keys sorted.
    """
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return _jsonify_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    return value


def _jsonify_mapping(mapping: Mapping[Any, Any]) -> dict[str, Any]:
    """Convert a dumped mapping into JSON-safe values with sorted keys.

    Args:
        mapping: A mapping produced by ``model_dump``.

    Returns:
        The mapping with deterministic key ordering and primitive values.
    """
    return {str(key): _jsonify(mapping[key]) for key in sorted(mapping, key=str)}
