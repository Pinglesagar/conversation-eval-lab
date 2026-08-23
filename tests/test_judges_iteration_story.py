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
from lab.judges.calibration import (
    CalibrationThresholds,
    calibrate,
    compare_reports,
    self_consistency,
)
from lab.judges.hallucinated_confirmation import dataset
from lab.judges.judge import Recording, ReplayJudge, StaleRecordingError


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
    """v1: TP 2, FP 0, FN 6, TN 16 — no false alarms, six misses.

        TPR = 2/8   = 0.250     TNR = 16/16 = 1.000
        precision = 2/2 = 1.000
        raw agreement = 18/24 = 0.750
        pe = (2*8 + 22*16)/576 = 0.6389
        kappa = (0.750-0.6389)/(1-0.6389) = 0.308

    Note which direction the errors run. The prompt that "looks fine" is not
    trigger-happy, it is asleep: it waved through six explicit past-tense claims.
    """
    report = story.calibrate_version("v1")
    c = report.confusion
    assert (c.true_positive, c.false_positive, c.false_negative, c.true_negative) == (
        2,
        0,
        6,
        16,
    )
    assert report.true_positive_rate.value == pytest.approx(0.25)
    assert report.true_negative_rate.value == pytest.approx(1.0)
    assert report.precision.value == pytest.approx(1.0)
    assert report.raw_agreement.value == pytest.approx(0.75)
    assert report.cohens_kappa == pytest.approx(0.3076923, abs=1e-6)
    assert report.parse_errors == 0


def test_v2_confusion_matrix() -> None:
    """v2: TP 8, FP 0, FN 0, TN 16 — no measured error on this set.

        TPR = 8/8   = 1.000     TNR = 16/16 = 1.000
        raw agreement = 24/24 = 1.000
        pe = (8*8 + 16*16)/576 = 0.5556
        kappa = (1-0.5556)/(1-0.5556) = 1.000

    Asserted as counts, not celebrated as a score: 8/8 and 16/16 are consistent
    with true rates near 0.68 and 0.81 at 95% confidence, and a set the judge
    never fails cannot measure it again. `iteration.md` says so in the artefact.
    """
    report = story.calibrate_version("v2")
    c = report.confusion
    assert (c.true_positive, c.false_positive, c.false_negative, c.true_negative) == (
        8,
        0,
        0,
        16,
    )
    assert report.true_positive_rate.value == pytest.approx(1.0)
    assert report.true_negative_rate.value == pytest.approx(1.0)
    assert report.raw_agreement.value == pytest.approx(1.0)
    assert report.cohens_kappa == pytest.approx(1.0)
    assert report.parse_errors == 0


def test_v1_fails_the_gate_and_v2_passes() -> None:
    """The headline: the naive prompt fails, and it fails on the dangerous rate.

    v1's specificity is perfect. A gate on TNR alone — or on raw agreement, which
    is a respectable-looking 0.750 — would have shipped a judge that misses three
    defects in four.
    """
    thresholds = CalibrationThresholds()
    v1 = story.calibrate_version("v1")
    v2 = story.calibrate_version("v2")

    ok_v1, failures = v1.meets(thresholds)
    assert ok_v1 is False
    assert failures == ["TPR 0.250 (2/8) is below the required 0.85"]
    assert v1.true_negative_rate.value == pytest.approx(1.0)
    assert v1.raw_agreement.value == pytest.approx(0.75)

    assert v2.passes(thresholds) is True


def test_the_gate_thresholds_are_configurable_and_printed() -> None:
    """A threshold nobody can see is not a standard, and a fixed one is not a policy."""
    default = CalibrationThresholds()
    assert "TPR >= 0.85" in default.describe()
    assert "TNR >= 0.85" in default.describe()
    assert "parse errors <= 0%" in default.describe()

    v2 = story.calibrate_version("v2")
    assert v2.passes(default) is True

    # Kappa is off by default (prevalence dependent); switching it on prints it.
    strict = CalibrationThresholds(min_tpr=0.99, min_tnr=0.99, min_kappa=0.9, min_items=30)
    assert "kappa >= 0.90" in strict.describe()
    ok, failures = v2.meets(strict)
    assert ok is False
    assert failures == ["calibrated on only 24 items, below the minimum of 30"]


def test_every_v1_error_is_a_miss() -> None:
    """All six v1 disagreements are false negatives, and the critiques say why.

    The critique is the evidence for the diagnosis: v1 read "hallucinate" as
    "invent details the caller never gave", so a plain past-tense claim about a
    booking the caller *did* ask for came back PASS.
    """
    v1 = story.calibrate_version("v1")
    assert {d.kind for d in v1.disagreements} == {"false_negative"}
    assert {d.item_id for d in v1.disagreements} == {
        "p8-birthday-phantom",
        "gone-ahead-corner-table",
        "table-held-under-name",
        "moved-to-nine-claim",
        "cancelled-claim",
        "claim-buried-in-policy-answer",
    }
    assert any("inventing" in d.judge_critique for d in v1.disagreements)


def test_v2_has_no_disagreements_and_quotes_its_positives() -> None:
    """v2 agrees with the labeller everywhere, and every FAIL carries its quote.

    The quote requirement is the mechanism, so it is checked rather than believed:
    an unquoted FAIL would mean the prompt's own rule was not followed.
    """
    v2 = story.calibrate_version("v2")
    assert v2.disagreements == []

    judge = story.judge_v2()
    for item in story.labels():
        verdict = judge.judge(item.trace, item_id=item.item_id)
        assert verdict.label == item.label, item.item_id
        assert verdict.status == item.label
        if verdict.label == "fail":
            assert verdict.evidence, f"{item.item_id} failed without quoting a sentence"


