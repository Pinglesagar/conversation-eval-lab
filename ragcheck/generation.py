"""Generation metrics: groundedness, answer relevance, context recall/precision.

WHAT THIS DEMONSTRATES
----------------------
The half of RAG evaluation that needs an oracle, arranged so that the oracle is
a *parameter* and the arithmetic is not. Every function here takes a judge, and
what it does with the judge's binary verdicts is fully determined: count them,
divide, and keep the per-item verdicts attached to the number so a disputed
figure can be read rather than re-run.

That separation is the point. Swap the judge for a better model, a cheaper one,
a human, or the deliberately weak lexical stand-in in `ragcheck.offline`, and
the metric definition does not move. What moves is how much the number can be
trusted — which is what `lab.judges.calibration` measures and what
`require_calibrated()` refuses to let you skip.

THE FOUR METRICS, AND THE ONE QUESTION EACH ANSWERS
---------------------------------------------------
    groundedness        Of the claims the answer made, how many does the
                        retrieved context support? Catches the failure retrieval
                        metrics cannot see: perfect context, invented answer.
                        (Ragas: faithfulness. DeepEval: FaithfulnessMetric.)

    answer relevance    Did the answer address the question that was asked?
                        Catches the failure groundedness cannot see: every claim
                        supported, and none of them the answer.
                        (Ragas: answer_relevancy — by generating questions from
                        the answer and comparing embeddings, not like this; see
                        docs/RAG_NOTES.md for why this one is binary.)

    context recall      Of the claims a *reference* answer makes, how many could
                        be supported from the retrieved context? Catches the
                        failure both of the above miss: a faithful, relevant,
                        incomplete answer, because the context never contained
                        the missing half. This is the only metric here that needs
                        a written reference answer, and it is the one that tells
                        a retrieval team they own the bug.

    context precision   Were the retrieved passages worth retrieving, weighted by
                        where they sat in the ranking? Two forms:
                        `average_precision_at_k` in `ragcheck.retrieval` when gold
                        ids exist (no oracle, exactly reproducible), and
                        `judged_context_precision` here when they do not.

WHY THE CLAIM IS THE UNIT
-------------------------
A per-answer "is this faithful" verdict throws away the only thing that makes a
groundedness figure actionable: which sentence was invented. Per-claim verdicts
give a fraction whose numerator names its own failures, and — because a claim
trace is one question, one context, one sentence — they are also the granularity
a human can label at speed and at high agreement. Both of those matter more than
the aggregate.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.judges.calibration import Rate
from lab.judges.judge import Judge, Verdict

from ragcheck.claims import split_claims
from ragcheck.corpus import Retrieval
from ragcheck.dataset import RagCase
from ragcheck.traces import claim_trace, rag_trace

__all__ = [
    "claim_item_id",
    "answer_item_id",
    "passage_item_id",
    "ClaimVerdict",
    "SupportResult",
    "RelevanceResult",
    "ContextPrecisionResult",
    "groundedness",
    "context_recall",
    "answer_relevance",
    "judged_context_precision",
    "pooled",
]


# --------------------------------------------------------------------------- #
# Item ids
# --------------------------------------------------------------------------- #
#
# One place, three functions, because an item id is the join key between a
# metric, a judge recording and a human label file. When those drift apart the
# symptom is a calibration report that silently measures the wrong items, and
# nothing about it looks broken.


def claim_item_id(case_id: str, index: int, *, kind: str = "claim") -> str:
    """`c02#claim2` — the second claim of case c02's answer. 1-based."""
    return f"{case_id}#{kind}{index}"


def answer_item_id(case_id: str) -> str:
    """`c02#answer` — the whole answer, judged as one."""
    return f"{case_id}#answer"


def passage_item_id(case_id: str, rank: int) -> str:
    """`c02#passage1` — the passage retrieved at rank 1. 1-based."""
    return f"{case_id}#passage{rank}"


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


class ClaimVerdict(BaseModel):
    """One claim, the verdict on it, and the judge's reason for that verdict."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    claim: str
    supported: bool
    critique: str = ""
    evidence: str | None = None
    parse_error: bool = False

    @classmethod
    def from_verdict(cls, claim: str, verdict: Verdict) -> ClaimVerdict:
        return cls(
            item_id=verdict.item_id,
            claim=claim,
            supported=verdict.passed,
            critique=verdict.critique,
            evidence=verdict.evidence,
            parse_error=verdict.parse_error,
        )


class SupportResult(BaseModel):
    """A claim-level fraction for one case, with every verdict behind it.

    Used by both groundedness and context recall: same shape, different claims.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    metric: str
    claims: list[ClaimVerdict] = Field(default_factory=list)

    @property
    def rate(self) -> Rate:
        return Rate(
            name=f"{self.metric}[{self.case_id}]",
            numerator=sum(1 for claim in self.claims if claim.supported),
            denominator=len(self.claims),
        )

    @property
    def unsupported(self) -> list[ClaimVerdict]:
        """The claims that failed — the actionable half of the number."""
        return [claim for claim in self.claims if not claim.supported]


