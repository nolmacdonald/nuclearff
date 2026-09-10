"""Regenerates the notebooks in this directory (GitHub Issue 89).

Builds each notebook's cells via ``nbformat`` rather than hand-editing
``.ipynb`` JSON directly. Run from the repo root after ``uv sync --extra
dev``:

.. code-block:: bash

   uv run python examples/build_notebooks.py
   cd examples
   uv run --project .. jupyter nbconvert --to notebook --execute --inplace \\
       00_getting_started.ipynb 01_user_guide.ipynb 02_league_trade_history.ipynb

Not part of the installable package -- a maintenance script for this
directory only, matching ``dev/tables/ex_table.py``'s own "regenerate this
artifact" role for that directory, not imported by anything.
"""

from __future__ import annotations

import nbformat as nbf


def nb(cells: list) -> nbf.NotebookNode:
    notebook = nbf.v4.new_notebook()
    notebook["cells"] = cells
    notebook["metadata"] = {
        "kernelspec": {
            "display_name": "Python 3 (uv)",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python", "pygments_lexer": "ipython3"},
    }
    return notebook


def md(text: str):
    return nbf.v4.new_markdown_cell(text)


def code(text: str):
    return nbf.v4.new_code_cell(text)


# --- 00_getting_started.ipynb -----------------------------------------------

getting_started = nb(
    [
        md(
            "# Getting Started\n\n"
            "An introduction to `nuclearff`, the Nuclear Fantasy Football "
            "Python package. This notebook mirrors the [Getting Started]"
            "(https://nolmacdonald.github.io/nuclearff/getting_started.html) "
            "docs page, with real cells run against a real Sleeper league.\n\n"
            "**Running this notebook:** from the repo root, `uv sync --frozen "
            "--extra dev` once, then `uv run jupyter lab`. Every shell cell "
            "below uses `!uv run nuclearff ...` rather than a hardcoded venv "
            "path, so it always runs against this repo's current source and "
            "synced environment -- not a separately installed, possibly "
            "stale copy."
        ),
        md(
            "## Installation\n\n"
            "`nuclearff` is not published to PyPI. From a clone of the "
            "repository:\n\n"
            "```bash\n"
            "git clone https://github.com/nolmacdonald/nuclearff.git\n"
            "cd nuclearff\n"
            "uv sync --frozen\n"
            "```\n\n"
            "`--frozen` installs exactly what `uv.lock` specifies. Install "
            "the dev extras to run linting, type checking, and tests "
            "(`uv sync --frozen --extra dev`), or the docs extras to build "
            "documentation (`uv sync --frozen --extra docs`)."
        ),
        md(
            "## Configuration\n\n"
            "Every command reads its settings from a configuration file, so "
            "a run can be reproduced from a git commit plus a config."
        ),
        code("!uv run nuclearff config init -o ./configs/nuclearff.yaml --force"),
        md(
            "`paths.root` anchors every managed directory. This notebook "
            "passes `--root ./demo` on every command below, the same way "
            "the docs pages do -- it keeps this notebook's output "
            "self-contained under `examples/demo/`, off the real project "
            "data tree. (Don't reuse `./demo/data/raw` under a *fixed* "
            "`root/data/...` layout -- `paths.data` defaults to `data`, "
            "so `--root ./data` doubles into `./data/data/...`; a non-"
            "`data`-named root avoids that.)"
        ),
        code("!uv run nuclearff --root ./demo config paths --ensure"),
        md(
            "## Capturing Your League\n\n"
            "Fetch a complete, immutable snapshot of a real Sleeper league "
            "-- this project's own running example, `NUCLEARFF REDRAFT` "
            "(`1367225133634191360`)."
        ),
        code(
            "!uv run nuclearff --root ./demo sleeper fetch-league "
            "--league-id 1367225133634191360"
        ),
        md(
            "The command writes each endpoint to "
            "`data/raw/sleeper/<league_id>/<timestamp>/` and never "
            "overwrites an existing capture. It also reports league "
            "settings that are contradictory or that change how players "
            "should be valued -- both warnings printed above are real for "
            "this league, not illustrative.\n\n"
            "This is the starting point; `fetch-league` also has flags for "
            "multi-season history, standings and playoff results, weekly "
            "matchups, transaction history, and roster composition -- see "
            "`01_user_guide.ipynb` for a complete walkthrough."
        ),
        md(
            "## Data Sources\n\n"
            "`nuclearff` builds on two data sources:\n\n"
            "- **nflverse / nflreadpy** -- "
            "[nflreadpy](https://github.com/nflverse/nflreadpy) provides "
            "NFL play-by-play, player, roster, schedule, and advanced "
            "statistics data as `polars.DataFrame` objects, cached locally "
            "between calls.\n"
            "- **Sleeper API** -- the "
            "[Sleeper API](https://docs.sleeper.com) is a read-only, "
            "unauthenticated HTTP API exposing leagues, rosters, matchups, "
            "transactions, and player metadata. See the [Sleeper API "
            "Tutorial](https://nolmacdonald.github.io/nuclearff/"
            "sleeper_api_tutorial.html) for a full walkthrough of fetching "
            "this data directly in Python."
        ),
        md(
            "## Quick Start\n\n"
            "A minimal example independent of the CLI: weekly player "
            "statistics via `nflreadpy` directly."
        ),
        code(
            "import logging\n\n"
            "import nflreadpy as nfl\n\n"
            "from nuclearff import configure_logging\n\n"
            "configure_logging(level=logging.INFO)\n\n"
            "# Weekly player statistics for the 2023 and 2024 seasons\n"
            "player_stats = nfl.load_player_stats([2023, 2024])\n"
            "player_stats.head()"
        ),
        md(
            "`nflreadpy` returns [polars](https://docs.pola.rs) "
            "DataFrames. Use `.to_pandas()` if a downstream tool expects a "
            "pandas DataFrame instead.\n\n"
            "## Next steps\n\n"
            "- `01_user_guide.ipynb` -- every other CLI command: league "
            "history, standings, matchups, transactions, roster "
            "composition, player filtering, auction/keeper valuation, and "
            "the draft board.\n"
            "- `02_league_trade_history.ipynb` -- all 10 `report trades` "
            "visualizations.\n"
            "- [User Guide]"
            "(https://nolmacdonald.github.io/nuclearff/user_guide.html) and "
            "[League Trade History]"
            "(https://nolmacdonald.github.io/nuclearff/league_trade_history.html) "
            "-- the matching docs pages."
        ),
    ]
)

with open("examples/00_getting_started.ipynb", "w") as f:
    nbf.write(getting_started, f)

print("wrote examples/00_getting_started.ipynb")

# --- 01_user_guide.ipynb -----------------------------------------------

REDRAFT = "1367225133634191360"
PLAYOFF_LEAGUE = "1240509989819273216"  # this league's real completed 2025 season
AUCTION_LEAGUE = "1387966835797798912"  # Freeman Forever League -- real auction draft

user_guide = nb(
    [
        md(
            "# User Guide\n\n"
            "Every `nuclearff` CLI command and what it builds, in the order "
            "you'd actually reach for them. Mirrors the "
            "[User Guide](https://nolmacdonald.github.io/nuclearff/user_guide.html) "
            "docs page. Every example below runs against real Sleeper data "
            "-- `1367225133634191360` (`NUCLEARFF REDRAFT`, this project's "
            "running example) for most sections, plus two other real "
            "leagues on the same account where the running example doesn't "
            "fit (a real completed playoff season, and a real auction "
            "draft).\n\n"
            "Run `00_getting_started.ipynb` first if you haven't -- this "
            "notebook assumes `./demo` already has a config and continues "
            "building on it."
        ),
        md(
            "## Command groups\n\n"
            "Every command reads a configuration file and writes beneath "
            "`paths.root` (`--root`, which is how every example here keeps "
            "its output under `examples/demo/` instead of your real project "
            "data)."
        ),
        code("!uv run nuclearff --help"),
        md(
            "## Capturing a league\n\n"
            "The starting point for everything else is a league snapshot. "
            "This writes every raw endpoint to an immutable, timestamped "
            "directory (never overwritten) and a typed `LeagueConfig` YAML "
            "derived from the league's scoring and roster settings. "
            '"Settings to review" flags real settings that are internally '
            "inconsistent or otherwise worth a second look -- both warnings "
            "below are real for this league, not illustrative."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT}"
        ),
        md(
            "## Multi-season history\n\n"
            "Sleeper links a league to its prior season via "
            "`previous_league_id`. `--history` walks that chain all the way "
            "back -- for this league, all the way to its real 2021 "
            "inception, six real seasons. Two tables land in DuckDB: "
            "`sleeper_leagues` (raw settings/scoring/roster JSON per "
            "season) and `sleeper_league_configs` (the same data, typed and "
            "flattened)."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --history"
        ),
        md(
            "## Standings and playoff results\n\n"
            "`--standings` computes, per season, each roster's "
            "regular-season record *and* where they actually finished after "
            "the playoffs -- genuinely different things. In this league's "
            "real 2025 season, the best regular-season record (20-8) "
            "finished **4th**; the eventual champion had fewer wins (16-12) "
            "and won it in the playoffs."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --standings"
        ),
        code(
            "import duckdb\n\n"
            'DB = "./demo/data/cache/nuclearff.duckdb"\n\n'
            "with duckdb.connect(DB, read_only=True) as conn:\n"
            '    rows = conn.execute("""\n'
            "        SELECT display_name, wins, losses, regular_season_rank, "
            "final_rank\n"
            "        FROM sleeper_standings\n"
            f"        WHERE league_id = '{PLAYOFF_LEAGUE}'\n"
            "        ORDER BY COALESCE(final_rank, 999)\n"
            '    """).fetchall()\n'
            "rows"
        ),
        md(
            "**A real DuckDB gotcha worth knowing**: reconnect fresh "
            "(`with duckdb.connect(DB, read_only=True) as conn:`) before "
            "every query in this notebook, rather than opening one "
            "connection at the top and reusing it -- a read-only "
            "connection opened before a later `!uv run nuclearff ...` cell "
            "writes a *new* table doesn't see that table until you "
            "reconnect. Each query cell below reconnects for exactly this "
            "reason.\n\n"
            "Final placement is deliberately conservative: computed only "
            "from winners-bracket matches carrying a `p` (placement) field "
            "-- a match's winner gets rank `p`, its loser `p + 1`. The "
            "losers bracket's own placement field is captured raw but never "
            "turned into a guessed `final_rank`, since its numbering "
            "convention couldn't be confirmed against real data.\n\n"
            "## Playoff bracket visualization\n\n"
            "`report playoff-bracket` renders a completed season's winners "
            "and losers brackets as horizontal tree PNGs. That's this "
            "league's real, completed 2025 season below: 7 winners-bracket "
            "matches and 4 losers-bracket matches. Each leaf is labeled with "
            "the real team display name; the winner of each match renders "
            "bold; a match carrying a placement appends the resulting rank "
            "(e.g. `nolmacdonald (1st)`)."
        ),
        code(
            f"!uv run nuclearff --root ./demo report playoff-bracket {PLAYOFF_LEAGUE} "
            f'--season 2025 --league-name "NUCLEARFF REDRAFT"'
        ),
        code(
            "from IPython.display import Image, display\n\n"
            f'display(Image(filename="./demo/data/artifacts/{PLAYOFF_LEAGUE}-2025/brackets/winners_bracket.png"))\n'
            f'display(Image(filename="./demo/data/artifacts/{PLAYOFF_LEAGUE}-2025/brackets/losers_bracket.png"))'
        ),
        md(
            "## Weekly matchups\n\n"
            "`--matchups` fetches every week's roster-vs-roster scoring, for "
            "every season in the chain. `--max-week` caps how many weeks "
            "per season (default 18); weeks that haven't happened yet "
            "return no data and are silently skipped, not an error."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --matchups --max-week 3"
        ),
        md(
            "## Transaction history\n\n"
            "`--transactions` fetches every week's adds, drops, waivers, "
            "and trades, across the full history chain. Sleeper's own "
            "`type`/`status` fields are trusted and stored as-is, not "
            "re-derived -- a full-history fetch against this league "
            "surfaced four distinct transaction types (`waiver`, "
            "`free_agent`, `trade`, and a `commissioner` type not seen in a "
            "smaller single-season sample), all handled with no code "
            "change needed."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --transactions --max-week 3"
        ),
        code(
            "with duckdb.connect(DB, read_only=True) as conn:\n"
            '    rows = conn.execute("SELECT type, COUNT(*) FROM '
            'sleeper_transactions GROUP BY type").fetchall()\n'
            "rows"
        ),
        md(
            "## Roster composition\n\n"
            "`--roster-players` categorizes every roster's players by slot "
            "(`starter`, `reserve`/IR, `taxi`, `bench`), per season. Sleeper "
            'fills an empty starting slot with the literal string `"0"` '
            "before a draft; that filler is dropped rather than stored as a "
            "phantom player."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --roster-players"
        ),
        code(
            "with duckdb.connect(DB, read_only=True) as conn:\n"
            '    rows = conn.execute("SELECT slot, COUNT(*) FROM '
            'sleeper_roster_players GROUP BY slot").fetchall()\n'
            "rows"
        ),
        md(
            "## Combining flags\n\n"
            "Every flag above implies `--history` and is independent of the "
            "others -- combine as many as you want in one run; each writes "
            "its own table(s) without disturbing the rest. Every write "
            "replaces its table wholesale, so a fresh run is a fresh full "
            "snapshot, not an incremental append."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --history --standings --matchups --max-week 3"
        ),
        md(
            "## Discovering leagues from a username\n\n"
            "Every command above needs an already-known `league_id`. If "
            "you only have a Sleeper username, resolve it first -- this "
            "resolves the username to a `user_id` via `GET "
            "/v1/user/<username>`, the same resolution whether you pass a "
            "username or a raw numeric id."
        ),
        code(
            "!uv run nuclearff --root ./demo sleeper user-leagues nolmacdonald "
            "--season 2026"
        ),
        md(
            "`sleeper user-drafts <username> --season <year>` works the "
            "same way for drafts instead of leagues.\n\n"
            "## The player map, filtering, and trending\n\n"
            "`fetch-players` pulls Sleeper's full NFL player map (roughly "
            "5 MB) into a `sleeper_players` table -- the join target for "
            "every `player_id` column in this project. Cached to disk for "
            "24 hours; `--force-refresh` bypasses that."
        ),
        code("!uv run nuclearff --root ./demo sleeper fetch-players"),
        md(
            "If you only need part of the map, filter server-side instead "
            "of fetching everything -- confirmed live, this cuts the "
            "payload from roughly 14.6 MB to 435 KB."
        ),
        code(
            "from nuclearff.sleeper import SleeperClient\n\n"
            "with SleeperClient() as client:\n"
            '    active_qbs = client.get_players(position="QB", active=True)\n\n'
            "len(active_qbs)"
        ),
        md(
            "`sleeper trending` shows the most-added/dropped players "
            "league-wide right now, resolved to real names when a local "
            "player table exists. Team defenses (e.g. a real result like "
            '"Las Vegas Raiders") resolve through a first/last-name '
            "fallback -- Sleeper's real payload gives a defense entry no "
            "`full_name` field, only split `first_name`/`last_name`."
        ),
        code("!uv run nuclearff --root ./demo sleeper trending --limit 5"),
        md(
            "## Cross-referencing to nflverse\n\n"
            "Sleeper player objects already carry `gsis_id` -- nflverse's "
            "own primary key -- but not every player has one. `ids "
            "resolve-gsis` fills the gap from nflverse's own ID crosswalk, "
            "skipping any `sleeper_id` that maps to more than one player "
            "rather than guessing. Requires `fetch-players` to have run "
            "first."
        ),
        code("!uv run nuclearff --root ./demo ids resolve-gsis"),
        md(
            "## Auction/keeper draft valuation\n\n"
            "If your league runs (or ran) an auction draft, `report "
            "auction-board` turns realized fantasy points into a priced "
            "draft board -- recency-weighted realized points, per-position "
            "replacement level and VORP, FantasyPros consensus join, "
            "dollars that sum to exactly the league's real budget. "
            "`NUCLEARFF REDRAFT` (this notebook's running example "
            "everywhere else) runs a snake draft, so it isn't usable for "
            "this particular command -- `Freeman Forever League` below is "
            "a different real league on the same account whose "
            '`draft.type` is `"auction"`.'
        ),
        code(
            f"!uv run nuclearff --root ./demo report auction-board {AUCTION_LEAGUE} "
            f"--seasons 2024 2025 --as-of-season 2026"
        ),
        code(
            "from IPython.display import Image, display\n\n"
            f'display(Image(filename="./demo/data/artifacts/{AUCTION_LEAGUE}-2026/tables/top_12_wr.png"))'
        ),
        md(
            "Output is a CSV, a markdown report with methodology notes, "
            "and (unless `--no-tables`) a styled PNG table per position "
            "(QB/RB/WR/TE) -- the WR table is shown above as an example. "
            "Keeper cost adjustment is implemented but not yet wired into "
            "this command -- Sleeper exposes no keeper-price endpoint, so "
            "keeper costs must be supplied by the caller."
        ),
        md(
            "## Draft board visualization\n\n"
            "`report draft-board` renders a draft as a snake-order grid of "
            "position-colored pick cards, matching Sleeper's own "
            "draft-room UI: one column per draft slot, one row per round. "
            "Cell color follows position (green RB, blue WR, pink QB, "
            "orange TE), confirmed against Sleeper's own draft-room UI. A "
            "draft's pick order isn't always a simple alternating snake -- "
            "this league's own draft has `settings.reversal_round: 3`; the "
            "grid doesn't compute pick order itself, each pick's own "
            "`draft_slot` is already resolved correctly by Sleeper, "
            "reversal round included."
        ),
        code(f"!uv run nuclearff --root ./demo report draft-board {REDRAFT}"),
        code(
            "import glob\n\n"
            "from IPython.display import Image, display\n\n"
            f'display(Image(filename=glob.glob("./demo/data/artifacts/{REDRAFT}-draft-board/*.png")[0]))'
        ),
        md(
            "## League avatar table\n\n"
            "`report user-leagues` resolves a username (or user id) to "
            "every league they're in for a season and renders it as a PNG "
            "table -- avatar, name, league id, type, team count, status -- "
            "with a per-type count summary at the bottom. `Type` resolves "
            "Sleeper's numeric `settings.type` (0/1/2/3) to a readable "
            "label, not a name-substring match."
        ),
        code(
            "!uv run nuclearff --root ./demo report user-leagues nolmacdonald "
            "--season 2026"
        ),
        code(
            "from IPython.display import Image, display\n\n"
            'display(Image(filename="./demo/data/artifacts/332632476830679040-leagues/2026.png"))'
        ),
        md(
            "## Cumulative wins\n\n"
            "`report wins` derives a per-week win/loss/tie from stored "
            "matchup history (nothing stores this directly -- it's computed "
            "by comparing the two rosters sharing a matchup) and renders "
            "each manager's running win total as a step chart, one line "
            "per manager, ending in that manager's real Sleeper headshot "
            "instead of a text label.\n\n"
            "By default, only managers currently rostered in `league_id`'s "
            "own season appear. `--all-users` includes every manager "
            "across the league's full history instead -- passed below "
            "since this is a historical walkthrough."
        ),
        code(f"!uv run nuclearff --root ./demo report wins {REDRAFT} --all-users"),
        code(
            "from IPython.display import Image, display\n\n"
            f'display(Image(filename="./demo/data/artifacts/{REDRAFT}-wins.png"))'
        ),
        md(
            "## Draft order history\n\n"
            "`report draft-order` (needs `--drafts` fetched first, below) "
            "renders a manager's historical draft-order table: seasons "
            "drafted, average draft position, times drafted 1st overall, "
            'times drafted last. "Last pick" is season-relative -- a '
            "season's own real maximum draft slot, since team count can "
            "vary by season."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --drafts"
        ),
        code(
            f"!uv run nuclearff --root ./demo report draft-order {REDRAFT} --all-users"
        ),
        code(
            "from IPython.display import Image, display\n\n"
            f'display(Image(filename="./demo/data/artifacts/{REDRAFT}-draft-order.png"))'
        ),
        md(
            "## Trade network analysis\n\n"
            "`report trades` turns a league's stored trade history into "
            "ten PNGs -- who trades, who trades with whom, and how that's "
            "changed over time. It's its own notebook: "
            "`02_league_trade_history.ipynb`.\n\n"
            "## Querying what you've built\n\n"
            "Every table above lives in one DuckDB file. Query it directly "
            "with the `duckdb` CLI, or from Python:"
        ),
        code(
            "with duckdb.connect(DB, read_only=True) as conn:\n"
            '    conn.sql("SHOW TABLES").show()'
        ),
        md(
            "Every table is keyed by `league_id` (and usually `season`), "
            "so joining across them is ordinary SQL, not custom Python per "
            "question.\n\n"
            "## See Also\n\n"
            "- `00_getting_started.ipynb` -- installation and "
            "configuration.\n"
            "- `02_league_trade_history.ipynb` -- `report trades`'s ten "
            "trade-history visualizations, in depth.\n"
            "- [Sleeper API Tutorial](https://nolmacdonald.github.io/"
            "nuclearff/sleeper_api_tutorial.html) -- the Sleeper API "
            "itself, independent of the CLI.\n"
            "- [API Reference]"
            "(https://nolmacdonald.github.io/nuclearff/api/index.html) "
            "-- full reference for every public class and function."
        ),
    ]
)

