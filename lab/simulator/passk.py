"""pass^k: run a scenario k times and report whether it passes *every* time.

WHY THIS IS A FIRST-CLASS RESULT AND NOT A FOOTNOTE
---------------------------------------------------
A single run of a scenario against a temperature-bearing model is one sample from
a distribution. Reporting that sample as "PASS" is the most common way an
evaluation suite lies to its owner, because the lie is stable in the direction
people want: a suite of a hundred scenarios, each genuinely passing 80% of the
time, produces a green run roughly never — and the response is usually to re-run
until it is green, which is sampling until the answer is nice.

So this module makes the k-run verdict the unit of reporting:

    STABLE_PASS   passed every run           — a result you can act on
    STABLE_FAIL   failed every run           — a bug, reproducible, go fix it
    FLAKY         passed some runs, failed others

**FLAKY is not a pass.** `StabilityVerdict.passed` is True only for STABLE_PASS.
A scenario that passes 3 of 5 runs has not passed; it has told you that the
agent's behaviour on that scenario is not determined by the scenario, which is
usually a *more* serious finding than a clean failure, and is certainly not
something to ship on. It is also the finding a single-run suite structurally
cannot produce.

The verdict carries `pass_rate` as an explicit numerator and denominator — never
a bare percentage — and the per-run outcomes with their evidence, so a reader can
see which runs failed and why rather than taking "3/5" on faith.

WHAT FLAKE RATE MEANS HERE
--------------------------
`flake_rate = min(passes, failures) / k`: the fraction of runs that disagreed
with the majority. Zero for any unanimous result, maximal (0.5) for an even
split. It answers "how unreliable is this scenario" on a scale that does not
depend on which side happened to win, which a raw pass rate does not: 1/5 and 4/5
are equally unstable and equally unsafe to report as a verdict, and only one of
them looks alarming in a pass-rate column.

RELATION TO THE pass@k IN THE LITERATURE
----------------------------------------
Code-generation benchmarks report pass@k: the probability that *at least one* of
k samples is correct — a useful figure when a human filters the candidates. This
is the opposite quantity, sometimes written pass^k: the probability that *all* k
are correct. For a production agent nobody filters the outputs, every sample is
served to a caller, and the only interesting question is whether the bad one can
happen at all. Using the well-known name for the inverted metric would be the
kind of quiet mislabel this repo exists to catch, hence the caret.

WHAT THIS CANNOT TELL YOU
-------------------------
k runs bound the flake rate only as loosely as k allows: five green runs are
consistent with a scenario that fails one time in twenty. The honest reading of
STABLE_PASS at k=5 is "no instability observed in 5 runs", which is what
`describe()` prints. Raising k is the only fix, and `total_runs` is in every
report so the strength of the claim is always visible next to the claim.
"""

from __future__ import annotations

from typing import Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lab.trace.schema import Trace

__all__ = [
    "Stability",
    "format_rate",
    "RunOutcome",
    "PassKPolicy",
    "StabilityVerdict",
    "OutcomeLike",
    "coerce_outcome",
    "run_pass_k",
    "verdict_from_outcomes",
    "summarise_stability",
    "StabilitySummary",
]

Stability = Literal["STABLE_PASS", "STABLE_FAIL", "FLAKY"]


def format_rate(numerator: int, denominator: int) -> str:
    """`"3/5 (60.0%)"` — the only sanctioned way to render a rate in this repo.

    Defined here rather than in `lab.report` because a stability verdict has to be
    printable on its own — `describe()` is used in logs and assertion messages —
    and `lab.report` re-exports this exact function rather than reimplementing it.
    One implementation, so the rule (a percentage never appears without the counts
    that produced it) cannot drift between the two places that print rates.
    """
    if denominator == 0:
        return "0/0 (no runs)"
    return f"{numerator}/{denominator} ({100.0 * numerator / denominator:.1f}%)"


