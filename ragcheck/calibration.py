"""Calibrating the support oracle, and refusing it when it does not clear the bar.

WHAT THIS DEMONSTRATES
----------------------
A groundedness figure is a count of one judge's verdicts. Quoting it without
knowing that judge's agreement with a human is quoting a measurement taken with
an uncalibrated instrument — and in this package the instrument is deliberately
a weak one, so the point is not academic.

Everything here is a thin adapter over `lab.judges.calibration`. Nothing is
reimplemented: the confusion matrix, TPR, TNR, kappa, the disagreement list and
the thresholds all come from `lab`, because a RAG claim label is the same object
as a conversational-trace label once the claim is expressed as a trace (see
`ragcheck.traces`).

WHAT THE LABEL SET IS
---------------------
`fixtures/claim_labels.yaml` — 18 hand-labelled claim/context pairs, each one
self-contained: the question, the passage ids, the claim, the human verdict, and
a note saying why. Sixteen come from the answers in the evaluation set; two are
probes written to target a known blind spot (a paraphrase with no shared
vocabulary, and a figure that conflicts).

The positive class is **fail** — "this claim is NOT supported" — because the
interesting error is a missed unsupported claim, and TPR is then recall on
exactly the defect a grounding check exists to catch.

WHAT THE MEASUREMENT SAYS
-------------------------
The lexical stand-in does not clear 0.85 TPR on this set, which is why
`ragcheck.report.evaluate(gate=True)` raises rather than printing a green number.
That is the intended result: the gate works, and it refuses the oracle that ships
in this repository. Give the same judge a real model behind the same interface,
re-run `calibrate_claim_support`, and the gate decides again on the new numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field

from lab.judges.calibration import (
    CalibrationReport,
    CalibrationThresholds,
    Label,
    LabelledTrace,
    calibrate,
)
from lab.judges.judge import Judge
from lab.judges.registry import require_calibrated

from ragcheck.corpus import Corpus, Retrieval, load_corpus
from ragcheck.judges import claim_support_judge
from ragcheck.offline import STAND_IN_MODEL, LexicalOracle, Probe
from ragcheck.traces import claim_trace

__all__ = [
    "LABELS_PATH",
    "ClaimLabel",
    "load_claim_labels",
    "labelled_traces",
    "label_probes",
    "offline_claim_support_judge",
    "calibrate_claim_support",
    "gate_claim_support",
]

LABELS_PATH = Path(__file__).parent / "fixtures" / "claim_labels.yaml"


class ClaimLabel(BaseModel):
    """One hand-labelled claim/context pair, with the reason for the label.

    `note` is mandatory in spirit and defaulted in code for the same reason
    `lab.judges.calibration.LabelledTrace` requires one: on a set this small,
    mislabels are the largest single source of apparent judge error, and the note
    is what settles "the judge is wrong" versus "the label is wrong" in seconds.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    context: list[str] = Field(min_length=1, description="Chunk ids the claim was checked against.")
    claim: str = Field(min_length=1)
    label: Label = Field(description='"pass" when the claim IS supported, "fail" when it is not.')
    note: str = ""
    labeller: str = ""
    source: str = Field(default="", description="Which case the claim came from, if any.")


def load_claim_labels(path: str | Path = LABELS_PATH) -> list[ClaimLabel]:
    """Read the label file. `safe_load` only: labels are data."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "items" not in raw:
        raise ValueError(f"{path}: expected a mapping with an 'items' key")
    # A file-level `labeller:` applies to every row that does not name its own.
    # Who labelled a set is part of the measurement, and repeating it eighteen
    # times is how it ends up wrong on row twelve.
    default_labeller = str(raw.get("labeller", ""))
    items = [
        ClaimLabel.model_validate({"labeller": default_labeller, **item})
        for item in raw["items"]
    ]
    seen: set[str] = set()
    for item in items:
        if item.item_id in seen:
            raise ValueError(f"duplicate label item_id {item.item_id!r}")
        seen.add(item.item_id)
    return items


def labelled_traces(labels: Sequence[ClaimLabel], corpus: Corpus) -> list[LabelledTrace]:
    """Turn labels into the exact objects `lab.judges.calibration` consumes."""
    items: list[LabelledTrace] = []
    for index, label in enumerate(labels, start=1):
        retrieval = Retrieval(
            query=label.question,
            chunks=corpus.select(label.context),
            scores=[1.0] * len(label.context),
        )
        trace = claim_trace(
            case_id=label.item_id,
            question=label.question,
            retrieval=retrieval,
            claim=label.claim,
            index=index,
            kind="label",
        )
        items.append(
            LabelledTrace(
                item_id=label.item_id,
                label=label.label,
                trace=trace,
                note=label.note,
                labeller=label.labeller,
            )
        )
    return items


def label_probes(labels: Sequence[ClaimLabel]) -> list[Probe]:
    """The oracle's side of the same items."""
    return [
        Probe(
            item_id=label.item_id,
            kind="support",
            question=label.question,
            text=label.claim,
            chunk_ids=list(label.context),
        )
        for label in labels
    ]


def offline_claim_support_judge(
    corpus: Corpus, labels: Sequence[ClaimLabel], *, strict: bool = True
) -> Judge:
    """The claim-support judge wired to the stand-in, for exactly these items."""
    completion = LexicalOracle(corpus).completion(label_probes(labels))
    return claim_support_judge(completion=completion, model=STAND_IN_MODEL, strict=strict)


def calibrate_claim_support(
    *,
    corpus: Corpus | None = None,
    labels: Sequence[ClaimLabel] | None = None,
    judge: Judge | None = None,
) -> tuple[Judge, CalibrationReport]:
    """Measure a claim-support judge against the hand labels.

    Returns the judge with its report attached, so the caller can hand it
    straight to `require_calibrated()`. Defaults to the offline stand-in, which
    is the only judge this repository can run with no API key — and the report is
    what says how much its verdicts are worth.
    """
    resolved_corpus = corpus if corpus is not None else load_corpus()
    resolved_labels = list(labels) if labels is not None else load_claim_labels()
    resolved_judge = judge if judge is not None else offline_claim_support_judge(
        resolved_corpus, resolved_labels
    )
    report = calibrate(resolved_judge, labelled_traces(resolved_labels, resolved_corpus))
    return resolved_judge, report


def gate_claim_support(
    *,
    thresholds: CalibrationThresholds | None = None,
    ci: bool | None = None,
    allow_uncalibrated: bool = False,
    judge: Judge | None = None,
    corpus: Corpus | None = None,
    labels: Sequence[ClaimLabel] | None = None,
) -> CalibrationReport | None:
    """Calibrate, then refuse the judge if it is below the bar.

    A one-call gate for a pipeline: `gate_claim_support(ci=True)` raises
    `JudgeBelowThresholdError` for the stand-in, naming the numbers it fell short
    on. `allow_uncalibrated=True` is the only way past, it has to be written at
    the call site, and it logs a warning — `lab.judges.registry` owns that
    decision and this function does not soften it.
    """
    resolved_judge, _ = calibrate_claim_support(corpus=corpus, labels=labels, judge=judge)
    return require_calibrated(
        resolved_judge,
        thresholds=thresholds,
        ci=ci,
        allow_uncalibrated=allow_uncalibrated,
    )
