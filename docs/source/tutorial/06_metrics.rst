.. _tutorial_metrics:

6. Opportunity and Efficiency Metrics
============================================

Raw fantasy points (:doc:`05_scoring_engine`) tell you what happened.
:mod:`nuclearff.metrics` computes the underlying signals that explain
*why*, and — more importantly for valuation — which of it tends to repeat
season over season. These metrics are wide-receiver-focused (the plan's
original scope), split into three groups: opportunity **volume**,
route-participation **efficiency**, and **touchdown** regression.

Volume: target share, air yards, and WOPR
------------------------------------------------

:func:`~nuclearff.metrics.volume.target_share`,
:func:`~nuclearff.metrics.volume.air_yards_share`, and
:func:`~nuclearff.metrics.volume.wopr` all require **weekly-grain** data —
"team" is only unambiguous week to week, since a traded player has a
different team in different weeks. Passing seasonal data raises
``ValueError`` rather than silently dividing by the wrong team total.
:func:`~nuclearff.metrics.volume.racr` and
:func:`~nuclearff.metrics.volume.adot` are simple per-row ratios and accept
either grain.

.. code-block:: python

   from nuclearff.nflverse import load_weekly_receiving
   from nuclearff.metrics import target_share, air_yards_share, wopr, racr, adot

   weekly = load_weekly_receiving([2024])
   df = target_share(weekly)
   df = air_yards_share(df)
   df = wopr(df)
   df = racr(df)
   df = adot(df)

   top = (
       df.group_by(["player_display_name", "position"])
       .agg([
           pl.col("target_share").mean(),
           pl.col("air_yards_share").mean(),
           pl.col("wopr").mean(),
           pl.col("racr").mean(),
           pl.col("adot").mean(),
       ])
       .sort("wopr", descending=True)
       .head(6)
   )
   print(top)

.. code-block:: text

   shape: (6, 7)
   ┌──────────────────────┬──────────┬──────────────┬─────────────────┬───────┬───────┬────────┐
   │ player_display_name  ┆ position ┆ target_share ┆ air_yards_share ┆ wopr  ┆ racr  ┆ adot   │
   ╞══════════════════════╪══════════╪══════════════╪═════════════════╪═══════╪═══════╪════════╡
   │ Malik Nabers          ┆ WR       ┆ 0.357        ┆ 0.465           ┆ 0.861 ┆ 0.864 ┆ 9.401  │
   │ A.J. Brown            ┆ WR       ┆ 0.322        ┆ 0.516           ┆ 0.844 ┆ 0.856 ┆ 12.916 │
   │ Courtland Sutton      ┆ WR       ┆ 0.272        ┆ 0.489           ┆ 0.750 ┆ 0.636 ┆ 13.409 │
   │ George Pickens        ┆ WR       ┆ 0.268        ┆ 0.469           ┆ 0.731 ┆ 0.706 ┆ 13.638 │
   │ Rashid Shaheed        ┆ WR       ┆ 0.247        ┆ 0.502           ┆ 0.722 ┆ 0.607 ┆ 18.219 │
   │ Justin Jefferson      ┆ WR       ┆ 0.298        ┆ 0.379           ┆ 0.713 ┆ 0.953 ┆ 11.384 │
   └──────────────────────┴──────────┴──────────────┴─────────────────┴───────┴───────┴────────┘

``WOPR`` (Weighted Opportunity Rating, Hermsmeyer's formula:
``1.5 * target_share + 0.7 * air_yards_share``) is the single best-fitting
volume composite — Malik Nabers led 2024 despite a lower target share than
Justin Jefferson, because his share of his team's *air yards* was higher.

.. important::

   Every volume function above **recomputes its metric from raw columns
   rather than trusting nflreadpy's own precomputed ones**, then
   cross-checks the two and logs a warning on real disagreement. Confirmed
   on live 2024 data: the recomputed ``target_share`` matches nflreadpy's
   own column almost exactly, but ``air_yards_share`` disagrees on roughly
   7% of rows — nflreadpy's own denominator appears to draw from a richer
   play-by-play source than this project's per-player stats table
   captures. That's the cross-check doing its job, not a bug: trust the
   warning as a real data-quality signal, not noise to silence.

Efficiency: yards and targets per route run
--------------------------------------------------

Target share doesn't say how *hard* a receiver had to work for those
targets. :func:`~nuclearff.metrics.efficiency.yprr` (yards per route run)
and :func:`~nuclearff.metrics.efficiency.tprr` (targets per route run) need
routes-run data, which isn't in the same table as receiving stats — join
them first with :func:`~nuclearff.metrics.efficiency.join_routes`, which
bridges the two via each player's ``pfr_id``/``gsis_id``:

.. code-block:: python

   from nuclearff.nflverse import load_seasonal_receiving, load_routes, load_players
   from nuclearff.metrics import join_routes, yprr, tprr

   receiving = load_seasonal_receiving([2024])
   routes = load_routes([2024])
   players = load_players()

   joined = join_routes(receiving, routes, players)
   df = tprr(yprr(joined))

