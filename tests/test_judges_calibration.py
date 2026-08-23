"""Tests for the calibration maths, against hand-computed confusion matrices.

WHAT THIS DEMONSTRATES
----------------------
Every statistic here is checked against a matrix small enough to verify by hand,
with the arithmetic written out in the test docstring. That is the only way to
test a metrics module: an implementation compared against another implementation
of the same formula agrees with itself, including where both are wrong.

Two of these tests are the point of the whole module:

*   `test_raw_agreement_flatters_a_useless_judge` — the 0.900-agreement,
    0.000-kappa judge that has never once found a defect. This is the number an
    interviewer probes, and the arithmetic behind it is written into the test.
*   `test_kappa_is_undefined_when_there_is_no_variance` — perfect raw agreement
    with no information content at all, reported as `undefined` rather than as
    1.000.
"""

from __future__ import annotations

import json

import pytest

from lab.clock import FakeClock
from lab.judges.calibration import (
    CalibrationThresholds,
    LabelledTrace,
    Rate,
    calibrate,
    compare_reports,
    labels_digest,
    load_labels,
    self_consistency,
    write_labels,
)
from lab.judges.judge import Judge, Label, ScriptedCompletion
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

PROMPT = "Grade this.\n{{transcript}}\nAnswer PASS or FAIL."


def _trace(item_id: str) -> Trace:
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id="booking/generic", adapter="text", session_id=item_id, clock=clock
    )
    builder.session_start()
    clock.advance(0.5)
    builder.caller_utterance("Table for two at eight?")
    clock.advance(0.5)
    builder.agent_utterance("Let me look.", agent="BookingAgent")
    return builder.build()


def _item(item_id: str, label: Label, note: str = "hand-labelled") -> LabelledTrace:
    return LabelledTrace(item_id=item_id, label=label, trace=_trace(item_id), note=note)


def _judge_answering(answers: dict[str, Label], *, version: str = "v1") -> Judge:
    """A judge whose verdict for each item is dictated by the test."""
    raw = {
        item_id: ("PASS. clean" if label == "pass" else "FAIL. claimed")
        for item_id, label in answers.items()
    }
    return Judge(
        name="test_judge",
        prompt=PROMPT,
        version=version,
        model="test/stub",
        completion=ScriptedCompletion(raw),
    )


# --------------------------------------------------------------------------- #
# Rate: a ratio that cannot be printed without its fraction
# --------------------------------------------------------------------------- #


def test_rate_prints_its_fraction() -> None:
    assert str(Rate(name="tnr", numerator=15, denominator=16)) == "0.938 (15/16)"


def test_undefined_rate_is_undefined_and_not_zero() -> None:
    """Precision with no positive predictions is undefined, not 0.0.

    Reporting 0.0 would claim the judge made positive predictions and got them
    all wrong, which is a different — and much worse — judge than one that never
    fired.
    """
    rate = Rate(name="precision", numerator=0, denominator=0)
    assert rate.value is None
    assert rate.defined is False
    assert str(rate) == "undefined (0/0)"


# --------------------------------------------------------------------------- #
# The confusion matrix, one item per cell
# --------------------------------------------------------------------------- #


def test_one_item_in_every_cell() -> None:
    """TP=FP=FN=TN=1. Hand arithmetic:

        TPR = 1/(1+1) = 0.500        TNR = 1/(1+1) = 0.500
        precision = 1/(1+1) = 0.500  F1 = 2*1/(2*1+1+1) = 0.500
        raw agreement = (1+1)/4 = 0.500
        pe = (2*2 + 2*2)/16 = 0.500  kappa = (0.5-0.5)/(1-0.5) = 0.000

    A judge that agrees exactly half the time with a balanced set has scored
    precisely chance, and kappa says so where raw agreement's 0.500 could be
    mistaken for "half right".
    """
    items = [
        _item("tp", "fail"),
        _item("fp", "pass"),
        _item("fn", "fail"),
        _item("tn", "pass"),
    ]
    judge = _judge_answering({"tp": "fail", "fp": "fail", "fn": "pass", "tn": "pass"})
    report = calibrate(judge, items)

    assert (report.confusion.true_positive, report.confusion.false_positive) == (1, 1)
    assert (report.confusion.false_negative, report.confusion.true_negative) == (1, 1)
    assert report.n == 4
    assert report.true_positive_rate.value == pytest.approx(0.5)
    assert report.true_negative_rate.value == pytest.approx(0.5)
    assert report.precision.value == pytest.approx(0.5)
    assert report.f1.value == pytest.approx(0.5)
    assert report.raw_agreement.value == pytest.approx(0.5)
    assert report.cohens_kappa == pytest.approx(0.0)
    assert report.kappa_expected_agreement == pytest.approx(0.5)


