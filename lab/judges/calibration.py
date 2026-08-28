"""Judge calibration: measuring the measuring instrument.

WHAT THIS DEMONSTRATES
----------------------
An LLM judge is a classifier of unknown accuracy until somebody measures it
against labels a human wrote. This module is that measurement, and it is the
reason this package exists: writing a judge is an afternoon, and every eval
framework has one. Knowing its **true-positive and true-negative rate on the
population it will actually run on** is the part that makes its verdicts
admissible, and almost nothing ships it.

The output is a `CalibrationReport`: the full confusion matrix, TPR, TNR,
precision, recall, F1, raw agreement, Cohen's kappa, and — the part a human
actually uses — every individual disagreement, listed with the judge's own
critique next to the human's note, so the person who labelled the set can read
the twelve items that matter instead of the two hundred that agreed.

WHY RAW AGREEMENT IS REPORTED, AND WHY IT IS NOT ENOUGH
-------------------------------------------------------
Raw agreement — the fraction of items where judge and human said the same thing
— is the number everyone quotes and the number that flatters hardest exactly
when it matters most: on imbalanced data.

Take a realistic set: 20 sessions, 2 of which contain the defect. A judge that
answers "no defect" every single time, and is therefore worth nothing, scores:

    raw agreement    18/20 = 0.900      <- looks like a good judge
    true positive rate  0/2 = 0.000     <- it has never once found the defect
    Cohen's kappa           = 0.000     <- exactly chance
    precision           0/0 = undefined <- it never made a positive claim

That is the whole argument for chance correction. Kappa asks how much of the
agreement exceeds what two graders with the same marginal habits would hit by
luck; when a judge has no discrimination, kappa is 0 no matter how skewed the
class balance is. So both are reported, always, side by side — raw agreement
because it is what people ask for, kappa because it is what the number means.

Kappa has its own failure mode and it is stated in the report rather than hidden:
kappa depends on prevalence, so the same judge measured on a 10%-defect set and a
50%-defect set produces different kappas. Kappa is not comparable across
differently-balanced label sets; TPR and TNR are, which is why the thresholds in
`lab.judges.registry` gate on TPR and TNR and not on kappa.

COMPARING TWO VERSIONS: THE TEST, AND THE FLOOR UNDER IT
--------------------------------------------------------
`compare_reports` puts two calibrations of the same judge side by side, and a
delta table alone answers only "did v2 beat v1" — never "is that difference
distinguishable from chance". The comparison is **paired**: the same items, the
same labels, two prompts. So the test is McNemar's over the discordant items,
computed exactly rather than by the chi-square approximation, which is not
trustworthy at these counts.

The more useful half is the **detectability floor**. With `d` discordant pairs
all pointing one way the exact two-sided p is `2/2**d`, which depends on `d`
alone — not on the size of the set, not on either version's accuracy. At
alpha = 0.05 that puts a hard floor of **6** under every paired comparison: five
items moving together gives p = 0.0625 and publishes nothing. Printing it says
in advance what this label set *cannot* prove — that a v3 fixing three items and
breaking none is unpublishable at p = 0.250 however real the improvement is —
which is a more honest thing to hand a reader than a p-value on its own, and it
names the fix (more labelled items) rather than inviting a better prompt.

WHY EVERY RATE CARRIES ITS NUMERATOR AND DENOMINATOR
----------------------------------------------------
"TNR 0.94" and "TNR 15/16" are the same number and not the same claim. The second
tells you the measurement rests on sixteen items, so one relabelled item moves it
by six points. A bare percentage over a small set invites a confidence it has not
earned, so `Rate` refuses to print without its fraction, and division by zero
prints `undefined (0/0)` instead of raising or, worse, quietly reporting 0.0.

POSITIVE CLASS
--------------
For a detector, the interesting class is "the defect is present", which is a
judge verdict of **fail**. So `positive_label` defaults to `"fail"`:

    TP  human says fail, judge says fail      a defect found
    FN  human says fail, judge says pass      a defect MISSED  <- the dangerous cell
    FP  human says pass, judge says fail      a false alarm; a human's time wasted
    TN  human says pass, judge says pass      agreement on clean behaviour

    TPR = recall = sensitivity = TP/(TP+FN)   how much of the problem it catches
    TNR = specificity          = TN/(TN+FP)   how much of the clean traffic it leaves alone

Both are needed, and a gate on one alone is trivially gamed: a judge that always
says "fail" has TPR 1.00 and is useless; a judge that always says "pass" has TNR
1.00 and is useless. The default thresholds require both.

The labels and the verdicts share one vocabulary (`Label` = "pass" | "fail"), so
nothing in this module inverts a boolean. Inverted-polarity bugs in agreement
code are common, silent, and produce a report that is exactly wrong rather than
noisy — the cheapest defence is to never perform the inversion.

CALIBRATE ON THE POPULATION THE JUDGE WILL SEE
----------------------------------------------
A judge calibrated on a set drawn from a different distribution than it runs on
has an unknown error rate, whatever the report says. If the judge is the second
stage of a cascade — a deterministic check selects candidates, the judge grades
them — then the labelled set must be drawn from the *post-filter* population, not
from all traffic. `lab.judges.hallucinated_confirmation` does exactly this and
documents it; it is the difference between a calibration and a demo.

CONFIDENCE INTERVALS — A POSITION THAT WAS HELD HERE, AND IS REVERSED
---------------------------------------------------------------------
This docstring used to decline confidence intervals, in these words:

    "No confidence intervals. With a few dozen items the honest statement is the
    fraction itself — a Wilson interval on 8/8 would imply a precision the set
    cannot support, and quoting one would suggest the sample size is adequate
    when the real answer is 'label more items'."

That was backwards, and it is recorded here rather than deleted because the
reversal is the useful part. **`TPR 1.000` is the number that implies a precision
the set cannot support.** It reads as "this judge does not miss". The interval is
what refuses that reading: `TPR 1.000 (8/8), 95% CI [0.676, 1.000]` says, in the
same breath, that no error was observed and that the true rate could be near two
thirds. Far from suggesting the sample is adequate, an interval is the only line
in the report whose width is a direct measure of how inadequate it is — and the
old argument's own conclusion, *label more items*, is exactly what the interval
quantifies: at the default 0.85 threshold, a lower bound that clears the gate
needs 22 consecutive correct answers in the gated class
(`lab.stats.min_trials_for_lower_bound`). The set has 8 positives.

So every rate here prints its Wilson interval, and the report states plainly
whether the gate is cleared by the point estimate, by the lower bound, or by
neither. `CalibrationThresholds.gate_on` chooses which one the gate actually
scores; it defaults to `"point"`, and the reasoning for that default — including
the fact that `"wilson_lower"` would fail every judge committed to this
repository — is in `CalibrationThresholds` and printed in every report.

Two things an interval here does *not* cover, both stated in the artefact rather
than left for the reader:

*   **It is sampling error over items only.** It assumes the judge's answer for a
    given item is fixed. `SelfConsistency` shows that it is not. The second source
    of uncertainty is measured separately and printed beside the first as a band
    across identical runs (`ReplicateBands`); the two are never added, because
    adding them would invent a combined distribution nobody measured.
*   **It says nothing about label quality.** This measures a judge against a label
    set, and if the label set is noisy that noise is charged to the judge. No
    inter-*human* agreement is measured. Labelling the same items twice is the
    right next step and is out of scope here.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, computed_field

from lab.judges.judge import Judge, Label, Verdict
from lab.stats import (
    format_interval,
    min_trials_for_lower_bound,
    rule_of_three_upper_bound,
    wilson_interval,
)
from lab.trace.schema import Trace

__all__ = [
    "LabelledTrace",
    "Rate",
    "ConfusionMatrix",
    "ItemOutcome",
    "Disagreement",
    "CalibrationThresholds",
    "CalibrationReport",
    "calibrate",
    "load_labels",
    "write_labels",
    "labels_digest",
    "compare_reports",
    "PairedComparison",
    "mcnemar",
    "exact_mcnemar_p",
    "detectability_floor",
    "SelfConsistency",
    "ItemVerdictRuns",
    "self_consistency",
    "traces_of",
]

Cell = Literal["tp", "fp", "fn", "tn"]


# --------------------------------------------------------------------------- #
# The labelled set
# --------------------------------------------------------------------------- #


class LabelledTrace(BaseModel):
    """One trace, one human label, and the reason the human gave it.

    `note` is not decoration. When the judge disagrees with the label, the note is
    what tells a reviewer whether the judge is wrong or the label is — and on a
    hand-labelled set of a few dozen items, mislabels are the single largest
    source of apparent judge error. Requiring a note at label time costs a
    sentence and saves the argument.

    The trace is stored inline rather than referenced by path so a label file is
    self-contained: one file holds the evidence, the label and the reasoning, and
    it can be reviewed in a diff without chasing sidecar files that may have moved
    on since the label was written.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(description="Stable id for this item; keys the recording too.")
    label: Label = Field(description='The human verdict: "pass" or "fail".')
    trace: Trace
    note: str = Field(default="", description="Why the human labelled it that way.")
    labeller: str = Field(default="", description="Who labelled it.")

    @property
    def is_positive(self) -> bool:
        """True when this item is the defect class under the default polarity."""
        return self.label == "fail"


