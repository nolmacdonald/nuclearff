"""Thin wrappers around nflreadpy for nflverse data."""

from nuclearff.nflverse.loader import configure_cache, load_ff_playerids
from nuclearff.nflverse.stats import (
    load_ff_opportunity,
    load_ngs_receiving,
    load_players,
    load_routes,
    load_seasonal_receiving,
    load_seasonal_skill_stats,
    load_weekly_receiving,
    load_weekly_skill_stats,
)

__all__ = [
    "configure_cache",
    "load_ff_opportunity",
    "load_ff_playerids",
    "load_ngs_receiving",
    "load_players",
    "load_routes",
    "load_seasonal_receiving",
    "load_seasonal_skill_stats",
    "load_weekly_receiving",
    "load_weekly_skill_stats",
]
