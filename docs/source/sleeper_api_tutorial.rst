.. _sleeper_api_tutorial:

Sleeper API Tutorial
=====================

This tutorial covers the `Sleeper API <https://docs.sleeper.com>`_ itself —
what it is and what data it exposes — and then walks through fetching that
data yourself in Python with :class:`~nuclearff.sleeper.client.SleeperClient`.
:doc:`user_guide` shows the same data captured through the ``nuclearff`` CLI
instead; this page goes one layer deeper, using the same client the CLI is
built on.

About the Sleeper API
-----------------------

Sleeper's API is public, free, read-only, and requires no signup, API key, or
authentication of any kind — every example below works as soon as you know a
league ID. It is a plain JSON HTTP API at ``https://api.sleeper.app``; the
official reference lives at `docs.sleeper.com <https://docs.sleeper.com>`_.

In exchange for free, unauthenticated access, Sleeper asks callers to be
polite: stay under roughly 1000 requests per minute, and fetch the full
player map (the largest endpoint, ~5 MB) no more than once a day.
:class:`~nuclearff.sleeper.client.SleeperClient` enforces both limits itself
— requests are paced automatically, and the player map is cached to disk with
a 24-hour TTL — so nothing in this tutorial needs to hand-roll throttling.

Data hangs off two roots: a **sport** (``nfl``) for the current state and the
player map, and a **league** for everything else — members, rosters, weekly
matchups and transactions, and drafts. A draft has its own sub-resources
(picks, traded picks) once one exists.

What Data Can You Get
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
   crosswalk. See ``notes/sleeper-player-object.md`` in the project brain for
   the full field list, confirmed against a live fetch.

Prerequisites
---------------

Install ``nuclearff`` with the development extras, as covered in
:doc:`getting_started`::

   uv sync --frozen --extra dev

Nothing else is required — no account, no API key. You only need a league
ID, which is the number in a league's Sleeper URL
(``https://sleeper.com/leagues/<league_id>``).

Fetching League Data in Python
---------------------------------

The examples below run against the same example league used in
:doc:`getting_started`, ``1367225133634191360``, and show real output fetched
from it while writing this page. Your own league's members, rosters, and
current week will differ.

Connect to a league
~~~~~~~~~~~~~~~~~~~~~~

Use :class:`~nuclearff.sleeper.client.SleeperClient` as a context manager so
its HTTP session is closed for you:

.. code-block:: python

   from nuclearff.sleeper import SleeperClient

   league_id = "1367225133634191360"

   with SleeperClient() as client:
       state = client.get_state("nfl")
       league = client.get_league(league_id)

``state`` is a small dict with ``season``, ``week``, and ``season_type`` —
useful for knowing which week's matchups or transactions to fetch next.
``league`` is the full settings object; two fields worth knowing immediately:

.. code-block:: pycon

   >>> league["name"]
   'NUCLEARFF REDRAFT'
   >>> league["roster_positions"]
   ['QB', 'RB', 'RB', 'WR', 'WR', 'TE', 'FLEX', 'FLEX', 'FLEX',
    'BN', 'BN', 'BN', 'BN', 'BN', 'BN']

``roster_positions`` lists one entry per roster slot. This is what
``nuclearff``'s league configuration reads to work out starter counts and
replacement level — see :doc:`api/index`.

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

Before a draft, ``roster["players"]`` is an empty list for every roster —
there is nothing to resolve to player data yet.

The Sleeper NFL player map
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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
   full 5 MB payload — confirmed live to shrink it to roughly 435 KB for
   ``get_players(position="QB", active=True)``. A filtered call always hits
   the network; the disk cache is specifically for the full unfiltered map.

Handling errors
~~~~~~~~~~~~~~~~~~

A failed request raises a typed exception rather than a raw
:mod:`requests` exception, so you can catch Sleeper-specific failures without
also catching unrelated bugs:
:exc:`~nuclearff.exceptions.SleeperHTTPError` for a non-retryable HTTP status
(or once retries are exhausted on a 429 / 5xx), and
:exc:`~nuclearff.exceptions.SleeperResponseError` if a response isn't the
shape expected — Sleeper returns a bare ``null`` for an unknown league or
draft ID, for example, rather than a 404. Both subclass
:exc:`~nuclearff.exceptions.SleeperAPIError` if you want to catch either.

From Client Calls to nuclearff Pipelines
-------------------------------------------

Everything above uses the same client the ``nuclearff`` CLI does — the CLI
just wraps it into repeatable, on-disk artifacts (a league snapshot, the
player map, and a growing set of DuckDB tables covering league history,
standings, matchups, transactions, and roster composition). See
:doc:`user_guide` for a complete walkthrough of every CLI command.

Reach for the CLI when you want those standard artifacts on disk. Reach for
:class:`~nuclearff.sleeper.client.SleeperClient` directly, as in this
tutorial, when you're exploring interactively or wiring the data into
something the CLI doesn't build yet.

See Also
----------

- :doc:`getting_started` — installation and configuration.
- :doc:`user_guide` — every ``nuclearff`` CLI command, feature by feature.
- :doc:`api/index` — full reference for ``SleeperClient``, ``LeagueSnapshot``,
  and every other public symbol.
- `docs.sleeper.com <https://docs.sleeper.com>`_ — Sleeper's own API reference.
