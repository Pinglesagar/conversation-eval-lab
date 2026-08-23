"""Retrieval metrics: recall@k, precision@k, MRR, nDCG@k, average precision.

WHAT THIS DEMONSTRATES
----------------------
The half of RAG evaluation that needs no model at all. Every number here is a
function of two lists — what the retriever returned, in order, and which chunk
ids actually answer the question — so every number is exactly reproducible, and
every one of them is checked in `tests/test_ragcheck_retrieval.py` against
arithmetic written out by hand.

That property is worth more than it sounds. When a groundedness figure moves,
the first question is always "did generation get worse, or did retrieval stop
finding the passage", and these metrics answer it without an oracle in the loop.

WHAT EACH METRIC IS FOR, AND WHERE IT LIES
------------------------------------------
    recall@k        Did the answer get into the window at all? The only metric
                    that bounds the whole system: a fact absent from the context
                    cannot be in a grounded answer, so recall@k is a ceiling on
                    every generation metric downstream. Blind to rank: a gold
                    chunk at position k scores the same as one at position 1.

    precision@k     How much of the window was worth sending. Matters because
                    context is paid for twice — in tokens and in the attention
                    the real passage has to compete for. Denominator is k, not
                    the number returned: a retriever that returns two passages
                    when asked for five has not earned a higher score for it.

    MRR             How far down the list the first useful passage sat. One
                    number per question, and it ignores everything after the
                    first hit, which makes it the wrong metric for a question
                    whose answer is split across two chunks.

    nDCG@k          Rank-discounted, and normalised by the best ordering that
                    was available, so a question with one gold chunk and a
                    question with three are comparable. Binary gains here;
                    graded relevance would change the gain, not the shape.

    AP@k            Average precision over the window — the gold-id form of what
                    Ragas calls context precision. Rewards putting the useful
                    passages first rather than merely including them.

TWO AVERAGES, BOTH REPORTED
---------------------------
A macro average (mean of the per-question rates) and a micro average (pooled
numerators over pooled denominators) are different numbers, and quoting one
without saying which is a small dishonesty that compounds. On this fixture the
questions have one or two gold chunks, so a question with two golds pulls the
micro average around twice as hard as it pulls the macro one. Both are printed,
labelled, with their denominators.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.judges.calibration import Rate

from ragcheck.corpus import Corpus, Retrieval, Retriever
from ragcheck.dataset import RagCase, RagDataset

__all__ = [
    "Score",
    "hit_at_k",
    "recall_at_k",
    "precision_at_k",
    "reciprocal_rank",
    "dcg_at_k",
    "ndcg_at_k",
    "average_precision_at_k",
    "RetrievalRow",
    "RetrievalReport",
    "evaluate_retrieval",
    "context_for",
    "contexts_for",
]


class Score(BaseModel):
    """A real-valued metric that knows how many items it stands on.

    The sibling of `lab.judges.calibration.Rate`, for the metrics that are not
    ratios of counts. nDCG is an average of discounted gains, not a fraction of
    anything, so it cannot honestly be printed as `9/16` — but it can and must be
    printed with its `n`, for the same reason: 0.92 over four questions and 0.92
    over four hundred are not the same claim.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None
    n: int = Field(ge=0, description="How many items the value was averaged over.")
    note: str = ""

    @classmethod
    def mean(cls, name: str, values: Sequence[float], *, note: str = "") -> Score:
        """The macro average of `values`, or an undefined score over none."""
        if not values:
            return cls(name=name, value=None, n=0, note=note)
        return cls(name=name, value=sum(values) / len(values), n=len(values), note=note)

    def __str__(self) -> str:
        if self.value is None:
            return "undefined (n=0)"
        return f"{self.value:.3f} (n={self.n})"

    def __repr__(self) -> str:
        return f"Score(name={self.name!r}, {self})"


def _checked(ranked: Sequence[str], gold: Iterable[str], k: int) -> tuple[list[str], set[str]]:
    """Validate the inputs every metric here shares.

    Duplicate retrieved ids are rejected rather than de-duplicated. A retriever
    that returns the same chunk twice has a bug, and silently collapsing it would
    hide the bug while inflating precision@k.
    """
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    ranked_list = list(ranked)
    if len(set(ranked_list)) != len(ranked_list):
        raise ValueError(f"duplicate ids in the ranking {ranked_list}")
    gold_set = set(gold)
    if not gold_set:
        raise ValueError(
            "gold must be non-empty: recall's denominator is |gold|, and a "
            "question with no known answer cannot be scored, only guessed at"
        )
    return ranked_list, gold_set


