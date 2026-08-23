"""Score consistency, scorer calibration, and the demo's regression gate.

The three things in this file are the pack's answers to three questions a buyer of
a scoring product should ask and usually cannot:

    does the same performance get the same grade?          -> consistency
    how do you know the grade is right?                    -> calibration
    would you notice if any of that changed?               -> the gate

All of it runs offline with no API keys. The "judge" under calibration is the
product's own scorer, wired into `lab.judges` through the `Completion` protocol.
"""

from __future__ import annotations

import math

import pytest

from lab.judges.calibration import CalibrationThresholds
from lab.judges.registry import JudgeBelowThresholdError, JudgeRegistry
from lab.simulator.passk import PassKPolicy, summarise_stability

from roleplay.calibration import (
    JUDGE_NAME,
    PROMPT_VERSION,
    RUBRIC_PATH,
    ScorerCompletion,
    build_scorer_judge,
    calibrate_scorer,
    gate_report,
    labelled_from_corpus,
    render_disagreements,
)
from roleplay.consistency import ConsistencyReport, measure_consistency, spread_of
from roleplay.corpus import Corpus
from roleplay.demo import run_demo
from roleplay.scorer import PASS_TOTAL

from tests.roleplay_fixtures import ALIASES, corpus, script  # noqa: F401


# --------------------------------------------------------------------------- #
# ScoreSpread arithmetic
# --------------------------------------------------------------------------- #


def test_spread_arithmetic() -> None:
    spread = spread_of("x", [16, 15, 14, 13, 12], max_total=20, tolerance=0.0)
    assert spread.k == 5
    assert spread.mean == 14.0
    assert spread.stdev == pytest.approx(math.sqrt(2.0), abs=0.001)
    assert (spread.minimum, spread.maximum, spread.spread) == (12, 16, 4)
    assert spread.verdict == "OUTSIDE_TOLERANCE"
    assert not spread.ok


def test_a_flat_run_is_within_a_zero_tolerance() -> None:
    spread = spread_of("x", [16] * 5, max_total=20, tolerance=0.0)
    assert spread.stdev == 0.0
    assert spread.verdict == "WITHIN_TOLERANCE"
    assert spread.verdict_flips == 0


def test_verdict_flips_are_not_the_same_as_failures() -> None:
    """Three passes then two fails is one flip; alternating is four. Both are 3/5."""
    walking = spread_of("x", [16, 15, 14, 13, 12], max_total=20, tolerance=0.0)
    oscillating = spread_of("x", [14, 13, 14, 13, 14], max_total=20, tolerance=0.0)
    assert walking.verdict_flips == 1
    assert oscillating.verdict_flips == 4
    assert walking.spread > oscillating.spread


def test_stdev_is_defined_at_k_of_one() -> None:
    """k=1 is a legitimately weak claim, not an exception."""
    assert spread_of("x", [16], max_total=20, tolerance=0.0).stdev == 0.0


def test_the_tolerance_is_printed_with_the_verdict() -> None:
    described = spread_of("x", [16, 12], max_total=20, tolerance=1.5).describe()
    assert "tolerance 1.5 pt" in described
    assert "spread 4 pt" in described
    assert f"threshold {PASS_TOTAL}" in described


# --------------------------------------------------------------------------- #
# The warm/cold measurement
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def consistency_reports(corpus: Corpus) -> dict[str, ConsistencyReport]:  # noqa: F811
    out: dict[str, ConsistencyReport] = {}
    for scenario in corpus:
        if scenario.consistency is None:
            continue
        out[scenario.id] = measure_consistency(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            k=scenario.consistency.k,
            tolerance=scenario.consistency.tolerance,
        )
    return out


def test_measure_consistency_refuses_a_single_run(corpus: Corpus) -> None:  # noqa: F811
    scenario = corpus.by_id(ALIASES["consistency"])
    with pytest.raises(ValueError, match="cannot disagree with itself"):
        measure_consistency(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            k=1,
        )


def test_the_walking_row_matches_its_declared_numbers(consistency_reports) -> None:
    report = consistency_reports["consistency-identical-transcript-warm-k5"]
    assert report.warm_spread.scores == (16, 15, 14, 13, 12)
    assert report.warm_spread.spread == 4
    assert report.warm_spread.verdict_flips == 1
    assert report.warm_stability.verdict == "FLAKY"
    assert report.warm_stability.passes == 3


