# Examples

Runnable Jupyter notebooks mirroring the [User Tutorial](https://nolmacdonald.github.io/nuclearff/tutorial/index.html)
docs, one notebook per chapter, with real cells executed against a real
Sleeper league (`NUCLEARFF REDRAFT`, `1367225133634191360`) and two other
real leagues on the same account where that one doesn't fit (a completed
playoff season, and a real auction draft — see Chapter 9). Notebooks 1
through 17 call the library directly, exactly like the docs page's own code
blocks; only Chapter 18 shells out to the `nuclearff` CLI, using `!uv run
nuclearff ...` so it always runs against this repo's current source rather
than a separately installed, possibly stale copy.

| Notebook | Mirrors |
|---|---|
| [`01_introduction.ipynb`](01_introduction.ipynb) | [1. Introduction](https://nolmacdonald.github.io/nuclearff/tutorial/01_introduction.html) |
| [`02_configuration.ipynb`](02_configuration.ipynb) | [2. Configuration](https://nolmacdonald.github.io/nuclearff/tutorial/02_configuration.html) |
| [`03_sleeper_api.ipynb`](03_sleeper_api.ipynb) | [3. The Sleeper API in Python](https://nolmacdonald.github.io/nuclearff/tutorial/03_sleeper_api.html) |
| [`04_capturing_a_league.ipynb`](04_capturing_a_league.ipynb) | [4. Capturing and Storing a League](https://nolmacdonald.github.io/nuclearff/tutorial/04_capturing_a_league.html) |
| [`05_scoring_engine.ipynb`](05_scoring_engine.ipynb) | [5. The Scoring Engine](https://nolmacdonald.github.io/nuclearff/tutorial/05_scoring_engine.html) |
| [`06_metrics.ipynb`](06_metrics.ipynb) | [6. Opportunity and Efficiency Metrics](https://nolmacdonald.github.io/nuclearff/tutorial/06_metrics.html) |
| [`07_projections.ipynb`](07_projections.ipynb) | [7. Projections](https://nolmacdonald.github.io/nuclearff/tutorial/07_projections.html) |
| [`08_valuation.ipynb`](08_valuation.ipynb) | [8. Replacement Level, VORP, and Draft Tiers](https://nolmacdonald.github.io/nuclearff/tutorial/08_valuation.html) |
| [`09_auction_draft_board.ipynb`](09_auction_draft_board.ipynb) | [9. Building an Auction Draft Board](https://nolmacdonald.github.io/nuclearff/tutorial/09_auction_draft_board.html) |
| [`10_draft_and_playoff_visuals.ipynb`](10_draft_and_playoff_visuals.ipynb) | [10. Draft and Playoff Visualizations](https://nolmacdonald.github.io/nuclearff/tutorial/10_draft_and_playoff_visuals.html) |
| [`11_simulation.ipynb`](11_simulation.ipynb) | [11. Monte Carlo Season Simulation](https://nolmacdonald.github.io/nuclearff/tutorial/11_simulation.html) |
| [`12_backtesting.ipynb`](12_backtesting.ipynb) | [12. Backtesting and Model Validation](https://nolmacdonald.github.io/nuclearff/tutorial/12_backtesting.html) |
| [`13_league_history.ipynb`](13_league_history.ipynb) | [13. League History and Records](https://nolmacdonald.github.io/nuclearff/tutorial/13_league_history.html) |
| [`14_trade_network.ipynb`](14_trade_network.ipynb) | [14. Trade Network Analysis](https://nolmacdonald.github.io/nuclearff/tutorial/14_trade_network.html) |
| [`15_wins_and_leagues.ipynb`](15_wins_and_leagues.ipynb) | [15. Cumulative Wins and League Overviews](https://nolmacdonald.github.io/nuclearff/tutorial/15_wins_and_leagues.html) |
| [`16_draft_companion_tools.ipynb`](16_draft_companion_tools.ipynb) | [16. In Progress: Draft-Day Matchup Tools](https://nolmacdonald.github.io/nuclearff/tutorial/16_draft_companion_tools.html) |
| [`17_querying_and_provenance.ipynb`](17_querying_and_provenance.ipynb) | [17. Querying Everything, and Provenance](https://nolmacdonald.github.io/nuclearff/tutorial/17_querying_and_provenance.html) |
| [`18_cli_reference.ipynb`](18_cli_reference.ipynb) | [18. CLI Reference](https://nolmacdonald.github.io/nuclearff/tutorial/18_cli_reference.html) |

## Running

From the repo root:

```bash
uv sync --frozen --extra dev
uv run jupyter lab
```

Open a notebook from `examples/` and run it top to bottom. **Run them in
order** — Chapter 4 onward each depend on state an earlier chapter wrote
(the config from Chapter 2, the league snapshot and DuckDB tables from
Chapter 4), the same dependency chain the docs chapters themselves
describe. Every notebook fetches or reads into `examples/demo/` (gitignored),
so no separate setup step is required beyond running the earlier chapters
first.

## Regenerating

The notebooks are built from [`build_notebooks.py`](build_notebooks.py) via
`nbformat` rather than hand-edited as `.ipynb` JSON. To regenerate and
re-execute all 18, in order, from the repo root:

```bash
uv run python examples/build_notebooks.py
cd examples
uv run --project .. jupyter nbconvert --to notebook --execute --inplace \
    01_introduction.ipynb 02_configuration.ipynb 03_sleeper_api.ipynb \
    04_capturing_a_league.ipynb 05_scoring_engine.ipynb 06_metrics.ipynb \
    07_projections.ipynb 08_valuation.ipynb 09_auction_draft_board.ipynb \
    10_draft_and_playoff_visuals.ipynb 11_simulation.ipynb \
    12_backtesting.ipynb 13_league_history.ipynb 14_trade_network.ipynb \
    15_wins_and_leagues.ipynb 16_draft_companion_tools.ipynb \
    17_querying_and_provenance.ipynb 18_cli_reference.ipynb
cd ..
uv run ruff format examples/
```

`nbconvert` executes each notebook file individually, one kernel per file —
run them in the same order as the list above (matching the numbering) so
each chapter's fetch/write cells have already run before the next chapter
reads them back. The final `ruff format` pass is required, not cosmetic —
`build_notebooks.py`'s generated cell source isn't pre-wrapped to ruff's
line length, and CI runs `ruff format --check .` (and `ruff check .`)
across the whole repo, `examples/` included.
