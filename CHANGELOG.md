# Changelog

All notable changes to nuclearff will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial project structure with `src/` layout, scaffolded from the
  [ditto](https://github.com/nolmacdonald/ditto) template.
- `uv_build` build backend and `uv` dependency management with `dev` and
  `docs` extras; the lock file is authoritative and CI installs `--frozen`.
- `nuclearff` CLI entry point with `config` and `sleeper` command groups.
- Typed configuration models (`PathsConfig`, `RunConfig`, `ModelConfig`) with
  deterministic YAML round-tripping and a `--root` override for temporary
  directories.
- Read-only Sleeper API client with session reuse, retry and backoff on rate
  limits and transient server errors, request pacing, and a 24-hour disk cache
  for the player map.
- `nuclearff sleeper fetch-league`, which writes an immutable timestamped
  league snapshot and reports contradictory or load-bearing league settings.
- `nuclearff sleeper fetch-players`, which fetches the Sleeper NFL player map
  and stores a normalized subset of columns (including `gsis_id` and other
  cross-platform IDs) in a local DuckDB table for identity mapping against
  nflverse and other sources.
- `nuclearff ids resolve-gsis` (`ff_revised.md` Issue 7), which fills Sleeper
  players missing a `gsis_id` from nflverse's `ff_playerids` crosswalk,
  skips crosswalk rows whose `sleeper_id` is ambiguous rather than guessing,
  and writes a `player_id_map` table recording each `gsis_id`'s source. Real
  coverage against this project's real league: 88-95% of currently-rostered
  QB/RB/WR/TE, with the gap almost entirely players missing from this year's
  crosswalk (typically rookies).
- `nuclearff.duckdb_io`, a shared helper for reading/writing DuckDB tables via
  plain parameterized SQL rather than a Polars/pandas DataFrame handoff
  (avoids pulling in `pyarrow` as a transitive dependency for a handful of
  typed columns). Used by both `sleeper fetch-players` and `ids resolve-gsis`.
- `nuclearff.nflverse`: thin, cache-configured wrappers around `nflreadpy`
  (`ff_revised.md` Issue 6). `configure_cache`/`load_ff_playerids`
  (`nflverse.loader`) set up nflreadpy's filesystem cache and load the
  `ff_playerids` ID crosswalk; `load_weekly_receiving`,
  `load_seasonal_receiving`, `load_ngs_receiving`, `load_routes`, and
  `load_players` (`nflverse.stats`) are thin, schema-validated wrappers
  around nflreadpy's receiving-stats loaders, plus
  `load_weekly_skill_stats`/`load_seasonal_skill_stats` (QB/RB/WR/TE, via
  `SKILL_POSITIONS`) alongside the WR/RB/TE-only receiving loaders, and
  `load_ff_opportunity` (ffverse's expected-points model). `load_routes`
  labels every row with its real `source` (`snap-count proxy` vs.
  `unavailable`) rather than presenting an approximation as charted data;
  live investigation found `nflreadpy.load_ftn_charting` is not actually a
  per-player routes-run source despite the original technical plan's
  assumption, so there is currently no real routes-run data available via
  nflreadpy for any season.
- Typed exception hierarchy (`NuclearffError`, `ConfigError`, `SleeperAPIError`,
  `StorageError`, and friends).
- Logging configuration utilities.
- Sphinx documentation with PyData theme, plus decision notes under
  `docs/source/decisions/`.
- GitHub Actions workflows for CI (lint, type check, test matrix, build), docs
  deployment, and tagged releases.
- Dependabot configuration for GitHub Actions and Python dependencies.
- Issue templates for bugs, features, documentation, research tasks, and data
  sources, plus a pull request template.
- Ruff formatting and linting configuration.
- `ty` type checking configuration.

### Changed

- Docstrings are Google style, matching the project convention; the template
  shipped Sphinx configured for NumPy style.
- `requests` replaces `httpx` as the HTTP client, matching the plan and the
  `responses` test-mocking strategy.

### Fixed

- `[tool.ty]` `python-version` moved to `[tool.ty.environment]`, where current
  `ty` versions expect it. It was silently failing to parse.

[Unreleased]: https://github.com/nolmacdonald/nuclearff/commits/main
