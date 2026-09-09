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

Playoff bracket visualization
----------------------------------

``report playoff-bracket`` renders a completed season's winners and losers
playoff brackets as horizontal tree PNGs, from the ``sleeper_playoff_matches``
and ``sleeper_standings`` data ``--standings`` writes above:

.. code-block:: text

   $ nuclearff --root ./demo report playoff-bracket 1240509989819273216 \
       --season 2025 --league-name "NUCLEARFF REDRAFT"
   Winners bracket: ./demo/data/artifacts/1240509989819273216-2025/brackets/winners_bracket.png
   Losers bracket: ./demo/data/artifacts/1240509989819273216-2025/brackets/losers_bracket.png

That's this league's real, completed 2025 season: 7 winners-bracket matches
and 4 losers-bracket matches, each rendered as its own figure (Sleeper treats
the two brackets as visually distinct, and so does this). Each leaf/node is
labeled with the real team display name, not a bare roster id; the winner of
each match renders bold; and a match carrying a placement (the championship,
the third-place game, and similar) appends the resulting rank, e.g.
``nolmacdonald (1st)``.

A Sleeper bracket isn't a similarity-clustering dendrogram — it's a
fixed-shape single-elimination tree keyed by ``t1_from``/``t2_from`` match
references, so a later round's participant resolves through an earlier
match's *winner or loser* rather than a fresh pair of roster ids. That
includes a real wrinkle this league's own bracket has: the championship and
the third-place game split from the exact same pair of semifinal matches,
landing on the same computed tree position — handled by nudging the two
apart by their own box height so neither the lines nor the labels overlap.

``league_id``/``--season`` select the data (required — run ``--standings``
for that league and season first if this comes back with no matches).
``--league-name`` is cosmetic, used only in each figure's title.
``--out-dir`` overrides the default output location,
``<artifacts>/<league_id>-<season>/brackets/``.

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

Draft board visualization
------------------------------

``report draft-board`` renders a draft as a snake-order grid of
position-colored pick cards — matching Sleeper's own draft-room UI: one
column per draft slot (team), one row per round.

.. code-block:: text

   $ nuclearff --root ./demo report draft-board 1367225133634191360
   Picks:       150
   Draft board: ./demo/data/artifacts/1367225133634191360-draft-board/1367225133646778368.png

That's this league's real, currently in-progress 2026 draft: 150 real picks
rendered so far, across 10 columns and 15 rounds. Each cell shows the pick's
position and NFL team, the pick number (e.g. ``3.10`` — round 3, draft slot
10), and the player's name on two lines (first name, then bold last name).
Cell color follows position: green for RB, blue for WR, pink for QB, orange
for TE — confirmed against Sleeper's own draft-room UI; any other position
falls back to a neutral gray rather than guessing a color that hasn't been
seen in a real league yet.

A draft's pick order isn't always a simple alternating snake. This league's
own draft has ``settings.reversal_round: 3`` — round 3 continues round 2's
column direction instead of reversing back to round 1's. The grid doesn't
compute pick order itself: each pick's column is its own real ``draft_slot``,
which Sleeper has already resolved correctly, reversal round included.

``--draft-id`` selects which draft to render; omit it and the league's most
recent draft is used automatically (via ``sleeper user-drafts``' same
underlying lookup). ``--out`` overrides the default output location,
``<artifacts>/<league_id>-draft-board/<draft_id>.png``. Picks are also
persisted to a ``sleeper_draft_picks`` DuckDB table as a side effect, so
"who took which player, and when" is a query away without re-rendering
anything.

Rendering an in-progress draft (not every round complete yet) is the normal
case, not an error — the grid simply draws however many picks exist so far.

Trade network analysis
------------------------------

``report trades`` renders the manager trade network from stored trade
history — currently a bar chart of total trades per manager, a manager-pair
heatmap, a node-link network graph, a leaderboard table, and a manager-pair
leaderboard, with others landing as later issues on top of the same command:

