"""The package metadata: enough of it that a clean clone can actually install.

This file exists because of a real break. A search-and-replace that removed an
old name from the project deleted the `name` field with it, and `pyproject.toml`
stayed syntactically valid TOML — so nothing in the test suite noticed. The repo
imported fine, ran fine and tested fine in a working tree, and
`pip install -e .` failed on a fresh clone with a build-backend error that names
setuptools rather than the missing field.

That is the worst shape a defect can have: invisible to everyone who already has
the project working, and fatal to everyone who does not. These tests check the
fields the build backend requires, from the file rather than from an installed
distribution, so they fail in the working tree where the fix is cheap.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def config() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


@pytest.mark.parametrize("field", ["name", "version", "description", "requires-python"])
def test_the_build_backend_has_the_fields_it_refuses_to_build_without(config, field) -> None:
    project = config.get("project")
    assert project, "pyproject.toml has no [project] table"
    value = project.get(field)
    assert value, (
        f"[project].{field} is missing or empty. setuptools refuses to build an "
        f"editable install without it, and the error it raises names the build "
        f"backend rather than this field — so fix it here, not there."
    )


def test_the_name_matches_the_repository(config) -> None:
    assert config["project"]["name"] == "conversation-eval-lab"


def test_every_shipped_package_exists_on_disk(config) -> None:
    """A package listed for distribution but absent from the tree is a broken wheel."""
    includes = config["tool"]["setuptools"]["packages"]["find"]["include"]
    for pattern in includes:
        directory = pattern.rstrip("*")
        assert (ROOT / directory).is_dir(), (
            f"pyproject ships {pattern!r} but {directory}/ is not in the tree"
        )


def test_the_console_script_points_at_something_importable(config) -> None:
    scripts = config.get("project", {}).get("scripts", {})
    assert scripts, "no console script declared"
    for name, target in scripts.items():
        module, _, attribute = target.partition(":")
        imported = __import__(module, fromlist=[attribute or "__name__"])
        assert hasattr(imported, attribute), f"{name} points at {target}, which does not resolve"


def test_the_description_does_not_advertise_a_domain_the_repo_no_longer_ships(config) -> None:
    """The metadata is the first thing a stranger reads on PyPI or GitHub."""
    description = config["project"]["description"].lower()
    for gone in ("restaurant", "booking", "tablemate"):
        assert gone not in description, (
            f"the package description still mentions {gone!r}, which was removed"
        )
