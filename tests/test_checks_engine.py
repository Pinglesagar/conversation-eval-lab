"""Tests for the contract runner and its aggregation.

WHAT THIS DEMONSTRATES
----------------------
The runner's job is to be boring under stress. Three properties get the most
attention here, because each one is a way a check runner can quietly lie:

* **A contract that raises must not take down the report.** The exception becomes
  a failure attributed to the harness, and the remaining contracts still run.
* **Vacuous passes must never inflate a pass rate.** A contract that asserted
  nothing is excluded from the numerator *and* the denominator, and counted in
  its own column. This is the mechanism that stops a suite from going green by
  going silent.
* **Every rate carries its numerator and denominator.** There is no API here that
  returns a bare percentage, and a test asserts it.
"""

from __future__ import annotations

import pytest

from lab.checks import (
    ArgPredicate,
    CheckResult,
    Contract,
    ContractSet,
    PromiseContract,
    ToolContract,
    aggregate,
    run_contracts,
)
from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace


def _trace(scenario_id: str = "sc", *, book: bool = True) -> Trace:
    """A minimal booking session, optionally with the `create_booking` omitted."""
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id=scenario_id, adapter="test", session_id=f"sess-{scenario_id}", clock=clock
    )
    builder.session_start()
    clock.advance(1.0)
    builder.caller_utterance("A table for two at 8, please.")
    clock.advance(1.0)
    builder.tool_call("search_tables", {"party_size": 2, "time": "8pm"})
    if book:
        clock.advance(1.0)
        builder.tool_call("create_booking", {"party_size": 2, "time": "8pm", "name": "Sam"})
    clock.advance(1.0)
    builder.agent_utterance("Your table is confirmed.", agent="BookingAgent")
    clock.advance(1.0)
    builder.session_end()
    return builder.build()


class _Exploding(Contract):
    """A contract with a bug in it, for testing containment."""

    name = "exploding"

    def check(self, trace: Trace, context: object = None) -> CheckResult:  # type: ignore[override]
        raise RuntimeError("bad contract")


class _AlwaysPasses(Contract):
    name = "always-passes"

    def check(self, trace: Trace, context: object = None) -> CheckResult:  # type: ignore[override]
        return CheckResult(name=self.name, passed=True, detail="1/1 fine")


# --------------------------------------------------------------------------- #
# run_contracts and CheckReport
# --------------------------------------------------------------------------- #


def test_report_carries_the_trace_identity() -> None:
    report = run_contracts(_trace("booking-happy"), [PromiseContract()], suite="s")
    assert report.scenario_id == "booking-happy"
    assert report.session_id == "sess-booking-happy"
    assert report.adapter == "test"
    assert report.suite == "s"


def test_contracts_run_in_declaration_order() -> None:
    report = run_contracts(
        _trace(), [ToolContract(expected=("search_tables",)), PromiseContract()]
    )
    assert [r.name for r in report.results] == ["tools", "promise-kept"]


def test_a_failing_contract_makes_the_report_not_ok() -> None:
    report = run_contracts(_trace(book=False), [PromiseContract()])
    assert not report.ok
    assert report.failed == 1
    assert [r.name for r in report.failures()] == ["promise-kept"]


def test_lookup_by_name_and_getitem() -> None:
    report = run_contracts(_trace(), [PromiseContract()])
    assert report["promise-kept"].passed
    assert report.by_name("nope") is None
    with pytest.raises(KeyError, match="no check named"):
        _ = report["nope"]


# --------------------------------------------------------------------------- #
# Error containment
# --------------------------------------------------------------------------- #


def test_a_raising_contract_becomes_a_failure_not_a_crash() -> None:
    report = run_contracts(_trace(), [_Exploding()])
    result = report["exploding"]
    assert not result.passed
    assert result.error is not None
    assert "bad contract" in result.error
    assert result.status == "ERROR"


def test_an_errored_contract_stays_in_the_denominator() -> None:
    """It must not shrink the denominator the way a vacuous result does: vacuity
    is a coverage gap, an exception is live breakage, and hiding the latter is
    how a suite reports health it has not measured."""
    report = run_contracts(_trace(), [_Exploding()])
    assert report.total == 1
    assert report.vacuous == 0
    assert report.applicable == 1
    assert report.passed == 0
    assert "0/1 applicable checks passed" in report.summary_line()


def test_the_rest_of_the_suite_still_runs_after_an_exception() -> None:
    """One broken contract must not cost you the nineteen useful verdicts."""
    report = run_contracts(
        _trace(), [_Exploding(), ToolContract(expected=("search_tables",)), _AlwaysPasses()]
    )
    assert len(report.results) == 3
    assert report.errors == 1
    assert report["tools"].passed
    assert report["always-passes"].passed
    assert not report.ok


# --------------------------------------------------------------------------- #
# Vacuous accounting
# --------------------------------------------------------------------------- #


def test_a_vacuous_pass_is_excluded_from_both_sides_of_the_rate() -> None:
    """The whole point: a contract that asserted nothing cannot be counted as
    evidence of health, in either the numerator or the denominator."""
    report = run_contracts(
        _trace(),
        [
            ToolContract(expected=("search_tables",)),  # applicable, passes
            ToolContract(name="empty"),  # declares nothing -> vacuous
        ],
    )
    assert report.total == 2
    assert report.applicable == 1
    assert report.passed == 1
    assert report.vacuous == 1
    assert report.ok  # vacuous does not fail a run


def test_summary_line_prints_a_numerator_and_denominator() -> None:
    report = run_contracts(
        _trace(), [ToolContract(expected=("search_tables",)), ToolContract(name="empty")]
    )
    line = report.summary_line()
    assert "1/1 applicable checks passed" in line
    assert "1 vacuous" in line
    assert "2 declared" in line
    assert "%" not in line


