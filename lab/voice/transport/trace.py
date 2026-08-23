"""Records to `Trace`, so the tier's numbers live in the repo's one representation.

WHY BOTHER, WHEN `measure.py` ALREADY COMPUTES EVERYTHING
---------------------------------------------------------
Because of an invariant this repository holds and this tier would otherwise be
the exception to:

    Every timing figure and every behavioural verdict must be derivable from
    trace events alone.

`measure.py` computes the delivery gap from ledgers, which is the right way to get
it — the ledgers are the evidence. But a figure that only exists in a
tier-specific model is a figure no general check can read, no report can pick up,
and no reviewer can diff against another run's trace. So the recording is also
projected into a `Trace`, and the delivery gap becomes what every other latency
figure in this repo is: a pairing over an event stream.

    trace.event_pairs("agent_audio_first_byte", "audio_delivered")

Those two kinds exist for this purpose and are documented in
`lab.trace.schema`. The projection is not a second implementation of the
measurement — `tests/test_voice_transport.py` asserts the two routes agree to
within a nanosecond on the committed recording, which is what makes it a
projection rather than a rival.

CHRONOLOGICAL, FROM ONE CLOCK
-----------------------------
Events are emitted in timestamp order, which is legitimate here for a reason worth
stating given this repo's rule that *ordering decisions* are made on stream
position: every timestamp in a recording comes from one `MonotonicClock` in one
process, so chronological order **is** the order in which these things happened.
What the rule forbids is inferring "which arriving audio belongs to which turn"
from a timestamp comparison across two independent streams, and that inference is
made in `measure.py` by segmentation and ordinal pairing before this module runs.

The trace is also deliberately *incomplete* in one respect: it carries no
`caller_utterance`, `transcript_in` or `agent_utterance` events, because this tier
has no caller, no recogniser and no model. A trace that invented them would let a
conversational check run against a session where no conversation happened.
"""

from __future__ import annotations

from typing import Callable

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace
from lab.voice.transport.measure import (
    DEFAULT_MAX_GAP_S,
    DEFAULT_MIN_RUN_FRAMES,
    speech_runs,
)
from lab.voice.transport.records import TransportRecording

__all__ = ["trace_from_recording"]

_TRANSPORT_ENGINE = "transport:webrtc/livekit"


def trace_from_recording(
    recording: TransportRecording,
    *,
    threshold_rms: float | None = None,
    min_run_frames: int = DEFAULT_MIN_RUN_FRAMES,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> Trace:
    """Project a transport recording into a trace.

    Agent-side events come from the push ledgers; `audio_delivered` events come
    from the receiver's speech runs. Nothing is invented: if a turn was pushed and
    no matching run arrived, the trace carries the `agent_audio_first_byte` with no
    `audio_delivered` after it, and `event_pairs` drops the unmatched opener — a
    lost turn stays visibly lost instead of being paired with the next turn's
    audio.

    The builder is driven with a `FakeClock` because every timestamp is supplied
    explicitly from the recording; a real clock here would let the wall time of the
    *projection* leak into the trace.
    """
    threshold = recording.onset_threshold_rms if threshold_rms is None else threshold_rms
    runs = speech_runs(
        recording.arrivals,
        threshold_rms=threshold,
        min_frames=min_run_frames,
        max_gap_s=max_gap_s,
    )
    builder = TraceBuilder(
        scenario_id=recording.row,
        adapter="voice:transport",
        session_id=recording.room,
        clock=FakeClock(),
    )

    # (ts, tie-break, emit) — collected first and sorted, so the stream is
    # chronological even though it is assembled stream by stream. The tie-break
    # keeps a delivery from being emitted before the push it belongs to when the
    # two share a timestamp to the microsecond the ledger was rounded to.
    pending: list[tuple[float, int, Callable[[], None]]] = []

    def at(ts: float, order: int, emit: Callable[[], None]) -> None:
        pending.append((ts, order, emit))

    clip_engine = f"clip:{recording.utterances[0].clip}" if recording.utterances else None

    for utterance in recording.utterances:
        ledger = utterance.pushes
        if not ledger.n:
            continue
        onset_position = next(
            (index for index, energy in enumerate(ledger.rms) if energy > threshold), None
        )
        if onset_position is not None:
            at(
                ledger.ts_s[onset_position],
                0,
                lambda t=ledger.ts_s[onset_position], turn=utterance.turn: builder.agent_audio_first_byte(
                    turn=turn, ts=t, engine=clip_engine
                ),
            )
        samples = ledger.n * int(round(utterance.sample_rate * utterance.frame_ms / 1000.0))
        at(
            ledger.ts_s[-1],
            2,
            lambda t=ledger.ts_s[-1], turn=utterance.turn, n=samples * 2: builder.agent_audio_complete(
                turn=turn, num_bytes=n, ts=t, engine=clip_engine
            ),
        )

    for index, run in enumerate(runs, start=1):
        at(
            run.onset_s,
            1,
            lambda t=run.onset_s, turn=index, n=run.frames: builder.audio_delivered(
                turn=turn,
                participant="receiver",
                ts=t,
                engine=_TRANSPORT_ENGINE,
                frames=n,
            ),
        )

    for event in recording.lifecycle:
        if event.kind == "connected":
            at(
                event.ts_s,
                -1,
                lambda e=event: builder.transport_connected(
                    participant=e.participant,
                    attempt=e.attempt,
                    ts=e.ts_s,
                    engine=_TRANSPORT_ENGINE,
                    observer=e.observer,
                ),
            )
        elif event.kind == "disconnected":
            at(
                event.ts_s,
                3,
                lambda e=event: builder.transport_disconnected(
                    participant=e.participant,
                    reason=e.reason or "unknown",
                    ts=e.ts_s,
                    engine=_TRANSPORT_ENGINE,
                    observer=e.observer,
                ),
            )

    stamps = [ts for ts, _, _ in pending]
    start = min(stamps) if stamps else 0.0
    end = max(stamps) if stamps else 0.0

    builder.session_start(
        ts=start,
        transport=recording.transport,
        url_digest=recording.url_digest,
        onset_threshold_rms=threshold,
    )
    for _, _, emit in sorted(pending, key=lambda item: (item[0], item[1])):
        emit()
    builder.session_end(
        ts=end,
        reason="completed",
        turns=len(recording.utterances),
        engine=_TRANSPORT_ENGINE,
        runs_delivered=len(runs),
    )
    return builder.build()