def hit_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> bool:
    """True when at least one gold chunk is in the top k."""
    ranked_list, gold_set = _checked(ranked, gold, k)
    return any(chunk_id in gold_set for chunk_id in ranked_list[:k])


def recall_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> Rate:
    """Gold chunks found in the top k, over all gold chunks."""
    ranked_list, gold_set = _checked(ranked, gold, k)
    found = sum(1 for chunk_id in ranked_list[:k] if chunk_id in gold_set)
    return Rate(name=f"recall@{k}", numerator=found, denominator=len(gold_set))


def precision_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> Rate:
    """Gold chunks in the top k, over k."""
    ranked_list, gold_set = _checked(ranked, gold, k)
    found = sum(1 for chunk_id in ranked_list[:k] if chunk_id in gold_set)
    return Rate(name=f"precision@{k}", numerator=found, denominator=k)


def reciprocal_rank(ranked: Sequence[str], gold: Iterable[str], k: int | None = None) -> float:
    """1 / (rank of the first gold chunk), or 0.0 when there is none in the top k."""
    limit = len(ranked) if k is None else k
    ranked_list, gold_set = _checked(ranked, gold, max(limit, 1))
    for position, chunk_id in enumerate(ranked_list[:limit], start=1):
        if chunk_id in gold_set:
            return 1.0 / position
    return 0.0


def dcg_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """Discounted cumulative gain with binary gains, log base 2 of (rank + 1)."""
    ranked_list, gold_set = _checked(ranked, gold, k)
    return sum(
        1.0 / math.log2(position + 1)
        for position, chunk_id in enumerate(ranked_list[:k], start=1)
        if chunk_id in gold_set
    )


def ndcg_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """nDCG@k: the DCG achieved over the best DCG that was achievable.

    The ideal ranking puts `min(k, |gold|)` gold chunks first, so a question with
    three gold chunks and k=2 is normalised against the best possible two, not
    against an unreachable three. Without that, nDCG would punish a perfect
    retriever for a window smaller than the answer.
    """
    ranked_list, gold_set = _checked(ranked, gold, k)
    achieved = dcg_at_k(ranked_list, gold_set, k)
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(k, len(gold_set)) + 1))
    return achieved / ideal if ideal else 0.0


def average_precision_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """AP@k: mean of precision@i over the positions i that hold a gold chunk.

    Divided by the number of gold chunks *inside the window* (Ragas's
    convention for context precision), not by |gold|. Dividing by |gold| would
    fold a recall failure into a precision figure, and then no single number
    tells you which of the two moved.
    """
    ranked_list, gold_set = _checked(ranked, gold, k)
    window = ranked_list[:k]
    relevant_in_window = sum(1 for chunk_id in window if chunk_id in gold_set)
    if relevant_in_window == 0:
        return 0.0
    running = 0.0
    hits = 0
    for position, chunk_id in enumerate(window, start=1):
        if chunk_id in gold_set:
            hits += 1
            running += hits / position
    return running / relevant_in_window


