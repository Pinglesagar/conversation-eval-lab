"""The gates: a judge must be measured, and it must not be grading itself.

WHAT THIS DEMONSTRATES
----------------------
The failure mode this module exists to prevent is specific, common, and quiet:

    someone writes a judge, wires it into the pipeline, and it starts turning
    builds red and green. Nobody ever measured it against a human label. Six
    weeks later a real regression ships because the judge had a 40% miss rate on
    that class of failure, and nobody can say when it started, because there was
    never a number to regress from.

Nothing about that goes wrong loudly. The judge produces verdicts; the verdicts
look like data; the dashboard is green. So the check has to be structural: a
judge reaching a gate must be able to produce a `CalibrationReport` that clears
stated thresholds, or `require_calibrated()` raises and the pipeline stops.

    require_calibrated(judge)      # raises unless TPR >= 0.85 and TNR >= 0.85

**The override is deliberately ugly.** `allow_uncalibrated=True` is the only way
past the gate, it must be written at the call site, and it logs a warning that is
hard to miss in a CI log. That combination is the point: bypassing the gate is
sometimes legitimate (a judge under development, a one-off exploratory run), and
it should always be visible in the diff to whoever reviews it and in the log to
whoever reads it. An override that can be set from a config file or an
environment variable becomes permanent within a month, and nobody remembers
turning it on.

CI MODE
-------
In CI the gate raises. Interactively it logs a loud warning and returns, because
a researcher exploring a new prompt should not have to satisfy a gate to see a
verdict — the gate protects automation, and interactive work has a human reading
the output. CI is detected from the environment (`LAB_JUDGE_CI`, else the
conventional `CI`), and can be forced either way with `ci=`.

The asymmetry is intentional, and so is its direction: the strict behaviour is the
one that runs unattended.

THE SECOND GATE: THE JUDGE IS NOT THE THING IT GRADES
-----------------------------------------------------
`require_calibrated` answers "is this instrument any good". It cannot answer "is
this instrument pointed at itself", and that is a separate way for a green
dashboard to mean nothing:

    the agent is a model, the judge is a model, both routes are read from the
    environment, and on a laptop with one provider configured they are the same
    variable's worth of the same deployment. Self-enhancement bias — a model
    scoring text it wrote above equivalent text it did not — then inflates every
    verdict, in a known direction, and the judge's calibration figure does not
    describe it, because that figure was measured on somebody else's text.

    require_independent_judge(judge_route=..., subject_route=...)   # raises

Two things differ from the calibration gate, both argued in the function's
docstring: this one refuses in and out of CI, and its bypass is a keyword
argument with no environment or config equivalent. The check is a *configuration*
check — it costs nothing, needs no credential, and it is run before the first
paid call rather than after the report is written.

WHY THRESHOLDS AND NOT A SCORE
------------------------------
`CalibrationThresholds` (defined in `lab.judges.calibration`) gates on TPR **and**
TNR, both defaulting to 0.85, plus a minimum item count and a zero tolerance for
unparseable judge output. Gating on a single blended score would let a judge trade
away the property that matters: a defect detector with 0.99 TNR and 0.20 TPR has a
respectable-looking average and misses four fifths of the defects. The two rates
are reported and gated separately because they fail for different reasons and are
fixed by different prompt changes.
"""

from __future__ import annotations

import logging
import os
from typing import Iterator

from pydantic import BaseModel, ConfigDict

from lab.judges.calibration import CalibrationReport, CalibrationThresholds
from lab.judges.judge import Judge

__all__ = [
    "CI_ENV_VARS",
    "CalibrationGateError",
    "UncalibratedJudgeError",
    "JudgeBelowThresholdError",
    "SelfGradingError",
    "JudgeStatus",
    "JudgeRegistry",
    "DEFAULT_REGISTRY",
    "in_ci_mode",
    "normalise_route",
    "self_grading_conflict",
    "require_independent_judge",
    "register",
    "get",
    "require_calibrated",
]