def write_labels(items: Sequence[LabelledTrace], path: str | Path) -> Path:
    """Write a labelled set as JSONL, one item per line, key-sorted.

    Key-sorted and one-per-line so that re-labelling a single item produces a
    one-line diff in review. A label set is data a human is accountable for; it
    should read like a document, not like a serialised object graph.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in items
    ]
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def load_labels(path: str | Path) -> list[LabelledTrace]:
    """Read a labelled set written by `write_labels`."""
    source = Path(path)
    items: list[LabelledTrace] = []
    with source.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                items.append(LabelledTrace.model_validate(json.loads(stripped)))
            except Exception as exc:  # noqa: BLE001 - re-raised with location
                raise ValueError(
                    f"{source}:{lineno}: not a valid LabelledTrace: {exc}"
                ) from exc
    return items


def labels_digest(items: Sequence[LabelledTrace]) -> str:
    """A stable digest of *which items carry which labels*.

    Stamped into every report so a report can be tied to the label set it was
    computed on. Relabel one item and the digest moves, which is how a stale
    report gets caught instead of being quoted for another six months. The digest
    covers item ids and labels only — editing a note does not invalidate a
    measurement, because notes are not inputs to it.
    """
    payload = ";".join(f"{item.item_id}={item.label}" for item in sorted(items, key=lambda i: i.item_id))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Rates that cannot be printed without their fraction
# --------------------------------------------------------------------------- #


class Rate(BaseModel):
    """A ratio of counts, which always knows how many items it stands on.

    Every rate in this repo is printed as `value (numerator/denominator)`. An
    undefined rate — no items in the denominator — prints `undefined (0/0)` and
    has `value is None`. That case is common and meaningful: precision is
    genuinely undefined for a judge that never returned a positive, and reporting
    it as 0.0 would claim the judge made positive predictions and got them all
    wrong.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def value(self) -> float | None:
        """The ratio, or None when the denominator is zero."""
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    @property
    def defined(self) -> bool:
        return self.denominator > 0

    def __str__(self) -> str:
        if self.value is None:
            return f"undefined ({self.numerator}/{self.denominator})"
        return f"{self.value:.3f} ({self.numerator}/{self.denominator})"

    def __repr__(self) -> str:
        return f"Rate(name={self.name!r}, {self})"

    # ------------------------------------------------------- the interval

    def interval(self, confidence: float = 0.95) -> tuple[float, float] | None:
        """The Wilson score interval, or None when the denominator is zero.

        Deliberately a method and not a `computed_field`: an interval is a
        function of the two counts already serialised, so storing it would put a
        derived number in the JSON that a reader could not check against anything.
        The rule this repository follows everywhere else — a summary that cannot
        be recomputed from the counts is a claim rather than a result — applies to
        its own confidence intervals too.
        """
        if self.denominator == 0:
            return None
        return wilson_interval(
            self.numerator, self.denominator, confidence=confidence
        )

    def interval_text(self, confidence: float = 0.95) -> str:
        """`[0.676, 1.000]`, or `undefined` on an empty denominator."""
        return format_interval(self.interval(confidence))

    def with_interval(self, confidence: float = 0.95) -> str:
        """`1.000 (8/8) 95% CI [0.676, 1.000]` — the whole claim on one line."""
        return f"{self} {confidence:.0%} CI {self.interval_text(confidence)}"

    def zero_error(self) -> bool:
        """True when every trial in the denominator went the right way.

        The case the rule of three exists for, and the case a point estimate
        flatters hardest.
        """
        return self.denominator > 0 and self.numerator == self.denominator

    def rule_of_three_text(self) -> str | None:
        """The 3/n sentence, or None when there was an observed error.

        Returned rather than printed so the caller decides where it goes; the
        reports put it under the rates table, once per rate that earned it.
        """
        if not self.zero_error():
            return None
        bound = rule_of_three_upper_bound(self.denominator)
        return (
            f"{self.name}: 0 errors in {self.denominator}, so the 95% upper bound "
            f"on the true error rate is about 3/{self.denominator} = {bound:.3f}"
        )


class ConfusionMatrix(BaseModel):
    """The 2x2 table. Everything else in the report is a function of these four.

    Reported in full, and first, because a summary statistic that cannot be
    recomputed from the counts is a claim rather than a result — and because
    which *kind* of error a judge makes changes what you do about it. Same F1,
    different cells, different decision: a judge that misses defects needs a
    better prompt; a judge that false-alarms needs a tighter rubric or a cheaper
    human review step.
    """

    model_config = ConfigDict(extra="forbid")

    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_negative: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def n(self) -> int:
        return (
            self.true_positive
            + self.false_positive
            + self.false_negative
            + self.true_negative
        )

    def grid_lines(self) -> list[str]:
        """The matrix as a small fixed-width table, human labels across the top."""
        tp, fp, fn, tn = (
            self.true_positive,
            self.false_positive,
            self.false_negative,
            self.true_negative,
        )
        width = max(len(str(v)) for v in (tp, fp, fn, tn, self.n)) + 1
        return [
            f"{'':>14}  {'human: fail':>{width + 11}}  {'human: pass':>{width + 11}}",
            f"{'judge: fail':>14}  {('TP ' + str(tp)):>{width + 11}}  {('FP ' + str(fp)):>{width + 11}}",
            f"{'judge: pass':>14}  {('FN ' + str(fn)):>{width + 11}}  {('TN ' + str(tn)):>{width + 11}}",
        ]


