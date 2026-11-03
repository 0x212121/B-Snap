# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information
import os
import sys
from pathlib import Path

from sphinx.application import Sphinx

# Tambahkan root project ke sys.path
sys.path.insert(0, os.path.abspath("../.."))


from app.version import __version__

version = __version__


project = 'B-SNAP'
copyright = '2025, Indra W.'
author = 'Indra W.'
release = f'v{version}'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",      # Google/NumPy style docstring
    "sphinx.ext.viewcode",      # link ke source code
    "sphinxcontrib.openapi",    # render OpenAPI spec
    "myst_parser",              # support Markdown
]

templates_path = ['_templates']
exclude_patterns = ['README.md']  # Exclude README from build



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

# Support Markdown
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Keep Markdown heading links consistent with the GitHub documentation sources.
myst_heading_anchors = 3

# Theme
# html_theme = "sphinx_rtd_theme"
html_permalinks_icon = '<span>#</span>'
html_theme = 'sphinxawesome_theme'
html_static_path = ["_static"]


def _normalize_html_whitespace(app: Sphinx, exception: Exception | None) -> None:
    """Keep the tracked HTML bundle free of theme-generated trailing whitespace."""
    if exception is not None or app.builder.format != "html":
        return
    for path in Path(app.outdir).rglob("*.html"):
        original = path.read_text(encoding="utf-8")
        normalized = "\n".join(line.rstrip() for line in original.splitlines()) + "\n"
        if normalized != original:
            path.write_text(normalized, encoding="utf-8")


def setup(app: Sphinx) -> None:
    """Normalize generated artifacts after a successful HTML build."""
    app.connect("build-finished", _normalize_html_whitespace)