LOGGER = logging.getLogger("lab.judges.registry")

#: Checked in order; the first one that is set decides. `LAB_JUDGE_CI` exists so
#: a developer can rehearse the strict behaviour locally, and so a CI job can
#: switch it off for one step without unsetting the conventional `CI` flag that
#: half the tooling in the world reads.
CI_ENV_VARS: tuple[str, ...] = ("LAB_JUDGE_CI", "CI")

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSEY = frozenset({"0", "false", "no", "off", ""})


def in_ci_mode() -> bool:
    """True when the environment says this is an unattended run."""
    for name in CI_ENV_VARS:
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = raw.strip().lower()
        if value in _TRUTHY:
            return True
        if value in _FALSEY:
            return False
    return False


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class CalibrationGateError(RuntimeError):
    """Base class for gate refusals."""


class UncalibratedJudgeError(CalibrationGateError):
    """The judge has never been measured against human labels."""


class JudgeBelowThresholdError(CalibrationGateError):
    """The judge has been measured and does not agree with humans well enough.

    Distinct from `UncalibratedJudgeError` on purpose: they call for different
    responses. An uncalibrated judge needs a labelled set; a below-threshold judge
    already has one, and needs a better prompt — its report's disagreement list
    says which items to look at first.
    """


class SelfGradingError(CalibrationGateError):
    """The judge and the system it grades resolve to the same model route.

    Its own gate rather than a variant of the two above, because the fix is
    different in kind. An uncalibrated judge needs labels; a below-threshold judge
    needs a prompt. A self-grading judge needs a *different model*, and no amount
    of labelling or prompting removes the problem — the measurement is biased in a
    known direction before the first item is read.
    """


# --------------------------------------------------------------------------- #
# Independence: the judge must not be the thing it is grading
# --------------------------------------------------------------------------- #


def normalise_route(route: str | None) -> str:
    """A model route reduced to the form two routes are compared in.

    Case and surrounding whitespace only. Nothing clever: `azure/gpt-4.1` and
    `openai/gpt-4.1` stay *different* here even though they may well be the same
    weights, because the harness cannot know that and a guard that guessed would
    refuse legitimate configurations. See `self_grading_conflict` for what that
    limitation costs and why it is stated rather than papered over.
    """
    return (route or "").strip().lower()


def self_grading_conflict(
    *,
    judge_route: str | None,
    subject_route: str | None,
    judge_role: str = "judge",
    subject_role: str = "agent under test",
    judge_env_var: str = "LAB_JUDGE_MODEL",
) -> str | None:
    """The refusal message when one model would grade its own output, else None.

    Separated from the raising gate so a caller that collects every reason a run
    may not proceed — `lab.cli._live_refusals` does exactly that — can report this
    one alongside the others instead of dying on the first.

    WHAT IS BEING REFUSED, AND WHY IT IS NOT A STYLE OBJECTION
    ---------------------------------------------------------
    Self-enhancement bias is the measured tendency of a model to score text it
    produced above equivalent text it did not. It is not removed by temperature 0,
    by a careful rubric, or by hiding the provenance, because the preference is in
    the weights that wrote both the answer and the grade. A calibration measured
    in that configuration is a measurement of a judge grading a stranger, quoted
    over verdicts where it was grading itself, and the difference between those
    two numbers is exactly the quantity nobody has.

    WHAT THIS DOES NOT CATCH, STATED RATHER THAN IMPLIED
    ---------------------------------------------------
    Route equality is the only fact available here. Two different routes may point
    at the same weights — a private deployment name reveals nothing about the
    model behind it — and this returns None for that case. So a passing check is
    "the two routes are not written the same way", which is weaker than
    independence and is all a configuration check can honestly claim. The strong
    version of this property needs the panel of judges, and that needs three
    credentials at record time.
    """
    judge = normalise_route(judge_route)
    subject = normalise_route(subject_route)
    if not judge or not subject or judge != subject:
        return None
    # The route itself is deliberately not quoted back. It can name a private
    # deployment inside somebody's cloud account, this message reaches stderr and
    # therefore a CI log, and naming the two roles is exactly as actionable when
    # the comparison only ever has two sides.
    return (
        f"the {judge_role} and the {subject_role} are routed to the same model, "
        "so the model would be grading its own output. "
        "Self-enhancement bias — a model scoring its own text above equivalent "
        "text it did not write — is directional and is not removed by "
        "temperature 0 or by a better rubric, so verdicts recorded in this "
        "configuration are not evidence about the agent, and the judge's "
        "calibration (measured against human labels, on somebody else's text) "
        f"does not describe them. Point {judge_env_var} at a different model, or "
        "pass allow_self_grading=True at the call site to record anyway and say "
        "so in the report."
    )