def test_the_oscillating_row_matches_its_declared_numbers(consistency_reports) -> None:
    report = consistency_reports["consistency-borderline-transcript-warm-k5"]
    assert report.warm_spread.scores == (14, 13, 14, 13, 14)
    assert report.warm_spread.spread == 1
    assert report.warm_spread.verdict_flips == 4
    assert report.warm_stability.verdict == "FLAKY"


def test_the_cold_control_arm_is_flat_on_every_row(consistency_reports) -> None:
    """Without this the finding is 'scores move', which localises nothing."""
    for scenario_id, report in consistency_reports.items():
        assert report.cold_spread.spread == 0, scenario_id
        assert report.cold_stability.verdict == "STABLE_PASS", scenario_id
        assert report.localises_to_shared_state, scenario_id


def test_the_render_names_the_localisation(consistency_reports) -> None:
    rendered = consistency_reports["consistency-identical-transcript-warm-k5"].render()
    assert "warm (one long-lived scorer" in rendered
    assert "cold (a fresh scorer per repeat" in rendered
    assert "state the scoring service holds between sessions" in rendered


def test_every_consistency_row_meets_its_declared_floors(
    corpus: Corpus, consistency_reports  # noqa: F811
) -> None:
    """When the curve is fixed, these go red - which is the point of a floor."""
    for scenario in corpus:
        spec = scenario.consistency
        if spec is None:
            continue
        report = consistency_reports[scenario.id]
        if spec.expected_spread is not None:
            assert report.warm_spread.spread >= spec.expected_spread, scenario.id
        if spec.expected_flips is not None:
            assert report.warm_spread.verdict_flips >= spec.expected_flips, scenario.id


def test_flaky_is_not_a_pass(consistency_reports) -> None:
    summary = summarise_stability(
        [r.warm_stability for r in consistency_reports.values()]
    )
    assert summary.stable_pass == 0
    assert summary.flaky == len(consistency_reports)


def test_a_loosened_policy_is_recorded_with_the_number(corpus: Corpus) -> None:  # noqa: F811
    """Tolerating known noise must be a reviewable decision, not a habit."""
    scenario = corpus.by_id(ALIASES["consistency"])
    report = measure_consistency(
        scenario_id=scenario.id,
        trainee_turns=scenario.trainee.turns,
        profile=corpus.profile_for(scenario),
        k=5,
        policy=PassKPolicy(stable_pass_at_or_above=0.6),
    )
    assert report.warm_stability.verdict == "STABLE_PASS"
    assert "STABLE_PASS at pass rate >= 60%" in report.warm_stability.policy.describe()
    # The spread verdict is unmoved: loosening the binary gate does not make the
    # score stop moving, and the two verdict families are independent for that reason.
    assert report.warm_spread.verdict == "OUTSIDE_TOLERANCE"


# --------------------------------------------------------------------------- #
# Calibrating the scorer as a judge
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def calibration(corpus: Corpus):  # noqa: F811
    return calibrate_scorer(corpus)


def test_the_labelled_set_comes_from_the_corpus(corpus: Corpus) -> None:  # noqa: F811
    items, traces = labelled_from_corpus(corpus)
    assert len(items) == len(corpus) == len(traces)
    assert {i.item_id for i in items} == {s.id for s in corpus}
    for item in items:
        assert item.note, "every label must carry the reviewer's reason"
        assert item.labeller == "corpus:expectation"


def test_labelled_traces_never_contain_a_score(corpus: Corpus) -> None:  # noqa: F811
    """The instrument must not be handed its own answer."""
    items, _ = labelled_from_corpus(corpus)
    for item in items:
        assert "score_session" not in item.trace.tool_names()


def test_the_judge_is_stamped_with_the_rubric_it_measured(calibration) -> None:
    report, judge, _ = calibration
    assert report.judge == JUDGE_NAME
    assert report.prompt_version == PROMPT_VERSION
    assert report.prompt_sha256 == judge.prompt_sha256
    assert judge.template.digest == judge.prompt_sha256
    assert "{{transcript}}" in RUBRIC_PATH.read_text(encoding="utf-8")


def test_the_measured_rates(calibration) -> None:
    """Pinned. A change in the scorer's agreement should be a diff to review."""
    report, _, _ = calibration
    assert report.n == 15
    confusion = report.confusion
    assert (confusion.true_positive, confusion.false_positive) == (3, 1)
    assert (confusion.false_negative, confusion.true_negative) == (3, 8)
    assert report.true_positive_rate.value == pytest.approx(0.5, abs=0.001)
    assert report.true_negative_rate.value == pytest.approx(8 / 9, abs=0.001)
    assert report.cohens_kappa == pytest.approx(0.412, abs=0.002)
    assert report.parse_errors == 0