class RetrievalRow(BaseModel):
    """Every retrieval metric for one question, next to the evidence for them."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    k: int
    retrieved: list[str]
    gold: list[str]
    hit: bool
    recall: Rate
    precision: Rate
    reciprocal_rank: float
    ndcg: float
    average_precision: float

    @property
    def missed(self) -> list[str]:
        """Gold chunks the window did not contain — the ceiling on generation."""
        return [chunk_id for chunk_id in self.gold if chunk_id not in self.retrieved[: self.k]]


class RetrievalReport(BaseModel):
    """Per-question rows plus both averages, each carrying its denominator."""

    model_config = ConfigDict(extra="forbid")

    k: int
    rows: list[RetrievalRow]

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def hit_rate(self) -> Rate:
        return Rate(
            name=f"hit@{self.k}",
            numerator=sum(1 for row in self.rows if row.hit),
            denominator=self.n,
        )

    @property
    def pooled_recall(self) -> Rate:
        """Micro average: every gold chunk in the set weighs the same."""
        return Rate(
            name=f"recall@{self.k} (micro)",
            numerator=sum(row.recall.numerator for row in self.rows),
            denominator=sum(row.recall.denominator for row in self.rows),
        )

    @property
    def mean_recall(self) -> Score:
        """Macro average: every question weighs the same."""
        return Score.mean(
            f"recall@{self.k} (macro)",
            [row.recall.value or 0.0 for row in self.rows],
        )

    @property
    def mean_precision(self) -> Score:
        return Score.mean(
            f"precision@{self.k} (macro)", [row.precision.value or 0.0 for row in self.rows]
        )

    @property
    def mrr(self) -> Score:
        return Score.mean(f"MRR@{self.k}", [row.reciprocal_rank for row in self.rows])

    @property
    def mean_ndcg(self) -> Score:
        return Score.mean(f"nDCG@{self.k}", [row.ndcg for row in self.rows])

    @property
    def mean_average_precision(self) -> Score:
        return Score.mean(
            f"MAP@{self.k}", [row.average_precision for row in self.rows], note="gold-id context precision"
        )

    def rows_with_misses(self) -> list[RetrievalRow]:
        """The questions whose answer never made it into the window.

        The list to read first: every one of these caps some generation metric
        below 1.0 for a reason that has nothing to do with the generator.
        """
        return [row for row in self.rows if row.missed]

    def to_text(self) -> str:
        lines = [
            f"RETRIEVAL — {self.n} questions, k={self.k}",
            "",
            f"  hit@{self.k}                {self.hit_rate}",
            f"  recall@{self.k} (macro)     {self.mean_recall}",
            f"  recall@{self.k} (micro)     {self.pooled_recall}",
            f"  precision@{self.k} (macro)  {self.mean_precision}",
            f"  MRR                       {self.mrr}",
            f"  nDCG@{self.k}               {self.mean_ndcg}",
            f"  MAP@{self.k}                {self.mean_average_precision}",
        ]
        misses = self.rows_with_misses()
        if misses:
            lines += ["", "  questions whose window missed a gold chunk:"]
            lines += [
                f"    {row.case_id}  missed {row.missed}  got {row.retrieved[: self.k]}"
                for row in misses
            ]
        return "\n".join(lines)


def evaluate_retrieval(
    dataset: RagDataset, retriever: Retriever, *, k: int = 3
) -> RetrievalReport:
    """Score `retriever` over every question in `dataset`.

    Deliberately ignores any pinned `retrieved` on a row: a pinned context exists
    so a *generation* metric can be isolated, and scoring the retriever against
    a context somebody wrote by hand would measure nothing.
    """
    rows: list[RetrievalRow] = []
    for case in dataset:
        retrieval = retriever.retrieve(case.question, k=k)
        ids = retrieval.ids
        rows.append(
            RetrievalRow(
                case_id=case.id,
                question=case.question,
                k=k,
                retrieved=ids,
                gold=list(case.gold),
                hit=hit_at_k(ids, case.gold_set, k),
                recall=recall_at_k(ids, case.gold_set, k),
                precision=precision_at_k(ids, case.gold_set, k),
                reciprocal_rank=reciprocal_rank(ids, case.gold_set, k),
                ndcg=ndcg_at_k(ids, case.gold_set, k),
                average_precision=average_precision_at_k(ids, case.gold_set, k),
            )
        )
    return RetrievalReport(k=k, rows=rows)


def context_for(
    case: RagCase, corpus: Corpus, retriever: Retriever | None = None, *, k: int = 3
) -> Retrieval:
    """The context a *generation* metric should be measured against for `case`.

    A pinned `retrieved` wins over the live retriever, and a case with neither a
    pin nor a retriever is an error rather than an empty context — a groundedness
    figure computed against nothing would read as a perfect score.

    One function, used by the metrics and by the offline oracle alike, so the two
    can never disagree about which passages a given case was answered from.
    """
    if case.retrieved:
        chunks = corpus.select(case.retrieved[:k])
        return Retrieval(query=case.question, chunks=chunks, scores=[1.0] * len(chunks))
    if retriever is None:
        raise ValueError(
            f"case {case.id} pins no context and no retriever was supplied: there is "
            "nothing to measure the answer against"
        )
    return retriever.retrieve(case.question, k=k)


def contexts_for(
    dataset: RagDataset, corpus: Corpus, retriever: Retriever | None = None, *, k: int = 3
) -> dict[str, Retrieval]:
    """`{case_id: context}` for every answered case in `dataset`."""
    return {
        case.id: context_for(case, corpus, retriever, k=k)
        for case in dataset
        if case.has_answer or case.has_reference
    }
