"""Tests for the run report: rates, calibration, and self-audit.

WHAT THIS DEMONSTRATES
----------------------
Rendering is where an evaluation most often overstates itself, so the tests here
are about what the report *cannot* do: it cannot print a percentage without its
counts, cannot present a judge verdict without the judge's calibration, cannot
quote a latency without the calibration gate's verdict, and cannot report an
empty run as a pass. Each of those is a construction-time or render-time
guarantee rather than a review convention.
"""

from __future__ import annotations

import json
import re

import pytest

from lab.report.report import (
    ContractStat,
    FailureRecord,
    JudgeCalibration,
    JudgeSummary,
    Rate,
    RunReport,
    VoiceMetrics,
)
from lab.simulator.passk import RunOutcome, verdict_from_outcomes


def _verdict(scenario: str, pattern: str, evidence: str | None = None):
    return verdict_from_outcomes(
        scenario,
        [
            RunOutcome(
                index=i,
                passed=char == "P",
                session_id=f"{scenario}-{i}",
                evidence=None if char == "P" else (evidence or "no create_booking call"),
                failed_checks=[] if char == "P" else ["decision_vs_action"],
            )
            for i, char in enumerate(pattern)
        ],
    )


def _calibration() -> JudgeCalibration:
    return JudgeCalibration(
        labelled_positive=20,
        labelled_negative=20,
        true_positives=18,
        true_negatives=17,
        labelled_by="two reviewers, disagreements resolved by re-reading the trace",
    )


def _report() -> RunReport:
    return RunReport(
        title="TableMate evaluation",
        subject="tablemate 0.1.0",
        run_label="fixture-run",
        stability=[
            _verdict("booking/simple", "PPPPP"),
            _verdict("booking/large_party", "FFFFF"),
            _verdict("modification/party_size", "PPFPF"),
        ],
        contracts=[
            ContractStat(
                name="decision_vs_action",
                failures=5,
                runs=15,
                description="a claimed booking must have a create_booking call",
                failing_scenarios=["booking/large_party"],
            ),
            ContractStat(
                name="no_re_ask",
                failures=2,
                runs=15,
                description="a handoff must not re-ask an answered question",
                failing_scenarios=["modification/party_size"],
            ),
        ],
        judges=[
            JudgeSummary(
                name="tone",
                model="gpt-4o-mini",
                calibration=_calibration(),
                judged=15,
                flagged=3,
                prompt_id="tone-v3",
            )
        ],
        voice=VoiceMetrics(
            samples=45,
            mean_ms=812.5,
            p50_ms=790.0,
            p95_ms=1240.0,
            calibration_verdict="PASS",
            calibration_report="fixtures/calibration_report.md",
        ),
        failures=[
            FailureRecord(
                scenario_id="booking/large_party",
                contract="decision_vs_action",
                evidence='agent said "you\'re all booked for six" but create_booking '
                "was never called",
                session_id="booking/large_party-0",
                trace_path="fixtures/large_party.jsonl",
                from_agent="GreeterAgent",
                to_agent="BookingAgent",
                note="reproduces on every run for party_size >= 6",
            )
        ],
        notes=["Judge verdicts replayed from fixtures; no live calls in this run."],
    )


# --------------------------------------------------------------------------- #
# The rate rule
# --------------------------------------------------------------------------- #


def test_every_rate_in_the_markdown_carries_its_counts() -> None:
    """No naked percentage anywhere in the document. The house rule, enforced."""
    markdown = _report().to_markdown()
    percentages = list(re.finditer(r"\d+\.\d+%", markdown))
    assert percentages, "expected the report to quote some rates"
    for match in percentages:
        prefix = markdown[max(0, match.start() - 24) : match.start()]
        assert re.search(r"\d+/\d+ \($", prefix), (
            f"percentage {match.group(0)!r} is not preceded by its counts: "
            f"...{prefix}{match.group(0)}"
        )


def test_expected_rate_strings_are_present() -> None:
    markdown = _report().to_markdown()
    assert "1/3 (33.3%)" in markdown  # one of three scenarios stable-passes
    assert "5/5 (100.0%)" in markdown  # booking/simple
    assert "3/5 (60.0%)" in markdown  # the flaky scenario's pass rate
    assert "2/5 (40.0%)" in markdown  # its flake rate
    assert "5/15 (33.3%)" in markdown  # decision_vs_action failures


def test_rate_cannot_exceed_its_denominator() -> None:
    with pytest.raises(ValueError, match="cannot exceed its denominator"):
        Rate(numerator=6, denominator=5)


# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #


