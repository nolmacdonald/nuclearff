.. _tutorial_configuration:

2. Configuration
================

``nuclearff`` reads two different kinds of configuration, and it's worth
telling them apart before going further:

- :class:`~nuclearff.config.models.NuclearffConfig` — *your* settings: where
  data lives on disk, and the projection model's knobs (recency weights, age
  curve, simulation settings). You write this once per project.
- :class:`~nuclearff.config.league.LeagueConfig` — *the league's* settings:
  scoring rules and roster shape, derived from a real Sleeper league. You get
  one of these per league, and it's derived from Sleeper rather than
  hand-written.

Project configuration
----------------------

:func:`~nuclearff.config.loader.default_config` returns a
:class:`~nuclearff.config.models.NuclearffConfig` populated with documented
defaults, ready to write to disk:

.. code-block:: python

   from nuclearff.config import default_config, dump_config, load_config

   cfg = default_config()
   path = dump_config(cfg, "./demo/configs/nuclearff.yaml")
   print(path)

.. code-block:: text

   demo/configs/nuclearff.yaml

.. code-block:: yaml

   model:
     age_curve:
       decline_per_year: 0.025
       min_factor: 0.85
       peak_age: 26
       plateau_end: 28
     baseline: vols
     flex_wr_rate: 0.5
     name: wr_default
     recency:
       weights: [0.5, 0.3, 0.2]
     seasons: [2023, 2024, 2025]
     simulation:
       ceiling_percentile: 0.9
       floor_percentile: 0.1
       games: 17
       n_simulations: 10000
     thresholds:
       min_games: 8
       min_routes: 200
       min_targets: 50
     weights:
       efficiency: 0.25
       expected_td: 0.1
       situation: 0.15
       volume: 0.5
   paths:
     configs: configs
     data: data
     root: .
   run:
     log_level: INFO
     season: 2026
     seed: 2026

Every field here is documented on
:class:`~nuclearff.config.models.ModelConfig` and its nested settings — the
recency weights and weight blocks drive :doc:`07_projections`, the age curve
and simulation settings drive :doc:`11_simulation`. Reload it with
:func:`~nuclearff.config.loader.load_config`, and pass ``root=`` to relocate
the whole managed directory tree — this is how every example in this
tutorial keeps its output in a scratch directory instead of your real
project data:

.. code-block:: python

   cfg = load_config(path, root="./demo")
   cfg.paths.ensure()

   for directory in cfg.paths.all_dirs():
       print(directory.is_dir(), directory)

.. code-block:: text

   True demo/data/raw
   True demo/data/processed
   True demo/data/cache
   True demo/data/manifests
   True demo/data/artifacts
   True demo/configs/leagues

``paths.root`` anchors every one of those — :attr:`~nuclearff.config.models.PathsConfig.cache_dir`
is where the Sleeper client caches the player map and where the shared
DuckDB database lives (:doc:`04_capturing_a_league` onward);
:attr:`~nuclearff.config.models.PathsConfig.artifacts_dir` is where every
figure and report in this tutorial gets written;
:attr:`~nuclearff.config.models.PathsConfig.leagues_dir` is where a league's
derived ``LeagueConfig`` (below) is stored.

League configuration
----------------------

A :class:`~nuclearff.config.league.LeagueConfig` is *derived* from a real
Sleeper league, not hand-written. Fetch the league and convert it:

.. code-block:: python

   from nuclearff.sleeper import SleeperClient
   from nuclearff.config import league_config_from_sleeper, dump_league_config

   league_id = "1367225133634191360"

   with SleeperClient() as client:
       league_json = client.get_league(league_id)

   league_cfg = league_config_from_sleeper(league_json)
   print(league_cfg.name, league_cfg.season, league_cfg.num_teams)
   print(league_cfg.roster.counts)

.. code-block:: text

   NUCLEARFF REDRAFT 2026 10
   {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 3, 'BN': 6}

:class:`~nuclearff.config.league.ScoringSettings` and
:class:`~nuclearff.config.league.RosterSlots` both store their data as a
generic mapping (``scoring.values``, ``roster.counts``) rather than one
named field per Sleeper key — a league can send scoring or roster-slot keys
this project has never seen (an IDP league's tackle/sack keys, a superflex
slot), and a generic mapping absorbs them instead of failing to parse:

.. code-block:: python

   print(league_cfg.scoring.values["rec"], league_cfg.scoring.values["pass_td"])

.. code-block:: text

   1.0 6.0

Save it so it can be reloaded without hitting Sleeper again:

.. code-block:: python

   from nuclearff.config import load_league_config

   path = dump_league_config(
       league_cfg, cfg.paths.leagues_dir / f"{league_cfg.league_id}.yaml"
   )
   reloaded = load_league_config(path)
   assert reloaded == league_cfg

Replacement rank and starter demand
----------------------------------------

``LeagueConfig`` is where value-based drafting's replacement level comes
from (used throughout :doc:`08_valuation`). ``wr_starter_demand`` (and the
more general ``starter_demand`` for any position) accounts for locked WR
slots *and* the assumed WR share of FLEX slots:

.. code-block:: python

   print("WR starter demand:", league_cfg.wr_starter_demand())
   print("WR replacement rank (vols):", league_cfg.replacement_rank("WR"))
   print("WR replacement rank (vorp):", league_cfg.replacement_rank("WR", baseline="vorp"))
   print("RB replacement rank (vols):", league_cfg.replacement_rank("RB"))

.. code-block:: text

   WR starter demand: 35.0
   WR replacement rank (vols): 35
   WR replacement rank (vorp): 53
   RB replacement rank (vols): 30

For this league — 10 teams, 2 WR + 3 FLEX slots, FLEX assumed 50% WR by
default — VOLS (Value Over Last Starter) says the 35th-best WR is
replacement level. VORP (Value Over Replacement Player) goes deeper,
adding a fraction of the league's bench slots on the theory that a real
share of bench spots are speculative WR stashes — landing at rank 53
instead. Neither number is "the" replacement level; they're two different,
named assumptions about where a bench player becomes replaceable, and
:doc:`08_valuation` uses both.

What's Next
-----------

With a project configuration and a real league's ``LeagueConfig`` in hand,
:doc:`03_sleeper_api` covers the Sleeper API itself — everything
``league_config_from_sleeper`` above reads from, and every other endpoint
Sleeper exposes.
