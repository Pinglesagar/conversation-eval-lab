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
    SelfGradingError,
    UncalibratedJudgeError,
    in_ci_mode,
    normalise_route,
    require_independent_judge,
    self_grading_conflict,
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


# --------------------------------------------------------------------------- #
# The second gate: the judge must not be the thing it grades
#
# Both directions are pinned. A guard that refuses everything is as useless as
# one that refuses nothing, and the failure mode here is specifically that a
# well-meant tightening starts refusing the live-caller configuration
# `lab.simulator.flake_band` depends on.
# --------------------------------------------------------------------------- #


def test_same_route_is_refused_and_the_message_says_why() -> None:
    with pytest.raises(SelfGradingError) as excinfo:
        require_independent_judge(
            judge_route="azure/gpt-4.1", subject_route="azure/gpt-4.1"
        )
    message = str(excinfo.value)
    # The refusal has to carry the reason, not just the verdict: "invalid config"
    # sends the reader to the source, and the reason is the whole finding.
    assert "grading its own output" in message
    assert "self-enhancement bias" in message.lower()
    assert "LAB_JUDGE_MODEL" in message
    assert "allow_self_grading=True" in message
    # The route may name a private deployment, and this message reaches a log.
    assert "azure/gpt-4.1" not in message


def test_different_routes_are_allowed() -> None:
    # The direction that matters as much as the refusal: a guard that fires on a
    # legitimate configuration gets switched off, and then guards nothing.
    require_independent_judge(
        judge_route="azure/gpt-4.1", subject_route="azure/gpt-4o-mini"
    )
    assert (
        self_grading_conflict(
            judge_route="azure/gpt-4.1", subject_route="azure/gpt-4o-mini"
        )
        is None
    )


def test_an_unconfigured_route_is_not_a_conflict() -> None:
    # Two empty routes are equal as strings and are not a self-grading run; the
    # missing-variable case belongs to whoever needs the variable.
    for judge, subject in (("", ""), (None, None), ("azure/x", None), (None, "azure/x")):
        assert (
            self_grading_conflict(judge_route=judge, subject_route=subject) is None
        )


def test_route_comparison_ignores_case_and_padding_only() -> None:
    assert normalise_route("  AZURE/GPT-4.1 ") == "azure/gpt-4.1"
    with pytest.raises(SelfGradingError):
        require_independent_judge(
            judge_route=" AZURE/GPT-4.1", subject_route="azure/gpt-4.1 "
        )
    # Same family, two providers, stays allowed: the harness cannot know whether
    # those are the same weights, and `self_grading_conflict` says so rather than
    # guessing. A passing check means "the routes are written differently", which
    # is weaker than independence and is all a config check can claim.
    require_independent_judge(
        judge_route="openai/gpt-4.1", subject_route="azure/gpt-4.1"
    )


def test_the_override_is_a_call_site_argument_and_it_shouts(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="lab.judges.registry"):
        require_independent_judge(
            judge_route="azure/gpt-4.1",
            subject_route="azure/gpt-4.1",
            allow_self_grading=True,
        )
    assert "A MODEL IS GRADING ITS OWN OUTPUT" in caplog.text
    assert "allow_self_grading=True was passed at the call site" in caplog.text


def test_the_override_has_no_environment_or_config_equivalent(monkeypatch) -> None:
    """The property the docstring claims, asserted rather than asserted-in-prose.

    An override that can be set outside the source becomes permanent within a
    month. So: nothing in the environment may open this gate — including the
    variables that plausibly would, and including CI mode, which is the axis
    `require_calibrated` deliberately does bend on.
    """
    for name in (
        "LAB_ALLOW_SELF_GRADING",
        "ALLOW_SELF_GRADING",
        "LAB_JUDGE_CI",
        "CI",
    ):
        monkeypatch.setenv(name, "1")
    with pytest.raises(SelfGradingError):
        require_independent_judge(
            judge_route="azure/gpt-4.1", subject_route="azure/gpt-4.1"
        )
    for name in ("LAB_JUDGE_CI", "CI"):
        monkeypatch.setenv(name, "0")
    with pytest.raises(SelfGradingError):
        require_independent_judge(
            judge_route="azure/gpt-4.1", subject_route="azure/gpt-4.1"
        )