def test_headline_verdict_is_derived_and_flags_flakiness() -> None:
    report = _report()
    assert report.verdict == "FAIL"
    headline = report.headline()
    assert "1/3 (33.3%) scenarios stable-pass" in headline
    assert "1/3 (33.3%) FLAKY (not a pass)" in headline


def test_a_clean_run_passes() -> None:
    report = RunReport(
        stability=[_verdict("a", "PPP"), _verdict("b", "PPP")],
        contracts=[ContractStat(name="decision_vs_action", failures=0, runs=6)],
    )
    assert report.verdict == "PASS"
    assert report.integrity_gaps() == []


def test_an_empty_report_is_a_failure_not_a_pass() -> None:
    # A misconfigured run that collected nothing must not announce success.
    assert RunReport().verdict == "FAIL"


def test_a_flaky_scenario_alone_is_enough_to_fail_the_run() -> None:
    report = RunReport(stability=[_verdict("a", "PPP"), _verdict("b", "PPF")])
    assert report.verdict == "FAIL"


# --------------------------------------------------------------------------- #
# Judges cannot be reported without calibration
# --------------------------------------------------------------------------- #


def test_a_judge_summary_cannot_be_built_without_calibration() -> None:
    with pytest.raises(ValueError):
        JudgeSummary(name="tone", model="gpt-4o-mini", judged=10, flagged=2)  # type: ignore[call-arg]


def test_judge_section_prints_verdicts_beside_tpr_and_tnr() -> None:
    markdown = _report().to_markdown()
    assert "3/15 (20.0%)" in markdown  # flag rate
    assert "18/20 (90.0%)" in markdown  # TPR
    assert "17/20 (85.0%)" in markdown  # TNR
    assert "missed 2/20 (10.0%) of labelled failures" in markdown
    assert "wrongly flagged 3/20 (15.0%)" in markdown


def test_calibration_counts_must_be_consistent() -> None:
    with pytest.raises(ValueError, match="true_positives"):
        JudgeCalibration(
            labelled_positive=5, labelled_negative=5, true_positives=6, true_negatives=5
        )


def test_a_thinly_calibrated_judge_is_flagged_as_a_gap() -> None:
    report = RunReport(
        stability=[_verdict("a", "PP")],
        judges=[
            JudgeSummary(
                name="tone",
                model="gpt-4o-mini",
                judged=2,
                flagged=1,
                calibration=JudgeCalibration(
                    labelled_positive=3,
                    labelled_negative=3,
                    true_positives=3,
                    true_negatives=3,
                ),
            )
        ],
    )
    gaps = " ".join(report.integrity_gaps())
    assert "calibrated on only 6 hand-labelled examples" in gaps


# --------------------------------------------------------------------------- #
# Voice metrics cannot be reported without the calibration gate
# --------------------------------------------------------------------------- #


def test_voice_metrics_require_a_calibration_verdict() -> None:
    with pytest.raises(ValueError):
        VoiceMetrics(samples=10, mean_ms=800.0)  # type: ignore[call-arg]


def test_uncalibrated_voice_metrics_are_marked_untrustworthy() -> None:
    report = RunReport(
        stability=[_verdict("a", "PP")],
        voice=VoiceMetrics(samples=10, p95_ms=1400.0, calibration_verdict="NOT_RUN"),
    )
    markdown = report.to_markdown()
    assert "Calibration gate: **NOT_RUN**" in markdown
    assert "These figures are not certified" in markdown
    assert any("unproven until the calibration gate passes" in gap for gap in report.integrity_gaps())
    assert report.voice is not None and report.voice.trustworthy is False


def test_estimated_timestamps_in_a_voice_figure_are_called_out() -> None:
    report = RunReport(
        stability=[_verdict("a", "PP")],
        voice=VoiceMetrics(
            samples=10,
            p95_ms=1400.0,
            calibration_verdict="PASS",
            estimated_timestamps_used=True,
        ),
    )
    assert any("ts_estimated" in gap for gap in report.integrity_gaps())


# --------------------------------------------------------------------------- #
# Failures and self-audit
# --------------------------------------------------------------------------- #


def test_a_failure_record_requires_evidence() -> None:
    with pytest.raises(ValueError):
        FailureRecord(scenario_id="a", contract="decision_vs_action", evidence="")


def test_failures_are_rendered_with_their_quote_and_location() -> None:
    markdown = _report().to_markdown()
    assert "booking/large_party [GreeterAgent -> BookingAgent]" in markdown
    assert "> agent said \"you're all booked for six\" but create_booking" in markdown
    assert "trace `fixtures/large_party.jsonl`" in markdown


