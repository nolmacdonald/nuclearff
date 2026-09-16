.. _tutorial_sleeper_api:

3. The Sleeper API in Python
=================================

This chapter covers the `Sleeper API <https://docs.sleeper.com>`_ itself —
what it is and what data it exposes — and
:class:`~nuclearff.sleeper.client.SleeperClient`, the thin, paced, cached
wrapper every other chapter in this tutorial is built on.

About the Sleeper API
-----------------------

Sleeper's API is public, free, read-only, and requires no signup, API key,
or authentication of any kind — every example below works as soon as you
know a league ID. It is a plain JSON HTTP API at
``https://api.sleeper.app``; the official reference lives at
`docs.sleeper.com <https://docs.sleeper.com>`_.

In exchange for free, unauthenticated access, Sleeper asks callers to be
polite: stay under roughly 1000 requests per minute, and fetch the full
player map (the largest endpoint, ~5 MB) no more than once a day.
:class:`~nuclearff.sleeper.client.SleeperClient` enforces both limits itself
— requests are paced automatically, and the player map is cached to disk
with a 24-hour TTL — so nothing in this tutorial needs to hand-roll
throttling.

Data hangs off two roots: a **sport** (``nfl``) for the current state and
the player map, and a **league** for everything else — members, rosters,
weekly matchups and transactions, and drafts. A draft has its own
sub-resources (picks, traded picks) once one exists.

What data can you get
------------------------

Every endpoint below is exposed by
:class:`~nuclearff.sleeper.client.SleeperClient` as a plain Python method
that returns already-decoded JSON (a ``dict`` or ``list[dict]``):

.. list-table::
   :header-rows: 1
   :widths: 26 32 42

   * - Data
     - Method
     - Returns
   * - Current NFL state
     - ``get_state(sport="nfl")``
     - Current season, week, and season type (``pre``/``regular``/``post``).
   * - League settings
     - ``get_league(league_id)``
     - Name, scoring settings, roster/position slots, general settings.
   * - League members
     - ``get_users(league_id)``
     - One object per member: display name, avatar, team metadata.
   * - Rosters
     - ``get_rosters(league_id)``
     - One object per roster: owner, player IDs, starters, bench, taxi/IR.
   * - Weekly matchups
     - ``get_matchups(league_id, week)``
     - Points and starters for every roster in a given week.
   * - Weekly transactions
     - ``get_transactions(league_id, week)``
     - Adds, drops, trades, and waiver claims for a given week.
   * - Traded picks
     - ``get_traded_picks(league_id)``
     - Draft picks traded at the league level.
   * - League drafts
     - ``get_league_drafts(league_id)``
     - Every draft ever run for the league, newest first.
   * - A draft
     - ``get_draft(draft_id)``
     - Draft type, order, and settings (rounds, pick timer, ...).
   * - Draft picks
     - ``get_draft_picks(draft_id)``
     - Picks made so far in a specific draft.
   * - Draft-level traded picks
     - ``get_draft_traded_picks(draft_id)``
     - Picks traded within a specific draft.
   * - Trending players
     - ``get_trending(kind, lookback_hours, limit)``
     - Most-added or most-dropped players league-wide, recently.
   * - All NFL players
     - ``get_players(force_refresh=False, position=None, active=None)``
     - Every player Sleeper knows, keyed by ID; disk-cached for 24 hours.
       ``position=``/``active=`` filter server-side, bypassing the cache.
   * - A user
     - ``get_user(username_or_id)``
     - Resolve a Sleeper username (or user id) to its ``user_id``/``display_name``.
   * - A user's leagues
     - ``get_user_leagues(user_id, season, sport="nfl")``
     - Every league that user belongs to for a season.
   * - A user's drafts
     - ``get_user_drafts(user_id, season, sport="nfl")``
     - Every draft that user is in for a season.
   * - Winners bracket
     - ``get_winners_bracket(league_id)``
     - Playoff bracket match tree; a match's ``p`` field, when present, is a
       final-placement award.
   * - Losers bracket
     - ``get_losers_bracket(league_id)``
     - Consolation bracket, same shape as the winners bracket.
   * - Avatar image URL
     - ``avatar_url(avatar_id, thumbnail=False)``
     - CDN URL for a user/league avatar — a URL builder, not an API call.

.. note::

   Sleeper player objects already carry cross-platform IDs, including
   ``gsis_id`` — nflverse's own primary key. That means a Sleeper player can
   usually be joined straight onto ``nflreadpy`` data without an extra ID
   crosswalk — see :doc:`04_capturing_a_league`'s "Cross-referencing to
   nflverse" section for the players who *don't* already have one.

Connecting to a league
-------------------------

Use :class:`~nuclearff.sleeper.client.SleeperClient` as a context manager so
its HTTP session is closed for you. Every example below runs against the
same real league used throughout this tutorial:

.. code-block:: python

   from nuclearff.sleeper import SleeperClient

   league_id = "1367225133634191360"

   with SleeperClient() as client:
       state = client.get_state("nfl")
       league = client.get_league(league_id)

