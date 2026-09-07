.. _user_guide:

User Guide
============

This page walks through every ``nuclearff`` CLI command and what it builds,
in the order you'd actually reach for them: capture a league, pull its
history, and turn that into standings, matchups, transactions, and roster
data you can query. Every example below ran against a real league
(``1367225133634191360``, ``NUCLEARFF REDRAFT``) while writing this page;
your own league's numbers will differ, but the shape of the output won't.

If you haven't installed ``nuclearff`` yet, start with :doc:`getting_started`.
For the Sleeper API itself, independent of the CLI, see
:doc:`sleeper_api_tutorial`.

Command groups
-----------------

.. code-block:: text

   $ nuclearff --help
   positional arguments:
     <group>
       config              Inspect and create configuration
       sleeper             Read-only Sleeper API access
       ids                 Cross-source player identity resolution
       report              Build draft boards and reports

Every command reads a configuration file (``configs/nuclearff.yaml`` by
default, or whatever ``-c``/``--config`` points at) and writes beneath
``paths.root`` (override with ``--root``, which is how every example on this
page keeps its output out of your real project data)::

   nuclearff config init -o /path/to/nuclearff.yaml

Capturing a league
---------------------

The starting point for everything else is a league snapshot:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360
   League:   NUCLEARFF REDRAFT (1367225133634191360)
   Season:   2026 status=pre_draft
   Teams:    10
   Snapshot: ./demo/data/raw/sleeper/1367225133634191360/20260907T030103Z
   League config: ./demo/configs/leagues/1367225133634191360.yaml

   Settings to review:
     [WARNING] draft_rounds_mismatch: League settings report draft_rounds=3 but the draft object reports rounds=15.
              -> Trust the draft object. The league field is stale; use 15 rounds for pick-gap and VONA math.
     [WARNING] keepers_in_redraft: League type is 0 (redraft) but max_keepers=1.
              -> Treat the league as redraft, but confirm in the Sleeper UI whether a keeper is actually in play.
     [INFO] median_scoring: Each team also plays the weekly league median (league_average_match=1).
              -> Weight weekly floor more heavily than ceiling when breaking ties between players.
     [INFO] no_kicker_or_defense: No kicker, defense, or IDP roster slots.
              -> Kicker and defensive scoring keys in the payload are inert for lineup construction.

This writes every raw endpoint to an immutable, timestamped directory (never
overwritten — a second run creates a new timestamp) and a typed
``LeagueConfig`` YAML file derived from the league's scoring and roster
settings. The "Settings to review" block is
:func:`~nuclearff.sleeper.snapshot.detect_anomalies` flagging settings that
are internally inconsistent or otherwise worth a second look before you
trust them for valuation.

On its own, ``fetch-league`` covers *this* season only. Every flag below adds
more history and more data, and each one **implies** ``--history`` — you
don't need to pass both.

Multi-season history
-----------------------

Sleeper links a league to its prior season via ``previous_league_id``.
``--history`` walks that chain all the way back and writes it to a local
DuckDB database (``<cache_dir>/nuclearff.duckdb``):

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 --history
   ...
   History:  6 season(s) walked
   Configs:  6/6 parsed into LeagueConfig
   Database: ./demo/data/cache/nuclearff.duckdb

For this league, that reaches all the way back to its 2021 inception — six
real seasons, not a synthetic sample. Two tables land in DuckDB:
``sleeper_leagues`` (the raw settings/scoring/roster JSON per season) and
``sleeper_league_configs`` (the same data, typed and flattened). A season
that fails to parse into a ``LeagueConfig`` keeps its raw row and just skips
the typed one, rather than losing the whole hop; the walk itself stops
cleanly at the end of the chain, not with an error.

``--max-seasons`` caps how far back it walks (default: 20). You'll rarely
need it — the cap exists to guard against a malformed or cyclic chain, not
because 20 seasons is a realistic league history.

Standings and playoff results
---------------------------------

``--standings`` computes, per season, each roster's regular-season record
*and* where they actually finished after the playoffs:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 --standings
   ...
   Standings: 60 roster-season(s)
   Playoffs:  66 bracket match(es)

Those numbers are real: 6 seasons times 10 rosters. What makes this worth
having is that regular-season record and final standing are genuinely
different things. In this league's real 2025 season, the team with the best
regular-season record (20-8) finished **4th** — the eventual champion had
fewer wins (16-12) and won it in the playoffs. Query both columns to see it:

.. code-block:: python

   import duckdb

   conn = duckdb.connect("./demo/data/cache/nuclearff.duckdb", read_only=True)
   conn.execute("""
       SELECT display_name, wins, losses, regular_season_rank, final_rank
       FROM sleeper_standings
       WHERE league_id = '1240509989819273216'
       ORDER BY COALESCE(final_rank, 999)
   """).fetchall()

Final placement is deliberately conservative: it's computed only from
winners-bracket matches that carry a ``p`` (placement) field — a match's
winner gets rank ``p``, its loser ``p + 1``, confirmed unambiguous against
real data. The losers bracket's own placement field is captured raw in
``sleeper_playoff_matches`` but never turned into a guessed ``final_rank``,
because its numbering convention couldn't be confirmed against real data.
Rosters that only reached the losers bracket show ``final_rank = NULL``
rather than a number that might be wrong.

