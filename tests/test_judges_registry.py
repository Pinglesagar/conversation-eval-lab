"""Tests for the gate: does an unmeasured judge actually get stopped?

WHAT THIS DEMONSTRATES
----------------------
The gate is only worth having if it fires. These tests pin the four cases that
matter — no calibration, below threshold, override, and CI detection — and, just
as importantly, they pin what the error *says*: a gate failure that does not name
the number and the threshold sends someone to read the source instead of fixing
the judge.
"""

from __future__ import annotations

import logging

import pytest

from lab.clock import FakeClock
from lab.judges.calibration import (
    CalibrationThresholds,
    LabelledTrace,
    calibrate,
)
from lab.judges.judge import Judge, JudgeError, Label, ScriptedCompletion
from lab.judges.registry import (
    JudgeBelowThresholdError,
    JudgeRegistry,
    UncalibratedJudgeError,
    in_ci_mode,
)
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

PROMPT = "Grade this.\n{{transcript}}\nAnswer PASS or FAIL."


@pytest.fixture(autouse=True)
def _no_ambient_ci(monkeypatch) -> None:
    """CI detection reads the environment, so the environment must be pinned.

    Without this the suite would pass or fail depending on whether it happens to
    be running in CI, which is the one thing a test of CI behaviour must not do.
    """
    monkeypatch.delenv("LAB_JUDGE_CI", raising=False)
    monkeypatch.delenv("CI", raising=False)


def _trace(item_id: str) -> Trace:
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id="booking/generic", adapter="text", session_id=item_id, clock=clock
    )
    builder.session_start()
    clock.advance(0.5)
    builder.agent_utterance("Let me look.", agent="BookingAgent")
    return builder.build()


def _items(n_fail: int, n_pass: int) -> list[LabelledTrace]:
    items = [
        LabelledTrace(item_id=f"f{i}", label="fail", trace=_trace(f"f{i}"), note="claim")
        for i in range(n_fail)
    ]
    items += [
        LabelledTrace(item_id=f"p{i}", label="pass", trace=_trace(f"p{i}"), note="clean")
        for i in range(n_pass)
    ]
    return items


def _judge(answers: dict[str, Label], *, name: str = "test_judge") -> Judge:
    raw = {
        item_id: ("PASS. clean" if label == "pass" else "FAIL. claimed")
        for item_id, label in answers.items()
    }
    return Judge(
        name=name,
        prompt=PROMPT,
        version="v1",
        model="test/stub",
        completion=ScriptedCompletion(raw),
    )


def _good_judge() -> tuple[Judge, list[LabelledTrace]]:
    """A judge that agrees with every one of 20 labels — TPR and TNR both 1.000."""
    items = _items(8, 12)
    judge = _judge({item.item_id: item.label for item in items})
    return judge, items


def _sloppy_judge() -> tuple[Judge, list[LabelledTrace]]:
    """Perfect recall, poor specificity: TPR 8/8, TNR 6/12 = 0.500.

    The realistic failure. It finds every defect and cries wolf half the time,
    which is exactly the judge a TPR-only gate would wave through.
    """
    items = _items(8, 12)
    answers: dict[str, Label] = {item.item_id: item.label for item in items}
    for i in range(6):
        answers[f"p{i}"] = "fail"
    return _judge(answers), items


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_uncalibrated_judge_raises_in_ci() -> None:
    """The failure mode this module exists for: a judge nobody ever measured."""
    registry = JudgeRegistry()
    judge = registry.register(_judge({}))
    with pytest.raises(UncalibratedJudgeError) as exc:
        registry.require_calibrated(judge, ci=True)
    assert "has no calibration" in str(exc.value)
    assert "test_judge" in str(exc.value)


def test_below_threshold_judge_raises_a_different_error() -> None:
    """Two distinct errors, because they call for two different responses.

    An uncalibrated judge needs a labelled set. A below-threshold judge already
    has one and needs a better prompt — and its report already lists the items to
    read.
    """
    registry = JudgeRegistry()
    judge, items = _sloppy_judge()
    report = calibrate(judge, items)
    registry.register(judge)

    with pytest.raises(JudgeBelowThresholdError) as exc:
        registry.require_calibrated(judge, ci=True)
    message = str(exc.value)
    assert "TNR 0.500 (6/12)" in message  # the number, with its fraction
    assert "0.85" in message  # the threshold it missed
    assert "disagreement" in message  # where to look next
    assert report.true_positive_rate.value == pytest.approx(1.0)


