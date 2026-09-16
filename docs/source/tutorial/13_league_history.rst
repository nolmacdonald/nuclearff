.. _tutorial_league_history:

13. League History and Records
=====================================

:doc:`04_capturing_a_league` persisted six real seasons of standings,
matchups, and transactions. :mod:`nuclearff.archive` mines that same
stored data for a completely different purpose than valuation — a
league's own record book: champions, blowouts, rivalries, draft steals and
busts, and a full career profile per manager. Every function here is
read/aggregate-only: no new Sleeper fetching, just real history already on
disk.

Championship history
--------------------------

:func:`~nuclearff.archive.champions.championship_history` is the shared
foundation the rest of this chapter joins against — one row per season:
champion, runner-up, and regular-season leader:

.. code-block:: python

   from nuclearff.duckdb_io import read_table
   from nuclearff.archive.champions import championship_history

   db_path = "./demo/data/cache/nuclearff.duckdb"
   standings = read_table(db_path, "sleeper_standings")

   champs = championship_history(standings)
   print(champs.sort("season").select(
       ["season", "champion_display_name", "runner_up_display_name", "regular_season_leader_display_name"]
   ))

.. code-block:: text

   shape: (6, 4)
   ┌────────┬─────────────────────────┬──────────────────────────┬───────────────────────────────────┐
   │ season ┆ champion_display_name   ┆ runner_up_display_name   ┆ regular_season_leader_display_name │
   ╞════════╪═════════════════════════╪══════════════════════════╪═════════════════════════════════════╡
   │ 2021   ┆ bigTETONclimber          ┆ casitzmann                ┆ aperry151                            │
   │ 2022   ┆ null                     ┆ nolmacdonald              ┆ null                                 │
   │ 2023   ┆ ksavabi                  ┆ hyoga10                   ┆ hyoga10                              │
   │ 2024   ┆ aperry151                ┆ ksavabi                   ┆ casitzmann                           │
   │ 2025   ┆ nolmacdonald             ┆ ksavabi                   ┆ casitzmann                           │
   │ 2026   ┆ null                     ┆ null                      ┆ thatbolb                             │
   └────────┴─────────────────────────┴──────────────────────────┴───────────────────────────────────┘

Real, honest nulls in both directions: 2022's champion display name is
null because that season's champion roster's owner isn't resolvable in
that season's user list (the same real Sleeper data quirk
:doc:`14_trade_network` runs into for trades); 2026 has no champion yet
because the season is still in progress — ``championship_history`` doesn't
guess at an unfinished season.

Records: extremes and streaks
------------------------------------

:func:`~nuclearff.archive.records.score_extremes` and
:func:`~nuclearff.archive.records.margin_extremes` scan every real matchup
for the league's all-time highest/lowest score and biggest
blowout/closest margin:

.. code-block:: python

   from nuclearff.archive.records import score_extremes, margin_extremes, longest_streak
   from nuclearff.sleeper.wins import weekly_results

   matchups = read_table(db_path, "sleeper_matchups")
   print(score_extremes(matchups, standings))
   print(margin_extremes(matchups, standings))

.. code-block:: text

   kind      manager     season week points  opponent
   highest   thatbolb    2022   2    206.41  hyoga10
   lowest    casitzmann  2026   2    0.0     Donkeysride

   kind             season week winner    winner_points loser         loser_points margin
   biggest_blowout  2022   2    thatbolb  206.41         hyoga10       100.78       105.63
   closest_margin   2024   2    hyoga10   140.22         jwhitney0220  140.16       0.06

:func:`~nuclearff.archive.records.longest_streak` needs each roster's
real per-week win/loss, from :func:`~nuclearff.sleeper.wins.weekly_results`
(:doc:`15_wins_and_leagues` uses the same function for cumulative wins):

.. code-block:: python

   results = weekly_results(matchups)
   win_streaks = longest_streak(results, standings, "win")
   print(win_streaks.sort("length", descending=True).head(3))

