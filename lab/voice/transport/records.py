"""What a transport session recorded: the evidence, before any interpretation.

WHY THE RECORDING AND THE MEASUREMENT ARE SEPARATE MODULES
----------------------------------------------------------
A real-time room cannot be replayed. There is no seed that reproduces a WebRTC
session, no cassette that makes a jitter buffer behave the same way twice, and no
amount of care that makes a network path deterministic. So the transport tier
splits in two, and the split is the whole reason the tier is auditable:

    session.py   talks to a live room and writes down *what happened*
    measure.py   reads what was written down and computes *what it means*

Everything in this file is the first half — timestamped, unaggregated, and free of
verdicts. Every figure the tier reports is derived from these records by pure
functions, offline, with no key and no network. That is what makes a number from a
one-off live session reviewable: the reader can recompute it, vary the thresholds
it depended on, and see it move.

The models here are also the on-disk fixture format. There is no second
serialisation layer, because a fixture that is not the same shape as the thing it
records is a fixture that can drift from it.

THE ARRIVAL LEDGER IS COLUMNAR, AND THAT IS NOT A MICRO-OPTIMISATION
--------------------------------------------------------------------
A receiving WebRTC audio track hands over a frame every 10 ms, whether or not
anybody is speaking, for as long as the session lasts. A 60-second row is ~6,000
frames. Stored as a list of objects that is ~600 KB of braces and key names per
row; stored as two parallel arrays of numbers it is a fifth of that and it still
diffs line by line. The invariant that matters — one timestamp per one energy
reading — is enforced by a validator rather than by the shape.

WHAT IS DELIBERATELY *NOT* STORED
---------------------------------
1.  **No audio.** The ledger keeps per-frame RMS energy, not samples. The tier's
    three questions are all about *when* audio arrived and *whether* it arrived,
    never about what it sounded like — the speech-recognition tier already owns
    that, against clips it synthesised. Storing 60 seconds of PCM per row to
    re-derive a number that is already in the ledger would make the fixture
    twenty times larger and no more informative.
2.  **No onsets, no gaps, no verdicts.** Those are computed in `measure.py` from
    the raw ledgers every time they are read. A stored onset is a claim; a
    recomputed one is a measurement whose threshold the reader can move.
3.  **No credential, and no deployment identity.** `url_digest` is the first 12
    hex characters of the SHA-256 of the signalling URL. That is enough to prove
    two recordings came from the same deployment and not enough to say which
    deployment, which matters because the URL of a hosted project is an account
    identifier even though it is not a secret. Room names are ephemeral and
    random per run, so they are kept in full: they are evidence that three rows
    ran in three distinct sessions.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ArrivalLedger",
    "PushLedger",
    "TransportEventRecord",
    "TransportRecording",
    "UtteranceRecord",
    "rms_of",
    "rms_of_pcm16",
    "url_digest",
]


def url_digest(url: str) -> str:
    """A short, stable, non-reversible tag for a signalling URL.

    Twelve hex characters of SHA-256. Enough to show two recordings share a
    deployment; not enough to name it. See the module docstring.
    """
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:12]


class PushLedger(BaseModel):
    """Every audio frame this harness handed to the transport, in push order.

    `ts_s` is the instant *immediately before* the frame was handed over — the
    same boundary discipline `lab.trace.build` documents and
    `lab.voice.calibration` proves: capture a bare float, do the work, and build
    the record afterwards, so no part of the harness's own compute lands inside a
    measured interval.

    `rms` is the frame's own energy, computed from the audio the harness already
    had in memory. It is here so that "which frame was the onset?" is answerable
    from the record rather than from a re-read of the clip, and so that the
    threshold that decides it can be varied by a later reader.
    """

    model_config = ConfigDict(extra="forbid")

    ts_s: list[float] = Field(default_factory=list)
    rms: list[float] = Field(default_factory=list)
    source_index: list[int] = Field(
        default_factory=list,
        description=(
            "Index of each pushed frame within the source clip. Non-contiguous "
            "when frames were deliberately withheld, which is what makes an "
            "injected loss pattern reconstructable rather than merely counted."
        ),
    )
    queued_s: list[float] = Field(
        default_factory=list,
        description=(
            "Audio already sitting in the local send queue when this frame was "
            "handed over, in seconds. It bounds the part of a measured delivery "
            "gap that is this harness's own buffering rather than the transport's "
            "— without it, 'the network took 87 ms' is an unsupported reading of a "
            "figure that also contains a send queue. Empty when not recorded."
        ),
    )

    @model_validator(mode="after")
    def _same_length(self) -> "PushLedger":
        lengths = {len(self.ts_s), len(self.rms), len(self.source_index)}
        if len(lengths) > 1:
            raise ValueError(
                f"a push ledger must have one timestamp, one energy reading and one "
                f"source index per frame; got {len(self.ts_s)} / {len(self.rms)} / "
                f"{len(self.source_index)}"
            )
        if self.queued_s and len(self.queued_s) != len(self.ts_s):
            raise ValueError(
                f"queued_s must be empty or carry one reading per frame; got "
                f"{len(self.queued_s)} for {len(self.ts_s)} frame(s)"
            )
        return self

    @property
    def n(self) -> int:
        return len(self.ts_s)


class ArrivalLedger(BaseModel):
    """Every audio frame that arrived at the receiving participant, in arrival order.

    Position in these arrays *is* the ordering. Nothing downstream sorts this
    ledger or compares one entry's timestamp with another's to decide which came
    first — `lab/` decides ordering on stream position by policy, and a
    real-time stream is the case that policy exists for.

    `ts_s` is captured as the first statement in the receive loop, before the
    energy is computed, for the same reason as in `PushLedger`.
    """

    model_config = ConfigDict(extra="forbid")

    ts_s: list[float] = Field(default_factory=list)
    rms: list[float] = Field(default_factory=list)
    frame_samples: int = Field(
        default=160,
        gt=0,
        description=(
            "Samples per channel in each delivered frame. Recorded because it is "
            "the receiver's choice, not the sender's: this harness pushes 20 ms "
            "frames and is handed 10 ms ones back."
        ),
    )
    sample_rate: int = Field(default=16_000, gt=0)

    @model_validator(mode="after")
    def _same_length(self) -> "ArrivalLedger":
        if len(self.ts_s) != len(self.rms):
            raise ValueError(
                f"an arrival ledger must have one energy reading per timestamp; got "
                f"{len(self.ts_s)} timestamps and {len(self.rms)} readings"
            )
        return self

    @property
    def n(self) -> int:
        return len(self.ts_s)

    @property
    def frame_s(self) -> float:
        """Nominal seconds of audio per delivered frame."""
        return self.frame_samples / self.sample_rate

    def inter_arrival_s(self) -> list[float]:
        """Gaps between consecutive arrivals, in arrival order.

        The jitter measurement's raw material. `n - 1` values for `n` frames, and
        an empty list below two frames rather than a zero, because "no gaps
        observed" and "gaps of zero" are different states.
        """
        return [b - a for a, b in zip(self.ts_s, self.ts_s[1:], strict=False)]

    def span_s(self) -> float:
        """Wall time from first to last arrival. Zero below two frames."""
        return self.ts_s[-1] - self.ts_s[0] if len(self.ts_s) > 1 else 0.0


class TransportEventRecord(BaseModel):
    """One connection-lifecycle fact, as observed by the harness.

    Two roles per record, both needed: `participant` is who moved, and `observer`
    is which side of the session noticed. A publisher knows when it dropped; only
    the receiver knows when it noticed, and the interval between those is part of
    what a reconnect costs.

    `arrival_index` is the load-bearing field. It is how many frames the receiver
    had taken delivery of at the instant this event was recorded — the event's
    position in the *audio* stream. Ordering a lifecycle event against delivered
    audio by comparing their timestamps would be deciding ordering on timestamps
    across two independent streams, which this repo does not do; carrying the
    stream position makes the same question positional. Without it, "which audio
    arrived before the drop?" is unanswerable and `measure.lifecycle_observation`
    refuses rather than guessing.
    """

    model_config = ConfigDict(extra="forbid")

    ts_s: float
    kind: Literal["connected", "disconnected", "published", "subscribed", "unsubscribed"]
    participant: str
    observer: Literal["publisher", "receiver"]
    attempt: int = Field(default=1, ge=1)
    reason: str | None = None
    arrival_index: int | None = Field(
        default=None,
        ge=0,
        description="Frames delivered when this was observed; the event's position "
        "in the receiver's stream. See the class docstring.",
    )


class UtteranceRecord(BaseModel):
    """One agent turn, as it was pushed into the transport.

    `withheld_source_index` is the injected loss pattern: the frames that were
    prepared and then deliberately not handed over. Recorded as indices rather
    than as a count so that the pattern is reproducible from the fixture, and so
    that "1 in 4 dropped" cannot quietly become "the last quarter dropped".
    """

    model_config = ConfigDict(extra="forbid")

    turn: int = Field(ge=1)
    clip: str = Field(description="Fixture clip the audio came from — provenance.")
    frame_ms: float = Field(gt=0.0)
    sample_rate: int = Field(gt=0)
    pushes: PushLedger
    withheld_source_index: list[int] = Field(default_factory=list)

    @property
    def offered_frames(self) -> int:
        """Frames the utterance consisted of: pushed plus deliberately withheld."""
        return self.pushes.n + len(self.withheld_source_index)

    @property
    def nominal_loss(self) -> float | None:
        """Injected loss as a fraction of the frames the utterance consisted of.

        `None` when the utterance is empty, rather than 0.0. A rate with no
        denominator is the defect this repo refuses to ship; see `lab/report`.
        """
        return len(self.withheld_source_index) / self.offered_frames if self.offered_frames else None


class TransportRecording(BaseModel):
    """One live transport session, written down. The unit of committed evidence.

    One recording per row, one live session per recording. Rows are not batched
    into a single session on purpose: a room is real time and cannot be sped up,
    so batching would only mean that a failure in the third measurement costs the
    first two as well.
    """

    model_config = ConfigDict(extra="forbid")

    row: str = Field(description="Transport row id this recording belongs to.")
    room: str = Field(description="Ephemeral, randomly named room the session ran in.")
    url_digest: str = Field(
        description="12 hex characters of SHA-256 over the signalling URL. Not the URL."
    )
    transport: str = Field(default="webrtc:livekit")
    recorded_at: str = Field(description="ISO-8601 UTC, for the reader, not for arithmetic.")
    clock: str = Field(
        default="MonotonicClock",
        description=(
            "Which clock produced every ts_s in this recording. One clock, one "
            "process, one origin: the delivery gap is a subtraction between two "
            "reads of the same monotonic counter, so it carries no clock-skew term."
        ),
    )
    sample_rate: int = Field(default=16_000, gt=0)
    frame_ms: float = Field(default=20.0, gt=0.0)
    onset_threshold_rms: float = Field(
        default=0.02,
        gt=0.0,
        description=(
            "The energy threshold used to *report* at record time. Analysis is "
            "free to override it, and `measure.threshold_sensitivity` exists so "
            "that the choice can be shown not to matter."
        ),
    )
    utterances: list[UtteranceRecord] = Field(default_factory=list)
    arrivals: ArrivalLedger = Field(default_factory=ArrivalLedger)
    lifecycle: list[TransportEventRecord] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="Anything the recorder observed that the numbers do not carry.",
    )

    @model_validator(mode="after")
    def _lifecycle_is_in_observation_order(self) -> "TransportRecording":
        """The lifecycle list's order *is* the observation order, so enforce it.

        Every consumer reads this list positionally — "what happened after the
        drop" is a slice, not a timestamp filter. That is only sound if append
        order matches observation order, so a recording where it does not is
        rejected at load rather than silently re-ordered by a reader.
        """
        stamps = [event.ts_s for event in self.lifecycle]
        for index, (earlier, later) in enumerate(zip(stamps, stamps[1:], strict=False)):
            if later < earlier:
                raise ValueError(
                    f"lifecycle event {index + 1} is timestamped {later:.6f}s, before "
                    f"event {index} at {earlier:.6f}s; the list order is read as the "
                    "observation order, so an out-of-order append would silently "
                    "invert 'before the drop' and 'after it'"
                )
        return self

    # ------------------------------------------------------------------ helpers

    def events_of(self, kind: str) -> list[TransportEventRecord]:
        """Lifecycle records of one kind, in recorded order."""
        return [e for e in self.lifecycle if e.kind == kind]

    def total_withheld(self) -> int:
        return sum(len(u.withheld_source_index) for u in self.utterances)

    def total_offered(self) -> int:
        return sum(u.offered_frames for u in self.utterances)

    def total_pushed(self) -> int:
        return sum(u.pushes.n for u in self.utterances)

    def nominal_loss(self) -> float | None:
        """Injected loss across the whole recording, or None with no frames."""
        offered = self.total_offered()
        return self.total_withheld() / offered if offered else None

    def duration_s(self) -> float:
        """Wall time covered by the recording, from the earliest to the latest mark."""
        marks: list[float] = [*self.arrivals.ts_s]
        for utterance in self.utterances:
            marks.extend(utterance.pushes.ts_s)
        marks.extend(event.ts_s for event in self.lifecycle)
        return max(marks) - min(marks) if len(marks) > 1 else 0.0

    def describe(self) -> str:
        """One line, always carrying its denominators."""
        loss = self.nominal_loss()
        loss_text = (
            f"{self.total_withheld()}/{self.total_offered()} frames withheld"
            if loss is not None
            else "no frames"
        )
        return (
            f"{self.row}: {len(self.utterances)} utterance(s), {self.arrivals.n} frame(s) "
            f"delivered, {loss_text}, {len(self.lifecycle)} lifecycle event(s), "
            f"{self.duration_s():.1f}s of session over {self.transport} "
            f"(deployment {self.url_digest})"
        )

    # --------------------------------------------------------------------- io

    def write(self, path: str | Path, *, digits: int = 6) -> Path:
        """Write the recording as indented JSON, rounded so a diff is readable.

        Rounding is applied to the ledgers only, and at six decimals — microsecond
        resolution on a figure reported in milliseconds, so it cannot move a
        result. Without it the file is full of float64 noise digits that change on
        every run and make a real diff impossible to spot.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump(mode="json")
        _round_in_place(payload, digits)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def read(cls, path: str | Path) -> "TransportRecording":
        """Load a committed recording. Validated on the way in, like everything else."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _round_in_place(node: object, digits: int) -> None:
    """Round every float inside a nested JSON-able structure, in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, float):
                node[key] = round(value, digits)
            else:
                _round_in_place(value, digits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, float):
                node[index] = round(value, digits)
            else:
                _round_in_place(value, digits)


def rms_of(samples: Sequence[float]) -> float:
    """Root-mean-square energy of a block of samples scaled to [-1, 1].

    Pure Python and no numpy, deliberately. It runs inside the receive loop of a
    real-time session, where importing a vectorised path for 160 samples would
    cost more than it saves, and it must be computable by a reader re-checking a
    committed ledger with nothing installed.
    """
    if not samples:
        return 0.0
    return statistics.fmean(s * s for s in samples) ** 0.5


def rms_of_pcm16(frame: Iterable[int]) -> float:
    """RMS of signed 16-bit samples, scaled to [-1, 1] by 32768."""
    values = [s / 32768.0 for s in frame]
    return rms_of(values)
