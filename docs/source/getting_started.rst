.. _getting_started:

Getting Started
===============

Installation
------------

nuclearff is not published to PyPI. Clone the repository and install it with
``uv``::

   git clone https://github.com/nolmacdonald/nuclearff.git
   cd nuclearff
   uv sync

Install the development extras to run linting, type checking, and tests::

   uv sync --extra dev

For building documentation, install the docs extras::

   uv sync --extra docs

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
   metadata. Sleeper asks that clients stay under 1000 calls per minute.

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
