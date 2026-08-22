"""The engine: run a contract set over a trace, aggregate over many traces.

WHAT THIS DEMONSTRATES
----------------------
Three properties that separate a check runner you can trust from a loop over a
list of assertions.

**1. One broken contract must not take down the report.** Contracts are code, and
code raises — a scenario passes a string where a number was expected, a payload
arrives in an unexpected shape. If that exception propagates, the whole suite
dies and you learn nothing about the other nineteen contracts that were about to
tell you something useful. So `run` catches per contract, records the exception
as a failure attributed to the harness (`CheckResult.error`), and continues. The
run is still red — a contract that cannot execute is not a pass — but it is red
with information.

**2. Vacuous passes are counted, never hidden.** A contract that had nothing to
assert (`applicable=False`) does not fail a run and does not count towards the
pass rate either. It gets its own column. This is the mechanism that stops a
suite from going green by going silent: when a scenario stops exercising the
handoff, the propagation contracts turn vacuous and the report says so, instead
of reporting five passes that asserted nothing.

**3. Every rate ships with its numerator and denominator.** There is no method on
this module that returns a bare percentage. "87%" is unactionable — 87% of what,
and how many traces is that? — and worse, it hides the case where the
denominator collapsed. `CheckStat.rate` returns "13/15", and the renderers print
counts alongside every proportion.

WHAT THIS MODULE IS NOT
-----------------------
It is not the reporting layer. Rendering to markdown, charts, and cross-run
comparison live in `lab/report`. What lives here is aggregation of verdicts into
plain data structures that a reporter (or a pytest assertion, or a JSON dump) can
consume without reinterpreting anything.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.checks.contracts import Contract
from lab.checks.result import CheckResult
from lab.trace.schema import Trace

__all__ = [
    "CheckReport",
    "ContractSet",
    "CheckStat",
    "SuiteAggregate",
    "run_contracts",
    "aggregate",
]


class CheckReport(BaseModel):
    """Every verdict from one contract set against one trace."""

    model_config = ConfigDict(extra="forbid")

    suite: str = Field(description="Name of the contract set that produced this.")
    session_id: str
    scenario_id: str
    adapter: str
    results: list[CheckResult] = Field(default_factory=list)

    # ------------------------------------------------------------------ counts

    @property
    def total(self) -> int:
        """Every contract that ran, including vacuous ones."""
        return len(self.results)

    @property
    def applicable(self) -> int:
        """Contracts that actually asserted something — the honest denominator.

        Errored contracts are counted here, matching `CheckStat.applicable`: a
        contract that blew up is a live problem, and letting it shrink the
        denominator would let a suite hide breakage the same way vacuity hides
        coverage gaps. Only genuinely vacuous results are excluded.
        """
        return self.total - self.vacuous

    @property
    def passed(self) -> int:
        """Applicable contracts that passed. Vacuous passes are excluded by design."""
        return sum(1 for r in self.results if r.passed and r.applicable and r.error is None)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def vacuous(self) -> int:
        return sum(1 for r in self.results if r.passed and not r.applicable)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def ok(self) -> bool:
        """True when nothing failed.

        A vacuous result does not make a run red — it asserted nothing, so it
        found nothing wrong. It does show up in `vacuous` and in every rendering,
        which is where the pressure to fix the scenario belongs.
        """
        return self.failed == 0

    # ----------------------------------------------------------------- lookups

    def failures(self) -> list[CheckResult]:
        """Just the failures, in declaration order — what a triage view wants."""
        return [r for r in self.results if not r.passed]

    def by_name(self, name: str) -> CheckResult | None:
        """One result by contract name, or None."""
        return next((r for r in self.results if r.name == name), None)

    def __getitem__(self, name: str) -> CheckResult:
        result = self.by_name(name)
        if result is None:
            known = ", ".join(r.name for r in self.results) or "none"
            raise KeyError(f"no check named {name!r} in this report (have: {known})")
        return result

    # --------------------------------------------------------------- rendering

    def summary_line(self) -> str:
        """One line, with numerator and denominator for every figure."""
        parts = [
            f"{self.passed}/{self.applicable} applicable checks passed",
            f"{self.failed} failed",
            f"{self.vacuous} vacuous",
        ]
        if self.errors:
            parts.append(f"{self.errors} errored")
        parts.append(f"{self.total} declared")
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.scenario_id}: " + ", ".join(parts)

    def render(self, *, failures_only: bool = False) -> str:
        """Full human rendering: summary line, then each result with its evidence."""
        lines = [self.summary_line()]
        chosen = self.failures() if failures_only else self.results
        for result in chosen:
            lines.append(result.render(indent="  "))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"CheckReport(suite={self.suite!r}, scenario_id={self.scenario_id!r}, "
            f"passed={self.passed}/{self.applicable}, failed={self.failed})"
        )


def run_contracts(
    trace: Trace,
    contracts: Sequence[Contract],
    context: Mapping[str, Any] | None = None,
    *,
    suite: str = "ad-hoc",
) -> CheckReport:
    """Run each contract against `trace` and collect the verdicts.

    Contracts run in declaration order and are fully independent — none of them
    can see another's verdict. That is deliberate: a contract set has to be
    reorderable and subsettable without changing any result, or bisecting a
    failing suite becomes guesswork.

    An exception inside a contract becomes a failed `CheckResult` carrying the
    traceback's final line, and the run continues.
    """
    results: list[CheckResult] = []
    for contract in contracts:
        name = getattr(contract, "name", type(contract).__name__)
        try:
            results.append(contract.check(trace, context))
        except Exception as exc:  # noqa: BLE001 - a contract's bug is a finding, not a crash
            results.append(
                CheckResult(
                    name=name,
                    passed=False,
                    detail=f"contract raised {type(exc).__name__}: {exc}",
                    error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
                    contract=type(contract).__name__,
                )
            )
    return CheckReport(
        suite=suite,
        session_id=trace.session_id,
        scenario_id=trace.scenario_id,
        adapter=trace.adapter,
        results=results,
    )


@dataclass(frozen=True)
class ContractSet:
    """A named, reusable bundle of contracts.

    The unit a scenario declares and a suite runs. Frozen and comparable, because
    a contract set is configuration: it gets stored next to a scenario, diffed
    when a regression appears, and must mean the same thing on every machine.
    """

    name: str
    contracts: tuple[Contract, ...] = ()

    def __post_init__(self) -> None:
        duplicates = self._duplicate_names()
        if duplicates:
            raise ValueError(
                f"contract names must be unique within a set; duplicated: {sorted(duplicates)}. "
                "Reports are keyed by name, so a duplicate would silently shadow a verdict."
            )

    def _duplicate_names(self) -> set[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for contract in self.contracts:
            name = getattr(contract, "name", type(contract).__name__)
            if name in seen:
                duplicates.add(name)
            seen.add(name)
        return duplicates

    def run(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckReport:
        """Run the whole set over one trace."""
        return run_contracts(trace, self.contracts, context, suite=self.name)

    def run_all(
        self,
        traces: Iterable[Trace],
        context: Mapping[str, Any] | None = None,
    ) -> list[CheckReport]:
        """Run the set over many traces, sharing one context.

        For per-trace contexts, call `run` in a loop — passing a scenario's own
        facts is the caller's business, and guessing at a trace-to-context
        mapping here would be a source of silent mismatches.
        """
        return [self.run(trace, context) for trace in traces]

    def with_contracts(self, *extra: Contract) -> ContractSet:
        """A new set with extra contracts appended; the original is untouched."""
        return ContractSet(name=self.name, contracts=self.contracts + tuple(extra))


class CheckStat(BaseModel):
    """How one named contract fared across many traces."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: int = 0
    failed: int = 0
    vacuous: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        """Every trace this contract ran against."""
        return self.passed + self.failed + self.vacuous

    @property
    def applicable(self) -> int:
        """Traces where this contract actually asserted something."""
        return self.passed + self.failed

    @property
    def rate(self) -> str:
        """Pass rate as "passed/applicable" — never a bare percentage.

        Returns "0/0" when the contract was vacuous everywhere, which is the
        signal that the contract has stopped testing anything at all.
        """
        return f"{self.passed}/{self.applicable}"

    def render(self) -> str:
        line = f"{self.name}: {self.rate} applicable traces passed"
        if self.vacuous:
            line += f", {self.vacuous}/{self.total} vacuous"
        if self.errors:
            line += f", {self.errors} errored"
        if self.applicable == 0:
            line += "  <- asserted nothing on any trace"
        return line


