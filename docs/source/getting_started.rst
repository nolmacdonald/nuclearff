.. _getting_started:

Getting Started
===============

Installation
------------

nuclearff is not published to PyPI. Clone the repository and install it with
``uv``::

   git clone https://github.com/nolmacdonald/nuclearff.git
   cd nuclearff
   uv sync --frozen

``--frozen`` installs exactly what ``uv.lock`` specifies. Install the
development extras to run linting, type checking, and tests::

   uv sync --frozen --extra dev

For building documentation, install the docs extras::

   uv sync --frozen --extra docs

Configuration
-------------

Every command reads its settings from a configuration file, so a run can be
reproduced from a git commit plus a config. Write one populated with the
documented defaults::

   uv run nuclearff config init

``paths.root`` anchors every managed directory, which means ``--root`` relocates
the whole tree — that is how the test suite keeps runs off the real data
directory::

   uv run nuclearff config paths --ensure

Capturing your league
---------------------

Fetch a complete, immutable snapshot of a Sleeper league::

   uv run nuclearff sleeper fetch-league --league-id 1367225133634191360

The command writes each endpoint to
``data/raw/sleeper/<league_id>/<timestamp>/`` and never overwrites an existing
capture. It also reports league settings that are contradictory or that change
how players should be valued, for example a league whose ``draft_rounds`` field
disagrees with its draft object, or one that plays the weekly median.

This is the starting point; ``fetch-league`` also has flags for multi-season
history, standings and playoff results, weekly matchups, transaction history,
and roster composition — see the :doc:`user_guide` for a complete walkthrough
of every command.

Data Sources
------------

nuclearff builds on two data sources:

nflverse / nflreadpy
   `nflreadpy <https://github.com/nflverse/nflreadpy>`_ provides NFL play-by-play,
   player, roster, schedule, and advanced statistics data as
   :class:`polars.DataFrame` objects. Data is cached locally between calls.

Sleeper API
   The `Sleeper API <https://docs.sleeper.com>`_ is a read-only, unauthenticated
   HTTP API exposing leagues, rosters, matchups, transactions, and player
   metadata. Sleeper asks that clients stay under 1000 calls per minute. See
   the :doc:`sleeper_api_tutorial` for a full walkthrough of fetching this
   data yourself in Python.

Quick Start
-----------

.. code-block:: python

   import logging

   import nflreadpy as nfl

   from nuclearff import configure_logging

   # Configure logging for your application
   configure_logging(level=logging.INFO)

   # Weekly player statistics for the 2023 and 2024 seasons
   player_stats = nfl.load_player_stats([2023, 2024])
   print(player_stats.head())

.. note::

   ``nflreadpy`` returns `polars <https://docs.pola.rs>`_ data frames. Use
   ``.to_pandas()`` if a downstream tool expects a
   :class:`pandas.DataFrame`.