def test_a_calibrated_judge_passes_and_returns_its_report() -> None:
    registry = JudgeRegistry()
    judge, items = _good_judge()
    report = calibrate(judge, items)
    registry.register(judge)
    assert registry.require_calibrated(judge, ci=True) is report


def test_thresholds_are_configurable_and_printed() -> None:
    registry = JudgeRegistry(thresholds=CalibrationThresholds(min_tnr=0.4))
    judge, items = _sloppy_judge()
    calibrate(judge, items)
    registry.register(judge)
    # TNR 0.500 clears a 0.40 bar.
    assert registry.require_calibrated(judge, ci=True) is not None
    assert "TNR >= 0.40" in registry.thresholds.describe()


def test_small_label_sets_do_not_count_as_calibrated() -> None:
    """5/5 and 40/40 both print 1.000; only one of them survives a relabel."""
    registry = JudgeRegistry()
    items = _items(2, 3)
    judge = _judge({item.item_id: item.label for item in items})
    calibrate(judge, items)
    registry.register(judge)
    with pytest.raises(JudgeBelowThresholdError, match="only 5 items"):
        registry.require_calibrated(judge, ci=True)


# --------------------------------------------------------------------------- #
# The override
# --------------------------------------------------------------------------- #


def test_override_lets_it_through_and_shouts_about_it(caplog) -> None:
    """Bypassing the gate is legitimate and must be impossible to do quietly.

    It has to be written at the call site — so a reviewer sees it in the diff —
    and it logs a banner naming the judge, so whoever reads the log sees it too.
    An override settable from config becomes permanent within a month.
    """
    registry = JudgeRegistry()
    judge = registry.register(_judge({}))
    with caplog.at_level(logging.WARNING, logger="lab.judges.registry"):
        assert registry.require_calibrated(judge, ci=True, allow_uncalibrated=True) is None
    logged = caplog.text
    assert "UNCALIBRATED JUDGE ALLOWED THROUGH THE GATE" in logged
    assert "test_judge" in logged
    assert "allow_uncalibrated=True" in logged


def test_override_also_covers_a_below_threshold_judge(caplog) -> None:
    registry = JudgeRegistry()
    judge, items = _sloppy_judge()
    calibrate(judge, items)
    registry.register(judge)
    with caplog.at_level(logging.WARNING, logger="lab.judges.registry"):
        report = registry.require_calibrated(judge, ci=True, allow_uncalibrated=True)
    assert report is not None
    assert "TNR 0.500 (6/12)" in caplog.text


# --------------------------------------------------------------------------- #
# CI detection
# --------------------------------------------------------------------------- #


def test_outside_ci_the_gate_advises_instead_of_raising(caplog) -> None:
    """Interactive work has a human reading the output; automation does not.

    The asymmetry is deliberate, and so is its direction: the strict behaviour is
    the one that runs unattended.
    """
    registry = JudgeRegistry()
    judge = registry.register(_judge({}))
    with caplog.at_level(logging.WARNING, logger="lab.judges.registry"):
        assert registry.require_calibrated(judge, ci=False) is None
    assert "not enforced" in caplog.text


def test_ci_is_detected_from_the_environment(monkeypatch) -> None:
    assert in_ci_mode() is False
    monkeypatch.setenv("CI", "true")
    assert in_ci_mode() is True
    monkeypatch.setenv("LAB_JUDGE_CI", "0")
    # LAB_JUDGE_CI is checked first, so a job can opt one step out of strictness
    # without unsetting the CI flag half the tooling in the world reads.
    assert in_ci_mode() is False


def test_env_detection_is_used_when_ci_is_not_passed(monkeypatch) -> None:
    registry = JudgeRegistry()
    judge = registry.register(_judge({}))
    monkeypatch.setenv("CI", "1")
    with pytest.raises(UncalibratedJudgeError):
        registry.require_calibrated(judge)


# --------------------------------------------------------------------------- #
# Registry mechanics
# --------------------------------------------------------------------------- #


def test_registry_lookup_by_name_and_a_helpful_miss() -> None:
    registry = JudgeRegistry()
    registry.register(_judge({}, name="alpha"))
    assert registry.get("alpha").name == "alpha"
    assert registry.names() == ["alpha"]
    assert "alpha" in registry
    with pytest.raises(KeyError, match="alpha"):
        registry.get("beta")


