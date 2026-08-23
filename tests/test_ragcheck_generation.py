"""Generation metrics: the arithmetic, and the three failures they separate.

WHAT THIS DEMONSTRATES
----------------------
Every judged metric is tested with the judge's answers *dictated by the test* —
a `ScriptedCompletion` keyed by item id — so what is under test is the counting,
the denominator and the aggregation, never a model's opinion. The judge is a
parameter everywhere in `ragcheck`, and this file is what that buys: exact
expected values for metrics that are usually described as "fuzzy".

The three tests to read are the three failures each metric alone cannot see:

*   `test_perfect_retrieval_with_an_invented_figure_scores_half` — recall@3 =
    1.0, groundedness = 1/2. The classic. A retrieval-only suite passes this row.
*   `test_a_fully_grounded_answer_can_be_entirely_off_question` — groundedness =
    2/2, relevance = fail. Faithfulness cannot see the wrong question.
*   `test_context_recall_blames_retrieval_for_an_incomplete_answer` —
    groundedness = 1/1, relevance = pass, context recall = 1/2. Neither of the
    other two can see a missing passage; only the reference answer can.
"""

from __future__ import annotations

import json

import pytest

from lab.judges.judge import JudgeParseError, MissingRecordingError, ScriptedCompletion

from ragcheck.corpus import Retrieval, load_corpus
from ragcheck.dataset import RagCase, load_cases
from ragcheck.generation import (
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
from ragcheck.retrieval import context_for, recall_at_k

CORPUS = load_corpus()
CASES = load_cases(corpus=CORPUS)


def _raw(passed: bool, critique: str = "dictated by the test") -> str:
    return json.dumps({"verdict": "pass" if passed else "fail", "critique": critique})


def _support_judge(answers: dict[str, bool]):
    return claim_support_judge(
        completion=ScriptedCompletion({k: _raw(v) for k, v in answers.items()}),
        model="test/stub",
    )


def _relevance_judge(answers: dict[str, bool]):
    return answer_relevance_judge(
        completion=ScriptedCompletion({k: _raw(v) for k, v in answers.items()}),
        model="test/stub",
    )


def _passage_judge(answers: dict[str, bool]):
    return passage_relevance_judge(
        completion=ScriptedCompletion({k: _raw(v) for k, v in answers.items()}),
        model="test/stub",
    )


def _context(case_id: str, k: int = 3) -> Retrieval:
    return context_for(CASES.get(case_id), CORPUS, k=k)


# --------------------------------------------------------------------------- #
# groundedness
# --------------------------------------------------------------------------- #


def test_groundedness_is_supported_claims_over_claims_and_names_the_failures() -> None:
    """Two claims, one supported: 0.500 (1/2), and the unsupported one is quoted.

    The numerator naming its own failures is the difference between a metric and
    a bug report. "groundedness 0.5" starts an investigation; "c02#claim2: 'It is
    GBP 25 per person'" ends one.
    """
    case = CASES.get("c02")
    result = groundedness(
        case,
        _context("c02"),
        _support_judge({"c02#claim1": True, "c02#claim2": False}),
    )
    assert str(result.rate) == "0.500 (1/2)"
    assert [claim.claim for claim in result.unsupported] == [
        "It is GBP 25 per person, taken on the night."
    ]
    assert result.claims[0].item_id == "c02#claim1"


def test_perfect_retrieval_with_an_invented_figure_scores_half() -> None:
    """recall@3 = 1/1 and groundedness = 1/2 on the same row.

    This is the case that answers "why not just measure retrieval". The passage
    holding the answer was retrieved at rank 1; the answer states GBP 25 where
    the passage says 15. Every retrieval metric on the row is 1.0.
    """
    case = CASES.get("c02")
    retrieval = _context("c02")
    assert str(recall_at_k(retrieval.ids, case.gold_set, 3)) == "1.000 (1/1)"

    result = groundedness(
        case, retrieval, _support_judge({"c02#claim1": True, "c02#claim2": False})
    )
    assert result.rate.value == 0.5


def test_a_case_with_no_answer_is_inapplicable_rather_than_a_perfect_score() -> None:
    """0/0 prints as `undefined` and gets read as a pass, so it raises instead."""
    unanswered = CASES.get("c03")
    context = Retrieval(query=unanswered.question, chunks=CORPUS.select(["p03"]))
    with pytest.raises(ValueError, match="inapplicable"):
        groundedness(unanswered, context, _support_judge({}))


def test_an_item_the_judge_was_never_given_stops_the_run() -> None:
    """A missing answer must not become a default verdict.

    `ScriptedCompletion` raises for an unknown item id, which is the behaviour
    worth having: a silently defaulted verdict is a number nobody can trace.
    """
    with pytest.raises(MissingRecordingError):
        groundedness(CASES.get("c02"), _context("c02"), _support_judge({}))


def test_unreadable_judge_output_fails_closed_and_is_flagged() -> None:
    """Never "pass". A judge that defaults to pass on a provider hiccup converts
    an outage into a green build."""
    judge = claim_support_judge(
        completion=ScriptedCompletion({"c09#claim1": "banana"}),
        model="test/stub",
        strict=False,
    )
    result = groundedness(CASES.get("c09"), _context("c09"), judge)
    assert result.claims[0].supported is False
    assert result.claims[0].parse_error is True

    strict = claim_support_judge(
        completion=ScriptedCompletion({"c09#claim1": "banana"}), model="test/stub"
    )
    with pytest.raises(JudgeParseError):
        groundedness(CASES.get("c09"), _context("c09"), strict)


# --------------------------------------------------------------------------- #
# answer relevance
# --------------------------------------------------------------------------- #


def test_a_fully_grounded_answer_can_be_entirely_off_question() -> None:
    """c12: groundedness 2/2, relevance fail.

    The answer describes the Cellar Room's capacity and minimum spend, both
    supported by a retrieved passage, and the question was about the dress code.
    Groundedness cannot see this by construction — it only ever asks whether the
    context supports the answer — which is the argument against gating on
    faithfulness alone.
    """
    case = CASES.get("c12")
    retrieval = _context("c12")
    grounded = groundedness(
        case, retrieval, _support_judge({"c12#claim1": True, "c12#claim2": True})
    )
    relevance = answer_relevance(case, retrieval, _relevance_judge({"c12#answer": False}))

    assert str(grounded.rate) == "1.000 (2/2)"
    assert relevance.relevant is False
    assert relevance.item_id == "c12#answer"


def test_answer_relevance_is_one_verdict_per_answer() -> None:
    case = CASES.get("c01")
    relevance = answer_relevance(
        case, _context("c01"), _relevance_judge({"c01#answer": True})
    )
    assert relevance.relevant is True


# --------------------------------------------------------------------------- #
# context recall and context precision
# --------------------------------------------------------------------------- #


def test_context_recall_blames_retrieval_for_an_incomplete_answer() -> None:
    """c18: groundedness 1/1, relevance pass, context recall 1/2.

    The generator was handed a context missing p01 and stayed inside it, which is
    what was asked of it. The reference answer names both facts, and only one of
    them is in the context — so the finding is a retrieval finding, and context
    recall is the only metric here that says so.
    """
    case = CASES.get("c18")
    retrieval = _context("c18")

    grounded = groundedness(case, retrieval, _support_judge({"c18#claim1": True}))
    relevance = answer_relevance(case, retrieval, _relevance_judge({"c18#answer": True}))
    recall = context_recall(
        case, retrieval, _support_judge({"c18#ref1": False, "c18#ref2": True})
    )

    assert str(grounded.rate) == "1.000 (1/1)"
    assert relevance.relevant is True
    assert str(recall.rate) == "0.500 (1/2)"
    assert "GBP 15 per person" in recall.unsupported[0].claim
    # And the deterministic retrieval metric on the same row agrees, without any
    # oracle at all: the context holds one of the two gold chunks.
    assert str(recall_at_k(retrieval.ids, case.gold_set, 3)) == "0.500 (1/2)"


def test_context_recall_needs_a_reference_and_says_so() -> None:
    with pytest.raises(ValueError, match="measured against a reference"):
        context_recall(CASES.get("c02"), _context("c02"), _support_judge({}))


def test_judged_context_precision_matches_the_hand_computed_average_precision() -> None:
    """Useful at ranks 1 and 3 of three: (1/1 + 2/3)/2 = 0.833333.

    Identical arithmetic to `average_precision_at_k`, with the judge's verdicts
    standing in for gold ids — so the two forms of context precision are
    comparable, and the only difference between them is whether an oracle was
    needed.
    """
    result = judged_context_precision(
        CASES.get("c01"),
        _context("c01"),
        _passage_judge({"c01#passage1": True, "c01#passage2": False, "c01#passage3": True}),
    )
    assert result.value == pytest.approx(0.833333, abs=1e-6)
    assert result.k == 3


def test_each_passage_is_judged_alone() -> None:
    """A passage's usefulness must not depend on what else was retrieved.

    Judged one at a time, so the item ids are per rank and each prompt carries a
    single-chunk context. Showing the judge the whole window and asking which
    parts helped invites it to reason about the set, and that is not precision.
    """
    judge = _passage_judge(
        {"c05#passage1": True, "c05#passage2": False, "c05#passage3": False}
    )
    result = judged_context_precision(CASES.get("c05"), _context("c05"), judge)
    assert result.useful == [True, False, False]
    assert result.value == pytest.approx(1.0)  # (1/1) / 1 relevant item


def test_a_window_with_nothing_useful_scores_zero_and_an_empty_one_is_undefined() -> None:
    """0.0 and None are different findings, and averaging them hides the second."""
    judge = _passage_judge(
        {"c09#passage1": False, "c09#passage2": False, "c09#passage3": False}
    )
    assert judged_context_precision(CASES.get("c09"), _context("c09"), judge).value == 0.0

    empty = judged_context_precision(
        CASES.get("c09"), Retrieval(query="q"), _passage_judge({})
    )
    assert empty.value is None


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def test_pooling_weights_every_claim_rather_than_every_answer() -> None:
    """A six-claim answer has six chances to hallucinate.

        micro = (1 + 2) / (2 + 3) = 0.600
        macro = (0.500 + 0.667) / 2 = 0.583

    Both are reported by `GenerationReport`; `pooled()` is the micro one, and the
    docstring says which so a reader never has to guess.
    """
    first = groundedness(
        CASES.get("c02"),
        _context("c02"),
        _support_judge({"c02#claim1": True, "c02#claim2": False}),
    )
    second = groundedness(
        CASES.get("c05"),
        _context("c05"),
        _support_judge({"c05#claim1": True, "c05#claim2": True}),
    )
    # Fake a third claim onto the second result to make the weights differ.
    second.claims.append(second.claims[0].model_copy(update={"item_id": "x", "supported": False}))
    assert str(pooled([first, second], name="g")) == "0.600 (3/5)"


def test_a_claim_trace_carries_one_utterance_and_the_ids_are_stable() -> None:
    """The item id is the join key between a metric, a recording and a label.

    `c02#claim2` is the second claim of case c02's answer, everywhere, forever.
    When those drift the symptom is a calibration report that measures the wrong
    items, and nothing about it looks broken.
    """
    result = groundedness(
        CASES.get("c02"),
        _context("c02"),
        _support_judge({"c02#claim1": True, "c02#claim2": False}),
    )
    assert [claim.item_id for claim in result.claims] == ["c02#claim1", "c02#claim2"]


def test_a_case_can_be_built_in_memory_without_the_fixture_files() -> None:
    """The metrics do not depend on the committed dataset — only on its shape."""
    case = RagCase(
        id="adhoc",
        question="Is there a deposit for a party of ten?",
        gold=["p01"],
        retrieved=["p01"],
        answer="A deposit of GBP 15 per person is taken at booking.",
    )
    retrieval = context_for(case, CORPUS, k=1)
    result = groundedness(case, retrieval, _support_judge({"adhoc#claim1": True}))
    assert str(result.rate) == "1.000 (1/1)"
