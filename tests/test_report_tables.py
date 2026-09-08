"""Unit tests for nuclearff.report.tables: headshot fetch and PNG rendering.

The headshot download is mocked with ``responses``, same as the Sleeper HTTP
calls elsewhere in the suite — it intercepts at the ``requests`` transport
layer, before a socket is ever opened, so it stays compatible with
``conftest.py``'s autouse ``_no_network`` guard. This is the rendering-path
coverage flagged missing in issue #38: previously only the "rendering
skipped cleanly" branch (``render_tables=False``) was exercised, in
``test_report_build.py``.
"""

from __future__ import annotations

from io import BytesIO

import polars as pl
import pytest
import responses

from nuclearff.report.tables import _fetch_headshot, render_position_table


def _one_pixel_png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _board() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["a", "b"],
            "player_display_name": ["A Back", "B Wideout"],
            "position": ["RB", "WR"],
            "recent_team": ["DET", "CIN"],
            "adp_overall": [2.5, 1.5],
            "value_estimate": [300.0, 250.0],
            "vorp": [150.0, 110.0],
            "auction_value": [88.0, 65.0],
            "headshot_url": [None, "https://img.test/b.png"],
            "rank_position": [1, 1],
        }
    )


@responses.activate
def test_fetch_headshot_downloads_and_caches(tmp_path):
    url = "https://img.test/a.png"
    responses.get(url, body=_one_pixel_png(), content_type="image/png")
    dest = tmp_path / "a.png"

    path = _fetch_headshot(url, dest)

    assert path == str(dest)
    assert dest.is_file()
    assert len(responses.calls) == 1

    # A second call for the same dest reuses the cached file on disk.
    path_again = _fetch_headshot(url, dest)

    assert path_again == str(dest)
    assert len(responses.calls) == 1


def test_fetch_headshot_returns_empty_string_for_a_missing_url(tmp_path):
    assert _fetch_headshot(None, tmp_path / "x.png") == ""


@responses.activate
def test_fetch_headshot_returns_empty_string_on_failed_download(tmp_path):
    url = "https://img.test/missing.png"
    responses.get(url, status=404)

    assert _fetch_headshot(url, tmp_path / "missing.png") == ""


@responses.activate
def test_render_position_table_writes_a_png(tmp_path):
    responses.get(
        "https://img.test/b.png", body=_one_pixel_png(), content_type="image/png"
    )
    out_path = tmp_path / "wr.png"

    result = render_position_table(
        _board(),
        "WR",
        out_path,
        context={"league_name": "Test League", "num_teams": 10, "budget_per_team": 200},
        cache_dir=tmp_path / "cache",
    )

    assert result == out_path
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_position_table_raises_for_an_absent_position(tmp_path):
    with pytest.raises(ValueError, match="no players at position"):
        render_position_table(_board(), "K", tmp_path / "k.png")


def test_render_position_table_handles_a_missing_headshot(tmp_path):
    """A player with no headshot_url (e.g. a rookie) must not crash the table.

    Regression test: `circled_image` calls `plt.imread("")` when a row's
    `headshot_path` is empty, raising `FileNotFoundError` deep inside
    matplotlib/PIL. `_fetch_headshot` legitimately returns `""` for a missing
    URL, so this was reachable on any real board with an unheadshotted
    player, not just a contrived input.
    """
    board = pl.DataFrame(
        {
            "player_id": ["b", "c"],
            "player_display_name": ["B Wideout", "C Wideout"],
            "position": ["WR", "WR"],
            "recent_team": ["CIN", "SEA"],
            "adp_overall": [1.5, 5.0],
            "value_estimate": [250.0, 200.0],
            "vorp": [110.0, 90.0],
            "auction_value": [65.0, 50.0],
            "headshot_url": [None, None],
            "rank_position": [1, 2],
        }
    )
    out_path = tmp_path / "wr.png"

    result = render_position_table(board, "WR", out_path, cache_dir=tmp_path / "cache")

    assert result == out_path
    assert out_path.is_file()