class RunOutcome(BaseModel):
    """The verdict on one run of a scenario, with the evidence for it."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Zero-based repeat number.")
    passed: bool
    session_id: str | None = Field(
        default=None,
        description="Trace this verdict was read from, so a failure can be reopened.",
    )
    evidence: str | None = Field(
        default=None,
        description=(
            "A quote from the trace supporting the verdict. Required in practice "
            "for failures — see `StabilityVerdict.missing_evidence`."
        ),
    )
    failed_checks: list[str] = Field(
        default_factory=list, description="Names of the checks that failed, if known."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Set when the run raised rather than returning a verdict. An "
            "exception is a failure, never a skip: a scenario that crashes the "
            "harness has not passed."
        ),
    )


#: What an `evaluate` callable may return. `bool` and `(bool, evidence)` exist so
#: a one-line predicate is enough to get started; `RunOutcome` for the real thing.
OutcomeLike = RunOutcome | bool | tuple[bool, str]


def coerce_outcome(value: OutcomeLike, *, index: int, trace: Trace | None = None) -> RunOutcome:
    """Normalise an evaluator's return value into a `RunOutcome`."""
    session_id = trace.session_id if trace is not None else None
    if isinstance(value, RunOutcome):
        return value.model_copy(
            update={
                "index": index,
                "session_id": value.session_id or session_id,
            }
        )
    if isinstance(value, bool):
        return RunOutcome(index=index, passed=value, session_id=session_id)
    if isinstance(value, tuple) and len(value) == 2:
        passed, evidence = value
        return RunOutcome(
            index=index,
            passed=bool(passed),
            evidence=str(evidence),
            session_id=session_id,
        )
    raise TypeError(
        "an evaluate() callable must return a RunOutcome, a bool, or a "
        f"(bool, evidence) tuple; got {type(value).__name__}"
    )


class PassKPolicy(BaseModel):
    """When k runs count as stable. Printed with every verdict that used it.

    The defaults require unanimity, which is the whole argument of this module.
    The knobs exist because a large suite of long-running live scenarios sometimes
    has to tolerate a known-noisy environment — but loosening them is then a
    recorded, reviewable decision that travels attached to the numbers, instead of
    a habit of re-running until green.
    """

    model_config = ConfigDict(extra="forbid")

    stable_pass_at_or_above: float = Field(
        default=1.0,
        gt=0.0,
        le=1.0,
        description="Pass rate at or above which a result is STABLE_PASS.",
    )
    stable_fail_at_or_below: float = Field(
        default=0.0,
        ge=0.0,
        lt=1.0,
        description="Pass rate at or below which a result is STABLE_FAIL.",
    )

    @model_validator(mode="after")
    def _ordered(self) -> "PassKPolicy":
        if self.stable_fail_at_or_below >= self.stable_pass_at_or_above:
            raise ValueError(
                "stable_fail_at_or_below must be below stable_pass_at_or_above, or "
                "there is no band left for FLAKY and the whole verdict is decoration"
            )
        return self

    @property
    def is_unanimous(self) -> bool:
        """True for the default policy: every run must pass to score STABLE_PASS."""
        return self.stable_pass_at_or_above >= 1.0 and self.stable_fail_at_or_below <= 0.0

    def describe(self) -> str:
        if self.is_unanimous:
            return "unanimous: STABLE_PASS requires every run to pass"
        return (
            f"STABLE_PASS at pass rate >= {self.stable_pass_at_or_above:.0%}, "
            f"STABLE_FAIL at <= {self.stable_fail_at_or_below:.0%}, FLAKY between"
        )

    def classify(self, passes: int, total: int) -> Stability:
        """Score a pass count. Zero runs is a failure, not a pass."""
        if total == 0:
            return "STABLE_FAIL"
        rate = passes / total
        if rate >= self.stable_pass_at_or_above:
            return "STABLE_PASS"
        if rate <= self.stable_fail_at_or_below:
            return "STABLE_FAIL"
        return "FLAKY"


