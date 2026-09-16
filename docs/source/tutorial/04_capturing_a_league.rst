.. _tutorial_capturing_a_league:

4. Capturing and Storing a League
========================================

:doc:`03_sleeper_api` called ``SleeperClient`` directly for one-off lookups.
This chapter builds a *complete, reproducible* dataset for a league: an
immutable raw snapshot, full multi-season history, standings and playoff
results, weekly matchups, transactions, and roster composition — all
persisted to disk so the rest of this tutorial (and your own analysis) can
query them without hitting Sleeper again.

Everything below writes into one shared DuckDB database
(``<cache_dir>/nuclearff.duckdb``) and one raw-snapshot directory
(``<raw_dir>``). This chapter uses a scratch ``./demo`` directory throughout
so nothing here touches your real project data — see
:doc:`02_configuration`'s ``PathsConfig``.

Every example below ran against a real league
(``1367225133634191360``, ``NUCLEARFF REDRAFT``) while writing this page;
your own league's numbers will differ, but the shape of the output won't.

A league snapshot
---------------------

:func:`~nuclearff.sleeper.snapshot.fetch_league_snapshot` fetches every
read-only endpoint that describes a league — settings, users, rosters,
drafts — into one :class:`~nuclearff.sleeper.models.LeagueSnapshot`, and
:func:`~nuclearff.sleeper.snapshot.write_snapshot` writes it to an
immutable, timestamped directory (never overwritten — a second run creates
a new timestamp):

.. code-block:: python

   from nuclearff.sleeper import SleeperClient, fetch_league_snapshot, write_snapshot

   league_id = "1367225133634191360"
   raw_dir = "./demo/data/raw"

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       snapshot = fetch_league_snapshot(client, league_id)
       target = write_snapshot(snapshot, raw_dir)

   print(f"League:   {snapshot.league_name} ({snapshot.metadata.league_id})")
   print(f"Season:   {snapshot.league.get('season')} status={snapshot.league.get('status')}")
   print(f"Teams:    {snapshot.league.get('total_rosters')}")
   print(f"Snapshot: {target}")

.. code-block:: text

   League:   NUCLEARFF REDRAFT (1367225133634191360)
   Season:   2026 status=in_season
   Teams:    10
   Snapshot: demo/data/raw/sleeper/1367225133634191360/20260916T220712Z

A snapshot also carries ``anomalies`` — league settings that are internally
inconsistent or otherwise worth a second look before trusting them for
valuation, from :func:`~nuclearff.sleeper.snapshot.detect_anomalies`:

.. code-block:: python

   for anomaly in snapshot.anomalies:
       print(f"[{anomaly.severity.upper()}] {anomaly.code}: {anomaly.message}")
       print(f"  -> {anomaly.action}")

.. code-block:: text

   [WARNING] draft_rounds_mismatch: League settings report draft_rounds=3 but the draft object reports rounds=15.
     -> Trust the draft object. The league field is stale; use 15 rounds for pick-gap and VONA math.
   [WARNING] keepers_in_redraft: League type is 0 (redraft) but max_keepers=1.
     -> Treat the league as redraft, but confirm in the Sleeper UI whether a keeper is actually in play.
   [INFO] median_scoring: Each team also plays the weekly league median (league_average_match=1).
     -> Weight weekly floor more heavily than ceiling when breaking ties between players.
   [INFO] no_kicker_or_defense: No kicker, defense, or IDP roster slots.
     -> Kicker and defensive scoring keys in the payload are inert for lineup construction.

Real, contradictory settings like these are exactly why ``fetch_league_snapshot``
surfaces them up front rather than letting a downstream valuation quietly
use the wrong number.

Multi-season history
-----------------------

Sleeper links a league to its prior season via ``previous_league_id``.
:func:`~nuclearff.sleeper.leagues.walk_league_chain` walks that chain all
the way back, and :func:`~nuclearff.sleeper.leagues.write_league_tables`
persists it to DuckDB:

