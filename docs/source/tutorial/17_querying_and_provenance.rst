.. _tutorial_querying_and_provenance:

17. Querying Everything, and Provenance
==============================================

Every chapter in this tutorial wrote into one shared DuckDB database. This
closing chapter covers querying that database directly, the low-level
helpers every ``fetch_and_write_*`` function is built on, and how to
record exactly which code, config, and data produced a given result.

Querying what you've built
-------------------------------

Every table from Chapters 4 through 16 lives in one file
(``<cache_dir>/nuclearff.duckdb``). Query it directly with DuckDB, or with
:func:`~nuclearff.duckdb_io.read_table` to get a :class:`polars.DataFrame`
back:

.. code-block:: python

   import duckdb
   from nuclearff.duckdb_io import read_table

   db_path = "./demo/data/cache/nuclearff.duckdb"

   conn = duckdb.connect(db_path, read_only=True)
   conn.sql("SHOW TABLES").show()

   standings = read_table(db_path, "sleeper_standings")

Every table is keyed by ``league_id`` (and usually ``season``), so joining
across them — "which player did the eventual champion trade for" or "how
many waiver claims did the team with the worst record make" — is ordinary
SQL, not custom Python for each question.

The write primitives underneath
--------------------------------------

:mod:`nuclearff.duckdb_io` has two ways to write a table, and the
difference between them mattered enough to cause (and then fix) a real
bug in this project. :func:`~nuclearff.duckdb_io.replace_table` drops and
rewrites a table wholesale — correct for a genuine single-source snapshot
like the Sleeper player map. :func:`~nuclearff.duckdb_io.merge_table`
replaces only the rows a given call actually owns, by key, leaving every
other row untouched:

.. code-block:: python

   from nuclearff.duckdb_io import merge_table

   merge_table(
       db_path,
       "sleeper_matchups",
       create_table_sql="CREATE TABLE sleeper_matchups (league_id VARCHAR, season INTEGER, week INTEGER, roster_id INTEGER, points DOUBLE)",
       columns=["league_id", "season", "week", "roster_id", "points"],
       rows=[["1367225133634191360", 2026, 2, 1, 118.4]],
       key_column="league_id",
       key_values=["1367225133634191360"],
   )

.. important::

   This distinction is not academic. A real, previously-undetected bug in
   this exact project: every ``fetch_and_write_*`` function used to call
   ``replace_table`` with just one league's own rows — which silently
   erased every *other* league's rows already in the same shared table,
   since ``replace_table`` always drops the whole table first. Confirmed
   live: fetching three unrelated leagues on the same account in sequence
   left only the last-fetched league's data surviving, with no error at
   any point. Every ``fetch_and_write_*`` function in Chapters 4, 10, 13,
   and 16 now uses ``merge_table`` instead — if you're writing your own
   multi-league pipeline against a table more than one source populates,
   use ``merge_table``, not ``replace_table``.

Provenance: recording how a result was built
--------------------------------------------------

A number in a report is only as trustworthy as your ability to reproduce
it. :mod:`nuclearff.provenance` records exactly what produced a given
artifact: the git commit, whether the working tree was clean, the config
that was used, and hashes of anything that matters.

.. code-block:: python

   from nuclearff.provenance import git_commit_sha, is_git_dirty, sha256_json
   from nuclearff.config import default_config

   cfg = default_config()
   print("commit:", git_commit_sha("."))
   print("dirty: ", is_git_dirty("."))
   print("config hash:", sha256_json(cfg.canonical_dict())[:16] + "...")

.. code-block:: text

   commit: 4776322aa6fe6f1b166fddc988cd0dd85d39b75f
   dirty:  True
   config hash: 2c3d3e2e417323e8...

:func:`~nuclearff.provenance.build_run_manifest` assembles all of this
into one :class:`~nuclearff.provenance.RunManifest`, and
:func:`~nuclearff.provenance.write_run_manifest` writes it as
deterministic JSON:

.. code-block:: python

   from nuclearff.provenance import build_run_manifest, write_run_manifest

   manifest = build_run_manifest(run_id="tutorial-demo-001", config=cfg, repo_root=".")
   path = write_run_manifest(manifest, "./demo/data/manifests")
   print(path)

.. code-block:: text

   demo/data/manifests/tutorial-demo-001.json

.. code-block:: json

   {
     "artifact_paths": [],
     "config_hash": "2c3d3e2e417323e85ae5c7789a75066b0bc58a411e9cca4f271a1e7441990dd0",
     "created_at": "2026-09-16T22:29:34.744705Z",
     "git_commit": "4776322aa6fe6f1b166fddc988cd0dd85d39b75f",
     "git_dirty": true,
     "package_version": "0.1.0",
     "python_version": "3.14.2",
     "run_id": "tutorial-demo-001",
     "seed": null,
     "source_hashes": {}
   }

``git_dirty: true`` here is real, not a placeholder — this environment had
an uncommitted, untracked directory when the manifest was built, and the
manifest says so plainly rather than hiding it. ``source_hashes`` accepts
:func:`~nuclearff.provenance.sha256_file` results for any raw input worth
pinning (a downloaded CSV, a hand-authored context-deltas file) alongside
the config hash — enough, together, to answer "what exact code, config,
and data produced this artifact" months later. ``build_run_manifest`` has
no CLI command yet; it's a library primitive for a future pipeline to call.

What's Next
-----------

That's every feature this tutorial covers as a Python library. The final
chapter, :doc:`18_cli_reference`, covers the same functionality one more
way — as a single ``nuclearff`` command-line tool, for when you want a
repeatable, scriptable command instead of a Python session.