with open("examples/01_user_guide.ipynb", "w") as f:
    nbf.write(user_guide, f)

print("wrote examples/01_user_guide.ipynb")

# --- 02_league_trade_history.ipynb --------------------------------------

TRADES_DIR = f"./demo/data/artifacts/{REDRAFT}-trades"


def trade_figure(filename: str, alt: str):
    return code(
        "from IPython.display import Image, display\n\n"
        f'display(Image(filename="{TRADES_DIR}/{filename}"))  # {alt}'
    )


league_trade_history = nb(
    [
        md(
            "# League Trade History\n\n"
            "`report trades` turns a league's stored trade history into "
            "ten PNGs -- who trades, who trades with whom, and how that's "
            "changed over time. Mirrors the [League Trade History]"
            "(https://nolmacdonald.github.io/nuclearff/league_trade_history.html) "
            "docs page, grounded in one real league's real data "
            "(`1367225133634191360`, `NUCLEARFF REDRAFT`): 22 completed "
            "trades since 2021, across the 15 managers who have ever held "
            "a roster in the league.\n\n"
            "This is the same `sleeper_transactions` table `sleeper "
            "fetch-league --transactions` writes -- see "
            '`01_user_guide.ipynb`\'s "Transaction history" section if '
            "you haven't fetched it yet. No new fetching happens here; "
            "this command only reads and renders what's already stored.\n\n"
            "By default (`--all-users` not passed) this command only "
            "includes managers currently rostered in `league_id`'s own "
            "season -- 10 of this league's real 15 all-time managers. "
            "Since this notebook is specifically a full historical "
            "walkthrough, the command below passes `--all-users` to "
            "include all 15; drop the flag for a leaner, "
            "current-roster-only view instead."
        ),
        md(
            "## Fetching the full trade history\n\n"
            "This notebook is self-contained: it doesn't assume "
            "`01_user_guide.ipynb`'s `--transactions --max-week 3` "
            "illustration already ran (and that partial fetch wouldn't "
            "have this league's real full 22-trade history anyway -- it "
            "only covers each season's first 3 weeks). Fetch every "
            "transaction across the whole chain, plus `--standings` for "
            "the full manager roster (`--all-users` below needs it -- "
            "without it, a manager with zero trades has no way to be "
            "known about at all)."
        ),
        code(
            f"!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --transactions --standings"
        ),
        md("## Running the command"),
        code(f"!uv run nuclearff --root ./demo report trades {REDRAFT} --all-users"),
        md(
            "`nolmacdonald` (14 trades) is this league's most active "
            "trader by a wide margin; five managers have never made one. "
            "`--out-dir` overrides the default output location."
        ),
        md(
            "## Trades by manager\n\n"
            "A horizontal bar chart, one bar per manager, sorted highest "
            "first. A manager's bar counts **distinct trades, not trade "
            "relationships** -- Sleeper allows more than two rosters in a "
            "single trade, and this league has a real one: a 2022 "
            "three-team trade among `casitzmann`, `nolmacdonald`, and "
            "`nolanmacdonald`. Each of those three managers' bars counts "
            "it once, not twice, even though it touches two other "
            "managers each.\n\n"
            "A manager needs a resolvable Sleeper display name to appear "
            "at all. Two of this league's 22 real trades have a roster "
            "whose owner isn't in that season's user list -- a real (if "
            "rare) Sleeper data inconsistency, not a bug here -- so "
            "neither trade contributes to any manager's count."
        ),
        trade_figure("trades_by_manager.png", "Total trades per manager"),
        md(
            "## Trades between managers\n\n"
            "A manager x manager heatmap, symmetric by construction -- "
            "trades between `casitzmann` and `nolmacdonald` show as `4` "
            "in both directions -- with a `0` diagonal, since a manager "
            "can't trade with themselves. The three-team trade above "
            "lands as one `+1` for each of the three pairs it touches, "
            "not counted twice for any single pair."
        ),
        trade_figure("trades_heatmap.png", "Manager x manager trade heatmap"),
        md(
            "## Manager trade network\n\n"
            "A node-link graph: one node per manager (sized by their "
            "total trades), one edge per manager pair that has traded "
            "(widened by the trade count between that pair). With only "
            "22 trades across 15 managers, the graph is genuinely sparse "
            "-- this league's real graph has 13 edges across 15 nodes, "
            "and several managers never connect to the rest of the "
            "league at all. That's this league's real trading activity, "
            "not a rendering bug."
        ),
        trade_figure("trade_network.png", "Manager trade network graph"),
        md(
            "## Chord diagram\n\n"
            "A more presentation-oriented view of the same relationships "
            "as the network graph: managers sit evenly spaced on a "
            "circle, each pair with at least one trade joined by a "
            "curved arc widened by trade count. Node size still tracks "
            "total trades, so `nolmacdonald` is both the largest node "
            "and the center of the widest arcs.\n\n"
            "Drawn directly in matplotlib rather than through an "
            "interactive plotting library -- every other visualization "
            "on this page is a static PNG, and keeping this one the same "
            "avoids adding a headless-browser dependency for a single "
            "chart."
        ),
        trade_figure("chord_diagram.png", "Circular chord diagram"),
        md(
            "## Trade leaderboard table\n\n"
            "One reference row per manager -- Trades, Unique Partners, "
            "Most Frequent Partner, Trades With Partner -- sorted by "
            "trade count, most active first. A zero-trade manager still "
            "gets a full row rather than being omitted, with `—` in "
            "place of a partner that doesn't exist."
        ),
        trade_figure("trade_leaderboard.png", "Trade leaderboard table"),
        md(
            "## Manager-pair leaderboard\n\n"
            "A horizontal bar chart of the top 10 manager pairs by trade "
            "count, labeled `Manager A ↔ Manager B`. Unlike the "
            "heatmap it only shows pairs that actually traded -- with a "
            "real 22-trade history, that's 10 pairs across 15 managers, "
            "most tied at a single trade."
        ),
        trade_figure(
            "manager_pair_leaderboard.png", "Top manager pairs by trade count"
        ),
        md(
            "## Trades over time\n\n"
            "One thin line per manager plus a bold league-wide total "
            "line, by season. A manager's line covers only the seasons "
            "`sleeper_standings` shows them actually rostering. The "
            "league total counts each trade once regardless of how many "
            "managers it involved -- summing every manager's own line "
            "instead would roughly double-count a season's real trade "
            "volume."
        ),
        trade_figure("trades_over_time.png", "Trades by season, one line per manager"),
        md(
            "## Manager x season heatmap\n\n"
            "The same manager x season data as the line chart above, as "
            "a grid instead. Unlike the line chart, every cell is dense "
            "-- a season before or after a manager was in the league "
            "renders as the same explicit `0` as a season they rostered "
            "but didn't trade in. This league's real 2022 peak -- "
            "`nolmacdonald`'s 6 trades in a single season -- renders as "
            "the single darkest cell on the grid."
        ),
        trade_figure("manager_season_heatmap.png", "Manager x season trade heatmap"),
        md(
            "## Cumulative trade history\n\n"
            "A step chart of each manager's running trade total over "
            "time, answering \"who became the league's most prolific "
            "trader, and when did they take the lead.\" This league's "
            "real history: `nolmacdonald` is the all-time leader with 14 "
            "of the league's 22 trades, visibly taking the lead in late "
            "2023."
        ),
        trade_figure(
            "cumulative_trades.png", "Cumulative trades per manager over time"
        ),
        md(
            "## Trade partner diversity\n\n"
            "A scatter plot -- X axis is total trades, Y axis is unique "
            "trade partners -- separating a manager who trades widely "
            "from one who repeatedly trades with the same one or two "
            "people. This league's real 5 zero-trade managers collapse "
            "into a single labeled point at the origin rather than five "
            "fully-overlapping ones. `nolmacdonald` (14 trades, 7 "
            "partners) and `casitzmann` (9 trades, 6 partners) stand out "
            "as the widest traders, while `ksavabi`, `macbuffet66`, and "
            "`nawfeastdallas` (1 trade, 1 partner each) visibly contrast "
            "with `hyoga10` (3 trades, 3 partners) -- the same raw trade "
            "count band, opposite diversity."
        ),
        trade_figure(
            "trade_partner_diversity.png", "Total trades vs. unique trade partners"
        ),
        md(
            "## Reading these charts correctly\n\n"
            "Three real details from this exact data apply across every "
            "chart above, not just one:\n\n"
            "- **A manager needs a resolvable Sleeper display name to "
            "appear at all.** Two of this league's 22 real trades have a "
            "roster whose owner isn't in that season's user list, so "
            "neither trade contributes to any chart.\n"
            "- **Sleeper allows more than two rosters in a single "
            "trade.** Every trade-counting aggregate here de-duplicates "
            "by the underlying transaction, not by exploded pairwise "
            "edges -- a 3-team trade contributes one trade to each of "
            "the three managers involved, not two.\n"
            "- **Manager identity is a display name, not a stable id**, "
            "and this league's data shows exactly why that matters: "
            "`nolmacdonald` (14 trades) and `nolanmacdonald` (2 trades, "
            "including the three-team trade above) render as two "
            "separate managers throughout every chart above. Nothing "
            "here merges similar-looking names automatically.\n\n"
            "## See Also\n\n"
            "- `01_user_guide.ipynb` -- every other `nuclearff` CLI "
            "command, including `sleeper fetch-league --transactions`, "
            "the data this notebook's command reads.\n"
            "- [Sleeper API Tutorial](https://nolmacdonald.github.io/"
            "nuclearff/sleeper_api_tutorial.html) -- the Sleeper API "
            "itself, independent of the CLI.\n"
            "- [API Reference]"
            "(https://nolmacdonald.github.io/nuclearff/api/index.html) "
            "-- full reference for `nuclearff.sleeper.trades` and "
            "`nuclearff.report.trades`."
        ),
    ]
)

with open("examples/02_league_trade_history.ipynb", "w") as f:
    nbf.write(league_trade_history, f)

print("wrote examples/02_league_trade_history.ipynb")