class SuiteAggregate(BaseModel):
    """Roll-up of many `CheckReport`s: per-contract stats plus trace-level counts."""

    model_config = ConfigDict(extra="forbid")

    suite: str
    traces: int = 0
    traces_ok: int = 0
    stats: list[CheckStat] = Field(default_factory=list)

    @property
    def trace_pass_rate(self) -> str:
        """Traces with no failing contract, as "ok/total"."""
        return f"{self.traces_ok}/{self.traces}"

    def stat(self, name: str) -> CheckStat | None:
        return next((s for s in self.stats if s.name == name), None)

    def vacuous_contracts(self) -> list[CheckStat]:
        """Contracts that asserted nothing anywhere — the suite's blind spots.

        Worth surfacing on its own: these are the checks a reader believes are
        protecting them and which are not currently capable of failing.
        """
        return [s for s in self.stats if s.applicable == 0]

    def render(self) -> str:
        lines = [
            f"suite {self.suite!r}: {self.trace_pass_rate} traces passed every applicable check",
        ]
        for stat in self.stats:
            lines.append(f"  {stat.render()}")
        blind = self.vacuous_contracts()
        if blind:
            lines.append(
                f"  note: {len(blind)}/{len(self.stats)} contracts asserted nothing on any trace: "
                + ", ".join(s.name for s in blind)
            )
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"SuiteAggregate(suite={self.suite!r}, traces={self.trace_pass_rate})"


def aggregate(reports: Sequence[CheckReport], *, suite: str | None = None) -> SuiteAggregate:
    """Roll many per-trace reports into per-contract statistics.

    Contract order follows first appearance across the reports, so the aggregate
    reads in the order the set declared. Contracts absent from some reports are
    counted only where they ran — an aggregate must not invent results for a
    trace a contract never saw.
    """
    stats: dict[str, CheckStat] = {}
    order: list[str] = []
    traces_ok = 0

    for report in reports:
        if report.ok:
            traces_ok += 1
        for result in report.results:
            if result.name not in stats:
                stats[result.name] = CheckStat(name=result.name)
                order.append(result.name)
            stat = stats[result.name]
            if result.error is not None:
                stat.errors += 1
                stat.failed += 1
            elif not result.passed:
                stat.failed += 1
            elif result.applicable:
                stat.passed += 1
            else:
                stat.vacuous += 1

    resolved = suite or (reports[0].suite if reports else "empty")
    return SuiteAggregate(
        suite=resolved,
        traces=len(reports),
        traces_ok=traces_ok,
        stats=[stats[name] for name in order],
    )
