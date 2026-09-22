.. _tutorial_introduction:

1. Introduction
===============

``nuclearff`` is a Python package for fantasy football research and
valuation. It combines two data sources — real NFL statistics from the
`nflverse <https://github.com/nflverse>`_ ecosystem (via `nflreadpy
<https://github.com/nflverse/nflreadpy>`_) and league, roster, and matchup
data from the `Sleeper API <https://docs.sleeper.com>`_ — into a single
toolkit for scoring players under your league's own rules, projecting future
performance, valuing a draft, simulating outcomes, and mining your league's
own history.

This tutorial walks through every part of that toolkit as a Python library,
chapter by chapter, in the order you would actually reach for it: connect to
a league, capture and store its data, score and project players, value a
draft, and finally dig into your league's own history and trades. Every
example runs against a real Sleeper league (``1367225133634191360``,
``NUCLEARFF REDRAFT``) with real output shown below each snippet — your own
league's numbers will differ, but the shape of the output won't.

Everything in Chapters 2 through 17 is plain Python — importing functions
and classes and calling them directly, the way you would inside a notebook
or a script. ``nuclearff`` also ships a command-line interface that wraps
most of this into repeatable, on-disk commands; that's covered on its own,
at the end, in :doc:`18_cli_reference`.

Installation
------------

``nuclearff`` is not published to PyPI. Clone the repository and install it
with ``uv``:

.. code-block:: bash

   git clone https://github.com/nolmacdonald/nuclearff.git
   cd nuclearff
   uv sync --frozen

``--frozen`` installs exactly what ``uv.lock`` specifies. Install the
development extras to get ``jupyter``, ``pytest``, and the other tools used
throughout this tutorial:

.. code-block:: bash

   uv sync --frozen --extra dev

Every code block in this tutorial assumes you're running inside that
environment, e.g. via ``uv run python`` or ``uv run jupyter lab``.

Data Sources
------------

nflverse / nflreadpy
   `nflreadpy <https://github.com/nflverse/nflreadpy>`_ provides NFL
   play-by-play, player, roster, schedule, and advanced statistics data as
   :class:`polars.DataFrame` objects. Nothing here requires an account or a
   key; data is cached locally between calls.

Sleeper API
   The `Sleeper API <https://docs.sleeper.com>`_ is a read-only,
   unauthenticated HTTP API exposing leagues, rosters, matchups,
   transactions, and player metadata. Sleeper asks that clients stay under
   1000 calls per minute — :class:`~nuclearff.sleeper.client.SleeperClient`
   (:doc:`03_sleeper_api`) paces requests for you automatically.

A First Look
-------------

Before touching Sleeper at all, here is ``nuclearff`` pulling real NFL
statistics and configuring its own logging:

.. code-block:: python

   import logging

   import nflreadpy as nfl

   from nuclearff import configure_logging

   configure_logging(level=logging.INFO)

   # Weekly player statistics for the 2023 and 2024 seasons
   player_stats = nfl.load_player_stats([2023, 2024])
   print(player_stats.select(
       ["player_display_name", "season", "week", "position", "fantasy_points"]
   ).head(10))

.. code-block:: text

   shape: (10, 5)
   ┌──────────────────────┬────────┬──────┬──────────┬─────────────────┐
   │ player_display_name  ┆ season ┆ week ┆ position ┆ fantasy_points  │
   │ ---                  ┆ ---    ┆ ---  ┆ ---      ┆ ---             │
   │ str                  ┆ i32    ┆ i32  ┆ str      ┆ f64             │
   ╞══════════════════════╪════════╪══════╪══════════╪═════════════════╡
   │ Aaron Rodgers        ┆ 2023   ┆ 1    ┆ QB       ┆ 0.0             │
   │ Matt Prater          ┆ 2023   ┆ 1    ┆ K        ┆ 0.0             │
   │ Nick Folk            ┆ 2023   ┆ 1    ┆ K        ┆ 0.0             │
   │ Calais Campbell      ┆ 2023   ┆ 1    ┆ DE       ┆ 0.0             │
   │ Matthew Stafford     ┆ 2023   ┆ 1    ┆ QB       ┆ 14.46           │
   │ Graham Gano          ┆ 2023   ┆ 1    ┆ K        ┆ 0.0             │
   │ Thomas Morstead      ┆ 2023   ┆ 1    ┆ P        ┆ 0.0             │
   │ Al Woods             ┆ 2023   ┆ 1    ┆ DT       ┆ 0.0             │
   │ Brandon Graham       ┆ 2023   ┆ 1    ┆ DE       ┆ 0.0             │
   │ Kareem Jackson       ┆ 2023   ┆ 1    ┆ S        ┆ 0.0             │
   └──────────────────────┴────────┴──────┴──────────┴─────────────────┘

That real query returned 37,626 player-week rows across two seasons — every
position, including kickers and defensive linemen, since ``load_player_stats``
returns everyone the box score tracks. ``fantasy_points`` here is
nflreadpy's own generic scoring, not your league's — :doc:`05_scoring_engine`
covers replacing it with your league's actual rules.

.. note::

   ``nflreadpy`` returns `polars <https://docs.pola.rs>`_ data frames, and so
   does everything in ``nuclearff`` that consumes them. Use ``.to_pandas()``
   if a downstream tool expects a :class:`pandas.DataFrame`.

What's Next
-----------

:doc:`02_configuration` covers ``nuclearff``'s own configuration — where
data lives on disk, and the typed settings that drive scoring and
projection — before :doc:`03_sleeper_api` connects to a real league.