def test_f1_is_a_ratio_of_counts() -> None:
    """F1 = 2TP/(2TP+FP+FN), so it prints with a numerator like every other rate.

    Same value as the harmonic mean of precision and recall; unlike the harmonic
    mean it can be checked by a reader.
    """
    items = [_item("a", "fail"), _item("b", "fail"), _item("c", "pass")]
    judge = _judge_answering({"a": "fail", "b": "pass", "c": "fail"})
    report = calibrate(judge, items)
    # TP=1, FN=1, FP=1 -> precision 0.5, recall 0.5, F1 0.5 = 2/(2+1+1)
    assert report.f1.numerator == 2
    assert report.f1.denominator == 4
    precision = report.precision.value
    recall = report.recall.value
    assert precision is not None and recall is not None
    assert report.f1.value == pytest.approx(
        2 * precision * recall / (precision + recall)
    )


# --------------------------------------------------------------------------- #
# The subtle one
# --------------------------------------------------------------------------- #


def test_raw_agreement_flatters_a_useless_judge() -> None:
    """20 items, 2 defects, judge always says "pass". Hand arithmetic:

        TP=0  FP=0  FN=2  TN=18
        raw agreement = 18/20 = 0.900   <- looks like a good judge
        TPR = 0/2 = 0.000               <- has never found a defect
        TNR = 18/18 = 1.000
        precision = 0/0 = undefined     <- it never made a positive claim
        pe = (0*2 + 20*18)/400 = 0.900
        kappa = (0.900-0.900)/(1-0.900) = 0.000

    This is why raw agreement is never reported alone, and why the gate requires
    both TPR and TNR: this judge has a perfect specificity.
    """
    items = [_item(f"clean-{i}", "pass") for i in range(18)] + [
        _item("defect-1", "fail"),
        _item("defect-2", "fail"),
    ]
    judge = _judge_answering({item.item_id: "pass" for item in items})
    report = calibrate(judge, items)

    assert report.raw_agreement.value == pytest.approx(0.9)
    assert report.true_positive_rate.value == pytest.approx(0.0)
    assert report.true_negative_rate.value == pytest.approx(1.0)
    assert report.precision.value is None
    assert report.cohens_kappa == pytest.approx(0.0)
    assert report.kappa_expected_agreement == pytest.approx(0.9)

    ok, failures = report.meets(CalibrationThresholds())
    assert ok is False
    assert any("TPR" in failure for failure in failures)


def test_kappa_is_undefined_when_there_is_no_variance() -> None:
    """All 12 items labelled pass, judge says pass to all. po = 1, pe = 1.

    Perfect raw agreement carrying zero information. Kappa is `undefined` rather
    than 1.000, because 1.000 would be the most flattering possible lie: the
    judge has not been shown a single positive and cannot be said to detect
    anything.
    """
    items = [_item(f"clean-{i}", "pass") for i in range(12)]
    judge = _judge_answering({item.item_id: "pass" for item in items})
    report = calibrate(judge, items)

    assert report.raw_agreement.value == pytest.approx(1.0)
    assert report.cohens_kappa is None
    assert report.true_positive_rate.value is None

    ok, failures = report.meets(CalibrationThresholds())
    assert ok is False
    assert any("undefined" in failure for failure in failures)


def test_perfect_agreement_on_a_balanced_set_is_kappa_one() -> None:
    """6 items, 3 defects, all correct: po = 1, pe = 0.5, kappa = 1.000."""
    items = [_item(f"d{i}", "fail") for i in range(3)] + [
        _item(f"c{i}", "pass") for i in range(3)
    ]
    judge = _judge_answering({item.item_id: item.label for item in items})
    report = calibrate(judge, items)
    assert report.cohens_kappa == pytest.approx(1.0)
    assert report.disagreements == []


# --------------------------------------------------------------------------- #
# Disagreements
# --------------------------------------------------------------------------- #


def test_disagreements_list_both_sides_and_put_misses_first() -> None:
    """False negatives sort first: a missed defect is silent, a false alarm is not."""
    items = [
        _item("zebra-false-positive", "pass", note="only an offer"),
        _item("alpha-false-negative", "fail", note="said it was confirmed"),
        _item("agreed", "pass", note="clean"),
    ]
    judge = _judge_answering(
        {
            "zebra-false-positive": "fail",
            "alpha-false-negative": "pass",
            "agreed": "pass",
        }
    )
    report = calibrate(judge, items)

    assert [d.kind for d in report.disagreements] == ["false_negative", "false_positive"]
    miss = report.disagreements[0]
    assert miss.item_id == "alpha-false-negative"
    assert miss.human_note == "said it was confirmed"
    assert miss.judge_critique == "clean"  # the scripted critique for a PASS verdict
    assert "agreed" not in [d.item_id for d in report.disagreements]


