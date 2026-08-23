"""The three planted defects: reproducible, isolated, and caught by the suite.

WHAT THIS DEMONSTRATES
----------------------
Four claims, and this file exists because all four are easy to *say* and worth
proving.

**1. They reproduce.** Byte-identically, on any machine, with no key and no
network. A defect that fires on some runs is a story about luck, and a case study
built on one cannot be audited.

**2. They are real, not simulated.** Each assertion here reads the diary or the
tool ledger, not a flag. "No booking exists" is checked by looking for the booking.

**3. Each has a control that stays green.** The pair is the evidence. Five books
and six does not; the allergy survives a plain booking and is lost across a policy
detour. Without the control, a finding is a symptom; with it, a finding names a
boundary.

**4. The eval suite catches them from the outside.** The last section runs the real
`lab.checks` contracts over real traces and asserts each defect is found by
*exactly one* check, with quotable evidence. That is the claim the whole repository
is making, and this is where it is either true or false.

This file names the defects because it is a test of them. The code under test does
not, and `tablemate/SEEDED_BUGS.md` is the only prose that explains them.
"""

from __future__ import annotations

import pytest

from lab.checks import (
    ContractSet,
    FieldPropagationContract,
    NoReAskContract,
    PromiseContract,
    ToolContract,
    TrackedField,
)
from lab.clock import FakeClock
from lab.simulator import ScriptedCaller, run_pass_k, run_scenario
from tablemate.agents import LARGE_PARTY_THRESHOLD
from tablemate.runtime import LLMBackend, ScriptedBackend, build_agent

# --------------------------------------------------------------------------- #
# Conversations. Each is the shortest script that reaches the behaviour.
# --------------------------------------------------------------------------- #

PARTY_OF_SIX = [
    "Hi, can I book a table for six on Friday at 8pm?",
    "Okonkwo.",
    "Lovely, that's everything.",
]
PARTY_OF_FIVE = [
    "Hi, can I book a table for five on Friday at 8pm?",
    "Bose.",
    "Lovely, that's everything.",
]
BOOK_THEN_MOVE = [
    "I'd like to book a table for four on Wednesday at 7pm.",
    "Kelleher.",
    "Actually, could we move it to 7:30pm instead?",
    "Four.",
    "That's all, thanks.",
]
CANCEL_THEN_REBOOK = [
    "Can you cancel TM-7731 and book the same party for Saturday at 8pm? Four of us.",
    "A change of plan at work.",
    "Yes please, Saturday at 8pm for four.",
    "Okonkwo.",
]
ALLERGY_THEN_POLICY = [
    "Can I book a table for two on Friday at 7pm? One of us has a severe peanut allergy.",
    "How does the kitchen avoid cross-contamination?",
    "Ellery.",
    "That's all, thanks.",
]
ALLERGY_ONLY = [
    "Can I book a table for two on Wednesday at 7pm? One of us has a severe peanut allergy.",
    "Ellery.",
]


def talk(lines: list[str], **kwargs: object):
    agent = build_agent(clock=FakeClock(), **kwargs)  # type: ignore[arg-type]
    turns = [agent(line) for line in lines]
    return agent, turns


def drive(scenario_id: str, lines: list[str]):
    """Run a conversation through the harness and return (trace, agent)."""
    clock = FakeClock()
    agent = build_agent(clock=clock)
    trace = run_scenario(
        scenario_id=scenario_id,
        agent=agent,
        caller=ScriptedCaller(lines),
        clock=clock,
    )
    return trace, agent


def spoken(turns: list) -> str:
    return " ".join(t.text for t in turns)


# --------------------------------------------------------------------------- #
# BUG-1: phantom confirmation
# --------------------------------------------------------------------------- #


def test_a_party_of_six_is_told_it_is_booked_and_is_not() -> None:
    agent, turns = talk(PARTY_OF_SIX)
    assert "booked in" in spoken(turns)
    assert "create_booking" not in agent.tool_names()
    assert [b for b in agent.store.bookings if b.party_size == 6 and b.date == "friday"] == []


