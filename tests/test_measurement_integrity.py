"""Regression tests for two ways a check can stop being able to fail.

WHAT THIS DEMONSTRATES
----------------------
The failure mode both halves guard against is the same one, and it is the worst
kind an eval suite has: a check that reports PASS because it can no longer see
what it was pointed at. Nothing errors, the row is green, and the green is
indistinguishable from a healthy result.

**Ordering clauses must not be decided on `ts`.** Four contract clauses ask a
"did A come before B" question. `ts` answers it correctly only while timestamps
discriminate, and they routinely do not: a `FakeClock` plus an agent that returns
without sleeping — the deterministic setup this repo recommends for tests — gives
every event in a session `ts=0.0`, and `_WindowStamper` collapses a zero-span
window onto `t0` by documented design. A `<=` on tied timestamps reads as "in
order", so on such a trace those clauses cannot fail at all. Each test below
builds a blatantly bad trace whose timestamps are entirely tied and demands a
failure; the paired good trace demands silence, because a check that fires on
tied timestamps regardless would pass the first half for the wrong reason.

**A verdict badge must not be a string somebody typed.** The run report prints
"Calibration gate: PASS" for the timing gate. Reading that word out of the
committed artefact would put the credibility of every latency figure in the
report on one editable field, so the verdict is recomputed from the samples the
artefact carries. These tests forge the artefact in the ways that matter.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lab.checks import (
    FieldPropagationContract,
    NoProgressContract,
    NoReAskContract,
    Ordering,
    PromiseContract,
    ToolContract,
    TrackedField,
)
from lab.cli import _calibration_verdict, _verdict_from_calibration_samples
from lab.clock import FakeClock
from lab.simulator import ScriptedCaller, run_scenario
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

CALIBRATION_JSON = Path(__file__).resolve().parents[1] / "fixtures" / "calibration_report.json"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _builder(clock: FakeClock) -> TraceBuilder:
    return TraceBuilder(scenario_id="integrity", adapter="text", session_id="integrity", clock=clock)


def _all_tied(trace: Trace) -> bool:
    """Every event shares one timestamp — the case `ts` comparisons cannot see."""
    return len({event.ts for event in trace.events}) == 1


# --------------------------------------------------------------------------- #
# ToolContract.ordering
# --------------------------------------------------------------------------- #


def _ordering_trace(*, advance: float, in_order: bool) -> Trace:
    clock = FakeClock()
    builder = _builder(clock)
    builder.session_start()
    builder.caller_utterance("a table for two on Friday at 7pm, please")
    names = ["search_tables", "create_booking"] if in_order else ["create_booking", "search_tables"]
    for name in names:
        builder.tool_call(name, {"date": "friday", "time": "7pm", "party_size": 2}, agent="Booking")
        builder.tool_result(name, {"ok": True})
        clock.advance(advance)
    builder.agent_utterance("You are all booked in.", agent="BookingAgent")
    builder.session_end(reason="completed")
    return builder.build()


@pytest.mark.parametrize("advance", [0.0, 0.25])
def test_ordering_fails_on_a_reversed_pair_even_with_tied_timestamps(advance: float) -> None:
    trace = _ordering_trace(advance=advance, in_order=False)
    assert _all_tied(trace) is (advance == 0.0)
    contract = ToolContract(ordering=(Ordering(first="search_tables", then="create_booking"),))
    assert not contract.check(trace).passed


@pytest.mark.parametrize("advance", [0.0, 0.25])
def test_strict_ordering_fails_on_a_reversed_pair_even_with_tied_timestamps(advance: float) -> None:
    trace = _ordering_trace(advance=advance, in_order=False)
    contract = ToolContract(
        ordering=(Ordering(first="search_tables", then="create_booking", strict=True),)
    )
    assert not contract.check(trace).passed


@pytest.mark.parametrize("advance", [0.0, 0.25])
def test_ordering_stays_silent_on_a_correctly_ordered_pair(advance: float) -> None:
    trace = _ordering_trace(advance=advance, in_order=True)
    for rule in (
        Ordering(first="search_tables", then="create_booking"),
        Ordering(first="search_tables", then="create_booking", strict=True),
    ):
        assert ToolContract(ordering=(rule,)).check(trace).passed


# --------------------------------------------------------------------------- #
# NoReAskContract
# --------------------------------------------------------------------------- #


def _re_ask_trace(*, advance: float, question: str) -> Trace:
    clock = FakeClock()
    builder = _builder(clock)
    builder.session_start()
    builder.caller_utterance("It is for six of us on Friday at 7pm.")
    clock.advance(advance)
    builder.agent_handoff("GreeterAgent", "ModificationAgent", reason="amendment")
    clock.advance(advance)
    builder.agent_utterance(question, agent="ModificationAgent")
    builder.session_end(reason="completed")
    return builder.build()


@pytest.mark.parametrize("advance", [0.0, 0.4])
def test_no_re_ask_fails_on_a_re_ask_even_with_tied_timestamps(advance: float) -> None:
    trace = _re_ask_trace(advance=advance, question="How many people will be dining?")
    assert _all_tied(trace) is (advance == 0.0)
    contract = NoReAskContract(fields=(TrackedField("party_size", value=6),))
    result = contract.check(trace)
    assert not result.passed
    assert "party_size re-asked" in result.detail


@pytest.mark.parametrize("advance", [0.0, 0.4])
def test_no_re_ask_stays_silent_on_a_confirmation(advance: float) -> None:
    trace = _re_ask_trace(advance=advance, question="Still six of you?")
    assert NoReAskContract(fields=(TrackedField("party_size", value=6),)).check(trace).passed


def test_no_re_ask_through_the_driver_with_an_instant_agent() -> None:
    """The same defect, reached the way a user of `lab` would reach it.

    `run_scenario` with a `FakeClock` and an agent that returns immediately is the
    documented deterministic setup, and it produces a trace with a single
    timestamp. Before ordering moved off `ts`, this exact conversation passed.
    """
    replies = iter(
        [
            "Of course — putting you through to the amendment desk.",
            "How many people will be dining?",
            "All done, thank you.",
        ]
    )
    trace = run_scenario(
        scenario_id="instant-agent",
        agent=lambda heard: next(replies),
        caller=ScriptedCaller(["It is for six of us on Friday at 7pm.", "yes please", "thanks"]),
        clock=FakeClock(),
    )
    assert _all_tied(trace), "an instant agent on a FakeClock should tie every timestamp"
    contract = NoReAskContract(fields=(TrackedField("party_size", value=6),))
    assert not contract.check(trace).passed


def test_grace_seconds_still_forgives_a_re_ask_inside_its_window() -> None:
    """`grace_seconds` is genuinely temporal, so it stays on `ts`."""
    clock = FakeClock()
    builder = _builder(clock)
    builder.session_start()
    builder.caller_utterance("It is for six of us on Friday at 7pm.")
    clock.advance(0.2)
    builder.agent_utterance("How many people will be dining?", agent="BookingAgent")
    builder.session_end(reason="completed")
    trace = builder.build()
    field = TrackedField("party_size", value=6)
    assert not NoReAskContract(fields=(field,)).check(trace).passed
    assert NoReAskContract(fields=(field,), grace_seconds=0.5).check(trace).passed


# --------------------------------------------------------------------------- #
# PromiseContract(require_before_utterance=True)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("advance", [0.0, 0.3])
def test_promise_before_utterance_fails_when_the_call_follows_the_claim(advance: float) -> None:
    clock = FakeClock()
    builder = _builder(clock)
    builder.session_start()
    builder.caller_utterance("please book it")
    clock.advance(advance)
    builder.agent_utterance("That is all booked in.", agent="BookingAgent")
    clock.advance(advance)
    builder.tool_call("create_booking", {"name": "A", "party_size": 2}, agent="BookingAgent")
    builder.tool_result("create_booking", {"ref": "TM-1"})
    builder.session_end(reason="completed")
    trace = builder.build()
    assert PromiseContract().check(trace).passed, "absence is the default contract's question"
    assert not PromiseContract(require_before_utterance=True).check(trace).passed


def test_promise_before_utterance_stays_silent_when_the_call_precedes_the_claim() -> None:
    builder = _builder(FakeClock())
    builder.session_start()
    builder.caller_utterance("please book it")
    builder.tool_call("create_booking", {"name": "A", "party_size": 2}, agent="BookingAgent")
    builder.tool_result("create_booking", {"ref": "TM-1"})
    builder.agent_utterance("That is all booked in.", agent="BookingAgent")
    builder.session_end(reason="completed")
    trace = builder.build()
    assert _all_tied(trace)
    assert PromiseContract(require_before_utterance=True).check(trace).passed


# --------------------------------------------------------------------------- #
# NoProgressContract — the tie hides progress rather than a defect
# --------------------------------------------------------------------------- #


def _loop_trace(*, advance: float, searched: bool) -> Trace:
    clock = FakeClock()
    builder = _builder(clock)
    builder.session_start()
    builder.caller_utterance("hello")
    clock.advance(advance)
    builder.agent_utterance("What time would you like?", agent="BookingAgent")
    clock.advance(advance)
    builder.caller_utterance("7pm" if searched else "erm")
    clock.advance(advance)
    if searched:
        builder.tool_call("search_tables", {"time": "7pm"}, agent="BookingAgent")
        builder.tool_result("search_tables", {"slots": []})
        clock.advance(advance)
    builder.agent_utterance("What time would you like?", agent="BookingAgent")
    builder.session_end(reason="completed")
    return builder.build()


@pytest.mark.parametrize("advance", [0.0, 0.5])
def test_no_progress_sees_the_tool_call_between_two_repeats(advance: float) -> None:
    """The tie direction that produces a *false failure*, so it is tested too."""
    trace = _loop_trace(advance=advance, searched=True)
    result = NoProgressContract(fields=(TrackedField("time", value="7pm"),)).check(trace)
    assert result.passed, result.detail


@pytest.mark.parametrize("advance", [0.0, 0.5])
def test_no_progress_still_catches_a_genuine_stall(advance: float) -> None:
    trace = _loop_trace(advance=advance, searched=False)
    assert not NoProgressContract(fields=(TrackedField("time", value="7pm"),)).check(trace).passed


# --------------------------------------------------------------------------- #
# FieldPropagationContract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("advance", [0.0, 0.3])
@pytest.mark.parametrize("notes", ["", "severe nut allergy"])
def test_field_propagation_reads_the_same_verdict_with_or_without_ties(
    advance: float, notes: str
) -> None:
    clock = FakeClock()
    builder = _builder(clock)
    builder.session_start()
    builder.caller_utterance("Table for two on Friday at 7pm. One of us has a severe nut allergy.")
    clock.advance(advance)
    builder.agent_handoff("BookingAgent", "PolicyAgent", reason="allergy question")
    clock.advance(advance)
    builder.agent_handoff("PolicyAgent", "BookingAgent", reason="back to booking")
    clock.advance(advance)
    builder.tool_call("create_booking", {"name": "A", "notes": notes}, agent="BookingAgent")
    builder.tool_result("create_booking", {"ref": "TM-1"})
    builder.agent_utterance("All booked in.", agent="BookingAgent")
    builder.session_end(reason="completed")
    contract = FieldPropagationContract(
        name="propagation:nut",
        tracked=TrackedField("dietary_note", value="nut"),
        tool="create_booking",
        arg="notes",
    )
    result = contract.check(builder.build())
    assert result.applicable, "two handoffs sit between the supply and the call"
    assert result.passed is bool(notes)


# --------------------------------------------------------------------------- #
# The calibration badge
# --------------------------------------------------------------------------- #


@pytest.fixture()
def genuine() -> dict[str, Any]:
    return json.loads(CALIBRATION_JSON.read_text(encoding="utf-8"))


def test_the_committed_artefact_scores_pass_on_its_own_samples(genuine: dict[str, Any]) -> None:
    assert _verdict_from_calibration_samples(genuine) == "PASS"
    assert _calibration_verdict(CALIBRATION_JSON) == ("PASS", "fixtures/calibration_report.json")


def test_a_bare_verdict_field_is_not_evidence() -> None:
    assert _verdict_from_calibration_samples({"verdict": "PASS"}) == "NOT_RUN"
    assert _verdict_from_calibration_samples({}) == "NOT_RUN"
    assert _verdict_from_calibration_samples("PASS") == "NOT_RUN"


def test_a_forged_verdict_cannot_override_the_samples(genuine: dict[str, Any]) -> None:
    forged = copy.deepcopy(genuine)
    forged["verdict"] = "PASS"
    forged["delays"][0]["samples_s"] = [0.9] * 20  # the 100 ms row really took 900 ms
    forged["delays"][0]["passed"] = True
    assert _verdict_from_calibration_samples(forged) == "FAIL"


def test_a_pessimistic_verdict_cannot_override_the_samples(genuine: dict[str, Any]) -> None:
    claimed_fail = copy.deepcopy(genuine)
    claimed_fail["verdict"] = "FAIL"
    for row in claimed_fail["delays"]:
        row["passed"] = False
    assert _verdict_from_calibration_samples(claimed_fail) == "PASS"


def test_a_widened_tolerance_is_refused_rather_than_reported(genuine: dict[str, Any]) -> None:
    loosened = copy.deepcopy(genuine)
    loosened["tolerance"] = {"max_rel_error": 0.9, "max_stdev_s": 9.0}
    loosened["delays"][0]["samples_s"] = [0.15] * 20
    assert _verdict_from_calibration_samples(loosened) == "NOT_RUN"


def test_a_tightened_tolerance_is_honoured(genuine: dict[str, Any]) -> None:
    tightened = copy.deepcopy(genuine)
    tightened["tolerance"] = {"max_rel_error": 0.0001, "max_stdev_s": 0.0001}
    assert _verdict_from_calibration_samples(tightened) == "FAIL"


def test_a_row_that_cannot_support_a_spread_is_not_run(genuine: dict[str, Any]) -> None:
    thin = copy.deepcopy(genuine)
    thin["delays"][0]["samples_s"] = [0.1]
    assert _verdict_from_calibration_samples(thin) == "NOT_RUN"


def test_a_missing_or_unreadable_artefact_is_not_run(tmp_path: Path) -> None:
    assert _calibration_verdict(tmp_path / "absent.json") == ("NOT_RUN", None)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert _calibration_verdict(broken)[0] == "NOT_RUN"