class ItemOutcome(BaseModel):
    """Per-item result, so the matrix can be recomputed from the report alone."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    human_label: Label
    judge_label: Label
    cell: Cell
    parse_error: bool = False


class Disagreement(BaseModel):
    """One item where the judge and the human differ, with both sides' reasoning.

    The most useful section of any calibration report, and the reason this whole
    module produces an object rather than a number: a rate tells you the judge is
    wrong 14% of the time, and this tells you *how* it is wrong, which is the only
    input a prompt rewrite can actually use.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: Literal["false_negative", "false_positive"]
    human_label: Label
    judge_label: Label
    human_note: str = ""
    judge_critique: str = ""
    judge_evidence: str | None = None
    parse_error: bool = False

    @property
    def severity_rank(self) -> int:
        """False negatives sort first: a missed defect is silent, a false alarm is not."""
        return 0 if self.kind == "false_negative" else 1


# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #


class CalibrationThresholds(BaseModel):
    """What a judge must clear to be allowed to gate anything.

    Defaults are 0.85 on both TPR and TNR. Both, because either alone is trivially
    satisfied by a constant answer (see the module docstring). 0.85 is a
    convention rather than a discovery, which is why it is a parameter that gets
    printed next to the verdict — a threshold nobody can see is not a standard.

    `min_items` exists because a rate over five items is not a measurement: 5/5
    and 40/40 both print 1.000, and only one of them survives a single relabel.

    `max_parse_error_rate` defaults to 0: any unparseable judge output is a broken
    output contract, and under `strict=False` those items were forced to FAIL,
    which *inflates* TPR. A judge whose provider is returning junk can therefore
    look like a better detector than it is — so the gate refuses it outright
    rather than scoring it.

    There is deliberately no default kappa threshold. Kappa is prevalence
    dependent, so a fixed minimum would pass or fail the same judge depending on
    how the label set happened to be balanced. Set `min_kappa` yourself when
    comparing judges on one fixed set.

    WHICH NUMBER THE GATE SCORES, AND WHY THE DEFAULT IS THE WEAKER ONE
    -------------------------------------------------------------------
    `gate_on` chooses between the point estimate and the Wilson lower bound, and
    the choice is printed in every report rather than being an implementation
    detail, because the two answer different questions:

        "point"         is the measured rate at least 0.85?
        "wilson_lower"  is the rate at least 0.85 with 95% confidence?

    The second is the stronger claim and the honest one, and it is **not** the
    default. The reason is a number, not a preference. With a perfect score the
    95% lower bound clears 0.85 only from 22 trials upward, so the default gate
    scored on the lower bound would fail every judge committed to this repository
    — 8 positives and 16 negatives on `hallucinated_confirmation`, 15 and 12 on
    the advisory scorer — none of which regressed, and none of which has been
    measured on enough items to demonstrate the threshold either way. Switching
    the default would not raise the standard; it would replace a gate that passes
    on a point estimate with a gate that nothing can pass, which gets deleted
    within a week and then guards nothing.

    What is *not* acceptable is leaving the reader to assume the gate stands on
    more than it does, so both numbers are printed next to the verdict and the
    report says in words which one it was scored on. Setting
    `gate_on="wilson_lower"` is a one-word change for anyone who wants the
    stronger gate, and the label sets that would clear it are the deliverable
    that earns it.
    """

    model_config = ConfigDict(extra="forbid")

    min_tpr: float = Field(default=0.85, ge=0.0, le=1.0)
    min_tnr: float = Field(default=0.85, ge=0.0, le=1.0)
    min_items: int = Field(default=10, ge=1)
    max_parse_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    min_kappa: float | None = Field(default=None, ge=-1.0, le=1.0)
    gate_on: Literal["point", "wilson_lower"] = Field(
        default="point",
        description=(
            "Which figure TPR and TNR are scored on: the point estimate, or the "
            "lower limit of its Wilson interval. See the class docstring for why "
            "the weaker one is the default."
        ),
    )
    confidence: float = Field(
        default=0.95,
        gt=0.0,
        lt=1.0,
        description="Confidence level for every interval printed or gated on.",
    )

    def describe(self) -> str:
        parts = [
            f"TPR >= {self.min_tpr:.2f}",
            f"TNR >= {self.min_tnr:.2f}",
            f"n >= {self.min_items}",
            f"parse errors <= {self.max_parse_error_rate:.0%}",
        ]
        if self.min_kappa is not None:
            parts.append(f"kappa >= {self.min_kappa:.2f}")
        scored = (
            "the point estimate"
            if self.gate_on == "point"
            else f"the {self.confidence:.0%} Wilson lower bound"
        )
        parts.append(f"scored on {scored}")
        return ", ".join(parts)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