def test_the_run_command_refuses_to_record_a_self_grading_rig(monkeypatch) -> None:
    from lab import cli

    monkeypatch.setenv("LAB_LIVE_AGENT", "1")
    monkeypatch.setenv("LAB_LIVE_JUDGE", "1")
    monkeypatch.setenv(cli.AGENT_MODEL_ENV_VAR, "azure/gpt-4.1")
    monkeypatch.setenv("LAB_JUDGE_MODEL", "azure/gpt-4.1")

    rig = cli.LiveRig(agent=True, judge=True, record=True)
    refusals = cli._live_refusals(rig)
    assert len(refusals) == 1
    assert refusals[0].startswith("--record refused: ")
    assert "grading its own output" in refusals[0]

    # Different routes: no refusal at all.
    monkeypatch.setenv("LAB_JUDGE_MODEL", "azure/gpt-4o-mini")
    assert cli._live_refusals(rig) == []

    # And the check is a *record*-time check: replaying a committed cassette
    # spends nothing and records nothing, so there is nothing to refuse.
    monkeypatch.setenv("LAB_JUDGE_MODEL", "azure/gpt-4.1")
    assert cli._live_refusals(cli.LiveRig(agent=True, judge=True)) == []


def test_a_live_caller_sharing_the_judges_route_is_not_refused(monkeypatch) -> None:
    """The caller is an input to the verdict, not the thing being graded.

    `lab.simulator.flake_band` runs a live caller against the deterministic agent
    on purpose. Refusing that would break the only configuration that produces a
    clean flake number.
    """
    from lab import cli

    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    monkeypatch.setenv("LAB_LIVE_JUDGE", "1")
    monkeypatch.setenv("LAB_CALLER_MODEL", "azure/gpt-4.1")
    monkeypatch.setenv("LAB_JUDGE_MODEL", "azure/gpt-4.1")
    monkeypatch.delenv(cli.AGENT_MODEL_ENV_VAR, raising=False)

    assert cli._live_refusals(cli.LiveRig(caller=True, judge=True, record=True)) == []


def test_the_cli_and_the_case_study_name_the_same_agent_route_variable() -> None:
    """`lab.cli` mirrors the name rather than importing it; this pins the mirror."""
    from lab import cli
    from tablemate import runtime

    assert cli.AGENT_MODEL_ENV_VAR == runtime.MODEL_ENV_VAR


def test_the_rubric_scorer_refuses_to_grade_its_own_trainee(monkeypatch) -> None:
    from roleplay import live as roleplay_live
    from roleplay import livescorer

    # The mirrored names must still match the module that owns them, or the
    # check reads a variable nobody sets and passes for the wrong reason.
    assert livescorer.TRAINEE_MODEL_ENV_VAR == roleplay_live.TRAINEE_MODEL_ENV_VAR
    assert livescorer.LIVE_TRAINEE_ENV_VAR == roleplay_live.LIVE_TRAINEE_ENV_VAR

    monkeypatch.setenv(livescorer.LIVE_TRAINEE_ENV_VAR, "1")
    monkeypatch.setenv(livescorer.TRAINEE_MODEL_ENV_VAR, "azure/gpt-4.1")
    monkeypatch.setenv(livescorer.MODEL_ENV_VAR, "azure/gpt-4.1")
    with pytest.raises(SelfGradingError) as excinfo:
        livescorer.require_independent_scorer()
    assert "rubric scorer" in str(excinfo.value)
    assert "trainee under assessment" in str(excinfo.value)
    assert livescorer.MODEL_ENV_VAR in str(excinfo.value)

    # Building the live completion goes through the same gate, before any
    # credential is read and before anything is spent.
    with pytest.raises(SelfGradingError):
        livescorer.live_completion()

    # Two routes, no refusal.
    monkeypatch.setenv(livescorer.MODEL_ENV_VAR, "azure/gpt-4o-mini")
    livescorer.require_independent_scorer()
    assert livescorer.live_completion().env_var == livescorer.LIVE_ENV_VAR


def test_a_scripted_trainee_is_never_self_grading(monkeypatch) -> None:
    """One route, no live trainee: nothing the scorer's model wrote is graded."""
    from roleplay import livescorer

    monkeypatch.delenv(livescorer.LIVE_TRAINEE_ENV_VAR, raising=False)
    monkeypatch.setenv(livescorer.TRAINEE_MODEL_ENV_VAR, "azure/gpt-4.1")
    monkeypatch.setenv(livescorer.MODEL_ENV_VAR, "azure/gpt-4.1")
    livescorer.require_independent_scorer()
    assert livescorer.live_completion().env_var == livescorer.LIVE_ENV_VAR
