"""Voice-specific measurement.

WHAT THIS DEMONSTRATES
----------------------
Text evaluation asks whether the answer was right. Voice evaluation also has to
ask whether it arrived in time and whether the words survived the round trip
through speech. This package holds the timing and audio machinery.

    lab.voice.calibration   the timing calibration gate — proves the harness
                            recovers a known delay before any latency figure
                            from it is believed
    lab.voice.metrics       response-latency distributions: time-to-first-byte
                            against time-to-complete, with percentiles that
                            refuse to be reported below a stated sample count
    lab.voice.wer           word error rate, raw and normalised, explicitly
                            harness-relative
    lab.voice.silence       dead-air detection and attribution to the tool calls
                            and handoffs recorded inside each gap
    lab.voice.perturb       controlled caller-audio degradation — noise at a
                            target SNR, speed/pitch, telephone band, packet loss

Every one of these computes from a `Trace`, so the same code serves the voice
adapters and the text adapter; only `perturb` touches audio samples, and it is
the one module here that requires numpy (the `[audio]` extra).

Arriving in later steps: the voice adapters that emit the audio-boundary events
these modules read.

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
    from lab.voice.metrics import (  # noqa: F401
        Distribution,
        Quantile,
        ResponseLatencyReport,
        latencies_by_engine,
        min_samples_for_quantile,
        response_latency_report,
    )
    from lab.voice.perturb import (  # noqa: F401
        PerturbationDescriptor,
        add_noise,
        apply_chain,
        packet_loss,
        perturbation_payload,
        resample_speed,
        shift_pitch,
        telephone_band,
    )
    from lab.voice.silence import (  # noqa: F401
        SilenceGap,
        SilenceReport,
        find_gaps,
        silence_report,
    )
    from lab.voice.wer import (  # noqa: F401
        CorpusWER,
        UtteranceWER,
        WERScore,
        corpus_wer,
        normalise,
        trace_wer,
        wer,
    )

_LAZY: dict[str, str] = {
    "CalibrationReport": "lab.voice.calibration",
    "CalibrationTolerance": "lab.voice.calibration",
    "DelayMeasurement": "lab.voice.calibration",
    "MockDelayedAgent": "lab.voice.calibration",
    "recover_response_latencies": "lab.voice.calibration",
    "recover_turn_wall_times": "lab.voice.calibration",
    "run_calibration": "lab.voice.calibration",
    # lab.voice.metrics — latency distributions
    "Distribution": "lab.voice.metrics",
    "Quantile": "lab.voice.metrics",
    "ResponseLatencyReport": "lab.voice.metrics",
    "latencies_by_engine": "lab.voice.metrics",
    "min_samples_for_quantile": "lab.voice.metrics",
    "response_latency_report": "lab.voice.metrics",
    # lab.voice.wer — transcription accuracy
    "CorpusWER": "lab.voice.wer",
    "UtteranceWER": "lab.voice.wer",
    "WERScore": "lab.voice.wer",
    "corpus_wer": "lab.voice.wer",
    "normalise": "lab.voice.wer",
    "trace_wer": "lab.voice.wer",
    "wer": "lab.voice.wer",
    # lab.voice.silence — dead air
    "SilenceGap": "lab.voice.silence",
    "SilenceReport": "lab.voice.silence",
    "find_gaps": "lab.voice.silence",
    "silence_report": "lab.voice.silence",
    # lab.voice.perturb — needs numpy, so resolving these is what pulls it in
    "PerturbationDescriptor": "lab.voice.perturb",
    "add_noise": "lab.voice.perturb",
    "apply_chain": "lab.voice.perturb",
    "packet_loss": "lab.voice.perturb",
    "perturbation_payload": "lab.voice.perturb",
    "resample_speed": "lab.voice.perturb",
    "shift_pitch": "lab.voice.perturb",
    "telephone_band": "lab.voice.perturb",
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
