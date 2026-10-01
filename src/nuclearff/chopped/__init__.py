"""Chopped (guillotine) league analytics (epic #223).

In a Chopped league the lowest scorer still alive is eliminated every week
and their players go to waivers, so there is no head-to-head record to
analyze. This package measures what matters instead: survival margin above
the chop line, FAAB spending, and waiver bidding. Everything reads tables
nuclearff already persists; no new Sleeper endpoints.

``common.py`` holds the shared loaders (Chopped league settings, manager
names by owner id, who was alive each week) the other modules build on.
"""