def require_independent_judge(
    *,
    judge_route: str | None,
    subject_route: str | None,
    judge_role: str = "judge",
    subject_role: str = "agent under test",
    judge_env_var: str = "LAB_JUDGE_MODEL",
    allow_self_grading: bool = False,
) -> None:
    """Refuse to grade a model's output with that same model.

    Args:
        judge_route: The litellm route the grader will call.
        subject_route: The litellm route that produced the text being graded.
        judge_role: What to call the grader in the message ("judge", "scorer").
        subject_role: What to call the graded side ("agent under test", "trainee").
        judge_env_var: The variable a reader should change, named in the message.
        allow_self_grading: Bypass. Logs the same loud warning
            `allow_uncalibrated` does, for the same reason.

    Raises:
        SelfGradingError: the two routes are the same and the bypass was not
            passed.

    TWO DIFFERENCES FROM `require_calibrated`, BOTH DELIBERATE
    ----------------------------------------------------------
    **There is no CI/interactive split.** `require_calibrated` advises outside CI
    because a researcher exploring a new prompt has a human reading the output and
    the gate is protecting automation. That argument does not transfer: an
    uncalibrated judge produces numbers of *unknown* quality, which a human can
    discount, and a self-grading judge produces numbers that are wrong in a
    *known* direction, which reads exactly like a good result. So this one refuses
    everywhere.

    **The bypass is a keyword argument and nothing else.** No environment
    variable, no config key, no command-line flag — the same rule
    `require_calibrated` follows, for the reason its docstring gives: an override
    that can be set outside the source becomes permanent within a month and nobody
    remembers turning it on. A flag would put it in a shell history; a keyword
    argument puts it in a diff.
    """
    conflict = self_grading_conflict(
        judge_route=judge_route,
        subject_route=subject_route,
        judge_role=judge_role,
        subject_role=subject_role,
        judge_env_var=judge_env_var,
    )
    if conflict is None:
        return
    if allow_self_grading:
        _warn_self_grading(conflict)
        return
    raise SelfGradingError(conflict)


