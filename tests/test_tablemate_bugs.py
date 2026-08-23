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
from tablemate.agents import (
    BOOKING,
    GREETER,
    LARGE_PARTY_THRESHOLD,
    MODIFICATION,
    POLICY,
    RECORD_FIELDS,
)
from tablemate.runtime import (
    LIVE_BRIEFS,
    LIVE_PROMPTS,
    LLMBackend,
    PhrasingBackend,
    ScriptedBackend,
    build_agent,
)

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
        backend=PhrasingBackend(cassette=tmp_path / "phrasing.json", completion=_shouty),
    )
    assert scripted.tool_names() == expected
    assert rephrased.tool_names() == expected
    assert [
        (c.name, c.args) for c in scripted.toolbox.calls
    ] == [(c.name, c.args) for c in rephrased.toolbox.calls]


def test_the_phantom_confirmation_survives_rephrasing(tmp_path) -> None:
    agent, turns = talk(
        PARTY_OF_SIX,
        backend=PhrasingBackend(cassette=tmp_path / "phrasing.json", completion=_shouty),
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


# --------------------------------------------------------------------------- #
# The same three defects, with a model in the decision seat
# --------------------------------------------------------------------------- #
#
# Under `ScriptedBackend` all three defects are code paths and fire on every run.
# Under `LLMBackend` there is no branch to take: the model reads a prompt and a
# brief and decides for itself, so each defect becomes *probable* rather than
# certain. That change is the honest cost of putting a model in the decision seat
# and it is stated as a measured rate in `tablemate/SEEDED_BUGS.md`.
#
# What can still be asserted deterministically, offline, is the two things that
# make the rate what it is:
#
#   1.  **The mechanism is present.** The prompt really does hand a group booking
#       to the events team without a tool; the amendment desk's brief really does
#       omit the head count its instructions tell it to establish; the policy
#       desk's brief really has no field a dietary note could travel in, and the
#       projection really is destructive.
#   2.  **The recorded live run really did exhibit it.** `fixtures/live_sessions.json`
#       is a real conversation with a real model, and replaying it needs no key.
#
# The first is a statement about the design. The second is a statement about the
# world. Neither is a substitute for the other.

LIVE_CASSETTE = "fixtures/live_sessions.json"


def _live_asks(lines: list[str], *messages: dict) -> list[dict]:
    """Drive the live engine over a stand-in and return what the model was told."""
    from tests.test_tablemate_runtime import Replies

    replies = Replies(*messages)
    backend = LLMBackend(cassette="/nonexistent-cassette.json", completion=replies)
    agent = build_agent(clock=FakeClock(), backend=backend)
    for line in lines:
        agent(line)
    return replies.asks


def test_the_live_briefs_omit_exactly_the_three_documented_fields() -> None:
    """The "no fourth bug" clause of the answer key, as an assertion.

    Every narrowing in `LIVE_BRIEFS` is a place information can be lost, so the
    set of them is the set of reachable information-loss defects. Three
    omissions, each documented; a fourth appearing here without a line in
    SEEDED_BUGS.md is an undeclared defect, which is exactly what this repository
    argues an eval suite must never have.
    """
    omissions = {
        (desk, field)
        for desk, inbound in LIVE_BRIEFS.items()
        for field in RECORD_FIELDS
        if field not in inbound
    }
    assert omissions == {
        # BUG-2: the amendment desk is told to establish a head count and is not
        # given the one the booking desk already has.
        (MODIFICATION, "party_size"),
        # BUG-3: the policy desk has nowhere to put a dietary requirement, and
        # the projection is destructive, so it is gone for good.
        (POLICY, "dietary"),
        (POLICY, "notes"),
    }


def test_the_group_paragraph_hands_the_paperwork_over_and_names_no_tool() -> None:
    """BUG-1's mechanism: a prompt, not a branch.

    The small-party procedure is numbered and ends in a tool call. The group
    procedure is a paragraph of things to say, and it accounts for its own
    absence of a reference — "the events team sends the paperwork out" — which is
    what makes a model close the call verbally and feel it has finished the job.
    Reviewing the prompt, the group path reads *fuller* than the one above it.
    """
    prompt = LIVE_PROMPTS[BOOKING]
    small, _, group = prompt.partition("A party of six or more")
    assert "create_booking" in small
    assert "never tell a caller a table is booked" in small.lower()
    assert "create_booking" not in group
    assert "events team" in group
    # And the threshold is the same one the deterministic build uses, so the
    # boundary pair (five books, six does not) means the same thing either way.
    assert str(LARGE_PARTY_THRESHOLD) not in group  # spelled as a word
    assert "six or more" in prompt


def test_the_amendment_desk_is_told_to_establish_what_it_was_not_given() -> None:
    """BUG-2's mechanism: two sources of truth for one fact, and the brief is not it."""
    assert "party_size" not in LIVE_BRIEFS[MODIFICATION]
    assert "head count" in LIVE_PROMPTS[MODIFICATION]

    asks = _live_asks(
        ["I need to move my booking TM-1042 to half seven — there are four of us."],
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "transfer_to_amendment_desk",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {"role": "assistant", "content": "How many people will be dining?"},
    )
    amendment = asks[-1]["system"]
    assert "amend and cancel existing bookings" in amendment
    assert "booking reference: TM-1042" in amendment
    assert "party size" not in amendment, "the brief must not carry the head count"


def test_the_policy_desk_narrows_the_record_and_the_note_does_not_come_back() -> None:
    """BUG-3's mechanism: a destructive projection on the path back to the desk that needs it."""
    assert "dietary" not in LIVE_BRIEFS[POLICY]
    assert "notes" not in LIVE_BRIEFS[POLICY]

    def transfer(name: str) -> dict:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        }
    asks = _live_asks(
        [
            "A table for two on Friday at 7pm — one of us has a severe peanut allergy.",
            "Before that, is there parking nearby?",
        ],
        transfer("transfer_to_booking_desk"),
        {"role": "assistant", "content": "Noted. And your name?"},
        transfer("transfer_to_policy_desk"),
        {"role": "assistant", "content": "There is a car park two doors down."},
    )
    booking_brief = asks[1]["system"]
    policy_brief = asks[-1]["system"]
    assert "peanut" in booking_brief, "the booking desk was told, so it can be lost"
    assert "peanut" not in policy_brief, "the policy desk's brief has no room for it"


