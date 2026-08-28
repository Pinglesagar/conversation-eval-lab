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
    detectability_floor,
    exact_mcnemar_p,
    labels_digest,
    mcnemar,
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
# McNemar, and the floor under it
#
# `compare_reports` used to say whether v2 beat v1 without saying whether the
# difference was distinguishable from chance. These pin both halves: the exact
# test, and the number that says what the set cannot prove whatever the result.
# --------------------------------------------------------------------------- #


def test_exact_mcnemar_matches_the_closed_form_when_all_pairs_point_one_way() -> None:
    """d discordant pairs all one way -> 2/2**d. Every figure this repo quotes."""
    for d, expected in ((3, 0.25), (4, 0.125), (5, 0.0625), (6, 0.03125), (7, 0.015625)):
        assert exact_mcnemar_p(d, 0) == pytest.approx(expected)
        # Symmetric: a regression of the same size is equally (in)significant.
        assert exact_mcnemar_p(0, d) == pytest.approx(expected)


def test_exact_mcnemar_handles_the_even_split_and_the_empty_one() -> None:
    # An even split is the null exactly; the two-sided sum overshoots 1 and is
    # capped rather than printed as 1.5.
    assert exact_mcnemar_p(3, 3) == 1.0
    # Nothing moved: nothing to test.
    assert exact_mcnemar_p(0, 0) == 1.0
    with pytest.raises(ValueError):
        exact_mcnemar_p(-1, 0)


def test_the_detectability_floor_is_six_at_the_conventional_alpha() -> None:
    assert detectability_floor(0.05) == 6
    # Five is not enough, six is: the whole point, stated as the two p-values.
    assert exact_mcnemar_p(5, 0) > 0.05
    assert exact_mcnemar_p(6, 0) <= 0.05
    # The floor moves with alpha and with nothing else.
    assert detectability_floor(0.10) == 5
    assert detectability_floor(0.01) == 8
    with pytest.raises(ValueError):
        detectability_floor(0.0)


def test_mcnemar_pairs_by_item_and_counts_both_directions() -> None:
    items = [_item("a", "fail"), _item("b", "pass"), _item("c", "pass"), _item("d", "fail")]
    # v1 is right on a and b; v2 is right on a and c. So b regressed, c was
    # fixed, d is wrong in both, a is right in both.
    before = calibrate(
        _judge_answering({"a": "fail", "b": "pass", "c": "fail", "d": "pass"}), items
    )
    after = calibrate(
        _judge_answering(
            {"a": "fail", "b": "fail", "c": "pass", "d": "pass"}, version="v2"
        ),
        items,
    )
    paired = mcnemar(before, after)
    assert (paired.n_items, paired.both_correct, paired.both_wrong) == (4, 1, 1)
    assert paired.before_only_correct == 1
    assert paired.after_only_correct == 1
    assert paired.discordant == 2
    # One fixed, one broken, on two discordant pairs: the null exactly.
    assert paired.p_value == 1.0
    assert paired.significant is False


def test_the_worked_studys_comparison_is_significant_and_only_just() -> None:
    """The committed v1 -> v2 study: 6 fixed, 0 broken, p = 0.03125."""
    from lab.judges import hallucinated_confirmation as story

    items = story.labels()
    v1 = story.calibrate_version("v1", items=items)
    v2 = story.calibrate_version("v2", items=items)
    paired = mcnemar(v1, v2)

    assert (paired.n_items, paired.after_only_correct, paired.before_only_correct) == (
        24,
        6,
        0,
    )
    assert paired.p_value == pytest.approx(0.03125)
    assert paired.significant is True
    # Exactly on the floor: five items would have published nothing.
    assert paired.discordant == paired.floor == 6
    assert paired.floor_is_reachable is True


