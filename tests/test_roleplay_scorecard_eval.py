"""The cited-scorecard evaluator: a trace graded against the registry, not the vocabulary.

The headline test is the DEFECT-3 boundary from the other side: a session that
says every compliance keyword and records one of three required disclosures gets
full marks from rubric_v1 and fails the CG-1 gate here. Everything else is the
evaluator's own contract — every KPI reported once, every not-applicable carries
a reason, the grade is a pure function of the trace, and the two committed calls
fail for the codes their ledgers actually lack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab.trace.build import TraceBuilder
from lab.trace.io import read_jsonl
from roleplay.persona import classify_trainee_turn
from roleplay.scorecard import KPIS
from roleplay.scorecard_eval import REGIME_FOR_JURISDICTION, evaluate
from roleplay.scorer import CUSTOMER_AGENT, RubricScorer

ROOT = Path(__file__).resolve().parents[1]
ALL_KEYWORDS = "There is risk to your capital; past performance is no guide; the value can go down; there is a charge and a fee."
CODES = ("capital_at_risk", "past_performance", "fees_and_charges")


def _trace(*, trainee=(), customer=(), tools=(), jurisdiction="eu-retail", language="en"):
    b = TraceBuilder(scenario_id="unit", adapter="unit")
    b.session_start()
    b.tool_call("load_customer_profile", {"profile": "p", "jurisdiction": jurisdiction, "language": language}, call_id="lcp")
    b.tool_result("load_customer_profile", None, call_id="lcp", ok=True)
    for i, text in enumerate(trainee, start=1):
        b.caller_utterance(text)
        if i - 1 < len(customer):
            b.agent_utterance(customer[i - 1], agent=CUSTOMER_AGENT)
    for n, (name, args) in enumerate(tools):
        b.tool_call(name, args, call_id=f"{name}{n}")
        b.tool_result(name, None, call_id=f"{name}{n}", ok=True)
    b.session_end(reason="scored")
    return b.build()


def _disclosed(*codes, language="en"):
    return tuple(("record_disclosure", {"code": c, "jurisdiction": "eu-retail", "language": language, "turn": 1, "phrasing": c}) for c in codes)


def test_one_of_three_disclosures_fails_the_gate_even_when_every_keyword_is_spoken() -> None:
    trace = _trace(trainee=(ALL_KEYWORDS,), tools=_disclosed("capital_at_risk"))
    v1 = RubricScorer().score_trace(trace)
    assert v1.criteria["mandatory_disclosure"] == 4, "precondition: rubric_v1 rewards the vocabulary"
    report = evaluate(trace)
    assert report.outcome("CG-1").gate_passed is False
    assert report.missing_disclosures == ("past_performance", "fees_and_charges")
    assert report.score.verdict == "fail" and "CG-1" in report.score.gates_failed


def test_all_required_codes_recorded_pass_the_gate() -> None:
    report = evaluate(_trace(trainee=("hello",), tools=_disclosed(*CODES)))
    assert report.outcome("CG-1").gate_passed is True
    assert report.missing_disclosures == ()


def test_a_disclosure_in_another_language_does_not_count() -> None:
    tools = _disclosed("capital_at_risk", "past_performance") + _disclosed("fees_and_charges", language="es")
    report = evaluate(_trace(trainee=("hello",), tools=tools))
    assert report.outcome("CG-1").gate_passed is False
    assert report.missing_disclosures == ("fees_and_charges",)
    assert "another language" in report.outcome("CG-1").evidence


def test_every_kpi_is_reported_exactly_once_and_every_na_carries_a_reason() -> None:
    report = evaluate(_trace(trainee=("hello",)))
    assert [o.kpi_id for o in report.outcomes] == [k.id for k in KPIS]
    for o in report.outcomes:
        if not o.applicable:
            assert o.evidence.strip(), f"{o.kpi_id} is n/a with no reason"


def test_repeated_objection_lowers_oh1_and_a_handled_one_does_not() -> None:
    twice = (("raise_objection", {"key": "fees", "topic": "fees", "turn": 2}),) * 2
    assert evaluate(_trace(trainee=("a", "b"), tools=twice)).outcome("OH-1").points == 0
    once = (("raise_objection", {"key": "fees", "topic": "fees", "turn": 2}), ("resolve_objection", {"key": "fees", "topic": "fees", "turn": 3}))
    assert evaluate(_trace(trainee=("a", "b"), tools=once)).outcome("OH-1").points == 4
    assert evaluate(_trace(trainee=("a",))).outcome("OH-1").applicable is False


def test_a_close_attempt_scores_cl1() -> None:
    close = "Shall we go ahead and get the paperwork started today?"
    assert classify_trainee_turn(close) == "close_attempt", "precondition: the classifier must see a close"
    assert evaluate(_trace(trainee=("hello", close))).outcome("CL-1").points == 2
    assert evaluate(_trace(trainee=("hello",))).outcome("CL-1").points == 0


def test_urgency_language_fails_cl4() -> None:
    bad = evaluate(_trace(trainee=("The offer closes Friday, so we would need to decide today.",)))
    assert bad.outcome("CL-4").gate_passed is False and bad.score.verdict == "fail"
    assert evaluate(_trace(trainee=("Take your time; there is no deadline on this.",))).outcome("CL-4").gate_passed is True


def test_the_grade_is_a_pure_function_of_the_trace() -> None:
    a = _trace(trainee=("hello",), tools=_disclosed(*CODES))
    first = evaluate(a).as_dict()
    evaluate(_trace(trainee=("something else entirely",)))  # another session in between
    assert evaluate(a).as_dict() == first


def test_an_unmapped_jurisdiction_raises_rather_than_grading_against_nothing() -> None:
    with pytest.raises(KeyError, match="no regime mapping"):
        evaluate(_trace(trainee=("hello",), jurisdiction="mars-retail"))
    assert set(REGIME_FOR_JURISDICTION) == {"eu-retail", "amer-retail", "apac-retail"}


@pytest.mark.parametrize(
    "fixture, expected_missing",
    [
        ("fixtures/audio/spoken_call/trace.jsonl", ("past_performance",)),
        ("fixtures/audio/spoken_call_pass/trace.jsonl", ("capital_at_risk", "fees_and_charges")),
    ],
)
def test_both_committed_calls_fail_the_disclosure_gate_for_the_codes_their_ledgers_lack(fixture, expected_missing) -> None:
    report = evaluate(read_jsonl(ROOT / fixture))
    assert report.regime == "fca"
    assert report.missing_disclosures == expected_missing
    assert report.outcome("CG-1").gate_passed is False and report.score.verdict == "fail"
    assert report.v1.criteria["mandatory_disclosure"] == 4, "rubric_v1 still gives full marks on these calls"
