"""Bye-week collision warnings for a draft pick (issue #99).

Nothing else in the draft companion says that a candidate's bye week lands on
the same week as several players already rostered at the same position, which
can leave a real gap in a starting lineup. :func:`bye_weeks` derives each NFL
team's bye from a schedule as returned by
:func:`nuclearff.nflverse.schedules.load_schedules`, and
:func:`bye_collisions` compares a candidate's bye against a roster's picks so
far. The result is informational only: nothing here blocks or ranks a pick.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from nuclearff.nflverse.schedules import team_opponents

_REGULAR_SEASON = "REG"
_SCHEDULE_COLUMNS = ("season", "week", "game_type", "home_team", "away_team")


def bye_weeks(schedule: pl.DataFrame, season: int | None = None) -> dict[str, int]:
    """Derive each team's regular-season bye week from a schedule.

    A team's bye is the one week between week 1 and the last regular-season
    week in which it has no game.

    Args:
        schedule: Rows as returned by
            :func:`nuclearff.nflverse.schedules.load_schedules`.
        season: Season to use. Defaults to the latest season in ``schedule``.

    Returns:
        Team abbreviation mapped to its bye week. A team that does not have
        exactly one week without a game is left out, since a partial or
        unusual schedule gives no reliable bye (for example a team with no
        bye in a shortened season).

    Raises:
        ValueError: If ``schedule`` is missing a required column.
    """
    missing = [name for name in _SCHEDULE_COLUMNS if name not in schedule.columns]
    if missing:
        raise ValueError(
            f"bye_weeks: schedule is missing expected column(s) {missing!r} "
            f"(got {schedule.columns!r})."
        )

    games = schedule.filter(pl.col("game_type") == _REGULAR_SEASON)
    if season is None and not games.is_empty():
        season = games.select(pl.col("season").max()).item()
    games = games.filter(pl.col("season") == season)
    if games.is_empty():
        return {}

    last_week = games.select(pl.col("week").max()).item()
    played = team_opponents(games).group_by("team").agg(pl.col("week").unique())
    byes: dict[str, int] = {}
    for team, weeks in played.iter_rows():
        missing_weeks = sorted(set(range(1, last_week + 1)) - set(weeks))
        if len(missing_weeks) == 1:
            byes[team] = missing_weeks[0]
    return byes


def bye_collisions(
    roster_picks: list[dict[str, Any]],
    candidate_player: dict[str, Any],
    schedule: pl.DataFrame,
    season: int | None = None,
) -> list[str]:
    """Warn when a candidate's bye week collides with same-position players.

    Args:
        roster_picks: The roster's picks so far, each shaped like
            :func:`nuclearff.sleeper.draft.draft_pick_rows`'s output (needs
            ``"position"`` and ``"team"``), typically
            ``DraftState.picks_by_roster[roster_id]``.
        candidate_player: The player under consideration, with the same
            ``"position"`` and ``"team"`` keys.
        schedule: Rows as returned by
            :func:`nuclearff.nflverse.schedules.load_schedules`.
        season: Season whose byes to use. Defaults to the latest season in
            ``schedule``.

    Returns:
        One warning per collision, for example ``"3 WR on bye in week 7
        (including the candidate)"``. Empty when fewer than two players at
        the candidate's position, counting the candidate, share its bye week,
        for an empty roster, and when the candidate's position, team or bye
        week is unknown. Roster players with an unknown team are not counted.

    Raises:
        ValueError: If ``schedule`` is missing a required column.
    """
    position = candidate_player.get("position")
    team = candidate_player.get("team")
    if not isinstance(position, str) or not isinstance(team, str):
        return []

    byes = bye_weeks(schedule, season)
    candidate_bye = byes.get(team)
    if candidate_bye is None:
        return []

    same_week = 1  # the candidate
    for pick in roster_picks:
        pick_team = pick.get("team")
        if (
            pick.get("position") == position
            and isinstance(pick_team, str)
            and byes.get(pick_team) == candidate_bye
        ):
            same_week += 1

    if same_week < 2:
        return []
    return [
        f"{same_week} {position} on bye in week {candidate_bye} "
        "(including the candidate)"
    ]
