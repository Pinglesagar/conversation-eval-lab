"""Tests for the contract language — every contract, in both directions.

WHAT THIS DEMONSTRATES
----------------------
The discipline that makes an eval suite worth running: **each contract is tested
twice, once on a trace that should fail it and once on a trace that should not.**

A check that never fires is decoration. A check that fires on healthy traces is
worse than decoration — it trains its owners to ignore the report, and a suite
nobody reads is a suite that is not protecting anything. So for every contract
below there is a matched pair of traces, and the negative case is written to be
genuinely tempting: the read-back confirmation that a naive re-ask detector
flags, the legitimate repeated question that a naive loop counter flags, the
future-tense "I'll book that now" that a naive phrase search calls a broken
promise.

Traces are hand-built on a `FakeClock`, so every timestamp in every assertion is
exact and the tests are hermetic — no agent, no model, no network. The three
seeded bugs in the system under test each get a dedicated pair, marked below, so
that the contract and the bug it exists to catch are documented together.
"""

from __future__ import annotations

import pytest

from lab.checks import (
    ArgPredicate,
    FieldPropagationContract,
    NoProgressContract,
    NoReAskContract,
    Ordering,
    PhraseContract,
    Promise,
    PromiseContract,
    ToolContract,
    TrackedField,
)
from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace


# --------------------------------------------------------------------------- #
# Trace-building helpers
# --------------------------------------------------------------------------- #


class Conversation:
    """Tiny fluent builder: every step advances the fake clock by one second.

    Keeps the traces in this file readable as dialogue, which matters — a test
    whose fixture is unreadable cannot document the behaviour it pins down.
    """

    def __init__(self, scenario_id: str = "sc") -> None:
        self.clock = FakeClock()
        self.builder = TraceBuilder(
            scenario_id=scenario_id, adapter="test", session_id="sess", clock=self.clock
        )
        self.builder.session_start()

    def _tick(self) -> None:
        self.clock.advance(1.0)

    def caller(self, text: str) -> Conversation:
        self._tick()
        self.builder.caller_utterance(text)
        return self

    def agent(self, text: str, who: str = "BookingAgent") -> Conversation:
        self._tick()
        self.builder.agent_utterance(text, agent=who)
        return self

    # `tool` is positional-only so that a tool argument genuinely called `name`
    # (create_booking has one) lands in **args instead of colliding with the
    # method signature.
    def calls(self, tool: str, /, **args: object) -> Conversation:
        self._tick()
        self.builder.tool_call(tool, dict(args))
        return self

    def returns(self, tool: str, /, *, ok: bool = True, **result: object) -> Conversation:
        self._tick()
        self.builder.tool_result(tool, dict(result), ok=ok)
        return self

    def handoff(self, frm: str, to: str, reason: str = "routing") -> Conversation:
        self._tick()
        self.builder.agent_handoff(frm, to, reason=reason)
        return self

    def done(self) -> Trace:
        self._tick()
        self.builder.session_end()
        return self.builder.build()


# --------------------------------------------------------------------------- #
# ToolContract
# --------------------------------------------------------------------------- #


def test_expected_tool_present_passes() -> None:
    trace = Conversation().calls("create_booking", name="Sam").done()
    result = ToolContract(expected=("create_booking",)).check(trace)
    assert result.passed
    assert result.detail.startswith("1/1 tool clauses satisfied")


def test_expected_tool_missing_fails_and_names_what_was_called() -> None:
    trace = Conversation().calls("search_tables", party_size=2).done()
    result = ToolContract(expected=("create_booking",)).check(trace)
    assert not result.passed
    assert "never called" in result.detail
    assert "search_tables" in result.evidence[0].note or ""


def test_or_group_is_satisfied_by_either_alternative() -> None:
    """Contracts constrain the outcome, not which acceptable implementation
    produced it — otherwise the suite is rewritten every time the agent is."""
    for tool in ("create_booking", "hold_table"):
        trace = Conversation().calls(tool).done()
        assert ToolContract(expected=("create_booking|hold_table",)).check(trace).passed