class CalibrationReport(BaseModel):
    """A judge's measured agreement with human labels, in full.

    Distinct from `lab.voice.calibration.CalibrationReport`, which calibrates the
    *timing* instrument. Same idea applied twice: measure the instrument, publish
    the evidence, and gate on the result.
    """

    model_config = ConfigDict(extra="forbid")

    judge: str
    prompt_version: str
    model: str
    prompt_sha256: str
    positive_label: Label
    labels_sha256: str

    confusion: ConfusionMatrix
    prevalence: Rate
    true_positive_rate: Rate
    true_negative_rate: Rate
    precision: Rate
    recall: Rate
    f1: Rate
    raw_agreement: Rate

    cohens_kappa: float | None = Field(
        description="Chance-corrected agreement; None when it is undefined (no variance)."
    )
    kappa_observed_agreement: float
    kappa_expected_agreement: float

    parse_errors: int = 0
    items: list[ItemOutcome] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------- accessors

    @property
    def n(self) -> int:
        return self.confusion.n

    @property
    def parse_error_rate(self) -> Rate:
        return Rate(name="parse error rate", numerator=self.parse_errors, denominator=self.n)

    def rates(self) -> list[Rate]:
        """Every rate, in reporting order."""
        return [
            self.true_positive_rate,
            self.true_negative_rate,
            self.precision,
            self.recall,
            self.f1,
            self.raw_agreement,
            self.prevalence,
        ]

    # ------------------------------------------------------------------ gate

    def meets(
        self, thresholds: CalibrationThresholds | None = None
    ) -> tuple[bool, list[str]]:
        """Score the report against thresholds.

        Returns `(ok, failures)` where each failure is a sentence naming the
        number, the threshold and the fraction behind it — the text a CI log needs
        to be actionable without opening the JSON.

        An undefined rate fails rather than passing. A judge that never returned a
        positive has an undefined precision and has demonstrated nothing; treating
        "undefined" as "fine" is how a silent judge gets through a gate.

        `thresholds.gate_on` decides whether TPR and TNR are scored on the point
        estimate or on the Wilson lower bound. Under `"wilson_lower"` the failure
        sentence names both numbers, because "TPR 1.000 (8/8) failed" without the
        bound that failed it reads like a bug in the gate.
        """
        thr = thresholds if thresholds is not None else CalibrationThresholds()
        failures: list[str] = []

        if self.n < thr.min_items:
            failures.append(
                f"calibrated on only {self.n} items, below the minimum of {thr.min_items}"
            )

        for rate, minimum, name in (
            (self.true_positive_rate, thr.min_tpr, "TPR"),
            (self.true_negative_rate, thr.min_tnr, "TNR"),
        ):
            if rate.value is None:
                failures.append(
                    f"{name} is undefined ({rate.numerator}/{rate.denominator}): the label "
                    f"set contains no items of that class, so the judge has not been "
                    f"measured on it"
                )
            elif thr.gate_on == "wilson_lower":
                bounds = rate.interval(thr.confidence)
                assert bounds is not None  # a defined rate has a denominator
                lower = bounds[0]
                if lower >= minimum:
                    continue
                if rate.value >= minimum:
                    failures.append(
                        f"{name} {rate} has a {thr.confidence:.0%} Wilson lower bound "
                        f"of {lower:.3f}, below the required {minimum:.2f}: the point "
                        f"estimate clears the threshold and the evidence does not"
                    )
                else:
                    failures.append(
                        f"{name} {rate} is below the required {minimum:.2f} on both the "
                        f"point estimate and the {thr.confidence:.0%} Wilson lower "
                        f"bound ({lower:.3f})"
                    )
            elif rate.value < minimum:
                failures.append(
                    f"{name} {rate} is below the required {minimum:.2f}"
                )

        per = self.parse_error_rate
        if per.value is not None and per.value > thr.max_parse_error_rate:
            failures.append(
                f"parse error rate {per} exceeds {thr.max_parse_error_rate:.0%}: the "
                "judge's output contract is broken, and failed-closed items inflate TPR"
            )

        if thr.min_kappa is not None:
            if self.cohens_kappa is None:
                failures.append(
                    "Cohen's kappa is undefined (no variance in labels or verdicts), so "
                    f"the required kappa >= {thr.min_kappa:.2f} cannot be demonstrated"
                )
            elif self.cohens_kappa < thr.min_kappa:
                failures.append(
                    f"Cohen's kappa {self.cohens_kappa:.3f} is below the required "
                    f"{thr.min_kappa:.2f}"
                )

        return (not failures), failures

    def passes(self, thresholds: CalibrationThresholds | None = None) -> bool:
        return self.meets(thresholds)[0]

    # -------------------------------------------------------------- printing

    def summary_line(self) -> str:
        return (
            f"{self.judge} {self.prompt_version}: "
            f"TPR {self.true_positive_rate}, TNR {self.true_negative_rate}, "
            f"kappa {_fmt_kappa(self.cohens_kappa)}, "
            f"raw agreement {self.raw_agreement}, n={self.n}"
        )

    def to_text(self) -> str:
        """A fixed-width rendering for terminals and CI logs."""
        lines = [
            f"Judge calibration: {self.judge} {self.prompt_version}",
            f"  model            : {self.model}",
            f"  prompt sha256    : {self.prompt_sha256[:12]}",
            f"  labels sha256    : {self.labels_sha256[:12]}",
            f"  positive class   : judge says {self.positive_label!r}",
            "",
            *[f"  {line}" for line in self.confusion.grid_lines()],
            "",
        ]
        width = max(len(rate.name) for rate in self.rates())
        value_width = max(len(str(rate)) for rate in self.rates())
        for rate in self.rates():
            cell = self.interval_cell(rate)
            suffix = (
                f"95% CI {cell}"
                if cell.startswith("[") or cell == "undefined"
                else f"no interval — {cell}"
            )
            lines.append(f"  {rate.name:<{width}} : {str(rate):<{value_width}}  {suffix}")
        lines.append(f"  {'Cohen kappa':<{width}} : {_fmt_kappa(self.cohens_kappa)}")
        lines.append(
            f"  {'':<{width}}   (observed {self.kappa_observed_agreement:.3f}, "
            f"expected by chance {self.kappa_expected_agreement:.3f})"
        )
        if self.parse_errors:
            lines.append(f"  {'parse errors':<{width}} : {self.parse_error_rate}")
        if self.disagreements:
            lines.append("")
            lines.append(f"  {len(self.disagreements)} disagreement(s):")
            for item in self.disagreements:
                lines.append(
                    f"    [{item.kind}] {item.item_id}: "
                    f"human={item.human_label} judge={item.judge_label}"
                )
        return "\n".join(lines)

    # --------------------------------------------------- intervals and the gate

    def interval_cell(self, rate: Rate, confidence: float = 0.95) -> str:
        """The interval for one reported rate, or a refusal for the ones it fits.

        F1 is printed as a fraction like the others and is **not** a binomial
        proportion: its numerator is `2*TP` and its denominator `2*TP + FP + FN`,
        so the same item is counted twice and the trials are not independent. A
        Wilson interval on it would be arithmetic applied to the wrong quantity,
        which is the failure mode this whole section exists to argue against, so
        the cell says so instead.

        TPR, TNR, raw agreement and prevalence are proportions over denominators
        fixed by the label set, which is exactly the case Wilson covers. Precision
        is the one to read with care: its denominator is the judge's own positive
        count, which varies from run to run, so its interval is conditional on
        that count rather than on anything the label set fixed.
        """
        if rate is self.f1:
            return "not a proportion"
        return rate.interval_text(confidence)

    def gate_evidence(
        self, thresholds: CalibrationThresholds | None = None
    ) -> list[str]:
        """The two gated rates, their intervals, and which of them the gate read.

        Markdown lines. The section exists because a threshold cleared by a point
        estimate whose lower bound sits below that threshold is not clearing the
        bar it claims to clear, and the only defensible thing to do about it is to
        show both numbers and say which one was scored.
        """
        thr = thresholds if thresholds is not None else CalibrationThresholds()
        conf = thr.confidence
        gated = (
            (self.true_positive_rate, thr.min_tpr, "TPR"),
            (self.true_negative_rate, thr.min_tnr, "TNR"),
        )
        lines = [
            f"## The interval, and which number the gate is standing on",
            "",
            f"Gate: {thr.describe()}.",
            "",
            f"| gated rate | point estimate | {conf:.0%} Wilson CI | clears on the "
            "point? | clears on the lower bound? |",
            "|---|---|---|---|---|",
        ]
        clears_point = True
        clears_lower = True
        for rate, minimum, name in gated:
            bounds = rate.interval(conf)
            if bounds is None:
                clears_point = clears_lower = False
                lines.append(
                    f"| {name} >= {minimum:.2f} | undefined "
                    f"({rate.numerator}/{rate.denominator}) | undefined | no | no |"
                )
                continue
            assert rate.value is not None
            point_ok = rate.value >= minimum
            lower_ok = bounds[0] >= minimum
            clears_point = clears_point and point_ok
            clears_lower = clears_lower and lower_ok
            lines.append(
                f"| {name} >= {minimum:.2f} | {rate} | {format_interval(bounds)} | "
                f"{'yes' if point_ok else '**no**'} | "
                f"{'yes' if lower_ok else '**no**'} |"
            )
        lines.append("")

        rule_of_three = [
            text for rate, _, _ in gated if (text := rate.rule_of_three_text())
        ]
        if rule_of_three:
            lines.append(
                "Rule of three, the same fact in the form that is easier to hold on to:"
            )
            lines.append("")
            lines += [f"- {text}" for text in rule_of_three]
            lines.append("")

        if clears_point and not clears_lower:
            needed = min_trials_for_lower_bound(
                max(thr.min_tpr, thr.min_tnr), confidence=conf
            )
            lines += [
                "**The gate is cleared by the point estimate and not by the "
                "evidence.** That is stated rather than hidden, and it is not a "
                "reason to abandon the gate: it is the reason the interval is "
                "printed next to it. A perfect score clears a "
                f"{max(thr.min_tpr, thr.min_tnr):.2f} threshold on its {conf:.0%} "
                f"lower bound only from **{needed}** trials upward, so the fix is "
                "more labelled items in the class that falls short — not a weaker "
                "threshold, and not a better prompt.",
                "",
            ]
        elif clears_lower:
            lines += [
                f"Both gated rates clear the threshold on the {conf:.0%} lower "
                "bound as well as on the point estimate, so the gate verdict does "
                "not depend on which of the two is scored.",
                "",
            ]

        if thr.gate_on == "point":
            lines += [
                "This report was scored on the point estimate. "
                "`CalibrationThresholds(gate_on='wilson_lower')` scores the lower "
                "bound instead; it is not the default because at these set sizes "
                "it fails every judge in this repository, none of which regressed "
                "— see the class docstring.",
                "",
            ]
        else:
            lines += [
                "This report was scored on the Wilson lower bound, which is the "
                "stronger claim: the threshold is met with "
                f"{conf:.0%} confidence and not merely on the observed fraction.",
                "",
            ]
        return lines

    def to_markdown(self) -> str:
        """A self-contained markdown report: matrix, rates, and every disagreement."""
        c = self.confusion
        lines: list[str] = [
            f"# Judge calibration — `{self.judge}` prompt `{self.prompt_version}`",
            "",
            f"- Model: `{self.model}`",
            f"- Prompt sha256: `{self.prompt_sha256[:16]}`",
            f"- Label set sha256: `{self.labels_sha256[:16]}` ({self.n} items)",
            f"- Positive class: a judge verdict of **{self.positive_label}** "
            f"(prevalence {self.prevalence})",
            "",
            "## Confusion matrix",
            "",
            "| | human: fail | human: pass |",
            "|---|---|---|",
            f"| **judge: fail** | TP {c.true_positive} | FP {c.false_positive} |",
            f"| **judge: pass** | FN {c.false_negative} | TN {c.true_negative} |",
            "",
            "## Rates",
            "",
            "| metric | value | numerator / denominator | 95% Wilson CI |",
            "|---|---|---|---|",
        ]
        for rate in self.rates():
            value = "undefined" if rate.value is None else f"{rate.value:.3f}"
            lines.append(
                f"| {rate.name} | {value} | {rate.numerator} / {rate.denominator} | "
                f"{self.interval_cell(rate)} |"
            )
        lines.append(
            f"| Cohen's kappa | {_fmt_kappa(self.cohens_kappa)} | "
            f"observed {self.kappa_observed_agreement:.3f}, "
            f"chance {self.kappa_expected_agreement:.3f} | not a proportion |"
        )
        if self.parse_errors:
            per = self.parse_error_rate
            lines.append(
                f"| parse errors | {per.value:.3f} | {per.numerator} / {per.denominator} "
                f"| {per.interval_text()} |"
            )

        lines += [
            "",
            "Raw agreement is reported next to kappa deliberately: raw agreement "
            "flatters a judge on imbalanced data, because always answering with the "
            "majority class scores the majority fraction. Kappa subtracts the "
            "agreement two graders with these marginals would reach by chance.",
            "",
            "The interval is the Wilson score interval at 95%, computed from the two "
            "counts in the row beside it and from nothing else, so a reader can "
            "recheck it. It is sampling error over items only: it assumes the judge "
            "would give the same answer on a second run, which is a separate "
            "question with a separate measurement. No interval is given for Cohen's "
            "kappa or for F1 — neither is a proportion of independent trials, and a "
            "binomial interval on either would be arithmetic applied to the wrong "
            "quantity. Precision is the one to read with care: its denominator is "
            "the judge's own positive count rather than a class the label set "
            "fixed, so its interval is conditional on that count.",
            "",
        ]
        lines += self.gate_evidence()
        lines += [
            "## Disagreements",
            "",
        ]
        if not self.disagreements:
            lines.append("None — the judge matched every human label on this set.")
        else:
            lines.append(
                "False negatives first: a missed defect is silent, a false alarm "
                "announces itself to whoever reads the report."
            )
            lines.append("")
            for item in self.disagreements:
                lines += [
                    f"### `{item.item_id}` — {item.kind.replace('_', ' ')}",
                    "",
                    f"- human: **{item.human_label}** — {item.human_note or '(no note)'}",
                    f"- judge: **{item.judge_label}** — {item.judge_critique or '(no critique)'}",
                ]
                if item.judge_evidence:
                    lines.append(f"- judge quoted: “{item.judge_evidence}”")
                if item.parse_error:
                    lines.append("- **the judge's output was unparseable and failed closed**")
                lines.append("")

        if self.notes:
            lines += ["## Notes", ""]
            lines += [f"- {note}" for note in self.notes]
            lines.append("")
        return "\n".join(lines)

    def write(self, out_dir: str | Path, *, stem: str | None = None) -> dict[str, Path]:
        """Write the report as JSON and markdown. Returns the paths written.

        The JSON carries every per-item outcome, so a reader can rebuild the
        confusion matrix and check the arithmetic without rerunning the judge.
        """
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        base = stem or f"calibration_{self.prompt_version}"

        json_path = directory / f"{base}.json"
        json_path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        md_path = directory / f"{base}.md"
        md_path.write_text(self.to_markdown(), encoding="utf-8")
        return {"json": json_path, "markdown": md_path}

    def __repr__(self) -> str:
        return f"CalibrationReport({self.summary_line()})"