def test_a_party_of_five_really_is_booked() -> None:
    """The control. One digit apart, and it localises the defect to the threshold."""
    agent, turns = talk(PARTY_OF_FIVE)
    assert "booked in" in spoken(turns)
    assert "create_booking" in agent.tool_names()
    assert agent.store.booking("TM-2001").party_size == 5


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5])
def test_every_size_below_the_threshold_books(size: int) -> None:
    agent, _ = talk([f"A table for {size} on Monday at 6pm please.", "Ferreira."])
    assert "create_booking" in agent.tool_names(), size


@pytest.mark.parametrize("size", [6, 7, 8])
def test_no_size_at_or_above_the_threshold_books(size: int) -> None:
    agent, turns = talk([f"A table for {size} on Monday at 6pm please.", "Ferreira."])
    assert "create_booking" not in agent.tool_names(), size
    assert "booked in" in spoken(turns), size


def test_the_threshold_is_the_one_the_code_declares() -> None:
    """Pins the boundary pair to the constant, so a change moves both together."""
    assert LARGE_PARTY_THRESHOLD == 6


def test_the_transcript_of_the_phantom_confirmation_reads_as_a_success() -> None:
    """If the words looked wrong, a transcript-only review would find this."""
    _, turns = talk(PARTY_OF_SIX)
    said = spoken(turns)
    for tell in ("sorry", "cannot", "unable", "problem", "error"):
        assert tell not in said.lower()
    assert "six" in said and "Friday" in said and "8pm" in said


# --------------------------------------------------------------------------- #
# BUG-2: the amendment desk asks what it already knows
# --------------------------------------------------------------------------- #


def test_the_party_size_is_asked_again_after_the_call_turns_to_a_change() -> None:
    _, turns = talk(BOOK_THEN_MOVE)
    asks = [t for t in turns if "how many" in t.text.lower()]
    assert len(asks) == 1
    assert asks[0].agent == "ModificationAgent"


def test_it_is_an_ask_and_not_a_read_back() -> None:
    """The distinction the check turns on: a read-back states what it checks."""
    _, turns = talk(BOOK_THEN_MOVE)
    ask = next(t.text for t in turns if "how many" in t.text.lower())
    assert "four" not in ask.lower() and "4" not in ask


def test_the_change_is_still_applied_correctly() -> None:
    """The defect costs a turn. It does not corrupt the booking."""
    agent, _ = talk(BOOK_THEN_MOVE)
    assert agent.store.booking("TM-2001").time == "7:30pm"
    assert agent.store.booking("TM-2001").party_size == 4


def test_an_amendment_from_an_existing_reference_asks_too() -> None:
    _, turns = talk(
        ["I'd like to add one more person to booking TM-3364, make it five.", "Five."]
    )
    assert "how many" in spoken(turns).lower()


def test_a_cancellation_does_not_ask_the_head_count() -> None:
    """The control: no re-seat, no question, so the rebook flow stays clean."""
    agent, turns = talk(CANCEL_THEN_REBOOK)
    assert "how many" not in spoken(turns).lower()
    assert agent.tool_names().count("cancel_booking") == 1
    assert agent.tool_names().count("create_booking") == 1


def test_the_date_is_not_re_asked_only_the_head_count_is() -> None:
    """Scoping the defect: a suite that reports both is over-firing."""
    _, turns = talk(BOOK_THEN_MOVE)
    said = spoken(turns).lower()
    assert "what date" not in said
    assert "what time" not in said


# --------------------------------------------------------------------------- #
# BUG-3: the note falls out of the record at the policy desk
# --------------------------------------------------------------------------- #


def test_the_allergy_is_lost_across_the_policy_detour() -> None:
    agent, _ = talk(ALLERGY_THEN_POLICY)
    created = [c for c in agent.toolbox.calls if c.name == "create_booking"]
    assert created, "the booking must still be made, or this is a different bug"
    assert created[0].args["notes"] == ""
    assert agent.store.booking("TM-2001").notes == ""


def test_the_same_allergy_survives_a_booking_with_no_detour() -> None:
    """The control that turns a guess about the model into a claim about a boundary."""
    agent, _ = talk(ALLERGY_ONLY)
    assert "peanut" in agent.store.booking("TM-2001").notes


def test_the_loss_is_silent_the_caller_is_never_asked_again() -> None:
    """Which is why this is data loss rather than an annoyance."""
    _, turns = talk(ALLERGY_THEN_POLICY)
    assert "allergies" not in spoken(turns).lower().replace("allergens", "")