def test_every_missed_defect_is_a_compliance_miss(calibration) -> None:
    """The composition of the errors is the finding, not the rate."""
    report, _, _ = calibration
    misses = [d.item_id for d in report.disagreements if d.kind == "false_negative"]
    assert len(misses) == 3
    assert all(m.startswith("compliance-") for m in misses)
    alarms = [d.item_id for d in report.disagreements if d.kind == "false_positive"]
    assert alarms == ["locale-es-mx-registered-spanish-disclosure"]


def test_the_gate_refuses_the_scorer(calibration) -> None:
    report, judge, _ = calibration
    cleared, reasons = gate_report(report, judge)
    assert not cleared
    assert any("TPR" in r for r in reasons)
    assert any("registry refused" in r for r in reasons)


def test_the_registry_really_raises_in_ci_mode(calibration) -> None:
    """The gate has to be load-bearing, not a printed opinion."""
    report, judge, _ = calibration
    registry = JudgeRegistry(thresholds=CalibrationThresholds())
    registry.register(judge)
    with pytest.raises(JudgeBelowThresholdError, match="TPR"):
        registry.require_calibrated(judge, ci=True)


def test_a_lenient_threshold_would_admit_it(calibration) -> None:
    """Shows the refusal is about a stated standard rather than about the code."""
    report, _, _ = calibration
    ok, failures = report.meets(CalibrationThresholds(min_tpr=0.4, min_tnr=0.8))
    assert ok and not failures


def test_calibration_uses_a_fresh_scorer_per_item(corpus: Corpus) -> None:  # noqa: F811
    """Otherwise the confusion matrix is a function of item ordering."""
    _, traces = labelled_from_corpus(corpus)
    completion = ScorerCompletion(traces)
    judge = build_scorer_judge(traces)
    first = judge.judge(traces[ALIASES["consistency"]], item_id=ALIASES["consistency"])
    for _ in range(6):
        judge.judge(traces[ALIASES["exemplary"]], item_id=ALIASES["exemplary"])
    again = judge.judge(traces[ALIASES["consistency"]], item_id=ALIASES["consistency"])
    assert first.raw == again.raw
    assert isinstance(judge.completion, ScorerCompletion)
    assert completion.traces.keys() == traces.keys()


def test_an_unknown_item_is_an_error_not_an_empty_transcript(corpus: Corpus) -> None:  # noqa: F811
    _, traces = labelled_from_corpus(corpus)
    judge = build_scorer_judge(traces)
    with pytest.raises(KeyError, match="drifted apart"):
        judge.judge(traces[ALIASES["exemplary"]], item_id="no-such-row")


def test_disagreements_render_both_sides(calibration) -> None:
    report, _, _ = calibration
    rendered = render_disagreements(report)
    assert "FALSE_NEGATIVE" in rendered
    assert "human:" in rendered and "scorer:" in rendered


def test_a_calibration_report_can_be_written(calibration, tmp_path) -> None:
    report, _, _ = calibration
    written = report.write(tmp_path, stem="roleplay")
    assert written
    for path in written.values():
        assert path.is_file() and path.stat().st_size > 0


# --------------------------------------------------------------------------- #
# The demo's regression gate
# --------------------------------------------------------------------------- #


def test_the_demo_runs_clean_with_red_findings(capsys) -> None:
    """Two verdicts, and they are not the same verdict.

    The findings are red - three real defects, all reported. The gate is green,
    because nothing moved: every declared expected failure fired, no undeclared
    contract failed, every consistency floor was met, and the calibration gate
    refused the scorer as it is supposed to.
    """
    outcome = run_demo()
    assert outcome.ok, "\n".join(outcome.surprises)
    assert not outcome.gate_cleared
    assert len(outcome.results) == 15

    printed = capsys.readouterr().out
    assert "regression gate: PASS" in printed
    assert "SCORE INSTABILITY" in printed
    assert "HALLUCINATED FEEDBACK" in printed
    assert "COMPLIANCE MISS" in printed

    reds = {
        name
        for report in outcome.reports.values()
        for name in (r.name for r in report.failures())
    }
    assert reds == {"tools", "score-claims-backed", "feedback-grounded"}