def test_or_group_fails_when_no_alternative_appears() -> None:
    trace = Conversation().calls("check_policy", topic="dogs").done()
    result = ToolContract(expected=("create_booking|hold_table",)).check(trace)
    assert not result.passed
    assert "create_booking or hold_table" in result.detail


def test_forbidden_tool_fires_only_when_called() -> None:
    clean = Conversation().calls("search_tables").done()
    dirty = Conversation().calls("cancel_booking", booking_ref="AB1").done()
    contract = ToolContract(forbidden=("cancel_booking",))
    assert contract.check(clean).passed
    assert not contract.check(dirty).passed


def test_max_calls_catches_a_retry_storm_but_allows_the_budget() -> None:
    """An agent that searched eleven times found availability and also burned
    the caller's patience; the count is the finding."""
    within = Conversation().calls("search_tables").calls("search_tables").done()
    over = Conversation()
    for _ in range(4):
        over.calls("search_tables")
    contract = ToolContract(max_calls={"search_tables": 3})
    assert contract.check(within).passed
    result = contract.check(over.done())
    assert not result.passed
    assert "called 4x, maximum 3" in result.detail


def test_min_calls_fails_with_the_observed_count() -> None:
    trace = Conversation().calls("search_tables").done()
    result = ToolContract(min_calls={"search_tables": 2}).check(trace)
    assert not result.passed
    assert "called 1x, minimum 2" in result.detail


def test_ordering_holds_across_turns() -> None:
    """Search-then-book legitimately spans turns, so ordering is session-scoped."""
    trace = (
        Conversation()
        .calls("search_tables", party_size=2)
        .agent("Eight is free. Shall I book it?")
        .caller("Yes please.")
        .calls("create_booking", party_size=2)
        .done()
    )
    contract = ToolContract(ordering=(Ordering("search_tables", "create_booking"),))
    assert contract.check(trace).passed


def test_ordering_fails_when_the_sequence_is_inverted() -> None:
    trace = (
        Conversation().calls("create_booking", party_size=2).calls("search_tables").done()
    )
    result = ToolContract(ordering=(Ordering("search_tables", "create_booking"),)).check(trace)
    assert not result.passed
    assert "precedes first search_tables" in result.detail


def test_ordering_is_vacuous_when_the_second_tool_never_runs() -> None:
    """No `create_booking` means no ordering claim was tested — and the missing
    call is `expected`'s finding, not this rule's."""
    trace = Conversation().calls("search_tables").done()
    result = ToolContract(ordering=(Ordering("search_tables", "create_booking"),)).check(trace)
    assert result.passed
    assert not result.applicable


def test_strict_ordering_requires_a_predecessor_for_every_call() -> None:
    trace = (
        Conversation()
        .calls("search_tables")
        .calls("create_booking", party_size=2)
        .calls("create_booking", party_size=4)
        .done()
    )
    lenient = ToolContract(ordering=(Ordering("search_tables", "create_booking"),))
    strict = ToolContract(ordering=(Ordering("search_tables", "create_booking", strict=True),))
    assert lenient.check(trace).passed
    # Both bookings do follow the single search in time, so strict passes too.
    assert strict.check(trace).passed


def test_strict_ordering_fails_a_call_with_no_preceding_search() -> None:
    trace = (
        Conversation()
        .calls("create_booking", party_size=2)
        .calls("search_tables")
        .calls("create_booking", party_size=4)
        .done()
    )
    result = ToolContract(
        ordering=(Ordering("search_tables", "create_booking", strict=True),)
    ).check(trace)
    assert not result.passed
    assert "1/2 create_booking calls had no preceding search_tables" in result.detail


def test_arg_predicate_compares_against_scenario_context() -> None:
    good = Conversation().calls("create_booking", party_size=6).done()
    bad = Conversation().calls("create_booking", party_size=2).done()
    contract = ToolContract(args=(ArgPredicate("create_booking", "party_size", ref="party_size"),))
    assert contract.check(good, {"party_size": 6}).passed
    assert not contract.check(bad, {"party_size": 6}).passed


