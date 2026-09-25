"""Sphinx configuration for nuclearff documentation."""

from __future__ import annotations

import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

# -- Path setup ---------------------------------------------------------------
# Add the src directory to sys.path so Sphinx can import the package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# -- Project information ------------------------------------------------------
project = "nuclearff"
copyright = "2026, Nolan MacDonald"  # noqa: A001
author = "Nolan MacDonald"
# Derive release from the installed package to avoid version drift.
release = _pkg_version("nuclearff")

# -- General configuration ----------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_copybutton",
    "sphinx_design",
    "myst_parser",
]

# Names re-exported from a package __init__ are documented both there and in
# the module that defines them, so an unqualified cross-reference has two valid
# targets. Every other warning class remains fatal under -W.
suppress_warnings = ["ref.python"]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- Napoleon (Google docstring) settings -------------------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_use_admonition_for_examples = False
napoleon_use_ivar = True
napoleon_use_param = True
napoleon_use_rtype = True

# -- Autodoc settings ---------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}
autosummary_generate = True


def _skip_member_shadowing_its_own_submodule(app, what, name, obj, skip, options):  # noqa: ARG001
    """Don't re-document a re-exported member on any page but its own.

    A package that does ``from .vorp import vorp`` (true of
    ``nuclearff.valuation``: the ``vorp`` submodule's own ``vorp()``
    function) ends up with a member whose qualified name
    (``nuclearff.valuation.vorp``) is identical to its home submodule's own
    qualified name. The recursive autosummary already gives that submodule
    its own dedicated page; documenting the re-exported function *again* on
    the parent package's overview page registers the same name twice and
    Sphinx raises "duplicate object description" (a hard error under
    ``-W``). Skip the member everywhere except the page for its own home
    module, where it belongs.
    """
    if skip:
        return skip
    home_module = getattr(obj, "__module__", None)
    if not home_module or home_module.rsplit(".", 1)[-1] != name:
        return skip
    # ``ref_context["py:module"]`` is unset at this point in autodoc's own
    # member-enumeration pass (verified empirically -- it's a domain
    # cross-reference concept, populated later than skip-member fires), so
    # use the docname of the page actually being built instead: it's
    # ``api/generated/<home_module>`` on the member's own dedicated page and
    # something else (e.g. the parent package's overview page) everywhere
    # this collision matters. Empty here means an early autosummary-table
    # analysis pass, not a real page -- leave those alone.
    docname = app.env.docname
    if docname and not docname.endswith(home_module):
        return True
    return skip


def setup(app):
    app.connect("autodoc-skip-member", _skip_member_shadowing_its_own_submodule)


# -- Intersphinx mapping ------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
    "pandas": ("https://pandas.pydata.org/docs", None),
    "matplotlib": ("https://matplotlib.org/stable", None),
    "scipy": ("https://docs.scipy.org/doc/scipy", None),
    "polars": ("https://docs.pola.rs/api/python/stable", None),
}

# -- HTML output options ------------------------------------------------------
html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]
html_css_files = ["css/custom.css"]

html_logo = "_static/logo/nuclearff-light-color.svg"
html_favicon = "_static/logo/nuclearff-light-color.svg"

html_theme_options = {
    "navbar_start": ["navbar-logo"],
    "navbar_center": ["navbar-nav"],
    "navbar_end": ["navbar-icon-links", "theme-switcher"],
    "footer_start": ["footer-logo", "copyright"],
    "footer_center": [],
    "footer_end": [],
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/nolmacdonald/nuclearff",
            "icon": "fa-brands fa-github",
        },
    ],
    "use_edit_page_button": False,
    "show_toc_level": 2,
    "navigation_with_keys": False,
    "pygments_light_style": "default",
    "pygments_dark_style": "monokai",
}

html_context = {
    "github_user": "nolmacdonald",
    "github_repo": "nuclearff",
    "github_version": "main",
    "doc_path": "docs",
}