def test_iteration_table_reports_the_improvement() -> None:
    table = story.iteration_summary()
    assert (
        "| true positive rate (recall) | 0.250 (2/8) | 1.000 (8/8) | +0.750 |" in table
    )
    assert (
        "| true negative rate (specificity) | 1.000 (16/16) | 1.000 (16/16) | +0.000 |"
        in table
    )
    assert "| false negatives | 6 | 0 | -6 |" in table
    assert "| false positives | 0 | 0 | +0 |" in table
    assert "| unparseable answers | 0 | 0 | +0 |" in table
    # The generated artefact must carry the caveats, not just the good news.
    assert "Twenty-four items" in table
    assert "cannot measure that judge any" in table


# --------------------------------------------------------------------------- #
# Does the judge hold still?
# --------------------------------------------------------------------------- #


def test_v2_is_unanimous_across_three_identical_runs() -> None:
    runs = story.stability("v2")
    assert runs.runs == story.REPLICATES == 3
    assert runs.n == 24
    assert runs.unstable == []
    assert runs.unanimity.value == pytest.approx(1.0)


def test_v1_is_unstable_on_two_items() -> None:
    """The naive prompt does not hold still, at temperature 0, on identical input."""
    runs = story.stability("v1")
    assert [item.item_id for item in runs.unstable] == [
        "all-set-saturday",
        "claim-buried-in-policy-answer",
    ]
    assert runs.unanimity.numerator == 22
    assert runs.unanimity.denominator == 24


def test_v1_rates_are_stable_while_its_verdicts_are_not() -> None:
    """The point of measuring stability per item rather than per rate.

    v1's two unstable items sit on opposite sides, so they cancel: all three runs
    report exactly TP 2 / FN 6 / TN 16 and a reader watching the rates would
    conclude the judge is deterministic. It is not. A v3-vs-v2 comparison that
    moved by one or two items would have been reading this noise.
    """
    matrices = set()
    for run in range(1, story.REPLICATES + 1):
        judge = ReplayJudge(
            recording=story.verdicts_path("v1", run),
            name=story.JUDGE_NAME,
            prompt=story.prompt("v1"),
            version="v1",
            model=story.recorded_model("v1"),
            include_tools=False,
        )
        report = calibrate(judge, story.labels(), attach=False)
        c = report.confusion
        matrices.add(
            (c.true_positive, c.false_positive, c.false_negative, c.true_negative)
        )

    assert matrices == {(2, 0, 6, 16)}, "the rates were supposed to be identical"
    assert story.stability("v1").unstable, "yet the per-item verdicts moved"


def test_stability_refuses_to_compare_two_different_prompts() -> None:
    """"The same judge twice" is the premise; mixing versions is a category error."""
    with pytest.raises(ValueError, match="repeated runs of ONE judge"):
        self_consistency([story.judge_v1(), story.judge_v2()], story.labels())


def test_replicates_are_pinned_to_the_same_prompt() -> None:
    """Every run of a version must have been asked the identical question.

    The recorded digest is of the *rendered* prompt, so it is per item; what has to
    hold is that run 2 and run 3 asked each item exactly what run 1 asked. Without
    that, a "stability" figure would be measuring two different questions.
    """
    for version in story.VERSIONS:
        per_run = []
        for run in range(1, story.REPLICATES + 1):
            recording = Recording.load(story.verdicts_path(version, run))
            assert len(recording) == 24
            per_run.append(
                {call.item_id: call.prompt_sha256 for call in recording.calls}
            )
        assert per_run[1] == per_run[0]
        assert per_run[2] == per_run[0]

        # And the rendered prompts really are this version's template, not v1's.
        judge = story.judge(version)
        for item in story.labels():
            assert (
                per_run[0][item.item_id]
                == judge.request(item.trace, item_id=item.item_id).prompt_sha256
            )


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


def test_recordings_are_captured_provider_output() -> None:
    """The reports must name the model that actually answered.

    The model id is read out of the recording rather than written into the code, so
    a report cannot credit a model that never ran. This test is the descendant of
    one that asserted the opposite — that the fixtures were hand-written stand-ins
    and stamped as such. They are not stand-ins any more, and the assertion has to
    move with the fact.
    """
    for version in story.VERSIONS:
        recording = story.judge(version).recording
        assert len(recording) == 24
        models = {call.model for call in recording.calls}
        assert len(models) == 1
        model = models.pop()
        assert model == story.recorded_model(version)
        assert "synthetic" not in model
        assert "/" in model, "a litellm route names its provider"
        assert {call.prompt_version for call in recording.calls} == {version}

    for version in story.VERSIONS:
        report = story.calibrate_version(version)
        assert report.model == story.recorded_model(version)
        assert any("captured provider output" in note for note in report.notes)


def test_no_code_path_can_invent_a_verdict(tmp_path) -> None:
    """Offline regeneration replays recordings; it cannot synthesise one.

    The failure this guards against is the one this directory used to have: an
    offline mode that could produce a full calibration report without a model ever
    having answered.
    """
    (tmp_path / "verdicts_v1.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(Exception):
        story.calibrate_version("v1", directory=tmp_path)


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
