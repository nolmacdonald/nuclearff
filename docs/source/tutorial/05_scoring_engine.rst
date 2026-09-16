.. _tutorial_scoring_engine:

5. The Scoring Engine
============================

``nflreadpy``'s own ``fantasy_points`` column (:doc:`01_introduction`) uses
one generic scoring formula. Real leagues don't all score the same way —
this league gives a full point per reception and 6 points per passing
touchdown; another league might run half-PPR and 4-point passing
touchdowns. :class:`~nuclearff.scoring.engine.ScoringEngine` closes that
gap: it applies *this league's own* :class:`~nuclearff.config.league.ScoringSettings`
(:doc:`02_configuration`) to real nflverse stat lines.

Scoring a single stat line
------------------------------

:meth:`~nuclearff.scoring.engine.ScoringEngine.score_stat_line` is the
reference, hand-verifiable path — pure Python over one dict of nflverse
column names:

.. code-block:: python

   from nuclearff.config import load_league_config
   from nuclearff.scoring import ScoringEngine

   league_cfg = load_league_config("./demo/configs/leagues/1367225133634191360.yaml")
   engine = ScoringEngine(league_cfg.scoring)

   line = {"receptions": 8, "receiving_yards": 100, "receiving_tds": 1, "position": "WR"}
   print(engine.score_stat_line(line))

.. code-block:: text

   8 (rec) + 10.0 (yards/10) + 6.0 (TD) = 24.0

A stat the line doesn't include is treated as zero — you don't need to pad
out every possible key, just the ones a real player recorded. Pass
``"position"`` to get position-conditional reception bonuses
(``bonus_rec_wr``/``bonus_rec_te``/``bonus_rec_rb``) — without it, those
bonuses contribute nothing.

Scoring a real season, vectorized
---------------------------------------

:meth:`~nuclearff.scoring.engine.ScoringEngine.score_frame` is the
vectorized equivalent, built for real multi-row data straight out of
``nflreadpy``:

.. code-block:: python

   from nuclearff.nflverse import load_seasonal_skill_stats

   stats = load_seasonal_skill_stats([2024])
   scored = engine.score_frame(stats)

   print(
       scored.select(["player_display_name", "position", "season", "fantasy_points"])
       .sort("fantasy_points", descending=True)
       .head(6)
   )

.. code-block:: text

   shape: (6, 4)
   ┌──────────────────────┬──────────┬────────┬─────────────────┐
   │ player_display_name  ┆ position ┆ season ┆ fantasy_points  │
   │ str                  ┆ str      ┆ i32    ┆ f64             │
   ╞══════════════════════╪══════════╪════════╪═════════════════╡
   │ Lamar Jackson         ┆ QB       ┆ 2024   ┆ 512.38          │
   │ Joe Burrow            ┆ QB       ┆ 2024   ┆ 458.82          │
   │ Baker Mayfield        ┆ QB       ┆ 2024   ┆ 447.8           │
   │ Josh Allen            ┆ QB       ┆ 2024   ┆ 435.04          │
   │ Jayden Daniels        ┆ QB       ┆ 2024   ┆ 405.82          │
   │ Ja'Marr Chase         ┆ WR       ┆ 2024   ┆ 403.0           │
   └──────────────────────┴──────────┴────────┴─────────────────┘

That's this league's real 6-point passing touchdowns and full-PPR scoring
showing up immediately: five of the top six 2024 fantasy seasons under
these rules are quarterbacks, not the running backs and receivers a
half-PPR, 4-point-passing-touchdown league would rank there instead.

Unscored keys
-----------------

A league's ``scoring_settings`` payload usually includes keys
``ScoringEngine`` has no nflverse column for — kicker, defense/special
teams, and IDP (individual defensive player) keys, none of which appear in
standard offensive box scores. Constructing the engine logs a warning
listing every nonzero key it can't account for;
:meth:`~nuclearff.scoring.engine.ScoringEngine.unscored_keys` returns the
same list programmatically:

.. code-block:: python

   unscored = engine.unscored_keys()
   print(len(unscored), "unscored keys, e.g.:", sorted(unscored)[:5])

.. code-block:: text

   48 unscored keys, e.g.: ['blk_kick', 'blk_kick_ret_yd', 'def_3_and_out', 'def_4_and_stop', 'def_pr_yd']

This is expected, not a bug to fix — recall from :doc:`04_capturing_a_league`
that this exact league's own anomaly detection flagged
``no_kicker_or_defense``: it has no kicker, defense, or IDP roster slots at
all, so those 48 scoring keys in the payload are inert regardless. A league
that *does* roster a kicker or defense would need its own K/DEF stat source
joined in before those keys mean anything — ``ScoringEngine`` only scores
whatever stat columns you hand it.

What's Next
-----------

Raw fantasy points are one number per player-season. :doc:`06_metrics`
computes the underlying opportunity and efficiency signals — target share,
air yards, yards per route run — that explain *why* a receiver scored what
they did, and which of it is likely to repeat.
