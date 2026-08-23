"""One run, both halves, and the calibration that says what the second is worth.

WHAT THIS DEMONSTRATES
----------------------
The assembly, and three decisions about how results are presented that matter
more than the numbers:

*   **Retrieval and generation are reported separately, never blended.** There is
    no single "RAG score" here and there will not be one. recall@3 = 0.75 and
    groundedness = 12/14 are two findings owned by two different teams, and
    averaging them into 0.80 destroys the only actionable content either had.
*   **Every judged number is printed next to its judge's calibration.** A
    groundedness figure whose grader has an unmeasured error rate is not a
    measurement, so the report carries the TPR/TNR of the instrument in the same
    output as the reading. `evaluate(gate=True)` refuses to produce the reading
    at all when the instrument is below threshold.
*   **The findings list quotes evidence.** "3 claims unsupported" is a number;
    "c02#claim2: 'It is GBP 25 per person' — p01 says 15" is a bug report. The
    second is what gets fixed.

`evaluate()` needs no arguments and no API key: it loads the fixture corpus and
dataset, retrieves with the lexical retriever, and grades with the offline
stand-in oracle. Pass real judges to grade for real; nothing else changes.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.judges.calibration import CalibrationReport, CalibrationThresholds, Rate
from lab.judges.judge import Judge
from lab.judges.registry import require_calibrated

from ragcheck.calibration import (
    ClaimLabel,
    calibrate_claim_support,
    label_probes,
    load_claim_labels,
)
from ragcheck.corpus import Corpus, LexicalRetriever, Retrieval, Retriever, load_corpus
from ragcheck.dataset import RagDataset, load_cases
from ragcheck.generation import (
    ContextPrecisionResult,
    RelevanceResult,
    SupportResult,
    answer_relevance,
    context_recall,
    groundedness,
    judged_context_precision,
    pooled,
)
from ragcheck.judges import (
    answer_relevance_judge,
    claim_support_judge,
    passage_relevance_judge,
)
from ragcheck.offline import STAND_IN_MODEL, LexicalOracle, probes_for_dataset
from ragcheck.retrieval import (
    RetrievalReport,
    Score,
    average_precision_at_k,
    contexts_for,
    evaluate_retrieval,
    recall_at_k,
)

__all__ = [
    "GenerationRow",
    "GenerationReport",
    "RagReport",
    "offline_judges",
    "evaluate",
]


class GenerationRow(BaseModel):
    """Every generation metric for one answered case, with its evidence."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    answer: str
    context: list[str]
    gold: list[str]
    context_recall_gold: Rate = Field(
        description="Gold chunks present in the context this answer was produced from."
    )
    context_precision_gold: float = Field(
        description="AP@k over that context using gold ids — no oracle involved."
    )
    groundedness: SupportResult | None = None
    relevance: RelevanceResult | None = None
    context_recall: SupportResult | None = None
    context_precision: ContextPrecisionResult | None = None

    @property
    def findings(self) -> list[str]:
        """Human-readable defects for this row, each quoting its evidence."""
        out: list[str] = []
        if self.groundedness is not None:
            for claim in self.groundedness.unsupported:
                out.append(
                    f"{claim.item_id}: unsupported claim {claim.claim!r} — {claim.critique}"
                )
        if self.relevance is not None and not self.relevance.relevant:
            out.append(
                f"{self.relevance.item_id}: the answer does not address the question — "
                f"{self.relevance.critique}"
            )
        if self.context_recall is not None:
            for claim in self.context_recall.unsupported:
                out.append(
                    f"{claim.item_id}: the context cannot support the reference claim "
                    f"{claim.claim!r} — {claim.critique}"
                )
        if self.context_recall_gold.value is not None and self.context_recall_gold.value < 1.0:
            missing = [chunk for chunk in self.gold if chunk not in self.context]
            out.append(
                f"{self.case_id}: the context is missing gold chunk(s) {missing}, which "
                "caps every generation metric on this row"
            )
        return out