Both are **sample-gated**: below ``min_routes`` (200 by default), the
result is ``null`` rather than a noisy small-sample number:

.. code-block:: python

   qualified = df.filter(pl.col("yprr").is_not_null())
   print(f"{qualified.height} of {df.height} players ran >= 200 routes")

   print(
       qualified.sort("yprr", descending=True)
       .select(["player_display_name", "position", "routes_run", "yprr", "tprr"])
       .head(6)
   )

.. code-block:: text

   293 of 511 players ran >= 200 routes
   shape: (6, 5)
   ┌──────────────────────┬──────────┬────────────┬──────────┬──────────┐
   │ player_display_name  ┆ position ┆ routes_run ┆ yprr     ┆ tprr     │
   ╞══════════════════════╪══════════╪════════════╪══════════╪══════════╡
   │ Puka Nacua            ┆ WR       ┆ 595.0      ┆ 1.663866 ┆ 0.178151 │
   │ Ja'Marr Chase         ┆ WR       ┆ 1054.0     ┆ 1.620493 ┆ 0.166034 │
   │ Nico Collins          ┆ WR       ┆ 622.0      ┆ 1.617363 ┆ 0.159164 │
   │ Brian Thomas Jr.      ┆ WR       ┆ 821.0      ┆ 1.56151  ┆ 0.161998 │
   │ Marvin Mims Jr.       ┆ WR       ┆ 326.0      ┆ 1.542945 ┆ 0.159509 │
   │ Ladd McConkey         ┆ WR       ┆ 785.0      ┆ 1.463694 ┆ 0.142675 │
   └──────────────────────┴──────────┴────────────┴──────────┴──────────┘

``routes_run`` is honestly labeled by source (``routes_source`` on the
joined frame) — nflverse doesn't publish a direct routes-run column for
every season, so this is an approximation in some cases, not a guarantee
of exactness. :func:`~nuclearff.metrics.efficiency.ambiguous_player_id_pairs`
mirrors :doc:`04_capturing_a_league`'s Sleeper crosswalk check for this
join's own ``pfr_id``/``gsis_id`` bridge — 0 ambiguous pairs on this real
2024 data.

Touchdowns: expected TDs and regression
------------------------------------------------

Touchdowns are the least stable part of a receiver's fantasy output year
to year — a receiver can lead the league in expected scoring opportunities
and still finish with a below-average touchdown total, purely from
variance. :func:`~nuclearff.metrics.touchdowns.expected_tds` joins ffverse's
own expected-fantasy-opportunity model onto real receiving stats and adds
``td_regression`` (real minus expected):

.. code-block:: python

   from nuclearff.nflverse import load_seasonal_receiving, load_ff_opportunity
   from nuclearff.metrics import expected_tds

   receiving = load_seasonal_receiving([2024])
   opportunity = load_ff_opportunity([2024])
   df = expected_tds(receiving, opportunity)

   print(
       df.filter(pl.col("expected_tds").is_not_null())
       .sort("td_regression")
       .select(["player_display_name", "position", "receiving_tds", "expected_tds", "td_regression"])
       .head(5)
   )

.. code-block:: text

   shape: (5, 5)
   ┌──────────────────────┬──────────┬───────────────┬──────────────┬───────────────┐
   │ player_display_name  ┆ position ┆ receiving_tds ┆ expected_tds ┆ td_regression │
   ╞══════════════════════╪══════════╪═══════════════╪══════════════╪═══════════════╡
   │ Trey McBride          ┆ TE       ┆ 2             ┆ 8.55         ┆ -6.55         │
   │ Rome Odunze           ┆ WR       ┆ 3             ┆ 7.07         ┆ -4.07         │
   │ Adonai Mitchell       ┆ WR       ┆ 0             ┆ 3.56         ┆ -3.56         │
   │ George Pickens        ┆ WR       ┆ 3             ┆ 6.46         ┆ -3.46         │
   │ Travis Kelce          ┆ TE       ┆ 3             ┆ 6.28         ┆ -3.28         │
   └──────────────────────┴──────────┴───────────────┴──────────────┴───────────────┘

Negative ``td_regression`` (real 2024) is the "TD-unlucky" list — Trey
McBride scored 6.55 fewer touchdowns than his real opportunities predicted,
which is exactly the kind of gap a projection should expect to *close*
next season, not repeat. Sort descending instead for the "TD-lucky" list —
players whose touchdown total is more likely to regress down. A player
with no matching opportunity data gets ``expected_tds = null`` rather than
being dropped or raising (30 of 511 real 2024 rows had no match).

What's Next
-----------

Volume, efficiency, and touchdown regression are all inputs, not a
finished number. :doc:`07_projections` blends real historical seasons —
weighted toward the two metrics above that repeat, and away from
touchdown luck — into a single forward-looking estimate.
