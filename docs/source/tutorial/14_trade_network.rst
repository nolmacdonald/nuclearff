.. _tutorial_trade_network:

14. Trade Network Analysis
=================================

:mod:`nuclearff.report.trades` turns a league's stored trade history
(:doc:`04_capturing_a_league`'s ``sleeper_transactions``) into ten
different views of who trades, who trades with whom, and how that's
changed over time. This chapter walks through all ten, grounded in one
real league's real data: 22 completed trades since 2021, across the 15
managers who have ever held a roster in ``NUCLEARFF REDRAFT``. No new
Sleeper fetching happens here — every function below only reads and
renders what :doc:`04_capturing_a_league` already stored.

Trade edges and manager totals
------------------------------------

:func:`~nuclearff.sleeper.trades.load_trades` explodes every stored trade
transaction into one row per manager pair per trade;
:func:`~nuclearff.sleeper.trades.manager_trade_counts` rolls that up per
manager:

.. code-block:: python

   from nuclearff.duckdb_io import read_table
   from nuclearff.sleeper.trades import load_trades, manager_trade_counts

   db_path = "./demo/data/cache/nuclearff.duckdb"
   edges = load_trades(db_path)
   counts = manager_trade_counts(edges)
   print(edges.height, "trade edges")
   print(counts.sort("trades", descending=True).head(3))

.. code-block:: text

   22 trade edges
   shape: (3, 5)
   ┌──────────────┬────────┬─────────────────┬────────────────────────┬──────────────────────┐
   │ manager      ┆ trades ┆ unique_partners ┆ most_frequent_partner  ┆ trades_with_partner  │
   ╞══════════════╪════════╪═════════════════╪═════════════════════════╪══════════════════════╡
   │ nolmacdonald  ┆ 14     ┆ 7               ┆ BigCookie96              ┆ 4                    │
   │ casitzmann    ┆ 9      ┆ 6               ┆ nolmacdonald             ┆ 4                    │
   │ BigCookie96   ┆ 5      ┆ 2               ┆ nolmacdonald             ┆ 4                    │
   └──────────────┴────────┴─────────────────┴──────────────────────────┴──────────────────────┘

``nolmacdonald`` is this league's most active trader by a wide margin.
Note ``trades`` counts **distinct trades, not trade relationships** —
Sleeper allows more than two rosters in a single trade, and this league
has a real one: a 2022 three-team trade among ``casitzmann``,
``nolmacdonald``, and ``nolanmacdonald``. Each of those three managers'
counts it once, not twice.

Including managers who never traded
------------------------------------------

``manager_trade_counts`` only returns managers who appear in ``edges`` —
five of this league's real 15 all-time managers never made a trade and
are absent entirely. Join against the full manager roster from
``sleeper_standings`` (:doc:`04_capturing_a_league`) to include them at an
explicit zero — exactly what ``report trades`` does for you on the
command line:

.. code-block:: python

   import polars as pl

   standings = read_table(db_path, "sleeper_standings")
   all_managers = sorted({name for name in standings["display_name"].to_list() if name})

   counts_full = (
       pl.DataFrame({"manager": all_managers})
       .join(counts, on="manager", how="left")
       .with_columns(
           pl.col("trades").fill_null(0),
           pl.col("unique_partners").fill_null(0),
           pl.col("trades_with_partner").fill_null(0),
       )
   )
   print(f"{counts_full.filter(pl.col('trades') == 0).height} of {counts_full.height} managers never traded")

.. code-block:: text

   5 of 15 managers never traded

The rest of this chapter uses ``counts_full`` (and an equally-densified
trade matrix, below) so every chart includes all 15 real managers, not
just the 10 who show up in trade data.

Trades by manager, and between managers
--------------------------------------------

:func:`~nuclearff.report.trades.render_trades_by_manager` draws a
horizontal bar chart, one bar per manager:

.. code-block:: python

   render_trades_by_manager(counts_full.select("manager", "trades"), "./demo/data/artifacts/trades_by_manager.png")

.. figure:: ../_static/screenshots/trades_by_manager.png
   :width: 700
   :alt: Horizontal bar chart of total trades per manager

   Real output for this league's full 15-manager history.

:func:`~nuclearff.sleeper.trades.pairwise_trade_matrix` builds a symmetric
manager x manager count matrix — densify it the same way as ``counts``
before rendering, so a zero-trade manager still gets a dense, all-zero
row and column:

.. code-block:: python

   from nuclearff.sleeper.trades import pairwise_trade_matrix
   from nuclearff.report import render_trades_heatmap

   raw_matrix = pairwise_trade_matrix(edges)
   matrix = (
       pl.DataFrame({"manager": all_managers})
       .join(raw_matrix, on="manager", how="left")
       .fill_null(0)
       .select(["manager", *all_managers])
   )
   render_trades_heatmap(matrix, "./demo/data/artifacts/trades_heatmap.png")

.. figure:: ../_static/screenshots/trades_heatmap.png
   :width: 700
   :alt: Manager x manager heatmap of trade counts

   Real output for this league — ``casitzmann``/``nolmacdonald`` shows 4
   in both directions, with a 0 diagonal (a manager can't trade with
   themselves).

Trade network and chord diagram
--------------------------------------

:func:`~nuclearff.report.trades.render_trade_network` draws a node-link
graph — one node per manager (sized by total trades), one edge per pair
that has traded (widened by trade count):

.. code-block:: python

   from nuclearff.report import render_trade_network, render_chord_diagram

   render_trade_network(counts_full.select("manager", "trades"), matrix, "./demo/data/artifacts/trade_network.png")
   render_chord_diagram(counts_full.select("manager", "trades"), matrix, "./demo/data/artifacts/chord_diagram.png")

.. figure:: ../_static/screenshots/trade_network.png
   :width: 700
   :alt: Node-link graph of the manager trade network

   With only 22 trades across 15 managers, this real graph is genuinely
   sparse — 13 edges across 15 nodes, several managers never connecting
   to the rest of the league. That's real trading activity, not a
   rendering bug.

.. figure:: ../_static/screenshots/chord_diagram.png
   :width: 700
   :alt: Circular chord diagram of manager trade relationships

   The same relationships, drawn directly in matplotlib (not an
   interactive plotting library) to avoid a headless-browser dependency
   for one chart.

Leaderboards
----------------

:func:`~nuclearff.report.trades.render_trade_leaderboard` renders one
reference row per manager (Trades, Unique Partners, Most Frequent
Partner, Trades With Partner), and
:func:`~nuclearff.report.trades.render_manager_pair_leaderboard` shows the
top 10 manager *pairs* by trade count — unlike the heatmap, it only shows
pairs that actually traded:

.. code-block:: python

   from nuclearff.sleeper.trades import top_manager_pairs
   from nuclearff.report import render_trade_leaderboard, render_manager_pair_leaderboard

   render_trade_leaderboard(counts_full, "./demo/data/artifacts/trade_leaderboard.png")

   pairs = top_manager_pairs(edges)
   render_manager_pair_leaderboard(pairs, "./demo/data/artifacts/manager_pair_leaderboard.png")

.. figure:: ../_static/screenshots/trade_leaderboard.png
   :width: 700
   :alt: Trade leaderboard reference table

   A zero-trade manager still gets a full row, with ``—`` in place of a
   partner that doesn't exist.

.. figure:: ../_static/screenshots/manager_pair_leaderboard.png
   :width: 700
   :alt: Horizontal bar chart of the top manager pairs by trade count

   Each pair appears once — manager names are sorted alphabetically
   before grouping, so an A-B and B-A row for the same pair can't both
   exist.

Trades over time
--------------------

:func:`~nuclearff.sleeper.trades.trades_by_season` and
:func:`~nuclearff.sleeper.trades.total_trades_by_season` feed
:func:`~nuclearff.report.trades.render_trades_over_time` (one thin line
per manager plus a bold league-wide total) and
:func:`~nuclearff.report.trades.render_manager_season_heatmap` (the same
data as a dense manager x season grid):

.. code-block:: python

   from nuclearff.sleeper.trades import trades_by_season, total_trades_by_season
   from nuclearff.report import render_trades_over_time

   by_season = trades_by_season(edges)
   totals = total_trades_by_season(edges)
   render_trades_over_time(by_season, totals, "./demo/data/artifacts/trades_over_time.png")

.. figure:: ../_static/screenshots/trades_over_time.png
   :width: 700
   :alt: Line chart of trades by season, one line per manager plus a league total

   The league total counts each trade once regardless of how many
   managers it involved — summing every manager's own line would
   roughly double-count real trade volume, since most trades involve two
   managers.

.. figure:: ../_static/screenshots/manager_season_heatmap.png
   :width: 700
   :alt: Manager x season heatmap of trade counts

   This league's real 2022 peak — ``nolmacdonald``'s 6 trades in a single
   season — renders as the single darkest cell on the grid.

Cumulative trades and partner diversity
---------------------------------------------

:func:`~nuclearff.sleeper.trades.cumulative_trade_counts` and
:func:`~nuclearff.report.trades.render_cumulative_trades` answer "who
became the league's most prolific trader, and when did they take the
lead":

.. code-block:: python

   from nuclearff.sleeper.trades import cumulative_trade_counts
   from nuclearff.report import render_cumulative_trades, render_trade_partner_diversity

   cumulative = cumulative_trade_counts(edges)
   render_cumulative_trades(cumulative, "./demo/data/artifacts/cumulative_trades.png")

   render_trade_partner_diversity(
       counts_full.select("manager", "trades", "unique_partners"),
       "./demo/data/artifacts/trade_partner_diversity.png",
   )

.. figure:: ../_static/screenshots/cumulative_trades.png
   :width: 700
   :alt: Step chart of cumulative trades per manager over time

   ``nolmacdonald`` visibly takes the all-time lead in late 2023, holding
   it through 14 of the league's real 22 trades.

.. figure:: ../_static/screenshots/trade_partner_diversity.png
   :width: 550
   :alt: Scatter plot of total trades vs. unique trade partners

   X axis is total trades, Y axis is unique partners — separating a
   manager who trades widely from one who repeatedly trades with the
   same one or two people. This league's real 5 zero-trade managers
   collapse into a single labeled point at the origin.

Reading these charts correctly
-----------------------------------

Three real details from this exact data apply across every chart in this
chapter:

- **A manager needs a resolvable Sleeper display name to appear at all.**
  Two of this league's 22 real trades have a roster whose owner isn't in
  that season's user list, so neither trade contributes to any chart.
- **Sleeper allows more than two rosters in a single trade.** Every
  trade-counting aggregate here de-duplicates by the underlying
  transaction, not by exploded pairwise edges.
- **Manager identity is a display name, not a stable id.** This league's
  data shows exactly why that matters: ``nolmacdonald`` (14 trades) and
  ``nolanmacdonald`` (2 trades, including the three-team trade above)
  render as two separate managers throughout — nothing here merges
  similar-looking names automatically.

What's Next
-----------

Trades are one lens on league history. :doc:`15_wins_and_leagues` covers
two more: a manager's cumulative win total across their real career, and
a full snapshot of every league a Sleeper user belongs to.
