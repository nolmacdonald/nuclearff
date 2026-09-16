.. _tutorial_simulation:

11. Monte Carlo Season Simulation
========================================

A projection (:doc:`07_projections`) is a single number — a mean.
:mod:`nuclearff.simulation.montecarlo` turns a mean and a spread into a
full distribution of plausible season outcomes: a floor, a median, a
ceiling, and the probability of clearing any threshold you care about.

Simulating a real player's season
---------------------------------------

:func:`~nuclearff.simulation.montecarlo.simulate_player_season` needs a
mean and standard deviation of *weekly* fantasy points, plus an optional
skew (positive skew models the real shape of a fantasy scorer's week —
occasional big games pulling the tail right, more often than a symmetric
normal distribution would predict). Compute those from a real player's
actual 2024 weekly output under this league's scoring:

.. code-block:: python

   import polars as pl
   from scipy.stats import skew
   from nuclearff.config import load_league_config
   from nuclearff.scoring import ScoringEngine
   from nuclearff.nflverse import load_weekly_skill_stats
   from nuclearff.simulation import simulate_player_season, summarize_distribution
   from nuclearff.config.models import SimulationConfig

   league_cfg = load_league_config("./demo/configs/leagues/1367225133634191360.yaml")
   engine = ScoringEngine(league_cfg.scoring)

   weekly = engine.score_frame(load_weekly_skill_stats([2024]))
   chase_weeks = weekly.filter(pl.col("player_display_name") == "Ja'Marr Chase")["fantasy_points"].to_numpy()

   mean_ppg, sd_ppg, skew_val = float(chase_weeks.mean()), float(chase_weeks.std()), float(skew(chase_weeks))
   print(f"games={len(chase_weeks)} mean={mean_ppg:.2f} sd={sd_ppg:.2f} skew={skew_val:.2f}")

.. code-block:: text

   games=17 mean=23.71 sd=12.48 skew=1.13

That's Ja'Marr Chase's real, full 2024 season under this league's actual
6-point-passing-TD, full-PPR rules: a real skew of 1.13, well above a
symmetric distribution's 0 — his week-to-week scoring really does have a
long right tail of huge games, not just a wide spread around the mean.
Simulate 10,000 full seasons from those three numbers:

.. code-block:: python

   samples = simulate_player_season(mean_ppg, sd_ppg, skew_val, games=17, n_simulations=10000, seed=2026)
   print(samples.shape, round(samples.mean(), 1))

.. code-block:: text

   (10000,) 402.9

``samples`` is 10,000 simulated season totals — one full 17-game season
per row, resampled from the fitted skew-normal weekly distribution. The
mean of 10,000 simulated seasons (402.9) closely tracks the simple
``mean_ppg * 17`` estimate (403.1), which is exactly what a well-calibrated
simulation should do.

Summarizing the distribution
-----------------------------------

Raw samples aren't useful on their own — reduce them to the numbers you'd
actually put in a report with
:func:`~nuclearff.simulation.montecarlo.summarize_distribution`:

.. code-block:: python

   sim_cfg = SimulationConfig()  # floor=p10, ceiling=p90 by default
   summary = summarize_distribution(samples, sim_cfg, top_n_threshold=330.0)
   print(summary)

.. code-block:: python

   {
       'floor': 338.73,
       'median': 401.85,
       'ceiling': 469.26,
       'mean': 402.93,
       'p_exceeds_threshold': 0.9259,
   }

Read against real 2024 WR1 fantasy-point totals, a 330-point threshold is
a real "finished as a top-flight WR1" bar — this simulation says Chase had
a 92.6% chance of clearing it, given only his own real 2024 week-to-week
volatility. ``floor``/``ceiling`` come from
:class:`~nuclearff.config.models.SimulationConfig`'s configurable
percentiles (10th/90th by default, from :doc:`02_configuration`) — not
hardcoded, so a more conservative *or* more aggressive range is a config
change, not a code change.

Driving simulation from config
-------------------------------------

:func:`~nuclearff.simulation.montecarlo.simulate_from_config` is the same
simulation, but reading its knobs (``games``, ``n_simulations``) straight
from a :class:`~nuclearff.config.models.SimulationConfig` instead of
repeating them as keyword arguments — the version you'd actually wire into
a pipeline that already has a loaded project config:

.. code-block:: python

   from nuclearff.simulation import simulate_from_config

   samples = simulate_from_config(mean_ppg, sd_ppg, skew_val, sim_cfg, seed=2026)
   print(samples.shape, round(samples.mean(), 1))

.. code-block:: text

   (10000,) 403.0

What's Next
-----------

A simulation is only as good as the model behind its mean and spread.
:doc:`12_backtesting` covers how to actually check that — scoring a model
against real past seasons it never saw, rather than trusting its numbers
on faith.
