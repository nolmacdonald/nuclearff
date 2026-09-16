.. _tutorial_backtesting:

12. Backtesting and Model Validation
===========================================

Every projection choice so far — recency weights, an age curve, a
replacement baseline — is a modeling assumption, not a fact. The only way
to know whether one choice is actually better than another is to score it
against real seasons it never saw. :mod:`nuclearff.backtest` is that
harness: expanding-window folds, two baseline models to compare against,
and the metrics to score them.

Walk-forward folds
------------------------

:func:`~nuclearff.backtest.walkforward.season_folds` generates
expanding-window ``(train_seasons, test_season)`` pairs — each fold trains
on every season up to a point and tests on the very next one, the way a
real projection actually gets used (you never get to peek at the season
you're projecting):

.. code-block:: python

   from nuclearff.backtest import season_folds

   folds = season_folds([2021, 2022, 2023, 2024, 2025], min_train_seasons=2)
   print(folds)

.. code-block:: text

   [([2021, 2022], 2023), ([2021, 2022, 2023], 2024), ([2021, 2022, 2023, 2024], 2025)]

Two baselines
-----------------

:mod:`nuclearff.backtest.walkforward` ships two named baselines to compare
a real model against:
:func:`~nuclearff.backtest.walkforward.baseline_prior_year` (predict this
season as an exact copy of last season) and
:func:`~nuclearff.backtest.walkforward.baseline_recency_weighted` (this
tutorial's own recency-weighted blend from :doc:`07_projections`, applied
to whole fantasy-point totals).

Running a real backtest
------------------------------

:func:`~nuclearff.backtest.walkforward.run_walk_forward_backtest`
orchestrates fold generation, both models' predictions, and scoring, in
one call. Real 2021-2025 WR seasons, scored under this league's own
rules:

.. code-block:: python

   import polars as pl
   from nuclearff.config import load_league_config
   from nuclearff.scoring import ScoringEngine
   from nuclearff.nflverse import load_seasonal_skill_stats
   from nuclearff.backtest import (
       run_walk_forward_backtest, baseline_prior_year, baseline_recency_weighted,
   )
   from nuclearff.config.models import RecencyWeights

   league_cfg = load_league_config("./demo/configs/leagues/1367225133634191360.yaml")
   engine = ScoringEngine(league_cfg.scoring)

   seasons = [2021, 2022, 2023, 2024, 2025]
   scored = engine.score_frame(load_seasonal_skill_stats(seasons)).filter(pl.col("position") == "WR")

   models = {
       "prior_year": lambda df, test_season: baseline_prior_year(df, test_season, "fantasy_points"),
       "recency_weighted": lambda df, test_season: baseline_recency_weighted(
           df, test_season, "fantasy_points", RecencyWeights()
       ),
   }
   results = run_walk_forward_backtest(scored, seasons, "fantasy_points", models, min_train_seasons=2, top_k=12)
   print(results)

.. code-block:: text

   shape: (6, 8)
   ┌───────────────────┬─────────────┬──────────────────────┬───────────┬───────────┬────────────────┬─────────────┬───────────────┐
   │ model_name         ┆ test_season ┆ spearman_correlation ┆ mae       ┆ rmse      ┆ top_12_precision ┆ top_12_recall ┆ n_players_sc… │
   ╞════════════════════╪═════════════╪══════════════════════╪═══════════╪═══════════╪════════════════╪═════════════╪═══════════════╡
   │ prior_year          ┆ 2023        ┆ 0.788                ┆ 38.69     ┆ 52.43     ┆ 0.583           ┆ 0.583        ┆ 167.0         │
   │ recency_weighted    ┆ 2023        ┆ 0.799                ┆ 38.28     ┆ 52.07     ┆ 0.667           ┆ 0.667        ┆ 176.0         │
   │ prior_year          ┆ 2024        ┆ 0.746                ┆ 48.04     ┆ 62.95     ┆ 0.417           ┆ 0.417        ┆ 173.0         │
   │ recency_weighted    ┆ 2024        ┆ 0.752                ┆ 46.25     ┆ 60.08     ┆ 0.500           ┆ 0.500        ┆ 186.0         │
   │ prior_year          ┆ 2025        ┆ 0.776                ┆ 42.37     ┆ 61.57     ┆ 0.333           ┆ 0.333        ┆ 178.0         │
   │ recency_weighted    ┆ 2025        ┆ 0.784                ┆ 41.32     ┆ 59.03     ┆ 0.417           ┆ 0.417        ┆ 191.0         │
   └───────────────────┴─────────────┴──────────────────────┴───────────┴───────────┴────────────────┴─────────────┴───────────────┘

That's a genuinely useful, real result: across all three real folds,
``recency_weighted`` beats the naive ``prior_year`` baseline on every
single metric — lower error (MAE, RMSE), higher rank correlation, and
meaningfully better top-12 precision/recall (e.g. 0.667 vs. 0.583 in the
2023 fold). This is exactly the kind of evidence :doc:`07_projections`'s
recency weighting needs before being trusted over the simplest possible
alternative — a claim checked against data, not asserted.

Scoring one fold by hand
------------------------------

:func:`~nuclearff.backtest.walkforward.evaluate_fold` is what
``run_walk_forward_backtest`` calls once per model per fold — useful on
its own when you already have a prediction frame and just want a score:

.. code-block:: python

   from nuclearff.backtest import evaluate_fold, baseline_recency_weighted

   predicted = baseline_recency_weighted(scored, 2025, "fantasy_points", RecencyWeights())
   actual = scored.filter(pl.col("season") == 2025).select(["player_id", "fantasy_points"])

   metrics = evaluate_fold(
       predicted, actual,
       predicted_column="fantasy_points_predicted",
       actual_column="fantasy_points",
       top_k=12,
   )
   print(metrics)

Individual metrics
------------------------

Every score above is also available standalone in
:mod:`nuclearff.backtest.metrics` —
:func:`~nuclearff.backtest.metrics.mae`,
:func:`~nuclearff.backtest.metrics.rmse`,
:func:`~nuclearff.backtest.metrics.spearman_correlation`, and
:func:`~nuclearff.backtest.metrics.top_k_precision_recall` for continuous
predictions, plus two more for discrete outputs:
:func:`~nuclearff.backtest.metrics.tier_accuracy` (does a predicted draft
tier from :doc:`08_valuation` match the tier the player's real output
lands in) and :func:`~nuclearff.backtest.metrics.brier_score` (mean squared
error of a predicted probability against a binary real outcome):

.. code-block:: python

   from nuclearff.valuation import assign_tiers
   from nuclearff.backtest import tier_accuracy, brier_score

   joined = predicted.join(actual, on="player_id", how="inner").drop_nulls()
   pred_tiers = assign_tiers(joined["fantasy_points_predicted"].to_list())
   actual_tiers = assign_tiers(joined["fantasy_points"].to_list())
   print(tier_accuracy(pred_tiers, actual_tiers))

.. code-block:: python

   {'exact_match_rate': 0.372, 'within_one_rate': 1.0}

Real 2025 result: the recency-weighted model's predicted tier matches the
player's actual-output tier exactly 37.2% of the time — but is *never*
off by more than one tier, which is closer to what a draft-day tier board
actually needs than exact-tier accuracy alone would suggest.

What's Next
-----------

This chapter validated a *projection* against outcomes it never saw.
:doc:`13_league_history` turns the same stored league data
(:doc:`04_capturing_a_league`) toward a different question — not "what
will happen," but "what already did," across your league's entire real
history.
