"""Chopped league analytics: every analysis and plot, top to bottom.

In a Chopped league the lowest scorer still alive is eliminated each week and
their players go to waivers, so there is no head-to-head record to analyze.
This script fetches one Chopped league's history from Sleeper, then walks
through each analysis in ``nuclearff.chopped`` and renders it as a PNG.

Run it from anywhere; every path is relative to this file::

    python examples/scripts/chopped_leagues.py

Needs the ``dev`` extra for the table plots (``uv sync --extra dev``) and
network access to Sleeper on the first run. Figures are written at 300 dpi to
``figures/chopped/`` next to this script; the fetched data goes to ``data/``.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from nuclearff.chopped.claims import bid_outcomes, claim_activity, waiver_claims
from nuclearff.chopped.faab import (
    faab_by_week,
    faab_check,
    league_burndown,
    spend_checkpoints,
)
from nuclearff.chopped.finishes import weekly_finishes
from nuclearff.chopped.luck import guard_excluded_weeks, survival_luck
from nuclearff.chopped.survival import chop_line_problems, weekly_survival
from nuclearff.duckdb_io import read_table
from nuclearff.report import (
    render_bid_outcomes,
    render_claim_activity,
    render_faab_remaining,
    render_league_burndown,
    render_luck_scatter,
    render_luck_table,
    render_spend_leaderboard,
    render_user_leagues_table,
    render_weekly_finishes,
)
from nuclearff.sleeper import (
    SleeperClient,
    fetch_and_write_matchups,
    fetch_and_write_standings,
    fetch_and_write_transactions,
    walk_league_chain,
    write_league_tables,
)
from nuclearff.sleeper.leagues import league_type_name

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
FIGURES_DIR = HERE / "figures" / "chopped"
DATA_DIR = HERE / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = CACHE_DIR / "nuclearff.duckdb"

DPI = 300

USERNAME = "nolmacdonald"
LEAGUE_ID = "1367168140898766848"  # NUCLEARFF CHOPPED OG $10, the 2026 league
MAX_SEASONS = 2  # walk previous_league_id back this many seasons (2026, 2025)
SEASON = 2025  # season for the single-season plots; 2025 is complete, 2026 in progress
FETCH = True  # set False to reuse the DuckDB already in data/cache/

FIGURES_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def season_only(frame: pl.DataFrame) -> pl.DataFrame:
    """Keep one season's rows of a multi-season analysis."""
    return frame.filter(pl.col("season") == SEASON)


# -----------------------------------------------------------------------------
# FETCH THE LEAGUE
# -----------------------------------------------------------------------------
# Walk the league chain back through prior seasons, then write standings,
# weekly matchups and waiver transactions to DuckDB. Standings also persist
# each roster's elimination week (`sleeper_chopped_rosters`), which every
# analysis below relies on. Re-running only fetches what is missing.

with SleeperClient(cache_dir=CACHE_DIR) as client:
    if FETCH:
        chain = walk_league_chain(
            client, LEAGUE_ID, max_seasons=MAX_SEASONS, db_path=DB_PATH
        )
        write_league_tables(chain, DB_PATH)
        fetch_and_write_standings(client, chain, DB_PATH)
        fetch_and_write_matchups(client, chain, DB_PATH)
        fetch_and_write_transactions(client, chain, DB_PATH)

    # -------------------------------------------------------------------------
    # PLOT 1: CHOPPED LEAGUES TABLE
    # -------------------------------------------------------------------------
    # Every Chopped league the user belongs to that season. The league type
    # comes from Sleeper's own `settings.type`, not from the league's name.

    user = client.get_user(USERNAME)
    user_leagues = client.get_user_leagues(user["user_id"], SEASON, sport="nfl")
    chopped = [lg for lg in user_leagues if league_type_name(lg) == "chopped"]

    print(
        render_user_leagues_table(
            chopped,
            FIGURES_DIR / "01_chopped_leagues_table.png",
            title=f"{user['display_name']}'s Chopped Leagues",
            subtitle=f"{len(chopped)} leagues  |  nfl  |  {SEASON}",
            cache_dir=CACHE_DIR / "avatars",
            dpi=DPI,
        )
    )

# -----------------------------------------------------------------------------
# LOAD THE TABLES
# -----------------------------------------------------------------------------

transactions = read_table(DB_PATH, "sleeper_transactions")
leagues = read_table(DB_PATH, "sleeper_leagues")
standings = read_table(DB_PATH, "sleeper_standings")
matchups = read_table(DB_PATH, "sleeper_matchups")
chopped_rosters = read_table(DB_PATH, "sleeper_chopped_rosters")

league_name = (
    leagues.filter(pl.col("league_id") == LEAGUE_ID)["name"].first() or "Chopped League"
)

# -----------------------------------------------------------------------------
# PLOT 2: FAAB REMAINING AFTER EACH WEEK
# -----------------------------------------------------------------------------
# `faab_by_week` rebuilds each roster's FAAB week by week from winning bids
# and FAAB traded between teams. `faab_check` compares the rebuilt total with
# Sleeper's own `waiver_budget_used`; an empty frame means every roster agrees.

faab = faab_by_week(transactions, leagues, chopped_rosters, standings)
mismatches = faab_check(faab, leagues, chopped_rosters)
print(f"FAAB rebuilt for {faab['owner_id'].n_unique()} managers")
print(f"Rosters where the rebuild disagrees with Sleeper: {mismatches.height}")