def test_compare_reports_prints_the_test_and_the_floor() -> None:
    items = [_item("a", "fail"), _item("b", "pass"), _item("c", "pass")]
    before = calibrate(_judge_answering({"a": "fail", "b": "fail", "c": "pass"}), items)
    after = calibrate(
        _judge_answering({"a": "fail", "b": "pass", "c": "pass"}, version="v2"), items
    )
    table = compare_reports(before, after)

    assert "## Is the difference distinguishable from chance?" in table
    # One item moved: not distinguishable from anything.
    assert "Exact two-sided McNemar p = 1.00000" in table
    assert "**not** distinguishable from chance" in table
    # The floor is printed whether or not the comparison cleared it, and it is
    # the number a reader needs *before* labelling the next set.
    assert "The detectability floor on this set: 6 items" in table
    assert "| 6 | 0.03125 | yes |" in table
    assert "| 5 | 0.06250 | no |" in table
    # Three items, floor of six: this set could never have published anything.
    assert "cannot publish any improvement" in table


def test_mcnemar_refuses_reports_scored_on_different_items() -> None:
    """The pairing is the test; pairing the wrong items would invent a result."""
    first = [_item("a", "fail"), _item("b", "pass")]
    second = [_item("a", "fail"), _item("c", "pass")]
    before = calibrate(_judge_answering({"a": "fail", "b": "pass"}), first)
    after = calibrate(
        _judge_answering({"a": "fail", "c": "pass"}, version="v2"), second
    )
    with pytest.raises(ValueError, match="different items"):
        mcnemar(before, after)


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


# --------------------------------------------------------------------------- #
# Wilson intervals, and the position this module reversed
#
# The module used to decline confidence intervals on the grounds that "a Wilson
# interval on 8/8 would imply a precision the set cannot support". These pin the
# reversal: the interval is printed beside every rate, the report says which
# number the gate was scored on, and the stricter rule is available without being
# imposed.
# --------------------------------------------------------------------------- #


def test_a_rate_carries_its_wilson_interval() -> None:
    """8/8 -> [0.676, 1.000]; the arithmetic is worked in tests/test_stats.py."""
    rate = Rate(name="tpr", numerator=8, denominator=8)
    low, high = rate.interval()
    assert low == pytest.approx(0.676, abs=0.0005)
    assert high == 1.0
    assert rate.interval_text() == "[0.676, 1.000]"
    assert rate.with_interval() == "1.000 (8/8) 95% CI [0.676, 1.000]"


def test_an_undefined_rate_has_no_interval_rather_than_a_zero_one() -> None:
    rate = Rate(name="precision", numerator=0, denominator=0)
    assert rate.interval() is None
    assert rate.interval_text() == "undefined"


def test_the_rule_of_three_sentence_appears_only_on_a_perfect_score() -> None:
    perfect = Rate(name="tnr", numerator=16, denominator=16)
    assert perfect.zero_error() is True
    text = perfect.rule_of_three_text()
    assert text is not None and "3/16 = 0.188" in text

    imperfect = Rate(name="tnr", numerator=15, denominator=16)
    assert imperfect.zero_error() is False
    assert imperfect.rule_of_three_text() is None


def test_the_report_prints_an_interval_next_to_every_proportion() -> None:
    items = [_item("a", "fail"), _item("b", "pass"), _item("c", "pass")]
    report = calibrate(
        _judge_answering({"a": "fail", "b": "pass", "c": "pass"}), items
    )
    markdown = report.to_markdown()
    assert "| metric | value | numerator / denominator | 95% Wilson CI |" in markdown
    # TPR 1/1 and TNR 2/2, both perfect, both with an interval that says so.
    assert "| true positive rate (recall) | 1.000 | 1 / 1 | [" in markdown
    assert "95% CI" in report.to_text()


def test_f1_and_kappa_are_refused_an_interval_because_they_are_not_proportions() -> None:
    """F1's denominator counts the same item twice (2TP + FP + FN), so the trials
    are not independent and a binomial interval on it is the wrong arithmetic.
    Kappa is not a proportion of trials at all. Both say so in the cell rather
    than printing a number a reader would take at face value.
    """
    items = [_item("a", "fail"), _item("b", "pass"), _item("c", "pass")]
    report = calibrate(
        _judge_answering({"a": "fail", "b": "fail", "c": "pass"}), items
    )
    assert report.interval_cell(report.f1) == "not a proportion"
    assert report.interval_cell(report.true_positive_rate).startswith("[")
    markdown = report.to_markdown()
    assert "| Cohen's kappa |" in markdown
    assert "not a proportion |" in markdown