class StabilityVerdict(BaseModel):
    """The result of running one scenario k times.

    The object a report row is built from. `passed` is deliberately narrow:
    STABLE_PASS only, so no amount of downstream aggregation can turn a flaky
    scenario into a green one by rounding.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    verdict: Stability
    total_runs: int = Field(ge=0, description="k.")
    passes: int = Field(ge=0)
    policy: PassKPolicy = Field(default_factory=PassKPolicy)
    outcomes: list[RunOutcome] = Field(default_factory=list)
    label: str | None = Field(
        default=None, description="Optional human label, e.g. the persona or model."
    )

    @model_validator(mode="after")
    def _consistent(self) -> "StabilityVerdict":
        if self.passes > self.total_runs:
            raise ValueError(
                f"{self.scenario_id}: {self.passes} passes out of {self.total_runs} runs"
            )
        if self.outcomes and len(self.outcomes) != self.total_runs:
            raise ValueError(
                f"{self.scenario_id}: {len(self.outcomes)} outcomes recorded but "
                f"total_runs is {self.total_runs}; the verdict and its evidence "
                "must describe the same set of runs"
            )
        return self

    # ----------------------------------------------------------------- figures

    @property
    def failures(self) -> int:
        return self.total_runs - self.passes

    @property
    def pass_rate(self) -> float:
        """Fraction in [0, 1]. Never render this without `pass_rate_str`."""
        return self.passes / self.total_runs if self.total_runs else 0.0

    @property
    def pass_rate_str(self) -> str:
        """`"3/5 (60.0%)"`. The only form that appears in a report."""
        return format_rate(self.passes, self.total_runs)

    @property
    def flake_rate(self) -> float:
        """Fraction of runs disagreeing with the majority; 0.0 when unanimous."""
        if self.total_runs == 0:
            return 0.0
        return min(self.passes, self.failures) / self.total_runs

    @property
    def flake_rate_str(self) -> str:
        return format_rate(min(self.passes, self.failures), self.total_runs)

    @property
    def passed(self) -> bool:
        """True for STABLE_PASS alone. FLAKY is not a pass."""
        return self.verdict == "STABLE_PASS"

    @property
    def is_flaky(self) -> bool:
        return self.verdict == "FLAKY"

    def failing_outcomes(self) -> list[RunOutcome]:
        return [o for o in self.outcomes if not o.passed]

    def failed_check_names(self) -> list[str]:
        """Distinct checks that failed across the k runs, in first-seen order."""
        seen: list[str] = []
        for outcome in self.failing_outcomes():
            for name in outcome.failed_checks:
                if name not in seen:
                    seen.append(name)
        return seen

    def missing_evidence(self) -> list[int]:
        """Indices of failing runs that recorded no evidence.

        A failure without a quote from the trace is an assertion, not a finding:
        nobody can triage it and nobody can check it. Surfaced as data so a report
        can flag its own gaps instead of presenting them as clean results.
        """
        return [o.index for o in self.failing_outcomes() if not (o.evidence or o.error)]

    def first_evidence(self) -> str | None:
        """Evidence from the earliest failing run, for a one-line report row."""
        for outcome in self.failing_outcomes():
            if outcome.evidence:
                return outcome.evidence
            if outcome.error:
                return f"run raised: {outcome.error}"
        return None

    def describe(self) -> str:
        """One line, with the counts, the flake rate and the strength of the claim."""
        head = f"{self.scenario_id}: {self.verdict} — passed {self.pass_rate_str}"
        if self.verdict == "STABLE_PASS":
            return f"{head}; no instability observed in {self.total_runs} runs"
        if self.verdict == "FLAKY":
            return (
                f"{head}; flake rate {self.flake_rate_str} of runs disagreed with "
                "the majority — NOT a pass"
            )
        return head


def verdict_from_outcomes(
    scenario_id: str,
    outcomes: Sequence[RunOutcome],
    *,
    policy: PassKPolicy | None = None,
    label: str | None = None,
) -> StabilityVerdict:
    """Score a set of already-computed run outcomes.

    Separate from `run_pass_k` so that a suite which ran its repeats elsewhere —
    in CI shards, across two model versions, or replayed from stored traces — is
    scored by exactly the same code as one that ran them in a loop here.
    """
    ordered = [o.model_copy(update={"index": i}) for i, o in enumerate(outcomes)]
    passes = sum(1 for o in ordered if o.passed)
    effective = policy if policy is not None else PassKPolicy()
    return StabilityVerdict(
        scenario_id=scenario_id,
        verdict=effective.classify(passes, len(ordered)),
        total_runs=len(ordered),
        passes=passes,
        policy=effective,
        outcomes=ordered,
        label=label,
    )


def run_pass_k(
    *,
    scenario_id: str,
    k: int,
    run: Callable[[int], Trace],
    evaluate: Callable[[Trace], OutcomeLike],
    policy: PassKPolicy | None = None,
    label: str | None = None,
    catch_errors: bool = True,
) -> StabilityVerdict:
    """Run a scenario `k` times, score each run, and return the stability verdict.

    Args:
        scenario_id: Identifier for the scenario being repeated.
        k: How many times to run it. `k=1` is permitted and is scored honestly —
            it can only ever produce STABLE_PASS or STABLE_FAIL, and `total_runs`
            in the report makes the weakness of that claim visible.
        run: Called with the repeat index; returns that run's `Trace`. Takes the
            index so a caller can vary a seed, and must build a *fresh* agent per
            call — an agent carrying state from the previous repeat is measuring
            conversation history, not stability.
        evaluate: Scores one trace. Return a `RunOutcome` (with evidence), a
            bool, or a `(bool, evidence)` tuple.
        policy: When k runs count as stable. Defaults to unanimity.
        label: Optional human label carried into the report.
        catch_errors: True (default) records an exception from `run` or
            `evaluate` as a failed outcome carrying the message, so one crashing
            repeat does not discard the k-1 results that did complete. False
            re-raises, which is what you want while debugging the harness itself.

    Returns:
        A `StabilityVerdict`. Check `.passed`, and remember FLAKY is not a pass.
    """
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k!r}")

    outcomes: list[RunOutcome] = []
    for index in range(k):
        trace: Trace | None = None
        try:
            trace = run(index)
            outcomes.append(coerce_outcome(evaluate(trace), index=index, trace=trace))
        except Exception as exc:  # noqa: BLE001 - recorded as a failure, see below
            if not catch_errors:
                raise
            outcomes.append(
                RunOutcome(
                    index=index,
                    passed=False,
                    session_id=trace.session_id if trace is not None else None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    return verdict_from_outcomes(scenario_id, outcomes, policy=policy, label=label)


class StabilitySummary(BaseModel):
    """Suite-level counts across many `StabilityVerdict`s.

    Exists so a report cannot summarise a suite by averaging pass rates — which
    would let two flaky scenarios average out to a healthy-looking number. The
    only aggregate offered is a count of scenarios in each verdict class, plus the
    stable-pass rate over scenarios, printed with its denominator.
    """

    model_config = ConfigDict(extra="forbid")

    scenarios: int = Field(ge=0)
    stable_pass: int = Field(ge=0)
    stable_fail: int = Field(ge=0)
    flaky: int = Field(ge=0)
    total_runs: int = Field(ge=0, description="Sum of k across all scenarios.")
    min_runs_per_scenario: int = Field(ge=0)

    @property
    def stable_pass_rate_str(self) -> str:
        return format_rate(self.stable_pass, self.scenarios)

    @property
    def flaky_rate_str(self) -> str:
        return format_rate(self.flaky, self.scenarios)

    def describe(self) -> str:
        return (
            f"{self.stable_pass_rate_str} scenarios stable-pass; "
            f"{self.flaky_rate_str} flaky; "
            f"{format_rate(self.stable_fail, self.scenarios)} stable-fail "
            f"(k >= {self.min_runs_per_scenario}, {self.total_runs} runs in total)"
        )


def summarise_stability(verdicts: Sequence[StabilityVerdict]) -> StabilitySummary:
    """Count verdicts by class. No pass rates are averaged; see `StabilitySummary`."""
    return StabilitySummary(
        scenarios=len(verdicts),
        stable_pass=sum(1 for v in verdicts if v.verdict == "STABLE_PASS"),
        stable_fail=sum(1 for v in verdicts if v.verdict == "STABLE_FAIL"),
        flaky=sum(1 for v in verdicts if v.verdict == "FLAKY"),
        total_runs=sum(v.total_runs for v in verdicts),
        min_runs_per_scenario=min((v.total_runs for v in verdicts), default=0),
    )
