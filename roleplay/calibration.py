"""Calibrating the scorer: how do we know the score is right?

THE MOVE THIS MODULE MAKES
--------------------------
A rubric scorer is a judge. It reads a transcript and returns a verdict, and it
is wrong sometimes, in ways that are not visible from its own output — which is
the definition of an instrument that needs calibrating. So the scorer is measured
exactly the way `lab.judges` measures an LLM judge: run it over a hand-labelled
set, build the confusion matrix against the human column, publish TPR, TNR and
kappa, and refuse to let it gate anything until it clears a stated threshold.

Nothing in `lab.judges` was changed to make this work. The seam is
`lab.judges.judge.Completion` — the one-method protocol that turns a request into
raw text. `ScorerCompletion` implements it by running the product's own scorer and
returning its verdict in the JSON the parser already accepts. Everything either
side of that seam — prompt rendering, the digest that detects a changed rubric,
verdict parsing, the confusion matrix, the gate — is the same code that measures a
model.

WHERE THE HUMAN COLUMN COMES FROM
---------------------------------
The corpus itself. Every scenario declares `expectation.human_verdict` and a
`reason`, so the golden dataset and the regression suite are one artefact rather
than two that drift apart. That is not a shortcut; it is the only arrangement in
which a row cannot be added to the suite without someone stating what the right
answer is. `lab.judges.calibration.LabelledTrace` requires the reason too, for the
same purpose it serves there: on a set of this size, mislabels are the largest
single source of apparent scorer error, and the note is what settles the argument.

WHAT IS DELIBERATELY HELD FIXED
-------------------------------
The labelled traces are built with `RoleplayCoach.converse`, which never consults
the scorer, and each item is graded by a **fresh** scorer. Both choices exist to
isolate one defect at a time: the labelled input cannot contain the answer, and
the cross-session curve cannot move a verdict during calibration. Measuring the
compliance blindness and the cohort curve in the same run would produce a
confusion matrix that is a function of item ordering, which is not a measurement
of anything.

WHAT A LOW TPR MEANS HERE
-------------------------
The positive class is "fail" — a judge is a defect detector, and recall on the
sessions that ought to be stopped is the number that matters. A low TPR is not a
tuning problem. It says the product certifies sessions a competent reviewer would
fail, and the disagreement list says which ones and why.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from lab.judges.calibration import (
    CalibrationReport,
    CalibrationThresholds,
    LabelledTrace,
    calibrate,
)
from lab.judges.judge import Judge, JudgeRequest, PromptTemplate
from lab.judges.registry import JudgeRegistry
from lab.trace.schema import Trace

from roleplay.corpus import Corpus, load_corpus
from roleplay.runtime import RoleplayCoach
from roleplay.scorer import RubricScorer

__all__ = [
    "JUDGE_NAME",
    "PROMPT_VERSION",
    "RUBRIC_PATH",
    "SCORER_MODEL",
    "ScorerCompletion",
    "labelled_from_corpus",
    "build_scorer_judge",
    "calibrate_scorer",
    "gate_report",
    "render_disagreements",
    "written_labels",
]

#: The question being asked, in the vocabulary `lab.judges` uses: "would this
#: session pass certification?" Named for the question, not the implementation,
#: so swapping the deterministic scorer for a model-backed one later keeps the
#: same calibration history.
JUDGE_NAME: str = "roleplay_pass_verdict"

#: The rubric version. Required, never defaulted: a calibration report that
#: cannot name the rubric it measured is not evidence about anything.
PROMPT_VERSION: str = "v1"

RUBRIC_PATH: Path = Path(__file__).resolve().parent / "rubric_v1.md"

#: What is doing the grading. Recorded in the report where a model route would
#: normally go, and honest about being local code rather than a provider — a
#: report that said "gpt-4o" here would be a lie about provenance.
SCORER_MODEL: str = "local:rubric-scorer"


class ScorerCompletion:
    """The product's scorer, wearing the `lab.judges.Completion` interface.

    Keyed by `item_id` rather than by prompt text, because the scorer grades a
    *session* and the rendered prompt is a projection of it. The mapping from
    item id to trace is supplied at construction, so a missing item is an error
    and never a silently-empty transcript.

    A fresh `RubricScorer` per call, on purpose. See the module docstring: the
    cohort curve is a separate defect measured separately, and letting it run
    during calibration would make the confusion matrix a function of the order
    the labelled set happens to be in.
    """

    def __init__(
        self,
        traces: dict[str, Trace],
        *,
        make_scorer: Callable[[], RubricScorer] | None = None,
    ) -> None:
        self.traces = dict(traces)
        self.make_scorer = make_scorer if make_scorer is not None else RubricScorer
        self.calls: list[JudgeRequest] = []

    def __call__(self, request: JudgeRequest) -> str:
        self.calls.append(request)
        try:
            trace = self.traces[request.item_id]
        except KeyError as exc:
            raise KeyError(
                f"no trace for item {request.item_id!r}; the labelled set and the "
                f"completion have drifted apart ({len(self.traces)} trace(s) held)"
            ) from exc
        card = self.make_scorer().score_trace(trace)
        return json.dumps(
            {
                "verdict": card.verdict,
                "critique": card.summary_line(),
                "evidence": card.feedback[:200],
            }
        )


def labelled_from_corpus(
    corpus: Corpus | None = None,
    *,
    coach: RoleplayCoach | None = None,
) -> tuple[list[LabelledTrace], dict[str, Trace]]:
    """Build the labelled set from the corpus's own human column.

    Returns the labelled items and the `{item_id: trace}` map that
    `ScorerCompletion` needs. Both come from one walk of the corpus, so they
    cannot disagree about which trace belongs to which label.

    The traces are conversation-only: `converse` runs the roleplay and stops
    before the scoring pass.
    """
    resolved = corpus if corpus is not None else load_corpus()
    driver = coach if coach is not None else RoleplayCoach(scorer=RubricScorer())

    items: list[LabelledTrace] = []
    traces: dict[str, Trace] = {}
    for scenario in resolved:
        conversation = driver.converse(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=resolved.profile_for(scenario),
            session_id=f"label-{scenario.id}",
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        )
        traces[scenario.id] = conversation.trace
        items.append(
            LabelledTrace(
                item_id=scenario.id,
                label=scenario.expectation.human_verdict,
                trace=conversation.trace,
                note=scenario.expectation.reason,
                labeller="corpus:expectation",
            )
        )
    return items, traces


def build_scorer_judge(
    traces: dict[str, Trace],
    *,
    make_scorer: Callable[[], RubricScorer] | None = None,
) -> Judge:
    """Wrap the scorer as a `lab.judges.Judge` over the rubric prompt.

    `temperature=0.0` and `include_tools=True`. The temperature is inherited from
    `Judge`'s default and is right for the same reason it is right there — an
    instrument should not sample. The tool ledger is included because the rubric
    tells the grader to read the disclosure register, which lives in the tool
    events; a judge whose prompt cites evidence it was never shown is measuring
    something other than the rubric.
    """
    return Judge(
        name=JUDGE_NAME,
        prompt=PromptTemplate.from_path(RUBRIC_PATH),
        version=PROMPT_VERSION,
        model=SCORER_MODEL,
        completion=ScorerCompletion(traces, make_scorer=make_scorer),
        include_tools=True,
    )


def calibrate_scorer(
    corpus: Corpus | None = None,
    *,
    coach: RoleplayCoach | None = None,
    make_scorer: Callable[[], RubricScorer] | None = None,
) -> tuple[CalibrationReport, Judge, list[LabelledTrace]]:
    """Measure the scorer against the corpus's human column.

    Returns the report, the judge it is attached to, and the labelled set, so a
    caller can print the disagreements alongside the rates. The report is
    attached to the judge by `calibrate`, which is what makes
    `lab.judges.registry.require_calibrated` able to refuse it.
    """
    items, traces = labelled_from_corpus(corpus, coach=coach)
    judge = build_scorer_judge(traces, make_scorer=make_scorer)
    report = calibrate(
        judge,
        items,
        positive_label="fail",
        extra_notes=(
            "The judge under measurement is the product's own rubric scorer, not a "
            "model. The positive class is 'fail': the number that matters is recall "
            "on sessions a competent reviewer would stop.",
            "Labels are the corpus's own expectation.human_verdict, one per scenario, "
            "each with the reviewer's stated reason.",
            "Each item is graded by a fresh scorer, so the cross-session cohort curve "
            "cannot move a verdict during calibration.",
        ),
    )
    return report, judge, items


def gate_report(
    report: CalibrationReport,
    judge: Judge,
    *,
    thresholds: CalibrationThresholds | None = None,
) -> tuple[bool, list[str]]:
    """Run the calibration gate and return `(cleared, reasons)`.

    Deliberately returns the verdict instead of raising. This is a demonstration
    pack whose scorer is *expected* to fail the gate, and a demo that dies on the
    finding it exists to show is a worse demo. In a real pipeline the caller is
    `JudgeRegistry.require_calibrated`, which raises in CI — and that path is
    exercised here too, so the refusal is a real refusal and not a printed
    opinion.
    """
    thr = thresholds if thresholds is not None else CalibrationThresholds()
    registry = JudgeRegistry(thresholds=thr)
    registry.register(judge)
    ok, failures = report.meets(thr)
    if not ok:
        # Prove the gate is load-bearing rather than decorative: ask the registry
        # for the judge in CI mode and confirm it refuses.
        try:
            registry.require_calibrated(judge, ci=True)
        except Exception as exc:  # noqa: BLE001 - the refusal is the expected outcome
            failures = failures + [f"registry refused the judge in CI mode: {type(exc).__name__}"]
    return ok, failures


def render_disagreements(report: CalibrationReport, *, limit: int = 10) -> str:
    """The disagreement list, worst first, with both sides' reasoning.

    False negatives sort first — a missed defect is worse than a false alarm when
    the defect is a compliance breach — and `lab.judges.calibration` already
    orders them that way, so this only formats.
    """
    if not report.disagreements:
        return "no disagreements: the scorer matched the human column on every item"
    lines = [f"{len(report.disagreements)} disagreement(s), worst first:"]
    for item in report.disagreements[:limit]:
        lines.append(
            f"  {item.kind.upper():<15} {item.item_id}: human said {item.human_label}, "
            f"scorer said {item.judge_label}"
        )
        if item.human_note:
            lines.append(f"      human: {item.human_note.strip()}")
        if item.judge_critique:
            lines.append(f"      scorer: {item.judge_critique.strip()}")
    if len(report.disagreements) > limit:
        lines.append(f"  ... and {len(report.disagreements) - limit} more")
    return "\n".join(lines)


def written_labels(path: str | Path, corpus: Corpus | None = None) -> Path:
    """Write the labelled set to JSONL for review outside Python.

    Not committed by default. The set is derived from the corpus, so committing it
    would be committing a second copy of the ground truth, and a second copy is a
    copy that goes stale. The digest in the calibration report is what pins the
    labels a given report was measured against.
    """
    from lab.judges.calibration import write_labels

    items, _ = labelled_from_corpus(corpus)
    return write_labels(items, path)
