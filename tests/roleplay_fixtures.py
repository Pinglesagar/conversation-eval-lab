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
#: `test_roleplay_corpus.py` asserts it — an alias pointing at a deleted row would
#: otherwise fail as a KeyError inside an unrelated test.
ALIASES: dict[str, str] = {
    "exemplary": "pitch-exemplary-eu-retail-run",
    "terse": "pitch-terse-customer-patient-probing",
    "featuredump": "pitch-feature-dump-no-discovery",
    "cold": "pitch-cold-scorer-single-run-control",
    "missing": "compliance-missing-risk-disclosure",
    "advice": "compliance-explicit-unlicensed-advice",
    "reassurance": "compliance-no-real-risk-reassurance",
    "guaranteed": "compliance-guaranteed-return-caught",
    "aggressive": "objection-aggressive-fee-challenge",
    "unanswered": "objection-lock-in-left-unanswered",
    "praise": "objection-praise-for-unasked-question",
    "consistency": "consistency-identical-transcript-warm-k5",
    "borderline": "consistency-borderline-transcript-warm-k5",
    "apac": "locale-apac-suitability-disclosure",
    "spanish": "locale-es-mx-registered-spanish-disclosure",
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