def _warn_self_grading(message: str) -> None:
    """Log the bypass so loudly that it shows up in a skim of the log."""
    LOGGER.warning(
        "\n"
        "!!! ================================================================ !!!\n"
        "!!! A MODEL IS GRADING ITS OWN OUTPUT\n"
        "!!! reason  : %s\n"
        "!!! override: allow_self_grading=True was passed at the call site.\n"
        "!!! Every verdict from this run carries self-enhancement bias in a\n"
        "!!! known direction, and no calibration figure in this repository\n"
        "!!! measures it.\n"
        "!!! ================================================================ !!!",
        message,
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class JudgeStatus(BaseModel):
    """One row of a registry audit: is this judge fit to gate anything?"""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    calibrated: bool
    ok: bool
    summary: str | None = None
    failures: list[str] = []


class JudgeRegistry:
    """Named judges, each carrying its latest calibration.

    A registry rather than module-level globals so a test can build its own and
    not fight whatever the rest of the process registered. `DEFAULT_REGISTRY`
    exists for applications, which do want one shared answer to "which judges does
    this pipeline run".

    Keyed by judge name, not by (name, version): a judge is a *question*, and the
    prompt version is an implementation of it. Registering v2 replaces v1, which
    is the intended behaviour — two versions of the same question live in a
    registry only during an experiment, and then the experiment picks one.
    """

    def __init__(self, *, thresholds: CalibrationThresholds | None = None) -> None:
        self._judges: dict[str, Judge] = {}
        self.thresholds = thresholds if thresholds is not None else CalibrationThresholds()

    # ------------------------------------------------------------ membership

    def register(
        self, judge: Judge, *, calibration: CalibrationReport | None = None
    ) -> Judge:
        """Add or replace a judge. Returns it, so registration can wrap a build.

        Passing `calibration=` attaches the report through
        `Judge.attach_calibration`, which refuses a report belonging to a
        different judge or prompt version.
        """
        if calibration is not None:
            judge.attach_calibration(calibration)
        self._judges[judge.name] = judge
        return judge

    def get(self, name: str) -> Judge:
        try:
            return self._judges[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._judges)) or "(none registered)"
            raise KeyError(f"no judge named {name!r}. Registered: {known}") from exc

    def names(self) -> list[str]:
        return sorted(self._judges)

    def calibration_for(self, name: str) -> CalibrationReport | None:
        return self.get(name).calibration

    def __contains__(self, name: object) -> bool:
        return name in self._judges

    def __len__(self) -> int:
        return len(self._judges)

    def __iter__(self) -> Iterator[Judge]:
        return iter(self._judges[name] for name in self.names())

    # ------------------------------------------------------------------ gate

    def require_calibrated(
        self,
        judge: Judge | str,
        *,
        thresholds: CalibrationThresholds | None = None,
        ci: bool | None = None,
        allow_uncalibrated: bool = False,
    ) -> CalibrationReport | None:
        """Refuse to proceed with a judge whose agreement is unknown or too low.

        Args:
            judge: A `Judge`, or the name of one registered here.
            thresholds: Defaults to this registry's thresholds.
            ci: Force strict (True) or advisory (False) behaviour. Defaults to
                `in_ci_mode()`.
            allow_uncalibrated: Bypass the gate. Logs a warning naming the judge
                and the specific numbers that were tolerated, so the override
                leaves a trail in the log as well as in the diff.

        Returns:
            The judge's `CalibrationReport`, or None when it has none and the
            gate was bypassed.

        Raises:
            UncalibratedJudgeError: no calibration attached (CI mode).
            JudgeBelowThresholdError: calibrated, below thresholds (CI mode).
        """
        resolved = judge if isinstance(judge, Judge) else self.get(judge)
        thr = thresholds if thresholds is not None else self.thresholds
        strict = in_ci_mode() if ci is None else ci
        report = resolved.calibration

        if report is None:
            message = (
                f"judge {resolved.name!r} ({resolved.version}) has no calibration: its "
                f"agreement with human labels has never been measured, so its verdicts "
                f"cannot be interpreted. Build a labelled set and run "
                f"lab.judges.calibration.calibrate()."
            )
            if allow_uncalibrated:
                _warn_override(resolved, message)
                return None
            if strict:
                raise UncalibratedJudgeError(message)
            _warn_advisory(resolved, message)
            return None

        ok, failures = report.meets(thr)
        if ok:
            LOGGER.debug(
                "judge %r %s cleared the calibration gate: %s",
                resolved.name,
                resolved.version,
                report.summary_line(),
            )
            return report

        detail = "; ".join(failures)
        message = (
            f"judge {resolved.name!r} ({resolved.version}) is below the calibration "
            f"thresholds ({thr.describe()}): {detail}. Measured: "
            f"{report.summary_line()}. Its report lists "
            f"{len(report.disagreements)} disagreement(s) to read."
        )
        if allow_uncalibrated:
            _warn_override(resolved, message)
            return report
        if strict:
            raise JudgeBelowThresholdError(message)
        _warn_advisory(resolved, message)
        return report

    # ----------------------------------------------------------------- audit

    def audit(self, thresholds: CalibrationThresholds | None = None) -> list[JudgeStatus]:
        """Status of every registered judge, without raising.

        For the "which of our judges are actually trustworthy" question, which is
        worth being able to answer on demand rather than one exception at a time.
        """
        thr = thresholds if thresholds is not None else self.thresholds
        rows: list[JudgeStatus] = []
        for judge in self:
            report = judge.calibration
            if report is None:
                rows.append(
                    JudgeStatus(
                        name=judge.name,
                        version=judge.version,
                        calibrated=False,
                        ok=False,
                        failures=["never calibrated"],
                    )
                )
                continue
            ok, failures = report.meets(thr)
            rows.append(
                JudgeStatus(
                    name=judge.name,
                    version=judge.version,
                    calibrated=True,
                    ok=ok,
                    summary=report.summary_line(),
                    failures=failures,
                )
            )
        return rows

    def status_table(self, thresholds: CalibrationThresholds | None = None) -> str:
        """The audit as text, for a terminal or a CI log."""
        rows = self.audit(thresholds)
        thr = thresholds if thresholds is not None else self.thresholds
        lines = [f"Judge calibration gate — thresholds: {thr.describe()}", ""]
        if not rows:
            lines.append("  (no judges registered)")
            return "\n".join(lines)
        for row in rows:
            mark = "PASS" if row.ok else "FAIL"
            lines.append(f"  [{mark}] {row.name} {row.version}")
            if row.summary:
                lines.append(f"         {row.summary}")
            for failure in row.failures:
                lines.append(f"         - {failure}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"JudgeRegistry(judges={self.names()})"


def _warn_override(judge: Judge, message: str) -> None:
    """Log the bypass so loudly that it shows up in a skim of the log."""
    LOGGER.warning(
        "\n"
        "!!! ================================================================ !!!\n"
        "!!! UNCALIBRATED JUDGE ALLOWED THROUGH THE GATE\n"
        "!!! judge   : %s (%s)\n"
        "!!! reason  : %s\n"
        "!!! override: allow_uncalibrated=True was passed at the call site.\n"
        "!!! Results produced by this judge are not evidence about anything until\n"
        "!!! its agreement with human labels has been measured.\n"
        "!!! ================================================================ !!!",
        judge.name,
        judge.version,
        message,
    )


def _warn_advisory(judge: Judge, message: str) -> None:
    """Outside CI the gate advises rather than raising — but it still says so."""
    LOGGER.warning(
        "calibration gate not enforced (not running in CI mode): %s", message
    )


#: The registry an application shares. Tests should build their own.
DEFAULT_REGISTRY = JudgeRegistry()


def register(judge: Judge, *, calibration: CalibrationReport | None = None) -> Judge:
    """Register a judge in `DEFAULT_REGISTRY`."""
    return DEFAULT_REGISTRY.register(judge, calibration=calibration)


def get(name: str) -> Judge:
    """Look a judge up in `DEFAULT_REGISTRY`."""
    return DEFAULT_REGISTRY.get(name)


def require_calibrated(
    judge: Judge | str,
    *,
    thresholds: CalibrationThresholds | None = None,
    ci: bool | None = None,
    allow_uncalibrated: bool = False,
) -> CalibrationReport | None:
    """Gate `judge` against `DEFAULT_REGISTRY`'s thresholds.

    The module-level entry point, so pipeline code reads as
    `require_calibrated(judge)` rather than plumbing a registry through. See
    `JudgeRegistry.require_calibrated` for the semantics.
    """
    return DEFAULT_REGISTRY.require_calibrated(
        judge,
        thresholds=thresholds,
        ci=ci,
        allow_uncalibrated=allow_uncalibrated,
    )
