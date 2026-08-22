"""Tests for pass^k stability verdicts.

WHAT THIS DEMONSTRATES
----------------------
The classification is the product here, so all three classes are tested, and the
sharpest test is `test_flaky_is_not_a_pass`: a scenario passing 3 of 5 runs must
not report as passed anywhere — not on the verdict, not on the summary, not after
aggregation. That property is the entire reason this module exists, and it is the
one a well-meaning refactor is most likely to soften.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.simulator.driver import AgentTurn, ScriptedCaller, run_scenario
from lab.simulator.passk import (
    PassKPolicy,
    RunOutcome,
    StabilityVerdict,
    coerce_outcome,
    format_rate,
    run_pass_k,
    summarise_stability,
    verdict_from_outcomes,
)
from lab.trace.schema import EventKind, Trace


def _outcomes(pattern: str) -> list[RunOutcome]:
    """`"PPFPF"` -> five outcomes, pass where the character is P."""
    return [
        RunOutcome(
            index=i,
            passed=char == "P",
            evidence=None if char == "P" else f"run {i}: agent said it was booked",
        )
        for i, char in enumerate(pattern)
    ]


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def test_stable_pass_requires_every_run() -> None:
    verdict = verdict_from_outcomes("booking/simple", _outcomes("PPPPP"))
    assert verdict.verdict == "STABLE_PASS"
    assert verdict.passed is True
    assert verdict.pass_rate_str == "5/5 (100.0%)"
    assert verdict.flake_rate == 0.0
    assert "no instability observed in 5 runs" in verdict.describe()


def test_stable_fail_is_every_run_failing() -> None:
    verdict = verdict_from_outcomes("booking/large_party", _outcomes("FFFFF"))
    assert verdict.verdict == "STABLE_FAIL"
    assert verdict.passed is False
    assert verdict.pass_rate_str == "0/5 (0.0%)"
    assert verdict.flake_rate == 0.0  # unanimous, just unanimously wrong
    assert verdict.first_evidence() == "run 0: agent said it was booked"


def test_flaky_is_not_a_pass() -> None:
    verdict = verdict_from_outcomes("booking/dietary", _outcomes("PPFPF"))
    assert verdict.verdict == "FLAKY"
    # The property the whole module exists for.
    assert verdict.passed is False
    assert verdict.is_flaky is True
    assert verdict.pass_rate_str == "3/5 (60.0%)"
    assert verdict.flake_rate_str == "2/5 (40.0%)"
    assert "NOT a pass" in verdict.describe()


def test_flake_rate_is_symmetric_about_the_majority() -> None:
    # 1/5 and 4/5 are equally unstable and equally unsafe to report as a verdict.
    # A raw pass rate makes only one of them look alarming.
    mostly_failing = verdict_from_outcomes("a", _outcomes("PFFFF"))
    mostly_passing = verdict_from_outcomes("b", _outcomes("PPPPF"))
    assert mostly_failing.flake_rate == mostly_passing.flake_rate == pytest.approx(0.2)
    assert not mostly_failing.passed and not mostly_passing.passed


def test_k_of_one_is_scored_honestly() -> None:
    verdict = verdict_from_outcomes("booking/simple", _outcomes("P"))
    assert verdict.verdict == "STABLE_PASS"
    # ...but the claim's weakness is visible in the same object.
    assert verdict.total_runs == 1
    assert "1/1" in verdict.pass_rate_str


def test_a_loosened_policy_travels_with_the_verdict() -> None:
    policy = PassKPolicy(stable_pass_at_or_above=0.8, stable_fail_at_or_below=0.2)
    verdict = verdict_from_outcomes("booking/noisy", _outcomes("PPPPF"), policy=policy)
    assert verdict.verdict == "STABLE_PASS"
    assert verdict.policy.is_unanimous is False
    # Loosening is a recorded decision, printable next to the number it produced.
    assert "STABLE_PASS at pass rate >= 80%" in verdict.policy.describe()


def test_a_policy_with_no_flaky_band_is_rejected() -> None:
    with pytest.raises(ValueError, match="no band left for FLAKY"):
        PassKPolicy(stable_pass_at_or_above=0.5, stable_fail_at_or_below=0.6)


def test_zero_runs_is_a_failure_not_a_pass() -> None:
    verdict = verdict_from_outcomes("booking/never_ran", [])
    assert verdict.verdict == "STABLE_FAIL"
    assert verdict.pass_rate_str == "0/0 (no runs)"


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #


class _FlakyAgent:
    """Passes on even repeats, fails on odd ones. The bug this module catches."""

    def __init__(self, repeat: int) -> None:
        self.repeat = repeat

    def __call__(self, utterance: str) -> AgentTurn:
        if self.repeat % 2 == 0:
            return AgentTurn(
                text="you're booked",
                agent="BookingAgent",
                tools=[{"name": "create_booking", "args": {"party_size": 6}}],  # type: ignore[list-item]
            )
        # The phantom confirmation: says it is booked, never calls the tool.
        return AgentTurn(text="you're booked", agent="BookingAgent")


def _run(repeat: int) -> Trace:
    clock = FakeClock()
    return run_scenario(
        scenario_id="booking/large_party",
        agent=_FlakyAgent(repeat),
        caller=ScriptedCaller(["a table for six on Friday"]),
        clock=clock,
        session_id=f"sess-{repeat}",
    )


def _decision_matches_action(trace: Trace) -> RunOutcome:
    """A miniature decision-vs-action check, so the test has a real evaluator.

    Not the shipped check — `lab.checks` owns that — but a real one, reading the
    trace the same way: if the agent claimed a booking, a booking tool call must
    exist in the same trace.
    """
    claimed = any("booked" in text for text in trace.texts("agent"))
    called = "create_booking" in trace.tool_names()
    if claimed and not called:
        said = next(t for t in trace.texts("agent") if "booked" in t)
        return RunOutcome(
            index=0,
            passed=False,
            evidence=f'agent said "{said}" but create_booking was never called',
            failed_checks=["decision_vs_action"],
        )
    return RunOutcome(index=0, passed=True)


def test_run_pass_k_catches_a_flaky_agent_a_single_run_would_have_passed() -> None:
    verdict = run_pass_k(
        scenario_id="booking/large_party",
        k=5,
        run=_run,
        evaluate=_decision_matches_action,
    )
    assert verdict.verdict == "FLAKY"
    assert verdict.pass_rate_str == "3/5 (60.0%)"
    assert verdict.failed_check_names() == ["decision_vs_action"]
    assert "create_booking was never called" in (verdict.first_evidence() or "")
    # Session ids survive so a failing repeat can be reopened by name.
    assert [o.session_id for o in verdict.failing_outcomes()] == ["sess-1", "sess-3"]
    # A single run of repeat 0 would have reported a clean pass.
    assert _decision_matches_action(_run(0)).passed is True


def test_a_crashing_repeat_is_a_failure_not_a_lost_result() -> None:
    def sometimes_raises(index: int) -> Trace:
        if index == 2:
            raise RuntimeError("adapter exploded")
        return _run(0)

    verdict = run_pass_k(
        scenario_id="booking/crashy",
        k=4,
        run=sometimes_raises,
        evaluate=_decision_matches_action,
    )
    assert verdict.verdict == "FLAKY"
    assert verdict.pass_rate_str == "3/4 (75.0%)"
    crashed = verdict.failing_outcomes()[0]
    assert crashed.error is not None and "adapter exploded" in crashed.error
    # The three completed runs were kept: a crash discards nothing.
    assert verdict.total_runs == 4


def test_crashes_can_be_re_raised_while_debugging_the_harness() -> None:
    def always_raises(index: int) -> Trace:
        raise RuntimeError("adapter exploded")

    with pytest.raises(RuntimeError, match="adapter exploded"):
        run_pass_k(
            scenario_id="booking/crashy",
            k=2,
            run=always_raises,
            evaluate=_decision_matches_action,
            catch_errors=False,
        )


def test_k_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="k must be at least 1"):
        run_pass_k(scenario_id="x", k=0, run=_run, evaluate=_decision_matches_action)


def test_evaluate_may_return_a_bool_or_a_tuple() -> None:
    plain = run_pass_k(
        scenario_id="x", k=2, run=_run, evaluate=lambda trace: len(trace) > 0
    )
    assert plain.verdict == "STABLE_PASS"

    quoted = run_pass_k(
        scenario_id="x",
        k=2,
        run=_run,
        evaluate=lambda trace: (False, "no create_booking in the trace"),
    )
    assert quoted.verdict == "STABLE_FAIL"
    assert quoted.first_evidence() == "no create_booking in the trace"


def test_coerce_outcome_rejects_anything_else() -> None:
    with pytest.raises(TypeError, match="must return a RunOutcome"):
        coerce_outcome("passed", index=0)  # type: ignore[arg-type]


def test_traces_from_repeats_are_independent() -> None:
    """Each repeat gets a fresh agent, so run 3 is not measuring runs 0-2.

    The reason `run` takes the repeat index and builds its own agent: a stateful
    agent shared across repeats turns a stability measurement into a measurement
    of conversation history.
    """
    first, second = _run(0), _run(0)
    assert [e.kind for e in first.events] == [e.kind for e in second.events]
    assert [e.ts for e in first.events] == [e.ts for e in second.events]
    assert first.tool_names() == second.tool_names() == ["create_booking"]
    assert first.first(EventKind.SESSION_START) is not None


# --------------------------------------------------------------------------- #
# Suite-level aggregation
# --------------------------------------------------------------------------- #


def test_summary_counts_scenarios_and_never_averages_pass_rates() -> None:
    verdicts = [
        verdict_from_outcomes("a", _outcomes("PPP")),
        verdict_from_outcomes("b", _outcomes("PPF")),
        verdict_from_outcomes("c", _outcomes("FFF")),
        verdict_from_outcomes("d", _outcomes("PPPP")),
    ]
    summary = summarise_stability(verdicts)
    assert summary.scenarios == 4
    assert (summary.stable_pass, summary.flaky, summary.stable_fail) == (2, 1, 1)
    assert summary.stable_pass_rate_str == "2/4 (50.0%)"
    assert summary.total_runs == 13
    assert summary.min_runs_per_scenario == 3
    # Two scenarios at 67% and 100% must not average into a healthy-looking
    # aggregate; the only aggregate offered is a count per verdict class.
    assert "2/4 (50.0%)" in summary.describe()


def test_verdict_and_evidence_must_describe_the_same_runs() -> None:
    with pytest.raises(ValueError, match="must describe the same set of runs"):
        StabilityVerdict(
            scenario_id="x",
            verdict="STABLE_PASS",
            total_runs=5,
            passes=5,
            outcomes=_outcomes("PP"),
        )


def test_missing_evidence_is_reported_rather_than_hidden() -> None:
    verdict = verdict_from_outcomes(
        "x",
        [
            RunOutcome(index=0, passed=True),
            RunOutcome(index=1, passed=False),  # a failure with no quote
            RunOutcome(index=2, passed=False, evidence="tool never called"),
        ],
    )
    assert verdict.missing_evidence() == [1]
    assert verdict.first_evidence() == "tool never called"


# --------------------------------------------------------------------------- #
# The rate rule
# --------------------------------------------------------------------------- #


def test_rates_always_carry_their_denominator() -> None:
    assert format_rate(3, 5) == "3/5 (60.0%)"
    assert format_rate(0, 0) == "0/0 (no runs)"
    assert format_rate(1, 3) == "1/3 (33.3%)"