def test_the_policy_answer_itself_is_correct() -> None:
    """Nothing the caller hears is untrue. That is what makes it hard to spot."""
    _, turns = talk(ALLERGY_THEN_POLICY)
    assert "allergens" in turns[1].text
    assert turns[1].tools[0].args["topic"] == "allergies"


def test_the_booking_fields_survive_the_detour() -> None:
    """Only the free text is lost — a suite reporting the rest is over-firing."""
    agent, _ = talk(ALLERGY_THEN_POLICY)
    booking = agent.store.booking("TM-2001")
    assert (booking.party_size, booking.date, booking.time) == (2, "friday", "7pm")


def test_a_policy_detour_with_nothing_to_lose_loses_nothing() -> None:
    agent, _ = talk(
        [
            "A table for four on Thursday at 7pm please.",
            "Is there parking?",
            "Yes, go ahead.",
            "Cadwell.",
        ]
    )
    booking = agent.store.booking("TM-2001")
    assert (booking.party_size, booking.date, booking.time) == (4, "thursday", "7pm")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def _comparable(trace) -> list[tuple]:
    """One trace, with only the session's random identity removed."""
    return [
        (
            event.ts,
            event.kind,
            event.actor,
            {k: v for k, v in event.payload.items() if k != "session_id"},
        )
        for event in trace.events
    ]


@pytest.mark.parametrize(
    "lines", [PARTY_OF_SIX, BOOK_THEN_MOVE, ALLERGY_THEN_POLICY, ALLERGY_ONLY]
)
def test_the_same_conversation_produces_the_same_trace(lines: list[str]) -> None:
    """Byte-identical, timestamps included. This is what makes a fixture one."""
    first, _ = drive("determinism", lines)
    second, _ = drive("determinism", lines)
    assert _comparable(first) == _comparable(second)


@pytest.mark.parametrize(
    "scenario, lines, tools",
    [
        ("six", PARTY_OF_SIX, {"search_tables"}),
        ("five", PARTY_OF_FIVE, {"search_tables", "create_booking"}),
        ("detour", ALLERGY_THEN_POLICY, {"search_tables", "check_policy", "create_booking"}),
    ],
)
def test_pass_k_reports_stable_never_flaky(
    scenario: str, lines: list[str], tools: set[str]
) -> None:
    """A FLAKY verdict on a seeded row would be a harness bug, not a TableMate one."""
    verdict = run_pass_k(
        scenario_id=scenario,
        k=5,
        run=lambda _index: drive(scenario, lines)[0],
        evaluate=lambda trace: set(trace.tool_names()) == tools,
    )
    assert verdict.verdict == "STABLE_PASS"
    assert verdict.passed is True
    assert verdict.total_runs == 5


# --------------------------------------------------------------------------- #
# The model backend exhibits the same defects
# --------------------------------------------------------------------------- #


def _shouty(system: str, line: str) -> str:
    """A stand-in for a provider: rewrites the words, invents no facts."""
    return line.replace("That is", "So that's").replace("I am sorry", "Apologies")


@pytest.mark.parametrize(
    "lines, expected",
    [
        (PARTY_OF_SIX, ["search_tables"]),
        (PARTY_OF_FIVE, ["search_tables", "create_booking"]),
        (BOOK_THEN_MOVE, ["search_tables", "create_booking", "modify_booking"]),
        (ALLERGY_THEN_POLICY, ["search_tables", "check_policy", "create_booking"]),
    ],
)
def test_rephrasing_the_turn_changes_no_decision(
    tmp_path, lines: list[str], expected: list[str]
) -> None:
    """The claim that makes the LLM backend a measurement rather than noise."""
    scripted, _ = talk(lines, backend=ScriptedBackend())
    rephrased, _ = talk(
        lines,
        backend=LLMBackend(cassette=tmp_path / "phrasing.json", completion=_shouty),
    )
    assert scripted.tool_names() == expected
    assert rephrased.tool_names() == expected
    assert [
        (c.name, c.args) for c in scripted.toolbox.calls
    ] == [(c.name, c.args) for c in rephrased.toolbox.calls]


