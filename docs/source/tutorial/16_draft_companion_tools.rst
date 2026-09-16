.. _tutorial_draft_companion_tools:

16. In Progress: Draft-Day Matchup Tools
===============================================

:mod:`nuclearff.matchups` and :mod:`nuclearff.draft` are the start of a
larger, still-unfinished feature area — a live draft companion. Only two
functions exist so far, each real and independently useful, but neither
is wired into a report, a visualization, or the CLI yet. This chapter
covers what's actually there today.

Defense-vs-position
-------------------------

:func:`~nuclearff.matchups.dvp.points_allowed_by_position` aggregates real
fantasy points allowed, by position, per NFL defense — the raw signal
behind "this team is a soft matchup for wide receivers":

.. code-block:: python

   from nuclearff.config import load_league_config
   from nuclearff.scoring import ScoringEngine
   from nuclearff.nflverse import load_weekly_skill_stats
   from nuclearff.matchups.dvp import points_allowed_by_position

   league_cfg = load_league_config("./demo/configs/leagues/1367225133634191360.yaml")
   engine = ScoringEngine(league_cfg.scoring)

   weekly = load_weekly_skill_stats([2024])
   dvp = points_allowed_by_position(weekly, engine)
   print(
       dvp.filter(dvp["position"] == "WR")
       .sort("points_allowed", descending=True)
       .head(3)
   )

.. code-block:: text

   shape: (3, 6)
   ┌────────┬──────┬───────────────┬──────────┬────────────────┬───────────────────────────┐
   │ season ┆ week ┆ opponent_team ┆ position ┆ points_allowed ┆ season_avg_points_allowed │
   ╞════════╪══════╪═══════════════╪══════════╪════════════════╪═══════════════════════════╡
   │ 2024   ┆ 5    ┆ TB            ┆ WR       ┆ 92.2           ┆ 36.47                     │
   │ 2024   ┆ 5    ┆ BAL           ┆ WR       ┆ 75.5           ┆ 35.78                     │
   │ 2024   ┆ 11   ┆ JAX           ┆ WR       ┆ 73.6           ┆ 36.64                     │
   └────────┴──────┴───────────────┴──────────┴────────────────┴───────────────────────────┘

Points allowed are scored under this league's own rules
(:doc:`05_scoring_engine`), not a generic formula — the same real 2024
game where Tampa Bay allowed 92.2 real fantasy points to opposing WRs
would score differently under a half-PPR league.

Strength of schedule
--------------------------

:func:`~nuclearff.draft.sos.strength_of_schedule` averages a team's
*remaining* opponents' points-allowed at a position — how favorable a
draft candidate's rest-of-season schedule looks, given where they play:

.. code-block:: python

   from nuclearff.nflverse.schedules import load_schedules
   from nuclearff.draft.sos import strength_of_schedule

   schedules = load_schedules([2024])
   sos = strength_of_schedule("SF", "WR", dvp, schedules, 2024, remaining_weeks=[15, 16, 17])
   print(f"49ers WR strength of schedule, weeks 15-17: {sos:.1f}")

.. code-block:: text

   49ers WR strength of schedule, weeks 15-17: 33.0

A higher number means the 49ers' real weeks 15-17 opponents allowed more
real fantasy points to opposing WRs on average that season — a softer
remaining schedule for a 49ers wide receiver than a lower number would
indicate.

What's still missing
--------------------------

Both functions above are building blocks, not a finished feature — there
is no report, no CLI command, and no visualization built on top of either
yet, and the wider draft-companion epic they belong to
(`GitHub issue #102 <https://github.com/nolmacdonald/nuclearff/issues/102>`_)
covers a live draft-pick recommendation tool this is only the first piece
of. Nothing here should be read as a finished, supported feature the way
Chapters 5 through 15 are — check the project's GitHub issues for current
status before building on it.

What's Next
-----------

:doc:`17_querying_and_provenance` closes out this tutorial: one shared
database for everything built across every chapter, and how to record
exactly how a given dataset or report was produced.
