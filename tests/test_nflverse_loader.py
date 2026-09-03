"""Unit tests for the nflreadpy wrapper functions.

These never touch the network: `load_ff_playerids` and `configure_cache` are
thin wrappers, so the tests only verify delegation using monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

import nuclearff.nflverse.loader as loader


def test_load_ff_playerids_delegates_to_nflreadpy(monkeypatch):
    """The wrapper returns exactly what nflreadpy.load_ff_playerids returns."""
    expected = pl.DataFrame({"sleeper_id": [1], "gsis_id": ["00-0000001"]})
    monkeypatch.setattr(loader.nflreadpy, "load_ff_playerids", lambda: expected)

    result = loader.load_ff_playerids()

    assert result is expected


def test_configure_cache_sets_filesystem_mode(monkeypatch, tmp_path):
    """configure_cache points nflreadpy's own cache at the given directory."""
    calls: list[dict] = []
    monkeypatch.setattr(
        loader.nflreadpy_config, "update_config", lambda **kwargs: calls.append(kwargs)
    )

    loader.configure_cache(tmp_path, cache_duration=3600)

    assert calls == [
        {
            "cache_mode": "filesystem",
            "cache_dir": Path(tmp_path),
            "cache_duration": 3600,
        }
    ]


def test_configure_cache_passes_a_path_not_a_string(monkeypatch, tmp_path):
    """A `str` cache_dir would leave nflreadpy's config un-coerced and crash its
    downloader on `.mkdir()`; configure_cache must always pass a real Path.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        loader.nflreadpy_config, "update_config", lambda **kwargs: calls.append(kwargs)
    )

    loader.configure_cache(str(tmp_path))

    assert isinstance(calls[0]["cache_dir"], Path)


def test_configure_cache_defaults_to_a_day(monkeypatch, tmp_path):
    """The default cache duration matches Sleeper-style once-a-day guidance."""
    calls: list[dict] = []
    monkeypatch.setattr(
        loader.nflreadpy_config, "update_config", lambda **kwargs: calls.append(kwargs)
    )

    loader.configure_cache(tmp_path)

    assert calls[0]["cache_duration"] == loader.DEFAULT_CACHE_DURATION
