# Contributing to nuclearff

Thank you for your interest in contributing! This guide walks through the
development workflow for nuclearff.

## Development Setup

**uv is the only supported Python environment and dependency workflow.** Do not
use `python -m venv`, `virtualenv`, `pip install`, or a hand-maintained
`requirements.txt` — the lock file is authoritative and CI installs from it.

```bash
# Clone the repository
git clone https://github.com/nolmacdonald/nuclearff.git
cd nuclearff

# Create the virtual environment and install dev dependencies
uv sync --frozen --extra dev

# Or install everything, matching CI
uv sync --frozen --extra dev --extra docs
```

`--frozen` installs exactly what `uv.lock` specifies and fails if
`pyproject.toml` has drifted from it. To change dependencies:

```bash
uv add <package>              # runtime dependency
uv add --optional dev <pkg>   # dev extra
uv lock                       # refresh the lock file
uv sync --extra dev           # apply it locally
```

Commit both `pyproject.toml` and `uv.lock` in the same change.

## Code Style

All code must pass [Ruff](https://docs.astral.sh/ruff/) formatting and linting,
and [ty](https://github.com/astral-sh/ty) type checking:

```bash
uv run ruff format .      # format
uv run ruff check .       # lint
uv run ty check src/      # type check
```

Docstrings are Google-style, matching the Sphinx `napoleon` configuration in
`docs/source/conf.py`.

## Testing

```bash
uv run pytest
```

**Tests must never touch the network.** HTTP calls are mocked with
[`responses`](https://github.com/getsentry/responses), and nflverse inputs come
from committed fixtures under `tests/fixtures/`. A test that requires a live API
is a test that will fail in CI and on an airplane.

## Documentation

```bash
uv sync --frozen --extra docs
uv run sphinx-build -b html docs/source docs/_build/html
```

Open `docs/_build/html/index.html` in a browser. CI builds the docs with `-W`,
so a Sphinx warning fails the build.

## Issues, Branches and Pull Requests

Issues are the unit of planned work. Each issue defines **Problem**, **Scope**,
**Acceptance Criteria**, **Tests**, **Artifacts/Docs**, and **Non-goals**.

1. One focused branch per issue where practical, named `issue-<n>-<slug>`
   (for example `issue-17-png-postprocessing`).
2. Open a draft pull request early so CI runs against the work in progress.
3. Write tests for your changes.
4. Ensure all checks pass locally (`ruff format --check`, `ruff check`,
   `ty check src/`, `pytest`).
5. Include `Closes #<issue>` in the pull request body.
6. CI must be green before merge; merges to `main` are squashed to keep the
   history readable.

### Labels

Labels are defined in `.github/labels.yml` and synced to GitHub by
`.github/workflows/labels.yml` on every push to `main` that changes that file.
Edit the YAML, not the GitHub UI: labels missing from the file are removed on
the next sync.

The groups are:

- `type::*` — bug, feature, enhancement, documentation, refactor, ci, test,
  dependencies, research
- `area::*` — api, docs, ci, packaging, data, model, community-data, reporting
- `status::*` — needs-triage, in-progress, blocked, ready-for-review,
  needs-changes, wont-fix
- `priority::*` and `milestone::*`

## Repository Configuration

These settings are configured in the GitHub UI and are documented here so the
required state is reviewable. Settings live under **Settings → Branches**,
**Settings → General**, and **Settings → Code security**.

**Branch protection on `main`:**

- Require a pull request before merging, with at least one approving review.
- Require status checks to pass before merging, and require branches to be up to
  date. Required checks:
  - `Lint and type check`
  - `Test (Python 3.12)`
  - `Test (Python 3.13)`
  - `Build distributions`
  - `Build documentation`
- Require conversation resolution before merging.
- Do not allow force pushes or deletions.

**General:**

- Allow squash merging only; disable merge commits and rebase merging.
- Automatically delete head branches after merge.
- Enable Issues and Projects.

**Code security:**

- Enable Dependabot alerts and security updates. Version updates are configured
  in `.github/dependabot.yml`; merge dependency updates only after CI (and, once
  they exist, backtests) pass.

**Projects board** tracks `Backlog → Ready → In Progress → Review → Done`, with
milestone fields for `MVP`, `Model Validation`, and `Draft Ready`.

## Releases

Tagged checkpoints follow the plan's milestones: `v0.1.0` scaffold and data MVP,
`v0.2.0` validated WR model, `v1.0.0` draft-ready. Pushing a `v*` tag runs
`.github/workflows/release.yml`, which re-runs the quality gates, builds with
`uv build`, and opens a draft GitHub release. Human-facing ranking and report
artifacts are attached only when they are intentionally published.

## Reporting Issues

Please use the issue templates in `.github/ISSUE_TEMPLATE/` when filing bugs,
feature requests, documentation improvements, research tasks, or data-source
proposals.

## Code of Conduct

Be kind, inclusive, and constructive. Harassment of any kind is not tolerated.