def test_the_booking_desk_alone_loses_nothing_which_is_why_the_control_is_green() -> None:
    """`happy-dietary-note-single-agent`'s mechanism: no boundary, no loss."""
    assert LIVE_BRIEFS[BOOKING] == RECORD_FIELDS
    assert LIVE_BRIEFS[GREETER] == RECORD_FIELDS


# ------------------------------------------- what the recorded live run showed
#
# Every test below replays `fixtures/live_sessions.json` — a real conversation
# with a real model, driven again offline with no key. Two paths, and both are
# needed: `replay()` re-drives the *engine* (prompts, tool loop, projection,
# handoff translation) from the recorded model answers, while `--score` reads the
# committed traces. The first proves the code still behaves as it did; the second
# is the evidence behind every rate in SEEDED_BUGS.md.


def test_the_recorded_live_run_shows_the_phantom_confirmation(monkeypatch) -> None:
    """BUG-1, from a real model: told the room is theirs, no create_booking."""
    from tablemate.__main__ import bug_1_signal, replay

    monkeypatch.chdir(_repo_root())
    trace = replay("edge-large-party-of-six")
    scenario = _scenario("edge-large-party-of-six")
    signal = bug_1_signal(trace, scenario)
    assert signal.applicable and signal.fired, signal
    assert "create_booking" not in trace.tool_names()
    # And nothing errored: the transcript reads as a competent, courteous call.
    assert all(
        e.payload.get("ok", True) for e in trace.events if e.kind == "tool_result"
    )


def test_the_recorded_live_run_shows_the_dietary_note_lost_at_the_boundary(
    monkeypatch,
) -> None:
    """BUG-3, from a real model: the coeliac note does not reach the booking."""
    from tablemate.__main__ import bug_3_signal, replay

    monkeypatch.chdir(_repo_root())
    trace = replay("edge-coeliac-then-menu-policy")
    signal = bug_3_signal(trace, _scenario("edge-coeliac-then-menu-policy"))
    assert signal.applicable and signal.fired, signal
    assert "notes=''" in (signal.evidence or "")


def test_the_recorded_live_run_shows_the_amendment_desk_re_asking(monkeypatch) -> None:
    """BUG-2, from a real model — and only in one of the three repeats.

    The repeat index is load-bearing here in a way it never is under
    `ScriptedBackend`: repeats 0 and 1 of this row never reached the amendment
    desk at all, so the defect was not merely absent but *unreachable*. This test
    asserts the one repeat where it fired; `--score fixtures/live_run` reports the
    rate over all three, which is the number SEEDED_BUGS.md quotes.
    """
    from tablemate.__main__ import bug_2_signal
    from lab.trace.io import read_jsonl

    root = _repo_root()
    scenario = _scenario("edge-modification-after-booking")
    signals = [
        bug_2_signal(
            read_jsonl(
                root / "fixtures/live_run/traces"
                / f"edge-modification-after-booking-{index}.jsonl"
            ),
            scenario,
        )
        for index in range(3)
    ]
    fired = [s for s in signals if s.fired]
    assert len(fired) == 1, [s.evidence for s in signals]
    assert "how many people are coming" in (fired[0].evidence or "")
    assert sum(1 for s in signals if not s.applicable) == 2


def test_the_live_run_committed_its_evidence_and_it_still_scores(monkeypatch) -> None:
    """The audit path: the quoted rates are recomputable with no model at all."""
    from tablemate.__main__ import _rates, bugs_for, controls_for, score
    from lab.cli import evaluate_trace
    from lab.trace.io import read_jsonl

    root = _repo_root()
    paths = sorted((root / "fixtures/live_run/traces").glob("*.jsonl"))
    assert len(paths) == 30, "ten scenarios at k=3"
    rows = []
    for path in paths:
        trace = read_jsonl(path)
        scenario = _scenario(trace.scenario_id)
        row = score(scenario, trace, repeat=int(path.stem[-1]), evaluate=evaluate_trace)
        row["reaches"] = bugs_for(scenario.id)
        row["controls"] = controls_for(scenario.id)
        rows.append(row)

    rates = _rates(rows)
    # The numbers SEEDED_BUGS.md quotes. If a detector is retuned these move, and
    # the prose has to move with them — which is the reason this is a test.
    assert (rates["BUG-1"]["fired"], rates["BUG-1"]["applicable"]) == (5, 6)
    assert (rates["BUG-2"]["fired"], rates["BUG-2"]["applicable"]) == (1, 4)
    assert (rates["BUG-3"]["fired"], rates["BUG-3"]["applicable"]) == (1, 1)
    # And the emergent finding: an unbacked promise from a desk BUG-1 does not
    # cover. Declared here so that it cannot quietly disappear.
    emergent = [r["emergent"] for r in rows if r["emergent"]]
    assert len(emergent) == 2
    assert all("ModificationAgent" in line for line in emergent)


def _repo_root():
    import pathlib

    return pathlib.Path(__file__).resolve().parents[1]


def _scenario(scenario_id: str):
    from scenarios.loader import load_corpus

    return next(s for s in load_corpus().scenarios if s.id == scenario_id)