class GenerationReport(BaseModel):
    """Per-case generation rows, plus aggregates that keep their denominators."""

    model_config = ConfigDict(extra="forbid")

    k: int
    rows: list[GenerationRow]
    support_judge: str = ""
    relevance_judge: str = ""
    passage_judge: str = ""
    judge_model: str = ""

    @property
    def n(self) -> int:
        return len(self.rows)

    def _support_results(self) -> list[SupportResult]:
        return [row.groundedness for row in self.rows if row.groundedness is not None]

    def _recall_results(self) -> list[SupportResult]:
        return [row.context_recall for row in self.rows if row.context_recall is not None]

    @property
    def pooled_groundedness(self) -> Rate:
        """Every claim weighs the same — a six-claim answer has six chances to lie."""
        return pooled(self._support_results(), name="groundedness (micro, claims)")

    @property
    def mean_groundedness(self) -> Score:
        """Every answer weighs the same."""
        return Score.mean(
            "groundedness (macro, answers)",
            [result.rate.value or 0.0 for result in self._support_results()],
        )

    @property
    def relevance_rate(self) -> Rate:
        rows = [row for row in self.rows if row.relevance is not None]
        return Rate(
            name="answer relevance (answers)",
            numerator=sum(1 for row in rows if row.relevance and row.relevance.relevant),
            denominator=len(rows),
        )

    @property
    def pooled_context_recall(self) -> Rate:
        return pooled(self._recall_results(), name="context recall (micro, reference claims)")

    @property
    def mean_context_precision_judged(self) -> Score:
        values = [
            row.context_precision.value
            for row in self.rows
            if row.context_precision is not None and row.context_precision.value is not None
        ]
        return Score.mean("context precision (judged)", values)

    @property
    def mean_context_precision_gold(self) -> Score:
        return Score.mean(
            "context precision (gold ids)",
            [row.context_precision_gold for row in self.rows],
        )

    def findings(self) -> list[str]:
        return [finding for row in self.rows for finding in row.findings]

    def to_text(self) -> str:
        lines = [
            f"GENERATION — {self.n} answered questions, k={self.k}",
            f"  judges: {self.support_judge}, {self.relevance_judge}, "
            f"{self.passage_judge} on {self.judge_model}",
            "",
            f"  groundedness (micro, claims)   {self.pooled_groundedness}",
            f"  groundedness (macro, answers)  {self.mean_groundedness}",
            f"  answer relevance               {self.relevance_rate}",
            f"  context recall (reference)     {self.pooled_context_recall}",
            f"  context precision (gold ids)   {self.mean_context_precision_gold}",
            f"  context precision (judged)     {self.mean_context_precision_judged}",
            "",
            "  per case:",
        ]
        for row in self.rows:
            grounded = row.groundedness.rate if row.groundedness else None
            relevant = (
                ("relevant" if row.relevance.relevant else "OFF-QUESTION")
                if row.relevance
                else "-"
            )
            lines.append(
                f"    {row.case_id}  grounded {grounded}  {relevant}  "
                f"context {row.context} (recall of gold {row.context_recall_gold})"
            )
        findings = self.findings()
        if findings:
            lines += ["", f"  {len(findings)} finding(s):"]
            lines += [f"    - {finding}" for finding in findings]
        return "\n".join(lines)


class RagReport(BaseModel):
    """Retrieval, generation, and the judge's measured agreement. Never averaged."""

    model_config = ConfigDict(extra="forbid")

    k: int
    retrieval: RetrievalReport
    generation: GenerationReport
    calibration: CalibrationReport | None = None
    gate_error: str = Field(
        default="",
        description="Why the calibration gate refused, when it was asked and it did.",
    )

    def to_text(self) -> str:
        parts = [self.retrieval.to_text(), "", self.generation.to_text()]
        if self.calibration is not None:
            parts += [
                "",
                "THE INSTRUMENT — agreement of the support judge with hand labels",
                "",
                "  " + self.calibration.summary_line(),
            ]
            if self.calibration.disagreements:
                parts.append("  disagreements a human should read:")
                parts += [
                    f"    [{item.kind}] {item.item_id}: human={item.human_label} "
                    f"judge={item.judge_label}"
                    for item in self.calibration.disagreements
                ]
            verdict = "PASS" if self.calibration.passes() else "REFUSED"
            parts += ["", f"  calibration gate: {verdict}"]
            if self.gate_error:
                parts.append(f"    {self.gate_error}")
        else:
            parts += [
                "",
                "THE INSTRUMENT — not measured. Every judged number above is a "
                "reading from an uncalibrated instrument.",
            ]
        return "\n".join(parts)


def offline_judges(
    corpus: Corpus,
    dataset: RagDataset,
    contexts: dict[str, Retrieval],
    labels: Sequence[ClaimLabel] = (),
) -> tuple[Judge, Judge, Judge]:
    """The three judges, wired to one table of stand-in verdicts.

    One table covering the dataset probes *and* the label probes, so the
    calibration measures the very same verdicts the metrics are computed from
    rather than a parallel set that happens to look similar. Where the two
    overlap — every claim in the label file is also a claim in the dataset — the
    inputs are identical and the verdict is therefore identical, which is a
    property worth having rather than a coincidence to rely on.
    """
    oracle = LexicalOracle(corpus)
    probes = list(probes_for_dataset(dataset, contexts)) + list(label_probes(labels))
    completion = oracle.completion(probes)
    return (
        claim_support_judge(completion=completion, model=STAND_IN_MODEL),
        answer_relevance_judge(completion=completion, model=STAND_IN_MODEL),
        passage_relevance_judge(completion=completion, model=STAND_IN_MODEL),
    )


