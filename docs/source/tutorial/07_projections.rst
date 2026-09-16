.. _tutorial_projections:

7. Projections
====================

:doc:`06_metrics` computes signals for one season at a time. A projection
blends **several** real seasons into a single forward-looking estimate —
weighting recent seasons more heavily, adjusting for age, and letting you
hand-apply context a model can't see on its own (a coaching change, a
depth-chart note). :mod:`nuclearff.projection.blend` does each of these as
its own composable step, plus one function that orchestrates all of them.

.. note::

   This is explicitly a **recency-weighted average of real, realized
   stats** — not a machine-learning forecast. It carries no injury news, no
   depth-chart change, and a rookie with no prior season gets no
   projection at all. See :doc:`09_auction_draft_board` for where this
   caveat matters most in practice.

Recency-weighted rate
--------------------------

:func:`~nuclearff.projection.blend.recency_weighted_rate` takes multi-season
data and blends one rate column across the seasons before a target season,
using :class:`~nuclearff.config.models.RecencyWeights` (0.5 / 0.3 / 0.2 over
the three most recent seasons, by default). Below, blending real 2022-2024
target share into a 2025 estimate:

.. code-block:: python

   import polars as pl
   from nuclearff.nflverse import load_weekly_receiving, load_players
   from nuclearff.metrics import target_share
   from nuclearff.config.models import RecencyWeights, ModelConfig
   from nuclearff.projection import recency_weighted_rate, blend_projection

   weekly = target_share(load_weekly_receiving([2022, 2023, 2024]))
   seasonal = weekly.group_by(["player_id", "player_display_name", "season"]).agg(
       pl.col("target_share").mean().alias("target_share"),
       pl.col("week").n_unique().alias("games"),
   )

   blended = recency_weighted_rate(seasonal, "target_share", 2025, RecencyWeights())

.. code-block:: text

   recency_weighted_rate('target_share'): excluded 509 player-season row(s)
   below the 8-game minimum-sample threshold from the blend

That log line is :class:`~nuclearff.config.models.SampleThresholds` at
work — a real 2024 backup with two garbage-time targets doesn't get a rate
blended in at full weight, or at all, if it never crosses ``min_games``.

Age curve and hand-authored context
------------------------------------------

:func:`~nuclearff.projection.blend.apply_age_curve` multiplies a projection
by an age-based factor — flat through ``plateau_end`` (28 by default), then
declining, floored at ``min_factor``. It reads real age from
``players``'s ``birth_date`` column, joined by ``gsis_id``:

.. code-block:: python

   from nuclearff.projection import apply_age_curve
   from nuclearff.config.models import AgeCurveConfig

   players = load_players()
   aged = apply_age_curve(
       blended.rename({"target_share_blended": "proj"}), players, 2025, AgeCurveConfig(), "proj"
   )

:func:`~nuclearff.projection.blend.apply_context_deltas` applies a
hand-authored ``{player_id: delta}`` multiplier or additive adjustment on
top — a way to fold in a real depth-chart change or coaching hire the
statistical blend has no way to see, without hiding it inside opaque model
weights. A ``player_id`` in the dict but not found in ``df`` logs a
warning rather than failing silently (catches a typo in a hand-written
deltas file).

Orchestrating the full blend
-----------------------------------

:func:`~nuclearff.projection.blend.blend_projection` runs all of the above
in one call — recency weighting, the age curve, and any context deltas:

.. code-block:: python

   model_cfg = ModelConfig()
   blended = blend_projection(seasonal, players, 2025, ["target_share"], model_cfg)

   names = seasonal.select(["player_id", "player_display_name"]).unique(subset="player_id")
   result = (
       blended.join(names, on="player_id", how="left")
       .filter(pl.col("target_share_blended").is_not_null())
       .sort("target_share_blended", descending=True)
   )
   print(result.select(["player_display_name", "target_share_blended", "age_factor"]).head(8))

.. code-block:: text

   shape: (8, 3)
   ┌───────────────────────┬──────────────────────┬────────────┐
   │ player_display_name   ┆ target_share_blended ┆ age_factor │
   ╞═══════════════════════╪══════════════════════╪════════════╡
   │ Malik Nabers           ┆ 0.357041             ┆ 1.0        │
   │ Puka Nacua             ┆ 0.311315             ┆ 1.0        │
   │ A.J. Brown             ┆ 0.308804             ┆ 1.0        │
   │ Justin Jefferson       ┆ 0.291574             ┆ 1.0        │
   │ CeeDee Lamb            ┆ 0.287673             ┆ 1.0        │
   │ DJ Moore               ┆ 0.282566             ┆ 1.0        │
   │ Amon-Ra St. Brown      ┆ 0.280632             ┆ 1.0        │
   │ Davante Adams          ┆ 0.279868             ┆ 0.9        │
   └───────────────────────┴──────────────────────┴────────────┘

Real ``age_factor`` values below 1.0 start appearing exactly where you'd
expect — Davante Adams, well past ``plateau_end``, is projected at 90% of
his blended rate before any other adjustment. Everyone at or before
``plateau_end`` gets exactly ``1.0``, not a gradual pre-peak ramp; the
curve models decline, not development.

Games played
----------------

A rate projection (target share, yards per route run) says nothing about
how many games a player will actually play. :func:`~nuclearff.projection.blend.project_games_played`
recency-weights real games played the same way, capped at ``max_games``
(17):

.. code-block:: python

   from nuclearff.projection import project_games_played

   games = project_games_played(seasonal, 2025, RecencyWeights())
   print(games.join(names, on="player_id", how="left").sort("projected_games", descending=True).head(3))

.. code-block:: text

   shape: (3, 3)
   ┌────────────┬─────────────────┬──────────────────────┐
   │ player_id  ┆ projected_games ┆ player_display_name  │
   ╞════════════╪═════════════════╪══════════════════════╡
   │ 00-0038542 ┆ 17.0            ┆ Bijan Robinson        │
   │ 00-0039338 ┆ 17.0            ┆ Brock Bowers          │
   │ 00-0039064 ┆ 17.0            ┆ Zay Flowers           │
   └────────────┴─────────────────┴──────────────────────┘

Multiplying a rate projection by projected games (and, for touchdowns,
folding in the regression from :doc:`06_metrics`) is how a rate becomes a
seasonal point total — exactly what :doc:`09_auction_draft_board`'s
``project_points`` does for realized fantasy points instead of target
share.

What's Next
-----------

A single-number projection hides how much uncertainty is really there.
:doc:`11_simulation` turns a mean and a spread into a full range of
plausible outcomes. First, though, :doc:`08_valuation` turns *any*
projection — this chapter's target share, or realized fantasy points —
into a draft-ready value: replacement level, VORP, and discrete tiers.