def test_arg_predicate_bridges_notations() -> None:
    """The agent passing the string "six" is not a bug; the contract says so."""
    trace = Conversation().calls("create_booking", party_size="six").done()
    contract = ToolContract(args=(ArgPredicate("create_booking", "party_size", ref="party_size"),))
    assert contract.check(trace, {"party_size": 6}).passed


def test_arg_predicate_contains_checks_a_free_text_field() -> None:
    carried = Conversation().calls("create_booking", notes="severe nut allergy").done()
    dropped = Conversation().calls("create_booking", notes="").done()
    contract = ToolContract(
        args=(ArgPredicate("create_booking", "notes", op="contains", value="nut allergy"),)
    )
    assert contract.check(carried).passed
    assert not contract.check(dropped).passed


def test_arg_predicate_is_vacuous_when_the_tool_never_ran() -> None:
    """Absence of the call is one bug, reported once, by `expected` — not
    re-reported here as a second finding."""
    trace = Conversation().calls("search_tables").done()
    result = ToolContract(
        args=(ArgPredicate("create_booking", "party_size", ref="party_size"),)
    ).check(trace, {"party_size": 6})
    assert result.passed
    assert not result.applicable
    assert "never called" in result.detail


def test_arg_predicate_is_vacuous_when_the_scenario_never_specified_the_value() -> None:
    trace = Conversation().calls("create_booking", party_size=2).done()
    result = ToolContract(
        args=(ArgPredicate("create_booking", "party_size", ref="party_size"),)
    ).check(trace, context={})
    assert result.passed
    assert not result.applicable


def test_missing_argument_fails_rather_than_being_skipped() -> None:
    trace = Conversation().calls("create_booking", name="Sam").done()
    result = ToolContract(
        args=(ArgPredicate("create_booking", "party_size", ref="party_size"),)
    ).check(trace, {"party_size": 6})
    assert not result.passed


def test_quantifier_all_requires_every_call_to_comply() -> None:
    trace = (
        Conversation()
        .calls("create_booking", party_size=6)
        .calls("create_booking", party_size=2)
        .done()
    )
    any_ok = ToolContract(
        args=(ArgPredicate("create_booking", "party_size", ref="party_size"),)
    )
    all_ok = ToolContract(
        args=(
            ArgPredicate("create_booking", "party_size", ref="party_size", quantifier="all"),
        )
    )
    assert any_ok.check(trace, {"party_size": 6}).passed
    assert not all_ok.check(trace, {"party_size": 6}).passed


def test_numeric_comparison_operators() -> None:
    trace = Conversation().calls("create_booking", party_size=6).done()
    assert ToolContract(
        args=(ArgPredicate("create_booking", "party_size", op="gte", value=6),)
    ).check(trace).passed
    assert not ToolContract(
        args=(ArgPredicate("create_booking", "party_size", op="lt", value=6),)
    ).check(trace).passed


def test_in_operator_accepts_an_allowed_set() -> None:
    trace = Conversation().calls("create_booking", time="7pm").done()
    assert ToolContract(
        args=(ArgPredicate("create_booking", "time", op="in", value=("6pm", "7pm", "8pm")),)
    ).check(trace).passed


def test_unknown_operator_raises_rather_than_passing_silently() -> None:
    """A typo in a contract must not become a green check."""
    trace = Conversation().calls("create_booking", party_size=6).done()
    with pytest.raises(ValueError, match="unknown op"):
        ToolContract(args=(ArgPredicate("create_booking", "party_size", op="equals", value=6),)).check(
            trace
        )


def test_a_contract_with_no_clauses_is_vacuous_not_passing() -> None:
    trace = Conversation().calls("create_booking").done()
    result = ToolContract().check(trace)
    assert result.passed
    assert not result.applicable
    assert result.status == "VACUOUS"


