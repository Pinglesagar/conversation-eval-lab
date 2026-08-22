"""Voice-specific measurement.

WHAT THIS DEMONSTRATES
----------------------
Text evaluation asks whether the answer was right. Voice evaluation also has to
ask whether it arrived in time and whether the words survived the round trip
through speech. This package holds the timing and audio machinery.

Present in the foundation commit:

    lab.voice.calibration   the timing calibration gate — proves the harness
                            recovers a known delay before any latency figure
                            from it is believed

Arriving in later steps: word-error-rate scoring over recorded audio fixtures
(`jiwer`, the optional `[audio]` extra) and the voice adapters that emit
audio-boundary events.

The convenience re-exports below are resolved lazily (PEP 562). Importing a
submodule eagerly from a package's `__init__` puts it in `sys.modules` before
`python -m lab.voice.calibration` gets to execute it, which makes `runpy` warn
about a double import — and the calibration gate is meant to be run that way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers only, never at runtime
    from lab.voice.calibration import (  # noqa: F401
        CalibrationReport,
        CalibrationTolerance,
        DelayMeasurement,
        MockDelayedAgent,
        recover_response_latencies,
        recover_turn_wall_times,
        run_calibration,
    )

_LAZY: dict[str, str] = {
    "CalibrationReport": "lab.voice.calibration",
    "CalibrationTolerance": "lab.voice.calibration",
    "DelayMeasurement": "lab.voice.calibration",
    "MockDelayedAgent": "lab.voice.calibration",
    "recover_response_latencies": "lab.voice.calibration",
    "recover_turn_wall_times": "lab.voice.calibration",
    "run_calibration": "lab.voice.calibration",
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
