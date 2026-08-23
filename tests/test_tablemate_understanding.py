"""Routing and extraction: the decisions, pinned.

WHAT THIS DEMONSTRATES
----------------------
Why the *agent's* comprehension deserves its own tests even though it is not the
thing under evaluation. Every finding the case study makes is conditional on the
agent having understood the turn: "the note never reached the booking" is a
propagation finding if the agent heard the note and a comprehension finding if it
did not, and those are different bugs with different fixes. So the cases below pin
the boundary between the two, and the false-positive cases matter as much as the
recall ones — a misroute would hide a planted defect behind a routing mistake, and
the case study would be measuring the wrong thing.
"""

from __future__ import annotations

import pytest

from tablemate.understanding import (
    Intent,
    extract_slots,
    intents_in,
    is_policy_question,
    looks_like_question,
    merged,
    note_clause,
    number_word,
    policy_topic,
    route_intent,
    wants_to_end,
)


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "utterance, expected",
    [
        ("I'd like to book a table for four on Friday", Intent.BOOK),
        ("Can I make a reservation for Saturday?", Intent.BOOK),
        ("Any tables free tonight?", Intent.BOOK),
        ("Could you move my booking to half seven?", Intent.MODIFY),
        ("I need to cancel my reservation", Intent.MODIFY),
        ("It's booking reference TM-1042", Intent.MODIFY),
        ("Do you take dogs?", Intent.POLICY),
        ("What's your cancellation policy?", Intent.POLICY),
        ("Is there parking nearby?", Intent.POLICY),
        ("Can I bring my own wine?", Intent.POLICY),
        ("Right, thanks", Intent.NONE),
    ],
)
def test_routing(utterance: str, expected: str) -> None:
    assert route_intent(utterance) == expected


def test_amendment_beats_policy_when_both_words_appear() -> None:
    """ "Cancel my booking" is a request; "cancellation policy" is a question."""
    assert route_intent("Please cancel my booking") == Intent.MODIFY
    assert route_intent("What is your cancellation policy?") == Intent.POLICY


def test_the_noun_booking_is_not_a_request_for_a_new_table() -> None:
    """Otherwise an agent books a table for someone ringing to cancel one."""
    assert Intent.BOOK not in intents_in(
        "I need to cancel my booking on Saturday but I can't find the email"
    )
    assert Intent.BOOK in intents_in("cancel TM-7731 and book the same party for Saturday")


def test_a_stated_requirement_is_not_a_policy_question() -> None:
    """The single most damaging misroute available: it hides a real defect."""
    assert not is_policy_question("Two of the four are children and will need high chairs")
    assert not is_policy_question("One of us has a severe peanut allergy")
    assert is_policy_question("Do you have high chairs?")


def test_a_question_and_a_requirement_in_one_breath_are_judged_per_clause() -> None:
    """Judged whole, this is a question that mentions allergies. It is not."""
    utterance = (
        "Can I book a table for two on Friday at 7pm? One of us has a severe "
        "peanut allergy."
    )
    assert intents_in(utterance) == frozenset({Intent.BOOK})


def test_multi_intent_keeps_both() -> None:
    found = intents_in(
        "Can I book a table for four on Friday, and do you have a set menu for groups?"
    )
    assert found == frozenset({Intent.BOOK, Intent.POLICY})


def test_questions_are_recognised_without_punctuation() -> None:
    """Transcription loses the question mark before it loses anything else."""
    assert looks_like_question("do you take dogs")
    assert looks_like_question("what time do you close")
    assert not looks_like_question("we will be ten minutes late")


@pytest.mark.parametrize(
    "utterance, topic",
    [
        ("Are dogs allowed?", "dogs"),
        ("Do you have high chairs?", "children"),
        ("Is there a dress code?", "dress_code"),
        ("What's the corkage?", "corkage"),
        ("Is it step-free?", "accessibility"),
        ("Is there parking?", "parking"),
        ("Do you take a deposit?", "deposit"),
        ("How does the kitchen handle cross-contamination?", "allergies"),
        ("Is there a set menu?", "menu"),
        ("What time do you close?", "general"),
    ],
)
def test_policy_topics(utterance: str, topic: str) -> None:
    assert policy_topic(utterance) == topic


