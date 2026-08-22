"""The run report: markdown and JSON, with every rate carrying its denominator.

WHAT THIS DEMONSTRATES
----------------------
A report is not a summary of a run — it is the artifact a decision gets made
from, and most of the ways an evaluation misleads its own author happen at
rendering time rather than at measurement time. Four of those ways are made
structurally impossible here, by the types rather than by a style guide:

**1. No naked percentages.** Every rate is rendered by `format_rate`, which
prints `"5/6 (83.3%)"`. "83% pass" is consistent with 5 of 6 and with 830 of
1000, and those two numbers justify completely different decisions. The counts
are the finding; the percentage is a reading aid.

**2. A judge verdict cannot be printed without its calibration.**
`JudgeSummary.calibration` is a required field of type `JudgeCalibration`, so
there is no way to construct a judge section that omits the judge's measured
true-positive and true-negative rates against hand-labelled examples. A model
grading a model is an instrument with unknown error until someone measures it,
and an unlabelled judge verdict is an opinion wearing a number's clothes. Making
it a required field is the difference between a convention people forget and an
invariant that fails at construction.

**3. A latency figure cannot be printed without the calibration gate's verdict.**
`VoiceMetrics.calibration_verdict` is likewise required, including the explicit
`NOT_RUN` value. `lab.voice.calibration` exists to establish that the stopwatch
is accurate; a report that quotes a p95 while staying quiet about whether that
gate ran has skipped the only step that made the number mean anything.

**4. Stability is a section, not a footnote.** The headline verdict is computed
from `StabilityVerdict`s, where FLAKY is not a pass (`lab.simulator.passk`). A
suite cannot average its way to green here: `StabilitySummary` counts scenarios
per verdict class and never averages pass rates.

The report also audits itself. `integrity_gaps()` lists the places where the
report's own evidence is thin — failures recorded without a quote, scenarios run
at k=1, contracts with a zero denominator — and `to_markdown()` prints them in
their own section. A report that presents its gaps as clean results is worse than
no report, because it is trusted.

DETERMINISM
-----------
Nothing here reads the clock or the environment. Two reports built from the same
results are byte-identical, so a rendered report can be committed and its diff
reviewed like source. Any run label or timestamp is passed in by the caller.

SCOPE
-----
This module renders results; it does not compute them. Checks live in
`lab.checks`, judges in `lab.judges`, timing in `lab.voice`. It deliberately
imports none of them: the report's models are a rendering contract, populated by
whatever produced the numbers, which is what lets the same renderer serve a
deterministic check suite, a judged run and a replayed fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lab.simulator.passk import (
    StabilitySummary,
    StabilityVerdict,
    format_rate,
    summarise_stability,
)

__all__ = [
    "format_rate",
    "Rate",
    "ContractStat",
    "JudgeCalibration",
    "JudgeSummary",
    "VoiceMetrics",
    "FailureRecord",
    "RunReport",
    "write_report",
]


class Rate(BaseModel):
    """A count over a total. Carries both numbers into the JSON, not just the ratio.

    Exists so that a machine-readable report is as auditable as the markdown one:
    a consumer of the JSON can recompute the percentage and check the arithmetic,
    which it cannot do if the file only ever stored 0.833.
    """

    model_config = ConfigDict(extra="forbid")

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)

    @model_validator(mode="after")
    def _within_bounds(self) -> "Rate":
        if self.numerator > self.denominator:
            raise ValueError(
                f"a rate cannot exceed its denominator: {self.numerator}/{self.denominator}"
            )
        return self

    @property
    def fraction(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0

    @property
    def text(self) -> str:
        return format_rate(self.numerator, self.denominator)

    def __str__(self) -> str:
        return self.text


class ContractStat(BaseModel):
    """How one contract (a deterministic check) fared across the suite.

    `runs` is the denominator and is mandatory: "3 failures" is unreadable without
    knowing whether the contract was evaluated 3 times or 300. A contract with
    `runs == 0` was never exercised, which `integrity_gaps()` reports as a gap
    rather than as a clean pass — an unexercised check is indistinguishable from a
    passing one in a naive summary, and that is exactly how a suite ends up green
    while testing nothing.

    `vacuous` is the subtler version of the same problem, and the reason the
    failure rate is quoted over `applicable` rather than over `runs`: a check that
    *ran* but had nothing to assert on (a propagation contract on a trace with no
    handoff, say) has been skipped, not satisfied. Counting those as passes is how
    a suite rots — the scenarios drift, half the contracts stop applying, the
    dashboard stays green. So they are counted separately and a contract that was
    vacuous everywhere is reported as a gap.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    failures: int = Field(ge=0)
    runs: int = Field(ge=0, description="How many runs this contract was evaluated on.")
    vacuous: int = Field(
        default=0,
        ge=0,
        description="Of those runs, how many gave the contract nothing to assert on.",
    )
    description: str | None = None
    failing_scenarios: list[str] = Field(
        default_factory=list, description="Scenario ids where it failed, deduplicated."
    )

    @model_validator(mode="after")
    def _within_bounds(self) -> "ContractStat":
        if self.vacuous > self.runs:
            raise ValueError(
                f"{self.name}: {self.vacuous} vacuous out of {self.runs} runs"
            )
        if self.failures > self.applicable:
            raise ValueError(
                f"{self.name}: {self.failures} failures out of {self.applicable} "
                f"applicable runs ({self.runs} runs, {self.vacuous} vacuous)"
            )
        return self

    @property
    def applicable(self) -> int:
        """Runs where the contract actually asserted something."""
        return self.runs - self.vacuous

    @property
    def rate(self) -> Rate:
        return Rate(numerator=self.failures, denominator=self.applicable)

    @property
    def failure_rate_str(self) -> str:
        return self.rate.text

    @property
    def passed(self) -> bool:
        """True only when the contract asserted something and never failed."""
        return self.applicable > 0 and self.failures == 0


