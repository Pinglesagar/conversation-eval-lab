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

WHAT THIS DOES NOT CLAIM
------------------------
No confidence intervals. With a few dozen items the honest statement is the
fraction itself — a Wilson interval on 8/8 would imply a precision the set cannot
support, and quoting one would suggest the sample size is adequate when the real
answer is "label more items". No inter-*human* agreement either: this measures a
judge against a label set, and if the label set itself is noisy that noise is
attributed to the judge. Labelling the same items twice, by two people, is the
right next step and is out of scope here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, computed_field

from lab.judges.judge import Judge, Label, Verdict
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
    """

    model_config = ConfigDict(extra="forbid")

    min_tpr: float = Field(default=0.85, ge=0.0, le=1.0)
    min_tnr: float = Field(default=0.85, ge=0.0, le=1.0)
    min_items: int = Field(default=10, ge=1)
    max_parse_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    min_kappa: float | None = Field(default=None, ge=-1.0, le=1.0)

    def describe(self) -> str:
        parts = [
            f"TPR >= {self.min_tpr:.2f}",
            f"TNR >= {self.min_tnr:.2f}",
            f"n >= {self.min_items}",
            f"parse errors <= {self.max_parse_error_rate:.0%}",
        ]
        if self.min_kappa is not None:
            parts.append(f"kappa >= {self.min_kappa:.2f}")
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
        for rate in self.rates():
            lines.append(f"  {rate.name:<{width}} : {rate}")
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
            "| metric | value | numerator / denominator |",
            "|---|---|---|",
        ]
        for rate in self.rates():
            value = "undefined" if rate.value is None else f"{rate.value:.3f}"
            lines.append(f"| {rate.name} | {value} | {rate.numerator} / {rate.denominator} |")
        lines.append(
            f"| Cohen's kappa | {_fmt_kappa(self.cohens_kappa)} | "
            f"observed {self.kappa_observed_agreement:.3f}, "
            f"chance {self.kappa_expected_agreement:.3f} |"
        )
        if self.parse_errors:
            per = self.parse_error_rate
            lines.append(
                f"| parse errors | {per.value:.3f} | {per.numerator} / {per.denominator} |"
            )

        lines += [
            "",
            "Raw agreement is reported next to kappa deliberately: raw agreement "
            "flatters a judge on imbalanced data, because always answering with the "
            "majority class scores the majority fraction. Kappa subtracts the "
            "agreement two graders with these marginals would reach by chance.",
            "",
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

    def row(name: str, a: Rate, b: Rate) -> str:
        def cell(rate: Rate) -> str:
            value = "undefined" if rate.value is None else f"{rate.value:.3f}"
            return f"{value} ({rate.numerator}/{rate.denominator})"

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
        row("true positive rate", before.true_positive_rate, after.true_positive_rate),
        row("true negative rate", before.true_negative_rate, after.true_negative_rate),
        row("precision", before.precision, after.precision),
        row("F1", before.f1, after.f1),
        row("raw agreement", before.raw_agreement, after.raw_agreement),
        f"| Cohen's kappa | {_fmt_kappa(before.cohens_kappa)} | "
        f"{_fmt_kappa(after.cohens_kappa)} | {kappa_delta} |",
        f"| false positives | {before.confusion.false_positive} | "
        f"{after.confusion.false_positive} | "
        f"{after.confusion.false_positive - before.confusion.false_positive:+d} |",
        f"| false negatives | {before.confusion.false_negative} | "
        f"{after.confusion.false_negative} | "
        f"{after.confusion.false_negative - before.confusion.false_negative:+d} |",
        "",
    ]
    return "\n".join(lines)


def traces_of(labelled: Sequence[LabelledTrace]) -> list[Trace]:
    """The traces from a labelled set, in order. Convenience for recording."""
    return [item.trace for item in labelled]
