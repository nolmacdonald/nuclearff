.. _tutorial_valuation:

8. Replacement Level, VORP, and Draft Tiers
====================================================

A projection (:doc:`07_projections`) ranks players against each other. It
says nothing about *where the bench begins* — the point past which two
players are interchangeable because either is a free-agent-caliber
replacement anyway. :mod:`nuclearff.valuation` answers that with
value-based drafting: replacement level, VORP, VONA, and discrete draft
tiers.

Scoring and projecting a real board
------------------------------------------

Everything below needs a projected-points table first. Reuse the
:class:`~nuclearff.scoring.engine.ScoringEngine` from :doc:`05_scoring_engine`
and two of the same wiring functions :doc:`09_auction_draft_board` covers in
full — :func:`~nuclearff.pipeline.auction_board.score_seasons` (this
league's own rules over real seasonal stats) and
:func:`~nuclearff.pipeline.auction_board.project_points` (recency-weighted
realized points, the same idea as :doc:`07_projections`'s ``blend_projection``
but operating on whole fantasy-point totals instead of one rate column):

.. code-block:: python

   from nuclearff.config import load_league_config
   from nuclearff.scoring import ScoringEngine
   from nuclearff.nflverse import load_seasonal_skill_stats
   from nuclearff.pipeline import score_seasons, project_points

   league_cfg = load_league_config("./demo/configs/leagues/1367225133634191360.yaml")
   engine = ScoringEngine(league_cfg.scoring)

   stats = load_seasonal_skill_stats([2023, 2024, 2025])
   scored = score_seasons(stats, engine)
   projections = project_points(scored, 2026)

``projections`` now has one row per player with a ``value_estimate``
column (the recency-weighted points estimate) plus identity columns.

Replacement level
----------------------

:func:`~nuclearff.valuation.vorp.replacement_points` returns the projected
points of the replacement-level player at a position, under one of two
named baselines:

.. code-block:: python

   from nuclearff.valuation import replacement_points

   vols = replacement_points(projections, league_cfg, position="WR", baseline="vols", proj_column="value_estimate")
   vorp_baseline = replacement_points(projections, league_cfg, position="WR", baseline="vorp", proj_column="value_estimate")
   print(f"WR replacement level (vols): {vols:.1f}")
   print(f"WR replacement level (vorp): {vorp_baseline:.1f}")

.. code-block:: text

   WR replacement level (vols): 168.0
   WR replacement level (vorp): 135.8

Recall from :doc:`02_configuration`: **VOLS** (Value Over Last Starter)
uses the league's actual starter demand as the replacement rank; **VORP**
(Value Over Replacement Player) goes deeper into the bench on the
assumption that real waiver-wire depth, not the last nominal starter, is
where a player actually becomes replaceable. Neither is more "correct" —
they're two different, named assumptions, and VORP's bench-stash fraction
is explicitly an uncalibrated heuristic (see
:meth:`~nuclearff.config.league.LeagueConfig.replacement_rank`'s own
docstring).

VORP
--------

:func:`~nuclearff.valuation.vorp.vorp` adds a ``vorp`` column: each
player's value estimate minus the replacement level:

.. code-block:: python

   import polars as pl
   from nuclearff.valuation import vorp

   valued = vorp(projections, league_cfg, position="WR", baseline="vols", proj_column="value_estimate")
   top_wr = (
       valued.filter(pl.col("position") == "WR")
       .sort("vorp", descending=True)
       .select(["player_display_name", "value_estimate", "vorp"])
   )
   print(top_wr.head(8))

.. code-block:: text

   shape: (8, 3)
   ┌───────────────────────┬────────────────┬─────────┐
   │ player_display_name   ┆ value_estimate ┆ vorp    │
   ╞═══════════════════════╪════════════════╪═════════╡
   │ Ja'Marr Chase          ┆ 330.24         ┆ 162.28  │
   │ Amon-Ra St. Brown      ┆ 323.63         ┆ 155.67  │
   │ Puka Nacua             ┆ 309.18         ┆ 141.22  │
   │ Jaxon Smith-Njigba     ┆ 285.81         ┆ 117.85  │
   │ CeeDee Lamb            ┆ 260.11         ┆ 92.15   │
   │ George Pickens         ┆ 237.03         ┆ 69.07   │
   │ Davante Adams          ┆ 236.92         ┆ 68.96   │
   │ Justin Jefferson       ┆ 236.43         ┆ 68.47   │
   └───────────────────────┴────────────────┴─────────┘

Notice the real gap: the top four separate from the next four by roughly
25-50 points of VORP each, while Lamb through Jefferson are bunched within
25 points of each other — exactly the kind of structure draft tiers
(below) are meant to surface explicitly instead of leaving it implicit in
a sorted list.

VONA: value over next available
--------------------------------------

Replacement level answers "how much better is this player than a
free-agent floor." :func:`~nuclearff.valuation.vorp.vona` answers a
sharper, in-the-moment question: "how much do I lose if I wait and take
this position at my *next* pick instead?" It needs the set of players
already drafted and the number of picks until your next turn:

.. code-block:: python

   from nuclearff.valuation import vona

   wr_pool = valued.filter(pl.col("position") == "WR")
   drafted_ids = set(wr_pool.sort("vorp", descending=True).head(3)["player_id"])

   with_vona = vona(wr_pool, drafted_ids, next_pick_gap=8, position="WR", proj_column="value_estimate")
   print(
       with_vona.sort("vona", descending=True)
       .select(["player_display_name", "value_estimate", "vona"])
       .head(6)
   )

.. code-block:: text

   shape: (6, 3)
   ┌───────────────────────┬────────────────┬───────┐
   │ player_display_name   ┆ value_estimate ┆ vona  │
   ╞═══════════════════════╪════════════════╪═══════╡
   │ Puka Nacua             ┆ 309.18         ┆ null  │
   │ Ja'Marr Chase          ┆ 330.24         ┆ null  │
   │ Amon-Ra St. Brown      ┆ 323.63         ┆ null  │
   │ Jaxon Smith-Njigba     ┆ 285.81         ┆ 60.03 │
   │ CeeDee Lamb            ┆ 260.11         ┆ 34.33 │
   │ George Pickens         ┆ 11.25          ┆ 11.25 │
   └───────────────────────┴────────────────┴───────┘

The three already-``drafted_ids`` correctly get ``vona = null`` — they're
off the board, not a real choice anymore. Jaxon Smith-Njigba's real 60.03
VONA says: if you pass on him now, the best WR still around at your next
turn (8 picks later) will cost you 60 points of value — a much sharper
signal than his VORP alone for deciding whether to reach.

Draft tiers
---------------

A sorted VORP list still asks you to eyeball where one tier of value ends
and the next begins. :func:`~nuclearff.valuation.tiers.assign_tiers` groups
a continuous value column into discrete tiers via k-means, auto-selecting
``k`` by silhouette score unless you fix it:

.. code-block:: python

   from nuclearff.valuation import assign_tiers

   ranked = valued.filter(pl.col("position") == "WR").sort("vorp", descending=True).head(8)
   tiers = assign_tiers(ranked["vorp"].to_list())
   print(list(zip(ranked["player_display_name"], tiers, strict=True)))

.. code-block:: text

   [('Ja'Marr Chase', 1), ('Amon-Ra St. Brown', 1), ('Puka Nacua', 1),
    ('Jaxon Smith-Njigba', 1), ('CeeDee Lamb', 2), ('George Pickens', 2),
    ('Davante Adams', 2), ('Justin Jefferson', 2)]

That's exactly the gap spotted above, made explicit: the real top-4 land
in tier 1, everyone from Lamb down lands in tier 2 — a tier boundary
computed from the actual data's clustering, not a fixed "top 5 / next 5"
rule that would have split Smith-Njigba and Lamb arbitrarily.

What's Next
-----------

:doc:`09_auction_draft_board` puts all of this together end to end —
including converting VORP into real auction dollars — against a league
that actually runs an auction draft, plus renders the full report as CSV,
markdown, and styled PNG tables.
