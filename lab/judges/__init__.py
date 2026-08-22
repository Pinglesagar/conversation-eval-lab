"""Model-graded checks, with calibration as the price of admission.

WHAT THIS DEMONSTRATES
----------------------
Judges are for the residue: the properties no assertion over a trace can
express — whether a sentence claimed something as accomplished fact, whether a
refusal was appropriate, whether an answer was responsive. They are also the
least trustworthy component in any harness, because a judge is itself a model,
with its own error rate, and that error rate is invisible unless somebody
measures it.

So this package is arranged around the measurement rather than around the judge:

    lab.judges.judge         a versioned prompt -> binary verdict + critique.
                             Runs through litellm, or replays from a recording so
                             tests need no API key.
    lab.judges.calibration   agreement against hand-labelled traces: confusion
                             matrix, TPR, TNR, precision, recall, F1, raw
                             agreement, Cohen's kappa, and every disagreement
                             listed for a human to read.
    lab.judges.registry      the gate. `require_calibrated()` raises in CI when a
                             judge has no calibration or falls below thresholds.
                             An uncalibrated judge silently gating a build is the
                             failure mode this package exists to prevent.

    lab.judges.hallucinated_confirmation
                             a worked iteration: prompt v1, prompt v2, 24 human
                             labels, and both calibration reports, so the
                             improvement is a number rather than a claim.

Three conventions run through all of it, and each one is a mistake declined:

*   **Binary verdicts, not 1-5 scores.** Scales manufacture disagreement between
    graders who actually agree, and no honest true-positive rate can be computed
    against them without a threshold chosen after the fact. Nuance goes in the
    critique, where a human can read it.
*   **Raw agreement is always printed next to Cohen's kappa.** Raw agreement
    flatters hardest on the imbalanced data that real evaluation sets consist of:
    a judge that answers "no defect" every time scores 0.90 on a set with 10%
    defects. Kappa is 0.000 for that judge; that is what chance correction buys.
*   **Every rate carries its numerator and denominator.** "TNR 15/16" tells you
    the measurement rests on sixteen items. "TNR 0.94" invites a confidence the
    sample size has not earned.

Nothing here needs an API key. Live calls are opt-in behind `LAB_LIVE_JUDGE`, and
every judged path in the repo ships a recorded fixture that replays through the
identical code — same prompt rendering, same parser, same verdict construction —
so the offline suite exercises the real path rather than a mock of it.

The re-exports below resolve lazily (PEP 562), matching `lab.voice`: importing a
submodule eagerly from a package `__init__` puts it in `sys.modules` before
`python -m lab.judges.hallucinated_confirmation` can execute it, which makes
`runpy` warn about a double import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers only, never at runtime
    from lab.judges.calibration import (  # noqa: F401
        CalibrationReport,
        CalibrationThresholds,
        ConfusionMatrix,
        Disagreement,
        LabelledTrace,
        Rate,
        calibrate,
        compare_reports,
        load_labels,
        write_labels,
    )
    from lab.judges.judge import (  # noqa: F401
        Judge,
        JudgeParseError,
        PromptTemplate,
        Recording,
        ReplayJudge,
        ScriptedCompletion,
        Verdict,
        record_verdicts,
    )
    from lab.judges.registry import (  # noqa: F401
        JudgeBelowThresholdError,
        JudgeRegistry,
        UncalibratedJudgeError,
        require_calibrated,
    )

_LAZY: dict[str, str] = {
    # judge.py
    "Judge": "lab.judges.judge",
    "JudgeParseError": "lab.judges.judge",
    "PromptTemplate": "lab.judges.judge",
    "Recording": "lab.judges.judge",
    "ReplayJudge": "lab.judges.judge",
    "ScriptedCompletion": "lab.judges.judge",
    "Verdict": "lab.judges.judge",
    "record_verdicts": "lab.judges.judge",
    # calibration.py
    "CalibrationReport": "lab.judges.calibration",
    "CalibrationThresholds": "lab.judges.calibration",
    "ConfusionMatrix": "lab.judges.calibration",
    "Disagreement": "lab.judges.calibration",
    "LabelledTrace": "lab.judges.calibration",
    "Rate": "lab.judges.calibration",
    "calibrate": "lab.judges.calibration",
    "compare_reports": "lab.judges.calibration",
    "load_labels": "lab.judges.calibration",
    "write_labels": "lab.judges.calibration",
    # registry.py
    "JudgeBelowThresholdError": "lab.judges.registry",
    "JudgeRegistry": "lab.judges.registry",
    "UncalibratedJudgeError": "lab.judges.registry",
    "require_calibrated": "lab.judges.registry",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name on first access."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