# --------------------------------------------------------------------------- #
# PromiseContract  --  catches BUG-1 (phantom confirmation)
# --------------------------------------------------------------------------- #


def _phantom_confirmation() -> Trace:
    """BUG-1: for a party of six the agent claims success and never books."""
    return (
        Conversation("large-party")
        .caller("Hi, I'd like a table for six on Friday at 7pm.")
        .calls("search_tables", date="Friday", time="7pm", party_size=6)
        .returns("search_tables", available=True)
        .agent("Great news, your table for six on Friday at 7pm is confirmed.")
        .done()
    )


def _honest_confirmation() -> Trace:
    """The same conversation, with the booking actually made."""
    return (
        Conversation("small-party")
        .caller("Hi, I'd like a table for two on Friday at 7pm.")
        .calls("search_tables", date="Friday", time="7pm", party_size=2)
        .returns("search_tables", available=True)
        .calls("create_booking", name="Sam", date="Friday", time="7pm", party_size=2)
        .returns("create_booking", ref="AB12")
        .agent("Great news, your table for two on Friday at 7pm is confirmed.")
        .done()
    )


def test_promise_contract_catches_the_phantom_confirmation() -> None:
    """BUG-1. The transcript reads perfectly and no tool errored — the failure
    exists only in the gap between the two channels."""
    result = PromiseContract().check(_phantom_confirmation())
    assert not result.passed
    assert "0/1 spoken commitments backed" in result.detail
    quoted = " ".join(e.quote for e in result.evidence)
    assert "is confirmed" in quoted
    assert any("no create_booking" in (e.note or "") for e in result.evidence)


def test_promise_contract_is_silent_when_the_booking_really_happened() -> None:
    result = PromiseContract().check(_honest_confirmation())
    assert result.passed
    assert result.applicable
    assert result.detail.startswith("1/1 spoken commitments")


def test_promise_contract_is_vacuous_when_nothing_was_claimed() -> None:
    """No claim, no verdict — and the report says so rather than counting a pass."""
    trace = (
        Conversation()
        .caller("Do you take dogs?")
        .calls("check_policy", topic="dogs")
        .agent("Yes, dogs are welcome in the courtyard.")
        .done()
    )
    result = PromiseContract().check(trace)
    assert result.passed
    assert not result.applicable
    assert result.status == "VACUOUS"


@pytest.mark.parametrize(
    "line",
    [
        "I'll go ahead and book that for you now.",
        "Shall I confirm that booking?",
        "Would you like me to book it?",
        "Let me get that booked for you.",
        "Once I've booked it you'll get a text message.",
        "I'm afraid that isn't confirmed yet.",
        "Your booking is not confirmed, I'm afraid.",
        "I was unable to book that table.",
        "Can I confirm the booking for you?",
    ],
)
def test_promise_contract_ignores_intent_questions_and_negation(line: str) -> None:
    """The false-positive suite. Every one of these is a *correct* thing to say
    with no booking in the trace, and a naive phrase search flags them all."""
    trace = Conversation().caller("Table for two please.").agent(line).done()
    result = PromiseContract().check(trace)
    assert result.passed, f"false positive on: {line}"


@pytest.mark.parametrize(
    "line",
    [
        "Your table for two is confirmed.",
        "You're all set for Friday at 8.",
        "I've booked that for you.",
        "That's all booked.",
        "I've put you down for Friday at 8pm.",
        "Your reservation has been confirmed.",
        "You're all booked in, can I help with anything else?",
    ],
)
def test_promise_contract_catches_every_way_of_claiming_success(line: str) -> None:
    """The recall suite, including the comma-welded case: the sentence ends in a
    question mark and still contains a firm false claim."""
    trace = Conversation().caller("Table for two please.").agent(line).done()
    result = PromiseContract().check(trace)
    assert not result.passed, f"missed claim: {line}"


