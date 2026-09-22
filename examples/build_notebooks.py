"""Regenerates the notebooks in this directory (GitHub Issue 178).

Builds each notebook's cells via ``nbformat`` rather than hand-editing
``.ipynb`` JSON directly. Run from the repo root after ``uv sync --extra
dev``:

.. code-block:: bash

   uv run python examples/build_notebooks.py
   cd examples
   uv run --project .. jupyter nbconvert --to notebook --execute --inplace \\
       01_introduction.ipynb 02_configuration.ipynb 03_sleeper_api.ipynb \\
       04_capturing_a_league.ipynb 05_scoring_engine.ipynb 06_metrics.ipynb \\
       07_projections.ipynb 08_valuation.ipynb 09_auction_draft_board.ipynb \\
       10_draft_and_playoff_visuals.ipynb 11_simulation.ipynb \\
       12_backtesting.ipynb 13_league_history.ipynb 14_trade_network.ipynb \\
       15_wins_and_leagues.ipynb 16_draft_companion_tools.ipynb \\
       17_querying_and_provenance.ipynb 18_cli_reference.ipynb

One notebook per ``docs/source/tutorial/`` chapter (#178), numbered to
match exactly. They share one running ``./demo`` directory and must be
executed **in that order** -- each chapter after 04 reads data an earlier
chapter persisted (the config in 02, the league snapshot and DuckDB tables
in 04, the auction league's own fetch in 09), the same dependency chain the
docs chapters themselves describe. This mirrors the docs' own "Python
first" structure: Chapters 1-17 call the library directly, exactly like
the doc page's own code blocks; only Chapter 18 shells out to the
``nuclearff`` CLI.

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


def write(notebook: nbf.NotebookNode, filename: str) -> None:
    path = f"examples/{filename}"
    with open(path, "w") as f:
        nbf.write(notebook, f)
    print(f"wrote {path}")


def image(path: str, alt: str = ""):
    comment = f"  # {alt}" if alt else ""
    return code(
        "from IPython.display import Image, display\n\n"
        f'display(Image(filename="{path}")){comment}'
    )


def whats_next(text: str) -> str:
    return "## What's Next\n\n" + text


def teaching(text: str) -> str:
    """A markdown block explicitly beyond what the docs page says (#178)."""
    return "> **Why this matters:** " + text


# Real Sleeper league ids used throughout this tutorial -- see
# docs/source/tutorial's own running examples.
REDRAFT = "1367225133634191360"  # NUCLEARFF REDRAFT -- the running example
PLAYOFF_LEAGUE = "1240509989819273216"  # this league's real completed 2025 season
AUCTION_LEAGUE = "1387966835797798912"  # Freeman Forever League -- real auction draft

DB = "./demo/data/cache/nuclearff.duckdb"

NAV_HEADER = (
    "Part of the `nuclearff` [User Tutorial](https://nolmacdonald.github.io/nuclearff/"
    "tutorial/index.html) notebook series -- every notebook shares one running "
    "`./demo` directory and depends on the ones before it having already run. "
    "See [README.md](README.md) for the full run order."
)

# ==========================================================================
# 01_introduction.ipynb
# ==========================================================================

ch01 = nb(
    [
        md(
            "# 1. Introduction\n\n"
            f"{NAV_HEADER}\n\n"
            "`nuclearff` is a Python package for fantasy football research and "
            "valuation. It combines two data sources -- real NFL statistics from "
            "the [nflverse](https://github.com/nflverse) ecosystem (via "
            "[nflreadpy](https://github.com/nflverse/nflreadpy)) and league, "
            "roster, and matchup data from the "
            "[Sleeper API](https://docs.sleeper.com) -- into a single toolkit for "
            "scoring players under your league's own rules, projecting future "
            "performance, valuing a draft, simulating outcomes, and mining your "
            "league's own history.\n\n"
            "This notebook series walks through every part of that toolkit as a "
            "Python library, chapter by chapter, in the order you would actually "
            "reach for it: connect to a league, capture and store its data, score "
            "and project players, value a draft, and finally dig into your "
            "league's own history and trades. Every example runs against a real "
            "Sleeper league (`1367225133634191360`, `NUCLEARFF REDRAFT`) with "
            "real output -- your own league's numbers will differ, but the shape "
            "of the output won't. Mirrors the [Introduction]"
            "(https://nolmacdonald.github.io/nuclearff/tutorial/01_introduction.html) "
            "docs page.\n\n"
            "Notebooks `01` through `17` are plain Python -- importing functions "
            "and classes and calling them directly, the way you would inside a "
            "notebook or a script. `nuclearff` also ships a command-line "
            "interface that wraps most of this into repeatable, on-disk "
            "commands; that's covered on its own, at the end, in "
            "`18_cli_reference.ipynb`."
        ),
        md(
            teaching(
                "the docs page and this notebook cover the same ground twice on "
                "purpose. Reading the *why* in prose (the docs) and then typing "
                "the *how* yourself, cell by cell, with real output appearing as "
                "you go, builds a different kind of understanding than either "
                "alone -- this is the same reason the original 3-notebook set "
                "(#89) existed, just now kept in sync with all 18 chapters "
                "instead of 3 old, now-retired doc pages (#178)."
            )
        ),
        md(
            "## Installation\n\n"
            "`nuclearff` is not published to PyPI. Clone the repository and "
            "install it with `uv`:\n\n"
            "```bash\n"
            "git clone https://github.com/nolmacdonald/nuclearff.git\n"
            "cd nuclearff\n"
            "uv sync --frozen\n"
            "```\n\n"
            "`--frozen` installs exactly what `uv.lock` specifies. Install the "
            "development extras to get `jupyter`, `pytest`, and the other tools "
            "used throughout this tutorial:\n\n"
            "```bash\n"
            "uv sync --frozen --extra dev\n"
            "```\n\n"
            "Every cell below assumes you're running inside that environment -- "
            "this notebook itself was executed with `uv run jupyter nbconvert "
            "--execute`, not a separately installed copy."
        ),
        md(
            "## Data Sources\n\n"
            "**nflverse / nflreadpy** -- "
            "[nflreadpy](https://github.com/nflverse/nflreadpy) provides NFL "
            "play-by-play, player, roster, schedule, and advanced statistics "
            "data as `polars.DataFrame` objects. Nothing here requires an "
            "account or a key; data is cached locally between calls.\n\n"
            "**Sleeper API** -- the [Sleeper API](https://docs.sleeper.com) is a "
            "read-only, unauthenticated HTTP API exposing leagues, rosters, "
            "matchups, transactions, and player metadata. Sleeper asks clients "
            "to stay under 1000 calls per minute -- `SleeperClient` "
            "(`03_sleeper_api.ipynb`) paces requests for you automatically."
        ),
        md(
            "## A First Look\n\n"
            "Before touching Sleeper at all, here is `nuclearff` pulling real "
            "NFL statistics and configuring its own logging:"
        ),
        code(
            "import logging\n\n"
            "import nflreadpy as nfl\n\n"
            "from nuclearff import configure_logging\n\n"
            "configure_logging(level=logging.INFO)\n\n"
            "# Weekly player statistics for the 2023 and 2024 seasons\n"
            "player_stats = nfl.load_player_stats([2023, 2024])\n"
            "player_stats.select(\n"
            '    ["player_display_name", "season", "week", "position", "fantasy_points"]\n'
            ").head(10)"
        ),
        md(
            "That real query returns tens of thousands of player-week rows "
            "across two seasons -- every position, including kickers and "
            "defensive linemen, since `load_player_stats` returns everyone the "
            "box score tracks. `fantasy_points` here is nflreadpy's own generic "
            "scoring, not your league's -- `05_scoring_engine.ipynb` covers "
            "replacing it with your league's actual rules."
        ),
        code("player_stats.height"),
        md(
            "`nflreadpy` returns [polars](https://docs.pola.rs) DataFrames, and "
            "so does everything in `nuclearff` that consumes them. Use "
            "`.to_pandas()` if a downstream tool expects a `pandas.DataFrame` "
            "instead."
        ),
        md(
            whats_next(
                "`02_configuration.ipynb` covers `nuclearff`'s own "
                "configuration -- where data lives on disk, and the typed "
                "settings that drive scoring and projection -- before "
                "`03_sleeper_api.ipynb` connects to a real league.\n\n"
                "## See Also\n\n"
                "- [Introduction](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/01_introduction.html) -- the matching docs page.\n"
                "- [API Reference](https://nolmacdonald.github.io/nuclearff/"
                "api/index.html) -- full reference for every public class and "
                "function."
            )
        ),
    ]
)

# ==========================================================================
# 02_configuration.ipynb
# ==========================================================================

ch02 = nb(
    [
        md(
            "# 2. Configuration\n\n"
            f"{NAV_HEADER} Run `01_introduction.ipynb` first if you haven't.\n\n"
            "`nuclearff` reads two different kinds of configuration, and it's "
            "worth telling them apart before going further:\n\n"
            "- `NuclearffConfig` -- *your* settings: where data lives on disk, "
            "and the projection model's knobs (recency weights, age curve, "
            "simulation settings). You write this once per project.\n"
            "- `LeagueConfig` -- *the league's* settings: scoring rules and "
            "roster shape, derived from a real Sleeper league. You get one of "
            "these per league, and it's derived from Sleeper rather than "
            "hand-written.\n\n"
            "Mirrors the [Configuration](https://nolmacdonald.github.io/"
            "nuclearff/tutorial/02_configuration.html) docs page."
        ),
        md(
            "## Project configuration\n\n"
            "`default_config()` returns a `NuclearffConfig` populated with "
            "documented defaults, ready to write to disk. This notebook writes "
            "everything under `./demo` rather than a real project's data tree, "
            "the same scratch-directory convention every chapter after this "
            "one reuses."
        ),
        code(
            "from nuclearff.config import default_config, dump_config, load_config\n\n"
            "cfg = default_config()\n"
            'path = dump_config(cfg, "./demo/configs/nuclearff.yaml")\n'
            "path"
        ),
        code(
            'cfg = load_config(path, root="./demo")\n'
            "cfg.paths.ensure()\n\n"
            "for directory in cfg.paths.all_dirs():\n"
            "    print(directory.is_dir(), directory)"
        ),
        md(
            "`paths.root` anchors every one of those directories -- "
            "`cache_dir` is where the Sleeper client caches the player map and "
            "where the shared DuckDB database lives (`04_capturing_a_league.ipynb` "
            "onward); `artifacts_dir` is where every figure and report in this "
            "tutorial gets written; `leagues_dir` is where a league's derived "
            "`LeagueConfig` (below) is stored.\n\n"
            "Every field on `ModelConfig` and its nested settings is "
            "documented -- the recency weights and weight blocks drive "
            "`07_projections.ipynb`, the age curve and simulation settings "
            "drive `11_simulation.ipynb`."
        ),
        code("cfg.model"),
        md(
            teaching(
                "it's tempting to skip straight to fetching a league and "
                "treat this config as boilerplate. It isn't -- every number "
                "you'll see printed from Chapter 7 onward (a recency-weighted "
                "target share, an age-curve factor, a simulated floor/ceiling) "
                "traces back to a field on this exact object. If a later "
                "chapter's output ever looks off, `cfg.model` here is the "
                "first place to check, not the code that consumes it."
            )
        ),
        md(
            "## League configuration\n\n"
            "A `LeagueConfig` is *derived* from a real Sleeper league, not "
            "hand-written. Fetch the league and convert it:"
        ),
        code(
            "from nuclearff.config import dump_league_config, league_config_from_sleeper\n"
            "from nuclearff.sleeper import SleeperClient\n\n"
            f'league_id = "{REDRAFT}"\n\n'
            "with SleeperClient() as client:\n"
            "    league_json = client.get_league(league_id)\n\n"
            "league_cfg = league_config_from_sleeper(league_json)\n"
            "print(league_cfg.name, league_cfg.season, league_cfg.num_teams)\n"
            "print(league_cfg.roster.counts)"
        ),
        md(
            "`ScoringSettings` and `RosterSlots` both store their data as a "
            "generic mapping (`scoring.values`, `roster.counts`) rather than "
            "one named field per Sleeper key -- a league can send scoring or "
            "roster-slot keys this project has never seen (an IDP league's "
            "tackle/sack keys, a superflex slot), and a generic mapping "
            "absorbs them instead of failing to parse:"
        ),
        code('league_cfg.scoring.values["rec"], league_cfg.scoring.values["pass_td"]'),
        md(
            "`playoff_week_start` -- the first week of the fantasy playoffs -- "
            "is parsed through the same way, straight from Sleeper's own "
            "`settings.playoff_week_start` rather than a hardcoded guess:"
        ),
        code("league_cfg.playoff_week_start"),
        md(
            "It's `None` for a league that doesn't expose one (a \"Chopped\" "
            "league has no bracket at all) -- `13_league_history.ipynb`'s "
            "regular-season/playoff split uses exactly this field rather than "
            "assuming a fixed week range.\n\n"
            "Save it so it can be reloaded without hitting Sleeper again:"
        ),
        code(
            "from nuclearff.config import load_league_config\n\n"
            "path = dump_league_config(\n"
            '    league_cfg, cfg.paths.leagues_dir / f"{league_cfg.league_id}.yaml"\n'
            ")\n"
            "reloaded = load_league_config(path)\n"
            "assert reloaded == league_cfg\n"
            "path"
        ),
        md(
            "## Replacement rank and starter demand\n\n"
            "`LeagueConfig` is where value-based drafting's replacement level "
            "comes from (used throughout `08_valuation.ipynb`). "
            "`wr_starter_demand` (and the more general `starter_demand` for "
            "any position) accounts for locked WR slots *and* the assumed WR "
            "share of FLEX slots:"
        ),
        code(
            'print("WR starter demand:", league_cfg.wr_starter_demand())\n'
            'print("WR replacement rank (vols):", league_cfg.replacement_rank("WR"))\n'
            'print("WR replacement rank (vorp):", league_cfg.replacement_rank("WR", baseline="vorp"))\n'
            'print("RB replacement rank (vols):", league_cfg.replacement_rank("RB"))'
        ),
        md(
            "For this league -- 10 teams, 2 WR + 3 FLEX slots, FLEX assumed "
            "50% WR by default -- VOLS (Value Over Last Starter) says the "
            "35th-best WR is replacement level. VORP (Value Over Replacement "
            "Player) goes deeper, adding a fraction of the league's bench "
            "slots on the theory that a real share of bench spots are "
            "speculative WR stashes -- landing at a deeper rank instead. "
            'Neither number is "the" replacement level; they\'re two '
            "different, named assumptions about where a bench player becomes "
            "replaceable, and `08_valuation.ipynb` uses both."
        ),
        md(
            whats_next(
                "With a project configuration and a real league's "
                "`LeagueConfig` in hand, `03_sleeper_api.ipynb` covers the "
                "Sleeper API itself -- everything `league_config_from_sleeper` "
                "above reads from, and every other endpoint Sleeper exposes.\n\n"
                "## See Also\n\n"
                "- `01_introduction.ipynb` -- installation.\n"
                "- [Configuration](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/02_configuration.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 03_sleeper_api.ipynb
# ==========================================================================

ch03 = nb(
    [
        md(
            "# 3. The Sleeper API in Python\n\n"
            f"{NAV_HEADER} Run `02_configuration.ipynb` first if you haven't.\n\n"
            "This notebook covers the [Sleeper API](https://docs.sleeper.com) "
            "itself -- what it is and what data it exposes -- and "
            "`SleeperClient`, the thin, paced, cached wrapper every other "
            "chapter in this tutorial is built on. Mirrors the [Sleeper API "
            "Tutorial](https://nolmacdonald.github.io/nuclearff/tutorial/"
            "03_sleeper_api.html) docs page."
        ),
        md(
            "## About the Sleeper API\n\n"
            "Sleeper's API is public, free, read-only, and requires no "
            "signup, API key, or authentication of any kind -- every example "
            "below works as soon as you know a league ID. It is a plain JSON "
            "HTTP API at `https://api.sleeper.app`; the official reference "
            "lives at [docs.sleeper.com](https://docs.sleeper.com).\n\n"
            "In exchange for free, unauthenticated access, Sleeper asks "
            "callers to be polite: stay under roughly 1000 requests per "
            "minute, and fetch the full player map (the largest endpoint, ~5 "
            "MB) no more than once a day. `SleeperClient` enforces both "
            "limits itself -- requests are paced automatically, and the "
            "player map is cached to disk with a 24-hour TTL -- so nothing in "
            "this tutorial needs to hand-roll throttling.\n\n"
            "Data hangs off two roots: a **sport** (`nfl`) for the current "
            "state and the player map, and a **league** for everything else "
            "-- members, rosters, weekly matchups and transactions, and "
            "drafts. A draft has its own sub-resources (picks, traded picks) "
            "once one exists."
        ),
        md(
            teaching(
                "unauthenticated doesn't mean unlimited, and it's easy to "
                "write a naive loop (fetch every week of every season "
                "individually, in a tight `for`) that looks fine on a small "
                "league and then gets you rate-limited on a real 6-season, "
                "15-manager one. `SleeperClient`'s pacing exists specifically "
                "so you never have to notice this -- but understanding *why* "
                "it's there (a shared, free resource, politely used) matters "
                "if you ever write your own bulk-fetch loop outside this "
                "project."
            )
        ),
        md(
            "## Connecting to a league\n\n"
            "Use `SleeperClient` as a context manager so its HTTP session is "
            "closed for you. Every example below runs against the same real "
            "league used throughout this tutorial:"
        ),
        code(
            "from nuclearff.sleeper import SleeperClient\n\n"
            f'league_id = "{REDRAFT}"\n\n'
            "with SleeperClient() as client:\n"
            '    state = client.get_state("nfl")\n'
            "    league = client.get_league(league_id)\n\n"
            "state"
        ),
        md(
            "`state` is a small dict telling you which week's matchups or "
            "transactions to fetch next; `league` is the full settings "
            "object:"
        ),
        code('league["name"]'),
        code('league["roster_positions"]'),
        md(
            "`roster_positions` lists one entry per roster slot -- this is "
            "exactly what `RosterSlots` (`02_configuration.ipynb`) counts to "
            "work out starter demand and replacement level."
        ),
        md(
            "### Who owns which roster\n\n"
            "Rosters and users are separate endpoints, joined by `owner_id` / "
            "`user_id`:"
        ),
        code(
            "with SleeperClient() as client:\n"
            "    users = client.get_users(league_id)\n"
            "    rosters = client.get_rosters(league_id)\n\n"
            'names_by_user_id = {u["user_id"]: u["display_name"] for u in users}\n\n'
            'for roster in sorted(rosters, key=lambda r: r["roster_id"]):\n'
            '    owner = names_by_user_id.get(roster["owner_id"], "unknown")\n'
            '    print(roster["roster_id"], owner)'
        ),
        md(
            "## The Sleeper NFL player map\n\n"
            "`get_players()` returns every player Sleeper knows about -- "
            "roughly 5 MB of JSON -- keyed by Sleeper player ID:"
        ),
        code(
            "with SleeperClient() as client:\n"
            "    players = client.get_players()\n\n"
            'players["4046"]'
        ),
        md(
            "Because this payload is large and Sleeper asks callers not to "
            "re-fetch it often, `get_players()` caches it to "
            "`<cache_dir>/sleeper_players_nfl.json` and only re-requests it "
            "once the cache is older than `players_ttl_hours` (24 hours by "
            "default). Pass `force_refresh=True` to bypass the cache.\n\n"
            "Pass `position=` and/or `active=` to filter server-side and skip "
            "the full payload instead:"
        ),
        code(
            "with SleeperClient() as client:\n"
            '    active_qbs = client.get_players(position="QB", active=True)\n\n'
            "len(active_qbs), len(players)"
        ),
        md(
            "A filtered call always hits the network; the disk cache is "
            "specifically for the full unfiltered map."
        ),
        md(
            "## Trending players\n\n"
            "`get_trending` returns the most-added or most-dropped players "
            "league-wide, right now -- but only ever a bare `player_id`. "
            "Resolve names yourself against the player map:"
        ),
        code(
            "with SleeperClient() as client:\n"
            '    trending = client.get_trending(kind="add", lookback_hours=24, limit=5)\n'
            "    players = client.get_players()\n\n"
            "for row in trending:\n"
            '    p = players.get(row["player_id"], {})\n'
            "    name = (\n"
            '        p.get("full_name")\n'
            "        or f\"{p.get('first_name', '')} {p.get('last_name', '')}\".strip()\n"
            '        or row["player_id"]\n'
            "    )\n"
            "    print(f\"{name:30s} count={row['count']}\")"
        ),
        md(
            "Note the `first_name`/`last_name` fallback: a team defense has "
            "no `full_name` field in Sleeper's real payload, only the split "
            "fields -- if today's trending list includes one, it still "
            "resolves to a readable name above rather than crashing or "
            "printing a blank."
        ),
        md(
            "## Handling errors\n\n"
            "A failed request raises a typed exception rather than a raw "
            "`requests` exception, so you can catch Sleeper-specific "
            "failures without also catching unrelated bugs: "
            "`SleeperHTTPError` for a non-retryable HTTP status (or once "
            "retries are exhausted on a 429 / 5xx), and "
            "`SleeperResponseError` if a response isn't the shape expected "
            "-- Sleeper returns a bare `null` for an unknown league or draft "
            "ID, for example, rather than a 404. Both subclass "
            "`SleeperAPIError` if you want to catch either:"
        ),
        code(
            "from nuclearff.exceptions import SleeperAPIError\n\n"
            "with SleeperClient() as client:\n"
            "    try:\n"
            '        client.get_league("not-a-real-league-id")\n'
            "    except SleeperAPIError as exc:\n"
            '        print(f"Sleeper call failed: {exc}")'
        ),
        md(
            teaching(
                "catching `SleeperAPIError` specifically -- instead of a "
                "bare `except Exception` -- matters the first time you write "
                "a script that loops over a list of league IDs from a file "
                "someone else typed by hand. A typo'd league ID should stop "
                "that one league and keep going; a real bug in your own code "
                "(a `KeyError` from assuming a field exists) should still "
                "crash loudly so you notice it. A blanket `except Exception` "
                "hides both the same way."
            )
        ),
        md(
            whats_next(
                "Everything above uses `SleeperClient` directly for one-off, "
                "interactive lookups. `04_capturing_a_league.ipynb` builds on "
                "the exact same client to capture a *complete*, reproducible "
                "league dataset -- a snapshot, full multi-season history, "
                "standings, matchups, transactions, and roster composition, "
                "all persisted so the rest of this tutorial can query them "
                "without hitting Sleeper again.\n\n"
                "## See Also\n\n"
                "- `02_configuration.ipynb` -- `LeagueConfig`, built from "
                "`get_league` above.\n"
                "- [Sleeper API Tutorial](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/03_sleeper_api.html) -- the matching "
                "docs page."
            )
        ),
    ]
)

# ==========================================================================
# 04_capturing_a_league.ipynb
# ==========================================================================

ch04 = nb(
    [
        md(
            "# 4. Capturing and Storing a League\n\n"
            f"{NAV_HEADER} Run `03_sleeper_api.ipynb` first if you haven't.\n\n"
            "`03_sleeper_api.ipynb` called `SleeperClient` directly for "
            "one-off lookups. This notebook builds a *complete, "
            "reproducible* dataset for a league: an immutable raw snapshot, "
            "full multi-season history, standings and playoff results, "
            "weekly matchups, transactions, and roster composition -- all "
            "persisted to disk so the rest of this tutorial (and your own "
            "analysis) can query them without hitting Sleeper again. Mirrors "
            "the [Capturing and Storing a League](https://nolmacdonald.github.io/"
            "nuclearff/tutorial/04_capturing_a_league.html) docs page.\n\n"
            "Everything below writes into one shared DuckDB database "
            "(`<cache_dir>/nuclearff.duckdb`) and one raw-snapshot directory. "
            "This is the notebook every later chapter in this series builds "
            "on -- run it before any chapter from here on."
        ),
        md(
            "## A league snapshot\n\n"
            "`fetch_league_snapshot` fetches every read-only endpoint that "
            "describes a league -- settings, users, rosters, drafts -- into "
            "one `LeagueSnapshot`, and `write_snapshot` writes it to an "
            "immutable, timestamped directory (never overwritten -- a second "
            "run creates a new timestamp):"
        ),
        code(
            "from nuclearff.sleeper import SleeperClient, fetch_league_snapshot, write_snapshot\n\n"
            f'league_id = "{REDRAFT}"\n'
            'raw_dir = "./demo/data/raw"\n\n'
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    snapshot = fetch_league_snapshot(client, league_id)\n"
            "    target = write_snapshot(snapshot, raw_dir)\n\n"
            'print(f"League:   {snapshot.league_name} ({snapshot.metadata.league_id})")\n'
            "print(f\"Season:   {snapshot.league.get('season')} status={snapshot.league.get('status')}\")\n"
            "print(f\"Teams:    {snapshot.league.get('total_rosters')}\")\n"
            'print(f"Snapshot: {target}")'
        ),
        md(
            "A snapshot also carries `anomalies` -- league settings that are "
            "internally inconsistent or otherwise worth a second look before "
            "trusting them for valuation, from `detect_anomalies`:"
        ),
        code(
            "for anomaly in snapshot.anomalies:\n"
            '    print(f"[{anomaly.severity.upper()}] {anomaly.code}: {anomaly.message}")\n'
            '    print(f"  -> {anomaly.action}")'
        ),
        md(
            "Real, contradictory settings like a stale `draft_rounds` field "
            "or a keeper cap set on a nominally-redraft league are exactly "
            "why `fetch_league_snapshot` surfaces them up front rather than "
            "letting a downstream valuation quietly use the wrong number."
        ),
        md(
            teaching(
                "it's tempting to skip reading `anomalies` when the snapshot "
                'call otherwise "worked." Don\'t -- a WARNING-severity '
                "anomaly here is exactly the kind of thing that silently "
                "wrecks a valuation three chapters later (e.g. drafting "
                "against `draft_rounds=3` when the real draft ran 15 rounds) "
                "with no exception anywhere to point at the cause. Read this "
                "list every time you capture a league you haven't seen "
                "before."
            )
        ),
        md(
            "## Multi-season history\n\n"
            "Sleeper links a league to its prior season via "
            "`previous_league_id`. `walk_league_chain` walks that chain all "
            "the way back, and `write_league_tables` persists it to DuckDB:"
        ),
        code(
            "from nuclearff.sleeper import walk_league_chain, write_league_tables\n\n"
            'db_path = "./demo/data/cache/nuclearff.duckdb"\n\n'
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    leagues = walk_league_chain(client, league_id, max_seasons=20)\n"
            "    raw_count, config_count = write_league_tables(leagues, db_path)\n\n"
            'print(f"History:  {len(leagues)} season(s) walked")\n'
            'print(f"Configs:  {config_count}/{raw_count} parsed into LeagueConfig")'
        ),
        md(
            "For this league, that reaches all the way back to its 2021 "
            "inception -- six real seasons, not a synthetic sample. Two "
            "tables land in DuckDB: `sleeper_leagues` (the raw "
            "settings/scoring/roster JSON per season) and "
            "`sleeper_league_configs` (the same data, typed and flattened "
            "via `LeagueConfig`). A season that fails to parse keeps its raw "
            "row and just skips the typed one, rather than losing the whole "
            "hop; the walk itself stops cleanly at the end of the chain, not "
            "with an error.\n\n"
            "Every function below takes that same `leagues` list -- resolve "
            "the chain once, then fetch as many kinds of data against it as "
            "you want."
        ),
        md(
            "## Standings and playoff results\n\n"
            "`fetch_and_write_standings` computes, per season, each roster's "
            "regular-season record *and* where they actually finished after "
            "the playoffs:"
        ),
        code(
            "from nuclearff.sleeper import fetch_and_write_standings\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    standings_count, matches_count = fetch_and_write_standings(client, leagues, db_path)\n\n"
            'print(f"Standings: {standings_count} roster-season(s)")\n'
            'print(f"Playoffs:  {matches_count} bracket match(es)")'
        ),
        md(
            "What makes this worth having is that regular-season record and "
            "final standing are genuinely different things. Query both "
            "columns for this league's completed 2025 season:"
        ),
        code(
            "import duckdb\n\n"
            "with duckdb.connect(db_path, read_only=True) as conn:\n"
            "    season_2025_rows = conn.execute(\n"
            '        """\n'
            "        SELECT display_name, wins, losses, regular_season_rank, final_rank\n"
            "        FROM sleeper_standings\n"
            f"        WHERE league_id = '{PLAYOFF_LEAGUE}'\n"
            "        ORDER BY COALESCE(final_rank, 999)\n"
            '        """\n'
            "    ).fetchall()\n"
            "season_2025_rows"
        ),
        md(
            "In this league's real 2025 season, the team with the best "
            "regular-season record finished **4th** -- the eventual champion "
            "had fewer wins and won it in the playoffs.\n\n"
            "Final placement is deliberately conservative: it's computed "
            "only from winners-bracket matches that carry a `p` (placement) "
            "field via `resolve_final_ranks` -- a match's winner gets rank "
            "`p`, its loser `p + 1`. The losers bracket's own placement field "
            "is captured raw in `sleeper_playoff_matches` but never turned "
            "into a guessed `final_rank`, because its numbering convention "
            "couldn't be confirmed against real data -- rosters that only "
            "reached the losers bracket show `final_rank = NULL` rather than "
            'a number that might be wrong. (A Sleeper "Chopped" league has '
            "no playoff bracket at all; `is_chopped_league` and "
            "`resolve_chopped_final_ranks` handle that case from the "
            "elimination order instead.)"
        ),
        md(
            teaching(
                "notice the `with duckdb.connect(...) as conn:` block above "
                "closes `conn` again as soon as the query finishes, rather "
                "than keeping one connection open for the rest of the "
                "notebook. That's deliberate, not just tidiness -- a "
                "read-only connection left open would block the next "
                "`fetch_and_write_*` write cell below from opening its own "
                "connection to the same file. Reconnect fresh before every "
                "query in a notebook like this one, rather than reusing a "
                "connection across cells that also write."
            )
        ),
        md(
            "## Weekly matchups\n\n"
            "`fetch_and_write_matchups` fetches every week's "
            "roster-vs-roster scoring, for every season in the chain:"
        ),
        code(
            "from nuclearff.sleeper import fetch_and_write_matchups\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    matchup_count = fetch_and_write_matchups(client, leagues, db_path, max_week=3)\n\n"
            'print(f"Matchups: {matchup_count} roster-week row(s)")'
        ),
        md(
            "Each row in `sleeper_matchups` carries points, the matchup id "
            "pairing two rosters together, and JSON columns for "
            "`players`/`starters`/`players_points` -- a full weekly box score "
            "per roster. `max_week` caps how many weeks are fetched per "
            "season (default 18, a full regular + postseason); weeks that "
            "haven't happened yet return no data and are silently skipped, "
            "not treated as an error."
        ),
        md(
            "## Transaction history\n\n"
            "`fetch_and_write_transactions` fetches every week's adds, "
            "drops, waivers, and trades, across the full history chain:"
        ),
        code(
            "from nuclearff.sleeper import fetch_and_write_transactions\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    tx_count, tx_player_count = fetch_and_write_transactions(\n"
            "        client, leagues, db_path, max_week=3\n"
            "    )\n\n"
            'print(f"Transactions: {tx_count}")\n'
            'print(f"Add/drop rows: {tx_player_count}")'
        ),
        md(
            "Sleeper's own `type` and `status` fields are trusted and stored as-is, not re-derived:"
        ),
        code(
            "with duckdb.connect(db_path, read_only=True) as conn:\n"
            "    tx_type_counts = conn.execute(\n"
            '        "SELECT type, COUNT(*) FROM sleeper_transactions GROUP BY type"\n'
            "    ).fetchall()\n"
            "tx_type_counts"
        ),
        md(
            "Two tables land in DuckDB: `sleeper_transactions` (one row per "
            "transaction -- `creator`/`roster_ids`/`consenter_ids` resolved "
            "to display names alongside the raw ids, timestamps parsed from "
            "Sleeper's epoch-millisecond format to real UTC `TIMESTAMP` "
            "values) and `sleeper_transaction_players` (`adds`/`drops` "
            "unnested to one row per player per direction)."
        ),
        md(
            "## Roster composition\n\n"
            "`fetch_and_write_roster_players` categorizes every roster's "
            "players by slot, per season:"
        ),
        code(
            "from nuclearff.sleeper import fetch_and_write_roster_players\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    roster_player_count = fetch_and_write_roster_players(client, leagues, db_path)\n\n"
            'print(f"Roster players: {roster_player_count}")'
        ),
        code(
            "with duckdb.connect(db_path, read_only=True) as conn:\n"
            "    slot_counts = conn.execute(\n"
            '        "SELECT slot, COUNT(*) FROM sleeper_roster_players GROUP BY slot"\n'
            "    ).fetchall()\n"
            "slot_counts"
        ),
        md(
            "Each row is a `(league_id, season, roster_id, player_id)` with "
            "a `slot` of `starter`, `reserve` (IR), `taxi`, or `bench` -- "
            "join it to `sleeper_players` (below) for the player's name and "
            "position. Sleeper fills an empty starting slot with the literal "
            'string `"0"` before a draft; that filler is dropped rather '
            "than stored as a phantom player.\n\n"
            "Every one of these writes replaces or merges its own table's "
            "rows (see `17_querying_and_provenance.ipynb` for the "
            "`merge_table` vs. `replace_table` distinction, and a real bug it "
            "fixed) -- a fresh run is a fresh full snapshot for that league, "
            "not an incremental append. There's no ordering requirement "
            "between the functions above; call the ones you need."
        ),
        md(
            "## Combining flags\n\n"
            "Every fetch function above takes the same `leagues` chain, so "
            "the fastest real workflow is to just call every one you need "
            "back to back, as this notebook has been doing. There's no "
            'single "fetch everything" function in the library layer -- '
            "that convenience wrapper is `18_cli_reference.ipynb`'s "
            "`sleeper fetch-league --history --standings --matchups "
            "--transactions --roster-players` in one shell command instead."
        ),
        md(
            "## Discovering leagues from a username\n\n"
            "Every function above needs an already-known `league_id`. If you "
            "only have a Sleeper username, resolve it first, the same way "
            "`03_sleeper_api.ipynb` did:"
        ),
        code(
            "with SleeperClient() as client:\n"
            '    user = client.get_user("nolmacdonald")\n'
            '    disc_leagues = client.get_user_leagues(user["user_id"], 2026, sport="nfl")\n\n'
            "print(f\"{user['display_name']} ({user['user_id']})\")\n"
            'print(f"{len(disc_leagues)} leagues for 2026")\n'
            "for league in disc_leagues[:3]:\n"
            "    print(f\"  {league['league_id']}  {league['name']}  status={league.get('status')}\")"
        ),
        md(
            "`client.get_user_drafts(user_id, season)` works the same way for drafts instead of leagues."
        ),
        md(
            "## The player map, and filtering server-side\n\n"
            "`write_players_table` pulls Sleeper's full NFL player map into "
            "a `sleeper_players` DuckDB table -- this is the join target for "
            "every `player_id` column above:"
        ),
        code(
            "from nuclearff.sleeper import write_players_table\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    players = client.get_players()\n\n"
            "count = write_players_table(players, db_path)\n"
            'print(f"Players: {count}")'
        ),
        md(
            "As covered in `03_sleeper_api.ipynb`, filter server-side with "
            "`get_players(position=..., active=...)` instead of fetching "
            "everything when you only need part of the map."
        ),
        md(
            "## Cross-referencing to nflverse\n\n"
            "Sleeper player objects already carry several cross-platform "
            "IDs, including `gsis_id` -- nflverse's own primary key -- but "
            "not every player has one. `nuclearff.ids.crosswalk` fills the "
            "gap from nflverse's own ID crosswalk:"
        ),
        code(
            "from nuclearff.ids import (\n"
            "    ambiguous_sleeper_ids,\n"
            "    read_sleeper_players,\n"
            "    resolve_missing_gsis_ids,\n"
            "    write_player_id_map,\n"
            ")\n"
            "from nuclearff.nflverse import load_ff_playerids\n\n"
            "players_df = read_sleeper_players(db_path)\n"
            "ff_ids = load_ff_playerids()\n\n"
            "resolved = resolve_missing_gsis_ids(players_df, ff_ids)\n"
            "n_written = write_player_id_map(resolved, db_path)\n\n"
            'print(resolved["gsis_id_source"].value_counts())\n'
            'print(f"Skipped {ambiguous_sleeper_ids(ff_ids).height} ambiguous crosswalk row(s).")'
        ),
        md(
            "`read_sleeper_players` requires `write_players_table` to have "
            "run first. The result -- written by `write_player_id_map` to a "
            "`player_id_map` table -- records each player's best-known "
            "`gsis_id` and where it came from: Sleeper's own field, or the "
            "crosswalk. A crosswalk row whose `sleeper_id` maps to more than "
            "one player is skipped rather than guessed "
            "(`ambiguous_sleeper_ids`); most of what's still unresolved is "
            "players missing from this year's crosswalk entirely, typically "
            "rookies."
        ),
        md(
            "## Querying what you've built\n\n"
            "Every table above lives in one DuckDB file:"
        ),
        code(
            "with duckdb.connect(db_path, read_only=True) as conn:\n"
            '    conn.sql("SHOW TABLES").show()'
        ),
        md(
            "(the full set, once every function on this page has been "
            "called at least once -- your own database only has tables for "
            "what you've actually run). Every table is keyed by `league_id` "
            '(and usually `season`), so joining across them -- "which '
            'player did the eventual champion trade for" or "how many '
            'waiver claims did the team with the worst record make" -- is '
            "ordinary SQL, not custom Python per question. "
            "`17_querying_and_provenance.ipynb` covers this in more depth, "
            "alongside recording *how* a dataset was built."
        ),
        md(
            whats_next(
                "With a real, persisted league dataset in place, "
                "`05_scoring_engine.ipynb` starts turning real NFL statistics "
                "into fantasy points under this league's own rules -- the "
                "foundation everything in Chapters 6 through 12 builds on.\n\n"
                "## See Also\n\n"
                "- `03_sleeper_api.ipynb` -- the client this notebook builds "
                "on.\n"
                "- [Capturing and Storing a League](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/04_capturing_a_league.html) -- the "
                "matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 05_scoring_engine.ipynb
# ==========================================================================

ch05 = nb(
    [
        md(
            "# 5. The Scoring Engine\n\n"
            f"{NAV_HEADER} Run `04_capturing_a_league.ipynb` first.\n\n"
            "`nflreadpy`'s own `fantasy_points` column (`01_introduction.ipynb`) "
            "uses one generic scoring formula. Real leagues don't all score "
            "the same way -- this league gives a full point per reception "
            "and 6 points per passing touchdown; another league might run "
            "half-PPR and 4-point passing touchdowns. `ScoringEngine` closes "
            "that gap: it applies *this league's own* `ScoringSettings` "
            "(`02_configuration.ipynb`) to real nflverse stat lines. Mirrors "
            "the [Scoring Engine](https://nolmacdonald.github.io/nuclearff/"
            "tutorial/05_scoring_engine.html) docs page."
        ),
        md(
            "## Scoring a single stat line\n\n"
            "`score_stat_line` is the reference, hand-verifiable path -- "
            "pure Python over one dict of nflverse column names:"
        ),
        code(
            "from nuclearff.config import load_league_config\n"
            "from nuclearff.scoring import ScoringEngine\n\n"
            f'league_cfg = load_league_config("./demo/configs/leagues/{REDRAFT}.yaml")\n'
            "engine = ScoringEngine(league_cfg.scoring)\n\n"
            'line = {"receptions": 8, "receiving_yards": 100, "receiving_tds": 1, "position": "WR"}\n'
            "engine.score_stat_line(line)"
        ),
        md(
            "A stat the line doesn't include is treated as zero -- you don't "
            "need to pad out every possible key, just the ones a real player "
            'recorded. Pass `"position"` to get position-conditional '
            "reception bonuses (`bonus_rec_wr`/`bonus_rec_te`/`bonus_rec_rb`) "
            "-- without it, those bonuses contribute nothing."
        ),
        md(
            "## Scoring a real season, vectorized\n\n"
            "`score_frame` is the vectorized equivalent, built for real "
            "multi-row data straight out of `nflreadpy`:"
        ),
        code(
            "from nuclearff.nflverse import load_seasonal_skill_stats\n\n"
            "stats = load_seasonal_skill_stats([2024])\n"
            "scored = engine.score_frame(stats)\n\n"
            'scored.select(["player_display_name", "position", "season", "fantasy_points"]).sort(\n'
            '    "fantasy_points", descending=True\n'
            ").head(6)"
        ),
        md(
            "That's this league's real 6-point passing touchdowns and "
            "full-PPR scoring showing up immediately: quarterbacks dominate "
            "the top of the 2024 season under these rules in a way a "
            "half-PPR, 4-point-passing-touchdown league would not produce."
        ),
        md(
            teaching(
                "it's a common mistake to reach for a league's `scoring` "
                "settings dict directly and hand-roll the math yourself for "
                '"just one quick check." `ScoringEngine` exists precisely '
                "because that math has more edge cases than it looks like -- "
                "position-conditional bonuses, unscored keys (below), and "
                "keeping every downstream chapter (projections, valuation, "
                "backtesting) using the *exact same* scoring logic instead of "
                "each reimplementing a slightly different version."
            )
        ),
        md(
            "## Unscored keys\n\n"
            "A league's `scoring_settings` payload usually includes keys "
            "`ScoringEngine` has no nflverse column for -- kicker, "
            "defense/special teams, and IDP (individual defensive player) "
            "keys, none of which appear in standard offensive box scores. "
            "Constructing the engine logs a warning listing every nonzero "
            "key it can't account for; `unscored_keys()` returns the same "
            "list programmatically:"
        ),
        code(
            "unscored = engine.unscored_keys()\n"
            'print(len(unscored), "unscored keys, e.g.:", sorted(unscored)[:5])'
        ),
        md(
            "This is expected, not a bug to fix -- recall from "
            "`04_capturing_a_league.ipynb` that this exact league's own "
            "anomaly detection flagged `no_kicker_or_defense`: it has no "
            "kicker, defense, or IDP roster slots at all, so those unscored "
            "keys in the payload are inert regardless. A league that *does* "
            "roster a kicker or defense would need its own K/DEF stat source "
            "joined in before those keys mean anything -- `ScoringEngine` "
            "only scores whatever stat columns you hand it."
        ),
        md(
            whats_next(
                "Raw fantasy points are one number per player-season. "
                "`06_metrics.ipynb` computes the underlying opportunity and "
                "efficiency signals -- target share, air yards, yards per "
                "route run -- that explain *why* a receiver scored what they "
                "did, and which of it is likely to repeat.\n\n"
                "## See Also\n\n"
                "- `02_configuration.ipynb` -- `LeagueConfig`/`ScoringSettings`, "
                "the input `ScoringEngine` wraps.\n"
                "- [Scoring Engine](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/05_scoring_engine.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 06_metrics.ipynb
# ==========================================================================

ch06 = nb(
    [
        md(
            "# 6. Opportunity and Efficiency Metrics\n\n"
            f"{NAV_HEADER} Run `05_scoring_engine.ipynb` first.\n\n"
            "Raw fantasy points (`05_scoring_engine.ipynb`) tell you what "
            "happened. `nuclearff.metrics` computes the underlying signals "
            "that explain *why*, and -- more importantly for valuation -- "
            "which of it tends to repeat season over season. These metrics "
            "are wide-receiver-focused (the plan's original scope), split "
            "into three groups: opportunity **volume**, route-participation "
            "**efficiency**, and **touchdown** regression. Mirrors the "
            "[Metrics](https://nolmacdonald.github.io/nuclearff/tutorial/"
            "06_metrics.html) docs page."
        ),
        md(
            "## Volume: target share, air yards, and WOPR\n\n"
            "`target_share`, `air_yards_share`, and `wopr` all require "
            '**weekly-grain** data -- "team" is only unambiguous week to '
            "week, since a traded player has a different team in different "
            "weeks. Passing seasonal data raises `ValueError` rather than "
            "silently dividing by the wrong team total. `racr` and `adot` "
            "are simple per-row ratios and accept either grain."
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.metrics import adot, air_yards_share, racr, target_share, wopr\n"
            "from nuclearff.nflverse import load_weekly_receiving\n\n"
            "weekly = load_weekly_receiving([2024])\n"
            "df = target_share(weekly)\n"
            "df = air_yards_share(df)\n"
            "df = wopr(df)\n"
            "df = racr(df)\n"
            "df = adot(df)\n\n"
            'df.group_by(["player_display_name", "position"]).agg(\n'
            "    [\n"
            '        pl.col("target_share").mean(),\n'
            '        pl.col("air_yards_share").mean(),\n'
            '        pl.col("wopr").mean(),\n'
            '        pl.col("racr").mean(),\n'
            '        pl.col("adot").mean(),\n'
            "    ]\n"
            ').sort("wopr", descending=True).head(6)'
        ),
        md(
            "`WOPR` (Weighted Opportunity Rating, Hermsmeyer's formula: "
            "`1.5 * target_share + 0.7 * air_yards_share`) is the single "
            "best-fitting volume composite -- a receiver can lead 2024 WOPR "
            "with a lower target share than another player, purely because "
            "their share of their team's *air yards* is higher."
        ),
        md(
            "**Every volume function above recomputes its metric from raw "
            "columns rather than trusting nflreadpy's own precomputed ones**, "
            "then cross-checks the two and logs a warning on real "
            "disagreement. Confirmed on live 2024 data: the recomputed "
            "`target_share` matches nflreadpy's own column almost exactly, "
            "but `air_yards_share` disagrees on roughly 7% of rows -- "
            "nflreadpy's own denominator appears to draw from a richer "
            "play-by-play source than this project's per-player stats table "
            "captures. That's the cross-check doing its job, not a bug: "
            "trust the warning as a real data-quality signal, not noise to "
            "silence."
        ),
        md(
            teaching(
                "a 7% disagreement sounds alarming until you remember what "
                "the cross-check is actually for: it exists so *you* notice "
                "a real, small, explainable gap between two independently "
                "computed numbers, rather than silently trusting whichever "
                "one happened to be computed first. If you ever see this "
                "warning fire on a metric you didn't expect, that's the "
                "signal to go check the two source columns by hand before "
                "trusting either one downstream -- not to suppress the "
                "warning."
            )
        ),
        md(
            "## Efficiency: yards and targets per route run\n\n"
            "Target share doesn't say how *hard* a receiver had to work for "
            "those targets. `yprr` (yards per route run) and `tprr` "
            "(targets per route run) need routes-run data, which isn't in "
            "the same table as receiving stats -- join them first with "
            "`join_routes`, which bridges the two via each player's "
            "`pfr_id`/`gsis_id`:"
        ),
        code(
            "from nuclearff.metrics import join_routes, tprr, yprr\n"
            "from nuclearff.nflverse import load_players, load_routes, load_seasonal_receiving\n\n"
            "receiving = load_seasonal_receiving([2024])\n"
            "routes = load_routes([2024])\n"
            "players = load_players()\n\n"
            "joined = join_routes(receiving, routes, players)\n"
            "df = tprr(yprr(joined))"
        ),
        md(
            "Both are **sample-gated**: below `min_routes` (200 by default), the result is `null` rather than a noisy small-sample number:"
        ),
        code(
            'qualified = df.filter(pl.col("yprr").is_not_null())\n'
            'print(f"{qualified.height} of {df.height} players ran >= 200 routes")\n\n'
            'qualified.sort("yprr", descending=True).select(\n'
            '    ["player_display_name", "position", "routes_run", "yprr", "tprr"]\n'
            ").head(6)"
        ),
        md(
            "`routes_run` is honestly labeled by source (`routes_source` on "
            "the joined frame) -- nflverse doesn't publish a direct "
            "routes-run column for every season, so this is an approximation "
            "in some cases, not a guarantee of exactness. "
            "`ambiguous_player_id_pairs` mirrors `04_capturing_a_league.ipynb`'s "
            "Sleeper crosswalk check for this join's own `pfr_id`/`gsis_id` "
            "bridge."
        ),
        md(
            "## Touchdowns: expected TDs and regression\n\n"
            "Touchdowns are the least stable part of a receiver's fantasy "
            "output year to year -- a receiver can lead the league in "
            "expected scoring opportunities and still finish with a "
            "below-average touchdown total, purely from variance. "
            "`expected_tds` joins ffverse's own expected-fantasy-opportunity "
            "model onto real receiving stats and adds `td_regression` (real "
            "minus expected):"
        ),
        code(
            "from nuclearff.metrics import expected_tds\n"
            "from nuclearff.nflverse import load_ff_opportunity, load_seasonal_receiving\n\n"
            "receiving = load_seasonal_receiving([2024])\n"
            "opportunity = load_ff_opportunity([2024])\n"
            "df = expected_tds(receiving, opportunity)\n\n"
            'df.filter(pl.col("expected_tds").is_not_null()).sort("td_regression").select(\n'
            '    ["player_display_name", "position", "receiving_tds", "expected_tds", "td_regression"]\n'
            ").head(10)"
        ),
        md(
            'Negative `td_regression` is the "TD-unlucky" list -- a '
            "player who scored fewer touchdowns than their real "
            "opportunities predicted, which is exactly the kind of gap a "
            "projection should expect to *close* next season, not repeat. "
            'Sort descending instead for the "TD-lucky" list -- players '
            "whose touchdown total is more likely to regress down. A player "
            "with no matching opportunity data gets `expected_tds = null` "
            "rather than being dropped or raising."
        ),
        md(
            whats_next(
                "Volume, efficiency, and touchdown regression are all "
                "inputs, not a finished number. `07_projections.ipynb` "
                "blends real historical seasons -- weighted toward the two "
                "metrics above that repeat, and away from touchdown luck -- "
                "into a single forward-looking estimate.\n\n"
                "## See Also\n\n"
                "- `05_scoring_engine.ipynb` -- turns these same stats into "
                "fantasy points instead of opportunity signals.\n"
                "- [Metrics](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/06_metrics.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 07_projections.ipynb
# ==========================================================================

ch07 = nb(
    [
        md(
            "# 7. Projections\n\n"
            f"{NAV_HEADER} Run `06_metrics.ipynb` first.\n\n"
            "`06_metrics.ipynb` computes signals for one season at a time. "
            "A projection blends **several** real seasons into a single "
            "forward-looking estimate -- weighting recent seasons more "
            "heavily, adjusting for age, and letting you hand-apply context "
            "a model can't see on its own (a coaching change, a depth-chart "
            "note). `nuclearff.projection.blend` does each of these as its "
            "own composable step, plus one function that orchestrates all of "
            "them. Mirrors the [Projections](https://nolmacdonald.github.io/"
            "nuclearff/tutorial/07_projections.html) docs page.\n\n"
            "This is explicitly a **recency-weighted average of real, "
            "realized stats** -- not a machine-learning forecast. It "
            "carries no injury news, no depth-chart change, and a rookie "
            "with no prior season gets no projection at all. See "
            "`09_auction_draft_board.ipynb` for where this caveat matters "
            "most in practice."
        ),
        md(
            "## Recency-weighted rate\n\n"
            "`recency_weighted_rate` takes multi-season data and blends one "
            "rate column across the seasons before a target season, using "
            "`RecencyWeights` (0.5 / 0.3 / 0.2 over the three most recent "
            "seasons, by default). Below, blending real 2022-2024 target "
            "share into a 2025 estimate:"
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.config.models import ModelConfig, RecencyWeights\n"
            "from nuclearff.metrics import target_share\n"
            "from nuclearff.nflverse import load_players, load_weekly_receiving\n"
            "from nuclearff.projection import blend_projection, recency_weighted_rate\n\n"
            "weekly = target_share(load_weekly_receiving([2022, 2023, 2024]))\n"
            'seasonal = weekly.group_by(["player_id", "player_display_name", "season"]).agg(\n'
            '    pl.col("target_share").mean().alias("target_share"),\n'
            '    pl.col("week").n_unique().alias("games"),\n'
            ")\n\n"
            'blended = recency_weighted_rate(seasonal, "target_share", 2025, RecencyWeights())'
        ),
        md(
            "The log line this prints is `SampleThresholds` at work -- a "
            "real 2024 backup with two garbage-time targets doesn't get a "
            "rate blended in at full weight, or at all, if it never crosses "
            "`min_games`."
        ),
        md(
            "## Age curve and hand-authored context\n\n"
            "`apply_age_curve` multiplies a projection by an age-based "
            "factor -- flat through `plateau_end` (28 by default), then "
            "declining, floored at `min_factor`. It reads real age from "
            "`players`'s `birth_date` column, joined by `gsis_id`:"
        ),
        code(
            "from nuclearff.config.models import AgeCurveConfig\n"
            "from nuclearff.projection import apply_age_curve\n\n"
            "players = load_players()\n"
            "aged = apply_age_curve(\n"
            '    blended.rename({"target_share_blended": "proj"}), players, 2025, AgeCurveConfig(), "proj"\n'
            ")"
        ),
        md(
            "`apply_context_deltas` applies a hand-authored "
            "`{player_id: delta}` multiplier or additive adjustment on top "
            "-- a way to fold in a real depth-chart change or coaching hire "
            "the statistical blend has no way to see, without hiding it "
            "inside opaque model weights. A `player_id` in the dict but not "
            "found in `df` logs a warning rather than failing silently "
            "(catches a typo in a hand-written deltas file)."
        ),
        md(
            "## Orchestrating the full blend\n\n"
            "`blend_projection` runs all of the above in one call -- "
            "recency weighting, the age curve, and any context deltas:"
        ),
        code(
            "model_cfg = ModelConfig()\n"
            'blended = blend_projection(seasonal, players, 2025, ["target_share"], model_cfg)\n\n'
            'names = seasonal.select(["player_id", "player_display_name"]).unique(subset="player_id")\n'
            "result = (\n"
            '    blended.join(names, on="player_id", how="left")\n'
            '    .filter(pl.col("target_share_blended").is_not_null())\n'
            '    .sort("target_share_blended", descending=True)\n'
            ")\n"
            'result.select(["player_display_name", "target_share_blended", "age_factor"]).head(8)'
        ),
        md(
            "Real `age_factor` values below 1.0 start appearing exactly "
            "where you'd expect -- a receiver well past `plateau_end` is "
            "projected below their blended rate before any other "
            "adjustment. Everyone at or before `plateau_end` gets exactly "
            "`1.0`, not a gradual pre-peak ramp; the curve models decline, "
            "not development."
        ),
        md(
            "## Games played\n\n"
            "A rate projection (target share, yards per route run) says "
            "nothing about how many games a player will actually play. "
            "`project_games_played` recency-weights real games played the "
            "same way, capped at `max_games` (17):"
        ),
        code(
            "from nuclearff.projection import project_games_played\n\n"
            "games = project_games_played(seasonal, 2025, RecencyWeights())\n"
            'games.join(names, on="player_id", how="left").sort("projected_games", descending=True).head(3)'
        ),
        md(
            "Multiplying a rate projection by projected games (and, for "
            "touchdowns, folding in the regression from `06_metrics.ipynb`) "
            "is how a rate becomes a seasonal point total -- exactly what "
            "`09_auction_draft_board.ipynb`'s `project_points` does for "
            "realized fantasy points instead of target share."
        ),
        md(
            teaching(
                'the note above about "no injury news, no depth-chart '
                "change\" is easy to skim past. It's the single most "
                "important caveat in this whole tutorial: this projection "
                "model will confidently produce a number for a player who "
                "just suffered a season-ending injury, because it has no "
                "mechanism to know that happened. `apply_context_deltas` is "
                "the escape hatch -- use it, don't trust the blended rate "
                "alone for anyone whose situation has materially changed "
                "since the seasons being blended."
            )
        ),
        md(
            whats_next(
                "A single-number projection hides how much uncertainty is "
                "really there. `11_simulation.ipynb` turns a mean and a "
                "spread into a full range of plausible outcomes. First, "
                "though, `08_valuation.ipynb` turns *any* projection -- this "
                "chapter's target share, or realized fantasy points -- into "
                "a draft-ready value: replacement level, VORP, and discrete "
                "tiers.\n\n"
                "## See Also\n\n"
                "- `06_metrics.ipynb` -- the per-season signals this chapter "
                "blends across seasons.\n"
                "- [Projections](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/07_projections.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 08_valuation.ipynb
# ==========================================================================

ch08 = nb(
    [
        md(
            "# 8. Replacement Level, VORP, and Draft Tiers\n\n"
            f"{NAV_HEADER} Run `07_projections.ipynb` first.\n\n"
            "A projection (`07_projections.ipynb`) ranks players against "
            "each other. It says nothing about *where the bench begins* -- "
            "the point past which two players are interchangeable because "
            "either is a free-agent-caliber replacement anyway. "
            "`nuclearff.valuation` answers that with value-based drafting: "
            "replacement level, VORP, VONA, and discrete draft tiers. "
            "Mirrors the [Valuation](https://nolmacdonald.github.io/nuclearff/"
            "tutorial/08_valuation.html) docs page."
        ),
        md(
            "## Scoring and projecting a real board\n\n"
            "Everything below needs a projected-points table first. Reuse "
            "the `ScoringEngine` from `05_scoring_engine.ipynb` and two "
            "wiring functions `09_auction_draft_board.ipynb` covers in full "
            "-- `score_seasons` (this league's own rules over real seasonal "
            "stats) and `project_points` (recency-weighted realized points, "
            "the same idea as `07_projections.ipynb`'s `blend_projection` "
            "but operating on whole fantasy-point totals instead of one "
            "rate column):"
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.config import load_league_config\n"
            "from nuclearff.nflverse import load_seasonal_skill_stats\n"
            "from nuclearff.pipeline import project_points, score_seasons\n"
            "from nuclearff.scoring import ScoringEngine\n\n"
            f'league_cfg = load_league_config("./demo/configs/leagues/{REDRAFT}.yaml")\n'
            "engine = ScoringEngine(league_cfg.scoring)\n\n"
            "stats = load_seasonal_skill_stats([2023, 2024, 2025])\n"
            "scored = score_seasons(stats, engine)\n"
            "projections = project_points(scored, 2026)"
        ),
        md(
            "`projections` now has one row per player with a "
            "`value_estimate` column (the recency-weighted points estimate) "
            "plus identity columns."
        ),
        md(
            "## Replacement level\n\n"
            "`replacement_points` returns the projected points of the "
            "replacement-level player at a position, under one of two named "
            "baselines:"
        ),
        code(
            "from nuclearff.valuation import replacement_points\n\n"
            'vols = replacement_points(projections, league_cfg, position="WR", baseline="vols", proj_column="value_estimate")\n'
            'vorp_baseline = replacement_points(projections, league_cfg, position="WR", baseline="vorp", proj_column="value_estimate")\n'
            'print(f"WR replacement level (vols): {vols:.1f}")\n'
            'print(f"WR replacement level (vorp): {vorp_baseline:.1f}")'
        ),
        md(
            "Recall from `02_configuration.ipynb`: **VOLS** (Value Over "
            "Last Starter) uses the league's actual starter demand as the "
            "replacement rank; **VORP** (Value Over Replacement Player) goes "
            "deeper into the bench on the assumption that real waiver-wire "
            "depth, not the last nominal starter, is where a player "
            'actually becomes replaceable. Neither is more "correct" -- '
            "they're two different, named assumptions, and VORP's "
            "bench-stash fraction is explicitly an uncalibrated heuristic "
            "(see `LeagueConfig.replacement_rank`'s own docstring)."
        ),
        md(
            "## VORP\n\n`vorp` adds a `vorp` column: each player's value estimate minus the replacement level:"
        ),
        code(
            "from nuclearff.valuation import vorp\n\n"
            'valued = vorp(projections, league_cfg, position="WR", baseline="vols", proj_column="value_estimate")\n'
            "top_wr = (\n"
            '    valued.filter(pl.col("position") == "WR")\n'
            '    .sort("vorp", descending=True)\n'
            '    .select(["player_display_name", "value_estimate", "vorp"])\n'
            ")\n"
            "top_wr.head(8)"
        ),
        md(
            "Watch for the real gap in the output: the top handful of "
            "players typically separate from the next group by a wide VORP "
            "margin, while the next several bunch up close together -- "
            "exactly the kind of structure draft tiers (below) are meant to "
            "surface explicitly instead of leaving it implicit in a sorted "
            "list."
        ),
        md(
            "## VONA: value over next available\n\n"
            'Replacement level answers "how much better is this player '
            'than a free-agent floor." `vona` answers a sharper, '
            'in-the-moment question: "how much do I lose if I wait and '
            'take this position at my *next* pick instead?" It needs the '
            "set of players already drafted and the number of picks until "
            "your next turn:"
        ),
        code(
            "from nuclearff.valuation import vona\n\n"
            'wr_pool = valued.filter(pl.col("position") == "WR")\n'
            'drafted_ids = set(wr_pool.sort("vorp", descending=True).head(3)["player_id"])\n\n'
            'with_vona = vona(wr_pool, drafted_ids, next_pick_gap=8, position="WR", proj_column="value_estimate")\n'
            'with_vona.sort("vona", descending=True).select(\n'
            '    ["player_display_name", "value_estimate", "vona"]\n'
            ").head(6)"
        ),
        md(
            "The three already-`drafted_ids` correctly get `vona = null` -- "
            "they're off the board, not a real choice anymore. A high VONA "
            "for the next-best player says: if you pass on them now, the "
            "best WR still around at your next turn (8 picks later) will "
            "cost you that many points of value -- a much sharper signal "
            "than VORP alone for deciding whether to reach."
        ),
        md(
            "## Draft tiers\n\n"
            "A sorted VORP list still asks you to eyeball where one tier of "
            "value ends and the next begins. `assign_tiers` groups a "
            "continuous value column into discrete tiers via k-means, "
            "auto-selecting `k` by silhouette score unless you fix it:"
        ),
        code(
            "from nuclearff.valuation import assign_tiers\n\n"
            'ranked = valued.filter(pl.col("position") == "WR").sort("vorp", descending=True).head(8)\n'
            'tiers = assign_tiers(ranked["vorp"].to_list())\n'
            'list(zip(ranked["player_display_name"], tiers, strict=True))'
        ),
        md(
            "The real top tier separates cleanly from the rest -- a tier "
            "boundary computed from the actual data's clustering, not a "
            'fixed "top 5 / next 5" rule that would split two '
            "closely-valued players arbitrarily."
        ),
        md(
            teaching(
                "it's worth actually testing `assign_tiers` on a "
                "deliberately shuffled input once, the way this project's "
                "own test suite does -- k-means assigns arbitrary numeric "
                'labels to clusters, so "tier 1" isn\'t guaranteed to be the '
                "*highest*-value cluster unless something remaps it "
                "afterward. `assign_tiers` does that remapping for you, but "
                "it's exactly the kind of thing that's easy to get backwards "
                "if you ever reimplement tiering yourself."
            )
        ),
        md(
            whats_next(
                "`09_auction_draft_board.ipynb` puts all of this together "
                "end to end -- including converting VORP into real auction "
                "dollars -- against a league that actually runs an auction "
                "draft, plus renders the full report as CSV, markdown, and "
                "styled PNG tables.\n\n"
                "## See Also\n\n"
                "- `07_projections.ipynb` -- the projection this chapter "
                "turns into a draft value.\n"
                "- [Valuation](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/08_valuation.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 09_auction_draft_board.ipynb
# ==========================================================================

ch09 = nb(
    [
        md(
            "# 9. Building an Auction Draft Board\n\n"
            f"{NAV_HEADER} Run `08_valuation.ipynb` first.\n\n"
            "`08_valuation.ipynb` covered VORP and tiers as standalone "
            "pieces. This notebook wires everything from Chapters 5 through "
            "8 into one pipeline: Sleeper league settings -> real fantasy "
            "points -> a value estimate -> replacement level and VORP -> "
            "auction dollars -> a full report. This is the "
            "`nuclearff.pipeline.auction_board` module, and it's exactly "
            "what produces the auction board CSV, markdown report, and PNG "
            "tables. Mirrors the [Auction Draft Board](https://nolmacdonald.github.io/"
            "nuclearff/tutorial/09_auction_draft_board.html) docs page.\n\n"
            f"`NUCLEARFF REDRAFT` (`{REDRAFT}`), this tutorial's running "
            "example everywhere else, runs a snake draft -- no auction "
            "budget to price against. This notebook uses a different real "
            f"league on the same Sleeper account, `Freeman Forever League` "
            f"(`{AUCTION_LEAGUE}`), whose draft actually is an auction."
        ),
        md(
            "## The wiring, step by step\n\n"
            "`build_auction_board` runs the whole pipeline in one call, but "
            "every step it wires together already exists on its own -- "
            "nothing here reimplements scoring, projection, replacement "
            "level, or the dollar conversion:"
        ),
        code(
            "from nuclearff.pipeline import build_auction_board\n"
            "from nuclearff.sleeper import SleeperClient\n\n"
            f'league_id = "{AUCTION_LEAGUE}"\n'
            'client = SleeperClient(cache_dir="./demo/data/cache")\n\n'
            "board, context = build_auction_board(\n"
            '    league_id, seasons=[2024, 2025], as_of_season=2026, client=client, baseline="vols",\n'
            ")\n\n"
            'print(context["league_name"], context["season"])\n'
            "print(f\"Budget: ${context['budget_per_team']}/team x {context['num_teams']} teams\")\n"
            'print("Players valued:", board.height)\n'
            'print("In draft pool:", board.filter(board["in_draft_pool"]).height)'
        ),
        md(
            "Internally, `build_auction_board` did exactly this:\n\n"
            "1. **League settings and budget** -- `league_config_from_sleeper` "
            "for scoring/roster shape, `budget_from_draft` for the per-team "
            "dollar amount, read straight from the Sleeper draft object.\n"
            "2. **Realized points** -- `score_seasons`, the same "
            "`ScoringEngine` call from `05_scoring_engine.ipynb`, across "
            "every season in `seasons`.\n"
            "3. **A value estimate** -- `project_points`, a "
            "recency-weighted average of those realized seasons "
            "(`07_projections.ipynb`'s idea, applied to whole-season point "
            "totals).\n"
            "4. **Replacement level and VORP, per position** -- "
            "`add_vorp_all_positions`, one call per position instead of "
            "four (`08_valuation.ipynb`).\n"
            "5. **Auction dollars** -- `auction_values` (below).\n"
            "6. **A market comparison** -- FantasyPros consensus rankings, "
            "joined by `gsis_id`.\n\n"
            "`value_estimate` is a **recency-weighted average of what "
            "players actually did**, not a forward projection. It carries "
            "no injury news, no depth-chart change, and a rookie with no "
            "prior season doesn't appear on the board at all -- the same "
            "caveat `07_projections.ipynb` states for `blend_projection`, "
            "because this is the exact same idea applied to realized points "
            "instead of one rate column."
        ),
        md(
            "## Auction dollars\n\n`auction_values` is the dollar conversion on its own -- it distributes the league's spendable budget across the draftable pool in proportion to each player's share of total positive VORP:"
        ),
        code(
            'top = board.sort("auction_value", descending=True).select(\n'
            '    ["player_display_name", "position", "auction_value", "vorp", "rank_overall"]\n'
            ")\n"
            "top.head(6)"
        ),
        md(
            "## Keeper cost adjustment\n\n"
            "Keeper inflation is implemented but not wired into "
            "`build_auction_board` -- Sleeper exposes no keeper-price "
            "endpoint, so keeper costs must come from you. Given a "
            "`{player_id: cost}` mapping, `keeper_inflation_multiplier` "
            "returns the factor every non-kept dollar of value inflates by, "
            "and `keeper_adjusted_values` applies it:"
        ),
        code(
            "from nuclearff.valuation.auction import (\n"
            "    keeper_adjusted_values,\n"
            "    keeper_inflation_multiplier,\n"
            ")\n\n"
            'top3_ids = board.sort("auction_value", descending=True).head(3)["player_id"].to_list()\n'
            "keeper_costs = dict.fromkeys(top3_ids, 5.0)\n\n"
            "multiplier = keeper_inflation_multiplier(board, keeper_costs, teams=10, budget_per_team=200)\n"
            'print(f"Inflation multiplier: {multiplier:.3f}")\n\n'
            "adjusted = keeper_adjusted_values(board, keeper_costs, teams=10, budget_per_team=200)\n"
            'adjusted.sort("auction_value", descending=True).select(\n'
            '    ["player_display_name", "auction_value", "auction_value_keeper_adjusted"]\n'
            ").head(4)"
        ),
        md(
            "Kept players (illustrated here with a made-up $5 keeper cost "
            "for the top 3) show their real kept cost, not their market "
            "value; every *other* player's value inflates by the real "
            "multiplier printed above -- the market value those three "
            "keepers would otherwise have consumed still has to be spent by "
            "the other nine teams, on everyone else."
        ),
        md(
            teaching(
                "the $5 keeper cost above is deliberately made up -- "
                "Sleeper has no keeper-price field to read, so this project "
                "correctly refuses to invent one. If your own league runs "
                "keepers, the real costs (whatever your league's actual "
                'keeper rule computes, e.g. "last auction price + $5") '
                "have to come from you, by hand, the same way this cell "
                "supplies them. That's a deliberate design choice, not a "
                "missing feature -- see the project's own decision notes if "
                "you're curious why."
            )
        ),
        md(
            "## Writing the full report\n\n"
            "`write_report` writes the CSV, one styled PNG table per "
            "position, and a markdown report with a methodology section -- "
            "everything `report auction-board` writes on the command line "
            "(`18_cli_reference.ipynb`), called directly:"
        ),
        code(
            "from nuclearff.report import write_report\n\n"
            'report_path = write_report(board, context, "./demo/data/artifacts", top_n=12)\n'
            "report_path"
        ),
        image(
            "./demo/data/artifacts/tables/top_12_wr.png",
            "Styled auction-board table for the top 12 WRs",
        ),
        md(
            "One of four position tables this call renders (QB, RB, WR, "
            "TE). Pass `render_tables=False` to skip these and avoid the "
            "`plottable`/`matplotlib` dev extra. The markdown report's "
            "methodology section states the same caveats as above in plain "
            "language."
        ),
        md(
            whats_next(
                "An auction board values every player at once. "
                "`10_draft_and_playoff_visuals.ipynb` covers the "
                "complementary picture -- what actually happened in a "
                "specific draft or a specific season's playoffs, rendered "
                "as a grid or a bracket tree.\n\n"
                "## See Also\n\n"
                "- `08_valuation.ipynb` -- VORP and tiers on their own, "
                "before this chapter's pipeline wires them together.\n"
                "- [Auction Draft Board](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/09_auction_draft_board.html) -- the "
                "matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 10_draft_and_playoff_visuals.ipynb
# ==========================================================================

ch10 = nb(
    [
        md(
            "# 10. Draft and Playoff Visualizations\n\n"
            f"{NAV_HEADER} Run `09_auction_draft_board.ipynb` first.\n\n"
            "`04_capturing_a_league.ipynb` persisted draft picks, standings, "
            "and playoff bracket matches. This notebook renders three views "
            "straight from that stored data: a snake-order draft board "
            "grid, a playoff bracket tree, and a manager's draft-order "
            "history table. Mirrors the [Draft and Playoff Visualizations]"
            "(https://nolmacdonald.github.io/nuclearff/tutorial/"
            "10_draft_and_playoff_visuals.html) docs page."
        ),
        md(
            "## Draft board\n\n"
            "`fetch_and_write_draft_picks` fetches (and persists) one "
            "draft's picks; `render_draft_board` renders them as a "
            "snake-order grid of position-colored pick cards -- one column "
            "per draft slot (team), one row per round, matching Sleeper's "
            "own draft-room UI:"
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.duckdb_io import read_table\n"
            "from nuclearff.report import render_draft_board\n"
            "from nuclearff.sleeper import SleeperClient, fetch_and_write_draft_picks\n\n"
            f'league_id = "{REDRAFT}"\n'
            'db_path = "./demo/data/cache/nuclearff.duckdb"\n\n'
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    drafts = client.get_league_drafts(league_id)\n"
            '    draft_id = drafts[0]["draft_id"]\n'
            "    pick_count = fetch_and_write_draft_picks(client, draft_id, db_path)\n"
            "    draft = client.get_draft(draft_id)\n"
            "    users = client.get_users(league_id)\n\n"
            'settings = draft["settings"]\n'
            'teams, rounds = settings["teams"], settings["rounds"]\n'
            'draft_order = draft.get("draft_order") or {}\n'
            'names_by_user = {u["user_id"]: u["display_name"] for u in users}\n'
            "names = {\n"
            '    slot: names_by_user.get(uid) or f"Slot {slot}"\n'
            "    for uid, slot in draft_order.items()\n"
            "    if isinstance(slot, int)\n"
            "}\n\n"
            'picks = read_table(db_path, "sleeper_draft_picks").filter(\n'
            '    pl.col("draft_id") == draft_id\n'
            ").to_dicts()\n\n"
            "written = render_draft_board(\n"
            '    picks, names, "./demo/data/artifacts/draft_board.png",\n'
            '    teams=teams, rounds=rounds, title="NUCLEARFF REDRAFT — Draft Board",\n'
            ")\n"
            'print(f"Picks: {pick_count}")\n'
            'print(f"Draft board: {written}")'
        ),
        image("./demo/data/artifacts/draft_board.png", "Snake-order draft board grid"),
        md(
            "Each cell shows the pick's position and NFL team, the pick "
            "number (e.g. `3.10` -- round 3, draft slot 10), and the "
            "player's name. Cell color follows position: green for RB, blue "
            "for WR, pink for QB, orange for TE -- confirmed against "
            "Sleeper's own draft-room UI; any other position falls back to "
            "a neutral gray.\n\n"
            "A draft's pick order isn't always a simple alternating snake -- "
            'this league\'s real draft has `settings["reversal_round"] == 3`: '
            "round 3 continues round 2's column direction instead of "
            "reversing back to round 1's. The grid doesn't compute pick "
            "order itself; each pick's column is its own real `draft_slot`, "
            "which Sleeper has already resolved correctly, reversal round "
            "included. Rendering an in-progress draft (not every round "
            "complete yet) is the normal case, not an error -- the grid "
            "simply draws however many picks exist so far."
        ),
        md(
            "## Playoff bracket\n\n"
            "`render_playoff_brackets` reads `sleeper_playoff_matches` and "
            "`sleeper_standings` (`04_capturing_a_league.ipynb`'s "
            "`fetch_and_write_standings`) and renders a completed season's "
            "winners and losers brackets as horizontal tree PNGs:"
        ),
        code(
            "from nuclearff.report import render_playoff_brackets\n\n"
            "season = 2025\n"
            f'season_league_id = "{PLAYOFF_LEAGUE}"  # this league\'s 2025 league_id\n\n'
            'matches = read_table(db_path, "sleeper_playoff_matches").filter(\n'
            '    (pl.col("league_id") == season_league_id) & (pl.col("season") == season)\n'
            ").to_dicts()\n"
            'standings = read_table(db_path, "sleeper_standings").filter(\n'
            '    (pl.col("league_id") == season_league_id) & (pl.col("season") == season)\n'
            ")\n"
            'names = dict(zip(standings["roster_id"], standings["display_name"], strict=True))\n\n'
            "written = render_playoff_brackets(\n"
            '    matches, names, "./demo/data/artifacts/brackets", league_name="NUCLEARFF REDRAFT"\n'
            ")\n"
            "for bracket, path in written.items():\n"
            '    print(f"{bracket.title()} bracket: {path}")'
        ),
        image(
            "./demo/data/artifacts/brackets/winners_bracket.png",
            "Winners playoff bracket tree",
        ),
        image(
            "./demo/data/artifacts/brackets/losers_bracket.png",
            "Losers playoff bracket tree",
        ),
        md(
            "That's this league's real, completed 2025 season, each "
            "bracket rendered as its own figure (Sleeper treats the two "
            "brackets as visually distinct, and so does this). Each "
            "leaf/node is labeled with the real team display name; the "
            "winner of each match renders bold; and a match carrying a "
            "placement (the championship, the third-place game) appends the "
            "resulting rank, e.g. `nolmacdonald (1st)`.\n\n"
            "A Sleeper bracket isn't a similarity-clustering dendrogram -- "
            "it's a fixed-shape single-elimination tree keyed by "
            "`t1_from`/`t2_from` match references, so a later round's "
            "participant resolves through an earlier match's *winner or "
            "loser* rather than a fresh pair of roster ids. This league's "
            "own bracket has a real wrinkle: the championship and the "
            "third-place game split from the exact same pair of semifinal "
            "matches, landing on the same computed tree position -- the "
            "renderer nudges the two apart by their own box height so "
            "neither the lines nor the labels overlap."
        ),
        md(
            "## Draft order history\n\n"
            "`draft_order_stats` summarizes a manager's draft-slot history "
            "across every season on file -- average position, and how "
            "often they landed first or last overall:"
        ),
        code(
            "from nuclearff.sleeper import (\n"
            "    draft_order_stats,\n"
            "    fetch_and_write_all_drafts,\n"
            "    walk_league_chain,\n"
            ")\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    all_leagues = walk_league_chain(client, league_id, max_seasons=20)\n"
            "    n = fetch_and_write_all_drafts(client, all_leagues, db_path)\n"
            'print(f"Draft picks: {n}")\n\n'
            'all_picks = read_table(db_path, "sleeper_draft_picks")\n'
            'all_standings = read_table(db_path, "sleeper_standings")\n'
            "stats = draft_order_stats(all_picks, all_standings)\n"
            'stats.sort("avg_draft_position").head(10)'
        ),
        md(
            "By default (as with `report trades`/`report wins` in later "
            "chapters), only managers currently rostered in the league's "
            "own season appear -- pass `--all-users`'s Python equivalent by "
            "*not* filtering `standings` to the current `league_id` before "
            "calling `draft_order_stats`, exactly as done above, to include "
            "every manager across the league's full history instead."
        ),
        md(
            whats_next(
                "Point-in-time projections and draft snapshots are half the "
                "picture. `11_simulation.ipynb` turns a single projected "
                "mean into a full range of plausible season outcomes.\n\n"
                "## See Also\n\n"
                "- `04_capturing_a_league.ipynb` -- where `sleeper_draft_picks`/"
                "`sleeper_playoff_matches`/`sleeper_standings` come from.\n"
                "- [Draft and Playoff Visualizations](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/10_draft_and_playoff_visuals.html) -- "
                "the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 11_simulation.ipynb
# ==========================================================================

ch11 = nb(
    [
        md(
            "# 11. Monte Carlo Season Simulation\n\n"
            f"{NAV_HEADER} Run `10_draft_and_playoff_visuals.ipynb` first.\n\n"
            "A projection (`07_projections.ipynb`) is a single number -- a "
            "mean. `nuclearff.simulation.montecarlo` turns a mean and a "
            "spread into a full distribution of plausible season outcomes: "
            "a floor, a median, a ceiling, and the probability of clearing "
            "any threshold you care about. Mirrors the "
            "[Simulation](https://nolmacdonald.github.io/nuclearff/tutorial/"
            "11_simulation.html) docs page."
        ),
        md(
            "## Simulating a real player's season\n\n"
            "`simulate_player_season` needs a mean and standard deviation "
            "of *weekly* fantasy points, plus an optional skew (positive "
            "skew models the real shape of a fantasy scorer's week -- "
            "occasional big games pulling the tail right, more often than a "
            "symmetric normal distribution would predict). Compute those "
            "from a real player's actual 2024 weekly output under this "
            "league's scoring:"
        ),
        code(
            "import polars as pl\n"
            "from scipy.stats import skew\n\n"
            "from nuclearff.config import load_league_config\n"
            "from nuclearff.config.models import SimulationConfig\n"
            "from nuclearff.nflverse import load_weekly_skill_stats\n"
            "from nuclearff.scoring import ScoringEngine\n"
            "from nuclearff.simulation import simulate_player_season, summarize_distribution\n\n"
            f'league_cfg = load_league_config("./demo/configs/leagues/{REDRAFT}.yaml")\n'
            "engine = ScoringEngine(league_cfg.scoring)\n\n"
            "weekly = engine.score_frame(load_weekly_skill_stats([2024]))\n"
            'chase_weeks = weekly.filter(pl.col("player_display_name") == "Ja\'Marr Chase")["fantasy_points"].to_numpy()\n\n'
            "mean_ppg, sd_ppg, skew_val = float(chase_weeks.mean()), float(chase_weeks.std()), float(skew(chase_weeks))\n"
            'print(f"games={len(chase_weeks)} mean={mean_ppg:.2f} sd={sd_ppg:.2f} skew={skew_val:.2f}")'
        ),
        md(
            "That's Ja'Marr Chase's real, full 2024 season under this "
            "league's actual 6-point-passing-TD, full-PPR rules: a "
            "meaningfully positive skew, well above a symmetric "
            "distribution's 0 -- his week-to-week scoring really does have "
            "a long right tail of huge games, not just a wide spread around "
            "the mean. Simulate 10,000 full seasons from those three "
            "numbers:"
        ),
        code(
            "samples = simulate_player_season(mean_ppg, sd_ppg, skew_val, games=17, n_simulations=10000, seed=2026)\n"
            "samples.shape, round(samples.mean(), 1)"
        ),
        md(
            "`samples` is 10,000 simulated season totals -- one full "
            "17-game season per row, resampled from the fitted skew-normal "
            "weekly distribution. The mean of 10,000 simulated seasons "
            "should closely track the simple `mean_ppg * 17` estimate, "
            "which is exactly what a well-calibrated simulation should do:"
        ),
        code("round(mean_ppg * 17, 1)"),
        md(
            "## Summarizing the distribution\n\n"
            "Raw samples aren't useful on their own -- reduce them to the "
            "numbers you'd actually put in a report with "
            "`summarize_distribution`:"
        ),
        code(
            "sim_cfg = SimulationConfig()  # floor=p10, ceiling=p90 by default\n"
            "summary = summarize_distribution(samples, sim_cfg, top_n_threshold=330.0)\n"
            "summary"
        ),
        md(
            "Read against real 2024 WR1 fantasy-point totals, a 330-point "
            'threshold is a real "finished as a top-flight WR1" bar -- '
            "this simulation's `p_exceeds_threshold` is Chase's real "
            "probability of clearing it, given only his own real 2024 "
            "week-to-week volatility. `floor`/`ceiling` come from "
            "`SimulationConfig`'s configurable percentiles (10th/90th by "
            "default, from `02_configuration.ipynb`) -- not hardcoded, so a "
            "more conservative *or* more aggressive range is a config "
            "change, not a code change."
        ),
        md(
            "## Driving simulation from config\n\n"
            "`simulate_from_config` is the same simulation, but reading its "
            "knobs (`games`, `n_simulations`) straight from a "
            "`SimulationConfig` instead of repeating them as keyword "
            "arguments -- the version you'd actually wire into a pipeline "
            "that already has a loaded project config:"
        ),
        code(
            "from nuclearff.simulation import simulate_from_config\n\n"
            "samples2 = simulate_from_config(mean_ppg, sd_ppg, skew_val, sim_cfg, seed=2026)\n"
            "samples2.shape, round(samples2.mean(), 1)"
        ),
        md(
            teaching(
                "a single mean projection and a full simulated distribution "
                "answer different questions, and it's worth being precise "
                "about which one you're actually asking. \"Who is the best "
                'projected WR1" is a mean-projection question -- '
                '`07_projections.ipynb` answers it. "How much risk am I '
                "taking on by drafting this specific player instead of a "
                'safer one with a similar mean" is a distribution question '
                "-- only this chapter's `floor`/`ceiling` spread answers "
                "that, and two players with an identical mean can have very "
                "different floors."
            )
        ),
        md(
            whats_next(
                "A simulation is only as good as the model behind its mean "
                "and spread. `12_backtesting.ipynb` covers how to actually "
                "check that -- scoring a model against real past seasons it "
                "never saw, rather than trusting its numbers on faith.\n\n"
                "## See Also\n\n"
                "- `07_projections.ipynb` -- where the mean this chapter "
                "simulates around comes from.\n"
                "- [Simulation](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/11_simulation.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 12_backtesting.ipynb
# ==========================================================================

ch12 = nb(
    [
        md(
            "# 12. Backtesting and Model Validation\n\n"
            f"{NAV_HEADER} Run `11_simulation.ipynb` first.\n\n"
            "Every projection choice so far -- recency weights, an age "
            "curve, a replacement baseline -- is a modeling assumption, not "
            "a fact. The only way to know whether one choice is actually "
            "better than another is to score it against real seasons it "
            "never saw. `nuclearff.backtest` is that harness: "
            "expanding-window folds, two baseline models to compare "
            "against, and the metrics to score them. Mirrors the "
            "[Backtesting](https://nolmacdonald.github.io/nuclearff/"
            "tutorial/12_backtesting.html) docs page."
        ),
        md(
            "## Walk-forward folds\n\n"
            "`season_folds` generates expanding-window "
            "`(train_seasons, test_season)` pairs -- each fold trains on "
            "every season up to a point and tests on the very next one, the "
            "way a real projection actually gets used (you never get to "
            "peek at the season you're projecting):"
        ),
        code(
            "from nuclearff.backtest import season_folds\n\n"
            "folds = season_folds([2021, 2022, 2023, 2024, 2025], min_train_seasons=2)\n"
            "folds"
        ),
        md(
            "## Two baselines\n\n"
            "`nuclearff.backtest.walkforward` ships two named baselines to "
            "compare a real model against: `baseline_prior_year` (predict "
            "this season as an exact copy of last season) and "
            "`baseline_recency_weighted` (this tutorial's own "
            "recency-weighted blend from `07_projections.ipynb`, applied to "
            "whole fantasy-point totals)."
        ),
        md(
            "## Running a real backtest\n\n"
            "`run_walk_forward_backtest` orchestrates fold generation, both "
            "models' predictions, and scoring, in one call. Real 2021-2025 "
            "WR seasons, scored under this league's own rules:"
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.backtest import (\n"
            "    baseline_prior_year,\n"
            "    baseline_recency_weighted,\n"
            "    run_walk_forward_backtest,\n"
            ")\n"
            "from nuclearff.config import load_league_config\n"
            "from nuclearff.config.models import RecencyWeights\n"
            "from nuclearff.nflverse import load_seasonal_skill_stats\n"
            "from nuclearff.scoring import ScoringEngine\n\n"
            f'league_cfg = load_league_config("./demo/configs/leagues/{REDRAFT}.yaml")\n'
            "engine = ScoringEngine(league_cfg.scoring)\n\n"
            "seasons = [2021, 2022, 2023, 2024, 2025]\n"
            'scored = engine.score_frame(load_seasonal_skill_stats(seasons)).filter(pl.col("position") == "WR")\n\n'
            "models = {\n"
            '    "prior_year": lambda df, test_season: baseline_prior_year(df, test_season, "fantasy_points"),\n'
            '    "recency_weighted": lambda df, test_season: baseline_recency_weighted(\n'
            '        df, test_season, "fantasy_points", RecencyWeights()\n'
            "    ),\n"
            "}\n"
            'results = run_walk_forward_backtest(scored, seasons, "fantasy_points", models, min_train_seasons=2, top_k=12)\n'
            "results"
        ),
        md(
            "This is a genuinely useful, real result to look for: across "
            "all three real folds, does `recency_weighted` beat the naive "
            "`prior_year` baseline on every single metric -- lower error "
            "(MAE, RMSE), higher rank correlation, and better top-12 "
            "precision/recall? This is exactly the kind of evidence "
            "`07_projections.ipynb`'s recency weighting needs before being "
            "trusted over the simplest possible alternative -- a claim "
            "checked against data, not asserted."
        ),
        md(
            teaching(
                "a backtest that only ever confirms your existing model is "
                '"working" isn\'t doing its job. The valuable failure mode '
                "to watch for here is a fold where `prior_year` actually "
                "*beats* `recency_weighted` -- that's real information about "
                "a season where recent form was a worse predictor than "
                "simple continuity (an unusually volatile league-wide year, "
                "say), not a bug to explain away."
            )
        ),
        md(
            "## Scoring one fold by hand\n\n"
            "`evaluate_fold` is what `run_walk_forward_backtest` calls once "
            "per model per fold -- useful on its own when you already have "
            "a prediction frame and just want a score:"
        ),
        code(
            "from nuclearff.backtest import evaluate_fold\n\n"
            'predicted = baseline_recency_weighted(scored, 2025, "fantasy_points", RecencyWeights())\n'
            'actual = scored.filter(pl.col("season") == 2025).select(["player_id", "fantasy_points"])\n\n'
            "metrics = evaluate_fold(\n"
            "    predicted, actual,\n"
            '    predicted_column="fantasy_points_predicted",\n'
            '    actual_column="fantasy_points",\n'
            "    top_k=12,\n"
            ")\n"
            "metrics"
        ),
        md(
            "## Individual metrics\n\n"
            "Every score above is also available standalone in "
            "`nuclearff.backtest.metrics` -- `mae`, `rmse`, "
            "`spearman_correlation`, and `top_k_precision_recall` for "
            "continuous predictions, plus two more for discrete outputs: "
            "`tier_accuracy` (does a predicted draft tier from "
            "`08_valuation.ipynb` match the tier the player's real output "
            "lands in) and `brier_score` (mean squared error of a predicted "
            "probability against a binary real outcome):"
        ),
        code(
            "from nuclearff.backtest import tier_accuracy\n"
            "from nuclearff.valuation import assign_tiers\n\n"
            'joined = predicted.join(actual, on="player_id", how="inner").drop_nulls()\n'
            'pred_tiers = assign_tiers(joined["fantasy_points_predicted"].to_list())\n'
            'actual_tiers = assign_tiers(joined["fantasy_points"].to_list())\n'
            "tier_accuracy(pred_tiers, actual_tiers)"
        ),
        md(
            "`exact_match_rate` is usually much lower than `within_one_rate` "
            "-- the recency-weighted model's predicted tier rarely matches "
            "the player's actual-output tier *exactly*, but is close to "
            "never off by more than one tier, which is closer to what a "
            "draft-day tier board actually needs than exact-tier accuracy "
            "alone would suggest."
        ),
        md(
            whats_next(
                "This chapter validated a *projection* against outcomes it "
                "never saw. `13_league_history.ipynb` turns the same stored "
                "league data (`04_capturing_a_league.ipynb`) toward a "
                'different question -- not "what will happen," but "what '
                "already did,\" across your league's entire real history.\n\n"
                "## See Also\n\n"
                "- `07_projections.ipynb` -- the recency-weighted model this "
                "chapter validates.\n"
                "- [Backtesting](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/12_backtesting.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 13_league_history.ipynb
# ==========================================================================

ch13 = nb(
    [
        md(
            "# 13. League History and Records\n\n"
            f"{NAV_HEADER} Run `12_backtesting.ipynb` first.\n\n"
            "`04_capturing_a_league.ipynb` persisted six real seasons of "
            "standings, matchups, and transactions. `nuclearff.archive` "
            "mines that same stored data for a completely different purpose "
            "than valuation -- a league's own record book: champions, "
            "blowouts, rivalries, draft steals and busts, and a full career "
            "profile per manager. Every function here is "
            "read/aggregate-only: no new Sleeper fetching, just real "
            "history already on disk. Mirrors the [League History]"
            "(https://nolmacdonald.github.io/nuclearff/tutorial/"
            "13_league_history.html) docs page."
        ),
        md(
            "## Championship history\n\n"
            "`championship_history` is the shared foundation the rest of "
            "this notebook joins against -- one row per season: champion, "
            "runner-up, and regular-season leader:"
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.archive.champions import championship_history\n"
            "from nuclearff.duckdb_io import read_table\n\n"
            'db_path = "./demo/data/cache/nuclearff.duckdb"\n'
            'standings = read_table(db_path, "sleeper_standings")\n\n'
            "champs = championship_history(standings)\n"
            'champs.sort("season").select(\n'
            '    ["season", "champion_display_name", "runner_up_display_name", "regular_season_leader_display_name"]\n'
            ")"
        ),
        md(
            "Watch for real, honest nulls in both directions: a season can "
            "show a null champion because that season's champion roster's "
            "owner isn't resolvable in that season's user list (a real "
            "Sleeper data quirk `14_trade_network.ipynb` runs into for "
            "trades too), and the current in-progress season has no "
            "champion yet -- `championship_history` doesn't guess at an "
            "unfinished season."
        ),
        md(
            "## Season awards\n\n"
            "`season_awards` surfaces three superlatives, each honestly "
            "derivable from `sleeper_standings` alone -- no subjective "
            "judgment call invented for something this project has no data "
            'to back (a "best trade" or "most exciting week" award would '
            "need a real, stated scoring rule from you, not a guessed "
            "heuristic):"
        ),
        code(
            "from nuclearff.archive.awards import season_awards\n\n"
            "awards = season_awards(standings)\n"
            "awards"
        ),
        md(
            "Three real, spot-checkable kinds of result to look for:\n\n"
            "- **Best regular season** -- the most dominant #1 seed in this "
            "league's history by total points, among every season's own "
            "regular-season leader.\n"
            "- **Cinderella run** -- a champion whose real regular-season "
            "rank was well outside the top spot (`value` is the rank gap). "
            "Since a champion's `final_rank` is always 1 by definition, "
            "this needs no playoff-week boundary at all -- unlike "
            "`14_trade_network.ipynb`'s rivalries split, \"did this team "
            'overcome a bad regular season" is already answered by '
            "`regular_season_rank` vs. `final_rank` alone.\n"
            "- **Biggest year-over-year improvement** -- the largest "
            "single-manager point jump between **consecutive** seasons. A "
            "gap season (absent one year, back the next) is not treated as "
            '"improvement."\n\n'
            "An award with no eligible season (e.g. no season has a "
            "resolved `final_rank` yet) is simply absent from the result "
            "rather than a row of nulls."
        ),
        md(
            "## Records: extremes and streaks\n\n"
            "`score_extremes` and `margin_extremes` scan every real matchup "
            "for the league's all-time highest/lowest score and biggest "
            "blowout/closest margin:"
        ),
        code(
            "from nuclearff.archive.records import longest_streak, margin_extremes, score_extremes\n"
            "from nuclearff.sleeper.wins import weekly_results\n\n"
            'matchups = read_table(db_path, "sleeper_matchups")\n'
            "score_extremes(matchups, standings)"
        ),
        code("margin_extremes(matchups, standings)"),
        md(
            "`longest_streak` needs each roster's real per-week win/loss, "
            "from `weekly_results` (`15_wins_and_leagues.ipynb` uses the "
            "same function for cumulative wins):"
        ),
        code(
            "results = weekly_results(matchups)\n"
            'win_streaks = longest_streak(results, standings, "win")\n'
            'win_streaks.sort("length", descending=True).head(3)'
        ),
        md(
            'Pass `"loss"` for `streak_type` to get the same for losing streaks instead.'
        ),
        md(
            "## Rivalries: head-to-head\n\n"
            "`head_to_head` returns the all-time combined record for every "
            "manager pair that has ever met:"
        ),
        code(
            "from nuclearff.archive.rivalries import head_to_head\n\n"
            "h2h = head_to_head(matchups, standings)\n"
            'h2h.sort("games", descending=True).select(\n'
            '    ["manager_a", "manager_b", "games", "wins_a", "wins_b", "biggest_blowout_margin"]\n'
            ").head(3)"
        ),
        md(
            "This is the combined regular-season **and** playoff record. "
            "`head_to_head_by_phase` splits it -- using `LeagueConfig`'s "
            "real `playoff_week_start` (`02_configuration.ipynb`) to tell "
            "the two apart, rather than a hardcoded week range:"
        ),
        code(
            "from nuclearff.archive.rivalries import head_to_head_by_phase\n\n"
            'league_configs = read_table(db_path, "sleeper_league_configs")\n'
            "split = head_to_head_by_phase(matchups, standings, league_configs)\n\n"
            'top_row = h2h.sort("games", descending=True).row(0, named=True)\n'
            "top_pair = split.filter(\n"
            '    (pl.col("manager_a") == top_row["manager_a"]) & (pl.col("manager_b") == top_row["manager_b"])\n'
            ")\n"
            'top_pair.select(["phase", "games", "wins_a", "wins_b"])'
        ),
        md(
            "The two phases' `games` should sum to exactly this pair's "
            "combined `games` above -- the real cross-check "
            "`head_to_head_by_phase`'s own docstring promises. A "
            "`(league_id, season)` with no resolvable `playoff_week_start` "
            '-- a real "Chopped" league with no bracket, or a season '
            "simply missing from `league_configs` -- is excluded from "
            "**both** phases rather than guessed into one."
        ),
        code('top_pair["games"].sum() == top_row["games"]'),
        md(
            "## Draft retrospectives\n\n"
            "`grade_historical_picks` grades every historical pick against "
            "how the player *actually* performed that season -- a real "
            "final-output number, not a pre-draft projection. It needs "
            "`season_actuals` (exploded weekly box scores) concatenated "
            "across every season on file:"
        ),
        code(
            "from nuclearff.archive.draft_retro import grade_historical_picks\n"
            "from nuclearff.sleeper.performance import season_actuals\n\n"
            'picks = read_table(db_path, "sleeper_draft_picks")\n'
            "actuals = pl.concat(\n"
            '    [season_actuals(matchups, standings, lid) for lid in standings["league_id"].unique()],\n'
            '    how="diagonal_relaxed",\n'
            ")\n"
            'graded = grade_historical_picks(picks, actuals, standings).filter(pl.col("season") < 2026)\n\n'
            'graded.sort("rank_delta", descending=True).select(\n'
            '    ["season", "manager", "player_name", "round", "pick_no", "expected_rank", "actual_rank", "rank_delta"]\n'
            ").head(3)"
        ),
        md(
            "A large positive `rank_delta` is a real steal: a player "
            "picked late by draft position who actually finished as one of "
            "the league's best that season. Sort ascending instead for "
            "busts -- a top pick who finished far down the real rankings, "
            "often due to a real injury. `sleeper_draft_picks.is_keeper` is "
            "a known, unresolved confound here -- a keeper pick's draft "
            "slot reflects a league rule, not a real assessment of the "
            "player's value, and this function leaves filtering it to the "
            "caller rather than silently excluding keeper leagues' data."
        ),
        md(
            teaching(
                '"draft steal" and "draft bust" are fun to read, but the '
                "`is_keeper` caveat above is the kind of thing that quietly "
                "invalidates a whole draft-retro report if you skip it. A "
                "keeper kept at round 14 because a league rule says so isn't "
                "the same signal as a real 14th-round gamble that happened "
                "to pay off -- filter `is_keeper` out before drawing "
                "conclusions about draft *skill* specifically, and keep it "
                "in only if you're asking a different question (\"which "
                'picks, keeper or not, produced the most value").'
            )
        ),
        md(
            "## Franchise profiles\n\n"
            "`franchise_profile` assembles everything above into one row "
            "per manager -- pure assembly, no new stat computed:"
        ),
        code(
            "from nuclearff.archive.franchise import franchise_profile\n"
            "from nuclearff.sleeper import draft_order_stats\n"
            "from nuclearff.sleeper.trades import load_trades, manager_trade_counts\n\n"
            "se, me = score_extremes(matchups, standings), margin_extremes(matchups, standings)\n"
            'win_streaks = longest_streak(results, standings, "win")\n'
            'loss_streaks = longest_streak(results, standings, "loss")\n'
            "trade_counts = manager_trade_counts(load_trades(db_path))\n"
            "draft_stats = draft_order_stats(picks, standings)\n\n"
            "profile = franchise_profile(standings, champs, trade_counts, draft_stats, se, me, win_streaks, loss_streaks)\n"
            'profile.sort("career_wins", descending=True).select(\n'
            '    ["manager", "seasons_played", "career_wins", "career_losses", "championships", "records_held"]\n'
            ").head(4)"
        ),
        md(
            "A common, real-feeling fantasy football story that a plain "
            "career-record table would hide: check whether this league's "
            "own career wins leader has actually won a championship. "
            "`championships` sitting right next to career record answers "
            "that instantly instead of needing a separate lookup."
        ),
        md(
            "## Team name history\n\n"
            "`team_name_changes` tracks each manager's chronological "
            "team-name history, keyed by `owner_id` (stable across name "
            "changes) rather than the display name that changes with it:"
        ),
        code(
            "from nuclearff.archive.name_history import team_name_changes\n"
            "from nuclearff.sleeper import SleeperClient, walk_league_chain\n"
            "from nuclearff.sleeper.roster_names import fetch_and_write_team_names\n\n"
            f'league_id = "{REDRAFT}"\n'
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    chain = walk_league_chain(client, league_id, max_seasons=20)\n"
            "    fetch_and_write_team_names(client, chain, db_path)\n\n"
            'roster_names = read_table(db_path, "sleeper_roster_names")\n'
            "names_history = team_name_changes(roster_names, standings)\n"
            'names_history.sort("change_count", descending=True).head(3)'
        ),
        md(
            "A league where nobody has ever set a custom Sleeper team name "
            "will show an honest `change_count` of 0 across the board -- "
            "every roster falls back to a null name every season. A league "
            "that *does* use custom team names shows real sequences and "
            "non-zero counts here instead."
        ),
        md(
            "## Waiver spend\n\n"
            "`manager_waiver_spend` and `career_waiver_spend` turn stored "
            "transactions into real FAAB spending history:"
        ),
        code(
            "from nuclearff.archive.waiver_spend import career_waiver_spend, manager_waiver_spend\n\n"
            'transactions = read_table(db_path, "sleeper_transactions")\n'
            "spend = manager_waiver_spend(transactions)\n"
            "career = career_waiver_spend(spend)\n"
            'career.sort("total_spent", descending=True).head(3)'
        ),
        md(
            "A manager's real failed-claims count sitting next to their "
            "successful acquisitions is the kind of detail a season-total "
            "spend number alone would hide -- someone who spent the most "
            "may have also lost the most bids along the way."
        ),
        md(
            "## On this day\n\n"
            "`transactions_on_this_day` finds every real transaction whose "
            "anniversary matches a given date -- `transaction_summary_rows` "
            "turns that into display-ready text, resolving player ids to "
            "names when a `sleeper_players` table is available:"
        ),
        code(
            "from datetime import date\n\n"
            "from nuclearff.archive.on_this_day import (\n"
            "    transaction_summary_rows,\n"
            "    transactions_on_this_day,\n"
            ")\n"
            "from nuclearff.ids import read_sleeper_players\n\n"
            "players = read_sleeper_players(db_path)\n"
            "today = date.today()\n"
            "on_this_day = transactions_on_this_day(transactions, today)\n"
            "transaction_summary_rows(on_this_day, players).head(3)"
        ),
        md(
            "Since this cell uses today's real calendar date, its output "
            "will differ every time this notebook is re-executed -- that's "
            'expected, not a bug; it\'s the whole point of an "on this day" '
            "lookup."
        ),
        md(
            whats_next(
                "League history above was keyed by manager and season. "
                "`14_trade_network.ipynb` zooms into one specific "
                "relationship -- who trades with whom -- as ten different "
                "visualizations from the same stored transaction data.\n\n"
                "## See Also\n\n"
                "- `04_capturing_a_league.ipynb` -- where every table this "
                "chapter reads comes from.\n"
                "- [League History](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/13_league_history.html) -- the matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 14_trade_network.ipynb
# ==========================================================================

TRADES_DIR = f"./demo/data/artifacts/{REDRAFT}-trades"


def trade_figure(filename: str, alt: str):
    return image(f"{TRADES_DIR}/{filename}", alt)


ch14 = nb(
    [
        md(
            "# 14. Trade Network Analysis\n\n"
            f"{NAV_HEADER} Run `13_league_history.ipynb` first.\n\n"
            "`nuclearff.report.trades` turns a league's stored trade "
            "history (`04_capturing_a_league.ipynb`'s "
            "`sleeper_transactions`) into ten different views of who "
            "trades, who trades with whom, and how that's changed over "
            "time. This notebook walks through all ten, grounded in one "
            f"real league's real data (`{REDRAFT}`, `NUCLEARFF REDRAFT`). "
            "No new Sleeper fetching happens here -- every function below "
            "only reads and renders what `04_capturing_a_league.ipynb` "
            "already stored. Mirrors the [Trade Network Analysis]"
            "(https://nolmacdonald.github.io/nuclearff/tutorial/"
            "14_trade_network.html) docs page."
        ),
        md(
            "## Trade edges and manager totals\n\n"
            "`load_trades` explodes every stored trade transaction into one "
            "row per manager pair per trade; `manager_trade_counts` rolls "
            "that up per manager:"
        ),
        code(
            "import polars as pl\n\n"
            "from nuclearff.duckdb_io import read_table\n"
            "from nuclearff.sleeper.trades import load_trades, manager_trade_counts\n\n"
            'db_path = "./demo/data/cache/nuclearff.duckdb"\n'
            "edges = load_trades(db_path)\n"
            "counts = manager_trade_counts(edges)\n"
            'print(edges.height, "trade edges")\n'
            'counts.sort("trades", descending=True).head(3)'
        ),
        md(
            "The most active trader stands out by a wide margin in the "
            "real output above. Note `trades` counts **distinct trades, "
            "not trade relationships** -- Sleeper allows more than two "
            "rosters in a single trade, and this league has real ones: a "
            "three-team trade counts once for each of the three managers "
            "involved, not twice for the two other parties each of them "
            "touches."
        ),
        md(
            "## Including managers who never traded\n\n"
            "`manager_trade_counts` only returns managers who appear in "
            "`edges` -- several of this league's real all-time managers "
            "never made a trade and are absent entirely. Join against the "
            "full manager roster from `sleeper_standings` "
            "(`04_capturing_a_league.ipynb`) to include them at an explicit "
            "zero -- exactly what `report trades` does for you on the "
            "command line:"
        ),
        code(
            'standings = read_table(db_path, "sleeper_standings")\n'
            'all_managers = sorted({name for name in standings["display_name"].to_list() if name})\n\n'
            "counts_full = (\n"
            '    pl.DataFrame({"manager": all_managers})\n'
            '    .join(counts, on="manager", how="left")\n'
            "    .with_columns(\n"
            '        pl.col("trades").fill_null(0),\n'
            '        pl.col("unique_partners").fill_null(0),\n'
            '        pl.col("trades_with_partner").fill_null(0),\n'
            "    )\n"
            ")\n"
            "print(f\"{counts_full.filter(pl.col('trades') == 0).height} of {counts_full.height} managers never traded\")"
        ),
        md(
            "The rest of this notebook uses `counts_full` (and an "
            "equally-densified trade matrix, below) so every chart includes "
            "every real manager, not just the ones who show up in trade "
            "data."
        ),
        md(
            teaching(
                "this densification step is the exact bug this project "
                "shipped and then fixed in its own dashboard (issue #177): "
                "it's easy to write `manager_trade_counts(edges)` once, "
                "render straight from it, and never notice that a "
                "zero-trade manager silently vanished -- the chart still "
                "renders, it just quietly under-represents the league. "
                "Whenever you build a per-manager view from trade data "
                "yourself, always join against the full roster first, the "
                "way this cell does."
            )
        ),
        md(
            "## Trades by manager, and between managers\n\n"
            "`render_trades_by_manager` draws a horizontal bar chart, one "
            "bar per manager:"
        ),
        code(
            "from nuclearff.report import render_trades_by_manager\n\n"
            f'render_trades_by_manager(counts_full.select("manager", "trades"), "{TRADES_DIR}/trades_by_manager.png")'
        ),
        trade_figure("trades_by_manager.png", "Total trades per manager"),
        md(
            "`pairwise_trade_matrix` builds a symmetric manager x manager "
            "count matrix -- densify it the same way as `counts` before "
            "rendering, so a zero-trade manager still gets a dense, "
            "all-zero row and column:"
        ),
        code(
            "from nuclearff.report import render_trades_heatmap\n"
            "from nuclearff.sleeper.trades import pairwise_trade_matrix\n\n"
            "raw_matrix = pairwise_trade_matrix(edges)\n"
            "# pairwise_trade_matrix is only dense over managers who appear in\n"
            "# edges -- a manager who never traded is missing as both a row and a\n"
            "# column, so densify both before selecting the full all_managers set.\n"
            "missing_cols = [m for m in all_managers if m not in raw_matrix.columns]\n"
            "raw_matrix = raw_matrix.with_columns([pl.lit(0).alias(m) for m in missing_cols])\n"
            "matrix = (\n"
            '    pl.DataFrame({"manager": all_managers})\n'
            '    .join(raw_matrix, on="manager", how="left")\n'
            "    .fill_null(0)\n"
            '    .select(["manager", *all_managers])\n'
            ")\n"
            f'render_trades_heatmap(matrix, "{TRADES_DIR}/trades_heatmap.png")'
        ),
        md(
            teaching(
                "this exact league's real data surfaces a case the plain "
                "row-join above doesn't handle on its own: "
                "`pairwise_trade_matrix`'s docstring says it's \"dense over "
                'every manager who appears in `edges`" -- but with this '
                "league's own trade history, several all-time managers "
                "never traded *at all*, so they're missing as columns too, "
                'not just rows. A plain `.select(["manager", *all_managers])` '
                "on the joined frame raises `ColumnNotFoundError` for "
                "exactly those managers. The `missing_cols` step above adds "
                "them back as an explicit zero column before selecting -- "
                "worth remembering any time you densify a matrix-shaped "
                "DataFrame against a roster that's a strict superset of "
                "what the raw data contains."
            )
        ),
        trade_figure("trades_heatmap.png", "Manager x manager trade heatmap"),
        md(
            "The heatmap is symmetric by construction (trades between two "
            "managers show the same count in both directions) with a 0 "
            "diagonal, since a manager can't trade with themselves."
        ),
        md(
            "## Trade network and chord diagram\n\n"
            "`render_trade_network` draws a node-link graph -- one node "
            "per manager (sized by total trades), one edge per pair that "
            "has traded (widened by trade count):"
        ),
        code(
            "from nuclearff.report import render_chord_diagram, render_trade_network\n\n"
            f'render_trade_network(counts_full.select("manager", "trades"), matrix, "{TRADES_DIR}/trade_network.png")\n'
            f'render_chord_diagram(counts_full.select("manager", "trades"), matrix, "{TRADES_DIR}/chord_diagram.png")'
        ),
        trade_figure(
            "trade_network.png", "Node-link graph of the manager trade network"
        ),
        trade_figure("chord_diagram.png", "Circular chord diagram"),
        md(
            "With a real league's actual trade volume, this graph is often "
            "genuinely sparse -- several managers may never connect to the "
            "rest of the league at all. That's real trading activity, not "
            "a rendering bug. The chord diagram shows the same "
            "relationships, drawn directly in matplotlib (not an "
            "interactive plotting library) to avoid a headless-browser "
            "dependency for one chart -- see this project's own decision "
            "notes for why that specific choice was made after a real "
            "`kaleido`/headless-Chrome dependency problem came up."
        ),
        md(
            "## Leaderboards\n\n"
            "`render_trade_leaderboard` renders one reference row per "
            "manager (Trades, Unique Partners, Most Frequent Partner, "
            "Trades With Partner), and `render_manager_pair_leaderboard` "
            "shows the top 10 manager *pairs* by trade count -- unlike the "
            "heatmap, it only shows pairs that actually traded:"
        ),
        code(
            "from nuclearff.report import render_manager_pair_leaderboard, render_trade_leaderboard\n"
            "from nuclearff.sleeper.trades import top_manager_pairs\n\n"
            f'render_trade_leaderboard(counts_full, "{TRADES_DIR}/trade_leaderboard.png")\n\n'
            "pairs = top_manager_pairs(edges)\n"
            f'render_manager_pair_leaderboard(pairs, "{TRADES_DIR}/manager_pair_leaderboard.png")'
        ),
        trade_figure("trade_leaderboard.png", "Trade leaderboard reference table"),
        trade_figure(
            "manager_pair_leaderboard.png", "Top manager pairs by trade count"
        ),
        md(
            "A zero-trade manager still gets a full row on the leaderboard, "
            "with `—` in place of a partner that doesn't exist. The "
            "pair leaderboard shows each pair once -- manager names are "
            "sorted alphabetically before grouping, so an A-B and B-A row "
            "for the same pair can't both exist."
        ),
        md(
            "## Trades over time\n\n"
            "`trades_by_season` and `total_trades_by_season` feed "
            "`render_trades_over_time` (one thin line per manager plus a "
            "bold league-wide total) and `render_manager_season_heatmap` "
            "(the same data as a dense manager x season grid):"
        ),
        code(
            "from nuclearff.report import render_manager_season_heatmap, render_trades_over_time\n"
            "from nuclearff.sleeper.trades import total_trades_by_season, trades_by_season\n\n"
            "by_season = trades_by_season(edges)\n"
            "totals = total_trades_by_season(edges)\n"
            f'render_trades_over_time(by_season, totals, "{TRADES_DIR}/trades_over_time.png")'
        ),
        trade_figure(
            "trades_over_time.png",
            "Trades by season, one line per manager plus a league total",
        ),
        md(
            "`render_manager_season_heatmap` needs a fully dense manager x "
            "season matrix -- `trades_by_season` alone is only dense over "
            "combinations that actually had a trade (deliberately, so it "
            "stays usable for the line chart above too). Densify it the "
            "same way the CLI's own `report trades` command does, over "
            "every manager and every season in this league's real history:"
        ),
        code(
            'all_seasons = sorted(standings["season"].unique().to_list())\n'
            "by_season_lookup = {\n"
            '    (row["manager"], row["season"]): row["trades"] for row in by_season.to_dicts()\n'
            "}\n"
            "season_matrix = pl.DataFrame(\n"
            "    [\n"
            "        {\n"
            '            "manager": manager,\n'
            "            **{\n"
            "                str(season): by_season_lookup.get((manager, season), 0)\n"
            "                for season in all_seasons\n"
            "            },\n"
            "        }\n"
            "        for manager in all_managers\n"
            "    ]\n"
            ")\n"
            f'render_manager_season_heatmap(season_matrix, "{TRADES_DIR}/manager_season_heatmap.png")'
        ),
        trade_figure("manager_season_heatmap.png", "Manager x season trade heatmap"),
        md(
            "The league total counts each trade once regardless of how "
            "many managers it involved -- summing every manager's own line "
            "would roughly double-count a season's real trade volume, "
            "since most trades involve exactly two managers."
        ),
        md(
            "## Cumulative trades and partner diversity\n\n"
            "`cumulative_trade_counts` and `render_cumulative_trades` "
            "answer \"who became the league's most prolific trader, and "
            'when did they take the lead":'
        ),
        code(
            "from nuclearff.report import render_cumulative_trades, render_trade_partner_diversity\n"
            "from nuclearff.sleeper.trades import cumulative_trade_counts\n\n"
            "cumulative = cumulative_trade_counts(edges)\n"
            f'render_cumulative_trades(cumulative, "{TRADES_DIR}/cumulative_trades.png")\n\n'
            "render_trade_partner_diversity(\n"
            '    counts_full.select("manager", "trades", "unique_partners"),\n'
            f'    "{TRADES_DIR}/trade_partner_diversity.png",\n'
            ")"
        ),
        trade_figure(
            "cumulative_trades.png",
            "Step chart of cumulative trades per manager over time",
        ),
        trade_figure(
            "trade_partner_diversity.png", "Total trades vs. unique trade partners"
        ),
        md(
            "The partner-diversity scatter separates a manager who trades "
            "widely from one who repeatedly trades with the same one or two "
            "people -- X axis is total trades, Y axis is unique partners. "
            "Every zero-trade manager collapses into a single labeled point "
            "at the origin rather than several fully-overlapping ones."
        ),
        md(
            "## Reading these charts correctly\n\n"
            "Three real details from this exact data apply across every "
            "chart above, not just one:\n\n"
            "- **A manager needs a resolvable Sleeper display name to "
            "appear at all.** A real trade with a roster whose owner isn't "
            "in that season's user list contributes to no chart.\n"
            "- **Sleeper allows more than two rosters in a single trade.** "
            "Every trade-counting aggregate here de-duplicates by the "
            "underlying transaction, not by exploded pairwise edges -- a "
            "3-team trade contributes one trade to each of the three "
            "managers involved, not two.\n"
            "- **Manager identity is a display name, not a stable id**, "
            "and a league with two similarly-spelled display names shows "
            "exactly why that matters -- nothing here merges similar-"
            "looking names automatically."
        ),
        md(
            whats_next(
                "Trades are one lens on league history. "
                "`15_wins_and_leagues.ipynb` covers two more: a manager's "
                "cumulative win total across their real career, and a full "
                "snapshot of every league a Sleeper user belongs to.\n\n"
                "## See Also\n\n"
                "- `04_capturing_a_league.ipynb` -- `sleeper fetch-league "
                "--transactions`, the data this chapter reads.\n"
                "- [Trade Network Analysis](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/14_trade_network.html) -- the matching "
                "docs page."
            )
        ),
    ]
)

# ==========================================================================
# 15_wins_and_leagues.ipynb
# ==========================================================================

ch15 = nb(
    [
        md(
            "# 15. Cumulative Wins and League Overviews\n\n"
            f"{NAV_HEADER} Run `14_trade_network.ipynb` first.\n\n"
            "Two more views built from the data `04_capturing_a_league.ipynb` "
            "and `03_sleeper_api.ipynb` already gave you: a manager's "
            "running win total over their entire real career, and a full "
            "snapshot of every league a Sleeper user belongs to. Mirrors "
            "the [Wins and Leagues](https://nolmacdonald.github.io/"
            "nuclearff/tutorial/15_wins_and_leagues.html) docs page."
        ),
        md(
            "## Cumulative wins\n\n"
            "`weekly_results` derives each roster's per-week win/loss/tie "
            "from raw matchup points (nothing stores this directly), and "
            "`cumulative_wins` turns that into a running total per manager, "
            "in each manager's own chronological order:"
        ),
        code(
            "from nuclearff.duckdb_io import read_table\n"
            "from nuclearff.sleeper.wins import cumulative_wins, weekly_results\n\n"
            'db_path = "./demo/data/cache/nuclearff.duckdb"\n'
            'matchups = read_table(db_path, "sleeper_matchups")\n'
            'standings = read_table(db_path, "sleeper_standings")\n\n'
            "results = weekly_results(matchups)\n"
            "cumulative = cumulative_wins(results, standings)\n"
            "print(cumulative.columns)\n"
            "print(f\"{cumulative['manager'].n_unique()} managers\")"
        ),
        md(
            "`render_cumulative_wins` renders this as a step chart, one "
            "line per manager, ending in that manager's real Sleeper "
            "headshot rather than a text label. It needs each manager's "
            "avatar id -- pull it straight from `get_users`:"
        ),
        code(
            "from nuclearff.report import render_cumulative_wins\n"
            "from nuclearff.sleeper import SleeperClient\n\n"
            f'league_id = "{REDRAFT}"\n'
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    users = client.get_users(league_id)\n"
            'avatar_ids = {u["display_name"]: u.get("avatar") for u in users}\n\n'
            "written = render_cumulative_wins(\n"
            '    cumulative, avatar_ids, "./demo/data/artifacts/wins.png",\n'
            '    cache_dir="./demo/data/cache/avatars",\n'
            ")\n"
            "written"
        ),
        image(
            "./demo/data/artifacts/wins.png",
            "Step chart of cumulative wins per manager, each line ending in a headshot",
        ),
        md(
            "A manager's line starts at game 1 of *their own* real "
            "history, not the league's -- a manager who joined partway "
            "through doesn't get their earlier seasons backfilled. This is "
            "raw chronological win count, not adjusted for strength of "
            "schedule or playoff seeding -- recall from "
            "`04_capturing_a_league.ipynb` why that distinction matters "
            "here specifically: a real regular-season leader can still "
            "finish well outside first place. A manager missing from "
            "`avatar_ids` (or mapped to a failed download) renders with a "
            "neutral placeholder image rather than a broken one."
        ),
        md(
            "## A user's leagues, as a PNG table\n\n"
            "`render_user_leagues_table` resolves a username to every "
            "league they're in for a season and renders it as a PNG table "
            "-- avatar, name, league id, type, team count, status:"
        ),
        code(
            "from nuclearff.report import render_user_leagues_table, summarize_league_types\n\n"
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            '    user = client.get_user("nolmacdonald")\n'
            '    user_leagues = client.get_user_leagues(user["user_id"], 2026, sport="nfl")\n\n'
            "written = render_user_leagues_table(\n"
            '    user_leagues, "./demo/data/artifacts/user_leagues.png",\n'
            "    title=f\"{user['display_name']}'s Leagues\",\n"
            '    subtitle=f"{len(user_leagues)} leagues  |  nfl  |  2026",\n'
            '    cache_dir="./demo/data/cache/avatars",\n'
            ")\n"
            "print(f\"User:    {user['display_name']} ({user['user_id']})\")\n"
            'print(f"Leagues: {written}")'
        ),
        image(
            "./demo/data/artifacts/user_leagues.png",
            "PNG table of a user's leagues with avatars and a per-type summary",
        ),
        md(
            "`summarize_league_types` gives the same per-type breakdown the table's own bottom row shows, as a plain dict:"
        ),
        code("summarize_league_types(user_leagues)"),
        md(
            "`Type` resolves Sleeper's numeric `settings.type` (0/1/2/3) to "
            "a readable label from the league object itself, not a "
            "name-substring match -- a league can be genuinely Chopped-type "
            "without yet having eliminated anyone this season. A league "
            "with no avatar set gets a neutral placeholder image, not a "
            "broken cell."
        ),
        md(
            "## Actual vs. projected performance\n\n"
            "One more comparison worth knowing about: "
            "`nuclearff.sleeper.performance` turns Sleeper's own weekly "
            "player projections (fetched with "
            "`fetch_and_write_projections_range`, scored under this "
            "league's rules) against real results, ranking who most over- "
            "or under-performed expectation:"
        ),
        code(
            "from nuclearff.config import load_league_config\n"
            "from nuclearff.ids import read_sleeper_players\n"
            "from nuclearff.sleeper.performance import weekly_actuals, weekly_performance\n"
            "from nuclearff.sleeper.projections import fetch_and_write_projections_range\n\n"
            f'league_cfg = load_league_config("./demo/configs/leagues/{REDRAFT}.yaml")\n\n'
            'with SleeperClient(cache_dir="./demo/data/cache") as client:\n'
            "    fetch_and_write_projections_range(client, 2026, [1], db_path)\n\n"
            'projections = read_table(db_path, "sleeper_projections")\n'
            "players = read_sleeper_players(db_path)\n\n"
            "actuals = weekly_actuals(matchups, standings, league_id, 1)\n"
            "perf = weekly_performance(actuals, projections, players, league_cfg.scoring)\n"
            'perf.sort("delta", descending=True).select(\n'
            '    ["player_name", "position", "manager", "actual_points", "projected_points", "delta"]\n'
            ").head(3)"
        ),
        md(
            "`starters_only=True` (the default) excludes bench players. "
            "Pass a whole season's worth of weeks through `season_actuals` "
            "and `season_summary` (which gates on `min_games` so one huge "
            "single-week delta can't dominate a season ranking) for the "
            "same comparison averaged across a season instead of one week. "
            "`render_weekly_performance_table` and "
            "`render_season_performance_table` render either as a styled "
            "PNG table."
        ),
        md(
            teaching(
                "week 1 (or any very early week) of a season is the "
                "noisiest possible input for this comparison -- Sleeper's "
                "own projections haven't seen this season's real usage "
                "patterns yet, and a huge single-week delta is as likely to "
                "be projection error as it is real over/underperformance. "
                "Treat a single-week `report performance` the way this "
                "notebook's own numbers should be read: as \"what happened "
                'this week," not "who is actually good" -- '
                "`season_summary`'s `min_games` floor exists specifically "
                "to protect the season-long version of this question from "
                "that same noise."
            )
        ),
        md(
            whats_next(
                "This closes out the feature tour. "
                "`16_draft_companion_tools.ipynb` covers two smaller, newer "
                "library functions still under active development, and "
                "`17_querying_and_provenance.ipynb` ties everything in this "
                "tutorial back together -- one shared database, and how to "
                "record exactly how a dataset was built.\n\n"
                "## See Also\n\n"
                "- `13_league_history.ipynb` -- `weekly_results`, reused "
                "there for win/loss streaks.\n"
                "- [Wins and Leagues](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/15_wins_and_leagues.html) -- the "
                "matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 16_draft_companion_tools.ipynb
# ==========================================================================

ch16 = nb(
    [
        md(
            "# 16. In Progress: Draft-Day Matchup Tools\n\n"
            f"{NAV_HEADER} Run `15_wins_and_leagues.ipynb` first.\n\n"
            "`nuclearff.matchups` and `nuclearff.draft` are the start of a "
            "larger, still-unfinished feature area -- a live draft "
            "companion. Only two functions exist so far, each real and "
            "independently useful, but neither is wired into a report, a "
            "visualization, or the CLI yet. This notebook covers what's "
            "actually there today. Mirrors the [Draft Companion Tools]"
            "(https://nolmacdonald.github.io/nuclearff/tutorial/"
            "16_draft_companion_tools.html) docs page.\n\n"
            "**Both functions below are building blocks, not a finished "
            "feature** -- there is no report, no CLI command, and no "
            "visualization built on top of either yet, and the wider "
            "draft-companion epic they belong to ([GitHub issue #102]"
            "(https://github.com/nolmacdonald/nuclearff/issues/102)) covers "
            "a live draft-pick recommendation tool this is only the first "
            "piece of. Nothing here should be read as a finished, "
            "supported feature the way Chapters 5 through 15 are -- check "
            "the project's GitHub issues for current status before "
            "building on it."
        ),
        md(
            "## Defense-vs-position\n\n"
            "`points_allowed_by_position` aggregates real fantasy points "
            "allowed, by position, per NFL defense -- the raw signal "
            'behind "this team is a soft matchup for wide receivers":'
        ),
        code(
            "from nuclearff.config import load_league_config\n"
            "from nuclearff.matchups.dvp import points_allowed_by_position\n"
            "from nuclearff.nflverse import load_weekly_skill_stats\n"
            "from nuclearff.scoring import ScoringEngine\n\n"
            f'league_cfg = load_league_config("./demo/configs/leagues/{REDRAFT}.yaml")\n'
            "engine = ScoringEngine(league_cfg.scoring)\n\n"
            "weekly = load_weekly_skill_stats([2024])\n"
            "dvp = points_allowed_by_position(weekly, engine)\n"
            'dvp.filter(dvp["position"] == "WR").sort("points_allowed", descending=True).head(3)'
        ),
        md(
            "Points allowed are scored under this league's own rules "
            "(`05_scoring_engine.ipynb`), not a generic formula -- the same "
            "real game would score differently under a half-PPR league."
        ),
        md(
            "## Strength of schedule\n\n"
            "`strength_of_schedule` averages a team's *remaining* "
            "opponents' points-allowed at a position -- how favorable a "
            "draft candidate's rest-of-season schedule looks, given where "
            "they play:"
        ),
        code(
            "from nuclearff.draft.sos import strength_of_schedule\n"
            "from nuclearff.nflverse.schedules import load_schedules\n\n"
            "schedules = load_schedules([2024])\n"
            'sos = strength_of_schedule("SF", "WR", dvp, schedules, 2024, remaining_weeks=[15, 16, 17])\n'
            'print(f"49ers WR strength of schedule, weeks 15-17: {sos:.1f}")'
        ),
        md(
            "A higher number means those real remaining opponents allowed "
            "more real fantasy points to opposing WRs on average that "
            "season -- a softer remaining schedule for a wide receiver on "
            "that team than a lower number would indicate."
        ),
        md(
            teaching(
                "it's worth including a not-yet-finished feature area in a "
                "tutorial at all, rather than waiting until it's polished -- "
                "the two functions above are exactly as real and "
                "load-bearing as anything in Chapter 6's metrics, just not "
                "wired into a report yet. If you're extending this project "
                'yourself, this is a good place to see what "library '
                'primitive with no UI yet" looks like in practice, before '
                "deciding whether to build the report/CLI layer on top "
                "yourself."
            )
        ),
        md(
            whats_next(
                "`17_querying_and_provenance.ipynb` closes out this "
                "tutorial: one shared database for everything built across "
                "every chapter, and how to record exactly how a given "
                "dataset or report was produced.\n\n"
                "## See Also\n\n"
                "- `05_scoring_engine.ipynb` -- the `ScoringEngine` this "
                "chapter's DVP metric depends on.\n"
                "- [Draft Companion Tools](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/16_draft_companion_tools.html) -- the "
                "matching docs page.\n"
                "- [GitHub issue #102](https://github.com/nolmacdonald/"
                "nuclearff/issues/102) -- the wider draft-companion epic."
            )
        ),
    ]
)

# ==========================================================================
# 17_querying_and_provenance.ipynb
# ==========================================================================

ch17 = nb(
    [
        md(
            "# 17. Querying Everything, and Provenance\n\n"
            f"{NAV_HEADER} Run `16_draft_companion_tools.ipynb` first.\n\n"
            "Every chapter in this tutorial wrote into one shared DuckDB "
            "database. This closing notebook covers querying that database "
            "directly, the low-level helpers every `fetch_and_write_*` "
            "function is built on, and how to record exactly which code, "
            "config, and data produced a given result. Mirrors the "
            "[Querying and Provenance](https://nolmacdonald.github.io/"
            "nuclearff/tutorial/17_querying_and_provenance.html) docs page."
        ),
        md(
            "## Querying what you've built\n\n"
            "Every table from Chapters 4 through 16 lives in one file. "
            "Query it directly with DuckDB, or with `read_table` to get a "
            "`polars.DataFrame` back:"
        ),
        code(
            "import duckdb\n\n"
            "from nuclearff.duckdb_io import read_table\n\n"
            'db_path = "./demo/data/cache/nuclearff.duckdb"\n\n'
            "with duckdb.connect(db_path, read_only=True) as conn:\n"
            '    conn.sql("SHOW TABLES").show()'
        ),
        code('standings = read_table(db_path, "sleeper_standings")\nstandings.height'),
        md(
            "Every table is keyed by `league_id` (and usually `season`), "
            'so joining across them -- "which player did the eventual '
            'champion trade for" or "how many waiver claims did the team '
            'with the worst record make" -- is ordinary SQL, not custom '
            "Python for each question."
        ),
        md(
            "## The write primitives underneath\n\n"
            "`nuclearff.duckdb_io` has two ways to write a table, and the "
            "difference between them mattered enough to cause (and then "
            "fix) a real bug in this project. `replace_table` drops and "
            "rewrites a table wholesale -- correct for a genuine "
            "single-source snapshot like the Sleeper player map. "
            "`merge_table` replaces only the rows a given call actually "
            "owns, by key, leaving every other row untouched:"
        ),
        md(
            teaching(
                "the cell below writes to a throwaway `merge_table_demo` "
                "table, not the real `sleeper_matchups` this notebook "
                "series has been building all along -- unlike the "
                "docs page's illustrative-only code block, this notebook "
                "actually executes it, and running `merge_table` with real "
                "`key_values` against `sleeper_matchups` here would delete "
                "every real matchup row for that league_id and replace it "
                "with one fabricated row. Same mechanism, safe target."
            )
        ),
        code(
            "from nuclearff.duckdb_io import merge_table\n\n"
            "merge_table(\n"
            "    db_path,\n"
            '    "merge_table_demo",\n'
            '    create_table_sql="CREATE TABLE IF NOT EXISTS merge_table_demo '
            '(league_id VARCHAR, season INTEGER, week INTEGER, roster_id INTEGER, points DOUBLE)",\n'
            '    columns=["league_id", "season", "week", "roster_id", "points"],\n'
            f'    rows=[["{REDRAFT}", 2026, 2, 1, 118.4]],\n'
            '    key_column="league_id",\n'
            f'    key_values=["{REDRAFT}"],\n'
            ")"
        ),
        md(
            "**This distinction is not academic.** A real, "
            "previously-undetected bug in this exact project: every "
            "`fetch_and_write_*` function used to call `replace_table` with "
            "just one league's own rows -- which silently erased every "
            "*other* league's rows already in the same shared table, since "
            "`replace_table` always drops the whole table first. Confirmed "
            "live: fetching three unrelated leagues on the same account in "
            "sequence left only the last-fetched league's data surviving, "
            "with no error at any point. Every `fetch_and_write_*` function "
            "in Chapters 4, 10, 13, and 16 now uses `merge_table` instead -- "
            "if you're writing your own multi-league pipeline against a "
            "table more than one source populates, use `merge_table`, not "
            "`replace_table`."
        ),
        md(
            teaching(
                "this bug is worth sitting with for a moment, because it's "
                "a genuinely easy trap: `replace_table` looks completely "
                "correct in every single-league test, since there's only "
                "ever one league's data in the table to begin with. It only "
                "breaks the moment a *second* league shares the same "
                "database file -- exactly the situation this dashboard-style "
                "tutorial notebook is in right now, writing multiple real "
                "leagues (`NUCLEARFF REDRAFT`, `Freeman Forever League`) "
                "into the same `./demo` cache across its chapters. If you "
                "ever write a new `fetch_and_write_*`-style function for "
                "this project, `merge_table` is the default to reach for, "
                "not `replace_table`."
            )
        ),
        md(
            "## Provenance: recording how a result was built\n\n"
            "A number in a report is only as trustworthy as your ability "
            "to reproduce it. `nuclearff.provenance` records exactly what "
            "produced a given artifact: the git commit, whether the "
            "working tree was clean, the config that was used, and hashes "
            "of anything that matters."
        ),
        code(
            "from nuclearff.config import default_config\n"
            "from nuclearff.provenance import git_commit_sha, is_git_dirty, sha256_json\n\n"
            "cfg = default_config()\n"
            'print("commit:", git_commit_sha("."))\n'
            'print("dirty: ", is_git_dirty("."))\n'
            'print("config hash:", sha256_json(cfg.canonical_dict())[:16] + "...")'
        ),
        md(
            "`build_run_manifest` assembles all of this into one "
            "`RunManifest`, and `write_run_manifest` writes it as "
            "deterministic JSON:"
        ),
        code(
            "from datetime import UTC, datetime\n\n"
            "from nuclearff.provenance import build_run_manifest, write_run_manifest\n\n"
            "# write_run_manifest creates its target file exclusively (never\n"
            "# overwrites) -- a fixed run_id would collide the second time this\n"
            "# notebook is re-run, so it's timestamped instead.\n"
            'run_id = f"tutorial-demo-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"\n'
            'manifest = build_run_manifest(run_id=run_id, config=cfg, repo_root=".")\n'
            'path = write_run_manifest(manifest, "./demo/data/manifests")\n'
            "path"
        ),
        code(
            "import json\n\nprint(json.dumps(json.loads(path.read_text()), indent=2))"
        ),
        md(
            "`source_hashes` accepts `sha256_file` results for any raw "
            "input worth pinning (a downloaded CSV, a hand-authored "
            "context-deltas file) alongside the config hash -- enough, "
            'together, to answer "what exact code, config, and data '
            'produced this artifact" months later. `build_run_manifest` '
            "has no CLI command yet; it's a library primitive for a future "
            "pipeline to call."
        ),
        md(
            whats_next(
                "That's every feature this tutorial covers as a Python "
                "library. The final notebook, `18_cli_reference.ipynb`, "
                "covers the same functionality one more way -- as a single "
                "`nuclearff` command-line tool, for when you want a "
                "repeatable, scriptable command instead of a Python "
                "session.\n\n"
                "## See Also\n\n"
                "- Every prior notebook -- this one is a capstone over all "
                "of them, not new functionality of its own.\n"
                "- [Querying and Provenance](https://nolmacdonald.github.io/"
                "nuclearff/tutorial/17_querying_and_provenance.html) -- the "
                "matching docs page."
            )
        ),
    ]
)

# ==========================================================================
# 18_cli_reference.ipynb
# ==========================================================================

ch18 = nb(
    [
        md(
            "# 18. CLI Reference\n\n"
            f"{NAV_HEADER} Run `17_querying_and_provenance.ipynb` first.\n\n"
            "Notebooks 1 through 17 covered `nuclearff` as a Python "
            "library. Every one of those operations is also available as "
            "a single command-line tool, `nuclearff`, for when you want a "
            "repeatable, scriptable command instead of a Python session. "
            "This notebook is a complete reference for it -- nothing here "
            "introduces new functionality; it's the same library, wrapped. "
            "Every shell cell below runs `!uv run nuclearff ...` rather "
            "than a hardcoded venv path, so it always uses this repo's "
            "current source and synced environment. Mirrors the [CLI "
            "Reference](https://nolmacdonald.github.io/nuclearff/tutorial/"
            "18_cli_reference.html) docs page.\n\n"
            "Every command reads a configuration file "
            "(`configs/nuclearff.yaml` by default, or whatever "
            "`-c`/`--config` points at) and writes beneath `paths.root` "
            "(override with `--root`, which is how every example below "
            "keeps its output under `./demo` instead of a real project's "
            "data)."
        ),
        code("!uv run nuclearff --help"),
        md(
            "## `config` -- project configuration\n\n"
            "`init` writes a default configuration file (see "
            "`02_configuration.ipynb`'s `default_config`); `--force` "
            "overwrites an existing one. `show` prints the fully resolved "
            "configuration as YAML. `paths` prints every managed directory "
            "and whether it exists; `--ensure` creates any that are "
            "missing."
        ),
        code("!uv run nuclearff --root ./demo config paths --ensure"),
        code("!uv run nuclearff --root ./demo config show"),
        md(
            "## `sleeper` -- read-only Sleeper API access\n\n"
            "`fetch-league` is the CLI equivalent of "
            "`04_capturing_a_league.ipynb`'s functions, wired together in "
            "one call -- every flag below **implies** `--history` (you "
            "don't need to pass both), and each writes its own table "
            "without disturbing the others:"
        ),
        code(
            "!uv run nuclearff --root ./demo sleeper fetch-league "
            f"--league-id {REDRAFT} --history --standings --matchups "
            "--transactions --roster-players --drafts --max-week 3"
        ),
        md(
            "`--max-seasons` (default 20) caps how far `--history` walks "
            "back; `--max-week` (default 18) caps how many weeks "
            "`--matchups`/`--transactions` fetch per season.\n\n"
            "`fetch-players` writes the full Sleeper player map to DuckDB; "
            "`fetch-projections` fetches one or more weeks of Sleeper's "
            "own player projections. `user-leagues`/`user-drafts` resolve "
            "a username to their leagues or drafts for a season. "
            "`trending` prints the most-added or most-dropped players "
            "league-wide."
        ),
        code("!uv run nuclearff --root ./demo sleeper fetch-players"),
        code("!uv run nuclearff --root ./demo sleeper trending --kind add --limit 5"),
        code(
            "!uv run nuclearff --root ./demo sleeper user-leagues nolmacdonald "
            "--season 2026"
        ),
        md(
            "## `ids` -- cross-source identity resolution\n\n"
            "Fills missing Sleeper `gsis_id` values from the nflverse "
            "`ff_playerids` crosswalk (`04_capturing_a_league.ipynb`). "
            "Requires `fetch-players` to have run first."
        ),
        code("!uv run nuclearff --root ./demo ids resolve-gsis"),
        md(
            "## `report` -- draft boards, reports, and visualizations\n\n"
            "`auction-board` builds the full auction board "
            "(`09_auction_draft_board.ipynb`): CSV, markdown report, and "
            "PNG position tables (unless `--no-tables`). Uses the real "
            "auction league from that chapter, not the running "
            "snake-draft example."
        ),
        code(
            f"!uv run nuclearff --root ./demo report auction-board {AUCTION_LEAGUE} "
            "--seasons 2024 2025 --as-of-season 2026"
        ),
        md(
            "`playoff-bracket` renders a completed season's winners/losers "
            "playoff brackets (`10_draft_and_playoff_visuals.ipynb`). "
            "Requires `--standings` to have been run for that league and "
            "season first."
        ),
        code(
            f"!uv run nuclearff --root ./demo report playoff-bracket {PLAYOFF_LEAGUE} "
            '--season 2025 --league-name "NUCLEARFF REDRAFT"'
        ),
        md(
            "`draft-board` renders a draft as a snake-order grid "
            "(`10_draft_and_playoff_visuals.ipynb`). Omitting `--draft-id` "
            "uses the league's most recent draft."
        ),
        code(f"!uv run nuclearff --root ./demo report draft-board {REDRAFT}"),
        md(
            "`trades` renders all ten trade-history visualizations "
            "(`14_trade_network.ipynb`); `wins` renders the "
            "cumulative-wins step chart (`15_wins_and_leagues.ipynb`); "
            "`draft-order` renders a manager's draft-slot history "
            "(`10_draft_and_playoff_visuals.ipynb`). All three default to "
            "only managers currently rostered in `league_id`'s own season "
            "-- `--all-users` includes every manager across the league's "
            "full history instead."
        ),
        code(f"!uv run nuclearff --root ./demo report trades {REDRAFT} --all-users"),
        code(f"!uv run nuclearff --root ./demo report wins {REDRAFT} --all-users"),
        code(
            f"!uv run nuclearff --root ./demo report draft-order {REDRAFT} --all-users"
        ),
        md(
            "`on-this-day` renders transactions matching today's (or a "
            "given) calendar-date anniversary, across a league's full "
            "history (`13_league_history.ipynb`)."
        ),
        code(f"!uv run nuclearff --root ./demo report on-this-day {REDRAFT}"),
        md(
            "`performance`/`season-performance` render actual-vs-projected "
            "over/underperformer tables for one week or a full season "
            "(`15_wins_and_leagues.ipynb`'s \"Actual vs. projected "
            'performance"). Both default to starters only -- '
            "`--all-players` includes bench players. `season-performance`'s "
            "`--min-games` (default 3) keeps a single huge-delta week from "
            "dominating a season ranking."
        ),
        code(f"!uv run nuclearff --root ./demo report performance {REDRAFT} --week 1"),
        md(
            "`user-leagues` renders a Sleeper user's leagues for a season "
            "as a PNG table (`15_wins_and_leagues.ipynb`)."
        ),
        code(
            "!uv run nuclearff --root ./demo report user-leagues nolmacdonald "
            "--season 2026"
        ),
        md(
            teaching(
                "every command above is a thin wrapper over exactly the "
                "same Python functions the first 17 notebooks called "
                "directly -- `report trades` is `load_trades` + "
                "`manager_trade_counts` + the ten `render_*` calls from "
                "`14_trade_network.ipynb`, nothing more. If a command's "
                "output ever surprises you, the fastest way to understand "
                "why is to go read (or re-run, cell by cell) the matching "
                "chapter's Python instead of guessing from the flag names "
                "-- every wiring decision is visible in code there, not "
                "hidden behind a flag."
            )
        ),
        md(
            whats_next(
                "That's every `nuclearff` command, and the close of this "
                "notebook series. If a command's behavior isn't clear from "
                "its flags, the corresponding earlier notebook walks "
                "through the same functionality as plain Python -- often "
                "the faster way to understand *why* a command produces "
                "what it does.\n\n"
                "## See Also\n\n"
                "- `01_introduction.ipynb` -- the start of this series.\n"
                "- [CLI Reference](https://nolmacdonald.github.io/nuclearff/"
                "tutorial/18_cli_reference.html) -- the matching docs page.\n"
                "- [API Reference](https://nolmacdonald.github.io/nuclearff/"
                "api/index.html) -- full reference for every public class "
                "and function used throughout this tutorial."
            )
        ),
    ]
)

# ==========================================================================
# Write every notebook
# ==========================================================================

for notebook, filename in [
    (ch01, "01_introduction.ipynb"),
    (ch02, "02_configuration.ipynb"),
    (ch03, "03_sleeper_api.ipynb"),
    (ch04, "04_capturing_a_league.ipynb"),
    (ch05, "05_scoring_engine.ipynb"),
    (ch06, "06_metrics.ipynb"),
    (ch07, "07_projections.ipynb"),
    (ch08, "08_valuation.ipynb"),
    (ch09, "09_auction_draft_board.ipynb"),
    (ch10, "10_draft_and_playoff_visuals.ipynb"),
    (ch11, "11_simulation.ipynb"),
    (ch12, "12_backtesting.ipynb"),
    (ch13, "13_league_history.ipynb"),
    (ch14, "14_trade_network.ipynb"),
    (ch15, "15_wins_and_leagues.ipynb"),
    (ch16, "16_draft_companion_tools.ipynb"),
    (ch17, "17_querying_and_provenance.ipynb"),
    (ch18, "18_cli_reference.ipynb"),
]:
    write(notebook, filename)
