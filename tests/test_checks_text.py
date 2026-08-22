"""Tests for the string primitives the contracts are built on.

WHAT THIS DEMONSTRATES
----------------------
Every false positive a deterministic conversation check produces can be traced
back to one of these functions being too eager, and every miss to one being too
timid. They are tested directly, in isolation from the trace machinery, because
debugging "why did the no-re-ask check fire" is a different and much slower
exercise than debugging "does `contains_value` match 6 inside 16".

The cases here are the specific ambiguities of spoken booking dialogue: numbers
written three ways, questions with no question mark, assertions welded to
questions by a comma, and near-repeats that differ only in filler.
"""

from __future__ import annotations

import pytest

from lab.checks.text import (
    clauses,
    contains_value,
    is_question,
    loose_equal,
    normalize,
    question_key,
    sentences,
    surface_forms,
    to_number,
)


# --------------------------------------------------------------------------- #
# normalize / sentences / clauses
# --------------------------------------------------------------------------- #


def test_normalize_lowercases_strips_punctuation_and_collapses_space() -> None:
    assert normalize("  Table for SIX, please!  ") == "table for six please"


def test_normalize_keeps_apostrophes_so_contractions_stay_one_token() -> None:
    """`isn't` must not become `isn t`, or the negation hedges stop matching."""
    assert normalize("It isn't confirmed") == "it isn't confirmed"


def test_sentences_splits_on_terminal_punctuation() -> None:
    text = "Six people, got it. And what time would you like? Great."
    assert sentences(text) == [
        "Six people, got it.",
        "And what time would you like?",
        "Great.",
    ]


def test_sentences_returns_one_unit_for_unpunctuated_speech() -> None:
    """STT output often has no punctuation at all; it is still one utterance."""
    assert sentences("table for six on friday at seven") == [
        "table for six on friday at seven"
    ]


def test_sentences_of_empty_text_is_empty() -> None:
    assert sentences("") == []
    assert sentences("   ") == []


def test_clauses_separates_an_assertion_welded_to_a_question() -> None:
    """The case that makes clause-splitting necessary rather than merely tidy.

    The sentence ends in a question mark, so any sentence-level mood filter
    discards it whole — including the confident false claim at the front.
    """
    assert clauses("You're all booked in, can I help with anything else?") == [
        "You're all booked in",
        "can I help with anything else?",
    ]


def test_clauses_leaves_a_simple_sentence_intact() -> None:
    assert clauses("Your table is confirmed.") == ["Your table is confirmed."]


# --------------------------------------------------------------------------- #
# is_question
# --------------------------------------------------------------------------- #


def test_question_mark_makes_a_question() -> None:
    assert is_question("What time would you like?") is True


def test_interrogative_opener_makes_a_question_without_punctuation() -> None:
    """Voice transcripts arrive unpunctuated; requiring '?' would blind every
    question-based contract on exactly the traces this harness targets."""
    assert is_question("how many people will that be") is True
    assert is_question("shall i confirm that") is True


def test_leading_filler_does_not_hide_an_interrogative_opener() -> None:
    assert is_question("So, how many people") is True


def test_an_assertion_is_not_a_question() -> None:
    assert is_question("Your table for six is confirmed") is False
    assert is_question("I've booked that for you") is False


def test_empty_string_is_not_a_question() -> None:
    assert is_question("") is False
    assert is_question("   ") is False


# --------------------------------------------------------------------------- #
# numbers and surface forms
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [(6, 6.0), ("6", 6.0), ("six", 6.0), ("  Six ", 6.0), (2.5, 2.5)],
)
def test_to_number_bridges_digits_and_words(value: object, expected: float) -> None:
    assert to_number(value) == expected


@pytest.mark.parametrize("value", ["banana", None, "", True, False])
def test_to_number_returns_none_for_non_numbers(value: object) -> None:
    """`True` is excluded on purpose: bool is an int in Python, and letting it
    through would make `loose_equal(True, 1)` quietly true."""
    assert to_number(value) is None


def test_surface_forms_of_an_integer_covers_digits_and_the_word() -> None:
    assert surface_forms(6) == {"6", "six"}


def test_surface_forms_of_a_phrase_is_its_normalised_self() -> None:
    assert surface_forms("Nut Allergy") == {"nut allergy"}


def test_surface_forms_of_none_is_empty() -> None:
    assert surface_forms(None) == set()


# --------------------------------------------------------------------------- #
# contains_value
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    ["a table for six", "6 people please", "SIX of us", "party of 6, thanks"],
)
def test_contains_value_finds_a_number_in_either_notation(text: str) -> None:
    assert contains_value(text, 6) is True


@pytest.mark.parametrize("text", ["sixteen people", "16 people", "a table for 60"])
def test_contains_value_respects_word_boundaries(text: str) -> None:
    """The bug this guards against: party_size 6 matching the "6" inside "16",
    which would make a re-ask of the real value look like a read-back."""
    assert contains_value(text, 6) is False


def test_contains_value_matches_a_multiword_phrase() -> None:
    assert contains_value("one of us has a severe nut allergy", "nut allergy") is True


def test_contains_value_tokens_mode_tolerates_reordering() -> None:
    """A speaker may say "allergy to nuts" where the scenario wrote "nut allergy"."""
    assert contains_value("an allergy involving nut products", "nut allergy", mode="tokens") is True
    assert contains_value("an allergy involving nut products", "nut allergy") is False


def test_contains_value_eq_mode_requires_the_whole_text() -> None:
    assert contains_value("six", 6, mode="eq") is True
    assert contains_value("a table for six", 6, mode="eq") is False


def test_contains_value_is_false_for_empty_text_or_missing_value() -> None:
    assert contains_value("", 6) is False
    assert contains_value("six people", None) is False


def test_unknown_match_mode_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="unknown match mode"):
        contains_value("six", 6, mode="fuzzy")


# --------------------------------------------------------------------------- #
# loose_equal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("left,right", [(6, "6"), (6, "six"), ("six", 6), ("Friday", " friday ")])
def test_loose_equal_bridges_notations(left: object, right: object) -> None:
    assert loose_equal(left, right) is True


@pytest.mark.parametrize("left,right", [(6, 7), (6, "seven"), ("Friday", "Saturday"), (6, None)])
def test_loose_equal_separates_genuinely_different_values(left: object, right: object) -> None:
    assert loose_equal(left, right) is False


def test_loose_equal_does_not_pretend_to_understand_times() -> None:
    """Documented non-goal: "7pm" and "19:00" are the same instant and this
    function says they are not. Guessing at time normalisation inside an
    assertion helper produces confident wrong verdicts; a parser with its own
    tests is the right home for it."""
    assert loose_equal("7pm", "19:00") is False


# --------------------------------------------------------------------------- #
# question_key
# --------------------------------------------------------------------------- #


def test_question_key_collapses_a_reworded_repeat() -> None:
    """A stuck agent rarely repeats itself verbatim, so loop detection has to
    survive the filler it adds on the second attempt."""
    assert question_key("How many people?") == question_key("Sorry, so how many people again?")


def test_question_key_preserves_word_order() -> None:
    """A set-based key would make these collide; a loop detector cannot afford it."""
    assert question_key("table for two") != question_key("two for table")


def test_question_key_of_pure_filler_is_empty() -> None:
    """An all-filler line has no content to compare, so it is not a repeat candidate."""
    assert question_key("Okay, sure, thanks!") == ()