def test_promise_contract_covers_cancellation_and_modification() -> None:
    cancelled = Conversation().agent("That's cancelled for you.").done()
    modified = Conversation().agent("I've moved you to 9pm.").done()
    assert not PromiseContract().check(cancelled).passed
    assert not PromiseContract().check(modified).passed

    honest_cancel = (
        Conversation()
        .calls("cancel_booking", booking_ref="AB12", reason="caller request")
        .agent("That's cancelled for you.")
        .done()
    )
    assert PromiseContract().check(honest_cancel).passed


def test_a_claim_satisfied_by_a_later_call_passes_by_default() -> None:
    """Within a turn, whether text reaches TTS before the tool fires is a runtime
    detail. The default contract targets total absence, not ordering."""
    trace = (
        Conversation()
        .agent("Your table is confirmed.")
        .calls("create_booking", party_size=2)
        .done()
    )
    assert PromiseContract().check(trace).passed


def test_strict_mode_requires_the_deed_before_the_claim() -> None:
    trace = (
        Conversation()
        .agent("Your table is confirmed.")
        .calls("create_booking", party_size=2)
        .done()
    )
    result = PromiseContract(require_before_utterance=True).check(trace)
    assert not result.passed
    assert "before the claim" in " ".join(e.note or "" for e in result.evidence)


def test_promise_patterns_are_configurable_per_contract() -> None:
    trace = Conversation().agent("Your table is locked in.").done()
    assert PromiseContract().check(trace).passed  # not in the default vocabulary
    custom = PromiseContract(
        promises=(Promise(label="held", says=(r"\blocked in\b",), requires=("hold_table",)),)
    )
    assert not custom.check(trace).passed


def test_ignored_agents_are_exempt() -> None:
    trace = Conversation().agent("Your table is confirmed.", who="Summariser").done()
    assert PromiseContract(ignore_agents=("Summariser",)).check(trace).status == "VACUOUS"


# --------------------------------------------------------------------------- #
# NoReAskContract  --  catches BUG-2 (amnesiac handoff)
# --------------------------------------------------------------------------- #

_PARTY = (TrackedField("party_size", context_key="party_size"),)
_CTX = {"party_size": 6}


def test_no_re_ask_catches_the_amnesiac_handoff() -> None:
    """BUG-2: the receiving agent asks for what the caller already said."""
    trace = (
        Conversation("modify")
        .caller("Hi, I have a booking for six but I need to move it.")
        .agent("Sure, I can help with that.", who="GreeterAgent")
        .handoff("GreeterAgent", "ModificationAgent", reason="modify")
        .agent("Happy to help. And how many people will that be?", who="ModificationAgent")
        .done()
    )
    result = NoReAskContract(fields=_PARTY).check(trace, _CTX)
    assert not result.passed
    assert "party_size re-asked 1x" in result.detail
    notes = " ".join(e.note or "" for e in result.evidence)
    assert "ModificationAgent re-asks party_size" in notes


def test_a_read_back_confirmation_is_not_a_re_ask() -> None:
    """PITFALL 1. Confirming a value is good behaviour — it guards against
    mis-transcription. A detector that flags it gets switched off within a week."""
    trace = (
        Conversation("modify")
        .caller("Hi, I have a booking for six but I need to move it.")
        .handoff("GreeterAgent", "ModificationAgent")
        .agent("So that's a table for six, is that right?", who="ModificationAgent")
        .done()
    )
    result = NoReAskContract(fields=_PARTY).check(trace, _CTX)
    assert result.passed
    assert result.applicable


def test_scoring_is_per_sentence_not_per_turn() -> None:
    """PITFALL 2. This turn confirms the value *and* re-asks it. Turn-level
    scoring excuses the re-ask because the value appears somewhere in the turn."""
    trace = (
        Conversation()
        .caller("A table for six, please.")
        .handoff("GreeterAgent", "BookingAgent")
        .agent(
            "Six people, lovely. Sorry, how many guests did you say?",
            who="BookingAgent",
        )
        .done()
    )
    result = NoReAskContract(fields=_PARTY).check(trace, _CTX)
    assert not result.passed
    offending = [e.quote for e in result.evidence if "how many guests" in e.quote]
    assert offending, "the re-asking sentence should be quoted on its own"
    assert "Six people, lovely." not in offending[0]