def _fmt_kappa(kappa: float | None) -> str:
    return "undefined" if kappa is None else f"{kappa:.3f}"


# --------------------------------------------------------------------------- #
# The measurement
# --------------------------------------------------------------------------- #


def calibrate(
    judge: Judge,
    labelled: Sequence[LabelledTrace],
    *,
    positive_label: Label = "fail",
    attach: bool = True,
    extra_notes: Iterable[str] = (),
) -> CalibrationReport:
    """Run `judge` over a labelled set and score the agreement.

    Args:
        judge: The judge to measure. Its prompt digest and version are stamped
            into the report, so the report can never be quoted for a prompt it
            did not measure.
        labelled: Hand-labelled items. Item ids must be unique — a duplicate id
            would silently weight one item twice and, with a `ReplayJudge`, would
            also make it ambiguous which recorded answer applies.
        positive_label: Which verdict counts as the positive class. Defaults to
            "fail": a judge is a defect detector, and recall on defects is the
            figure that matters.
        attach: Also store the report on `judge.calibration`, which is what
            `lab.judges.registry.require_calibrated()` reads. On by default —
            measuring a judge and then forgetting to attach the result is the
            failure mode the registry exists to catch, and there is no reason to
            leave the gap open.
        extra_notes: Appended to the report's notes, for anything specific to
            this label set (how it was sampled, who labelled it).

    Returns:
        A `CalibrationReport`.
    """
    if not labelled:
        raise ValueError("cannot calibrate on an empty labelled set")

    seen: set[str] = set()
    for item in labelled:
        if item.item_id in seen:
            raise ValueError(
                f"duplicate item_id {item.item_id!r} in the labelled set; "
                "one item would be counted twice"
            )
        seen.add(item.item_id)

    negative_label: Label = "pass" if positive_label == "fail" else "fail"

    tp = fp = fn = tn = 0
    parse_errors = 0
    outcomes: list[ItemOutcome] = []
    disagreements: list[Disagreement] = []

    for item in labelled:
        verdict: Verdict = judge.judge(item.trace, item_id=item.item_id)
        if verdict.parse_error:
            parse_errors += 1

        human_positive = item.label == positive_label
        judge_positive = verdict.label == positive_label

        if human_positive and judge_positive:
            cell: Cell = "tp"
            tp += 1
        elif not human_positive and judge_positive:
            cell = "fp"
            fp += 1
        elif human_positive and not judge_positive:
            cell = "fn"
            fn += 1
        else:
            cell = "tn"
            tn += 1

        outcomes.append(
            ItemOutcome(
                item_id=item.item_id,
                human_label=item.label,
                judge_label=verdict.label,
                cell=cell,
                parse_error=verdict.parse_error,
            )
        )

        if cell in ("fp", "fn"):
            disagreements.append(
                Disagreement(
                    item_id=item.item_id,
                    kind="false_negative" if cell == "fn" else "false_positive",
                    human_label=item.label,
                    judge_label=verdict.label,
                    human_note=item.note,
                    judge_critique=verdict.critique,
                    judge_evidence=verdict.evidence,
                    parse_error=verdict.parse_error,
                )
            )

    disagreements.sort(key=lambda d: (d.severity_rank, d.item_id))

    n = tp + fp + fn + tn
    kappa, observed, expected = _cohens_kappa(tp, fp, fn, tn)

    report = CalibrationReport(
        judge=judge.name,
        prompt_version=judge.version,
        model=judge.model,
        prompt_sha256=judge.prompt_sha256,
        positive_label=positive_label,
        labels_sha256=labels_digest(labelled),
        confusion=ConfusionMatrix(
            true_positive=tp, false_positive=fp, false_negative=fn, true_negative=tn
        ),
        prevalence=Rate(
            name=f"prevalence of {positive_label!r}", numerator=tp + fn, denominator=n
        ),
        true_positive_rate=Rate(
            name="true positive rate (recall)", numerator=tp, denominator=tp + fn
        ),
        true_negative_rate=Rate(
            name="true negative rate (specificity)", numerator=tn, denominator=tn + fp
        ),
        precision=Rate(name="precision", numerator=tp, denominator=tp + fp),
        recall=Rate(name="recall", numerator=tp, denominator=tp + fn),
        # F1 is the harmonic mean of precision and recall, and it is also exactly
        # 2TP / (2TP + FP + FN) — a ratio of counts, so it can be printed with its
        # numerator and denominator like every other rate in this repo instead of
        # arriving as a bare float nobody can check.
        f1=Rate(name="F1", numerator=2 * tp, denominator=2 * tp + fp + fn),
        raw_agreement=Rate(name="raw agreement", numerator=tp + tn, denominator=n),
        cohens_kappa=kappa,
        kappa_observed_agreement=observed,
        kappa_expected_agreement=expected,
        parse_errors=parse_errors,
        items=outcomes,
        disagreements=disagreements,
        notes=[
            f"Positive class is a judge verdict of {positive_label!r}; the negative "
            f"class is {negative_label!r}. TPR is recall on the positive class, TNR is "
            "specificity. Both are gated: a constant answer maximises one of them.",
            "Raw agreement is reported alongside Cohen's kappa because raw agreement "
            "flatters a judge on imbalanced data — always answering with the majority "
            "class scores the majority fraction, with zero discrimination.",
            "Kappa is prevalence dependent and therefore not comparable across label "
            "sets with different class balance; TPR and TNR are, which is why the "
            "registry gates on those.",
            "Every rate is printed with its numerator and denominator so a reader can "
            "see how few items a figure rests on.",
            f"Verdicts came from {judge.model!r} via prompt {judge.version} "
            f"(sha256 {judge.prompt_sha256[:12]}); a prompt edit invalidates this report.",
            *extra_notes,
        ],
    )

    if attach:
        judge.attach_calibration(report)
    return report


