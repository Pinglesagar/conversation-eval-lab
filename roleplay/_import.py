"""Resolve a dotted path to an object.

Lives here rather than in `lab` because it is the one thing the live trainee
factory needs from a module that was otherwise all argparse. Ten lines beats a
dependency on a CLI.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

__all__ = ["import_object"]


def import_object(dotted: str) -> Any:
    """Import `pkg.mod:name` (or `pkg.mod.name`) and return the object."""
    module_name, _, attribute = dotted.partition(":")
    if not attribute:
        module_name, _, attribute = dotted.rpartition(".")
    return getattr(_import_module(module_name), attribute)


def _import_module(name: str) -> Any:
    """Import `name`, looking in the checkout as well as on `sys.path`.

    `lab` is installed as a package; the case study beside it — `scenarios`,
    `error_analysis`, the fixtures — deliberately is not, because it is not part
    of the library and has no business in a wheel. That layering is right and it
    has one sharp edge: a console script does not put the working directory on
    `sys.path`, so `evallab validate` would fail to find the corpus while
    `python -m lab.cli validate` found it, purely because of how it was invoked.

    So the checkout root is added on the retry, and only on the retry: an
    installed module of the same name still wins, and nothing is put on the path
    when nothing needed it.

    And when the retry fails too, that is the end of the road, so it ends in a
    sentence rather than a stack. The way to get here is a *non-editable* install
    — `pip install .` instead of `pip install -e .` — after which `repo_root()`
    points inside site-packages, where the case study was never copied because
    the packaging deliberately excludes it. The traceback that used to come out
    named `scenarios` and nothing else, which is the least useful half of the
    explanation.
    """
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        root = str(repo_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"cannot import '{name}', and it is not beside the library either "
                f"(looked in {root}).\n"
                "The case study — scenarios/, roleplay/, fixtures/ — "
                "ships in the checkout, not in the wheel, so an installed copy has "
                "only the library.\n"
                "Install for development from a clone instead:\n"
                '    pip install -e ".[dev]"\n'
                "or point the command at your own corpus with --corpus-module."
            ) from exc


def repo_root() -> Path:
    """The checkout root, for resolving the case study's default paths."""
    return Path(__file__).resolve().parents[1]