def test_render_includes_evidence_for_a_failure() -> None:
    report = run_contracts(_trace(book=False), [PromiseContract()])
    rendered = report.render()
    assert "FAIL" in rendered
    assert "Your table is confirmed." in rendered
    assert "no create_booking" in rendered


def test_render_can_be_narrowed_to_failures() -> None:
    report = run_contracts(
        _trace(book=False),
        [PromiseContract(), ToolContract(name="searched-first", expected=("search_tables",))],
    )
    assert "searched-first" not in report.render(failures_only=True)
    assert "promise-kept" in report.render(failures_only=True)
    assert "searched-first" in report.render()


# --------------------------------------------------------------------------- #
# ContractSet
# --------------------------------------------------------------------------- #


def test_contract_set_runs_its_bundle() -> None:
    suite = ContractSet(
        name="booking", contracts=(ToolContract(expected=("create_booking",)), PromiseContract())
    )
    report = suite.run(_trace())
    assert report.suite == "booking"
    assert report.ok


def test_duplicate_contract_names_are_rejected() -> None:
    """Reports are keyed by name, so a duplicate would silently shadow a verdict."""
    with pytest.raises(ValueError, match="must be unique"):
        ContractSet(
            name="dupes",
            contracts=(ToolContract(expected=("a",)), ToolContract(expected=("b",))),
        )


def test_distinct_names_make_two_similar_contracts_legal() -> None:
    suite = ContractSet(
        name="ok",
        contracts=(
            ToolContract(name="searched", expected=("search_tables",)),
            ToolContract(name="booked", expected=("create_booking",)),
        ),
    )
    assert suite.run(_trace()).ok


def test_with_contracts_does_not_mutate_the_original() -> None:
    base = ContractSet(name="base", contracts=(PromiseContract(),))
    extended = base.with_contracts(ToolContract(expected=("search_tables",)))
    assert len(base.contracts) == 1
    assert len(extended.contracts) == 2


def test_run_all_returns_one_report_per_trace() -> None:
    suite = ContractSet(name="s", contracts=(PromiseContract(),))
    reports = suite.run_all([_trace("a"), _trace("b", book=False)])
    assert [r.scenario_id for r in reports] == ["a", "b"]
    assert [r.ok for r in reports] == [True, False]


def test_context_reaches_the_contracts() -> None:
    suite = ContractSet(
        name="s",
        contracts=(
            ToolContract(args=(ArgPredicate("create_booking", "party_size", ref="party_size"),)),
        ),
    )
    assert suite.run(_trace(), {"party_size": 2}).ok
    assert not suite.run(_trace(), {"party_size": 6}).ok


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _reports() -> list:
    suite = ContractSet(
        name="booking",
        contracts=(
            ToolContract(name="searched", expected=("search_tables",)),
            PromiseContract(),
            ToolContract(name="empty"),
        ),
    )
    return suite.run_all([_trace("a"), _trace("b", book=False), _trace("c")])


def test_aggregate_counts_traces_and_per_contract_outcomes() -> None:
    agg = aggregate(_reports())
    assert agg.suite == "booking"
    assert agg.traces == 3
    assert agg.traces_ok == 2
    assert agg.trace_pass_rate == "2/3"

    searched = agg.stat("searched")
    assert searched is not None
    assert searched.rate == "3/3"

    promise = agg.stat("promise-kept")
    assert promise is not None
    assert promise.passed == 2
    assert promise.failed == 1
    assert promise.rate == "2/3"


def test_aggregate_keeps_vacuous_out_of_the_rate() -> None:
    agg = aggregate(_reports())
    empty = agg.stat("empty")
    assert empty is not None
    assert empty.vacuous == 3
    assert empty.applicable == 0
    assert empty.rate == "0/0"
    assert empty.total == 3


def test_vacuous_contracts_are_reported_as_blind_spots() -> None:
    """These are the checks a reader believes are protecting them and which are
    not currently capable of failing."""
    agg = aggregate(_reports())
    assert [s.name for s in agg.vacuous_contracts()] == ["empty"]
    assert "asserted nothing on any trace" in agg.render()


def test_aggregate_preserves_declaration_order() -> None:
    agg = aggregate(_reports())
    assert [s.name for s in agg.stats] == ["searched", "promise-kept", "empty"]


def test_aggregate_counts_a_contract_only_where_it_ran() -> None:
    """An aggregate must not invent results for a trace a contract never saw."""
    first = run_contracts(_trace("a"), [PromiseContract(), _AlwaysPasses()], suite="s")
    second = run_contracts(_trace("b"), [PromiseContract()], suite="s")
    agg = aggregate([first, second])
    promise = agg.stat("promise-kept")
    only_once = agg.stat("always-passes")
    assert promise is not None and promise.total == 2
    assert only_once is not None and only_once.total == 1


def test_an_errored_contract_counts_as_failed_and_errored() -> None:
    reports = [run_contracts(_trace(), [_Exploding()], suite="s")]
    agg = aggregate(reports)
    stat = agg.stat("exploding")
    assert stat is not None
    assert stat.errors == 1
    assert stat.failed == 1
    assert stat.passed == 0
    assert agg.traces_ok == 0


def test_aggregate_of_nothing_is_empty_not_an_error() -> None:
    agg = aggregate([])
    assert agg.traces == 0
    assert agg.trace_pass_rate == "0/0"
    assert agg.stats == []


def test_no_rate_is_ever_a_bare_percentage() -> None:
    """A house rule of this repo, enforced rather than documented: "87%" hides
    both the sample size and the case where the denominator collapsed."""
    agg = aggregate(_reports())
    rendered = agg.render()
    assert "%" not in rendered
    for stat in agg.stats:
        assert "/" in stat.rate