def evaluate(
    *,
    corpus: Corpus | None = None,
    dataset: RagDataset | None = None,
    retriever: Retriever | None = None,
    k: int = 3,
    support_judge: Judge | None = None,
    relevance_judge: Judge | None = None,
    passage_judge: Judge | None = None,
    labels: Sequence[ClaimLabel] | None = None,
    calibrate_support: bool = True,
    gate: bool = False,
    thresholds: CalibrationThresholds | None = None,
) -> RagReport:
    """Score retrieval and generation over `dataset`, and calibrate the grader.

    Args:
        corpus, dataset, retriever: default to the committed fixtures and the
            lexical retriever.
        k: retrieval window. Reported on every metric name that depends on it,
            because "recall 0.75" without a k is not a number.
        support_judge, relevance_judge, passage_judge: pass real judges to grade
            for real. Default to the offline stand-in.
        labels: hand labels for the support judge; default to the committed set.
        calibrate_support: measure the support judge against the labels. On by
            default: producing judged metrics without it is the failure mode this
            repository exists to argue against.
        gate: raise if the support judge is below `thresholds`. Off by default so
            exploration works, on in a pipeline — `lab.judges.registry` makes the
            same distinction for the same reason.

    Returns:
        A `RagReport`. With the stand-in oracle its calibration section reports a
        refusal, which is the correct outcome and not a bug in the run.
    """
    resolved_corpus = corpus if corpus is not None else load_corpus()
    resolved_dataset = (
        dataset if dataset is not None else load_cases(corpus=resolved_corpus)
    )
    resolved_dataset.validate_against(resolved_corpus)
    resolved_retriever = (
        retriever if retriever is not None else LexicalRetriever(resolved_corpus)
    )
    resolved_labels = list(labels) if labels is not None else load_claim_labels()

    contexts = contexts_for(resolved_dataset, resolved_corpus, resolved_retriever, k=k)

    if support_judge is None or relevance_judge is None or passage_judge is None:
        built = offline_judges(
            resolved_corpus, resolved_dataset, contexts, resolved_labels
        )
        support_judge = support_judge or built[0]
        relevance_judge = relevance_judge or built[1]
        passage_judge = passage_judge or built[2]

    calibration: CalibrationReport | None = None
    gate_error = ""
    if calibrate_support:
        # Calibrate before grading, and gate before reporting. Measuring the
        # instrument after quoting its readings is the wrong order in the way
        # that always resolves towards shipping.
        _, calibration = calibrate_claim_support(
            corpus=resolved_corpus, labels=resolved_labels, judge=support_judge
        )
        if gate:
            # Raises JudgeBelowThresholdError, naming the rate it fell short on.
            # Deliberately not caught: a caller that asked for a gate asked for
            # the run to stop, and a gate that returns a report with a sad note
            # in it is not a gate.
            require_calibrated(support_judge, thresholds=thresholds, ci=True)
        elif not calibration.passes(thresholds):
            _, failures = calibration.meets(thresholds or CalibrationThresholds())
            gate_error = (
                "this judge would be refused by evaluate(gate=True): "
                + "; ".join(failures)
            )
    elif gate:
        raise ValueError(
            "gate=True with calibrate_support=False asks for a gate on a "
            "measurement that was never taken"
        )

    rows: list[GenerationRow] = []
    for case in resolved_dataset:
        if not (case.has_answer or case.has_reference):
            continue
        retrieval = contexts[case.id]
        rows.append(
            GenerationRow(
                case_id=case.id,
                question=case.question,
                answer=case.answer or "",
                context=retrieval.ids,
                gold=list(case.gold),
                context_recall_gold=recall_at_k(retrieval.ids, case.gold_set, k),
                context_precision_gold=average_precision_at_k(
                    retrieval.ids, case.gold_set, k
                ),
                groundedness=(
                    groundedness(case, retrieval, support_judge) if case.has_answer else None
                ),
                relevance=(
                    answer_relevance(case, retrieval, relevance_judge)
                    if case.has_answer
                    else None
                ),
                context_recall=(
                    context_recall(case, retrieval, support_judge)
                    if case.has_reference
                    else None
                ),
                context_precision=judged_context_precision(
                    case, retrieval, passage_judge, k=k
                ),
            )
        )

    generation = GenerationReport(
        k=k,
        rows=rows,
        support_judge=f"{support_judge.name} {support_judge.version}",
        relevance_judge=f"{relevance_judge.name} {relevance_judge.version}",
        passage_judge=f"{passage_judge.name} {passage_judge.version}",
        judge_model=support_judge.model,
    )
    return RagReport(
        k=k,
        retrieval=evaluate_retrieval(resolved_dataset, resolved_retriever, k=k),
        generation=generation,
        calibration=calibration,
        gate_error=gate_error,
    )