.. code-block:: text

   shape: (3, 7)
   ┌───────────┬─────────────┬────────┬──────────────┬────────────┬────────────┬──────────┐
   │ manager   ┆ streak_type ┆ length ┆ start_season ┆ start_week ┆ end_season ┆ end_week │
   ╞═══════════╪═════════════╪════════╪══════════════╪════════════╪════════════╪══════════╡
   │ aperry151 ┆ win         ┆ 8      ┆ 2021         ┆ 5          ┆ 2021       ┆ 12       │
   │ hyoga10   ┆ win         ┆ 6      ┆ 2023         ┆ 7          ┆ 2023       ┆ 12       │
   │ ksavabi   ┆ win         ┆ 6      ┆ 2023         ┆ 11         ┆ 2023       ┆ 17       │
   └───────────┴─────────────┴────────┴──────────────┴────────────┴────────────┴──────────┘

Pass ``"loss"`` for ``streak_type`` to get the same for losing streaks
instead.

Rivalries: head-to-head
-----------------------------

:func:`~nuclearff.archive.rivalries.head_to_head` returns the all-time
combined record for every manager pair that has ever met:

.. code-block:: python

   from nuclearff.archive.rivalries import head_to_head

   h2h = head_to_head(matchups, standings)
   print(h2h.sort("games", descending=True).select(
       ["manager_a", "manager_b", "games", "wins_a", "wins_b", "biggest_blowout_margin"]
   ).head(3))

.. code-block:: text

   shape: (3, 6)
   ┌───────────┬──────────────┬───────┬────────┬────────┬────────────────────────┐
   │ manager_a ┆ manager_b    ┆ games ┆ wins_a ┆ wins_b ┆ biggest_blowout_margin │
   ╞═══════════╪══════════════╪═══════╪════════╪════════╪════════════════════════╡
   │ aperry151 ┆ hyoga10      ┆ 15    ┆ 7      ┆ 8      ┆ 61.34                  │
   │ aperry151 ┆ casitzmann   ┆ 12    ┆ 2      ┆ 10     ┆ 64.02                  │
   │ ksavabi   ┆ nolmacdonald ┆ 12    ┆ 5      ┆ 7      ┆ 63.64                  │
   └───────────┴──────────────┴───────┴────────┴────────┴────────────────────────┘

This is the combined regular-season **and** playoff record — a
regular-season/playoff split is tracked as a real gap (GitHub Issue #137,
blocked on a ``LeagueConfig.playoff_week_start`` field this league
history epic shares with the draft-companion epic), not silently modeled
as if the two were identical.

Draft retrospectives
--------------------------

:func:`~nuclearff.archive.draft_retro.grade_historical_picks` grades every
historical pick against how the player *actually* performed that
season — a real final-output number, not a pre-draft projection. It needs
:func:`~nuclearff.sleeper.performance.season_actuals` (exploded weekly
box scores) concatenated across every season on file:

.. code-block:: python

   import polars as pl
   from nuclearff.sleeper.performance import season_actuals
   from nuclearff.archive.draft_retro import grade_historical_picks

   picks = read_table(db_path, "sleeper_draft_picks")
   actuals = pl.concat(
       [season_actuals(matchups, standings, lid) for lid in standings["league_id"].unique()],
       how="diagonal_relaxed",
   )
   graded = grade_historical_picks(picks, actuals, standings).filter(pl.col("season") < 2026)

   print(
       graded.sort("rank_delta", descending=True)
       .select(["season", "manager", "player_name", "round", "pick_no", "expected_rank", "actual_rank", "rank_delta"])
       .head(3)
   )

