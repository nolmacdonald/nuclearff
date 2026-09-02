"""Unit tests for nuclearff.config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nuclearff.config import (
    ModelConfig,
    NuclearffConfig,
    PathsConfig,
    default_config,
    dump_config,
    load_config,
)
from nuclearff.config.loader import load_model_config, to_yaml
from nuclearff.config.models import (
    AgeCurveConfig,
    RecencyWeights,
    SimulationConfig,
    WeightBlocks,
)
from nuclearff.exceptions import ConfigError


def test_defaults_match_the_plan():
    """Default settings encode the weighting and gates from the technical plan."""
    config = default_config()

    assert config.model.weights.volume == 0.50
    assert config.model.weights.efficiency == 0.25
    assert config.model.weights.expected_td == 0.10
    assert config.model.weights.situation == 0.15
    assert config.model.recency.weights == (0.5, 0.3, 0.2)
    assert config.model.thresholds.min_games == 8
    assert config.model.thresholds.min_routes == 200
    assert config.model.baseline == "vols"
    assert config.model.seasons == (2023, 2024, 2025)


def test_weight_blocks_must_sum_to_one():
    """A weighting that is not a convex combination is rejected."""
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        WeightBlocks(volume=0.6, efficiency=0.25, expected_td=0.10, situation=0.15)


def test_recency_weights_must_sum_to_one():
    """Recency weights are a weighted average, so they must sum to one."""
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        RecencyWeights(weights=(0.6, 0.3, 0.2))


def test_recency_weights_reject_negatives():
    """A negative season weight is a mistake, not a modeling choice."""
    with pytest.raises(ValidationError, match="non-negative"):
        RecencyWeights(weights=(1.2, -0.2))


def test_seasons_must_cover_recency_weights():
    """Three recency weights need at least three seasons of input."""
    with pytest.raises(ValidationError, match="require at least"):
        ModelConfig(seasons=(2024, 2025))


def test_seasons_must_be_sorted():
    """Season order is load-bearing for the recency blend."""
    with pytest.raises(ValidationError, match="sorted oldest first"):
        ModelConfig(seasons=(2025, 2024, 2023))


def test_age_curve_plateau_cannot_precede_peak():
    """A plateau ending before the peak would invert the curve."""
    with pytest.raises(ValidationError, match="must be >="):
        AgeCurveConfig(peak_age=28, plateau_end=26)


def test_simulation_floor_below_ceiling():
    """The reported floor percentile must sit below the ceiling percentile."""
    with pytest.raises(ValidationError, match="must be <"):
        SimulationConfig(floor_percentile=0.9, ceiling_percentile=0.1)


def test_unknown_keys_are_rejected(tmp_path):
    """A typo in a config file fails loudly instead of being ignored."""
    path = tmp_path / "config.yaml"
    path.write_text("run:\n  sead: 7\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid configuration"):
        load_config(path)


def test_missing_file_raises_config_error(tmp_path):
    """An explicitly requested config file that does not exist is an error."""
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml")


def test_invalid_yaml_raises_config_error(tmp_path):
    """Unparseable YAML is reported as a configuration problem."""
    path = tmp_path / "config.yaml"
    path.write_text("run: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_non_mapping_yaml_raises_config_error(tmp_path):
    """A YAML list is not a configuration."""
    path = tmp_path / "config.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="mapping at the top level"):
        load_config(path)


def test_empty_file_loads_defaults(tmp_path):
    """An empty config file means 'use every default'."""
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")

    assert load_config(path) == default_config()


def test_round_trip_is_byte_identical(tmp_path):
    """Dump, load, dump produces identical bytes, so config hashes are stable."""
    original = default_config()
    first = tmp_path / "a.yaml"
    dump_config(original, first)

    reloaded = load_config(first, root=original.paths.root)
    second = tmp_path / "b.yaml"
    dump_config(reloaded, second)

    assert first.read_bytes() == second.read_bytes()
    assert reloaded == original


def test_canonical_dict_is_deterministic_and_json_safe():
    """Canonical output sorts keys and stringifies paths for stable hashing."""
    canonical = default_config().canonical_dict()

    assert list(canonical) == sorted(canonical)
    assert isinstance(canonical["paths"]["root"], str)
    assert canonical["model"]["seasons"] == [2023, 2024, 2025]


def test_root_override_relocates_every_directory(tmp_path):
    """Pointing root at a temporary directory keeps runs off the real tree."""
    path = tmp_path / "config.yaml"
    dump_config(default_config(), path)

    config = load_config(path, root=tmp_path)

    for directory in config.paths.all_dirs():
        assert directory.is_relative_to(tmp_path)


def test_ensure_creates_directories(tmp_path):
    """ensure() creates the full managed tree and is safe to repeat."""
    paths = PathsConfig(root=tmp_path)
    paths.ensure()
    paths.ensure()

    for directory in paths.all_dirs():
        assert directory.is_dir()


def test_absolute_data_dir_ignores_root(tmp_path):
    """An absolute data directory is used as given."""
    paths = PathsConfig(root=tmp_path / "elsewhere", data=tmp_path / "abs")

    assert paths.cache_dir == (tmp_path / "abs" / "cache").resolve()


def test_config_is_frozen():
    """Configuration is immutable, so a run cannot mutate its own settings."""
    config = default_config()

    with pytest.raises(ValidationError):
        config.run.seed = 99


def test_model_config_yaml_round_trip(tmp_path):
    """A standalone model config survives a YAML round trip unchanged."""
    model = ModelConfig(
        name="wr_efficiency_heavy",
        weights=WeightBlocks(
            volume=0.40, efficiency=0.35, expected_td=0.10, situation=0.15
        ),
    )
    path = tmp_path / "model.yaml"
    dump_config(model, path)

    assert load_model_config(path) == model


def test_to_yaml_sorts_keys():
    """Serialized YAML is sorted so diffs reflect real changes only."""
    text = to_yaml(NuclearffConfig())
    top_level = [
        line.split(":")[0]
        for line in text.splitlines()
        if line and not line.startswith(" ")
    ]

    assert top_level == sorted(top_level)
