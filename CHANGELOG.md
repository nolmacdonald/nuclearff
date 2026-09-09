# Changelog

All notable changes to nuclearff will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial project structure with `src/` layout, scaffolded from the
  [ditto](https://github.com/nolmacdonald/ditto) template.
- `uv_build` build backend and `uv` dependency management with `dev` and
  `docs` extras; the lock file is authoritative and CI installs `--frozen`.
- `nuclearff` CLI entry point with `config` and `sleeper` command groups.
- Typed configuration models (`PathsConfig`, `RunConfig`, `ModelConfig`) with
  deterministic YAML round-tripping and a `--root` override for temporary
  directories.
- Read-only Sleeper API client with session reuse, retry and backoff on rate
  limits and transient server errors, request pacing, and a 24-hour disk cache
  for the player map.
- `nuclearff sleeper fetch-league`, which writes an immutable timestamped
  league snapshot and reports contradictory or load-bearing league settings.
- `nuclearff sleeper fetch-players`, which fetches the Sleeper NFL player map
  and stores a normalized subset of columns (including `gsis_id` and other
  cross-platform IDs) in a local DuckDB table for identity mapping against
  nflverse and other sources.
- `nuclearff sleeper fetch-league --history` (GitHub Issue 16) and
  `nuclearff.sleeper.leagues`: walks a league's `previous_league_id` chain
  backward via `SleeperClient.get_league`, reusing
  `config.league.league_config_from_sleeper` per hop, and persists both raw
  (`sleeper_leagues`, settings/scoring_settings/roster_positions as JSON
  columns) and parsed (`sleeper_league_configs`) tables to DuckDB via the
  existing `duckdb_io.replace_table` pattern. A hop that fails to fetch stops
  the walk without raising (its own `previous_league_id` is needed to
  continue); a hop that fetches but fails to type into `LeagueConfig` keeps
  its raw row with no config row, rather than aborting. Verified live against
  the real league: the chain reaches all the way back to the league's 2021
  inception (6 seasons, `1367225133634191360` -> ... -> `731562064849539072`),
  well past the technical plan's benchmark of reaching `1240509989819273216`,
  and both tables round-trip through DuckDB for every hop.
- `nuclearff sleeper fetch-league --standings` (GitHub Issue 21, implies
  `--history`) and `nuclearff.sleeper.standings`: `SleeperClient.get_winners_bracket`/
  `get_losers_bracket`, plus per-season standings (wins/losses/ties/points,
  owner display name) and playoff bracket data persisted to DuckDB
  (`sleeper_standings`, `sleeper_playoff_matches`). Final placement is
  computed **only** from winners-bracket matches carrying a `p` (placement)
  field — a match's winner gets rank `p`, its loser `p + 1`, confirmed
  against a real completed season. The losers bracket's own `p` field is
  captured raw but deliberately not used to compute `final_rank`: whether it
  continues the same overall numbering or restarts among just the
  losers-bracket teams couldn't be confirmed against real data, so those
  rosters get a `NULL` final rank rather than a guessed one. Verified live:
  the real league's 2025 season shows exactly this in practice — the
  regular-season wins leader (20-8) finished 4th place, while the eventual
  champion had fewer wins (16-12), and the 4 teams that only reached the
  losers bracket correctly show `NULL`.
- Sleeper API coverage (GitHub Issue 17): `SleeperClient.get_user`/`get_user_leagues`/
  `get_user_drafts` (the user-discovery entry point nuclearff previously
  lacked entirely — every prior command required an already-known
  `league_id`), exposed via `nuclearff sleeper user-leagues`/`user-drafts`,
  which resolve a Sleeper username to its user id before listing. Wired the
  previously dead-code `get_matchups`/`get_trending` client methods into
  `nuclearff sleeper fetch-league --matchups` (weekly matchups persisted to
  a new `sleeper_matchups` DuckDB table via `nuclearff.sleeper.matchups`,
  combined with the issue #16 history chain) and `nuclearff sleeper
  trending`. `LeagueSnapshot`/`fetch_league_snapshot` now also capture
  `draft_traded_picks`, previously fetched by the client but never stored.
  `get_players` accepts `position=`/`active=` query filters, per the
  documented endpoint — verified live to cut the payload from ~14.6 MB to
  ~435 KB for `position="QB", active=True`; filtered calls bypass the disk
  cache, which is specifically the full unfiltered map's contract. Sleeper
  transaction wiring (also flagged as dead code by this issue's original
  audit) is intentionally not repeated here — that is
  [issue #20](https://github.com/nolmacdonald/nuclearff/issues/20)'s scope.
- `nuclearff sleeper fetch-league --transactions` (GitHub Issue 20, implies
  `--history`) and `nuclearff.sleeper.transactions`: weekly transactions
  across a league's full history, persisted to DuckDB (`sleeper_transactions`,
  and a normalized `sleeper_transaction_players` unnesting `adds`/`drops`
  into one row per player/direction). Sleeper's own `type`/`status` fields
  are trusted and stored verbatim rather than derived — confirmed live
  against the real league's full history: `waiver`/`free_agent`/`trade`, and
  a 4th type not seen in the single-season sample originally checked,
  `commissioner`. `created`/`status_updated` (epoch milliseconds) are parsed
  to UTC timestamps. `creator`/`roster_ids`/`consenter_ids` are resolved to
  display names alongside the raw ids, reusing
  `nuclearff.sleeper.standings.roster_display_names` rather than
  re-implementing that join. Verified live against the real league: 1,759
  transactions across 6 seasons (965 waiver, 771 free agent, 22 trade, 1
  commissioner; 1,316 complete, 443 failed), 2,670 add/drop rows, and the
  real trade used as this module's test fixture round-trips exactly.
- `nuclearff sleeper fetch-league --roster-players` (GitHub Issue 23, implies
  `--history`) and `nuclearff.sleeper.roster_players`: categorizes every
  roster's players by slot (`starter`/`reserve`/`taxi`/`bench`) per season,
  persisted to a `sleeper_roster_players` DuckDB table joinable to
  `sleeper_players` for name/position without re-parsing JSON. Filters
  Sleeper's `"0"` empty-starting-slot filler (confirmed against a real
  pre-draft league) rather than inserting it as a player row. Verified live
  against the real league's full history: 791 roster-player rows across 5
  populated seasons (2026 pre-draft correctly excluded), and one real
  completed-season roster shows exactly 17 players (9 starters, 6 bench, 2
  reserve) — matching this issue's own acceptance benchmark.
- `nuclearff.sleeper.users.roster_owners` (GitHub Issue 24): resolves a
  league's roster ids to their owning user's display name *and* every
  co-owner's, given `roster.co_owners`. Complements (not a replacement for)
  `standings.roster_display_names`, which `nuclearff.sleeper.standings` and
  `nuclearff.sleeper.transactions` already use for the simpler single-owner
  join — this covers what that doesn't: multi-owner rosters. A `user_id`
  with no matching entry in `users` (a real data inconsistency) is logged
  and resolves to `None` rather than raising. No CLI wiring or DuckDB table
  of its own; pure mapping infrastructure. Verified live against the real
  league: all 10 rosters resolve correctly.
- `nuclearff report playoff-bracket` (GitHub Issue 22) and
  `nuclearff.report.bracket`: renders a season's winners and losers playoff
  brackets from `sleeper_playoff_matches`/`sleeper_standings` (issue #21) as
  separate horizontal-tree PNGs, labeled with real team display names. A
  Sleeper bracket is a fixed-shape single-elimination tree keyed by
  `t1_from`/`t2_from` match references, not a similarity-clustering
  dendrogram, so `compute_bracket_positions` hand-builds the layout with
  matplotlib line segments rather than coercing it into
  `scipy.cluster.hierarchy`'s linkage-matrix format — zero new dependencies,
  matplotlib is already core. `matplotlib` is imported lazily inside
  `render_bracket_tree`, the same posture `nuclearff.report.tables` uses.
  `_nudge_colliding_positions` separates matches that legitimately land on
  the same computed position (a championship and its 3rd-place game both
  split from the same pair of semifinal matches) by a margin proportional to
  their own box height, found and fixed by rendering the real bracket and
  visually inspecting the output — a fixed nudge smaller than the box height
  still left the boxes visually overlapping. Verified live against the real
  league's completed 2025 season: 7 winners-bracket and 4 losers-bracket
  matches (this issue's acceptance benchmark), later playoff rounds resolve
  correctly through `t1_from`/`t2_from`, and both figures render with no
  overlapping labels.
- Chopped-format league support in `nuclearff.sleeper.standings` (GitHub
  Issue 34): Sleeper's "Chopped" league type (16 teams, lowest scorer
  eliminated weekly, no playoff bracket) returns `null`, not `[]`, from both
  `winners_bracket`/`losers_bracket` — confirmed live against a real
  completed league (`1262207133378695168`). `is_chopped_league` detects the
  type from `settings.type == 3` and the presence of `settings.last_chopped_leg`
  together (not `type` alone, since Sleeper's numeric type values beyond
  0/1/2 aren't officially documented). `resolve_chopped_final_ranks` derives
  `final_rank` from `roster.settings.eliminated` (the leg a roster was
  chopped, absent for the winner) as a fallback in `standings_rows` — used
  only when the winners bracket produced no ranks and the league is
  confirmed Chopped-format, so a normal league's genuinely empty bracket
  (season in progress) still correctly gets `NULL`. `fetch_and_write_standings`
  also stops logging a misleading "could not fetch winners_bracket" warning
  for this expected-null case. `_LEAGUE_TYPE_NAMES` (`nuclearff.sleeper.leagues`)
  now maps `3` to `"chopped"` instead of `"unknown"`. Verified live against
  the real Chopped league: all 16 rosters get the correct `final_rank`,
  matching the real elimination order exactly (leg 1 chopped -> rank 16,
  never chopped -> rank 1, independently confirmed by
  `league.metadata.latest_league_winner_roster_id`). No bracket
  visualization for this league type — that's not this issue's scope; a
  Chopped league's elimination order isn't a pairwise match tree, so
  `nuclearff.report.bracket` (issue #22) doesn't apply.
- `nuclearff report draft-board` (GitHub Issue 36) and `nuclearff.sleeper.draft`/
  `nuclearff.report.draft_board`: renders a draft as Sleeper's own snake-order
  grid of position-colored pick cards (one column per draft slot, one row per
  round). `SleeperClient.get_draft_picks` was already fetched by
  `fetch_league_snapshot` but never made queryable — `draft_pick_rows`/
  `fetch_and_write_draft_picks` close that gap with a new `sleeper_draft_picks`
  table. The grid's column for a pick is that pick's own real `draft_slot`,
  not a computed alternation — a league's `settings.reversal_round` can make
  a later round continue the same direction as the round before it instead of
  reversing, and Sleeper already resolves that into each pick's `draft_slot`,
  confirmed live against this project's real, currently in-progress 2026
  draft (`reversal_round: 3`; round 3 continues round 2's direction rather
  than reversing back to round 1's). Cell color is confirmed for RB/WR/QB/TE
  against a real screenshot of Sleeper's own draft-room UI; any other
  position falls back to a neutral, explicitly-unconfirmed color rather than
  guessing. Found and fixed a real overflow bug by rendering the real draft
  and looking at the PNG: a long combined "First Last" name (e.g. "Rhamondre
  Stevenson") ran past its cell into the next column — fixed by splitting
  first/last name onto two lines (matching the reference screenshot's own
  layout), which also fits each name part comfortably on its own. Verified
  live against the real draft: 100+ real picks render correctly across 10
  columns and 11+ rounds, all four confirmed position colors present, no
  cell text overlapping a neighbor.
- `nuclearff.sleeper.trades` (GitHub Issue 41, split from Issue 40): turns
  `sleeper_transactions` rows where `type == "trade"` into a manager-pair
  edge list (`load_trades`) and per-manager aggregates
  (`manager_trade_counts`, `pairwise_trade_matrix`, `trades_by_season`,
  `cumulative_trade_counts`), the shared data prep for every trade-network
  visualization issue (#42-#51). No new Sleeper fetching — reads what
  `nuclearff sleeper fetch-league --transactions` (issue #20) already wrote.
  Only `status == "complete"` trades count. Sleeper's schema allows more
  than two rosters per trade, so an N-team trade explodes into one edge per
  unordered manager pair (`C(n, 2)`) rather than assuming exactly two
  parties — every aggregate that counts *trades* rather than *edges*
  de-duplicates back down to distinct `transaction_id` values per manager
  first, so a manager in one 3-way trade is counted once, not twice.
  Verified against this league's real 2-team trade fixture (see
  `tests/test_sleeper_transactions.py`) plus a synthetic 3-team trade, since
  this league's real history has none yet. Density over the full manager
  roster (a manager who never traded) is deliberately out of scope — that
  needs `sleeper_standings`, a different table; callers needing a
  zero-trade manager to still appear join against that roster themselves.
- `nuclearff report trades <league_id>` (GitHub Issue 42, split from Issue
  40) and `nuclearff.report.trades.render_trades_by_manager`: a horizontal
  bar chart of total trades per manager, the first of the trade-network
  visualizations built on issue #41's data prep. The CLI command joins
  `manager_trade_counts` against `sleeper_standings`' full manager roster
  so a manager with zero trades still appears at `0` rather than being
  silently absent (falls back to trade participants only, with a warning,
  if `sleeper_standings` hasn't been populated yet). Managers are sorted
  ascending into the chart so the highest trade count renders at the top,
  ties broken alphabetically for a deterministic image.
- `nuclearff.report.trades.render_trades_heatmap` (GitHub Issue 43, split
  from Issue 40), wired into the same `report trades` command: a symmetric
  manager x manager heatmap of `pairwise_trade_matrix`'s output, densified
  by the CLI against the full `sleeper_standings` roster the same way as
  issue #42's bar chart, so a manager with zero trades still renders as an
  all-zero row and column. Uses matplotlib's `layout="constrained"`, not
  `tight_layout()`: `imshow`'s equal-aspect box combined with a colorbar
  made `tight_layout()` center the grid in its allotted space, leaving a
  large dead gap between the title and the grid that persisted even after
  explicitly re-anchoring the axes (`colorbar()`/`tight_layout()` both
  reposition the axes and silently reset that anchor). A figure-size floor
  fixes a second real bug found the same way: below ~6 inches, constrained
  layout has too little room for the title, tick labels, and colorbar
  together and silently falls back to an overlapping, unreadable layout —
  hit by the 1-2 manager case, a small but real league size. Verified
  against this league's real 22-trade, 15-manager history, including the
  real three-team trade contributing one edge to each of its three pairs.
- `nuclearff.report.trades.render_trade_network` (GitHub Issue 44, split
  from Issue 40), wired into the same `report trades` command: a
  `networkx` node-link graph, one node per manager (sized by total trades)
  and one edge per trading pair (widened by trade count), from the same
  densified `counts`/`matrix` inputs as the bar chart and heatmap so a
  zero-trade manager still appears as an isolated node. `networkx` is a
  new `dev`-extra dependency (visualization-only, matching how `plottable`
  itself was added). Two real layout bugs found rendering against this
  league's real 15-manager, 22-trade history: the italic sparse-data
  caveat overlapped the title outright (fixed with the same `pad=30`
  title/`y=1.006` caveat pattern already used in `report/tables.py`, which
  this function had omitted), and `spring_layout`'s default node spacing
  (`1/sqrt(n)`) is tuned for single-character labels, not real manager
  names — every label in the connected cluster overlapped its neighbors
  until `k` was widened to `3/sqrt(n)`.
- `nuclearff.report.trades.render_trade_leaderboard` (GitHub Issue 46,
  split from Issue 40), wired into the same `report trades` command: a
  `plottable` PNG table, one row per manager (Trades, Unique Partners,
  Most Frequent Partner, Trades With Partner), sorted by trade count. No
  circle-cropped headshots like `report/tables.py`'s player tables — a
  Sleeper manager has no headshot URL anywhere in this project's data
  model, only an unwired avatar id, out of scope here rather than an
  oversight. The CLI's manager-roster densification (previously narrowed
  to just `manager`/`trades` for the bar chart) now keeps all 5
  `manager_trade_counts` columns, so a zero-trade manager still gets a
  full row with `"—"` for a partner that doesn't exist, not a crash or a
  missing row. A real column-width bug surfaced rendering against this
  league's real 15-manager roster: `plottable` doesn't wrap or shrink a
  header wider than its column, so "UNIQUE PARTNERS"/"MOST FREQUENT
  PARTNER"/"TRADES WITH PARTNER" (far wider than `report/tables.py`'s
  short abbreviations like "PTS") overflowed into neighboring columns
  until every column was widened to fit its own header text.
- `nuclearff.sleeper.trades.top_manager_pairs` and
  `nuclearff.report.trades.render_manager_pair_leaderboard` (GitHub Issue
  47, split from Issue 40), wired into the same `report trades` command: a
  horizontal bar chart of the top 10 manager pairs by trade count, labeled
  `Manager A ↔ Manager B`. Each pair appears once by construction, not by
  post-hoc deduplication — `load_trades` already sorts `manager_a`/
  `manager_b` alphabetically per trade, so grouping directly on those two
  columns can never produce both an A-B and a B-A row for the same pair.
  Unlike `pairwise_trade_matrix`'s dense grid, a pair that never traded is
  simply absent rather than padded with a `0` bar. Verified against this
  league's real 22-trade history: 10 real pairs, `casitzmann`/
  `nolmacdonald` and `BigCookie96`/`nolmacdonald` tied at the top with 4
  trades each.
- `nuclearff.sleeper.trades.total_trades_by_season` and
  `nuclearff.report.trades.render_trades_over_time` (GitHub Issue 48,
  split from Issue 40), wired into the same `report trades` command: one
  thin line per manager plus a bold league-wide total, by season. The
  total counts each trade once regardless of participant count, the same
  de-duplication every other trade aggregate in this module already uses
  — grouping distinct `transaction_id` per season directly, not summing
  `trades_by_season` across managers (which would roughly double-count a
  season's real volume, since most trades involve two managers). A new
  CLI helper, `_densify_trades_by_season`, joins against
  `sleeper_standings` so a manager's line covers only the seasons they
  actually rostered — a season they rostered but didn't trade in is a
  real `0` (not absent), while a season before/after they were in the
  league is genuinely absent (a real gap, not a misleading straight line
  connecting seasons that never happened for them) — the distinction
  issue #48's acceptance criteria specifically called out. Verified
  against this league's real 6-season history: `total_trades_by_season`'s
  per-season sum matches `edges["transaction_id"].n_unique()` exactly (no
  double-counting), and one manager (`nolmacdonald`) alone accounted for
  6 of 2022's 8 league-wide trades.
- `nuclearff.report.trades.render_manager_season_heatmap` (GitHub Issue 49,
  split from Issue 40), wired into the same `report trades` command: a
  manager x season grid, one cell per combination, annotated with the raw
  trade count. Deliberately denser than Issue 48's line chart — a new CLI
  helper, `_densify_manager_season_matrix`, fills every manager x season
  cell with an explicit `0` (including seasons before/after a manager was
  in the league), since a heatmap has no "connect the dots" failure mode
  to avoid the way a line chart does. Verified against this league's real
  15-manager, 6-season history: `nolmacdonald`'s real 2022 peak (6 trades)
  renders as the single darkest cell on the grid, and every non-participating
  manager (e.g. `bigTETONclimber`, `jwhitney0220`) renders as an all-zero row
  rather than a gap.
- `nuclearff.report.trades.render_cumulative_trades` (GitHub Issue 50, split
  from Issue 40), wired into the same `report trades` command: a step chart
  (not a straight-line one, so a manager's count doesn't appear to accrue
  gradually between real trade events) of each manager's running trade
  total over time, from `cumulative_trade_counts` (Issue 41). Lines are
  labeled directly at their end rather than in a legend, since a legend for
  15 real managers would either overflow the figure or need its own overlap
  fix. Rendering against this league's real 22-trade history surfaced
  exactly that overlap problem anyway: several managers plateau at the same
  low count (1-2 trades) and their default end-of-line label positions
  collided into unreadable merged text (e.g. `aperry151` and
  `nolanmacdonald` overlapping into "aperry151donald"). Fixed with a label
  declutter pass in axes-fraction y-space (so the minimum gap holds
  regardless of the data's actual range) plus a thin leader line — drawn by
  `annotate`'s own `arrowprops`, since a label's real data point and its
  decluttered text position live in different coordinate systems — back to
  each real endpoint. Also switched label text to a fixed dark color rather
  than the line's own color, since `tab20` (the color cycle used for 15
  distinguishable lines) includes pale entries illegible on white; the
  leader line still carries the series' real color. Verified against the
  real history: `nolmacdonald` is confirmed the league's all-time most
  prolific trader (14 of 22 trades), visibly taking the lead in late 2023.
- `nuclearff.report.trades.render_trade_partner_diversity` (GitHub Issue 51,
  split from Issue 40), wired into the same `report trades` command: a
  scatter of total trades vs. unique trade
  partners per manager, separating a manager who trades widely from one who
  repeatedly trades with the same 1-2 people — a distinction the raw trade
  count alone can't make. A real coordinate collision, not just a
  synthetic edge case: 5 of this league's 15 real managers never traded,
  so they'd all land on the exact same `(0, 0)` point. Grouped by the
  `(trades, unique_partners)` coordinate before plotting, drawing one
  marker with a comma-joined label per group rather than stacking
  fully-overlapping duplicate points and labels, then reused Issue 50's
  axes-fraction y-space label-declutter pass for groups that are close but
  not identical. Verified against the real history: the zero-trade cluster
  (`bigTETONclimber`, `bigshett`, `jwhitney0220`, `ruhbberduhcky`,
  `thatbolb`) renders as one clean label at the origin, `nolmacdonald`
  (14 trades, 7 partners) and `casitzmann` (9 trades, 6 partners) stand out
  as the widest traders, and `ksavabi`/`macbuffet66`/`nawfeastdallas` (each
  1 trade, 1 partner) visibly contrast with `hyoga10` (3 trades, 3
  partners) — same raw trade count band, opposite diversity.
- `nuclearff.report.trades.render_chord_diagram` (GitHub Issue 45, split
  from Issue 40, completing the trade-network epic), wired into the same
  `report trades` command as a tenth PNG: a circular chord diagram, one
  point per manager evenly spaced on a circle, joined by a curved arc
  (quadratic Bezier, pulled toward the circle's center) per manager pair
  with at least one trade, widened by that pair's trade count. Issue 45
  originally decided on `plotly` + `kaleido` for this (a static PNG via
  `fig.write_image()`, not an interactive HTML file, to match every other
  artifact in this epic) — reversed during implementation: current
  `kaleido` (1.x, required by current `plotly`) needs a separately
  installed headless Chrome to export anything at all, and failed outright
  with no browser present; pinning back to the old self-contained
  `kaleido==0.2.1` isn't an option either, since current `plotly` has
  dropped support for that legacy API. Rather than adding a real
  headless-browser dependency (exactly what `report/tables.py`'s own "no
  headless browser anywhere in the path" module docstring rules out) or
  pinning to two now-unmaintained packages, hand-drew the diagram in
  matplotlib instead — the fallback #40's own original design notes had
  already recommended before the plotly decision, needing no `dev` extra
  at all. See `decisions.md` in the project brain for the full reasoning.
  Verified against the real 15-manager, 22-trade history: node size and
  edge width both track the already-confirmed real ranking (`nolmacdonald`
  the largest node with the thickest edges), and node positions measured
  from the rendered PNG sit at a consistent radius from center (a real
  circle, not skewed by the title's layout margin).
- `docs/source/league_trade_history.rst`, a dedicated page for all ten
  `report trades` visualizations (issues #42-#51, #45 — the completed
  trade-network epic), added to `index.rst`'s toctree and landing card.
  Split out of `user_guide.rst`'s own "Trade network analysis" section
  (which shrinks to a one-line pointer at this page) once that section
  grew to cover all ten charts — the same "grows past a single section,
  gets its own page" pattern `sleeper_api_tutorial.rst` already set in
  2026-09-03 (see `decisions.md`). Every real-data detail already verified
  while building each visualization (`nolmacdonald`'s real 14-trade,
  7-partner lead; the real 3-team trade among `casitzmann`/`nolmacdonald`/
  `nolanmacdonald`; the 5 real zero-trade managers; the real 13-edge,
  15-node sparse network) is carried into this page rather than
  re-derived, and a real `uv run sphinx-build -b html docs/source
  docs/_build/html -W` build is clean (0 warnings).
- `docs/source/user_guide.rst`, a complete walkthrough of every `nuclearff`
  CLI command and flag — league capture, multi-season history, standings and
  playoff results, weekly matchups, transaction history, roster composition,
  user/draft discovery, player filtering and trending, `gsis_id` resolution,
  and auction/keeper draft valuation — with real output captured against the
  project's real league while writing it. `docs/source/sleeper_api_tutorial.rst`
  updated to match: its player-filtering note was stale (issue #17 landed
  `position=`/`active=` support after this page was first drafted), and its
  endpoint table now covers user discovery and the winners/losers brackets.
- `nuclearff ids resolve-gsis` (`ff_revised.md` Issue 7), which fills Sleeper
  players missing a `gsis_id` from nflverse's `ff_playerids` crosswalk,
  skips crosswalk rows whose `sleeper_id` is ambiguous rather than guessing,
  and writes a `player_id_map` table recording each `gsis_id`'s source. Real
  coverage against this project's real league: 88-95% of currently-rostered
  QB/RB/WR/TE, with the gap almost entirely players missing from this year's
  crosswalk (typically rookies).
- `LeagueConfig`/`ScoringSettings`/`RosterSlots` (`nuclearff.config.league`,
  `ff_revised.md` Issue 5), typed league scoring and roster settings derived
  from a raw Sleeper league object. `starter_demand`/`replacement_rank`
  compute the VOLS/VORP value-based-drafting baseline for any position
  (QB/RB/WR/TE) from the league's actual roster slots and a configurable
  FLEX/superflex allocation rate, rather than being hardcoded to one
  position. `nuclearff sleeper fetch-league` now also writes the derived
  league config to `configs/leagues/<league_id>.yaml`. Verified against this
  project's real league: full PPR, no TE premium,
  `replacement_rank("WR", "vols") == 35` (the plan's expected WR33-36 band).
  The per-position FLEX-share and bench-fraction default rates beyond WR are
  explicitly flagged in the code as uncalibrated heuristics, to be revisited
  once real draft/roster data exists.
- `nuclearff.duckdb_io`, a shared helper for reading/writing DuckDB tables via
  plain parameterized SQL rather than a Polars/pandas DataFrame handoff
  (avoids pulling in `pyarrow` as a transitive dependency for a handful of
  typed columns). Used by both `sleeper fetch-players` and `ids resolve-gsis`.
- `nuclearff.nflverse`: thin, cache-configured wrappers around `nflreadpy`
  (`ff_revised.md` Issue 6). `configure_cache`/`load_ff_playerids`
  (`nflverse.loader`) set up nflreadpy's filesystem cache and load the
  `ff_playerids` ID crosswalk; `load_weekly_receiving`,
  `load_seasonal_receiving`, `load_ngs_receiving`, `load_routes`, and
  `load_players` (`nflverse.stats`) are thin, schema-validated wrappers
  around nflreadpy's receiving-stats loaders, plus
  `load_weekly_skill_stats`/`load_seasonal_skill_stats` (QB/RB/WR/TE, via
  `SKILL_POSITIONS`) alongside the WR/RB/TE-only receiving loaders, and
  `load_ff_opportunity` (ffverse's expected-points model). `load_routes`
  labels every row with its real `source` (`snap-count proxy` vs.
  `unavailable`) rather than presenting an approximation as charted data;
  live investigation found `nflreadpy.load_ftn_charting` is not actually a
  per-player routes-run source despite the original technical plan's
  assumption, so there is currently no real routes-run data available via
  nflreadpy for any season.
- `nuclearff.scoring.ScoringEngine` (`ff_revised.md` Issue 9), the
  league-agnostic scorer: bridges Sleeper's scoring-key vocabulary to
  nflverse's stat-column vocabulary via declarative linear/composite/
  threshold/position-bonus mapping tables, so the same engine scores any
  league's rules. `unscored_keys()` surfaces any nonzero scoring key it has
  no data source for (kicker/defense/IDP, or per-catch yardage-bucket keys)
  rather than silently under-counting. Verified against this project's real
  league and real 2024 data: reproduces `nflreadpy`'s own
  `fantasy_points_ppr` column exactly for every top player except one whose
  value correctly differs because this league's real 6-point passing-TD
  rule (from a rare WR trick-play TD) diverges from nflreadpy's generic
  4-point default — confirming the engine applies the league's actual
  rules rather than a generic assumption.
- `nuclearff.metrics.volume` (`ff_revised.md` Issue 10): `target_share`,
  `air_yards_share`, `wopr`, `racr`, `adot` — recomputed transparently from
  raw weekly data rather than trusted blindly from `nflreadpy`'s own
  pre-computed columns, with a cross-check against them. Live 2024
  validation found `air_yards_share` disagrees with nflreadpy's own column
  on ~7% of rows (an unresolved methodology difference, documented rather
  than silently accepted).
- `nuclearff.metrics.efficiency` (Issue 10): `join_routes` (bridges
  nflverse's `pfr_player_id`-keyed routes data onto `gsis_id`-keyed
  receiving data via `load_players`, rejecting ambiguous ID pairs rather
  than guessing), `yprr`, `tprr` — both sample-gated and explicitly labeled
  as running on the snap-count routes proxy, not real charted routes-run
  data.
- `nuclearff.metrics.touchdowns.expected_tds` (`ff_revised.md` Issue 11),
  regressing raw receiving TDs toward an opportunity-driven expectation via
  ffverse's own maintained `load_ff_opportunity` model, rather than
  reimplementing red-zone/air-yards TD modeling from scratch.
- `nuclearff.projection.blend` (`ff_revised.md` Issue 13):
  `recency_weighted_rate`, `apply_age_curve`, `apply_context_deltas`,
  `project_games_played`, and `blend_projection`, composing a multi-season,
  age-adjusted, sample-gated forward projection from `ModelConfig`'s
  `RecencyWeights`/`AgeCurveConfig`/`SampleThresholds`. A player with fewer
  seasons than the recency window has their weights renormalized rather
  than diluted by assumed-zero seasons.
- `nuclearff.valuation.vorp` (`ff_revised.md` Issue 14): `replacement_points`,
  `vorp`, `vona` — turns a projected-points table into draft value using
  `LeagueConfig`'s replacement-rank math. `nuclearff.valuation.tiers`:
  `assign_tiers`, k-means tiering (auto-`k` via silhouette score) with
  cluster labels remapped so tier 1 is always the highest-value group, not
  sklearn's arbitrary cluster order.
- `nuclearff.simulation.montecarlo` (`ff_revised.md` Issue 15):
  `simulate_player_season`, `summarize_distribution`, `simulate_from_config`
  — seeded, reproducible Monte-Carlo season simulation via
  `scipy.stats.skewnorm`, correctly solving for the loc/scale that
  reproduce a target mean/sd even with nonzero skew. Uses
  `numpy.random.default_rng`, never legacy global random state, so a
  recorded seed can actually reproduce a published ranking.
- `configs/models/wr_default.yaml`, the default WR projection model
  configuration (recency weights, age curve, sample thresholds, simulation
  parameters) referenced by the modules above.
- **Milestone: the full projection → valuation → simulation pipeline runs
  end-to-end**, validated against real 2023-2025 data for this project's
  real league: `blend_projection` → `vorp` → `assign_tiers` →
  `simulate_from_config` produces a top-12 WR list (Ja'Marr Chase, Amon-Ra
  St. Brown, Puka Nacua, Jaxon Smith-Njigba, Justin Jefferson, Nico
  Collins, ...) that closely matches real-world 2026 consensus rankings,
  with sensible Monte-Carlo floor/median/ceiling outputs. Exercised via a
  direct Python script rather than a `nuclearff` command — no CLI/report
  layer wraps this pipeline yet.
- `nuclearff.backtest.metrics` (`ff_revised.md` Issue 12):
  `spearman_correlation`, `mae`, `rmse`, `top_k_precision_recall`,
  `tier_accuracy`, `brier_score` — pure evaluation functions,
  degenerate-input-safe (constant/short input returns `nan` without scipy
  warnings; out-of-range probabilities raise). `nuclearff.backtest.walkforward`:
  `season_folds` (expanding-window, leakage-safe by construction),
  `baseline_prior_year`, `baseline_recency_weighted` (delegates to
  `projection.blend`'s own recency weighting), and
  `run_walk_forward_backtest`, the harness the plan's recommendation #7
  calls for before locking `ModelConfig`'s projection weights. Run against
  real 2021-2025 WR data: the recency-weighted baseline beats prior-year
  PPG on Spearman/MAE/RMSE across every fold — a real, sensible signal
  confirming the harness itself works, not yet a claim about the full
  projection model's weights (that comparison is separate, later work).
- `nuclearff.provenance` (`ff_revised.md` Issue 8): `RunManifest` plus
  `build_run_manifest`/`write_run_manifest`, capturing git commit/dirty
  state, package/Python versions, a deterministic config hash, and source
  hashes for reproducing a future pipeline run. Verified end-to-end: real
  git SHA/dirty-state capture, deterministic config hashing, JSON
  round-trip. Not yet called by anything — infrastructure for the ranking
  pipeline (`ff_revised.md` Issue 20) once it exists.
- `nuclearff.nflverse.rankings`: FantasyPros expert-consensus rankings via
  `nflreadpy.load_ff_rankings` (no scraping) — `load_fantasypros_ecr`,
  `attach_player_ids` (exact `fantasypros_id` -> `gsis_id` join, ambiguous IDs
  skipped rather than guessed), and `consensus_adp` normalizing to the
  pipeline's market-value schema. Labeled `source="fantasypros_ecr"`
  throughout: this is expert *consensus ranking*, not observed ADP, and the
  module says so rather than implying draft-behavior data it does not have.
  Verified live: 446/450 (99.1%) of QB/RB/WR/TE rows resolve to a `gsis_id`.
- `nuclearff.pipeline.auction_board`: the wiring layer taking a Sleeper league
  id to a priced draft board — league scoring -> realized points -> recency-
  weighted value estimate -> per-position replacement level and VORP ->
  auction dollars -> FantasyPros consensus join. Returns the board plus a
  context dict of league facts so reports never hardcode a league's settings.
- `nuclearff.report`: `write_board_csv`, `render_position_table` (per-position
  top-N PNG via `plottable`, matching `dev/tables/ex_table.py`'s conventions),
  and `write_report` (CSV + tables + `report.md` with methodology and
  caveats). Rendering dependencies are imported lazily so the package still
  imports without the `dev` extra.
- `nuclearff report auction-board <league_id> --seasons ... --as-of-season ...`,
  wiring the above into the CLI.
- `nuclearff.valuation.auction`: `budget_from_draft` (reads the confirmed
  `draft.settings.budget`, and refuses to assume a conventional $200 when
  absent), `auction_values` (VBD -> dollars; dollars sum to exactly the league
  budget across the draft pool), `keeper_inflation_multiplier`, and
  `keeper_adjusted_values`. Keeper *costs* must be supplied by the caller —
  Sleeper exposes no keeper price, and this module never invents one.
- Typed exception hierarchy (`NuclearffError`, `ConfigError`, `SleeperAPIError`,
  `StorageError`, and friends).
- Logging configuration utilities.
- Sphinx documentation with PyData theme, plus decision notes under
  `docs/source/decisions/`.
- GitHub Actions workflows for CI (lint, type check, test matrix, build), docs
  deployment, and tagged releases.
- Dependabot configuration for GitHub Actions and Python dependencies.
- Issue templates for bugs, features, documentation, research tasks, and data
  sources, plus a pull request template.
- Ruff formatting and linting configuration.
- `ty` type checking configuration.
- `nuclearff report user-leagues <username_or_user_id> --season <year>`
  (GitHub Issue 77): renders a PNG table of every league a Sleeper user is
  in for a season — avatar, name, league id, type, team count, status — plus
  a per-type count summary (however many types are actually present, not a
  fixed template) with a Total row. Wraps two existing-but-never-rendered
  pieces: `SleeperClient.get_user_leagues` and `SleeperClient.avatar_url`.
  Also adds `nuclearff.sleeper.leagues.league_type_name`, extracted from the
  `_LEAGUE_TYPE_NAMES` lookup `league_rows` already had, for reuse here —
  deliberately trusts `settings.type` alone rather than the stricter
  `is_chopped_league` (which also requires `last_chopped_leg` in settings,
  a signal that a real account's real Chopped league can lack before it has
  actually chopped anyone). Two real rendering bugs surfaced and fixed while
  verifying against a real account's real 18 leagues: avatars overlapping
  adjacent rows' text (root cause: two `plottable.Table`-bearing Axes
  sharing one figure under `constrained_layout`, which doesn't guarantee
  the row height `circled_image` sizes against — fixed by rendering the
  league grid and the summary as two independent, correctly-sized figures
  and compositing them with PIL) and long league names overflowing into
  the League ID column (fixed by sizing that column to the longest real
  name present, plus adding a real border divider between every remaining
  column — none had one before, which made even correctly-sized adjacent
  columns look like they were touching). A league name with a character
  outside the Basic Multilingual Plane (a real emoji in a real league name,
  `"TEXAS BOYS \U0001f920"`) rendered as a missing-glyph box under
  matplotlib's bundled font; stripped for the rendered label rather than
  adding a new emoji-font dependency for one cosmetic case.

### Changed

- Docstrings are Google style, matching the project convention; the template
  shipped Sphinx configured for NumPy style.
- `requests` replaces `httpx` as the HTTP client, matching the plan and the
  `responses` test-mocking strategy.

### Removed

- The "Decision Notes" Sphinx section (`docs/source/decisions.rst`,
  `docs/source/decisions/0001-repository-conventions.md`) and its toctree
  entry, and the matching guidance in `CONTRIBUTING.md` pointing contributors
  at it (GitHub Issue 66). Deleted outright, not migrated — the four
  decisions it recorded (build backend, docstring style, `requests`/
  `argparse`, draft-rounds resolution) are already reflected in the code and
  this file's own `### Changed` entries above.

### Fixed

- `[tool.ty]` `python-version` moved to `[tool.ty.environment]`, where current
  `ty` versions expect it. It was silently failing to parse.
- `nuclearff.report.tables.render_position_table` raised `FileNotFoundError`
  for any player missing a `headshot_url` (rookies and inactive players
  routinely have none) — `circled_image` opens the `headshot_path` cell
  unconditionally, and an empty path from a missing/failed fetch crashed the
  whole table. A cached neutral placeholder image now fills that cell
  instead (GitHub Issue 38). Caught by a new `tests/test_report_tables.py`,
  added along with `tests/test_report_build.py`'s first `render_tables=True`
  test to close the coverage gap on this path that let it through.
- Removed a no-op `.rename({"player_id": "player_id"})` left over in
  `nuclearff.pipeline.auction_board.build_auction_board` (GitHub Issue 38).
- Fixed the Sphinx docs build, broken since the project's first commit but
  never caught because `docs.yml` only triggers on `main`/PRs into `main`,
  and nothing had ever been merged to `main` until now. Two pre-existing
  issues surfaced once `-W` (warnings-as-errors) actually ran end to end:
  `nuclearff.backtest.metrics.tier_accuracy`'s docstring had a `Returns`
  block wrapping a multi-line double-backtick literal, which docutils
  parses as "Inline literal start-string without end-string" — reworded to
  plain prose. Separately, `nuclearff.valuation`'s `from .vorp import vorp`
  re-export gives the `vorp()` function the exact same qualified name as its
  own home module (`nuclearff.valuation.vorp`), so the package overview page
  and the submodule's own dedicated page both registered an object under
  that identical name — a real Sphinx footgun for any package that re-
  exports a same-named callable from a same-named submodule. Fixed with an
  `autodoc-skip-member` hook in `conf.py` that skips a member from any page
  but its own home module's, rather than renaming the live module or
  function (both used elsewhere, including a logger name in
  `tests/test_valuation_vorp.py`).

[Unreleased]: https://github.com/nolmacdonald/nuclearff/commits/main
