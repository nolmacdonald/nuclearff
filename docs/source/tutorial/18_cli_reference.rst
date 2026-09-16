.. _tutorial_cli_reference:

18. CLI Reference
========================

Chapters 1 through 17 covered ``nuclearff`` as a Python library. Every one
of those operations is also available as a single command-line tool,
``nuclearff``, for when you want a repeatable, scriptable command instead
of a Python session. This chapter is a complete reference for it —
nothing here introduces new functionality; it's the same library, wrapped.

Every command reads a configuration file (``configs/nuclearff.yaml`` by
default, or whatever ``-c``/``--config`` points at) and writes beneath
``paths.root`` (override with ``--root``):

.. code-block:: text

   $ nuclearff --help
   usage: nuclearff [-h] [--version] [-c CONFIG] [--root ROOT]
                    [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
                    <group> ...

   positional arguments:
     <group>
       config              Inspect and create configuration
       sleeper             Read-only Sleeper API access
       ids                 Cross-source player identity resolution
       report              Build draft boards and reports

   options:
     -h, --help            show this help message and exit
     --version             show program's version number and exit
     -c, --config CONFIG   Configuration file (default: configs/nuclearff.yaml
                           when present)
     --root ROOT           Override paths.root; every managed directory resolves
                           beneath it
     --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                           Console log level (default: the configured run.log_level)

``config`` — project configuration
------------------------------------------

.. code-block:: text

   $ nuclearff config init [-o OUT] [--force]
   $ nuclearff config show
   $ nuclearff config paths [--ensure]

- ``init`` writes a default configuration file (see :doc:`02_configuration`'s
  ``default_config``); ``--force`` overwrites an existing one.
- ``show`` prints the fully resolved configuration as YAML.
- ``paths`` prints every managed directory and whether it exists;
  ``--ensure`` creates any that are missing.

``sleeper`` — read-only Sleeper API access
--------------------------------------------------

.. code-block:: text

   $ nuclearff sleeper state
   $ nuclearff sleeper fetch-league --league-id LEAGUE_ID [--history] [--standings]
       [--matchups] [--transactions] [--roster-players] [--drafts]
       [--max-seasons N] [--max-week N]
   $ nuclearff sleeper fetch-players [--force-refresh]
   $ nuclearff sleeper fetch-projections --season YEAR --week N [--through-week N]
       [--positions POS [POS ...]]
   $ nuclearff sleeper user-leagues USERNAME --season YEAR [--sport SPORT]
   $ nuclearff sleeper user-drafts USERNAME --season YEAR [--sport SPORT]
   $ nuclearff sleeper trending [--kind {add,drop}] [--lookback-hours N] [--limit N]

``fetch-league`` is the CLI equivalent of :doc:`04_capturing_a_league`'s
functions, wired together in one call — every flag below **implies**
``--history`` (you don't need to pass both), and each writes its own
table without disturbing the others:

.. code-block:: text

   $ nuclearff --root ./demo sleeper fetch-league --league-id 1367225133634191360 \
       --history --standings --matchups --transactions --roster-players --drafts \
       --max-week 3
   League:   NUCLEARFF REDRAFT (1367225133634191360)
   Season:   2026 status=in_season
   ...
   History:  6 season(s) walked
   Standings: 60 roster-season(s)
   Matchups: 180 roster-week row(s)
   Transactions: 365
   Roster players: 945

``--max-seasons`` (default 20) caps how far ``--history`` walks back;
``--max-week`` (default 18) caps how many weeks ``--matchups``/
``--transactions`` fetch per season.

``fetch-players`` writes the full Sleeper player map to DuckDB;
``fetch-projections`` fetches one or more weeks of Sleeper's own player
projections. ``user-leagues``/``user-drafts`` resolve a username to their
leagues or drafts for a season. ``trending`` prints the most-added or
most-dropped players league-wide.

``ids`` — cross-source identity resolution
--------------------------------------------------

.. code-block:: text

   $ nuclearff ids resolve-gsis

Fills missing Sleeper ``gsis_id`` values from the nflverse ``ff_playerids``
crosswalk (:doc:`04_capturing_a_league`). Requires ``fetch-players`` to
have run first.

``report`` — draft boards, reports, and visualizations
------------------------------------------------------------

.. code-block:: text

   $ nuclearff report auction-board LEAGUE_ID --seasons YEAR [YEAR ...] --as-of-season YEAR
       [--baseline {vols,vorp}] [--top N] [--out-dir DIR] [--no-tables]

Builds the full auction board (:doc:`09_auction_draft_board`): CSV,
markdown report, and PNG position tables (unless ``--no-tables``).

.. code-block:: text

   $ nuclearff report playoff-bracket LEAGUE_ID --season YEAR
       [--league-name NAME] [--out-dir DIR]

Renders a completed season's winners/losers playoff brackets
(:doc:`10_draft_and_playoff_visuals`). Requires ``--standings`` to have
been run for that league and season first.

.. code-block:: text

   $ nuclearff report draft-board LEAGUE_ID [--draft-id ID] [--out PATH]

Renders a draft as a snake-order grid (:doc:`10_draft_and_playoff_visuals`).
Omitting ``--draft-id`` uses the league's most recent draft.

.. code-block:: text

   $ nuclearff report trades LEAGUE_ID [--out-dir DIR] [--all-users]
   $ nuclearff report wins LEAGUE_ID [--out PATH] [--all-users]
   $ nuclearff report draft-order LEAGUE_ID [--out PATH] [--all-users]

``trades`` renders all ten trade-history visualizations
(:doc:`14_trade_network`); ``wins`` renders the cumulative-wins step chart
(:doc:`15_wins_and_leagues`); ``draft-order`` renders a manager's draft-slot
history (:doc:`10_draft_and_playoff_visuals`). All three default to only
managers currently rostered in ``league_id``'s own season —
``--all-users`` includes every manager across the league's full history
instead.

.. code-block:: text

   $ nuclearff report on-this-day LEAGUE_ID [--date YYYY-MM-DD] [--out PATH]

Renders transactions matching today's (or a given) calendar-date
anniversary, across a league's full history (:doc:`13_league_history`).

.. code-block:: text

   $ nuclearff report performance LEAGUE_ID --week N [--top-n N] [--all-players] [--out PATH]
   $ nuclearff report season-performance --season YEAR [--league-id ID]
       [--top-n N] [--min-games N] [--all-players] [--out PATH]

Render actual-vs-projected over/underperformer tables for one week or a
full season (:doc:`15_wins_and_leagues`'s "Actual vs. projected
performance"). Both default to starters only — ``--all-players`` includes
bench players. ``season-performance``'s ``--min-games`` (default 3) keeps
a single huge-delta week from dominating a season ranking.

.. code-block:: text

   $ nuclearff report user-leagues USERNAME --season YEAR [--sport SPORT] [--out PATH]

Renders a Sleeper user's leagues for a season as a PNG table
(:doc:`15_wins_and_leagues`).

Where to go from here
----------------------------

That's every ``nuclearff`` command. If a command's behavior isn't clear
from its flags, the corresponding tutorial chapter above walks through
the same functionality as plain Python — often the faster way to
understand *why* a command produces what it does, since every wiring
decision is visible in code rather than hidden behind a flag. The
:doc:`../api/index` has the full reference for every public class and
function used throughout this tutorial.
