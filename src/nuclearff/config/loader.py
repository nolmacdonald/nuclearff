"""Deterministic loading and serialization of nuclearff configuration.

Loading is deterministic in both directions: the same YAML always produces the
same model, and the same model always produces byte-identical YAML. That
property is what lets a run manifest hash a configuration and have the hash
mean something.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from nuclearff.config.models import ModelConfig, NuclearffConfig
from nuclearff.exceptions import ConfigError

logger = logging.getLogger(__name__)


def default_config() -> NuclearffConfig:
    """Return the configuration used when no file is supplied.

    Returns:
        A configuration with every field at its documented default.
    """
    return NuclearffConfig()


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping from disk.

    Args:
        path: File to read.

    Returns:
        The parsed mapping, or an empty mapping for an empty file.

    Raises:
        ConfigError: If the file is missing, unreadable, not valid YAML, or does
            not contain a mapping at the top level.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read configuration file {path}: {exc}") from exc

    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Configuration file {path} is not valid YAML: {exc}"
        ) from exc

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"Configuration file {path} must contain a mapping at the top level, "
            f"got {type(parsed).__name__}"
        )
    return parsed


def load_config(path: str | Path, *, root: str | Path | None = None) -> NuclearffConfig:
    """Load a configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file.
        root: Optional override for ``paths.root``. Supplying a temporary
            directory here relocates every managed directory, which is how tests
            keep runs off the real data tree.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file cannot be read or fails validation.
    """
    path = Path(path)
    payload = _read_yaml(path)

    if root is not None:
        paths = dict(payload.get("paths") or {})
        paths["root"] = str(root)
        payload = {**payload, "paths": paths}

    try:
        config = NuclearffConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {path}:\n{exc}") from exc

    logger.debug("Loaded configuration from %s", path)
    return config


def load_model_config(path: str | Path) -> ModelConfig:
    """Load a standalone model configuration from ``configs/models/``.

    Args:
        path: Path to a YAML file containing model settings.

    Returns:
        The validated model configuration.

    Raises:
        ConfigError: If the file cannot be read or fails validation.
    """
    path = Path(path)
    payload = _read_yaml(path)

    try:
        return ModelConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"Invalid model configuration in {path}:\n{exc}") from exc


def to_yaml(config: NuclearffConfig | ModelConfig) -> str:
    """Serialize a configuration to deterministic YAML.

    Keys are sorted and flow style is disabled, so the output is stable and
    diffs in version control reflect real changes only.

    Args:
        config: The configuration to serialize.

    Returns:
        The YAML document as a string.
    """
    if isinstance(config, NuclearffConfig):
        payload: Any = config.canonical_dict()
    else:
        payload = NuclearffConfig(model=config).canonical_dict()["model"]

    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False)


def dump_config(config: NuclearffConfig | ModelConfig, path: str | Path) -> Path:
    """Write a configuration to a YAML file, creating parent directories.

    Args:
        config: The configuration to write.
        path: Destination file.

    Returns:
        The path written.

    Raises:
        ConfigError: If the file cannot be written.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_yaml(config), encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not write configuration to {path}: {exc}") from exc

    logger.debug("Wrote configuration to %s", path)
    return path
