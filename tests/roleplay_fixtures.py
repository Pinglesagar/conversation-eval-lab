"""Shared fixtures for the roleplay tests.

A module rather than a `conftest.py`, deliberately: the booking suite in this repo
has no conftest and adding one would put roleplay fixtures in scope for every test
in the tree. Importing them explicitly keeps the two packs independent, which is
the same separation the packages themselves maintain.

The trainee scripts are not written here. They are read from the corpus, by short
alias, so a test and the YAML row it is about can never drift apart — if a row's
script changes, the test that asserts a score for it changes with it or fails.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from roleplay.corpus import Corpus, load_corpus
from roleplay.runtime import RoleplayCoach
from roleplay.scorer import RubricScorer

__all__ = ["ALIASES", "corpus", "profiles", "coach", "script"]

#: Short names for the rows the tests refer to. Every alias must resolve, and
#: `test_roleplay_checks.py` asserts it — an alias pointing at a deleted row would
#: otherwise fail as a KeyError inside an unrelated test.
#:
#: Not one alias per row. The corpus is seventy rows and these are the dozen or so
#: that behavioural tests name individually; the rest are exercised by the tests
#: that iterate the whole corpus, which is the right way round — a fixture list
#: that had to grow with every row would make adding a row a two-file change and
#: would say nothing about the row that was added.
ALIASES: dict[str, str] = {
    "exemplary": "01-control-a-good-session",
    "missing": "02-grader-claims-a-disclosure-that-never-happened",
    "consistency": "03-same-transcript-different-score",
    "featuredump": "04-feedback-cites-what-never-happened",
    "spanish": "05-spanish-disclosure-not-credited",
}


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    """The loaded corpus. Session-scoped: it is read-only and parsing it is I/O."""
    return load_corpus()


@pytest.fixture
def profiles(corpus: Corpus) -> dict[str, Any]:
    return corpus.profiles


@pytest.fixture
def coach() -> RoleplayCoach:
    """A freshly deployed product. Function-scoped, so no test inherits a warm
    scoring service from another — the tests that want a warm one build it."""
    return RoleplayCoach(scorer=RubricScorer())


@pytest.fixture
def script(corpus: Corpus) -> Callable[[str], dict[str, Any]]:
    """`script("exemplary")` -> the kwargs for one `RoleplayCoach.run` call.

    Returns everything the adapter needs and nothing it does not: no `session_id`,
    so a caller can pin one when a test compares two traces byte for byte.
    """

    def build(alias: str) -> dict[str, Any]:
        scenario = corpus.by_id(ALIASES[alias])
        return {
            "scenario_id": scenario.id,
            "trainee_turns": scenario.trainee.turns,
            "profile": corpus.profile_for(scenario),
            "jurisdiction": scenario.jurisdiction,
            "language": scenario.language,
        }

    return build
