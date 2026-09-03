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
- `LeagueConfig`/`ScoringSettings`/`RosterSlots` (`nuclearff.config.league`,
  `ff_revised.md` Issue 5), typed league scoring and roster settings derived
  from a raw Sleeper league object. `starter_demand`/`replacement_rank`
  compute the VOLS/VORP value-based-drafting baseline for any position
  (QB/RB/WR/TE) from the league's actual roster slots and a configurable
  FLEX/superflex allocation rate, rather than being hardcoded to one
  position. `nuclearff sleeper fetch-league` now also writes the derived
  league config to `configs/leagues/<league_id>.yaml`. Verified against this
  project's real league: full PPR, no TE premium,
  `replacement_rank("WR", "vols") == 35` (the plan's expected WR33-36 band).
  The per-position FLEX-share and bench-fraction default rates beyond WR are
  explicitly flagged in the code as uncalibrated heuristics, to be revisited
  once real draft/roster data exists.
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
- `nuclearff.scoring.ScoringEngine` (`ff_revised.md` Issue 9), the
  league-agnostic scorer: bridges Sleeper's scoring-key vocabulary to
  nflverse's stat-column vocabulary via declarative linear/composite/
  threshold/position-bonus mapping tables, so the same engine scores any
  league's rules. `unscored_keys()` surfaces any nonzero scoring key it has
  no data source for (kicker/defense/IDP, or per-catch yardage-bucket keys)
  rather than silently under-counting. Verified against this project's real
  league and real 2024 data: reproduces `nflreadpy`'s own
  `fantasy_points_ppr` column exactly for every top player except one whose
  value correctly differs because this league's real 6-point passing-TD
  rule (from a rare WR trick-play TD) diverges from nflreadpy's generic
  4-point default — confirming the engine applies the league's actual
  rules rather than a generic assumption.
- `nuclearff.metrics.volume` (`ff_revised.md` Issue 10): `target_share`,
  `air_yards_share`, `wopr`, `racr`, `adot` — recomputed transparently from
  raw weekly data rather than trusted blindly from `nflreadpy`'s own
  pre-computed columns, with a cross-check against them. Live 2024
  validation found `air_yards_share` disagrees with nflreadpy's own column
  on ~7% of rows (an unresolved methodology difference, documented rather
  than silently accepted).
- `nuclearff.metrics.efficiency` (Issue 10): `join_routes` (bridges
  nflverse's `pfr_player_id`-keyed routes data onto `gsis_id`-keyed
  receiving data via `load_players`, rejecting ambiguous ID pairs rather
  than guessing), `yprr`, `tprr` — both sample-gated and explicitly labeled
  as running on the snap-count routes proxy, not real charted routes-run
  data.
- `nuclearff.metrics.touchdowns.expected_tds` (`ff_revised.md` Issue 11),
  regressing raw receiving TDs toward an opportunity-driven expectation via
  ffverse's own maintained `load_ff_opportunity` model, rather than
  reimplementing red-zone/air-yards TD modeling from scratch.
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
