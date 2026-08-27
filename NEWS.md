# News

## nuclearff 0.1.0 (unreleased)

Initial scaffold of **nuclearff** — Nuclear Fantasy Football.

nuclearff is a Python package for fantasy football research and analysis,
built on:

- [nflreadpy](https://github.com/nflverse/nflreadpy) for nflverse NFL data
- [Sleeper API](https://docs.sleeper.com) for league, roster, and matchup data

Project tooling:

- `src/` layout with the `uv_build` build backend
- Ruff for formatting and linting
- `ty` for type checking
- `uv` for virtual environment and dependency management
- pytest for unit testing with coverage
- Sphinx documentation with PyData theme
- GitHub Actions for CI/CD