def test_the_first_ask_is_never_a_re_ask() -> None:
    """Asking for information you do not have is the job."""
    trace = (
        Conversation()
        .agent("How many people will that be?")
        .caller("Six of us.")
        .calls("search_tables", party_size=6)
        .done()
    )
    assert NoReAskContract(fields=_PARTY).check(trace, _CTX).passed


def test_no_re_ask_is_vacuous_when_the_caller_never_supplied_the_field() -> None:
    trace = Conversation().caller("Do you have space tonight?").agent("How many people?").done()
    result = NoReAskContract(fields=_PARTY).check(trace, _CTX)
    assert result.passed
    assert not result.applicable
    assert "never supplied" in result.detail


def test_no_re_ask_is_vacuous_when_it_declares_no_fields() -> None:
    trace = Conversation().caller("Six please.").done()
    assert not NoReAskContract().check(trace, _CTX).applicable


def test_grace_window_forgives_a_question_already_in_flight() -> None:
    """In a voice pipeline the agent's question may have been mid-utterance when
    the caller answered. Punishing that is punishing physics."""
    trace = (
        Conversation()
        .caller("Six of us.")
        .agent("How many people will that be?")
        .done()
    )
    assert not NoReAskContract(fields=_PARTY).check(trace, _CTX).passed
    assert NoReAskContract(fields=_PARTY, grace_seconds=2.0).check(trace, _CTX).passed


def test_supply_patterns_find_a_value_the_caller_never_said_literally() -> None:
    """"Just the two of us plus my parents" supplies party_size 4 without
    containing it, and only a scenario-authored pattern can see that."""
    field = TrackedField(
        "party_size",
        context_key="party_size",
        supply_patterns=(r"\btwo of us plus my parents\b",),
    )
    trace = (
        Conversation()
        .caller("Just the two of us plus my parents.")
        .handoff("GreeterAgent", "BookingAgent")
        .agent("And how many people will that be?", who="BookingAgent")
        .done()
    )
    result = NoReAskContract(fields=(field,)).check(trace, {"party_size": 4})
    assert not result.passed


def test_custom_ask_patterns_override_the_domain_defaults() -> None:
    trace = (
        Conversation()
        .caller("Six of us.")
        .agent("Remind me of the headcount?")
        .done()
    )
    assert NoReAskContract(fields=_PARTY).check(trace, _CTX).passed  # not a default pattern
    custom = TrackedField("party_size", context_key="party_size", ask_patterns=(r"\bheadcount\b",))
    assert not NoReAskContract(fields=(custom,)).check(trace, _CTX).passed


# --------------------------------------------------------------------------- #
# FieldPropagationContract  --  catches BUG-3 (dropped dietary note)
# --------------------------------------------------------------------------- #

_DIETARY = TrackedField("dietary", value="nut allergy")


def _propagation_contract(**kwargs: object) -> FieldPropagationContract:
    defaults: dict[str, object] = {
        "name": "dietary-reaches-booking",
        "tracked": _DIETARY,
        "tool": "create_booking",
        "arg": "notes",
    }
    defaults.update(kwargs)
    return FieldPropagationContract(**defaults)  # type: ignore[arg-type]


def test_field_propagation_catches_the_dropped_dietary_note() -> None:
    """BUG-3: the note is given to one agent and lost on the way to the tool."""
    trace = (
        Conversation("dietary")
        .caller("Table for two on Saturday. One of us has a severe nut allergy.")
        .agent("Noted. Let me check our allergy policy.")
        .handoff("BookingAgent", "PolicyAgent", reason="allergy policy")
        .calls("check_policy", topic="allergies")
        .handoff("PolicyAgent", "BookingAgent", reason="resume booking")
        .calls("create_booking", name="Sam", date="Saturday", party_size=2, notes="")
        .agent("All set, your table is booked.")
        .done()
    )
    result = _propagation_contract().check(trace)
    assert not result.passed
    assert "0/1 values reached create_booking.notes" in result.detail
    kinds = [e.kind for e in result.evidence]
    assert "caller_utterance" in kinds and "agent_handoff" in kinds and "tool_call" in kinds