print(
    render_faab_remaining(
        season_only(faab),
        FIGURES_DIR / "02_faab_remaining.png",
        title=f"{league_name}: FAAB Remaining After Each Week, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 3: FAAB SPENDING LEADERBOARD
# -----------------------------------------------------------------------------
# FAAB spent on winning bids through weeks 4, 8, 12 and 16, and the season
# total. A star marks the top spender in each column.

spend = spend_checkpoints(faab, leagues)

print(
    render_spend_leaderboard(
        season_only(spend),
        FIGURES_DIR / "03_faab_spending_leaderboard.png",
        title=f"{league_name}: FAAB Spending Leaderboard, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 4: LEAGUE FAAB BURNDOWN
# -----------------------------------------------------------------------------
# The league's starting FAAB split each week into spent on winning bids, held
# by teams still alive, and lost to the chop with an eliminated team.

burndown = league_burndown(faab, leagues)

print(
    render_league_burndown(
        season_only(burndown),
        FIGURES_DIR / "04_league_faab_burndown.png",
        title=f"{league_name}: League FAAB Burndown, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 5: WAIVER CLAIM ACTIVITY (SEASON)
# -----------------------------------------------------------------------------
# `waiver_claims` gives one row per claimed player with its bid and outcome:
# won, outbid, roster_full, over_budget or other. Losing claims keep the real
# amount bid. `claim_activity` rolls that up per manager.

claims = waiver_claims(transactions, leagues, standings)
print(claims.group_by("season", "outcome").len().sort("season", "outcome"))

activity = claim_activity(claims, transactions, leagues, chopped_rosters, standings)

print(
    render_claim_activity(
        season_only(activity),
        FIGURES_DIR / "05_waiver_claim_activity.png",
        title=f"{league_name}: Waiver Claim Activity, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 6: WAIVER CLAIM ACTIVITY (CAREER)
# -----------------------------------------------------------------------------
# The same table with every season combined per manager.

career_activity = claim_activity(
    claims, transactions, leagues, chopped_rosters, standings, career=True
)

print(
    render_claim_activity(
        career_activity,
        FIGURES_DIR / "06_waiver_claim_activity_career.png",
        title=f"{league_name}: Waiver Claim Activity, Career",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 7: WAIVER BID OUTCOMES (SEASON)
# -----------------------------------------------------------------------------
# How often each manager wins the players they bid on, and how narrowly they
# lose. A contest is one player in one waiver run. The runner-up is the
# highest losing bid; a tied loss matched the winning bid and lost on waiver
# order rather than price.

outcomes = bid_outcomes(claims)

print(
    render_bid_outcomes(
        season_only(outcomes),
        FIGURES_DIR / "07_waiver_bid_outcomes.png",
        title=f"{league_name}: Waiver Bid Outcomes, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 8: WAIVER BID OUTCOMES (CAREER)
# -----------------------------------------------------------------------------

career_outcomes = bid_outcomes(claims, career=True)

print(
    render_bid_outcomes(
        career_outcomes,
        FIGURES_DIR / "08_waiver_bid_outcomes_career.png",
        title=f"{league_name}: Waiver Bid Outcomes, Career",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 9: SURVIVAL LUCK TABLE (SEASON)
# -----------------------------------------------------------------------------
# `weekly_survival` puts every alive roster's score against the chop line (the
# lowest alive score) each week: margin above it, rank, percentile and a Z
# score. `chop_line_problems` lists any week where the chopped roster was not
# the lowest scorer; it should be empty. `survival_luck` sums each manager's
# margins into a luck index, counts nail-biter weeks (within 5% of the chop
# line) and skips the last weeks, where too few teams are alive for a Z score.

survival = weekly_survival(matchups, chopped_rosters, leagues, standings)
problems = chop_line_problems(survival)
print(f"Weeks where the chopped roster was not the lowest: {problems.height}")
print(guard_excluded_weeks(survival))

luck = survival_luck(survival)

print(
    render_luck_table(
        season_only(luck),
        FIGURES_DIR / "09_survival_luck_table.png",
        title=f"{league_name}: Survival Luck, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 10: SURVIVAL LUCK TABLE (CAREER)
# -----------------------------------------------------------------------------

career_luck = survival_luck(survival, career=True)

print(
    render_luck_table(
        career_luck,
        FIGURES_DIR / "10_survival_luck_table_career.png",
        title=f"{league_name}: Survival Luck, Career",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 11: SURVIVAL MARGIN VS. NAIL-BITERS
# -----------------------------------------------------------------------------
# Average margin above the chop line (x) against the share of weeks that were
# nail-biters (y). Lucky survivors sit top left: small cushions, many close
# calls, still alive.

print(
    render_luck_scatter(
        season_only(luck),
        FIGURES_DIR / "11_survival_margin_vs_nail_biters.png",
        title=f"{league_name}: Survival Margin vs. Nail-Biters, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 12: TOP-3 AND BOTTOM-3 WEEKS (SEASON)
# -----------------------------------------------------------------------------
# Weeks each manager finished in the top 3 or bottom 3 of the teams alive. A
# bottom-3 week that was not the chop is a close call.

finishes = weekly_finishes(survival)

print(
    render_weekly_finishes(
        season_only(finishes),
        FIGURES_DIR / "12_top3_bottom3_weeks.png",
        title=f"{league_name}: Top-3 and Bottom-3 Weeks, {SEASON}",
        dpi=DPI,
    )
)

# -----------------------------------------------------------------------------
# PLOT 13: TOP-3 AND BOTTOM-3 WEEKS (CAREER)
# -----------------------------------------------------------------------------

career_finishes = weekly_finishes(survival, career=True)

print(
    render_weekly_finishes(
        career_finishes,
        FIGURES_DIR / "13_top3_bottom3_weeks_career.png",
        title=f"{league_name}: Top-3 and Bottom-3 Weeks, Career",
        dpi=DPI,
    )
)