Weekly matchups
------------------

``--matchups`` fetches every week's roster-vs-roster scoring, for every
season in the chain:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 --matchups --max-week 3
   ...
   Matchups: 150 roster-week row(s)

(150 = 5 populated seasons times 3 weeks times 10 rosters — the current,
pre-draft season correctly contributes zero, since it has no games yet.)
Each row in ``sleeper_matchups`` carries points, the matchup id pairing two
rosters together, and JSON columns for ``players``/``starters``/
``players_points`` — a full weekly box score per roster.

``--max-week`` caps how many weeks are fetched per season (default: 18, a
full regular + postseason). Weeks that haven't happened yet return no data
and are silently skipped, not treated as an error.

Transaction history
-----------------------

``--transactions`` fetches every week's adds, drops, waivers, and trades,
across the full history chain:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 --transactions --max-week 3
   ...
   Transactions: 349
   Add/drop rows: 523

Sleeper's own ``type`` and ``status`` fields are trusted and stored as-is,
not re-derived — and it's worth trusting them literally: a full-history
fetch against this league surfaced four distinct transaction types
(``waiver``, ``free_agent``, ``trade``, and a ``commissioner`` type not seen
in a smaller single-season sample), all handled correctly with no code
change needed, because the type is never validated against a fixed set.

.. code-block:: python

   conn.execute("SELECT type, COUNT(*) FROM sleeper_transactions GROUP BY type").fetchall()
   # [('waiver', 245), ('free_agent', 99), ('trade', 5)]

Two tables land in DuckDB: ``sleeper_transactions`` (one row per
transaction — ``creator``/``roster_ids``/``consenter_ids`` resolved to
display names alongside the raw ids, ``created``/``status_updated`` parsed
from Sleeper's epoch-millisecond timestamps to real UTC ``TIMESTAMP``
values) and ``sleeper_transaction_players`` (``adds``/``drops`` unnested to
one row per player per direction, so "how many players did this roster add
this season" is a plain ``COUNT(*)``, not JSON parsing).

Roster composition
----------------------

``--roster-players`` categorizes every roster's players by slot, per season:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 --roster-players
   ...
   Roster players: 791

.. code-block:: python

   conn.execute("SELECT slot, COUNT(*) FROM sleeper_roster_players GROUP BY slot").fetchall()
   # [('bench', 298), ('reserve', 43), ('starter', 450)]

Each row is a ``(league_id, season, roster_id, player_id)`` with a ``slot``
of ``starter``, ``reserve`` (IR), ``taxi``, or ``bench`` — join it to
``sleeper_players`` (below) for the player's name and position. Sleeper
fills an empty starting slot with the literal string ``"0"`` before a draft;
that filler is dropped rather than stored as a phantom player.

Combining flags
-------------------

Every flag above implies ``--history`` and is independent of the others —
combine as many as you want in one run, and each writes its own table(s)
without disturbing the rest:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 \
       --history --standings --matchups --max-week 3
   ...
   History:  6 season(s) walked
   Configs:  6/6 parsed into LeagueConfig
   Database: ./demo/data/cache/nuclearff.duckdb

   Standings: 60 roster-season(s)
   Playoffs:  66 bracket match(es)

   Matchups: 150 roster-week row(s)

Every one of these writes replaces its own table wholesale — a fresh run is
a fresh full snapshot, not an incremental append. Run the flags you need
together; there's no ordering requirement between them.

Discovering leagues from a username
---------------------------------------

Every command above needs an already-known ``league_id``. If you only have
a Sleeper username, resolve it first:

.. code-block:: text

   $ nuclearff --root ./demo sleeper user-leagues nolmacdonald --season 2026
   User:    nolmacdonald (332632476830679040)
   Leagues: 18 for nfl 2026
     1397668648528650240  Antares Sunday Operations  status=in_season
     1389346819275771904  Backpack Boyz  status=in_season
     ...

``sleeper user-drafts <username> --season <year>`` works the same way for
drafts instead of leagues. Both resolve the username to a ``user_id`` first
(via ``GET /v1/user/<username>``), then print the display name alongside the
numeric id — the same resolution happens whether you pass a username or a
raw numeric id, so either works.

The player map, filtering, and trending
--------------------------------------------

``fetch-players`` pulls Sleeper's full NFL player map (every player it
knows, roughly 5 MB) into a ``sleeper_players`` DuckDB table — this is the
join target for every ``player_id`` column on this page:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-players
   Players:  12226
   Database: ./demo/data/cache/nuclearff.duckdb

The full map is cached to disk for 24 hours (Sleeper's own guidance for how
often to re-fetch it); ``--force-refresh`` bypasses that. If you only need
part of it, filter server-side in Python instead of fetching everything:

.. code-block:: python

   from nuclearff.sleeper import SleeperClient

   with SleeperClient() as client:
       active_qbs = client.get_players(position="QB", active=True)

