<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://nolmacdonald.github.io/nuclearff/_static/logo/atom-color-dark-mode.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://nolmacdonald.github.io/nuclearff/_static/logo/atom-color-light-mode.svg">
    <img src="https://nolmacdonald.github.io/nuclearff/_static/logo/atom-color-dark-mode.svg" width="160">
  </picture>
</p>

<p align="center">
  <strong>Nuclear Fantasy Football (NUCLEARFF)</strong>
</p>

<p align="center">
  <a href="https://github.com/nolmacdonald/nuclearff/actions/workflows/ci.yml">
    <img src="https://github.com/nolmacdonald/nuclearff/actions/workflows/ci.yml/badge.svg" alt="CI" />
  </a>
  <a href="https://github.com/nolmacdonald/nuclearff/actions/workflows/docs.yml">
    <img src="https://github.com/nolmacdonald/nuclearff/actions/workflows/docs.yml/badge.svg" alt="Docs" />
  </a>
  <a href="https://github.com/nolmacdonald/nuclearff">
    <img 
      src="https://img.shields.io/badge/python-3.11%2B-777BB4?logo=python&logoColor=white" 
      alt="Python >=3.11"
    />
  </a>
  <img 
  src="https://img.shields.io/badge/linting-ruff-46a2f1?logo=ruff&logoColor=white" 
  alt="Ruff"
  />
  <img 
  src="https://img.shields.io/badge/docs-sphinx-0A507A?logo=sphinx&logoColor=white" 
  alt="Sphinx Docs"
  />
  <img 
    src="https://img.shields.io/badge/build-uv__build-261230?logo=uv&logoColor=white" 
    alt="uv build backend"
  />
</p>


<p align="center">
  <a href="https://nolmacdonald.github.io/nuclearff"> Documentation</a> |
  <a href="examples"> Examples</a> |
  <a href="https://github.com/nolmacdonald/nuclearff/issues"> Report Bug</a> |
  <a href="https://github.com/nolmacdonald/nuclearff/issues"> Request Feature</a>
</p>

---

**nuclearff** is a Python package for fantasy football research and analysis.
It pulls NFL data through the [nflverse](https://github.com/nflverse) ecosystem
via [nflreadpy](https://github.com/nflverse/nflreadpy), and league, roster, and
matchup data through the [Sleeper API](https://docs.sleeper.com).

## Data Sources

| Source                                                       | Provides                                                                       |
|--------------------------------------------------------------|--------------------------------------------------------------------------------|
| [nflreadpy](https://github.com/nflverse/nflreadpy)           | Play-by-play, player and team stats, rosters, schedules, snap counts, Next Gen Stats |
| [Sleeper API](https://docs.sleeper.com)                      | Leagues, rosters, matchups, transactions, drafts, player metadata              |

## Tooling

| Feature              | Tooling                                                                                             |
|----------------------|-----------------------------------------------------------------------------------------------------|
| Build backend        | [uv_build](https://docs.astral.sh/uv/concepts/build-backend/)                                       |
| Configuration        | [pydantic](https://docs.pydantic.dev/) + YAML                                                       |
| Formatting & linting | [ruff](https://docs.astral.sh/ruff/)                                                                |
| Type checking        | [ty](https://github.com/astral-sh/ty)                                                               |
| Virtual environment  | [uv](https://docs.astral.sh/uv/)                                                                    |
| Testing & coverage   | [pytest](https://docs.pytest.org/) + pytest-cov                                                     |
| Data frames          | [polars](https://docs.pola.rs/) + [pandas](https://pandas.pydata.org/)                              |
| HTTP client          | [requests](https://requests.readthedocs.io/) (mocked with `responses` in tests)                     |
| Documentation        | [Sphinx](https://www.sphinx-doc.org/) + [PyData theme](https://pydata-sphinx-theme.readthedocs.io/) |
| CI/CD                | [GitHub Actions](https://github.com/features/actions)                                               |

## Quick Start

```bash
git clone https://github.com/nolmacdonald/nuclearff.git
cd nuclearff
uv sync --frozen --extra dev
uv run pytest
```

Capture your league's configuration straight from Sleeper:

```bash
uv run nuclearff config init
uv run nuclearff sleeper fetch-league --league-id <your-league-id>
```

The snapshot is written to an immutable, timestamped directory under
`data/raw/sleeper/`, and any league setting that looks wrong or is load-bearing
for valuation is reported for you to confirm.

```python
import logging

import nflreadpy as nfl

from nuclearff import configure_logging

configure_logging(level=logging.INFO)

player_stats = nfl.load_player_stats([2023, 2024])
print(player_stats.head())
```

## Command Line

```text
nuclearff config init          Write a default configuration file
nuclearff config show          Print the resolved configuration
nuclearff config paths         Print (and optionally create) managed directories
nuclearff sleeper state        Print the current NFL season and week
nuclearff sleeper fetch-league Capture an immutable league snapshot
```

## Development

```bash
# Install dev dependencies
uv sync --frozen --extra dev --extra docs

# Format and lint
uv run ruff format .
uv run ruff check .

# Type check
uv run ty check src/

# Run tests
uv run pytest
```

## Documentation

```bash
uv sync --frozen --extra docs
uv run sphinx-build -b html docs/source docs/_build/html
```

Full documentation is available at **[nolmacdonald.github.io/nuclearff](https://nolmacdonald.github.io/nuclearff)**.

## Examples

The [`examples/`](examples) directory has runnable Jupyter notebooks mirroring
the docs above, with real cells executed against a real Sleeper league.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

## License

Copyright (c) 2026 Nolan MacDonald. All rights reserved. See [LICENSE](LICENSE).