class RelevanceResult(BaseModel):
    """Whether one answer addressed its question, and why the judge said so."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    item_id: str
    relevant: bool
    critique: str = ""
    evidence: str | None = None
    parse_error: bool = False


class ContextPrecisionResult(BaseModel):
    """Judge-scored average precision over the retrieved window."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    k: int
    useful: list[bool] = Field(default_factory=list)
    critiques: list[str] = Field(default_factory=list)

    @property
    def value(self) -> float | None:
        """AP over the window, or None when nothing was retrieved.

        Same formula as `ragcheck.retrieval.average_precision_at_k`, with the
        judge's verdicts standing in for gold ids. 0.0 when the window holds
        nothing useful; None when there was no window to score, because those two
        are different findings and averaging them together hides the second.
        """
        if not self.useful:
            return None
        relevant = sum(1 for flag in self.useful if flag)
        if relevant == 0:
            return 0.0
        running = 0.0
        hits = 0
        for position, flag in enumerate(self.useful, start=1):
            if flag:
                hits += 1
                running += hits / position
        return running / relevant


# --------------------------------------------------------------------------- #
# The metrics
# --------------------------------------------------------------------------- #


def _support(
    *,
    case: RagCase,
    retrieval: Retrieval,
    judge: Judge,
    text: str,
    kind: str,
    metric: str,
) -> SupportResult:
    """Split `text` into claims and ask `judge` about each one, in order."""
    claims = split_claims(text)
    verdicts: list[ClaimVerdict] = []
    for index, claim in enumerate(claims, start=1):
        trace = claim_trace(
            case_id=case.id,
            question=case.question,
            retrieval=retrieval,
            claim=claim,
            index=index,
            kind=kind,
        )
        verdict = judge.judge(trace, item_id=claim_item_id(case.id, index, kind=kind))
        verdicts.append(ClaimVerdict.from_verdict(claim, verdict))
    return SupportResult(case_id=case.id, metric=metric, claims=verdicts)


def groundedness(case: RagCase, retrieval: Retrieval, judge: Judge) -> SupportResult:
    """Supported claims over all claims, for the answer the system gave.

    Raises when the case has no answer: a groundedness of 0/0 prints as
    `undefined` and gets read as a pass, so the honest response to "there is
    nothing to grade" is to say so.
    """
    if not case.has_answer:
        raise ValueError(
            f"case {case.id} carries no answer, so groundedness is not undefined — "
            "it is inapplicable. Filter with RagDataset.answered()."
        )
    return _support(
        case=case,
        retrieval=retrieval,
        judge=judge,
        text=case.answer or "",
        kind="claim",
        metric="groundedness",
    )


def context_recall(case: RagCase, retrieval: Retrieval, judge: Judge) -> SupportResult:
    """Reference claims the retrieved context could support, over all of them.

    The reference answer is the ground truth, so this measures the *context*, not
    the generator: a low figure means retrieval did not fetch what an answer
    needed, whatever the generator then did with it.
    """
    if not case.has_reference:
        raise ValueError(
            f"case {case.id} carries no reference answer, and context recall is "
            "measured against a reference by definition. Write one, or leave this "
            "metric out for this row."
        )
    return _support(
        case=case,
        retrieval=retrieval,
        judge=judge,
        text=case.reference or "",
        kind="ref",
        metric="context_recall",
    )


def answer_relevance(case: RagCase, retrieval: Retrieval, judge: Judge) -> RelevanceResult:
    """Did the answer address the question? One binary verdict per case."""
    if not case.has_answer:
        raise ValueError(f"case {case.id} carries no answer to judge for relevance")
    trace = rag_trace(
        case_id=case.id,
        question=case.question,
        retrieval=retrieval,
        answer=case.answer,
        session_id=answer_item_id(case.id),
    )
    verdict = judge.judge(trace, item_id=answer_item_id(case.id))
    return RelevanceResult(
        case_id=case.id,
        item_id=verdict.item_id,
        relevant=verdict.passed,
        critique=verdict.critique,
        evidence=verdict.evidence,
        parse_error=verdict.parse_error,
    )


def judged_context_precision(
    case: RagCase, retrieval: Retrieval, judge: Judge, *, k: int | None = None
) -> ContextPrecisionResult:
    """Ask the judge about each retrieved passage separately, then average.

    Each passage is judged alone, in its own single-chunk trace. Showing the
    judge the whole window and asking which parts were useful invites it to
    reason about the set — and a passage's usefulness would then depend on what
    else was retrieved, which is not what precision means.
    """
    window = retrieval.chunks if k is None else retrieval.chunks[:k]
    useful: list[bool] = []
    critiques: list[str] = []
    for rank, chunk in enumerate(window, start=1):
        single = Retrieval(query=retrieval.query, chunks=[chunk], scores=[1.0])
        trace = rag_trace(
            case_id=case.id,
            question=case.question,
            retrieval=single,
            session_id=passage_item_id(case.id, rank),
        )
        verdict = judge.judge(trace, item_id=passage_item_id(case.id, rank))
        useful.append(verdict.passed)
        critiques.append(verdict.critique)
    return ContextPrecisionResult(
        case_id=case.id, k=len(window), useful=useful, critiques=critiques
    )


def pooled(results: Sequence[SupportResult], *, name: str) -> Rate:
    """One rate over every claim in `results` — the micro average.

    Micro, not macro, and the choice is deliberate: a case with six claims
    contains six chances to hallucinate, and a macro average would give it the
    same weight as a one-sentence answer. `RagReport` prints both.
    """
    return Rate(
        name=name,
        numerator=sum(result.rate.numerator for result in results),
        denominator=sum(result.rate.denominator for result in results),
    )