Confirmed live, that cuts the payload from roughly 14.6 MB to 435 KB. A
filtered call always hits the network — the disk cache is specifically for
the full unfiltered map.

``sleeper trending`` shows the most-added or most-dropped players league-wide
right now, resolved to real names when a local player table exists (falling
back to the raw id with a hint to run ``fetch-players`` first otherwise):

.. code-block:: text

   $ nuclearff --root ./demo sleeper trending --limit 5
   Trending add (last 24h):
     Roschon Johnson              count=208336
     Devaughn Vele                count=112371
     Tank Dell                    count=88792
     Las Vegas Raiders             count=75882
     MarShawn Lloyd                count=59373

(``--kind drop``, ``--lookback-hours``, and ``--limit`` all adjust the
query.) Note that team defenses — like "Las Vegas Raiders" above — resolve
through a first/last-name fallback: Sleeper's real payload gives a defense
entry no ``full_name`` field, only split ``first_name``/``last_name``.

Cross-referencing to nflverse
----------------------------------

Sleeper player objects already carry several cross-platform IDs, including
``gsis_id`` — nflverse's own primary key — but not every player has one.
``ids resolve-gsis`` fills the gap from nflverse's own ID crosswalk:

.. code-block:: text

   $ nuclearff --root ./demo ids resolve-gsis
   Players:                   12226
   gsis_id from Sleeper:      3893
   gsis_id from ff_playerids: 3522
   Still unresolved:          4811

   Skipped 6 ambiguous crosswalk sleeper_id value(s) (maps to more than one player; not used to fill gaps).

This requires ``fetch-players`` to have run first (it reads the
``sleeper_players`` table). The result is a ``player_id_map`` table
recording each player's best-known ``gsis_id`` and where it came from —
Sleeper's own field, or the crosswalk. A crosswalk row whose ``sleeper_id``
maps to more than one player is skipped rather than guessed; most of what's
still unresolved after both sources is players missing from this year's
crosswalk entirely, typically rookies.

Auction/keeper draft valuation
-----------------------------------

If your league runs (or ran) an auction draft, ``report auction-board``
turns realized fantasy points into a priced draft board:

.. code-block:: text

   $ nuclearff report auction-board <league_id> --seasons 2023 2024 2025 --as-of-season 2026

Its options, from ``--help``:

.. code-block:: text

   positional arguments:
     league_id             Sleeper league identifier (must have an auction draft)

   options:
     --seasons SEASONS [SEASONS ...]
                           Historical seasons to score, oldest first
     --as-of-season AS_OF_SEASON
                           The season being drafted for
     --baseline {vols,vorp}
                           Replacement baseline (default: vols)
     --top TOP             Players per position table (default: 12)
     --out-dir OUT_DIR     Output directory
     --no-tables           Skip PNG tables (avoids the plottable/matplotlib dev extra)

This pipeline — recency-weighted realized points, per-position replacement
level and VORP, FantasyPros consensus join, dollars that sum to exactly the
league's real budget — was verified end-to-end against a real auction
league. ``NUCLEARFF REDRAFT``, this page's running example, runs a snake
draft, so it isn't a usable demo league for this particular command; point
it at a league whose ``draft.type`` is ``"auction"`` instead. Output is a
CSV, a markdown report with methodology notes, and (unless ``--no-tables``)
a styled PNG table per position.

Keeper cost adjustment (``keeper_inflation_multiplier``,
``keeper_adjusted_values``) is implemented but not yet wired into this
command — Sleeper exposes no keeper-price endpoint, so keeper costs must be
supplied by the caller.

Querying what you've built
-------------------------------

Every table on this page lives in one DuckDB file
(``<cache_dir>/nuclearff.duckdb``, printed by every command that writes to
it). Query it directly with the ``duckdb`` CLI, or from Python:

.. code-block:: python

   import duckdb

   conn = duckdb.connect("./demo/data/cache/nuclearff.duckdb", read_only=True)
   conn.sql("SHOW TABLES").show()

.. code-block:: text

   ┌─────────────────────────────┐
   │            name             │
   │           varchar           │
   ├─────────────────────────────┤
   │ sleeper_league_configs      │
   │ sleeper_leagues             │
   │ sleeper_matchups            │
   │ sleeper_players             │
   │ sleeper_playoff_matches     │
   │ sleeper_roster_players      │
   │ sleeper_standings           │
   │ sleeper_transaction_players │
   │ sleeper_transactions        │
   └─────────────────────────────┘

(the full set, once every flag on this page has been run at least once —
your own database only has tables for the flags you've actually used).

Every table is keyed by ``league_id`` (and usually ``season``), so joining
across them — "which player did the eventual champion trade for" or "how
many waiver claims did the team with the worst record make" — is ordinary
SQL, not custom Python for each question.

See Also
----------

- :doc:`getting_started` — installation and configuration.
- :doc:`sleeper_api_tutorial` — the Sleeper API itself, and using
  ``SleeperClient`` directly in Python rather than through the CLI.
- :doc:`api/index` — full reference for every public class and function.