class JudgeCalibration(BaseModel):
    """A judge's measured agreement with hand labels. Required to report a judge.

    True-positive rate and true-negative rate rather than a single accuracy
    figure, because the two failure modes have different costs and accuracy hides
    the trade-off: a judge that flags everything scores 100% TPR and 0% TNR, and
    a judge that flags nothing does the reverse. Both look respectable as
    "accuracy" on a skewed label set, and neither is usable.

    The labelled set is small by nature — someone read every example by hand — so
    the counts are printed rather than the rates alone. A TPR of 100% over 4
    labelled positives is a much weaker claim than one over 60, and only the
    denominator says which you are looking at.
    """

    model_config = ConfigDict(extra="forbid")

    labelled_positive: int = Field(
        ge=0, description="Hand-labelled examples that genuinely exhibit the property."
    )
    labelled_negative: int = Field(
        ge=0, description="Hand-labelled examples that genuinely do not."
    )
    true_positives: int = Field(ge=0, description="Positives the judge caught.")
    true_negatives: int = Field(ge=0, description="Negatives the judge let through.")
    labelled_by: str | None = Field(
        default=None, description="Who labelled the set — provenance matters for a gold set."
    )

    @model_validator(mode="after")
    def _within_bounds(self) -> "JudgeCalibration":
        if self.true_positives > self.labelled_positive:
            raise ValueError(
                f"true_positives ({self.true_positives}) exceeds labelled_positive "
                f"({self.labelled_positive})"
            )
        if self.true_negatives > self.labelled_negative:
            raise ValueError(
                f"true_negatives ({self.true_negatives}) exceeds labelled_negative "
                f"({self.labelled_negative})"
            )
        return self

    @property
    def n_labelled(self) -> int:
        return self.labelled_positive + self.labelled_negative

    @property
    def tpr(self) -> Rate:
        """Sensitivity: of the examples that really are failures, how many caught."""
        return Rate(numerator=self.true_positives, denominator=self.labelled_positive)

    @property
    def tnr(self) -> Rate:
        """Specificity: of the examples that really are fine, how many left alone."""
        return Rate(numerator=self.true_negatives, denominator=self.labelled_negative)

    @property
    def false_positives(self) -> Rate:
        """Clean examples the judge wrongly flagged — the cost paid in triage time."""
        return Rate(
            numerator=self.labelled_negative - self.true_negatives,
            denominator=self.labelled_negative,
        )

    @property
    def false_negatives(self) -> Rate:
        """Real failures the judge missed — the cost paid in shipped bugs."""
        return Rate(
            numerator=self.labelled_positive - self.true_positives,
            denominator=self.labelled_positive,
        )

    def describe(self) -> str:
        return (
            f"TPR {self.tpr.text}, TNR {self.tnr.text} "
            f"on {self.n_labelled} hand-labelled examples"
            + (f", labelled by {self.labelled_by}" if self.labelled_by else "")
        )


