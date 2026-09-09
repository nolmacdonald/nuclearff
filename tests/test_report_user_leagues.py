"""Unit tests for nuclearff.report.user_leagues (GitHub Issue 77).

Avatar downloads are mocked with ``responses``, same posture
``test_report_tables.py`` uses for headshots — it intercepts at the
``requests`` transport layer, compatible with ``conftest.py``'s autouse
``_no_network`` guard.
"""

from __future__ import annotations

from io import BytesIO

import responses

from nuclearff.report.user_leagues import (
    render_user_leagues_table,
    summarize_league_types,
)


def _one_pixel_png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), color=(0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _leagues() -> list[dict]:
    """Shaped like the real fixture (`tests/fixtures/sleeper/league.json`),
    with the real type-3-but-no-`last_chopped_leg` wrinkle Issue 77 found
    live on a real account (`NUCLEARFF CHOPPED $50`)."""
    return [
        {
            "league_id": "1",
            "name": "NUCLEARFF REDRAFT",
            "avatar": "abc123",
            "status": "in_season",
            "total_rosters": 10,
            "settings": {"type": 0},
        },
        {
            "league_id": "2",
            "name": "NUCLEARFF DYNASTY",
            "avatar": None,
            "status": "in_season",
            "total_rosters": 12,
            "settings": {"type": 2},
        },
        {
            "league_id": "3",
            "name": "NUCLEARFF CHOPPED $50",
            "avatar": None,
            "status": "in_season",
            "total_rosters": 16,
            "settings": {"type": 3},
        },
        {
            "league_id": "4",
            "name": "Freeman Forever League",
            "avatar": None,
            "status": "in_season",
            "total_rosters": 10,
            "settings": {"type": 1},
        },
    ]


# --- summarize_league_types -------------------------------------------------


def test_summarize_league_types_counts_by_type_and_totals():
    summary = summarize_league_types(_leagues())

    assert summary == {
        "chopped": 1,
        "dynasty": 1,
        "keeper": 1,
        "redraft": 1,
        "Total": 4,
    }


def test_summarize_league_types_sums_to_total():
    leagues = _leagues() + [_leagues()[0]]  # a second redraft league

    summary = summarize_league_types(leagues)

    assert sum(v for k, v in summary.items() if k != "Total") == summary["Total"]
    assert summary["redraft"] == 2


def test_summarize_league_types_handles_no_leagues():
    assert summarize_league_types([]) == {"Total": 0}


def test_summarize_league_types_does_not_hardcode_four_types():
    """Only the types actually present appear -- no zero-filled template rows."""
    leagues = [_leagues()[0]]  # redraft only

    assert summarize_league_types(leagues) == {"redraft": 1, "Total": 1}


# --- render_user_leagues_table ----------------------------------------------


@responses.activate
def test_render_user_leagues_table_writes_a_png(tmp_path):
    responses.get(
        "https://sleepercdn.com/avatars/abc123",
        body=_one_pixel_png(),
        content_type="image/png",
    )
    out_path = tmp_path / "leagues.png"

    result = render_user_leagues_table(
        _leagues(), out_path, cache_dir=tmp_path / "cache"
    )

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_user_leagues_table_handles_a_league_with_no_avatar(tmp_path):
    """Real for several leagues on a real account -- must not crash."""
    out_path = tmp_path / "leagues.png"

    result = render_user_leagues_table(
        [_leagues()[1]], out_path, cache_dir=tmp_path / "cache"
    )

    assert result.is_file()


def test_render_user_leagues_table_handles_a_single_league(tmp_path):
    out_path = tmp_path / "leagues.png"

    result = render_user_leagues_table(
        [_leagues()[0]], out_path, cache_dir=tmp_path / "cache"
    )

    assert result.is_file()