def test_items_let_a_reader_rebuild_the_matrix() -> None:
    items = [_item("a", "fail"), _item("b", "pass")]
    report = calibrate(_judge_answering({"a": "fail", "b": "fail"}), items)
    cells = {outcome.item_id: outcome.cell for outcome in report.items}
    assert cells == {"a": "tp", "b": "fp"}


# --------------------------------------------------------------------------- #
# Parse errors
# --------------------------------------------------------------------------- #


def test_parse_errors_are_counted_and_fail_the_gate() -> None:
    """Failing closed inflates TPR, so the gate refuses a broken output contract.

    Two defects, and the model returns junk for both. Failing closed marks them
    FAIL, which happens to be correct here — TPR reads 2/2. A judge whose provider
    is emitting garbage must not be able to score as a perfect detector, which is
    why `max_parse_error_rate` defaults to zero.
    """
    items = [_item("a", "fail"), _item("b", "fail")] + [
        _item(f"c{i}", "pass") for i in range(10)
    ]
    raw = {"a": "hmm", "b": "not sure"}
    raw.update({f"c{i}": "PASS. clean" for i in range(10)})
    judge = Judge(
        name="test_judge",
        prompt=PROMPT,
        version="v1",
        model="test/stub",
        completion=ScriptedCompletion(raw),
        strict=False,
    )
    report = calibrate(judge, items)

    assert report.parse_errors == 2
    assert report.true_positive_rate.value == pytest.approx(1.0)
    ok, failures = report.meets(CalibrationThresholds())
    assert ok is False
    assert any("parse error" in failure for failure in failures)


# --------------------------------------------------------------------------- #
# Guard rails
# --------------------------------------------------------------------------- #


def test_duplicate_item_ids_are_rejected() -> None:
    """A duplicate id would weight one item twice and make replay ambiguous."""
    items = [_item("same", "fail"), _item("same", "pass")]
    with pytest.raises(ValueError, match="duplicate item_id"):
        calibrate(_judge_answering({"same": "fail"}), items)


def test_empty_label_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty labelled set"):
        calibrate(_judge_answering({}), [])


def test_calibrate_attaches_the_report_to_the_judge() -> None:
    judge = _judge_answering({"a": "fail"})
    report = calibrate(judge, [_item("a", "fail")])
    assert judge.calibration is report


def test_report_records_the_prompt_it_measured() -> None:
    judge = _judge_answering({"a": "fail"})
    report = calibrate(judge, [_item("a", "fail")])
    assert report.prompt_sha256 == judge.prompt_sha256
    assert report.prompt_version == "v1"
    assert report.model == "test/stub"


def test_labels_digest_tracks_labels_not_notes() -> None:
    """Relabelling invalidates a report; rewording a note does not.

    The digest exists so a stale report is caught rather than quoted for another
    six months. Notes are not inputs to the measurement, so editing one must not
    invalidate it — otherwise nobody improves the notes.
    """
    base = [_item("a", "fail", note="original wording")]
    reworded = [_item("a", "fail", note="clearer wording")]
    relabelled = [_item("a", "pass", note="original wording")]
    assert labels_digest(base) == labels_digest(reworded)
    assert labels_digest(base) != labels_digest(relabelled)


def test_labels_round_trip_through_jsonl(tmp_path) -> None:
    items = [_item("a", "fail"), _item("b", "pass")]
    path = write_labels(items, tmp_path / "labels.jsonl")
    assert load_labels(path) == items


def test_report_json_is_recomputable(tmp_path) -> None:
    """The written JSON carries every per-item outcome, so the matrix is auditable."""
    items = [_item("a", "fail"), _item("b", "pass")]
    report = calibrate(_judge_answering({"a": "fail", "b": "pass"}), items)
    paths = report.write(tmp_path)
    data = json.loads(paths["json"].read_text())
    cells = [item["cell"] for item in data["items"]]
    assert sorted(cells) == ["tn", "tp"]
    assert data["true_positive_rate"]["numerator"] == 1
    assert data["true_positive_rate"]["value"] == pytest.approx(1.0)
    assert "Raw agreement" in paths["markdown"].read_text()


# --------------------------------------------------------------------------- #
# Comparing versions
# --------------------------------------------------------------------------- #


