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
- Typed exception hierarchy (`NuclearffError`, `ConfigError`, `SleeperAPIError`
  and friends).
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