def test_field_propagation_passes_when_the_note_survives() -> None:
    trace = (
        Conversation("dietary")
        .caller("Table for two on Saturday. One of us has a severe nut allergy.")
        .handoff("BookingAgent", "PolicyAgent")
        .calls("check_policy", topic="allergies")
        .handoff("PolicyAgent", "BookingAgent")
        .calls("create_booking", name="Sam", party_size=2, notes="severe nut allergy at table")
        .done()
    )
    result = _propagation_contract().check(trace)
    assert result.passed
    assert result.applicable
    assert "1/1 values reached" in result.detail


def test_field_propagation_is_vacuous_when_the_tool_never_ran() -> None:
    """Nothing propagated because nothing happened — and that absence is
    PromiseContract's finding. Failing here too would report one bug as two."""
    trace = (
        Conversation()
        .caller("One of us has a severe nut allergy.")
        .handoff("BookingAgent", "PolicyAgent")
        .agent("Our kitchen can accommodate that.")
        .done()
    )
    result = _propagation_contract().check(trace)
    assert result.passed
    assert not result.applicable
    assert "never called" in result.detail


def test_field_propagation_is_vacuous_with_no_handoff_to_test() -> None:
    """The hypothesis is "the boundary lost it"; with no boundary there is no
    verdict to give, and the report shows the gap instead of a green check."""
    trace = (
        Conversation()
        .caller("One of us has a severe nut allergy.")
        .calls("create_booking", party_size=2, notes="")
        .done()
    )
    result = _propagation_contract().check(trace)
    assert result.passed
    assert not result.applicable
    assert "no handoff" in result.detail


def test_field_propagation_can_be_run_without_requiring_a_handoff() -> None:
    trace = (
        Conversation()
        .caller("One of us has a severe nut allergy.")
        .calls("create_booking", party_size=2, notes="")
        .done()
    )
    result = _propagation_contract(require_handoff=False).check(trace)
    assert not result.passed


def test_field_propagation_is_vacuous_when_the_caller_never_mentioned_it() -> None:
    trace = (
        Conversation()
        .caller("Table for two on Saturday.")
        .handoff("BookingAgent", "PolicyAgent")
        .calls("create_booking", party_size=2, notes="")
        .done()
    )
    result = _propagation_contract().check(trace)
    assert result.passed
    assert not result.applicable


def test_field_propagation_reads_its_value_from_context() -> None:
    trace = (
        Conversation()
        .caller("I need a high chair for the baby.")
        .handoff("GreeterAgent", "BookingAgent")
        .calls("create_booking", party_size=3, notes="")
        .done()
    )
    contract = _propagation_contract(tracked=TrackedField("dietary", context_key="special_request"))
    assert not contract.check(trace, {"special_request": "high chair"}).passed


# --------------------------------------------------------------------------- #
# NoProgressContract
# --------------------------------------------------------------------------- #


def test_no_progress_catches_a_question_repeated_into_a_void() -> None:
    trace = (
        Conversation()
        .agent("What time would you like?")
        .caller("Sorry, what?")
        .agent("What time would you like?")
        .done()
    )
    result = NoProgressContract().check(trace)
    assert not result.passed
    assert "1 stalled repeat" in result.detail


def test_a_repeated_question_with_a_tool_call_between_is_not_a_loop() -> None:
    """THE POINT OF THE WINDOW. Identical surface text to the test above; the
    only difference is that something happened in between. A global repeat
    counter cannot tell these apart and flags both."""
    trace = (
        Conversation()
        .agent("Anything else I can help with?")
        .caller("Yes, cancel my other booking too.")
        .calls("cancel_booking", booking_ref="ZZ9", reason="caller request")
        .returns("cancel_booking", ok=True)
        .agent("Anything else I can help with?")
        .done()
    )
    result = NoProgressContract().check(trace)
    assert result.passed
    assert result.applicable
    assert "1/1 repeat windows showed progress" in result.detail