def test_compare_refuses_different_label_sets() -> None:
    """A "v1 -> v2 improvement" measured on two different sets is not a comparison.

    Improve the prompt, quietly drop the three items it kept failing, and the
    numbers move. This is the easiest self-deception available in eval work, so
    the comparison function refuses it outright.
    """
    first = [_item("a", "fail"), _item("b", "pass")]
    second = [_item("a", "fail")]
    before = calibrate(_judge_answering({"a": "fail", "b": "pass"}), first)
    after = calibrate(_judge_answering({"a": "fail"}, version="v2"), second)
    with pytest.raises(ValueError, match="different label sets"):
        compare_reports(before, after)


def test_compare_refuses_different_judges() -> None:
    items = [_item("a", "fail")]
    before = calibrate(_judge_answering({"a": "fail"}), items)
    other = Judge(
        name="another_judge",
        prompt=PROMPT,
        version="v1",
        model="test/stub",
        completion=ScriptedCompletion({"a": "PASS. clean"}),
    )
    after = calibrate(other, items)
    with pytest.raises(ValueError, match="different judges"):
        compare_reports(before, after)


def test_compare_reports_shows_the_delta() -> None:
    items = [_item("a", "fail"), _item("b", "pass"), _item("c", "pass")]
    before = calibrate(_judge_answering({"a": "fail", "b": "fail", "c": "pass"}), items)
    after = calibrate(
        _judge_answering({"a": "fail", "b": "pass", "c": "pass"}, version="v2"), items
    )
    table = compare_reports(before, after)
    assert "0.500 (1/2)" in table  # v1 true negative rate
    assert "1.000 (2/2)" in table  # v2 true negative rate
    assert "| false positives | 1 | 0 | -1 |" in table


# --------------------------------------------------------------------------- #
# Self-consistency: accuracy's missing half
# --------------------------------------------------------------------------- #


def test_self_consistency_counts_items_not_rates() -> None:
    """Two errors in opposite directions cancel in the rates and not in the items.

    Runs A and B both score TP 1 / FN 1 on the same two positives — identical
    confusion matrices, identical TPR — while disagreeing about *which* item was
    missed. A summary that only watched the rates would call this judge
    deterministic. It is not, and a later "v3 beat v2 by one item" comparison would
    have been reading exactly this noise.
    """
    items = [_item("a", "fail"), _item("b", "fail"), _item("c", "pass")]
    run_a = _judge_answering({"a": "fail", "b": "pass", "c": "pass"})
    run_b = _judge_answering({"a": "pass", "b": "fail", "c": "pass"})

    report_a = calibrate(run_a, items, attach=False)
    report_b = calibrate(run_b, items, attach=False)
    assert report_a.true_positive_rate.value == report_b.true_positive_rate.value
    assert report_a.confusion.model_dump() == report_b.confusion.model_dump()

    runs = self_consistency([run_a, run_b], items)
    assert runs.n == 3
    assert runs.runs == 2
    assert [row.item_id for row in runs.unstable] == ["a", "b"]
    assert runs.unanimity.numerator == 1
    assert str(runs.unanimity) == "0.333 (1/3)"
    assert "0.333 (1/3)" in runs.summary_line()

    markdown = runs.to_markdown()
    assert "`a` (human: **fail**) — fail, pass" in markdown
    assert "`b` (human: **fail**) — pass, fail" in markdown


def test_self_consistency_reports_a_stable_judge_as_stable() -> None:
    items = [_item("a", "fail"), _item("b", "pass")]
    answers = {"a": "fail", "b": "pass"}
    runs = self_consistency(
        [_judge_answering(answers), _judge_answering(answers)], items
    )
    assert runs.unstable == []
    assert runs.unanimity.value == pytest.approx(1.0)
    assert "No item changed verdict between runs." in runs.to_markdown()
    assert "not a guarantee for unseen items" in runs.to_markdown()


def test_self_consistency_needs_more_than_one_run() -> None:
    items = [_item("a", "fail")]
    with pytest.raises(ValueError, match="at least two runs"):
        self_consistency([_judge_answering({"a": "fail"})], items)


def test_self_consistency_refuses_two_different_prompt_versions() -> None:
    """Repeat runs of one judge; anything else is a prompt comparison in disguise."""
    items = [_item("a", "fail")]
    with pytest.raises(ValueError, match="repeated runs of ONE judge"):
        self_consistency(
            [
                _judge_answering({"a": "fail"}, version="v1"),
                _judge_answering({"a": "fail"}, version="v2"),
            ],
            items,
        )


def test_self_consistency_refuses_an_empty_set() -> None:
    with pytest.raises(ValueError, match="empty set"):
        self_consistency(
            [_judge_answering({}), _judge_answering({})], []
        )
