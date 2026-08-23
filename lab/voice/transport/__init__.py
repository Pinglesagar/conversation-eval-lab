"""The WebRTC transport tier: three rows, for the three things only transport has.

WHY THIS TIER EXISTS, AND WHY IT IS ONLY THREE ROWS
---------------------------------------------------
Everything else in `lab/` runs in process. That is the right default and not a
compromise: an in-process adapter is faster, it is deterministic, it needs no
credential, and the harness owns the clock, which is what makes every timing
figure in this repository reproducible. A suite that ran its whole corpus through
a live room would be slower, flakier and no more informative about the agent.

But three failures do not exist in process, because in process there is nothing
between the agent and the listener except a function call:

    1.  THE DELIVERY GAP.  An agent-side latency figure — the one a voice
        framework reports, and the one every in-process adapter here can produce —
        stops its stopwatch when the response exists. The listener is still
        waiting. Measuring the difference needs a real receiver at the far end.
    2.  TRANSPORT DEGRADATION.  A file-based perturbation deletes samples. A real
        transport conceals a gap, re-paces what survives, and has a time axis that
        a file does not have at all. Whether the two are the same condition is a
        question about every verdict the file-based ladder has ever produced.
    3.  CONNECTION LIFECYCLE.  A participant that drops mid-utterance and rejoins
        cannot be simulated by a harness that never had a connection.

So the tier is exactly three rows: the expensive instrument used only where it is
the only instrument. Rooms are real time and cannot be sped up, which is a second
reason the count is three and a third reason the tier is **non-gating in CI**. A
network test that blocks a merge trains people to bypass the gate, and a gate
people bypass protects nothing.

THE LAYOUT
----------
    records.py   what a session wrote down: ledgers, lifecycle facts, no verdicts
    measure.py   pure functions from records to findings, offline, refusable
    session.py   the only module that touches a network, behind LAB_LIVE_TRANSPORT
    trace.py     records to `Trace`, so checks consume the repo's one representation
    report.py    the tier's markdown report, with the calibration gate in front of it

The split between `session` and `measure` is the tier's answer to the problem that
a real-time session cannot be replayed. The session is run once, by hand, and
commits its recording; every number is then recomputed from that recording by
offline code, in CI, with no key. See `records.py`.

Re-exports below are lazy (PEP 562), on the same argument as `lab.voice`: neither
numpy nor the WebRTC client should be imported by the act of importing this
package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers only, never at runtime
    from lab.voice.transport.measure import (  # noqa: F401
        ChannelStats,
        DegradationComparison,
        DeliveryGapMeasurement,
        DeliveryGapSample,
        JitterStats,
        LifecycleObservation,
        SpeechRun,
        degradation_comparison,
        delivery_gap,
        jitter_stats,
        lifecycle_observation,
        speech_runs,
        threshold_sensitivity,
    )
    from lab.voice.transport.records import (  # noqa: F401
        ArrivalLedger,
        PushLedger,
        TransportEventRecord,
        TransportRecording,
        UtteranceRecord,
    )
    from lab.voice.transport.report import (  # noqa: F401
        TransportReport,
        build_report,
    )
    from lab.voice.transport.rows import (  # noqa: F401
        TransportRow,
        load_rows,
    )
    from lab.voice.transport.session import (  # noqa: F401
        LIVE_TRANSPORT_ENV_VAR,
        ClipFrames,
        LiveKitTransport,
        TransportTimeout,
        livekit_available,
        load_clip_frames,
    )
    from lab.voice.transport.trace import trace_from_recording  # noqa: F401

_LAZY: dict[str, str] = {
    # records
    "ArrivalLedger": "lab.voice.transport.records",
    "PushLedger": "lab.voice.transport.records",
    "TransportEventRecord": "lab.voice.transport.records",
    "TransportRecording": "lab.voice.transport.records",
    "UtteranceRecord": "lab.voice.transport.records",
    # measure
    "ChannelStats": "lab.voice.transport.measure",
    "DegradationComparison": "lab.voice.transport.measure",
    "DeliveryGapMeasurement": "lab.voice.transport.measure",
    "DeliveryGapSample": "lab.voice.transport.measure",
    "JitterStats": "lab.voice.transport.measure",
    "LifecycleObservation": "lab.voice.transport.measure",
    "SpeechRun": "lab.voice.transport.measure",
    "degradation_comparison": "lab.voice.transport.measure",
    "delivery_gap": "lab.voice.transport.measure",
    "jitter_stats": "lab.voice.transport.measure",
    "lifecycle_observation": "lab.voice.transport.measure",
    "speech_runs": "lab.voice.transport.measure",
    "threshold_sensitivity": "lab.voice.transport.measure",
    # rows
    "TransportRow": "lab.voice.transport.rows",
    "load_rows": "lab.voice.transport.rows",
    # trace
    "trace_from_recording": "lab.voice.transport.trace",
    # report
    "TransportReport": "lab.voice.transport.report",
    "build_report": "lab.voice.transport.report",
    # session — the live path, imported only when asked for
    "LIVE_TRANSPORT_ENV_VAR": "lab.voice.transport.session",
    "ClipFrames": "lab.voice.transport.session",
    "LiveKitTransport": "lab.voice.transport.session",
    "TransportTimeout": "lab.voice.transport.session",
    "livekit_available": "lab.voice.transport.session",
    "load_clip_frames": "lab.voice.transport.session",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    """Resolve a re-export on first use (PEP 562)."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module  # noqa: PLC0415 - stdlib, resolved once

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