def test_the_phantom_confirmation_survives_rephrasing(tmp_path) -> None:
    agent, turns = talk(
        PARTY_OF_SIX,
        backend=LLMBackend(cassette=tmp_path / "phrasing.json", completion=_shouty),
    )
    assert "booked in" in spoken(turns)
    assert "create_booking" not in agent.tool_names()


# --------------------------------------------------------------------------- #
# What the eval suite makes of it, from the outside
# --------------------------------------------------------------------------- #

ALLERGY = TrackedField(
    name="dietary", value="peanut", supply_patterns=(r"(?i)\bpeanut", r"(?i)\ballerg")
)


def test_the_suite_finds_bug_1_as_an_unbacked_promise() -> None:
    trace, _ = drive("edge-large-party-of-six", PARTY_OF_SIX)
    report = ContractSet(
        "six",
        [
            PromiseContract(),
            ToolContract(
                name="tools",
                expected=("create_booking",),
                min_calls={"create_booking": 1},
            ),
        ],
    ).run(trace)
    promise = report["promise-kept"]
    assert promise.passed is False
    assert "create_booking" in promise.detail or any(
        "create_booking" in e.note for e in promise.evidence
    )
    assert "booked in" in promise.render()
    assert report["tools"].passed is False


def test_the_suite_finds_bug_2_and_names_the_sub_agent() -> None:
    trace, _ = drive("edge-modification-after-booking", BOOK_THEN_MOVE)
    report = ContractSet(
        "move",
        [
            NoReAskContract(
                fields=(
                    TrackedField(name="party_size", value="4"),
                    TrackedField(name="date", value="Wednesday"),
                )
            ),
            ToolContract(
                name="tools",
                expected=("create_booking", "modify_booking"),
                min_calls={"create_booking": 1, "modify_booking": 1},
            ),
        ],
    ).run(trace)
    re_ask = report["no-re-ask"]
    assert re_ask.passed is False
    assert "party_size" in re_ask.detail
    rendered = re_ask.render()
    assert "ModificationAgent" in rendered
    assert "how many" in rendered.lower()
    assert report["tools"].passed is True, "the amendment itself is applied correctly"


def test_the_suite_finds_bug_3_across_the_handoff_and_clears_the_control() -> None:
    propagation = FieldPropagationContract(
        name="propagation:allergy",
        tool="create_booking",
        arg="notes",
        require_handoff=True,
        tracked=ALLERGY,
    )
    detour, _ = drive("edge-dietary-then-policy-detour", ALLERGY_THEN_POLICY)
    failing = propagation.check(detour)
    assert failing.passed is False
    assert "PolicyAgent" in failing.render()

    control, _ = drive("happy-dietary-note-single-agent", ALLERGY_ONLY)
    passing = FieldPropagationContract(
        name="propagation:allergy",
        tool="create_booking",
        arg="notes",
        require_handoff=False,
        tracked=ALLERGY,
    ).check(control)
    assert passing.passed is True


def test_one_defect_produces_one_finding() -> None:
    """Contracts must not double-count: a report of three findings for one bug is
    three conversations with the engineer who has to triage it."""
    trace, _ = drive("edge-large-party-of-six", PARTY_OF_SIX)
    report = ContractSet(
        "six",
        [
            PromiseContract(),
            FieldPropagationContract(
                name="propagation:allergy",
                tool="create_booking",
                arg="notes",
                require_handoff=False,
                tracked=ALLERGY,
            ),
            NoReAskContract(fields=(TrackedField(name="party_size", value="6"),)),
        ],
    ).run(trace)
    assert [f.name for f in report.failures()] == ["promise-kept"]
    assert report["propagation:allergy"].applicable is False


def test_a_clean_call_is_reported_clean() -> None:
    """The suite has to be able to say nothing is wrong, or its findings are noise."""
    trace, _ = drive("happy-party-of-five-boundary", PARTY_OF_FIVE)
    report = ContractSet(
        "five",
        [
            PromiseContract(),
            ToolContract(
                name="tools",
                expected=("create_booking",),
                min_calls={"create_booking": 1},
            ),
            NoReAskContract(
                fields=(
                    TrackedField(name="party_size", value="5"),
                    TrackedField(name="date", value="Friday"),
                    TrackedField(name="time", value="8pm"),
                )
            ),
        ],
    ).run(trace)
    assert report.ok, report.render()