def _cohens_kappa(tp: int, fp: int, fn: int, tn: int) -> tuple[float | None, float, float]:
    """Cohen's kappa for a 2x2 table, plus observed and chance agreement.

    kappa = (po - pe) / (1 - pe), where `po` is observed agreement and `pe` is the
    agreement expected from the two graders' marginal rates alone.

    Returns `(None, po, pe)` when kappa is undefined — `pe == 1`, which happens
    when both graders used only one class. That case is not a rounding edge: it is
    exactly the "judge always says pass on an all-pass set" situation, where
    perfect raw agreement carries no information at all. Reporting `None` says
    so; reporting 1.0 would be the most flattering possible lie.
    """
    n = tp + fp + fn + tn
    if n == 0:
        return None, 0.0, 0.0

    observed = (tp + tn) / n
    judge_positive = tp + fp
    judge_negative = fn + tn
    human_positive = tp + fn
    human_negative = fp + tn
    expected = (
        judge_positive * human_positive + judge_negative * human_negative
    ) / (n * n)

    if expected >= 1.0:
        return None, observed, expected
    return (observed - expected) / (1.0 - expected), observed, expected


# --------------------------------------------------------------------------- #
# Is a prompt-to-prompt difference distinguishable from chance?
# --------------------------------------------------------------------------- #


def exact_mcnemar_p(regressed: int, fixed: int) -> float:
    """Exact two-sided McNemar p over the discordant pairs. Closed form, stdlib.

    The concordant items — the ones both versions got right, and the ones both got
    wrong — carry no information about which version is better, so the test
    conditions on the `n = regressed + fixed` items where the two disagree and asks
    whether the split between them is further from even than chance would usually
    manage. Under the null each discordant item is a fair coin, so

        p = 2 * sum(C(n, i) for i in 0..min(regressed, fixed)) / 2**n     (capped at 1)

    **Exact, not the chi-square approximation**, and not because exactness is
    tidier: the approximation is unreliable below roughly 25 discordant pairs and
    this repository's label set has 24 *items*. Quoting an asymptotic p on six
    pairs would be a worse error than quoting no p at all, because it would look
    like a real number.

    Verified against the closed form 2/2**n for an all-one-way split:
    4 -> 0.12500, 5 -> 0.06250, 6 -> 0.03125, 7 -> 0.01562.
    """
    if regressed < 0 or fixed < 0:
        raise ValueError(
            f"discordant counts cannot be negative: {regressed=}, {fixed=}"
        )
    n = regressed + fixed
    if n == 0:
        # No item moved. There is nothing to test, and 1.0 is the honest answer:
        # the data is exactly what the null predicts.
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(regressed, fixed) + 1))
    return min(1.0, 2.0 * tail / 2**n)


