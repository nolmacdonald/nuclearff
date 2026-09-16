.. _index:

nuclearff
=========

**Nuclear Fantasy Football**

nuclearff is a Python package for fantasy football research and analysis. It
pulls NFL play-by-play, roster, and statistics data through the `nflverse
<https://github.com/nflverse>`_ ecosystem via `nflreadpy
<https://github.com/nflverse/nflreadpy>`_, and league, roster, and matchup data
through the `Sleeper API <https://docs.sleeper.com>`_.

.. grid:: 2
   :gutter: 3

   .. grid-item-card:: User Tutorial
      :link: tutorial/index
      :link-type: doc

      New to nuclearff? Start here — an 18-chapter, Python-first
      walkthrough of every feature: installation, the Sleeper API,
      scoring, projections, valuation, simulation, backtesting, league
      history, and more. Command-line usage is its own final chapter.

   .. grid-item-card:: API Reference
      :link: api/index
      :link-type: doc

      Detailed reference for all public classes and functions.

   .. grid-item-card:: Example Notebooks
      :link: https://github.com/nolmacdonald/nuclearff/tree/main/examples

      Runnable Jupyter notebooks mirroring these docs pages, with real
      cells executed against a real Sleeper league.

.. toctree::
   :maxdepth: 2
   :hidden:

   tutorial/index
   api/index
   changelog
   contributing
