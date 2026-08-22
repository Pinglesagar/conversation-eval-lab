"""Tests for the worked v1 -> v2 iteration in `lab.judges.hallucinated_confirmation`.

WHAT THIS DEMONSTRATES
----------------------
The committed artefacts — labels, recordings, both calibration reports — are
treated as *claims*, and these tests check them. Three things are verified that a
portfolio repository usually leaves as an assertion in a README:

*   **The numbers in the docstring are the numbers the code produces.** The
    confusion matrices are asserted cell by cell.
*   **The committed reports are reproducible byte for byte.** `regenerate()` into
    a temporary directory and diff against what is in git. A checked-in report
    that cannot be regenerated is a screenshot.
*   **The judge really is blind to the tool ledger**, and the label set really is
    drawn from the post-filter population. Both are load-bearing claims about what
    the measurement means, so both are enforced rather than described.
"""

from __future__ import annotations

import json

import pytest

from lab.judges import hallucinated_confirmation as story
from lab.judges.calibration import CalibrationThresholds, calibrate, compare_reports
from lab.judges.hallucinated_confirmation import dataset
from lab.judges.judge import StaleRecordingError


# --------------------------------------------------------------------------- #
# The label set
# --------------------------------------------------------------------------- #


def test_committed_labels_match_the_generator() -> None:
    """`labels.jsonl` is the artefact of record; `dataset.py` is how it was built."""
    assert story.labels() == dataset.labelled_items()


def test_label_set_has_the_documented_balance() -> None:
    counts = dataset.label_counts()
    assert counts == {"fail": 8, "pass": 16}
    assert len(dataset.ITEMS) == 24


def test_preconditions_hold() -> None:
    """No successful booking mutation anywhere, and every item carries a note.

    The judge is stage two of a cascade whose first stage removes sessions with a
    real booking. Calibrating on a set that violated that would measure the judge
    on a distribution it never sees.
    """
    dataset.check_preconditions()


def test_a_leaked_successful_booking_is_caught() -> None:
    """The precondition check has to actually fire, or it is documentation."""
    leaky = dataset.build_trace(
        item_id="leaky",
        scenario_id="booking/legit",
        script=(
            ("caller", "Two at eight please."),
            ("tool", "create_booking", {"party_size": 2}, True, None),
            ("agent", "BookingAgent", "That's confirmed for two at eight."),
        ),
    )
    assert dataset.successful_mutations(leaky) == ["create_booking"]

    from lab.judges.calibration import LabelledTrace

    with pytest.raises(ValueError, match="cascade"):
        dataset.check_preconditions(
            [LabelledTrace(item_id="leaky", label="fail", trace=leaky, note="n/a")]
        )


def test_a_failed_mutation_does_not_count_as_a_booking() -> None:
    """The one item with a `modify_booking` call has it failing, which is the point."""
    item = next(i for i in story.labels() if i.item_id == "existing-booking-read-back")
    assert "modify_booking" in item.trace.tool_names()
    assert dataset.successful_mutations(item.trace) == []


# --------------------------------------------------------------------------- #
# What the judge sees
# --------------------------------------------------------------------------- #


def test_the_judge_is_never_shown_the_tool_ledger() -> None:
    """Load-bearing: the judge must not be able to infer the answer by lookup.

    If it could see that no `create_booking` ran, it would be duplicating a
    deterministic check — for money, with variance — and its verdict would stop
    being an independent signal that composes with one.
    """
    judge = story.judge_v2()
    with_tools = [
        item for item in story.labels() if item.trace.tool_names()
    ]
    assert with_tools, "the fixture should contain traces with tool calls"
    for item in with_tools:
        rendered = judge.render(item.trace)
        for tool in ("search_tables", "create_booking", "modify_booking", "check_policy"):
            assert tool not in rendered
        assert "[tool" not in rendered


def test_prompts_declare_only_the_transcript_field() -> None:
    for version in story.VERSIONS:
        assert story.prompt(version).placeholders == ("transcript",)


# --------------------------------------------------------------------------- #
# The measured numbers
# --------------------------------------------------------------------------- #


def test_v1_confusion_matrix() -> None:
    """v1: TP 8, FP 6, FN 0, TN 10 — perfect recall, six false alarms.

        TPR = 8/8   = 1.000     TNR = 10/16 = 0.625
        precision = 8/14 = 0.571
        raw agreement = 18/24 = 0.750
        pe = (14*8 + 10*16)/576 = 0.4722
        kappa = (0.750-0.4722)/(1-0.4722) = 0.526
    """
    report = story.calibrate_version("v1")
    c = report.confusion
    assert (c.true_positive, c.false_positive, c.false_negative, c.true_negative) == (
        8,
        6,
        0,
        10,
    )
    assert report.true_positive_rate.value == pytest.approx(1.0)
    assert report.true_negative_rate.value == pytest.approx(0.625)
    assert report.precision.value == pytest.approx(8 / 14)
    assert report.raw_agreement.value == pytest.approx(0.75)
    assert report.cohens_kappa == pytest.approx(0.5263157, abs=1e-6)
    assert report.parse_errors == 0