def detectability_floor(alpha: float = 0.05) -> int:
    """How many items must move *together* before any improvement is publishable.

    The most useful number in this module, and the one a p-value alone hides. With
    `d` discordant pairs all pointing the same way the exact two-sided p is
    `2/2**d`, which depends on nothing but `d` — not on the size of the label set,
    not on the accuracy of either version. So there is a hard floor below which a
    paired comparison on *any* set cannot reach significance, and at alpha = 0.05
    that floor is **6**: five items moving together gives p = 0.0625 and does not
    clear it.

    That is a statement about what a labelled set cannot prove, which is worth
    more than the p-value it sits next to: it says in advance that a v3 fixing
    three items and breaking none is unpublishable at p = 0.250 however real the
    improvement is, and that the answer is more labels rather than more prompting.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha}")
    d = 1
    while exact_mcnemar_p(d, 0) > alpha:
        d += 1
    return d


class PairedComparison(BaseModel):
    """Two versions scored on the same items, split by who got what right.

    The 2x2 table McNemar's test runs on. Named for what it is rather than for
    the test, because the counts are readable on their own: `after_only_correct`
    is the list of items a prompt change fixed, and `before_only_correct` is the
    price it paid, and a change with a good delta and a non-zero price is a
    different object from one with none.
    """

    model_config = ConfigDict(extra="forbid")

    n_items: int
    both_correct: int
    both_wrong: int
    #: The version under test got these wrong and its predecessor got them right.
    before_only_correct: int
    #: Fixed by the change.
    after_only_correct: int
    alpha: float = 0.05

    @property
    def discordant(self) -> int:
        return self.before_only_correct + self.after_only_correct

    @property
    def p_value(self) -> float:
        return exact_mcnemar_p(self.before_only_correct, self.after_only_correct)

    @property
    def significant(self) -> bool:
        return self.p_value <= self.alpha

    @property
    def floor(self) -> int:
        """Smallest all-one-way discordant count this comparison could publish."""
        return detectability_floor(self.alpha)

    @property
    def floor_is_reachable(self) -> bool:
        """False when the set is too small to publish anything at all.

        On fewer items than the floor, every possible result is insignificant
        before a single verdict is read — which is a fact about the set, and one
        that ought to be discovered before the labelling rather than after.
        """
        return self.n_items >= self.floor

    def summary_line(self) -> str:
        return (
            f"discordant {self.after_only_correct}/{self.before_only_correct} "
            f"(fixed/broken, of {self.n_items} items), exact two-sided "
            f"p = {_fmt_p(self.p_value)}"
        )


def mcnemar(
    before: CalibrationReport, after: CalibrationReport, *, alpha: float = 0.05
) -> PairedComparison:
    """Pair two reports item by item. Both must have scored the same items.

    Correctness is `judge_label == human_label`, not the confusion cell, so the
    pairing does not depend on which class either report called positive.
    """
    first = {item.item_id: item for item in before.items}
    second = {item.item_id: item for item in after.items}
    if set(first) != set(second):
        missing = sorted(set(first) ^ set(second))
        raise ValueError(
            "cannot pair two reports scored on different items; these ids appear "
            f"in one and not the other: {missing}"
        )

    both_correct = both_wrong = before_only = after_only = 0
    for item_id, left in first.items():
        right = second[item_id]
        left_ok = left.judge_label == left.human_label
        right_ok = right.judge_label == right.human_label
        if left_ok and right_ok:
            both_correct += 1
        elif left_ok:
            before_only += 1
        elif right_ok:
            after_only += 1
        else:
            both_wrong += 1

    return PairedComparison(
        n_items=len(first),
        both_correct=both_correct,
        both_wrong=both_wrong,
        before_only_correct=before_only,
        after_only_correct=after_only,
        alpha=alpha,
    )


def _fmt_p(p: float) -> str:
    """A p-value at a precision that does not overstate it, and never as `0`."""
    if p < 0.0001:
        return f"{p:.1e}"
    return f"{p:.5f}"


def _paired_section(
    before: CalibrationReport, after: CalibrationReport, paired: PairedComparison
) -> list[str]:
    """The McNemar block and the detectability floor, as markdown lines."""
    before_v, after_v = before.prompt_version, after.prompt_version
    lines = [
        "## Is the difference distinguishable from chance?",
        "",
        f"The comparison is **paired** — the same {paired.n_items} items, the same "
        f"labels, two prompts — so the correct test is McNemar's over the items "
        f"where the two versions disagree, not a two-proportion z-test over the "
        f"two rates. A z-test treats the two columns as independent samples, and "
        f"they are the same items; it would be anticonservative here, which is the "
        f"direction that flatters.",
        "",
        f"| | {after_v} correct | {after_v} wrong |",
        "|---|---|---|",
        f"| **{before_v} correct** | {paired.both_correct} | "
        f"{paired.before_only_correct} |",
        f"| **{before_v} wrong** | {paired.after_only_correct} | "
        f"{paired.both_wrong} |",
        "",
        f"The {paired.both_correct + paired.both_wrong} concordant items carry no "
        f"information about which prompt is better. The test is over the "
        f"{paired.discordant} that moved.",
        "",
    ]
    if paired.discordant == 0:
        lines += [
            f"**No item changed verdict.** There is nothing to test: exact "
            f"two-sided p = {_fmt_p(paired.p_value)}, which is what the null "
            "predicts. Whatever moved in the rate table above moved by rounding, "
            "not by an item.",
            "",
        ]
    else:
        verdict = (
            f"distinguishable from chance at alpha = {paired.alpha:g}"
            if paired.significant
            else f"**not** distinguishable from chance at alpha = {paired.alpha:g}"
        )
        margin = ""
        if paired.significant and paired.discordant <= paired.floor:
            margin = (
                " That is the floor below, exactly: one fewer item moving would "
                "have published nothing."
            )
        lines += [
            f"`{after_v}` fixed {paired.after_only_correct} of the "
            f"{paired.n_items} items and broke {paired.before_only_correct}. "
            f"Exact two-sided McNemar "
            f"p = {_fmt_p(paired.p_value)} — {verdict}.{margin}",
            "",
        ]

    floor = paired.floor
    lines += [
        f"### The detectability floor on this set: {floor} items",
        "",
        "The half of this section that survives the next prompt change. With `d` "
        "discordant pairs **all pointing the same way**, the exact two-sided p is "
        "`2/2**d` — a function of `d` alone, not of the set size and not of either "
        "version's accuracy. So there is a hard floor under every paired "
        f"comparison, and at alpha = {paired.alpha:g} it is {floor}:",
        "",
        "| discordant pairs, all one way | exact two-sided p | publishable |",
        "|---|---|---|",
    ]
    for d in range(1, max(floor + 2, paired.discordant + 2)):
        p = exact_mcnemar_p(d, 0)
        mark = "yes" if p <= paired.alpha else "no"
        lines.append(f"| {d} | {_fmt_p(p)} | {mark} |")
    tail = "Nothing about the prompt moves that; more labelled items is the only "
    tail += "thing that does."
    if paired.floor_is_reachable:
        lines += [
            "",
            f"So **{floor} of these {paired.n_items} items must move together** "
            f"before this set can publish an improvement at all. A `v3` that fixed "
            f"{floor // 2} of them and broke none would score "
            f"p = {_fmt_p(exact_mcnemar_p(floor // 2, 0))} and be unpublishable, "
            f"however real the improvement was. That is not an argument for a "
            f"laxer threshold — it is the size of the labelled set, stated as the "
            f"smallest claim the set can support. " + tail,
            "",
        ]
    else:
        lines += [
            "",
            f"**This set cannot publish any improvement.** It holds "
            f"{paired.n_items} items and the floor is {floor}, so every possible "
            f"result — including all {paired.n_items} moving together, "
            f"p = {_fmt_p(exact_mcnemar_p(paired.n_items, 0))} — is insignificant "
            f"at alpha = {paired.alpha:g} before a single verdict is read. " + tail,
            "",
        ]
    return lines


# --------------------------------------------------------------------------- #
# Comparing two prompt versions
# --------------------------------------------------------------------------- #


def compare_reports(before: CalibrationReport, after: CalibrationReport) -> str:
    """A markdown delta table between two calibrations of the same judge.

    The artefact a prompt iteration is actually judged on. Two properties are
    enforced rather than assumed: both reports must describe the same judge, and
    both must have been measured on the same label set (equal `labels_sha256`).
    A "v1 -> v2 improvement" measured on two different label sets is not a
    comparison, and it is the easiest self-deception available in this line of
    work — improve the prompt, quietly drop the three items it kept getting
    wrong, and the numbers move.
    """
    if before.judge != after.judge:
        raise ValueError(
            f"cannot compare calibrations of different judges: "
            f"{before.judge!r} vs {after.judge!r}"
        )
    if before.labels_sha256 != after.labels_sha256:
        raise ValueError(
            "refusing to compare calibrations measured on different label sets "
            f"({before.labels_sha256[:12]} vs {after.labels_sha256[:12]}): the "
            "difference would mix a prompt change with a label change"
        )

    def row(name: str, a: Rate, b: Rate, *, interval: bool = True) -> str:
        def cell(rate: Rate) -> str:
            value = "undefined" if rate.value is None else f"{rate.value:.3f}"
            text = f"{value} ({rate.numerator}/{rate.denominator})"
            if interval and rate.defined:
                text += f" {rate.interval_text()}"
            return text

        if a.value is None or b.value is None:
            delta = "—"
        else:
            delta = f"{b.value - a.value:+.3f}"
        return f"| {name} | {cell(a)} | {cell(b)} | {delta} |"

    kappa_delta = (
        "—"
        if before.cohens_kappa is None or after.cohens_kappa is None
        else f"{after.cohens_kappa - before.cohens_kappa:+.3f}"
    )

    lines = [
        f"# `{after.judge}`: prompt {before.prompt_version} -> {after.prompt_version}",
        "",
        f"Same label set (`{after.labels_sha256[:16]}`, {after.n} items), same model "
        f"(`{after.model}`). Only the prompt changed.",
        "",
        f"| metric | {before.prompt_version} | {after.prompt_version} | delta |",
        "|---|---|---|---|",
        row(
            "true positive rate (recall)",
            before.true_positive_rate,
            after.true_positive_rate,
        ),
        row(
            "true negative rate (specificity)",
            before.true_negative_rate,
            after.true_negative_rate,
        ),
        row("precision", before.precision, after.precision),
        row("F1", before.f1, after.f1, interval=False),
        row("raw agreement", before.raw_agreement, after.raw_agreement),
        f"| Cohen's kappa | {_fmt_kappa(before.cohens_kappa)} | "
        f"{_fmt_kappa(after.cohens_kappa)} | {kappa_delta} |",
        f"| true positives | {before.confusion.true_positive} | "
        f"{after.confusion.true_positive} | "
        f"{after.confusion.true_positive - before.confusion.true_positive:+d} |",
        f"| true negatives | {before.confusion.true_negative} | "
        f"{after.confusion.true_negative} | "
        f"{after.confusion.true_negative - before.confusion.true_negative:+d} |",
        f"| false positives | {before.confusion.false_positive} | "
        f"{after.confusion.false_positive} | "
        f"{after.confusion.false_positive - before.confusion.false_positive:+d} |",
        f"| false negatives | {before.confusion.false_negative} | "
        f"{after.confusion.false_negative} | "
        f"{after.confusion.false_negative - before.confusion.false_negative:+d} |",
        f"| unparseable answers | {before.parse_errors} | {after.parse_errors} | "
        f"{after.parse_errors - before.parse_errors:+d} |",
        "",
        "All four confusion cells are printed, not just the two rates. A rate hides "
        "which direction the errors ran, and the direction is the whole story here: a "
        "judge that misses defects and a judge that invents them fail the same "
        "threshold and require opposite fixes.",
        "",
        "Each rate carries its 95% Wilson interval, and those intervals are **not "
        "the comparison**. They are computed as though the two columns were "
        "independent samples, and they are not: the same items were graded twice, "
        "so the columns are paired and the pairing carries most of the information. "
        "Reading two intervals for overlap discards it — it can call a real "
        "difference inconclusive because both intervals are wide, and it can flatter "
        "a difference driven by two items. The paired test below is what the "
        "comparison is decided on; the intervals are here to say how much each "
        "column on its own is worth.",
        "",
        *_paired_section(before, after, mcnemar(before, after)),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-consistency: is the instrument stable at all?
# --------------------------------------------------------------------------- #


class ItemVerdictRuns(BaseModel):
    """One item's verdict from each of several identical runs."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    human_label: Label
    verdicts: list[Label]

    @property
    def unanimous(self) -> bool:
        return len(set(self.verdicts)) <= 1

    def __str__(self) -> str:
        return f"{self.item_id}: {' -> '.join(self.verdicts)}"