.. code-block:: text

   shape: (3, 8)
   ┌────────┬─────────┬─────────────┬───────┬─────────┬───────────────┬─────────────┬────────────┐
   │ season ┆ manager ┆ player_name ┆ round ┆ pick_no ┆ expected_rank ┆ actual_rank ┆ rank_delta │
   ╞════════╪═════════╪═════════════╪═══════╪═════════╪═══════════════╪═════════════╪════════════╡
   │ 2024   ┆ ksavabi ┆ Jared Goff  ┆ 14    ┆ 139     ┆ 139           ┆ 6           ┆ 133        │
   └────────┴─────────┴─────────────┴───────┴─────────┴───────────────┴─────────────┴────────────┘

A real, dramatic 2024 steal: Jared Goff, picked as the 139th-ranked player
by draft position, actually finished as the real 6th-best player in the
league that season. Sort ascending instead for busts — this real
history's worst is a 2024 Christian McCaffrey pick, drafted 1st overall
and finishing 130th due to a real season-ending injury.
``sleeper_draft_picks.is_keeper`` is a known, unresolved confound here — a
keeper pick's draft slot reflects a league rule, not a real assessment of
the player's value, and this function leaves filtering it to the caller
rather than silently excluding keeper leagues' data.

Franchise profiles
------------------------

:func:`~nuclearff.archive.franchise.franchise_profile` assembles
everything above into one row per manager — pure assembly, no new stat
computed:

.. code-block:: python

   from nuclearff.archive.franchise import franchise_profile
   from nuclearff.sleeper.trades import load_trades, manager_trade_counts
   from nuclearff.sleeper import draft_order_stats

   se, me = score_extremes(matchups, standings), margin_extremes(matchups, standings)
   win_streaks = longest_streak(results, standings, "win")
   loss_streaks = longest_streak(results, standings, "loss")
   trade_counts = manager_trade_counts(load_trades(db_path))
   draft_stats = draft_order_stats(picks, standings)

   profile = franchise_profile(standings, champs, trade_counts, draft_stats, se, me, win_streaks, loss_streaks)
   print(
       profile.sort("career_wins", descending=True)
       .select(["manager", "seasons_played", "career_wins", "career_losses", "championships", "records_held"])
       .head(4)
   )

.. code-block:: text

   shape: (4, 6)
   ┌─────────────┬────────────────┬─────────────┬───────────────┬───────────────┬──────────────────────────────┐
   │ manager     ┆ seasons_played ┆ career_wins ┆ career_losses ┆ championships ┆ records_held                 │
   ╞═════════════╪════════════════╪═════════════╪═══════════════╪═══════════════╪═══════════════════════════════╡
   │ casitzmann  ┆ 6              ┆ 61          ┆ 39            ┆ 0             ┆ [lowest_single_week_score]    │
   │ hyoga10     ┆ 6              ┆ 54          ┆ 46            ┆ 0             ┆ [closest_margin_win]          │
   │ aperry151   ┆ 6              ┆ 53          ┆ 47            ┆ 1             ┆ [longest_win_streak]          │
   │ thatbolb    ┆ 6              ┆ 53          ┆ 47            ┆ 0             ┆ [highest_single_week_score]   │
   └─────────────┴────────────────┴─────────────┴───────────────┴───────────────┴──────────────────────────────┘

The league's career wins leader (``casitzmann``, 61-39) has never won a
championship — a real, and real-feeling, fantasy football story that a
career-record table alone would hide without ``championships`` sitting
right next to it.

Team name history
------------------------

:func:`~nuclearff.archive.name_history.team_name_changes` tracks each
manager's chronological team-name history, keyed by ``owner_id`` (stable
across name changes) rather than the display name that changes with it:

.. code-block:: python

   from nuclearff.sleeper.roster_names import fetch_and_write_team_names
   from nuclearff.archive.name_history import team_name_changes

   roster_names = read_table(db_path, "sleeper_roster_names")
   names = team_name_changes(roster_names, standings)
   print(names.sort("change_count", descending=True).head(3))