.. code-block:: python

   from nuclearff.sleeper import walk_league_chain, write_league_tables

   db_path = "./demo/data/cache/nuclearff.duckdb"

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       leagues = walk_league_chain(client, league_id, max_seasons=20)
       raw_count, config_count = write_league_tables(leagues, db_path)

   print(f"History:  {len(leagues)} season(s) walked")
   print(f"Configs:  {config_count}/{raw_count} parsed into LeagueConfig")

.. code-block:: text

   History:  6 season(s) walked
   Configs:  6/6 parsed into LeagueConfig

For this league, that reaches all the way back to its 2021 inception — six
real seasons, not a synthetic sample. Two tables land in DuckDB:
``sleeper_leagues`` (the raw settings/scoring/roster JSON per season) and
``sleeper_league_configs`` (the same data, typed and flattened via
:class:`~nuclearff.config.league.LeagueConfig`). A season that fails to
parse keeps its raw row and just skips the typed one, rather than losing
the whole hop; the walk itself stops cleanly at the end of the chain, not
with an error. ``max_seasons`` (default 20) guards against a malformed or
cyclic chain — you'll rarely need to change it.

Every function below takes that same ``leagues`` list — resolve the chain
once, then fetch as many kinds of data against it as you want.

Standings and playoff results
---------------------------------

:func:`~nuclearff.sleeper.standings.fetch_and_write_standings` computes,
per season, each roster's regular-season record *and* where they actually
finished after the playoffs:

.. code-block:: python

   from nuclearff.sleeper import fetch_and_write_standings

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       standings_count, matches_count = fetch_and_write_standings(client, leagues, db_path)

   print(f"Standings: {standings_count} roster-season(s)")
   print(f"Playoffs:  {matches_count} bracket match(es)")

.. code-block:: text

   Standings: 60 roster-season(s)
   Playoffs:  66 bracket match(es)

Those numbers are real: 6 seasons times 10 rosters. What makes this worth
having is that regular-season record and final standing are genuinely
different things. Query both columns with :func:`~nuclearff.duckdb_io.read_table`
or ``duckdb`` directly:

.. code-block:: python

   import duckdb

   conn = duckdb.connect(db_path, read_only=True)
   conn.execute("""
       SELECT display_name, wins, losses, regular_season_rank, final_rank
       FROM sleeper_standings
       WHERE league_id = '1240509989819273216'
       ORDER BY COALESCE(final_rank, 999)
   """).fetchall()

.. code-block:: python

   [('nolmacdonald', 16, 12, 2, 1), ('ksavabi', 14, 14, 6, 2),
    ('nawfeastdallas', 15, 13, 5, 3), ('casitzmann', 20, 8, 1, 4),
    ('thatbolb', 16, 12, 4, 5), ('jwhitney0220', 16, 12, 3, 6),
    ('hyoga10', 11, 17, 9, None), ('aperry151', 11, 17, 8, None),
    ('Donkeysride', 12, 16, 7, None), ('ruhbberduhcky', 9, 19, 10, None)]

In this league's real 2025 season, the team with the best regular-season
record (``casitzmann``, 20-8) finished **4th** — the eventual champion
(``nolmacdonald``) had fewer wins (16-12) and won it in the playoffs.

Final placement is deliberately conservative: it's computed only from
winners-bracket matches that carry a ``p`` (placement) field via
:func:`~nuclearff.sleeper.standings.resolve_final_ranks` — a match's winner
gets rank ``p``, its loser ``p + 1``. The losers bracket's own placement
field is captured raw in ``sleeper_playoff_matches`` but never turned into a
guessed ``final_rank``, because its numbering convention couldn't be
confirmed against real data — rosters that only reached the losers bracket
show ``final_rank = NULL`` rather than a number that might be wrong. (A
Sleeper "Chopped" league has no playoff bracket at all;
:func:`~nuclearff.sleeper.standings.is_chopped_league` and
:func:`~nuclearff.sleeper.standings.resolve_chopped_final_ranks` handle that
case from the elimination order instead.)

Weekly matchups
------------------

:func:`~nuclearff.sleeper.matchups.fetch_and_write_matchups` fetches every
week's roster-vs-roster scoring, for every season in the chain:

