"""Live draft companion (epic #102).

``sos.py`` (issue #105/#106), ``state.py`` (issue #92), ``needs.py`` (issue
#93), ``byeweeks.py`` (issue #99), ``stacking.py`` (issue #98),
``scouting.py`` (issue #107), ``injuries.py`` (issue #103), and
``trades.py`` (issue #104) are the modules landed here so far; the rest of
the epic's sub-issues (#94, #96-97, #100) add their own modules to this
package as they're implemented. Each is imported from its own submodule
path (e.g. ``from nuclearff.draft.state import poll_draft_state``), not
re-exported here -- matching how ``sos.py`` is already used elsewhere in
this codebase.
"""