class JudgeSummary(BaseModel):
    """What one judge said, and how much that is worth.

    `calibration` is required. That is the whole design of this class: a judge
    verdict without its TPR and TNR is not evidence, so the type system refuses
    to render one.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    model: str = Field(min_length=1, description="Which model graded, e.g. 'gpt-4o-mini'.")
    calibration: JudgeCalibration
    judged: int = Field(ge=0, description="Runs this judge scored.")
    flagged: int = Field(ge=0, description="Runs it judged as failing.")
    abstained: int = Field(
        default=0,
        ge=0,
        description=(
            "Runs it declined to score. Counted, never silently dropped: "
            "abstentions shrink the denominator and a reader must see by how much."
        ),
    )
    replayed_from_fixture: bool = Field(
        default=True,
        description="True when the verdicts came from a recording rather than a live call.",
    )
    prompt_id: str | None = Field(
        default=None,
        description="Identifier of the exact prompt version used, for reproducibility.",
    )

    @model_validator(mode="after")
    def _within_bounds(self) -> "JudgeSummary":
        if self.flagged + self.abstained > self.judged:
            raise ValueError(
                f"{self.name}: flagged ({self.flagged}) plus abstained "
                f"({self.abstained}) exceeds judged ({self.judged})"
            )
        return self

    @property
    def flag_rate(self) -> Rate:
        return Rate(numerator=self.flagged, denominator=self.judged)

    @property
    def abstention_rate(self) -> Rate:
        return Rate(numerator=self.abstained, denominator=self.judged)

    def describe(self) -> str:
        """One line, verdicts and calibration inseparable."""
        return (
            f"{self.name} ({self.model}): flagged {self.flag_rate.text} — "
            f"{self.calibration.describe()}"
        )


class VoiceMetrics(BaseModel):
    """Latency and transcription figures, inseparable from the calibration verdict.

    `calibration_verdict` is required and has a `NOT_RUN` value on purpose: the
    honest way to publish a latency without the gate is to say so, not to omit
    the field. `lab.voice.calibration` is what makes these numbers a measurement
    instead of a reading, and a p95 quoted next to `NOT_RUN` is correctly
    discounted by anyone who sees it.

    Percentiles, not just a mean: a mean over a long-tailed latency distribution
    describes a call nobody made.
    """

    model_config = ConfigDict(extra="forbid")

    samples: int = Field(ge=0, description="Turn-level latency samples behind these figures.")
    mean_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    calibration_verdict: Literal["PASS", "FAIL", "NOT_RUN"]
    calibration_report: str | None = Field(
        default=None, description="Path to the calibration report backing the verdict."
    )
    latency_definition: str = Field(
        default="agent_audio_first_byte.ts - caller_utterance.ts",
        description="Exactly which two trace events were subtracted.",
    )
    wer: float | None = Field(
        default=None, description="Word error rate as a fraction, if audio was scored."
    )
    wer_reference_words: int | None = Field(
        default=None, description="Reference word count — the denominator behind the WER."
    )
    estimated_timestamps_used: bool = Field(
        default=False,
        description=(
            "True if any figure above came from an event flagged `ts_estimated` by "
            "the driver. Should always be False: interpolated tool timestamps are "
            "for ordering, never for timing."
        ),
    )

    @property
    def trustworthy(self) -> bool:
        """Whether these figures may be quoted without a caveat."""
        return (
            self.calibration_verdict == "PASS"
            and self.samples > 0
            and not self.estimated_timestamps_used
        )


class FailureRecord(BaseModel):
    """One failure, with the quote that proves it.

    `evidence` is required and non-empty. A failure list without quotes cannot be
    triaged and cannot be checked: the reader has to take the harness's word for
    it, which is the position an evaluation report exists to get its reader out
    of. The quote should come from the trace — an agent utterance, a tool payload,
    a missing call — so that a disagreement is settled by reading the trace rather
    than by re-running the suite.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    contract: str = Field(min_length=1, description="Which check or judge failed.")
    evidence: str = Field(
        min_length=1, description="Verbatim quote or precise observation from the trace."
    )
    session_id: str | None = None
    trace_path: str | None = Field(
        default=None, description="Where the trace lives, so the claim can be reopened."
    )
    from_agent: str | None = Field(
        default=None, description="Handoff source, when the failure is on a transition."
    )
    to_agent: str | None = None
    note: str | None = Field(default=None, description="Diagnosis, if one is known.")

    def location(self) -> str:
        """Short human locator: scenario, and the handoff if there was one."""
        if self.from_agent and self.to_agent:
            return f"{self.scenario_id} [{self.from_agent} -> {self.to_agent}]"
        return self.scenario_id