def test_the_report_audits_its_own_gaps() -> None:
    report = RunReport(
        stability=[
            # A failure with no quote: untriageable, and reported as such.
            verdict_from_outcomes(
                "booking/undocumented",
                [RunOutcome(index=0, passed=True), RunOutcome(index=1, passed=False)],
            ),
            # k=1: no instability could possibly have been observed.
            verdict_from_outcomes("booking/once", [RunOutcome(index=0, passed=True)]),
        ],
        contracts=[
            # Never evaluated: neither passing nor failing.
            ContractStat(name="field_propagation", failures=0, runs=0)
        ],
    )
    gaps = report.integrity_gaps()
    joined = " ".join(gaps)
    assert "no evidence recorded" in joined
    assert "1/2 (50.0%) scenarios ran once (k=1)" in joined
    assert "1/1 (100.0%) contracts were never evaluated" in joined
    assert "## Report integrity" in report.to_markdown()


def test_live_judges_are_reported_as_a_reproducibility_gap() -> None:
    report = RunReport(
        stability=[_verdict("a", "PP")],
        judges=[
            JudgeSummary(
                name="tone",
                model="gpt-4o-mini",
                calibration=_calibration(),
                judged=2,
                flagged=0,
                replayed_from_fixture=False,
            )
        ],
    )
    assert any("not reproducible offline" in gap for gap in report.integrity_gaps())


def test_an_unexercised_contract_does_not_count_as_passing() -> None:
    contract = ContractStat(name="field_propagation", failures=0, runs=0)
    assert contract.passed is False
    assert contract.failure_rate_str == "0/0 (no runs)"


def test_a_contract_that_never_applied_is_skipped_not_passing() -> None:
    # A propagation contract on twelve traces that contained no handoff asserted
    # nothing twelve times. Counting that as a pass is how a suite rots green.
    contract = ContractStat(name="field_propagation", failures=0, runs=12, vacuous=12)
    assert contract.applicable == 0
    assert contract.passed is False
    assert contract.failure_rate_str == "0/0 (no runs)"

    report = RunReport(stability=[_verdict("a", "PP")], contracts=[contract])
    assert any("had nothing to assert on in any run" in gap for gap in report.integrity_gaps())


def test_a_failure_rate_is_quoted_over_the_runs_where_the_contract_applied() -> None:
    contract = ContractStat(name="no_re_ask", failures=2, runs=10, vacuous=4)
    assert contract.applicable == 6
    assert contract.failure_rate_str == "2/6 (33.3%)"

    report = RunReport(stability=[_verdict("a", "PP")], contracts=[contract])
    assert any("vacuous on 4/10 (40.0%) runs" in gap for gap in report.integrity_gaps())


def test_contract_failures_cannot_exceed_the_runs_it_applied_to() -> None:
    with pytest.raises(ValueError, match="failures out of"):
        ContractStat(name="x", failures=3, runs=2)
    with pytest.raises(ValueError, match="failures out of 1 applicable"):
        ContractStat(name="x", failures=2, runs=5, vacuous=4)
    with pytest.raises(ValueError, match="vacuous out of"):
        ContractStat(name="x", failures=0, runs=2, vacuous=3)


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def test_json_carries_both_the_counts_and_the_rendered_rates() -> None:
    document = json.loads(_report().to_json())
    assert document["verdict"] == "FAIL"
    flaky = next(s for s in document["stability"] if s["scenario_id"] == "modification/party_size")
    assert flaky["passes"] == 3 and flaky["total_runs"] == 5
    assert flaky["pass_rate"] == "3/5 (60.0%)"
    assert flaky["passed"] is False
    assert document["judges"][0]["tpr"] == "18/20 (90.0%)"
    assert document["voice"]["trustworthy"] is True
    assert document["integrity_gaps"] == []


def test_rendering_is_deterministic_so_a_committed_report_diffs_cleanly() -> None:
    first, second = _report(), _report()
    assert first.to_markdown() == second.to_markdown()
    assert first.to_json() == second.to_json()


def test_write_produces_both_formats(tmp_path) -> None:
    paths = _report().write(tmp_path, stem="run_report")
    assert paths["markdown"].read_text(encoding="utf-8").startswith("# TableMate evaluation")
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["subject"] == "tablemate 0.1.0"


def test_an_empty_suite_renders_without_pretending_it_ran(tmp_path) -> None:
    markdown = RunReport().to_markdown()
    assert "_No scenarios were run._" in markdown
    assert "_No deterministic contracts were evaluated._" in markdown
    assert "_No model-graded checks were run._" in markdown
    assert "Verdict: FAIL" in markdown