def test_require_calibrated_accepts_a_name() -> None:
    registry = JudgeRegistry()
    judge, items = _good_judge()
    calibrate(judge, items)
    registry.register(judge)
    assert registry.require_calibrated("test_judge", ci=True) is not None


def test_register_refuses_a_foreign_calibration() -> None:
    """Registration cannot launder one judge's numbers onto another."""
    judge_a, items = _good_judge()
    report = calibrate(judge_a, items)
    registry = JudgeRegistry()
    with pytest.raises(JudgeError, match="refusing to attach"):
        registry.register(_judge({}, name="other"), calibration=report)


def test_module_level_helpers_use_the_default_registry() -> None:
    """`require_calibrated(judge)` is how pipeline code is meant to read."""
    from lab.judges import registry as registry_module

    judge, items = _good_judge()
    calibrate(judge, items)
    registry_module.register(judge)
    try:
        assert registry_module.get("test_judge") is judge
        assert registry_module.require_calibrated("test_judge", ci=True) is judge.calibration
    finally:
        registry_module.DEFAULT_REGISTRY._judges.pop("test_judge", None)


def test_audit_reports_every_judge_without_raising() -> None:
    """"Which of our judges are actually trustworthy" is worth being able to ask."""
    registry = JudgeRegistry()
    good, items = _good_judge()
    calibrate(good, items)
    registry.register(good)
    registry.register(_judge({}, name="never_measured"))

    rows = {row.name: row for row in registry.audit()}
    assert rows["test_judge"].ok is True
    assert rows["never_measured"].ok is False
    assert rows["never_measured"].calibrated is False
    assert rows["never_measured"].failures == ["never calibrated"]

    table = registry.status_table()
    assert "[PASS] test_judge" in table
    assert "[FAIL] never_measured" in table


# --------------------------------------------------------------------------- #
# The gate against the real study's real numbers
# --------------------------------------------------------------------------- #


def test_the_gate_refuses_the_real_v1_and_admits_the_real_v2() -> None:
    """The gate is checked against measured numbers, not constructed ones.

    Everything above builds a judge whose behaviour the test chose. This one uses
    the committed calibration of `hallucinated_confirmation` — captured from a live
    model — because a gate that only ever refuses hand-made counterexamples has not
    been shown to refuse anything real.
    """
    from lab.judges import hallucinated_confirmation as story

    items = story.labels()
    v1 = story.judge_v1()
    v2 = story.judge_v2()
    calibrate(v1, items)
    calibrate(v2, items)

    registry = JudgeRegistry()
    registry.register(v1)

    with pytest.raises(JudgeBelowThresholdError) as excinfo:
        registry.require_calibrated(v1, ci=True)

    message = str(excinfo.value)
    assert "TPR 0.250 (2/8) is below the required 0.85" in message
    assert "TPR >= 0.85, TNR >= 0.85" in message, "the thresholds must be printed"
    assert "6 disagreement(s) to read" in message

    registry.register(v2)
    assert registry.require_calibrated(v2, ci=True) is v2.calibration


def test_the_real_v1_would_have_passed_a_gate_on_the_wrong_rate() -> None:
    """Why the gate insists on both rates, shown on a real judge.

    v1's specificity is a perfect 1.000 and its raw agreement is a respectable
    0.750. A gate configured on either of those alone admits a judge that misses
    three defects in four.
    """
    from lab.judges import hallucinated_confirmation as story

    v1 = story.judge_v1()
    report = calibrate(v1, story.labels())

    assert report.passes(CalibrationThresholds(min_tpr=0.0, min_tnr=0.85)) is True
    assert report.passes(CalibrationThresholds()) is False


def test_thresholds_are_configurable_per_registry_and_named_in_the_refusal() -> None:
    """A threshold is a policy, so it is a parameter — and it is printed."""
    from lab.judges import hallucinated_confirmation as story

    v2 = story.judge_v2()
    calibrate(v2, story.labels())

    strict = JudgeRegistry(thresholds=CalibrationThresholds(min_items=100))
    strict.register(v2)
    with pytest.raises(JudgeBelowThresholdError) as excinfo:
        strict.require_calibrated(v2, ci=True)
    assert "n >= 100" in str(excinfo.value)
    assert "calibrated on only 24 items" in str(excinfo.value)

    # ...and a per-call override wins over the registry's own policy.
    assert (
        strict.require_calibrated(v2, thresholds=CalibrationThresholds(), ci=True)
        is v2.calibration
    )
