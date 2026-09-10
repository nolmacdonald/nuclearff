# Examples

Runnable Jupyter notebooks mirroring the [docs](https://nolmacdonald.github.io/nuclearff),
with real cells executed against a real Sleeper league
(`NUCLEARFF REDRAFT`, `1367225133634191360`). Every shell cell runs `!uv run
nuclearff ...`, so it always uses this repo's current source rather than a
separately installed, possibly stale copy.

| Notebook | Mirrors |
|---|---|
| [`00_getting_started.ipynb`](00_getting_started.ipynb) | [Getting Started](https://nolmacdonald.github.io/nuclearff/getting_started.html) |
| [`01_user_guide.ipynb`](01_user_guide.ipynb) | [User Guide](https://nolmacdonald.github.io/nuclearff/user_guide.html) |
| [`02_league_trade_history.ipynb`](02_league_trade_history.ipynb) | [League Trade History](https://nolmacdonald.github.io/nuclearff/league_trade_history.html) |

## Running

From the repo root:

```bash
uv sync --frozen --extra dev
uv run jupyter lab
```

Open a notebook from `examples/` and run it top to bottom. Each one is
self-contained — it fetches its own data (under `examples/demo/`, gitignored)
before reading or visualizing it, so no separate setup step is required.

## Regenerating

The notebooks are built from [`build_notebooks.py`](build_notebooks.py) via
`nbformat` rather than hand-edited as `.ipynb` JSON. To regenerate and
re-execute all three from the repo root:

```bash
uv run python examples/build_notebooks.py
cd examples
uv run --project .. jupyter nbconvert --to notebook --execute --inplace \
    00_getting_started.ipynb 01_user_guide.ipynb 02_league_trade_history.ipynb
```
