"""Retrieval metrics, checked against arithmetic written out by hand.

WHAT THIS DEMONSTRATES
----------------------
The only honest way to test a metrics module: every expected value here is
derived in the test's own docstring from a ranking small enough to verify by
eye. An implementation compared against a second implementation of the same
formula agrees with itself, including everywhere both are wrong.

Three of these tests are the ones worth reading:

*   `test_micro_and_macro_recall_are_different_numbers` — the reason both are
    printed. 0.750 and 0.667 over the same two questions.
*   `test_ndcg_normalises_against_the_best_reachable_ranking` — a perfect
    retriever scores 1.0 even when k is smaller than the number of gold chunks.
    Normalising against an unreachable ideal would punish it for the window.
*   `test_average_precision_divides_by_the_relevant_items_in_the_window` — why a
    recall failure must not be folded into a precision figure.
"""

from __future__ import annotations

import pytest

from ragcheck.corpus import LexicalRetriever, load_corpus
from ragcheck.dataset import load_cases
from ragcheck.retrieval import (
    Score,
    average_precision_at_k,
    dcg_at_k,
    evaluate_retrieval,
    hit_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

# One ranking, reused: gold chunks at positions 1 and 3 of three.
RANKED = ["c3", "c1", "c7"]
GOLD = {"c3", "c7"}


# --------------------------------------------------------------------------- #
# recall, precision, hit
# --------------------------------------------------------------------------- #


def test_recall_at_k_counts_gold_chunks_inside_the_window() -> None:
    """recall@1 = 1/2, recall@2 = 1/2, recall@3 = 2/2.

    c3 is at rank 1 and c7 at rank 3, so widening the window from 2 to 3 is
    what finds the second gold chunk. The denominator is |gold| = 2 throughout:
    recall's denominator is a property of the question, not of k.
    """
    assert (recall_at_k(RANKED, GOLD, 1).numerator, recall_at_k(RANKED, GOLD, 1).denominator) == (1, 2)
    assert str(recall_at_k(RANKED, GOLD, 2)) == "0.500 (1/2)"
    assert str(recall_at_k(RANKED, GOLD, 3)) == "1.000 (2/2)"


def test_precision_at_k_divides_by_k_even_when_fewer_were_returned() -> None:
    """precision@3 = 2/3; precision@5 = 2/5 on a list of three.

    A retriever asked for five and returning three has not earned a smaller
    denominator for it. The alternative — dividing by what came back — lets a
    retriever raise its precision by returning less, which is a scoreboard a
    system can game without improving.
    """
    assert str(precision_at_k(RANKED, GOLD, 3)) == "0.667 (2/3)"
    assert str(precision_at_k(RANKED, GOLD, 5)) == "0.400 (2/5)"


def test_hit_at_k_is_true_as_soon_as_one_gold_chunk_is_in_the_window() -> None:
    assert hit_at_k(RANKED, {"c7"}, 3) is True
    assert hit_at_k(RANKED, {"c7"}, 2) is False


def test_recall_of_a_question_whose_answer_was_never_retrieved_is_zero_not_undefined() -> None:
    """0/2 is a finding; undefined would be a missing measurement."""
    rate = recall_at_k(RANKED, {"c9", "c10"}, 3)
    assert (rate.value, str(rate)) == (0.0, "0.000 (0/2)")


# --------------------------------------------------------------------------- #
# rank-sensitive metrics
# --------------------------------------------------------------------------- #


def test_reciprocal_rank_is_one_over_the_first_hit() -> None:
    """First gold chunk at rank 3 -> RR = 1/3; at rank 1 -> 1.0; absent -> 0.0."""
    assert reciprocal_rank(RANKED, {"c7"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(RANKED, {"c3"}) == 1.0
    assert reciprocal_rank(RANKED, {"c9"}) == 0.0


def test_reciprocal_rank_ignores_everything_after_the_first_hit() -> None:
    """RR cannot tell "one gold chunk at rank 1" from "three of them".

    Written down as a test because it is the reason MRR is the wrong headline
    for a question whose answer is split across passages — c18 in the fixture
    set scores RR = 1.0 while holding only half the answer.
    """
    assert reciprocal_rank(RANKED, {"c3"}) == reciprocal_rank(RANKED, {"c3", "c1", "c7"})


def test_dcg_discounts_by_log2_of_the_rank_plus_one() -> None:
    """Gold at ranks 1 and 3: 1/log2(2) + 1/log2(4) = 1.0 + 0.5 = 1.5."""
    assert dcg_at_k(RANKED, GOLD, 3) == pytest.approx(1.5)


def test_ndcg_at_k_is_dcg_over_the_ideal_dcg() -> None:
    """1.5 / (1/log2(2) + 1/log2(3)) = 1.5 / 1.630930 = 0.919721.

    The ideal ranking puts both gold chunks at ranks 1 and 2, which this ranking
    did not, so nDCG is below 1.0 even though recall@3 is perfect. That gap is
    the whole content of a rank-aware metric.
    """
    assert ndcg_at_k(RANKED, GOLD, 3) == pytest.approx(0.919721, abs=1e-6)


def test_ndcg_normalises_against_the_best_reachable_ranking() -> None:
    """Three gold chunks, k=2, both slots filled with gold: nDCG = 1.0.

    ideal = 1/log2(2) + 1/log2(3) — min(k, |gold|) terms, not |gold| terms. A
    window of two cannot hold three passages, and a metric that scores a perfect
    retriever at 0.68 for that is measuring the window, not the retriever.
    """
    assert ndcg_at_k(["a", "b"], {"a", "b", "c"}, 2) == pytest.approx(1.0)


def test_average_precision_divides_by_the_relevant_items_in_the_window() -> None:
    """Hits at ranks 1 and 3 of three: (1/1 + 2/3) / 2 = 0.833333.

    The divisor is the two gold chunks *inside* the window. Dividing by |gold|
    instead would drag a precision figure down whenever retrieval missed
    something, and then a single number would be moved by two different
    failures — which is how a dashboard stops being diagnostic.
    """
    assert average_precision_at_k(RANKED, GOLD, 3) == pytest.approx(0.833333, abs=1e-6)


def test_average_precision_rewards_putting_the_useful_passage_first() -> None:
    """Same two gold chunks, ranked 1-2 instead of 1-3: (1/1 + 2/2)/2 = 1.0."""
    assert average_precision_at_k(["c3", "c7", "c1"], GOLD, 3) == pytest.approx(1.0)


def test_average_precision_of_an_empty_window_is_zero() -> None:
    assert average_precision_at_k(["x", "y"], GOLD, 2) == 0.0


# --------------------------------------------------------------------------- #
# input validation
# --------------------------------------------------------------------------- #


def test_a_duplicated_retrieved_id_is_an_error_not_a_deduplication() -> None:
    """A retriever returning the same chunk twice has a bug.

    Silently collapsing the duplicate would hide it while inflating precision@k,
    so the metric refuses the input instead.
    """
    with pytest.raises(ValueError, match="duplicate ids"):
        recall_at_k(["a", "a", "b"], {"a"}, 3)


def test_an_empty_gold_set_is_refused() -> None:
    with pytest.raises(ValueError, match="gold must be non-empty"):
        recall_at_k(RANKED, set(), 3)


def test_k_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="k must be at least 1"):
        precision_at_k(RANKED, GOLD, 0)


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def test_micro_and_macro_recall_are_different_numbers() -> None:
    """Two questions: one gold found of one, one gold found of two.

        macro = (1.000 + 0.500) / 2 = 0.750     every question weighs the same
        micro = (1 + 1) / (1 + 2)  = 0.667      every gold chunk weighs the same

    Both are correct and they answer different questions. Reporting one of them
    as "recall" without saying which is the small dishonesty this test exists to
    make impossible to commit by accident.
    """
    from ragcheck.dataset import RagCase, RagDataset

    class OneShot:
        def __init__(self, table: dict[str, list[str]]) -> None:
            self.table = table

        def retrieve(self, query: str, *, k: int):
            from ragcheck.corpus import Chunk, Retrieval

            ids = self.table[query][:k]
            return Retrieval(
                query=query,
                chunks=[Chunk(id=chunk_id, text="x") for chunk_id in ids],
                scores=[1.0] * len(ids),
            )

    dataset = RagDataset(
        cases=[
            RagCase(id="one", question="q1", gold=["a"]),
            RagCase(id="two", question="q2", gold=["b", "c"]),
        ]
    )
    report = evaluate_retrieval(dataset, OneShot({"q1": ["a"], "q2": ["b", "z"]}), k=2)
    assert report.mean_recall.value == pytest.approx(0.75)
    assert report.pooled_recall.value == pytest.approx(2 / 3)
    assert str(report.pooled_recall) == "0.667 (2/3)"


def test_a_score_refuses_to_print_without_its_n() -> None:
    """0.920 over 4 items and over 400 are not the same claim."""
    assert str(Score.mean("x", [1.0, 0.8])) == "0.900 (n=2)"
    assert str(Score.mean("x", [])) == "undefined (n=0)"


# --------------------------------------------------------------------------- #
# the committed fixture, as a baseline
# --------------------------------------------------------------------------- #


def test_the_lexical_retriever_scores_a_recorded_baseline_on_the_fixture_set() -> None:
    """The toy retriever's measured numbers, pinned so a change shows up.

    Not a target and not an aspiration: a baseline. If the corpus, the questions
    or the scoring change, this test fails and somebody has to decide whether the
    new number is better. That is the entire mechanism by which an eval suite
    catches a regression, and it works exactly as well on a thirty-line lexical
    retriever as on a vector store.
    """
    corpus = load_corpus()
    dataset = load_cases(corpus=corpus)
    report = evaluate_retrieval(dataset, LexicalRetriever(corpus), k=3)

    assert str(report.hit_rate) == "0.778 (14/18)"
    assert str(report.pooled_recall) == "0.750 (15/20)"
    assert report.mean_recall.value == pytest.approx(0.75)
    assert report.mrr.value == pytest.approx(0.722222, abs=1e-6)
    assert report.mean_ndcg.value == pytest.approx(0.715278, abs=1e-6)

    # The four questions the lexical retriever cannot answer at all, plus c18,
    # whose answer is split across two chunks and only one is retrieved.
    assert [row.case_id for row in report.rows_with_misses()] == [
        "c03",
        "c14",
        "c15",
        "c16",
        "c18",
    ]


def test_widening_k_raises_recall_and_lowers_precision_on_the_fixture_set() -> None:
    """The trade-off every k is a choice about, measured rather than asserted."""
    corpus = load_corpus()
    dataset = load_cases(corpus=corpus)
    retriever = LexicalRetriever(corpus)

    narrow = evaluate_retrieval(dataset, retriever, k=1)
    wide = evaluate_retrieval(dataset, retriever, k=5)

    assert narrow.pooled_recall.value is not None and wide.pooled_recall.value is not None
    assert wide.pooled_recall.value > narrow.pooled_recall.value
    assert wide.mean_precision.value < narrow.mean_precision.value