.. code-block:: text

   $ nuclearff --root ./demo report trades 1367225133634191360
   Managers:                 15
   Trades by manager:        ./demo/data/artifacts/1367225133634191360-trades/trades_by_manager.png
   Trades heatmap:           ./demo/data/artifacts/1367225133634191360-trades/trades_heatmap.png
   Trade network:            ./demo/data/artifacts/1367225133634191360-trades/trade_network.png
   Trade leaderboard:        ./demo/data/artifacts/1367225133634191360-trades/trade_leaderboard.png
   Manager-pair leaderboard: ./demo/data/artifacts/1367225133634191360-trades/manager_pair_leaderboard.png

That's this league's real trade history: 22 completed trades since 2021,
across the 15 managers who have ever held a roster in the league.
``nolmacdonald`` (14 trades) is the league's most active trader by a wide
margin; five managers have never made one.

Two real details from this exact data are worth knowing before reading the
chart:

- **A manager's bar counts distinct trades, not trade relationships.**
  Sleeper allows more than two rosters in a single trade, and this league
  has a real one — a 2022 three-team trade among ``casitzmann``,
  ``nolmacdonald``, and ``nolanmacdonald``. Each of those three managers'
  bars counts it once, not twice, even though it touches two other managers
  each.
- **A manager needs a resolvable Sleeper display name to appear at all.**
  Two of this league's 22 real trades have a roster whose owner isn't in
  that season's user list — a real (if rare) Sleeper data inconsistency,
  not a bug here — so neither trade contributes to any manager's count.
- **Manager identity is a display name, not a stable id**, and this
  league's data shows exactly why that matters: ``nolmacdonald`` (14
  trades) and ``nolanmacdonald`` (2 trades, including the three-team trade
  above) render as two separate bars. Nothing here merges them
  automatically — reading the chart correctly means knowing your own
  league's naming history.

``sleeper_transactions`` (written by ``--transactions`` above) supplies the
trades; ``sleeper_standings`` (written by ``--standings``) supplies the
*full* manager roster, so a manager with zero trades still shows up at
``0`` instead of being silently missing. Skip ``--standings`` and the chart
still renders — it just can't include a manager who never traded, since
trade data alone gives no way to know they exist. The same roster join
applies to the heatmap below: a manager with zero trades still gets a
dense, all-zero row and column rather than being omitted from the grid.

The heatmap is symmetric — trades between ``casitzmann`` and
``nolmacdonald`` show as ``4`` in both directions — with a ``0`` diagonal,
since a manager can't trade with themselves. The three-team trade mentioned
above lands as one ``+1`` for each of the three pairs it touches
(``casitzmann``-``nolmacdonald``, ``casitzmann``-``nolanmacdonald``,
``nolmacdonald``-``nolanmacdonald``), not counted twice for any single pair.

The network graph draws one node per manager (sized by their total trades)
and one edge per manager pair that has traded (widened by the trade count
between that pair) — the same densified, full-roster inputs as the bar
chart and heatmap, so a zero-trade manager still appears, here as an
isolated node rather than being silently dropped. With only 22 trades
across 15 managers, the graph is genuinely sparse — several managers never
connect to the rest of the league at all. That is this league's real
trading activity, not a rendering bug, and the chart says so directly.

The leaderboard table is one reference row per manager — Trades, Unique
Partners, Most Frequent Partner, Trades With Partner — sorted by trade
count, most active first. It has no circle-cropped headshots unlike
``nuclearff``'s player tables: a Sleeper manager has no headshot URL
anywhere in this project's data model, only an avatar id nothing currently
resolves. A zero-trade manager still gets a full row rather than being
omitted, with ``—`` in place of a partner that doesn't exist.

The manager-pair leaderboard is a horizontal bar chart of the top 10 manager
pairs by trade count, labeled ``Manager A ↔ Manager B``. Unlike the heatmap
(dense over every manager, including zero-trade pairs) it only shows pairs
that actually traded — with a real 22-trade history, that's 10 pairs across
15 managers, most tied at a single trade. Each pair appears once: Sleeper
trade rows are exploded into manager-pair edges with the two names already
sorted alphabetically, so grouping directly on them can never produce both
an A↔B and a B↔A row for the same pair.

``--out-dir`` overrides the default output location,
``<artifacts>/<league_id>-trades/``.

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
   │ sleeper_draft_picks         │
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