``state`` is a small dict telling you which week's matchups or transactions
to fetch next; ``league`` is the full settings object:

.. code-block:: pycon

   >>> state
   {'week': 2, 'leg': 2, 'season': '2026', 'season_type': 'regular', ...}
   >>> league["name"]
   'NUCLEARFF REDRAFT'
   >>> league["roster_positions"]
   ['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'FLEX', 'FLEX',
    'BN', 'BN', 'BN', 'BN', 'BN', 'BN']

``roster_positions`` lists one entry per roster slot — this is exactly what
:class:`~nuclearff.config.league.RosterSlots` (:doc:`02_configuration`)
counts to work out starter demand and replacement level.

Who owns which roster
~~~~~~~~~~~~~~~~~~~~~~~~

Rosters and users are separate endpoints, joined by ``owner_id`` /
``user_id``:

.. code-block:: python

   with SleeperClient() as client:
       users = client.get_users(league_id)
       rosters = client.get_rosters(league_id)

   names_by_user_id = {u["user_id"]: u["display_name"] for u in users}

   for roster in sorted(rosters, key=lambda r: r["roster_id"]):
       owner = names_by_user_id.get(roster["owner_id"], "unknown")
       print(roster["roster_id"], owner)

.. code-block:: text

   1 casitzmann
   2 hyoga10
   3 nolmacdonald
   4 ksavabi
   5 aperry151
   ...            (10 rosters total)

The Sleeper NFL player map
------------------------------

:meth:`~nuclearff.sleeper.client.SleeperClient.get_players` returns every
player Sleeper knows about — roughly 5 MB of JSON — keyed by Sleeper player
ID:

.. code-block:: python

   with SleeperClient() as client:
       players = client.get_players()

   players["4046"]

.. code-block:: python

   {
       "player_id": "4046",
       "full_name": "Patrick Mahomes",
       "position": "QB",
       "team": "KC",
       "status": "Active",
       "active": True,
       "gsis_id": "00-0033873",
       "espn_id": 3139477,
       "yahoo_id": 30123,
       # ... plus several dozen scouting/broadcast fields
   }

Because this payload is large and Sleeper asks callers not to re-fetch it
often, ``get_players()`` caches it to
``<cache_dir>/sleeper_players_nfl.json`` and only re-requests it once the
cache is older than ``players_ttl_hours`` (24 hours by default). Pass
``force_refresh=True`` to bypass the cache.

.. tip::

   Pass ``position=`` and/or ``active=`` to filter server-side and skip the
   full payload — confirmed live, ``get_players(position="QB", active=True)``
   returns 355 players instead of the full ~12,000-player map. A filtered
   call always hits the network; the disk cache is specifically for the full
   unfiltered map.

Trending players
--------------------

:meth:`~nuclearff.sleeper.client.SleeperClient.get_trending` returns the
most-added or most-dropped players league-wide, right now — but only ever a
bare ``player_id``. Resolve names yourself against the player map:

.. code-block:: python

   with SleeperClient() as client:
       trending = client.get_trending(kind="add", lookback_hours=24, limit=5)
       players = client.get_players()

   for row in trending:
       p = players.get(row["player_id"], {})
       name = (
           p.get("full_name")
           or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
           or row["player_id"]
       )
       print(f"{name:30s} count={row['count']}")

.. code-block:: text

   Devaughn Vele                  count=1824382
   Caleb Douglas                  count=1576392
   Tampa Bay Buccaneers           count=1348055
   Devin Singletary               count=1288782
   Kaelon Black                   count=949235

Note the ``first_name``/``last_name`` fallback: a team defense — like
"Tampa Bay Buccaneers" above — has no ``full_name`` field in Sleeper's real
payload, only the split fields.

Handling errors
--------------------

A failed request raises a typed exception rather than a raw
:mod:`requests` exception, so you can catch Sleeper-specific failures
without also catching unrelated bugs:
:exc:`~nuclearff.exceptions.SleeperHTTPError` for a non-retryable HTTP
status (or once retries are exhausted on a 429 / 5xx), and
:exc:`~nuclearff.exceptions.SleeperResponseError` if a response isn't the
shape expected — Sleeper returns a bare ``null`` for an unknown league or
draft ID, for example, rather than a 404. Both subclass
:exc:`~nuclearff.exceptions.SleeperAPIError` if you want to catch either:

.. code-block:: python

   from nuclearff.exceptions import SleeperAPIError

   with SleeperClient() as client:
       try:
           client.get_league("not-a-real-league-id")
       except SleeperAPIError as exc:
           print(f"Sleeper call failed: {exc}")

What's Next
-----------

Everything above uses ``SleeperClient`` directly for one-off, interactive
lookups. :doc:`04_capturing_a_league` builds on the exact same client to
capture a *complete*, reproducible league dataset — a snapshot, full
multi-season history, standings, matchups, transactions, and roster
composition, all persisted so the rest of this tutorial can query them
without hitting Sleeper again.