def test_general_is_a_real_topic_the_sheet_does_not_carry() -> None:
    """Which is what makes the not-found path reachable instead of theoretical."""
    from tablemate.store import POLICIES

    assert "general" not in POLICIES


def test_sign_off_detection() -> None:
    assert wants_to_end("That's everything, thanks")
    assert wants_to_end("Lovely, bye")
    assert not wants_to_end("And another thing")


# --------------------------------------------------------------------------- #
# Slot extraction
# --------------------------------------------------------------------------- #


def test_extraction_order_stops_one_number_being_read_as_another() -> None:
    """ "A table for two at 7pm" is not a party of seven."""
    assert extract_slots("A table for two at 7pm on Friday") == {
        "party_size": 2,
        "time": "7pm",
        "date": "friday",
    }


def test_a_reference_is_not_a_party_size() -> None:
    assert extract_slots("It's TM-1042")["booking_ref"] == "TM-1042"
    assert "party_size" not in extract_slots("It's TM-1042")


def test_one_of_us_is_a_partitive_not_a_count() -> None:
    """The most common false positive in the party-size family."""
    found = extract_slots("One of us has a nut allergy")
    assert "party_size" not in found
    assert found["dietary"] == "One of us has a nut allergy"
    assert extract_slots("Five of us")["party_size"] == 5


def test_a_bare_number_is_only_a_party_size_when_one_was_asked_for() -> None:
    assert "party_size" not in extract_slots("Four")
    assert extract_slots("Four", expecting="party_size")["party_size"] == 4


def test_negated_allergen_talk_is_not_a_dietary_requirement() -> None:
    """ "No allergies, thanks" matches the vocabulary and means the opposite."""
    assert "dietary" not in extract_slots("No allergies, thanks")


def test_the_dietary_clause_carries_the_severity() -> None:
    """ "Nut" is not a useful note. "Severe nut allergy" is."""
    found = extract_slots("Two of us, and one has a severe nut allergy")
    assert found["dietary"] == "one has a severe nut allergy"


def test_the_name_on_the_booking_beats_the_caller_s_own_name() -> None:
    """A personal assistant says both in one breath."""
    found = extract_slots(
        "It is for my director, Helena Marchetti. I'm Tom Iredale, her assistant."
    )
    assert found["name"] == "Helena Marchetti"


def test_a_question_is_never_read_as_an_answer_to_the_name_prompt() -> None:
    """A wrong value is worse than a missing one: the agent stops asking."""
    assert "name" not in extract_slots(
        "How does the kitchen avoid cross-contamination?", expecting="name"
    )


@pytest.mark.parametrize(
    "utterance, time",
    [
        ("half past seven", None),
        ("at 7pm", "7pm"),
        ("at 7 pm", "7pm"),
        ("at 7:30pm", "7:30pm"),
        ("at 19:30", "19:30"),
        ("at noon", "noon"),
    ],
)
def test_times_in_the_forms_this_package_claims_to_read(
    utterance: str, time: str | None
) -> None:
    """ "Half past seven" is out of scope and says so — an honest gap, pinned."""
    assert extract_slots(utterance).get("time") == time


def test_notes_capture_the_request_and_skip_the_question() -> None:
    assert (
        note_clause("It's my mother's birthday and we'd love a candle")
        == "It's my mother's birthday and we'd love a candle"
    )
    assert note_clause("Do you have high chairs?") is None
    assert note_clause("we may be ten minutes late") == "we may be ten minutes late"
    assert note_clause("A table for two on Friday") is None


def test_a_note_is_trimmed_of_the_word_that_joined_it_to_the_sentence() -> None:
    assert note_clause("Two of us, and it's a birthday") == "it's a birthday"


def test_number_word_is_how_a_person_says_it() -> None:
    assert number_word(4) == "four"
    assert number_word(24) == "24"
    assert number_word("nonsense") == "nonsense"


def test_merged_never_erases_a_slot_with_silence() -> None:
    assert merged({"party_size": 4}, {"date": "friday", "time": None}) == {
        "party_size": 4,
        "date": "friday",
    }