def test_the_gate_section_names_both_numbers_and_which_one_was_scored() -> None:
    items = [_item(f"p{i}", "fail") for i in range(8)] + [
        _item(f"n{i}", "pass") for i in range(16)
    ]
    answers = {item.item_id: item.label for item in items}
    report = calibrate(_judge_answering(answers), items)

    assert report.passes() is True  # 8/8 and 16/16 on the point estimate
    section = "\n".join(report.gate_evidence())
    assert "| TPR >= 0.85 | 1.000 (8/8) | [0.676, 1.000] | yes | **no** |" in section
    assert "| TNR >= 0.85 | 1.000 (16/16) | [0.806, 1.000] | yes | **no** |" in section
    assert "cleared by the point estimate and not by the evidence" in section
    assert "**22**" in section  # the number of perfect trials 0.85 would need
    assert "scored on the point estimate" in section


def test_the_gate_section_says_so_when_the_lower_bound_also_clears() -> None:
    """A set large enough that the evidence, and not only the fraction, clears
    the bar. 30 positives and 30 negatives, all correct: the 95% lower bound is
    0.884 on both, so the verdict does not depend on which number is scored."""
    items = [_item(f"p{i}", "fail") for i in range(30)] + [
        _item(f"n{i}", "pass") for i in range(30)
    ]
    answers = {item.item_id: item.label for item in items}
    report = calibrate(_judge_answering(answers), items)
    section = "\n".join(report.gate_evidence())
    assert "clear the threshold on the 95% lower bound" in section
    assert "cleared by the point estimate and not by the evidence" not in section


def test_the_default_gate_still_scores_the_point_estimate() -> None:
    """The load-bearing compatibility assertion. Printing the lower bound must
    not silently change whether a committed judge passes; the shipped judge
    clears 0.85 on 8/8 and would fail on its lower bound of 0.676."""
    thresholds = CalibrationThresholds()
    assert thresholds.gate_on == "point"
    assert thresholds.confidence == 0.95
    assert "scored on the point estimate" in thresholds.describe()


def test_gating_on_the_lower_bound_is_available_and_fails_a_perfect_small_set() -> None:
    """The stricter rule, and the reason it is not the default, in one test."""
    items = [_item(f"p{i}", "fail") for i in range(8)] + [
        _item(f"n{i}", "pass") for i in range(16)
    ]
    answers = {item.item_id: item.label for item in items}
    report = calibrate(_judge_answering(answers), items)

    strict = CalibrationThresholds(gate_on="wilson_lower")
    ok, failures = report.meets(strict)
    assert ok is False
    assert any("Wilson lower bound of 0.676" in f for f in failures)
    assert any("the point estimate clears the threshold" in f for f in failures)
    assert "scored on the 95% Wilson lower bound" in strict.describe()


def test_the_strict_gate_names_both_numbers_when_both_fall_short() -> None:
    items = [_item(f"p{i}", "fail") for i in range(8)] + [
        _item(f"n{i}", "pass") for i in range(16)
    ]
    answers = {item.item_id: item.label for item in items}
    answers["p0"] = "pass"  # one miss: TPR 7/8 = 0.875... still above 0.85
    answers["p1"] = "pass"  # two misses: TPR 6/8 = 0.750, below it
    report = calibrate(_judge_answering(answers), items)
    ok, failures = report.meets(CalibrationThresholds(gate_on="wilson_lower"))
    assert ok is False
    assert any("on both the point estimate and the 95% Wilson lower bound" in f
               for f in failures)


def test_the_comparison_table_carries_an_interval_but_names_the_paired_test() -> None:
    """Two intervals side by side are not the comparison; the artefact says so."""
    items = [_item("a", "fail"), _item("b", "pass"), _item("c", "pass")]
    before = calibrate(_judge_answering({"a": "fail", "b": "fail", "c": "pass"}), items)
    after = calibrate(
        _judge_answering({"a": "fail", "b": "pass", "c": "pass"}, version="v2"), items
    )
    table = compare_reports(before, after)
    assert "0.500 (1/2) [0.095, 0.905]" in table  # v1 TNR, with its interval
    assert "those intervals are **not the comparison**" in table
    assert "the columns are paired" in table