class SelfConsistency(BaseModel):
    """How often a judge gives one item the same verdict twice.

    The measurement that makes an agreement figure interpretable. TPR and TNR
    describe a judge's *accuracy* against human labels on one sample per item; they
    say nothing about whether a second sample would have produced the same table.
    A judge that is 0.95 accurate and flips one verdict in ten is not a 0.95
    instrument — it is an instrument whose reading changes when nothing changed,
    and every downstream comparison ("v3 beat v2 by two items") is then partly
    reading its own noise.

    The counted unit is deliberately the **item**, not the rate. Aggregate rates
    can be perfectly stable while individual verdicts move, because errors in
    opposite directions cancel: two items swapping places leaves TPR and TNR
    untouched and the judge's per-item output different. Only the per-item view
    sees that, and the per-item view is the one a human debugging a disagreement
    actually needs.

    Not a substitute for calibration, and not a licence to average several samples
    into a verdict: this repository measures variance rather than voting it away,
    because voting raises cost per item threefold and hides the instability rather
    than reporting it.
    """

    model_config = ConfigDict(extra="forbid")

    judge: str
    prompt_version: str
    model: str
    runs: int
    items: list[ItemVerdictRuns] = Field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.items)

    @property
    def unstable(self) -> list[ItemVerdictRuns]:
        """The items whose verdict was not the same every time."""
        return [item for item in self.items if not item.unanimous]

    @property
    def unanimity(self) -> Rate:
        return Rate(
            name="unanimous items",
            numerator=self.n - len(self.unstable),
            denominator=self.n,
        )

    def summary_line(self) -> str:
        return (
            f"{self.judge} {self.prompt_version}: {self.unanimity} items unanimous "
            f"across {self.runs} identical runs of {self.model}"
        )

    def to_markdown(self) -> str:
        lines = [
            f"### Run-to-run stability — `{self.prompt_version}`",
            "",
            f"{self.runs} identical runs, same prompt, same model (`{self.model}`), "
            f"temperature 0. Unanimous on {self.unanimity}.",
            "",
        ]
        if not self.unstable:
            lines.append(
                "No item changed verdict between runs. Stability on this set is not a "
                "guarantee for unseen items, but an unstable judge would have shown it "
                "here."
            )
        else:
            lines.append("Items that did not hold still:")
            lines.append("")
            for item in self.unstable:
                lines.append(
                    f"- `{item.item_id}` (human: **{item.human_label}**) — "
                    + ", ".join(item.verdicts)
                )
        lines.append("")
        return "\n".join(lines)


def self_consistency(
    judges: Sequence[Judge], labelled: Sequence[LabelledTrace]
) -> SelfConsistency:
    """Score several runs of the same judge over the same labelled set.

    Each element of `judges` is one run — in practice a `ReplayJudge` over one
    replicate recording, so the measurement is reproducible offline from committed
    fixtures rather than being a number somebody once saw.

    Refuses a mixture of judges, prompt versions or models, because "the same
    judge twice" is the entire premise: comparing two different prompts and calling
    the difference instability would be a category error.
    """
    if len(judges) < 2:
        raise ValueError(
            "self-consistency needs at least two runs; one run cannot disagree "
            "with itself"
        )
    if not labelled:
        raise ValueError("cannot measure self-consistency on an empty set")

    names = {(j.name, j.version, j.model, j.prompt_sha256) for j in judges}
    if len(names) != 1:
        raise ValueError(
            "self-consistency compares repeated runs of ONE judge; these runs differ "
            f"in name, prompt version, model or prompt digest: {sorted(names)}"
        )

    first = judges[0]
    rows: list[ItemVerdictRuns] = []
    for item in labelled:
        verdicts = [
            judge.judge(item.trace, item_id=item.item_id).label for judge in judges
        ]
        rows.append(
            ItemVerdictRuns(
                item_id=item.item_id, human_label=item.label, verdicts=verdicts
            )
        )
    return SelfConsistency(
        judge=first.name,
        prompt_version=first.version,
        model=first.model,
        runs=len(judges),
        items=rows,
    )


def traces_of(labelled: Sequence[LabelledTrace]) -> list[Trace]:
    """The traces from a labelled set, in order. Convenience for recording."""
    return [item.trace for item in labelled]