.. code-block:: python

   from nuclearff.sleeper import fetch_and_write_matchups

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       matchup_count = fetch_and_write_matchups(client, leagues, db_path, max_week=3)

   print(f"Matchups: {matchup_count} roster-week row(s)")

.. code-block:: text

   Matchups: 180 roster-week row(s)

(180 = 6 seasons times 3 weeks times 10 rosters.) Each row in
``sleeper_matchups`` carries points, the matchup id pairing two rosters
together, and JSON columns for ``players``/``starters``/``players_points`` —
a full weekly box score per roster. ``max_week`` caps how many weeks are
fetched per season (default 18, a full regular + postseason); weeks that
haven't happened yet return no data and are silently skipped, not treated
as an error.

Transaction history
-----------------------

:func:`~nuclearff.sleeper.transactions.fetch_and_write_transactions` fetches
every week's adds, drops, waivers, and trades, across the full history
chain:

.. code-block:: python

   from nuclearff.sleeper import fetch_and_write_transactions

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       tx_count, tx_player_count = fetch_and_write_transactions(
           client, leagues, db_path, max_week=3
       )

   print(f"Transactions: {tx_count}")
   print(f"Add/drop rows: {tx_player_count}")

.. code-block:: text

   Transactions: 365
   Add/drop rows: 542

Sleeper's own ``type`` and ``status`` fields are trusted and stored as-is,
not re-derived:

.. code-block:: python

   conn.execute("SELECT type, COUNT(*) FROM sleeper_transactions GROUP BY type").fetchall()
   # [('waiver', 256), ('free_agent', 104), ('trade', 5)]

Two tables land in DuckDB: ``sleeper_transactions`` (one row per
transaction — ``creator``/``roster_ids``/``consenter_ids`` resolved to
display names alongside the raw ids, timestamps parsed from Sleeper's
epoch-millisecond format to real UTC ``TIMESTAMP`` values) and
``sleeper_transaction_players`` (``adds``/``drops`` unnested to one row per
player per direction).

Roster composition
----------------------

:func:`~nuclearff.sleeper.roster_players.fetch_and_write_roster_players`
categorizes every roster's players by slot, per season:

.. code-block:: python

   from nuclearff.sleeper import fetch_and_write_roster_players

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       roster_player_count = fetch_and_write_roster_players(client, leagues, db_path)

   print(f"Roster players: {roster_player_count}")

.. code-block:: text

   Roster players: 945

.. code-block:: python

   conn.execute("SELECT slot, COUNT(*) FROM sleeper_roster_players GROUP BY slot").fetchall()
   # [('starter', 540), ('bench', 357), ('reserve', 48)]

Each row is a ``(league_id, season, roster_id, player_id)`` with a ``slot``
of ``starter``, ``reserve`` (IR), ``taxi``, or ``bench`` — join it to
``sleeper_players`` (below) for the player's name and position. Sleeper
fills an empty starting slot with the literal string ``"0"`` before a
draft; that filler is dropped rather than stored as a phantom player.

Every one of these writes replaces its own table wholesale — a fresh run
is a fresh full snapshot, not an incremental append. There's no ordering
requirement between the functions above; call the ones you need.

Discovering leagues from a username
---------------------------------------

Every function above needs an already-known ``league_id``. If you only
have a Sleeper username, resolve it first, the same way :doc:`03_sleeper_api`
did:

.. code-block:: python

   with SleeperClient() as client:
       user = client.get_user("nolmacdonald")
       leagues = client.get_user_leagues(user["user_id"], 2026, sport="nfl")

   print(f"{user['display_name']} ({user['user_id']})")
   print(f"{len(leagues)} leagues for 2026")
   for l in leagues[:3]:
       print(f"  {l['league_id']}  {l['name']}  status={l.get('status')}")

.. code-block:: text

   nolmacdonald (332632476830679040)
   18 leagues for 2026
     1313346760332029952  NUCLEARFF DYNASTY  status=in_season
     1367225133634191360  NUCLEARFF REDRAFT  status=in_season
     1397668648528650240  Antares Sunday Operations  status=in_season