def test_a_handoff_counts_as_progress() -> None:
    trace = (
        Conversation()
        .agent("What time would you like?", who="GreeterAgent")
        .handoff("GreeterAgent", "BookingAgent")
        .agent("What time would you like?", who="BookingAgent")
        .done()
    )
    assert NoProgressContract().check(trace).passed


def test_a_newly_captured_field_counts_as_progress() -> None:
    """Without this signal, a window where the caller finally answered but no
    tool ran would be reported as a loop."""
    trace = (
        Conversation()
        .agent("What time would you like?")
        .caller("Six of us, around eight.")
        .agent("What time would you like?")
        .done()
    )
    assert not NoProgressContract().check(trace).passed
    with_fields = NoProgressContract(fields=_PARTY)
    assert with_fields.check(trace, _CTX).passed


def test_a_reworded_repeat_is_still_a_repeat() -> None:
    """Real agents add filler on the second attempt; the detector must survive it."""
    trace = (
        Conversation()
        .agent("How many people?")
        .caller("I already told you.")
        .agent("Sorry, so how many people again?")
        .done()
    )
    assert not NoProgressContract().check(trace).passed


def test_repeated_statements_are_ignored_when_questions_only() -> None:
    trace = Conversation().agent("No problem.").caller("Hm.").agent("No problem.").done()
    result = NoProgressContract().check(trace)
    assert result.passed
    assert not result.applicable


def test_no_progress_is_vacuous_when_nothing_repeats() -> None:
    trace = Conversation().agent("What time would you like?").caller("Eight.").done()
    result = NoProgressContract().check(trace)
    assert result.passed
    assert not result.applicable
    assert "no agent question recurred" in result.detail


def test_min_repeats_below_two_is_rejected() -> None:
    trace = Conversation().agent("What time?").done()
    with pytest.raises(ValueError, match="min_repeats must be >= 2"):
        NoProgressContract(min_repeats=1).check(trace)


# --------------------------------------------------------------------------- #
# PhraseContract
# --------------------------------------------------------------------------- #


def test_required_phrase_present_and_absent() -> None:
    said = Conversation().agent("We hold tables for 15 minutes.").done()
    silent = Conversation().agent("See you Friday.").done()
    contract = PhraseContract(required=("hold tables for 15 minutes",))
    assert contract.check(said).passed
    result = contract.check(silent)
    assert not result.passed
    assert "never said" in result.detail


def test_forbidden_phrase_is_caught_and_quoted() -> None:
    trace = Conversation().agent("I can give you a 20% discount on that.").done()
    result = PhraseContract(forbidden=("discount",)).check(trace)
    assert not result.passed
    assert "20% discount" in result.evidence[0].quote


def test_phrase_matching_is_case_insensitive_by_default() -> None:
    trace = Conversation().agent("A DISCOUNT is available.").done()
    assert not PhraseContract(forbidden=("discount",)).check(trace).passed
    assert PhraseContract(forbidden=("discount",), case_sensitive=True).check(trace).passed


def test_regex_mode_matches_a_pattern() -> None:
    trace = Conversation().agent("That'll be about 45 pounds a head.").done()
    assert not PhraseContract(forbidden=(r"\d+ pounds",), regex=True).check(trace).passed


def test_caller_speech_is_not_searched_by_default() -> None:
    """The caller is simulated; constraining its script would be checking the
    harness rather than the system under test."""
    trace = Conversation().caller("Can I get a discount?").agent("Our prices are fixed.").done()
    assert PhraseContract(forbidden=("discount",)).check(trace).passed
    assert not PhraseContract(forbidden=("discount",), actor=None).check(trace).passed


def test_phrase_contract_with_no_clauses_is_vacuous() -> None:
    trace = Conversation().agent("Hello.").done()
    assert not PhraseContract().check(trace).applicable