class RunReport(BaseModel):
    """A whole run, ready to render as markdown or JSON.

    Build it from results, then call `to_markdown()`, `to_json()` or `write()`.
    The headline verdict is derived, never set: it is PASS only when every
    scenario is STABLE_PASS and every contract that ran never failed, so the
    verdict at the top of the document cannot drift from the tables below it.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Evaluation run report", min_length=1)
    subject: str = Field(
        default="system under test",
        description="What was evaluated, ideally with a version.",
    )
    run_label: str | None = Field(
        default=None,
        description=(
            "Caller-supplied label — a commit sha, a date, a model name. Passed "
            "in rather than generated so that rendering stays deterministic and a "
            "committed report diffs cleanly."
        ),
    )
    stability: list[StabilityVerdict] = Field(default_factory=list)
    contracts: list[ContractStat] = Field(default_factory=list)
    judges: list[JudgeSummary] = Field(default_factory=list)
    voice: VoiceMetrics | None = None
    failures: list[FailureRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    # ---------------------------------------------------------------- verdicts

    @property
    def summary(self) -> StabilitySummary:
        return summarise_stability(self.stability)

    @property
    def contract_failures(self) -> int:
        return sum(c.failures for c in self.contracts)

    @property
    def verdict(self) -> Literal["PASS", "FAIL"]:
        """PASS only if every scenario is STABLE_PASS and no contract failed.

        An empty report is a FAIL. Zero scenarios producing PASS is the single
        most dangerous default a harness can have: a misconfigured run that
        collected nothing would announce success.
        """
        if not self.stability and not self.contracts:
            return "FAIL"
        if any(not v.passed for v in self.stability):
            return "FAIL"
        if any(c.failures > 0 for c in self.contracts):
            return "FAIL"
        return "PASS"

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def headline(self) -> str:
        """The one line someone will read: verdict, counts, and the flaky caveat."""
        summary = self.summary
        parts = [
            f"{self.verdict}",
            f"{summary.stable_pass_rate_str} scenarios stable-pass",
        ]
        if summary.flaky:
            parts.append(f"{summary.flaky_rate_str} FLAKY (not a pass)")
        if self.contracts:
            parts.append(
                f"{format_rate(self.contract_failures, sum(c.applicable for c in self.contracts))} "
                "contract evaluations failed"
            )
        return " — ".join(parts)

    # ------------------------------------------------------------- self-audit

    def integrity_gaps(self) -> list[str]:
        """Where this report's own evidence is weaker than it looks.

        Printed as its own section. The gaps are the things a reader would
        otherwise have to know to ask about: a failure with no quote, a contract
        that never ran, a scenario measured once, a latency quoted without a
        calibration pass. Surfacing them costs the report some polish and buys it
        the only property that matters, which is that its claims are the size of
        its evidence.
        """
        gaps: list[str] = []

        if not self.stability:
            gaps.append(
                "no scenarios were run, so the verdict rests on the contract table "
                "alone and no stability claim is available"
            )

        undocumented = [v for v in self.stability if v.missing_evidence()]
        for verdict in undocumented:
            gaps.append(
                f"{verdict.scenario_id}: runs {verdict.missing_evidence()} failed with "
                "no evidence recorded — untriageable as written"
            )

        single_run = [v.scenario_id for v in self.stability if v.total_runs == 1]
        if single_run:
            gaps.append(
                f"{format_rate(len(single_run), len(self.stability))} scenarios ran "
                "once (k=1), so no instability could have been observed in them: "
                + ", ".join(sorted(single_run))
            )

        never_ran = [c.name for c in self.contracts if c.runs == 0]
        if never_ran:
            gaps.append(
                f"{format_rate(len(never_ran), len(self.contracts))} contracts were "
                "never evaluated and are neither passing nor failing: "
                + ", ".join(sorted(never_ran))
            )

        vacuous_everywhere = [
            c.name for c in self.contracts if c.runs > 0 and c.applicable == 0
        ]
        if vacuous_everywhere:
            gaps.append(
                f"{format_rate(len(vacuous_everywhere), len(self.contracts))} contracts "
                "ran but had nothing to assert on in any run, so they are skipped "
                "rather than passing: " + ", ".join(sorted(vacuous_everywhere))
            )

        partly_vacuous = [c for c in self.contracts if 0 < c.vacuous < c.runs]
        for contract in partly_vacuous:
            gaps.append(
                f"contract {contract.name} was vacuous on "
                f"{format_rate(contract.vacuous, contract.runs)} runs; its failure "
                "rate is quoted over the runs where it applied"
            )

        if self.voice is not None:
            if self.voice.calibration_verdict != "PASS":
                gaps.append(
                    "voice metrics are reported with calibration verdict "
                    f"{self.voice.calibration_verdict}: the timing figures below are "
                    "unproven until the calibration gate passes"
                )
            if self.voice.estimated_timestamps_used:
                gaps.append(
                    "a voice figure was derived from an event flagged `ts_estimated`; "
                    "interpolated timestamps are for ordering only"
                )

        live_judges = [j.name for j in self.judges if not j.replayed_from_fixture]
        if live_judges:
            gaps.append(
                "judge verdicts came from live calls rather than fixtures, so this "
                "run is not reproducible offline: " + ", ".join(sorted(live_judges))
            )

        for judge in self.judges:
            if judge.abstained:
                gaps.append(
                    f"judge {judge.name} abstained on {judge.abstention_rate.text} of "
                    "the runs it was given; those runs are unjudged, not passing"
                )
            if judge.calibration.n_labelled < 10:
                gaps.append(
                    f"judge {judge.name} is calibrated on only "
                    f"{judge.calibration.n_labelled} hand-labelled examples; its TPR "
                    "and TNR are indicative rather than measured"
                )

        return gaps

    # ---------------------------------------------------------------- renderers

    def to_markdown(self) -> str:
        """The full report as markdown. Deterministic: no clock, no environment."""
        summary = self.summary
        lines: list[str] = [
            f"# {self.title}",
            "",
            f"**Verdict: {self.verdict}** — {self.headline()}",
            "",
            f"- Subject: {self.subject}",
        ]
        if self.run_label:
            lines.append(f"- Run: {self.run_label}")
        lines.extend(
            [
                f"- Scenarios: {summary.scenarios}, "
                f"runs: {summary.total_runs} (k >= {summary.min_runs_per_scenario})",
                "- Every rate below is printed as `n/N (percent)`. A percentage "
                "without its denominator is a defect, not a style choice.",
                "",
                *self._stability_section(summary),
                *self._contract_section(),
                *self._judge_section(),
                *self._voice_section(),
                *self._failure_section(),
                *self._integrity_section(),
            ]
        )
        if self.notes:
            lines.extend(["## Notes", "", *[f"- {note}" for note in self.notes], ""])
        return "\n".join(lines)

    def _stability_section(self, summary: StabilitySummary) -> list[str]:
        lines = [
            "## Stability (pass^k)",
            "",
            "A scenario passes only if it passes **every** run. FLAKY is not a pass: "
            "it means the agent's behaviour on that scenario is not determined by "
            "the scenario.",
            "",
            f"- Stable pass: {summary.stable_pass_rate_str}",
            f"- Flaky: {summary.flaky_rate_str}",
            f"- Stable fail: {format_rate(summary.stable_fail, summary.scenarios)}",
            "",
        ]
        if not self.stability:
            return lines + ["_No scenarios were run._", ""]
        lines.extend(
            _table(
                ["scenario", "verdict", "passed", "flake rate", "k", "first evidence"],
                [
                    [
                        verdict.scenario_id,
                        verdict.verdict,
                        verdict.pass_rate_str,
                        verdict.flake_rate_str,
                        str(verdict.total_runs),
                        _cell(verdict.first_evidence()),
                    ]
                    for verdict in self.stability
                ],
            )
        )
        policies = {v.policy.describe() for v in self.stability}
        lines.extend(["", *[f"- Policy: {p}" for p in sorted(policies)], ""])
        return lines

    def _contract_section(self) -> list[str]:
        lines = ["## Contract failures", ""]
        if not self.contracts:
            return lines + ["_No deterministic contracts were evaluated._", ""]
        lines.append(
            "Deterministic checks over the trace. The denominator is the runs where "
            "the contract had something to assert on, so a contract that never ran — "
            "or ran and never applied — is visible as `0/0` rather than passing by "
            "silence. `vacuous` counts the runs it was skipped on."
        )
        lines.append("")
        lines.extend(
            _table(
                ["contract", "failures", "vacuous", "scenarios affected", "what it checks"],
                [
                    [
                        contract.name,
                        contract.failure_rate_str,
                        format_rate(contract.vacuous, contract.runs),
                        _cell(", ".join(sorted(contract.failing_scenarios))),
                        _cell(contract.description),
                    ]
                    for contract in self.contracts
                ],
            )
        )
        lines.append("")
        return lines

    def _judge_section(self) -> list[str]:
        lines = ["## Judge verdicts", ""]
        if not self.judges:
            return lines + ["_No model-graded checks were run._", ""]
        lines.append(
            "Each verdict is printed beside the judge's measured agreement with "
            "hand labels. A judge verdict without its TPR and TNR is not evidence, "
            "so `JudgeSummary` cannot be constructed without them."
        )
        lines.append("")
        lines.extend(
            _table(
                ["judge", "model", "flagged", "TPR", "TNR", "labelled n", "source"],
                [
                    [
                        judge.name,
                        judge.model,
                        judge.flag_rate.text,
                        judge.calibration.tpr.text,
                        judge.calibration.tnr.text,
                        str(judge.calibration.n_labelled),
                        "fixture" if judge.replayed_from_fixture else "live",
                    ]
                    for judge in self.judges
                ],
            )
        )
        lines.append("")
        for judge in self.judges:
            lines.append(
                f"- {judge.name}: missed {judge.calibration.false_negatives.text} of "
                f"labelled failures, wrongly flagged "
                f"{judge.calibration.false_positives.text} of labelled clean examples"
                + (f" (prompt {judge.prompt_id})" if judge.prompt_id else "")
            )
        lines.append("")
        return lines

    def _voice_section(self) -> list[str]:
        if self.voice is None:
            return []
        voice = self.voice
        lines = [
            "## Voice metrics",
            "",
            f"- Calibration gate: **{voice.calibration_verdict}**"
            + (f" ({voice.calibration_report})" if voice.calibration_report else ""),
            f"- Latency definition: `{voice.latency_definition}`",
            f"- Samples: {voice.samples}",
        ]
        for label, value in (
            ("mean", voice.mean_ms),
            ("p50", voice.p50_ms),
            ("p95", voice.p95_ms),
        ):
            if value is not None:
                lines.append(f"- Response latency {label}: {value:.1f} ms")
        if voice.wer is not None:
            words = voice.wer_reference_words
            denominator = f" over {words} reference words" if words else ""
            lines.append(f"- Word error rate: {voice.wer:.3f}{denominator}")
        if not voice.trustworthy:
            lines.append(
                "- **These figures are not certified**: the calibration gate did not "
                "pass, or no samples were collected. See the integrity section."
            )
        lines.append("")
        return lines

    def _failure_section(self) -> list[str]:
        lines = ["## Failures", ""]
        if not self.failures:
            return lines + ["_No failures recorded._", ""]
        lines.append(
            f"{len(self.failures)} recorded, each with the quote it was found in."
        )
        lines.append("")
        for index, failure in enumerate(self.failures, start=1):
            lines.append(f"### {index}. {failure.contract} — {failure.location()}")
            lines.append("")
            lines.append(f"> {failure.evidence}")
            lines.append("")
            details = []
            if failure.session_id:
                details.append(f"session `{failure.session_id}`")
            if failure.trace_path:
                details.append(f"trace `{failure.trace_path}`")
            if details:
                lines.append("- " + ", ".join(details))
            if failure.note:
                lines.append(f"- {failure.note}")
            lines.append("")
        return lines

    def _integrity_section(self) -> list[str]:
        gaps = self.integrity_gaps()
        lines = ["## Report integrity", ""]
        if not gaps:
            return lines + [
                "No gaps found: every failure carries evidence, every contract ran, "
                "every scenario was repeated, and any voice figures are calibrated.",
                "",
            ]
        lines.append(
            "Where this report's evidence is weaker than its tables imply. Listed "
            "because a report that hides its gaps gets trusted for more than it can "
            "support."
        )
        lines.append("")
        lines.extend(f"- {gap}" for gap in gaps)
        lines.append("")
        return lines

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict: the model, plus the derived figures a reader needs.

        Derived values are materialised rather than left as properties, so the
        JSON stands alone — a consumer never has to reimplement `pass_rate` or
        `verdict` to read the file, and can still recompute both from the counts
        that sit beside them.
        """
        return {
            "title": self.title,
            "subject": self.subject,
            "run_label": self.run_label,
            "verdict": self.verdict,
            "headline": self.headline(),
            "stability_summary": {
                **self.summary.model_dump(mode="json"),
                "stable_pass_rate": self.summary.stable_pass_rate_str,
            },
            "stability": [
                {
                    **verdict.model_dump(mode="json"),
                    "pass_rate": verdict.pass_rate_str,
                    "flake_rate": verdict.flake_rate_str,
                    "passed": verdict.passed,
                }
                for verdict in self.stability
            ],
            "contracts": [
                {
                    **c.model_dump(mode="json"),
                    "applicable": c.applicable,
                    "failure_rate": c.failure_rate_str,
                }
                for c in self.contracts
            ],
            "judges": [
                {
                    **j.model_dump(mode="json"),
                    "flag_rate": j.flag_rate.text,
                    "tpr": j.calibration.tpr.text,
                    "tnr": j.calibration.tnr.text,
                }
                for j in self.judges
            ],
            "voice": (
                {**self.voice.model_dump(mode="json"), "trustworthy": self.voice.trustworthy}
                if self.voice is not None
                else None
            ),
            "failures": [f.model_dump(mode="json") for f in self.failures],
            "integrity_gaps": self.integrity_gaps(),
            "notes": self.notes,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """The report as JSON text, keys sorted so the file diffs cleanly."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"

    def write(self, out_dir: str | Path, *, stem: str = "run_report") -> dict[str, Path]:
        """Write `<stem>.md` and `<stem>.json` into `out_dir`; return the paths."""
        return write_report(self, out_dir, stem=stem)


def write_report(
    report: RunReport, out_dir: str | Path, *, stem: str = "run_report"
) -> dict[str, Path]:
    """Write a report as markdown and JSON side by side.

    Both formats, always: the markdown is what a human reviews in a pull request,
    the JSON is what a dashboard or a regression check reads. Writing only one
    forces the other consumer to parse prose or to re-run the suite.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    md_path = directory / f"{stem}.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")

    json_path = directory / f"{stem}.json"
    json_path.write_text(report.to_json(), encoding="utf-8")

    return {"markdown": md_path, "json": json_path}


# --------------------------------------------------------------------------- #
# Small rendering helpers
# --------------------------------------------------------------------------- #


def _cell(value: str | None) -> str:
    """A table cell that is never empty and never breaks the pipe layout."""
    if not value:
        return "—"
    return value.replace("|", "\\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """A markdown table as lines. No column-width padding: git diffs it better."""
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return out
