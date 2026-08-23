"""The assembled run: pinned numbers, separated verdicts, and a gate that bites.

WHAT THIS DEMONSTRATES
----------------------
`evaluate()` with no arguments and no API key produces a full report over the
committed fixtures, and every headline number in it is pinned here. That is what
makes the package a regression suite rather than a demo: change the corpus, the
questions, the splitter, the oracle or a threshold, and a test fails with the old
number next to the new one.

The two structural assertions are the ones worth reading:

*   `test_retrieval_and_generation_are_never_blended_into_one_score` — there is
    no "RAG score" and adding one would destroy the only actionable content the
    report has.
*   `test_the_gate_stops_the_run_rather_than_annotating_it` — `gate=True` raises.
    A gate that returns a report with a sad note in it is not a gate.
"""

from __future__ import annotations

import pytest

from lab.judges.calibration import CalibrationThresholds
from lab.judges.registry import JudgeBelowThresholdError

from ragcheck.report import evaluate

REPORT = evaluate()


# --------------------------------------------------------------------------- #
# the pinned numbers
# --------------------------------------------------------------------------- #


def test_the_offline_run_reproduces_its_recorded_numbers() -> None:
    """18 questions retrieved, 8 answered, and every aggregate as measured."""
    assert REPORT.k == 3
    assert REPORT.retrieval.n == 18
    assert REPORT.generation.n == 8

    assert str(REPORT.retrieval.pooled_recall) == "0.750 (15/20)"
    assert str(REPORT.generation.pooled_groundedness) == "0.857 (12/14)"
    assert str(REPORT.generation.relevance_rate) == "0.875 (7/8)"
    assert str(REPORT.generation.pooled_context_recall) == "0.500 (1/2)"
    assert REPORT.generation.mean_context_precision_gold.value == pytest.approx(1.0)


def test_the_three_worked_failures_are_all_present_in_one_run() -> None:
    """Each row fails a different metric while passing the others.

        c02   retrieval 1/1, groundedness 1/2      invented figure
        c12   groundedness 2/2, relevance fail     answered another question
        c18   groundedness 1/1, context recall 1/2 context was incomplete
    """
    rows = {row.case_id: row for row in REPORT.generation.rows}

    assert str(rows["c02"].context_recall_gold) == "1.000 (1/1)"
    assert str(rows["c02"].groundedness.rate) == "0.500 (1/2)"
    assert rows["c02"].relevance.relevant is True

    assert str(rows["c12"].groundedness.rate) == "1.000 (2/2)"
    assert rows["c12"].relevance.relevant is False

    assert str(rows["c18"].groundedness.rate) == "1.000 (1/1)"
    assert rows["c18"].relevance.relevant is True
    assert str(rows["c18"].context_recall.rate) == "0.500 (1/2)"
    assert str(rows["c18"].context_recall_gold) == "0.500 (1/2)"


def test_every_finding_quotes_the_evidence_for_itself() -> None:
    """"3 claims unsupported" is a number; a quoted claim is a bug report."""
    findings = REPORT.generation.findings()
    assert len(findings) == 5
    assert any("GBP 25 per person" in finding for finding in findings)
    assert any("phone you to check" in finding for finding in findings)
    assert any("c12#answer" in finding for finding in findings)
    assert any("missing gold chunk(s) ['p01']" in finding for finding in findings)


def test_the_pinned_context_is_used_for_generation_and_ignored_for_retrieval() -> None:
    """Two questions, measured two ways, on purpose.

    c02 pins its context so groundedness moves only when the answer changes; the
    retrieval report scores the live retriever on the same question, which ranks
    p13 above the gold p01. Both numbers are right and they are about different
    things.
    """
    row = {row.case_id: row for row in REPORT.generation.rows}["c02"]
    retrieval_row = {row.case_id: row for row in REPORT.retrieval.rows}["c02"]
    assert row.context == ["p01", "p03", "p14"]
    assert retrieval_row.retrieved == ["p13", "p01", "p03"]


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def test_retrieval_and_generation_are_never_blended_into_one_score() -> None:
    """recall@3 = 0.75 and groundedness = 12/14 are owned by two teams.

    Averaging them into 0.80 would produce a number that moves for two unrelated
    reasons and tells nobody what to fix. There is deliberately no such attribute
    on the report, and this test is what keeps it that way.
    """
    assert not hasattr(REPORT, "score")
    assert not hasattr(REPORT, "overall")
    text = REPORT.to_text()
    assert "RETRIEVAL" in text and "GENERATION" in text


def test_the_report_prints_the_judge_alongside_every_judged_number() -> None:
    """A reading without its instrument's error rate is not a measurement."""
    text = REPORT.to_text()
    assert "stand-in/lexical-v1" in text
    assert "TPR 0.800 (4/5)" in text
    assert "calibration gate: REFUSED" in text
    assert "c13#claim2" in text  # the disagreement a human should read


def test_a_run_without_calibration_says_so_instead_of_going_quiet() -> None:
    """The silent version of this failure is the one this repository is about."""
    uncalibrated = evaluate(calibrate_support=False)
    assert uncalibrated.calibration is None
    assert "not measured" in uncalibrated.to_text()


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


def test_the_gate_stops_the_run_rather_than_annotating_it() -> None:
    """`evaluate(gate=True)` raises, naming the rate that fell short.

    The offline oracle scores TPR 4/5 against the hand labels, the threshold is
    0.85, and so the pipeline stops. That is the correct outcome for the grader
    this repository ships, and it is the whole point of shipping the measurement
    next to the metric.
    """
    with pytest.raises(JudgeBelowThresholdError, match=r"TPR 0.800 \(4/5\)"):
        evaluate(gate=True)


def test_a_gate_on_a_measurement_that_was_never_taken_is_refused() -> None:
    with pytest.raises(ValueError, match="never taken"):
        evaluate(gate=True, calibrate_support=False)


def test_a_lower_threshold_lets_the_same_oracle_through() -> None:
    """Thresholds are a parameter, and lowering one is a visible decision.

    0.75 is below the oracle's measured 0.800 TPR, so the gate passes — and the
    report still prints the numbers, so the choice is auditable rather than
    hidden behind a green tick.
    """
    lenient = evaluate(gate=True, thresholds=CalibrationThresholds(min_tpr=0.75, min_tnr=0.75))
    assert lenient.calibration is not None
    assert lenient.calibration.passes(CalibrationThresholds(min_tpr=0.75, min_tnr=0.75))
    assert str(lenient.generation.pooled_groundedness) == "0.857 (12/14)"


def test_the_run_is_deterministic() -> None:
    """Same fixtures, same numbers, every time.

    An eval suite whose figures move on a re-run cannot distinguish a regression
    from a re-roll, and the first flaky red teaches everyone to press the button
    again instead of reading the output.
    """
    again = evaluate()
    assert again.to_text() == REPORT.to_text()
