"""Unit tests for nuclearff.report.wins (GitHub Issue 79).

Avatar downloads are mocked with ``responses``, same posture
``test_report_user_leagues.py`` uses.
"""

from __future__ import annotations

from io import BytesIO

import polars as pl
import responses

from nuclearff.report.wins import render_cumulative_wins


def _one_pixel_png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), color=(0, 128, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _cumulative() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "manager": ["nolmacdonald", "nolmacdonald", "casitzmann"],
            "season": [2025, 2025, 2025],
            "week": [1, 2, 1],
            "game_number": [1, 2, 1],
            "cumulative_wins": [1, 1, 0],
        }
    )


@responses.activate
def test_render_cumulative_wins_writes_a_png(tmp_path):
    responses.get(
        "https://sleepercdn.com/avatars/abc123",
        body=_one_pixel_png(),
        content_type="image/png",
    )
    out_path = tmp_path / "wins.png"

    result = render_cumulative_wins(
        _cumulative(),
        {"nolmacdonald": "abc123", "casitzmann": None},
        out_path,
        cache_dir=tmp_path / "cache",
    )

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_cumulative_wins_handles_a_manager_with_no_avatar(tmp_path):
    """A manager missing from avatar_ids (or mapped to None) must not crash."""
    out_path = tmp_path / "wins.png"

    result = render_cumulative_wins(
        _cumulative(), {}, out_path, cache_dir=tmp_path / "cache"
    )

    assert result.is_file()


def test_render_cumulative_wins_handles_a_single_manager(tmp_path):
    out_path = tmp_path / "wins.png"
    cumulative = pl.DataFrame(
        {
            "manager": ["nolmacdonald"],
            "season": [2025],
            "week": [1],
            "game_number": [1],
            "cumulative_wins": [1],
        }
    )

    result = render_cumulative_wins(
        cumulative, {}, out_path, cache_dir=tmp_path / "cache"
    )

    assert result.is_file()