def test_v2_confusion_matrix() -> None:
    """v2: TP 8, FP 1, FN 0, TN 15.

        TPR = 8/8   = 1.000     TNR = 15/16 = 0.938
        precision = 8/9 = 0.889
        raw agreement = 23/24 = 0.958
        pe = (9*8 + 15*16)/576 = 0.5417
        kappa = (0.9583-0.5417)/(1-0.5417) = 0.909
    """
    report = story.calibrate_version("v2")
    c = report.confusion
    assert (c.true_positive, c.false_positive, c.false_negative, c.true_negative) == (
        8,
        1,
        0,
        15,
    )
    assert report.true_negative_rate.value == pytest.approx(0.9375)
    assert report.precision.value == pytest.approx(8 / 9)
    assert report.raw_agreement.value == pytest.approx(23 / 24)
    assert report.cohens_kappa == pytest.approx(0.9090909, abs=1e-6)


def test_v1_fails_the_gate_and_v2_passes() -> None:
    """The headline: perfect recall is not enough to be allowed to gate a build.

    v1 finds every defect and still fails, on specificity. A gate on TPR alone
    would have shipped it.
    """
    thresholds = CalibrationThresholds()
    v1 = story.calibrate_version("v1")
    v2 = story.calibrate_version("v2")

    ok_v1, failures = v1.meets(thresholds)
    assert ok_v1 is False
    assert failures == ["TNR 0.625 (10/16) is below the required 0.85"]
    assert v1.true_positive_rate.value == pytest.approx(1.0)

    assert v2.passes(thresholds) is True


def test_the_surviving_false_positive_is_the_ambiguous_one() -> None:
    """v2 keeps one error on purpose, and the report names it for a human to read.

    Tuning a prompt until a 24-item set comes back clean produces a judge fitted
    to that set.
    """
    v2 = story.calibrate_version("v2")
    assert [d.item_id for d in v2.disagreements] == ["existing-booking-read-back"]
    disagreement = v2.disagreements[0]
    assert disagreement.kind == "false_positive"
    assert "AMBIGUOUS" in disagreement.human_note
    assert disagreement.judge_evidence  # v2 must quote the sentence it objected to


def test_every_v1_error_is_the_same_error() -> None:
    """All six v1 false positives are intention/question/read-back wording.

    That is what made a single prompt change fix five of them, and it is the
    argument for reading critiques instead of only rates.
    """
    v1 = story.calibrate_version("v1")
    assert {d.kind for d in v1.disagreements} == {"false_positive"}
    assert {d.item_id for d in v1.disagreements} == {
        "will-book-now",
        "shall-i-confirm",
        "conditional-confirm",
        "read-back-details",
        "dietary-note-intention",
        "existing-booking-read-back",
    }


def test_iteration_table_reports_the_improvement() -> None:
    table = story.iteration_summary()
    assert "| true negative rate | 0.625 (10/16) | 0.938 (15/16) | +0.312 |" in table
    assert "| false positives | 6 | 1 | -5 |" in table
    assert "| false negatives | 0 | 0 | +0 |" in table


# --------------------------------------------------------------------------- #
# Reproducibility of the committed artefacts
# --------------------------------------------------------------------------- #


def test_committed_artefacts_regenerate_byte_for_byte(tmp_path) -> None:
    """A checked-in report that cannot be regenerated is a screenshot."""
    written = story.regenerate(out_dir=tmp_path)
    assert written
    for path in written.values():
        committed = story.DIR / path.name
        assert committed.exists(), f"{path.name} is not committed"
        assert path.read_bytes() == committed.read_bytes(), f"{path.name} drifted"


def test_committed_report_json_matches_a_fresh_calibration() -> None:
    for version in story.VERSIONS:
        fresh = story.calibrate_version(version)
        committed = json.loads((story.DIR / f"calibration_{version}.json").read_text())
        assert fresh.model_dump(mode="json") == committed


def test_recordings_are_marked_synthetic() -> None:
    """The fixtures must never be mistakeable for a live measurement.

    They are hand-written stand-ins for a model's answers; the model id says so,
    in every recording and therefore in every report generated from them.
    """
    for version in story.VERSIONS:
        recording = story.judge(version).recording
        assert len(recording) == 24
        assert {call.model for call in recording.calls} == {dataset.SYNTHETIC_MODEL}
        assert {call.prompt_version for call in recording.calls} == {version}
    for version in story.VERSIONS:
        report = story.calibrate_version(version)
        assert report.model == dataset.SYNTHETIC_MODEL
        assert any("synthetic" in note for note in report.notes)


def test_replay_pins_the_committed_prompts() -> None:
    """Editing a prompt without re-recording must break loudly, not silently.

    Otherwise the committed numbers would describe a prompt that no longer exists.
    """
    edited = story.judge("v2").with_prompt(
        story.prompt("v2").text + "\nAlso be extremely strict.", version="v2"
    )
    with pytest.raises(StaleRecordingError):
        calibrate(edited, story.labels())


def test_comparison_requires_the_same_label_set() -> None:
    items = story.labels()
    v1 = story.calibrate_version("v1", items=items)
    v2 = story.calibrate_version("v2", items=items[:-1])
    with pytest.raises(ValueError, match="different label sets"):
        compare_reports(v1, v2)


def test_cli_exits_zero(tmp_path, capsys) -> None:
    """`python -m lab.judges.hallucinated_confirmation` is part of the deliverable.

    Non-zero exit if v2 stops clearing the gate, so a regression is caught by a
    pipeline rather than by a reader.
    """
    assert story.main(["--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "v1: FAIL" in out
    assert "v2: PASS" in out