``client.get_user_drafts(user_id, season)`` works the same way for drafts
instead of leagues.

The player map, and filtering server-side
------------------------------------------------

:func:`~nuclearff.sleeper.players.write_players_table` pulls Sleeper's full
NFL player map into a ``sleeper_players`` DuckDB table — this is the join
target for every ``player_id`` column above:

.. code-block:: python

   from nuclearff.sleeper import write_players_table

   with SleeperClient(cache_dir="./demo/data/cache") as client:
       players = client.get_players()

   count = write_players_table(players, db_path)
   print(f"Players: {count}")

.. code-block:: text

   Players: 12227

As covered in :doc:`03_sleeper_api`, filter server-side with
``get_players(position=..., active=...)`` instead of fetching everything
when you only need part of the map.

Cross-referencing to nflverse
----------------------------------

Sleeper player objects already carry several cross-platform IDs, including
``gsis_id`` — nflverse's own primary key — but not every player has one.
:mod:`nuclearff.ids.crosswalk` fills the gap from nflverse's own ID
crosswalk:

.. code-block:: python

   from nuclearff.ids import (
       read_sleeper_players,
       resolve_missing_gsis_ids,
       write_player_id_map,
       ambiguous_sleeper_ids,
   )
   from nuclearff.nflverse import load_ff_playerids

   players_df = read_sleeper_players(db_path)
   ff_ids = load_ff_playerids()

   resolved = resolve_missing_gsis_ids(players_df, ff_ids)
   n_written = write_player_id_map(resolved, db_path)

   print(resolved["gsis_id_source"].value_counts())
   print(f"Skipped {ambiguous_sleeper_ids(ff_ids).height} ambiguous crosswalk row(s).")

.. code-block:: text

   ┌────────────────┬───────┐
   │ gsis_id_source ┆ count │
   │ str            ┆ u32   │
   ╞════════════════╪═══════╡
   │ null           ┆ 4813  │
   │ ff_playerids   ┆ 3521  │
   │ sleeper        ┆ 3893  │
   └────────────────┴───────┘
   Skipped 12 ambiguous crosswalk row(s).

``read_sleeper_players`` requires ``write_players_table`` to have run first.
The result — written by ``write_player_id_map`` to a ``player_id_map``
table — records each player's best-known ``gsis_id`` and where it came
from: Sleeper's own field, or the crosswalk. A crosswalk row whose
``sleeper_id`` maps to more than one player is skipped rather than guessed
(:func:`~nuclearff.ids.crosswalk.ambiguous_sleeper_ids`); most of what's
still unresolved is players missing from this year's crosswalk entirely,
typically rookies.

Querying what you've built
-------------------------------

Every table above lives in one DuckDB file. Query it directly with
:func:`~nuclearff.duckdb_io.read_table` (returns a
:class:`polars.DataFrame`) or raw SQL:

.. code-block:: python

   from nuclearff.duckdb_io import read_table

   conn.sql("SHOW TABLES").show()

.. code-block:: text

   ┌─────────────────────────────┐
   │            name             │
   │           varchar           │
   ├─────────────────────────────┤
   │ player_id_map               │
   │ sleeper_leagues              │
   │ sleeper_league_configs       │
   │ sleeper_matchups             │
   │ sleeper_players              │
   │ sleeper_playoff_matches      │
   │ sleeper_roster_players       │
   │ sleeper_standings            │
   │ sleeper_transaction_players  │
   │ sleeper_transactions         │
   └─────────────────────────────┘

(the full set, once every function on this page has been called at least
once — your own database only has tables for what you've actually run).
Every table is keyed by ``league_id`` (and usually ``season``), so joining
across them — "which player did the eventual champion trade for" or "how
many waiver claims did the team with the worst record make" — is ordinary
SQL, not custom Python for each question. :doc:`17_querying_and_provenance`
covers this in more depth, alongside recording *how* a dataset was built.

What's Next
-----------

With a real, persisted league dataset in place, :doc:`05_scoring_engine`
starts turning real NFL statistics into fantasy points under this league's
own rules — the foundation everything in Chapters 6 through 12 builds on.
