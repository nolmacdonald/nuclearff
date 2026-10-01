"""Smoke tests for nuclearff.report.chopped (epic #223): every render writes a PNG."""

from __future__ import annotations

import polars as pl

from nuclearff.chopped.luck import survival_luck
from nuclearff.report.chopped import render_luck_scatter, render_luck_table
from tests.test_chopped_luck import SURVIVAL


def _luck() -> pl.DataFrame:
    return survival_luck(SURVIVAL)


def test_render_luck_table_writes_a_png(tmp_path):
    out_path = render_luck_table(_luck(), tmp_path / "luck.png")
    assert out_path.stat().st_size > 0


def test_render_luck_scatter_writes_a_png(tmp_path):
    out_path = render_luck_scatter(_luck(), tmp_path / "scatter.png")
    assert out_path.stat().st_size > 0
