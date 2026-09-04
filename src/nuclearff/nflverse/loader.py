"""Thin wrappers around ``nflreadpy`` for the nflverse data this project uses.

Only the fantasy-player-ID crosswalk is wired up so far. The plan's other
loaders (weekly/seasonal stats, NGS, FTN charting) arrive with the metrics
work later; this module exists so that expansion has an obvious home.
"""

from __future__ import annotations

import logging
from pathlib import Path

import nflreadpy
import polars as pl
from nflreadpy import config as nflreadpy_config

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DURATION = 86_400
"""Seconds before a cached nflreadpy download is considered stale (24h)."""


def configure_cache(
    cache_dir: str | Path, *, cache_duration: int = DEFAULT_CACHE_DURATION
) -> None:
    """Point nflreadpy's own download cache at this project's cache directory.

    This is nflreadpy's cache for its own downloads (``ff_playerids``, and
    later weekly/seasonal stats) — a separate concern from the Sleeper
    player-map cache (:class:`nuclearff.sleeper.client.SleeperClient`) and the
    DuckDB tables this project writes (:mod:`nuclearff.duckdb_io`).

    Args:
        cache_dir: Directory nflreadpy should cache downloads under.
        cache_duration: Seconds before a cached download is considered stale.
    """
    # nflreadpy_config.update_config() assigns straight through `setattr`
    # without pydantic coercion, even though `cache_dir` is typed `Path` and
    # its own docstring says a `str` is accepted. Passing a `str` here leaves
    # `config.cache_dir` as a `str`, which crashes the downloader's `.mkdir()`
    # call the first time it actually needs the cache directory. Passing a
    # real `Path` sidesteps the missing coercion entirely.
    nflreadpy_config.update_config(
        cache_mode="filesystem",
        cache_dir=Path(cache_dir),
        cache_duration=cache_duration,
    )


def load_ff_playerids() -> pl.DataFrame:
    """Load the DynastyProcess fantasy player ID crosswalk.

    Returns:
        One row per known player, with ``sleeper_id``, ``gsis_id``, and IDs
        for several other platforms. ``sleeper_id`` is not unique in this
        data — see :func:`nuclearff.ids.crosswalk.ambiguous_sleeper_ids`.
    """
    logger.info("Fetching nflverse ff_playerids crosswalk")
    return nflreadpy.load_ff_playerids()