.. code-block:: text

   shape: (3, 4)
   ┌─────────────────────┬──────────────┬───────────────┬──────────────┐
   │ owner_id            ┆ manager      ┆ name_sequence ┆ change_count │
   ╞═════════════════════╪══════════════╪═══════════════╪══════════════╡
   │ 332632476830679040  ┆ nolmacdonald ┆ [null, ...]   ┆ 0            │
   │ 451502081426059264  ┆ aperry151    ┆ [null, ...]   ┆ 0            │
   │ 460983681738076160  ┆ bigTETONclimber ┆ [null]     ┆ 0            │
   └─────────────────────┴──────────────┴───────────────┴──────────────┘

A real, honest zero across the board for this league: nobody in
``NUCLEARFF REDRAFT``'s real history has ever set a custom Sleeper team
name — every roster falls back to a null name every season, so
``change_count`` is genuinely 0 for everyone. A league that *does* use
custom team names would show real sequences and non-zero counts here
instead.

Waiver spend
-----------------

:func:`~nuclearff.archive.waiver_spend.manager_waiver_spend` and
:func:`~nuclearff.archive.waiver_spend.career_waiver_spend` turn stored
transactions into real FAAB spending history:

.. code-block:: python

   from nuclearff.archive.waiver_spend import manager_waiver_spend, career_waiver_spend

   transactions = read_table(db_path, "sleeper_transactions")
   spend = manager_waiver_spend(transactions)
   career = career_waiver_spend(spend)
   print(career.sort("total_spent", descending=True).head(3))

.. code-block:: text

   shape: (3, 5)
   ┌────────────────┬─────────────┬───────────────────┬───────────────┬──────────────────────┐
   │ manager        ┆ total_spent ┆ players_acquired   ┆ failed_claims ┆ avg_cost_per_player  │
   ╞════════════════╪═════════════╪════════════════════╪═══════════════╪══════════════════════╡
   │ nolmacdonald    ┆ 313         ┆ 61                 ┆ 114           ┆ 5.13                 │
   │ nawfeastdallas  ┆ 312         ┆ 57                 ┆ 54            ┆ 5.47                 │
   │ ksavabi         ┆ 300         ┆ 45                 ┆ 43            ┆ 6.67                 │
   └────────────────┴─────────────┴────────────────────┴───────────────┴──────────────────────┘

``nolmacdonald``'s real 114 failed claims against only 61 successful
acquisitions is the kind of detail a season-total spend number alone
would hide.

On this day
----------------

:func:`~nuclearff.archive.on_this_day.transactions_on_this_day` finds
every real transaction whose anniversary matches a given date —
:func:`~nuclearff.archive.on_this_day.transaction_summary_rows` turns that
into display-ready text, resolving player ids to names when a
``sleeper_players`` table is available:

.. code-block:: python

   from datetime import date
   from nuclearff.archive.on_this_day import transactions_on_this_day, transaction_summary_rows
   from nuclearff.ids import read_sleeper_players

   players = read_sleeper_players(db_path)
   on_this_day = transactions_on_this_day(transactions, date(2026, 9, 16))
   print(transaction_summary_rows(on_this_day, players).head(3))

.. code-block:: text

   shape: (3, 5)
   ┌──────┬────────────┬──────────────┬───────────────────────┬──────────────────────┐
   │ year ┆ type       ┆ parties      ┆ summary                ┆ group_id             │
   ╞══════╪════════════╪══════════════╪════════════════════════╪══════════════════════╡
   │ 2026 ┆ free_agent ┆ aperry151     ┆ added Tyler Shough     ┆ 1406038479384674304 │
   │ 2026 ┆ free_agent ┆ nolmacdonald  ┆ dropped Brian Robinson ┆ 1405983340279906304 │
   │ 2025 ┆ waiver     ┆ nolmacdonald  ┆ added Juwan Johnson    ┆ 1273851136817790976 │
   └──────┴────────────┴──────────────┴────────────────────────┴──────────────────────┘

What's Next
-----------

League history above was keyed by manager and season. :doc:`14_trade_network`
zooms into one specific relationship — who trades with whom — as ten
different visualizations from the same stored transaction data.
