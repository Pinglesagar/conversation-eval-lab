"""The three transport measurements, computed from records by pure functions.

WHAT THIS DEMONSTRATES
----------------------
That a number taken from a one-off live session can still be audited. Nothing
here touches a network, a key or a clock. Every function takes a
`TransportRecording` — the written-down evidence — and returns a model that
carries its own inputs, its own thresholds and, where it declines to answer, the
reason. Run it a year from now against the committed fixture and it produces the
same figures, which is the only sense in which a real-time measurement can be
reproducible.

SEGMENTATION, NOT TIMESTAMP MATCHING
------------------------------------
The central problem of this tier is deciding *which arriving audio is which
utterance*. It is easy to get wrong in a way that produces a plausible number.

A receiving WebRTC track hands over a frame every 10 ms for the whole session,
speech or not. So "the first frame that arrived" is meaningless — it arrived
before anything was said. The obvious fix is to search the arrival ledger for the
first energetic frame whose timestamp is later than the utterance's push, and
that is exactly the fix this module does **not** use, because it decides ordering
across two independent streams by comparing their timestamps. `lab/` decides
ordering on stream-position, on purpose and after a past defect, and a real-time
session is the case that policy exists for.

Instead each stream is segmented on its own terms. The receiver's ledger is cut
into *speech runs* — maximal stretches of energetic frames — by position. The
sender's utterances are already a list. Then run `k` is paired with utterance `k`
by ordinal, and the pairing is refused outright if the counts disagree. That
refusal is not defensive boilerplate: an unequal count is precisely what a lost
turn looks like, and it is the signal row 3 exists to catch. The same primitive
answers "how long did delivery take?" and "did the turn survive?".

WHY EVERY MEASUREMENT CAN REFUSE
--------------------------------
Each of the three returns a model with `reportable: bool` and a `refusal`
string. A transport measurement has four ways to be quietly wrong, and each one
is a refusal rather than a caveat:

1.  **The stopwatch is unproven.** No latency figure in this repo is reportable
    unless `lab.voice.calibration`'s gate has passed, so the delivery gap takes a
    `CalibrationReport` and refuses without one. The gate proves the harness
    recovers a known delay while excluding its own compute; this tier reuses that
    discipline (bare float at the boundary, records built afterwards) and would
    otherwise be asserting it.
2.  **The pairing is ambiguous.** Run count != utterance count.
3.  **The arithmetic is impossible.** A negative gap means audio arrived before it
    was sent — a mis-pairing or a broken clock, never a fast network.
4.  **The denominator is zero.** No frames, no utterances, no runs.
"""

from __future__ import annotations

import statistics
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.voice.calibration import CalibrationReport
from lab.voice.metrics import Distribution
from lab.voice.transport.records import ArrivalLedger, TransportRecording

__all__ = [
    "DEFAULT_MAX_GAP_S",
    "DEFAULT_MIN_RUN_FRAMES",
    "DEFAULT_THRESHOLD_RMS",
    "ChannelStats",
    "DegradationComparison",
    "DeliveryGapMeasurement",
    "DeliveryGapSample",
    "JitterStats",
    "LifecycleObservation",
    "LifecycleVerdict",
    "SpeechRun",
    "channel_stats",
    "degradation_comparison",
    "delivery_gap",
    "jitter_stats",
    "lifecycle_observation",
    "speech_runs",
    "threshold_sensitivity",
]

#: Energy above which a frame counts as speech. Chosen an order of magnitude
#: above the committed clips' silence floor and an order below their speech RMS
#: (~0.17), so the decision is not close. `threshold_sensitivity` exists to show
#: the reported gap does not depend on the exact value.
DEFAULT_THRESHOLD_RMS: float = 0.02

#: Energetic frames needed before a run counts as speech. At 10 ms per delivered
#: frame this is 30 ms — long enough that a single concealment artefact or codec
#: transient cannot invent an utterance, short enough not to clip a real onset.
DEFAULT_MIN_RUN_FRAMES: int = 3

#: Quiet time tolerated *inside* a run before it is closed, in seconds. The
#: number is a compromise between two constraints that were measured rather than
#: assumed:
#:
#:   * it must be SHORTER than the silence this tier pushes between utterances
#:     (400 ms), or two turns merge into one run and the pairing breaks;
#:   * it must be LONGER than any pause inside the utterance being published, or
#:     one turn splits into two runs and the pairing breaks the other way.
#:
#: The second constraint is the one that bites. Measured across this repo's
#: committed clips, internal pauses reach 280 ms — a sentence boundary in a
#: two-sentence clip is comfortably longer than 200 ms — so "longer than a pause
#: inside a sentence" is simply false for some of them. Rather than inflate the
#: tolerance until it collides with the first constraint, the recorder refuses a
#: clip that would split: see `session.longest_quiet_ms`. A clip is chosen for
#: this property, and the choice is checked.
#:
#: **In seconds, not in frames, and that was a bug.** The first version counted
#: quiet *positions* in the arrival ledger, on the assumption that a receiving
#: track delivers one frame every 10 ms. It does not. A second live session
#: contained a delivery stall followed by a burst — 24 frames arriving inside
#: 128 ms — and counting positions read that burst as a 24-frame silence and split
#: one utterance into two runs. The pairing then refused an otherwise good session.
#: Position and elapsed time diverge exactly under jitter, which is the condition
#: this tier exists to measure, so the gap is measured in time from the ledger's
#: own timestamps. Ordering is still decided by position — the ledger is walked in
#: arrival order — and only the *duration* of a gap is read off the clock.
DEFAULT_MAX_GAP_S: float = 0.20


# --------------------------------------------------------------------------- #
# Segmentation
# --------------------------------------------------------------------------- #


class SpeechRun(BaseModel):
    """A maximal stretch of energetic frames in an arrival ledger.

    Indices are positions in the ledger, and they are the primary result; the
    timestamps are read off those positions. Keeping both means a reader can go
    back to the raw arrays and check the boundaries by hand.
    """

    model_config = ConfigDict(extra="forbid")

    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0, description="Inclusive index of the last energetic frame.")
    onset_s: float
    offset_s: float
    frames: int = Field(gt=0)
    peak_rms: float
    mean_rms: float

    @property
    def duration_s(self) -> float:
        return self.offset_s - self.onset_s


def speech_runs(
    ledger: ArrivalLedger,
    *,
    threshold_rms: float = DEFAULT_THRESHOLD_RMS,
    min_frames: int = DEFAULT_MIN_RUN_FRAMES,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> list[SpeechRun]:
    """Cut an arrival ledger into speech runs, by position.

    A run opens at the first frame above `threshold_rms` and closes when more than
    `max_gap_s` of quiet has elapsed since the last energetic frame — elapsed
    time, read from the ledger's timestamps, not a count of quiet positions; see
    `DEFAULT_MAX_GAP_S` for the live session that made the difference matter.
    Runs shorter than `min_frames` energetic frames are discarded as artefacts.
    The trailing run is closed by the end of the ledger.

    All three parameters are arguments rather than constants so that a reader can
    move them and watch the answer move — `threshold_sensitivity` does exactly
    that, and the report prints the values it used.
    """
    if min_frames < 1:
        raise ValueError(f"min_frames must be at least 1, got {min_frames!r}")
    if max_gap_s < 0:
        raise ValueError(f"max_gap_s cannot be negative, got {max_gap_s!r}")

    runs: list[SpeechRun] = []
    start: int | None = None
    last_loud: int | None = None

    def close(first: int, last: int) -> None:
        block = ledger.rms[first : last + 1]
        loud = [value for value in block if value > threshold_rms]
        if len(loud) < min_frames:
            return
        runs.append(
            SpeechRun(
                start_index=first,
                end_index=last,
                onset_s=ledger.ts_s[first],
                offset_s=ledger.ts_s[last],
                frames=last - first + 1,
                peak_rms=max(block),
                mean_rms=statistics.fmean(block),
            )
        )

    for index, energy in enumerate(ledger.rms):
        if energy > threshold_rms:
            if start is None:
                start = index
            last_loud = index
            continue
        if start is None or last_loud is None:
            continue
        if ledger.ts_s[index] - ledger.ts_s[last_loud] > max_gap_s:
            close(start, last_loud)
            start = None
            last_loud = None

    if start is not None and last_loud is not None:
        close(start, last_loud)
    return runs


def _push_onset(
    rms: Sequence[float], ts_s: Sequence[float], threshold_rms: float
) -> tuple[int, float] | None:
    """First pushed frame at or above the threshold: its position and timestamp."""
    for index, energy in enumerate(rms):
        if energy > threshold_rms:
            return index, ts_s[index]
    return None


# --------------------------------------------------------------------------- #
# Row 1 — the delivery gap
# --------------------------------------------------------------------------- #


class DeliveryGapSample(BaseModel):
    """One turn, timed twice: agent-side and receiver-side.

    `agent_onset_s` is the instant the harness handed the utterance's first
    energetic frame to the transport — the instant an in-process adapter records
    as `agent_audio_first_byte`, and the instant a voice framework's own
    `e2e_latency` ends at. `delivered_onset_s` is the instant the matching audio
    arrived at the other participant. The gap is what the first number omits.
    """

    model_config = ConfigDict(extra="forbid")

    turn: int
    agent_onset_s: float
    delivered_onset_s: float
    gap_s: float
    run_index: int = Field(ge=0, description="Which speech run this turn was paired with.")
    queue_at_onset_s: float | None = Field(
        default=None,
        description=(
            "Audio already in the harness's own send queue when the onset frame was "
            "handed over. Part of the measured gap that is this process's buffering "
            "rather than the transport's, and the only term in the gap that a "
            "different sender would not reproduce."
        ),
    )

    @property
    def gap_ms(self) -> float:
        return self.gap_s * 1000.0

    @property
    def net_gap_s(self) -> float | None:
        """The gap with the local send queue subtracted, or None if unrecorded."""
        if self.queue_at_onset_s is None:
            return None
        return self.gap_s - self.queue_at_onset_s


class DeliveryGapMeasurement(BaseModel):
    """The delivery gap over one session, with its own admissibility.

    The distribution is a `lab.voice.metrics.Distribution`, so the quantiles obey
    the same refusal rule as every other latency figure in the repo: p95 needs
    twenty samples and says so when it does not have them. A real-time room cannot
    be sped up, so this tier will usually have enough samples for p50 and p90 and
    not for p95 — and it prints that rather than quietly interpolating.
    """

    model_config = ConfigDict(extra="forbid")

    row: str
    reportable: bool
    refusal: str | None = None
    calibration_verdict: Literal["PASS", "FAIL", "ABSENT"]
    samples: list[DeliveryGapSample] = Field(default_factory=list)
    distribution: Distribution | None = None
    net_distribution: Distribution | None = Field(
        default=None,
        description=(
            "The same gaps with each turn's local send-queue depth subtracted — the "
            "transport's own contribution, separated from this harness's buffering. "
            "None when the recording carries no queue readings."
        ),
    )
    queue_correlation: float | None = Field(
        default=None,
        description=(
            "Pearson correlation between the measured gap and the local send-queue "
            "depth at the same onset. The evidence for the split: a value near 1 "
            "means the raw figure's scatter is mostly the harness's own queue, and "
            "the net figure is the one that generalises to another sender."
        ),
    )
    threshold_rms: float
    min_run_frames: int
    max_gap_s: float
    utterances: int = Field(ge=0)
    runs_detected: int = Field(ge=0)
    agent_side_figure_s: float | None = Field(
        default=None,
        description=(
            "The number an agent-side harness would report for these turns: zero "
            "delivery time, because it stops its stopwatch before the transport "
            "runs. Held explicitly so the report can print the two side by side."
        ),
    )

    @property
    def mean_ms(self) -> float | None:
        mean = self.distribution.mean_s if self.distribution else None
        return mean * 1000.0 if mean is not None else None

    @property
    def net_mean_ms(self) -> float | None:
        mean = self.net_distribution.mean_s if self.net_distribution else None
        return mean * 1000.0 if mean is not None else None

    def describe(self) -> str:
        if not self.reportable:
            return f"{self.row}: delivery gap NOT REPORTED — {self.refusal}"
        assert self.distribution is not None
        p50 = self.distribution.quantile(0.50)
        net = (
            f"; net of this harness's send queue {self.net_mean_ms:.1f} ms "
            f"(stdev {(self.net_distribution.stdev_s or 0.0) * 1000:.1f} ms, "
            f"correlation {self.queue_correlation:.2f})"
            if self.net_distribution is not None and self.queue_correlation is not None
            else ""
        )
        return (
            f"{self.row}: delivery gap mean {self.mean_ms:.1f} ms over "
            f"{self.distribution.n} turn(s), {p50.describe()}{net}, "
            f"threshold {self.threshold_rms} RMS, calibration {self.calibration_verdict}"
        )


def delivery_gap(
    recording: TransportRecording,
    *,
    calibration: CalibrationReport | None,
    threshold_rms: float | None = None,
    min_run_frames: int = DEFAULT_MIN_RUN_FRAMES,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> DeliveryGapMeasurement:
    """Pair each utterance with its delivered audio and report the interval.

    Args:
        recording: The session evidence.
        calibration: The timing gate's report. **Required to be a PASS**: an
            uncalibrated stopwatch makes every figure below unproven, so a `None`
            or a `FAIL` produces a refusal rather than a number. Pass
            `lab.voice.calibration.run_calibration()` — it is offline,
            deterministic and costs milliseconds.
        threshold_rms: Energy threshold; defaults to the recording's own.
        min_run_frames: Passed to `speech_runs`.
        max_gap_s: Passed to `speech_runs`.
    """
    threshold = recording.onset_threshold_rms if threshold_rms is None else threshold_rms
    runs, pairing_refusal = _paired_runs(
        recording,
        threshold_rms=threshold,
        min_run_frames=min_run_frames,
        max_gap_s=max_gap_s,
    )
    verdict: Literal["PASS", "FAIL", "ABSENT"] = (
        "ABSENT" if calibration is None else calibration.verdict
    )

    def refuse(reason: str) -> DeliveryGapMeasurement:
        return DeliveryGapMeasurement(
            row=recording.row,
            reportable=False,
            refusal=reason,
            calibration_verdict=verdict,
            threshold_rms=threshold,
            min_run_frames=min_run_frames,
            max_gap_s=max_gap_s,
            utterances=len(recording.utterances),
            runs_detected=len(runs),
        )

    if calibration is None:
        return refuse(
            "no calibration report was supplied, so the harness's stopwatch is unproven; "
            "run lab.voice.calibration.run_calibration() and pass its report"
        )
    if not calibration.passed:
        return refuse(
            f"the timing calibration gate reports {calibration.verdict} "
            f"({calibration.tolerance.describe()}), so no latency figure from this "
            "harness is reportable until it is fixed"
        )
    if pairing_refusal is not None:
        return refuse(
            f"{pairing_refusal}; an unequal count is itself the finding rather than a "
            "reason to fall back on timestamp matching — see the connection-lifecycle row"
        )

    samples: list[DeliveryGapSample] = []
    for index, (utterance, run) in enumerate(zip(recording.utterances, runs, strict=True)):
        onset = _push_onset(utterance.pushes.rms, utterance.pushes.ts_s, threshold)
        if onset is None:
            return refuse(
                f"turn {utterance.turn} pushed no frame above {threshold} RMS, so it has "
                "no agent-side onset to measure from; the clip or the threshold is wrong"
            )
        onset_position, agent_onset_s = onset
        queued = (
            utterance.pushes.queued_s[onset_position]
            if utterance.pushes.queued_s
            else None
        )
        gap = run.onset_s - agent_onset_s
        if gap < 0:
            return refuse(
                f"turn {utterance.turn} appears to have been delivered {abs(gap) * 1000:.1f} ms "
                "before it was sent, which is a mis-pairing or a broken clock, never a fast "
                "network; the measurement is withheld"
            )
        samples.append(
            DeliveryGapSample(
                turn=utterance.turn,
                agent_onset_s=agent_onset_s,
                delivered_onset_s=run.onset_s,
                gap_s=gap,
                run_index=index,
                queue_at_onset_s=queued,
            )
        )

    net_samples = [
        sample.net_gap_s for sample in samples if sample.net_gap_s is not None
    ]
    queues = [
        sample.queue_at_onset_s
        for sample in samples
        if sample.queue_at_onset_s is not None
    ]
    correlation: float | None = None
    if len(queues) == len(samples) and len(samples) > 1:
        try:
            correlation = statistics.correlation(
                [sample.gap_s for sample in samples], queues
            )
        except statistics.StatisticsError:
            # Constant queue depth across every turn: no correlation is defined,
            # which is a fine state to be in and not one to invent a number for.
            correlation = None

    return DeliveryGapMeasurement(
        row=recording.row,
        reportable=True,
        refusal=None,
        calibration_verdict=verdict,
        samples=samples,
        net_distribution=(
            Distribution.from_samples(
                "delivery gap net of the harness's own send queue",
                net_samples,
                description=(
                    "Each turn's gap with the audio already queued locally at the "
                    "onset subtracted. Separates the transport's contribution from "
                    "this process's buffering."
                ),
            )
            if len(net_samples) == len(samples) and net_samples
            else None
        ),
        queue_correlation=correlation,
        distribution=Distribution.from_samples(
            "delivery gap (agent-side onset -> receiver-side arrival)",
            [sample.gap_s for sample in samples],
            description=(
                "Interval between the harness handing an utterance's first energetic "
                "frame to the transport and that audio arriving at the far participant. "
                "Both instants read from one monotonic clock in one process."
            ),
        ),
        threshold_rms=threshold,
        min_run_frames=min_run_frames,
        max_gap_s=max_gap_s,
        utterances=len(recording.utterances),
        runs_detected=len(runs),
        agent_side_figure_s=0.0,
    )


def threshold_sensitivity(
    recording: TransportRecording,
    *,
    calibration: CalibrationReport | None,
    thresholds: Sequence[float] = (0.005, 0.01, 0.02, 0.04, 0.08),
) -> list[tuple[float, DeliveryGapMeasurement]]:
    """Recompute the gap at several energy thresholds.

    The delivery gap rests on one judgement call — where speech starts — so the
    honest thing is to show what happens when that call is made differently. A
    measurement that moves by tens of milliseconds across this sweep is a
    measurement of the threshold, not of the transport.
    """
    return [
        (
            threshold,
            delivery_gap(recording, calibration=calibration, threshold_rms=threshold),
        )
        for threshold in thresholds
    ]


# --------------------------------------------------------------------------- #
# Row 2 — degradation
# --------------------------------------------------------------------------- #


class JitterStats(BaseModel):
    """Delivery pacing at the receiver, from inter-arrival gaps.

    **This is an upper bound on transport jitter, not an estimate of it.** Each
    arrival is timestamped when the harness's receive loop gets to it, so the
    series carries the event loop's own scheduling on top of the network's. It is
    reported because the direction of the bias is known and stated, and because
    the file-based ladder cannot produce this figure at all — a perturbed file has
    no time axis. The single-onset delivery gap of row 1 is the robust figure;
    this is the shape around it.

    Not RFC 3550's smoothed interarrival jitter. `mean_abs_deviation_ms` is the
    mean absolute deviation of inter-arrival gaps from the nominal frame period,
    named for what it is.
    """

    model_config = ConfigDict(extra="forbid")

    frames: int = Field(ge=0)
    gaps: int = Field(ge=0, description="Inter-arrival intervals: frames - 1, or 0.")
    nominal_frame_ms: float
    mean_gap_ms: float | None = None
    p50_gap_ms: float | None = None
    p95_gap_ms: float | None = None
    max_gap_ms: float | None = None
    mean_abs_deviation_ms: float | None = None
    late_frames: int = Field(
        default=0,
        ge=0,
        description="Gaps longer than 1.5x the nominal frame period — a stall, not a wobble.",
    )

    @property
    def late_rate(self) -> str:
        """Late frames with their denominator, never as a naked percentage."""
        if not self.gaps:
            return "0/0 (no intervals observed)"
        return f"{self.late_frames}/{self.gaps} ({self.late_frames / self.gaps:.1%})"


def jitter_stats(ledger: ArrivalLedger) -> JitterStats:
    """Inter-arrival statistics for a delivered frame stream."""
    gaps = ledger.inter_arrival_s()
    nominal_ms = ledger.frame_s * 1000.0
    if not gaps:
        return JitterStats(frames=ledger.n, gaps=0, nominal_frame_ms=nominal_ms)
    gaps_ms = sorted(gap * 1000.0 for gap in gaps)
    return JitterStats(
        frames=ledger.n,
        gaps=len(gaps),
        nominal_frame_ms=nominal_ms,
        mean_gap_ms=statistics.fmean(gaps_ms),
        p50_gap_ms=_percentile(gaps_ms, 0.50),
        p95_gap_ms=_percentile(gaps_ms, 0.95),
        max_gap_ms=gaps_ms[-1],
        mean_abs_deviation_ms=statistics.fmean(abs(g - nominal_ms) for g in gaps_ms),
        late_frames=sum(1 for g in gaps_ms if g > nominal_ms * 1.5),
    )


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile over an already-sorted sequence."""
    if not sorted_values:
        raise ValueError("no values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


class ChannelStats(BaseModel):
    """What a channel did to one utterance's audible energy.

    Deliberately shape-agnostic: the same four figures are computed from the
    receiver's frame ledger and from a perturbed file cut into frames of the same
    length, so the two are comparable without either being converted into the
    other's representation.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["transport", "file-ladder"]
    frames: int = Field(ge=0)
    silent_frames: int = Field(ge=0)
    mean_rms: float | None = None
    peak_rms: float | None = None
    duration_s: float | None = None

    @property
    def silent_fraction(self) -> float | None:
        """Silent frames over total frames, or None with no frames."""
        return self.silent_frames / self.frames if self.frames else None

    @property
    def silent_rate(self) -> str:
        if not self.frames:
            return "0/0 (no frames)"
        return f"{self.silent_frames}/{self.frames} ({self.silent_frames / self.frames:.1%})"


def channel_stats(
    rms: Sequence[float],
    *,
    source: Literal["transport", "file-ladder"],
    frame_s: float,
    threshold_rms: float = DEFAULT_THRESHOLD_RMS,
) -> ChannelStats:
    """Summarise a sequence of per-frame energies as one channel's behaviour."""
    values = [float(value) for value in rms]
    if not values:
        return ChannelStats(source=source, frames=0, silent_frames=0)
    return ChannelStats(
        source=source,
        frames=len(values),
        silent_frames=sum(1 for value in values if value <= threshold_rms),
        mean_rms=statistics.fmean(values),
        peak_rms=max(values),
        duration_s=len(values) * frame_s,
    )


class DegradationComparison(BaseModel):
    """Real transport loss beside the file-based ladder's version of the same loss.

    The comparison exists to test the ladder's validity, not the transport's. If a
    graded series of file perturbations is the instrument a suite uses to say "the
    agent survives 5% loss and fails at 20%", then whether a file perturbation
    resembles real loss is a question about every verdict that instrument produced.

    FOUR CHANNELS, BECAUSE TWO WOULD GIVE THE WRONG ANSWER
    ------------------------------------------------------
    The obvious comparison — silent-frame fraction under loss, transport against
    file — is wrong twice over, and both errors were measured rather than
    theorised:

    1.  **Each channel has its own silence floor.** The clip contains quiet frames
        of its own, and the two channels disagree about them even with no loss at
        all: the file reads 17.6% of frames as silent while the transport reads
        10.6%, because a codec's output has a noise floor that a raw file does not.
        Comparing the loaded figures compares the floors as much as the loss. So
        each side is measured against **its own** no-loss baseline and the
        comparison is between *increments*.
    2.  **The two instruments do not apply the same amount of loss.** This harness
        withholds every fourth frame — exactly 25.4%, deterministic. `packet_loss`
        is Bernoulli per packet, and over 72 packets a request for 25.4% realised
        16.7%. `perturb` reports that honestly in its descriptor, and ignoring it
        would credit the file ladder with a gentleness that is really just a
        smaller dose. So each increment is divided by the loss **actually
        applied**, giving points of added silence per point of loss.

    Skip either correction and the naive reading of this row says the `hold` fill
    mode agrees with reality within tolerance. Apply both and it does not — it
    understates the damage by a factor of nearly three, while `zero` overstates it
    by nearly two. Same recording, two conclusions; the difference is whether the
    comparison controls for the floor and the dose.
    """

    model_config = ConfigDict(extra="forbid")

    row: str
    reportable: bool
    refusal: str | None = None
    fill: str | None = Field(
        default=None,
        description=(
            "Which `perturb.packet_loss` fill mode the file side used. Part of the "
            "identity of the comparison, not a detail: it decides the answer."
        ),
    )
    injected_turn: int | None = None
    control_turn: int | None = None
    nominal_loss: float | None = Field(
        default=None, description="Loss this harness injected: withheld over offered."
    )
    file_realised_loss: float | None = Field(
        default=None,
        description="Loss `packet_loss` actually applied, from its own descriptor.",
    )
    withheld_frames: int = Field(ge=0)
    offered_frames: int = Field(ge=0)

    transport: ChannelStats | None = None
    control: ChannelStats | None = None
    file_ladder: ChannelStats | None = None
    file_baseline: ChannelStats | None = None
    jitter: JitterStats | None = None

    threshold_rms: float = DEFAULT_THRESHOLD_RMS
    agree: bool | None = None
    relative_tolerance: float = Field(
        default=0.25,
        gt=0.0,
        description=(
            "How far the two normalised coefficients may differ, as a fraction of "
            "the transport's. Stated because an agreement claim without a tolerance "
            "is an opinion. 25% is loose on purpose: the question is whether a rung "
            "of the ladder is the same order of damage, not whether it matches to "
            "the point."
        ),
    )
    findings: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------- normalising

    @property
    def transport_excess(self) -> float | None:
        """Added silent-frame fraction over the loss-free control arm."""
        if self.transport is None or self.control is None:
            return None
        loaded = self.transport.silent_fraction
        floor = self.control.silent_fraction
        if loaded is None or floor is None:
            return None
        return loaded - floor

    @property
    def ladder_excess(self) -> float | None:
        """Added silent-frame fraction over the unperturbed file."""
        if self.file_ladder is None or self.file_baseline is None:
            return None
        loaded = self.file_ladder.silent_fraction
        floor = self.file_baseline.silent_fraction
        if loaded is None or floor is None:
            return None
        return loaded - floor

    @property
    def transport_silence_per_loss(self) -> float | None:
        """Points of added silence per point of loss, over the transport."""
        excess = self.transport_excess
        if excess is None or not self.nominal_loss:
            return None
        return excess / self.nominal_loss

    @property
    def ladder_silence_per_loss(self) -> float | None:
        """Points of added silence per point of loss, in the file."""
        excess = self.ladder_excess
        if excess is None or not self.file_realised_loss:
            return None
        return excess / self.file_realised_loss

    @property
    def ratio(self) -> float | None:
        """Ladder coefficient over transport coefficient. 1.0 would be agreement."""
        transport = self.transport_silence_per_loss
        ladder = self.ladder_silence_per_loss
        if transport is None or ladder is None or transport == 0.0:
            return None
        return ladder / transport

    @property
    def nominal_rate(self) -> str:
        if self.nominal_loss is None:
            return "0/0 (no frames offered)"
        return f"{self.withheld_frames}/{self.offered_frames} ({self.nominal_loss:.1%})"

    def describe(self) -> str:
        if not self.reportable:
            return f"{self.row}: degradation NOT REPORTED — {self.refusal}"
        ratio = self.ratio
        return (
            f"{self.row} [fill={self.fill}]: {self.nominal_rate} injected -> "
            f"transport adds {(self.transport_excess or 0.0):.1%} silence "
            f"({(self.transport_silence_per_loss or 0.0):.2f} per unit loss); "
            f"file ladder adds {(self.ladder_excess or 0.0):.1%} at a realised "
            f"{(self.file_realised_loss or 0.0):.1%} "
            f"({(self.ladder_silence_per_loss or 0.0):.2f} per unit loss); "
            f"ratio {ratio:.2f}x — " + ("AGREE" if self.agree else "DISAGREE")
            if ratio is not None
            else f"{self.row}: normalised comparison unavailable"
        )


def _paired_runs(
    recording: TransportRecording,
    *,
    threshold_rms: float,
    min_run_frames: int,
    max_gap_s: float,
) -> tuple[list[SpeechRun], str | None]:
    """Runs paired to utterances by ordinal, or a refusal reason.

    The one pairing rule this module has, in one place, so rows 2 and 3 cannot
    drift into disagreeing about which arriving audio was which turn.
    """
    runs = speech_runs(
        recording.arrivals,
        threshold_rms=threshold_rms,
        min_frames=min_run_frames,
        max_gap_s=max_gap_s,
    )
    if not recording.utterances:
        return runs, "the recording contains no utterances"
    if len(runs) != len(recording.utterances):
        return runs, (
            f"{len(recording.utterances)} utterance(s) were pushed but {len(runs)} "
            f"speech run(s) arrived above {threshold_rms} RMS, so run k cannot be "
            "paired with utterance k"
        )
    return runs, None


def degradation_comparison(
    recording: TransportRecording,
    *,
    file_rms: Sequence[float] | None,
    file_baseline_rms: Sequence[float] | None = None,
    file_realised_loss: float | None = None,
    fill: str | None = None,
    threshold_rms: float | None = None,
    min_run_frames: int = DEFAULT_MIN_RUN_FRAMES,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
    relative_tolerance: float = 0.25,
) -> DegradationComparison:
    """Compare what the transport delivered with what the file ladder produces.

    Args:
        recording: A session with one utterance whose frames were deliberately
            withheld and, ideally, one with nothing withheld as the control arm.
        file_rms: Per-frame energies of the *same* clip after
            `lab.voice.perturb.packet_loss`, framed at the same length as the
            arrival ledger. Computed by the caller because it needs numpy, and this
            module stays importable without it.
        file_baseline_rms: Per-frame energies of the same clip with **no** loss.
            The file side's own silence floor; without it the comparison cannot be
            normalised and is refused.
        file_realised_loss: The loss `packet_loss` actually applied, from
            `PerturbationDescriptor.measured["realised_loss_rate"]`. Bernoulli loss
            over a short clip misses its target substantially, and using the
            requested rate instead credits the file with the wrong dose.
        fill: The fill mode used, recorded because it decides the answer.
        threshold_rms: Silence threshold, applied identically to all four channels.
        relative_tolerance: Agreement band on the normalised coefficients.

    Each channel is measured over its delivered speech region only — the run's own
    first-to-last frame — because the idle silence around an utterance is not
    evidence about loss and would dilute every figure towards the session's dead
    air.
    """
    threshold = recording.onset_threshold_rms if threshold_rms is None else threshold_rms

    def refuse(reason: str, *, withheld: int = 0, offered: int = 0) -> DegradationComparison:
        return DegradationComparison(
            row=recording.row,
            reportable=False,
            refusal=reason,
            fill=fill,
            withheld_frames=withheld,
            offered_frames=offered,
            threshold_rms=threshold,
            relative_tolerance=relative_tolerance,
        )

    injected_index = next(
        (
            index
            for index, utterance in enumerate(recording.utterances)
            if utterance.withheld_source_index
        ),
        None,
    )
    if injected_index is None:
        return refuse(
            "no utterance withheld any frame, so this recording carries no injected "
            "loss for the file ladder to be compared against"
        )
    injected = recording.utterances[injected_index]
    withheld_count = len(injected.withheld_source_index)
    if not injected.offered_frames:
        return refuse("the injected utterance offered no frames, so it has no loss rate")
    if file_rms is None:
        return refuse(
            "the file-ladder side was not computed (it needs numpy and the committed "
            "clip), so there is nothing to compare the transport against",
            withheld=withheld_count,
            offered=injected.offered_frames,
        )
    if file_baseline_rms is None or not file_realised_loss:
        return refuse(
            "the file side was supplied without its no-loss baseline or without the "
            "loss it actually realised, so the comparison could only be between two "
            "channels' silence floors at two different doses — which is how this row "
            "reaches the opposite conclusion; see the class docstring",
            withheld=withheld_count,
            offered=injected.offered_frames,
        )

    runs, refusal = _paired_runs(
        recording,
        threshold_rms=threshold,
        min_run_frames=min_run_frames,
        max_gap_s=max_gap_s,
    )
    if refusal is not None:
        return refuse(
            f"{refusal}; under injected loss an unequal count may itself be the "
            "finding, so the comparison is withheld rather than guessed",
            withheld=withheld_count,
            offered=injected.offered_frames,
        )

    def region(run: SpeechRun) -> list[float]:
        return recording.arrivals.rms[run.start_index : run.end_index + 1]

    frame_s = recording.arrivals.frame_s
    transport = channel_stats(
        region(runs[injected_index]),
        source="transport",
        frame_s=frame_s,
        threshold_rms=threshold,
    )
    control_index = next(
        (
            index
            for index, utterance in enumerate(recording.utterances)
            if not utterance.withheld_source_index
        ),
        None,
    )
    if control_index is None:
        return refuse(
            "no loss-free arm was recorded in this session, so the transport's silent "
            "fraction under loss cannot be separated from its own silence floor",
            withheld=withheld_count,
            offered=injected.offered_frames,
        )
    control = channel_stats(
        region(runs[control_index]),
        source="transport",
        frame_s=frame_s,
        threshold_rms=threshold,
    )
    ladder = channel_stats(
        file_rms, source="file-ladder", frame_s=frame_s, threshold_rms=threshold
    )
    baseline = channel_stats(
        file_baseline_rms, source="file-ladder", frame_s=frame_s, threshold_rms=threshold
    )

    comparison = DegradationComparison(
        row=recording.row,
        reportable=True,
        refusal=None,
        fill=fill,
        injected_turn=injected.turn,
        control_turn=recording.utterances[control_index].turn,
        nominal_loss=injected.nominal_loss,
        file_realised_loss=file_realised_loss,
        withheld_frames=withheld_count,
        offered_frames=injected.offered_frames,
        transport=transport,
        control=control,
        file_ladder=ladder,
        file_baseline=baseline,
        jitter=jitter_stats(recording.arrivals),
        threshold_rms=threshold,
        relative_tolerance=relative_tolerance,
    )

    ratio = comparison.ratio
    comparison.agree = ratio is not None and abs(ratio - 1.0) <= relative_tolerance

    naive_transport = transport.silent_fraction or 0.0
    naive_ladder = ladder.silent_fraction or 0.0
    findings = [
        f"per unit of loss actually applied, the transport adds "
        f"{(comparison.transport_silence_per_loss or 0.0):.2f} of silent frames and the "
        f"file ladder with fill={fill!r} adds "
        f"{(comparison.ladder_silence_per_loss or 0.0):.2f} — a factor of "
        f"{ratio:.2f}x" if ratio is not None else "the normalised comparison is unavailable",
        (
            f"they do NOT agree within {relative_tolerance:.0%}: a rung of the file "
            f"ladder is not the loss rate a real transport would produce"
            if not comparison.agree
            else f"they agree within {relative_tolerance:.0%} at this rate and fill mode"
        ),
        f"the naive comparison — loaded silent fractions, no baseline and no dose "
        f"correction — reads {naive_ladder:.1%} against {naive_transport:.1%} and would "
        f"have concluded "
        + (
            "agreement"
            if abs(naive_ladder - naive_transport) <= 0.05
            else "disagreement"
        )
        + "; each side's own silence floor and the loss each actually applied are what "
        "move it",
    ]
    if comparison.jitter is not None and comparison.jitter.mean_abs_deviation_ms is not None:
        findings.append(
            "the file ladder cannot express jitter at all — a perturbed file has no "
            f"time axis — while the transport delivered with a mean inter-arrival "
            f"deviation of {comparison.jitter.mean_abs_deviation_ms:.1f} ms from the "
            f"nominal {comparison.jitter.nominal_frame_ms:.0f} ms frame period and "
            f"{comparison.jitter.late_rate} late frames"
        )
    comparison.findings = findings
    return comparison


# --------------------------------------------------------------------------- #
# Row 3 — connection lifecycle
# --------------------------------------------------------------------------- #

#: The four states a dropped-and-rejoined participant can leave the session in.
#: `recovered-turn-lost` is separate from `recovered` on purpose: a transport that
#: comes back while the utterance it was carrying does not is the failure a
#: caller actually experiences, and calling it a recovery is how it gets missed.
LifecycleVerdict = Literal[
    "recovered-turn-intact",
    "recovered-turn-lost",
    "no-recovery",
    "hung",
]


class LifecycleObservation(BaseModel):
    """What a mid-utterance drop and reconnect did to the turn.

    Every interval is named for the two events it spans, because "reconnect time"
    means at least three different things in a WebRTC session — signalling, track
    republish, and the far side actually receiving again — and they differ by
    hundreds of milliseconds.
    """

    model_config = ConfigDict(extra="forbid")

    row: str
    reportable: bool
    refusal: str | None = None
    verdict: LifecycleVerdict | None = None

    disconnect_s: float | None = None
    reconnect_s: float | None = None
    republish_s: float | None = None
    resubscribe_s: float | None = None
    last_audio_before_s: float | None = Field(
        default=None,
        description="Last audible frame delivered up to and including the drop.",
    )
    first_audio_after_s: float | None = Field(
        default=None, description="First audible frame delivered after the drop."
    )
    settle_s: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Deliberate harness quiet time after rejoining, included in "
            "`audio_silence_s`. Declared so that figure can be read as an upper "
            "bound rather than as the transport's own cost."
        ),
    )

    interrupted_turn: int | None = None
    frames_offered_before_drop: int = Field(default=0, ge=0)
    frames_pushed_before_drop: int = Field(default=0, ge=0)
    runs_before_drop: int = Field(default=0, ge=0)
    runs_straddling_drop: int = Field(
        default=0,
        ge=0,
        description=(
            "Runs that began before the drop and were still arriving at it — the "
            "turn that was in flight. A third bucket, because with only 'before' "
            "and 'after' the interrupted turn falls into neither and the row "
            "reports that nothing was in flight."
        ),
    )
    runs_after_reconnect: int = Field(default=0, ge=0)
    frames_after_drop_in_flight: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames of the interrupted turn that arrived *after* the sender had "
            "gone: audio already in the receiver's jitter buffer, which briefly "
            "outlives the connection that produced it."
        ),
    )
    attempts: int = Field(default=1, ge=1)
    findings: list[str] = Field(default_factory=list)

    @property
    def downtime_s(self) -> float | None:
        """Disconnect to the publisher being connected again."""
        if self.disconnect_s is None or self.reconnect_s is None:
            return None
        return self.reconnect_s - self.disconnect_s

    @property
    def transport_recovery_s(self) -> float | None:
        """Disconnect to the far side being subscribed again.

        The transport-level cost of the drop, and the figure the row asserts on,
        because it contains nothing the harness chose to do.
        """
        if self.disconnect_s is None or self.resubscribe_s is None:
            return None
        return self.resubscribe_s - self.disconnect_s

    @property
    def audio_silence_s(self) -> float | None:
        """Last audible frame to next audible frame — what the listener experienced.

        An **upper bound** on the transport's contribution: `settle_s` of it is
        quiet the harness deliberately left after rejoining so the receiver's
        jitter buffer could reach steady state. Reported anyway, because it is the
        only figure here measured from the listener's own stream rather than from
        connection state, and because the transport-level number understates the
        hole in the conversation by roughly a factor of two.
        """
        if self.last_audio_before_s is None or self.first_audio_after_s is None:
            return None
        return self.first_audio_after_s - self.last_audio_before_s

    def describe(self) -> str:
        if not self.reportable:
            return f"{self.row}: lifecycle NOT REPORTED — {self.refusal}"
        recovery = self.transport_recovery_s
        heard = self.audio_silence_s
        return (
            f"{self.row}: {self.verdict}; dropped mid-turn after "
            f"{self.frames_pushed_before_drop}/{self.frames_offered_before_drop} frames, "
            f"transport recovered in "
            f"{f'{recovery * 1000:.0f} ms' if recovery is not None else 'an unmeasured interval'}, "
            f"listener heard nothing for "
            f"{f'{heard * 1000:.0f} ms' if heard is not None else 'an unmeasured interval'} "
            f"({self.settle_s * 1000:.0f} ms of it deliberate), "
            f"{self.attempts} connection attempt(s)"
        )


def lifecycle_observation(
    recording: TransportRecording,
    *,
    threshold_rms: float | None = None,
    min_run_frames: int = DEFAULT_MIN_RUN_FRAMES,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
    settle_s: float = 0.0,
) -> LifecycleObservation:
    """Decide whether the agent recovered, lost the turn, or hung.

    The verdict is read off the lifecycle records and the arrival runs, by
    position:

    * **no-recovery** — nothing connected after the disconnect.
    * **hung** — it reconnected and republished, but no audio ever arrived again.
      A live-lock: the session looks healthy and the listener hears nothing.
    * **recovered-turn-lost** — audio arrived again, but the utterance that was in
      flight was truncated and never re-delivered. The transport recovered; the
      turn did not.
    * **recovered-turn-intact** — the interrupted utterance was itself completed.

    The distinction between the last two is the row's whole point. A reconnect
    metric that stops at "the participant came back" scores a lost sentence as a
    success.
    """
    threshold = recording.onset_threshold_rms if threshold_rms is None else threshold_rms
    disconnects = recording.events_of("disconnected")

    def refuse(reason: str) -> LifecycleObservation:
        return LifecycleObservation(row=recording.row, reportable=False, refusal=reason)

    if not disconnects:
        return refuse(
            "no disconnect was recorded, so this session never exercised the lifecycle; "
            "the row cannot report a recovery it did not interrupt"
        )

    # Position, not timestamps. The lifecycle list is in observation order (the
    # recording's own validator enforces it), so "after the drop" is the slice
    # following the drop rather than a comparison between two clocks' readings.
    drop_position = next(
        index for index, event in enumerate(recording.lifecycle) if event.kind == "disconnected"
    )
    drop = recording.lifecycle[drop_position]
    tail = recording.lifecycle[drop_position + 1 :]
    later_connects = [event for event in tail if event.kind == "connected"]
    republish = [event for event in tail if event.kind == "published"]
    resubscribe = [event for event in tail if event.kind == "subscribed"]

    if drop.arrival_index is None:
        return refuse(
            "the disconnect record carries no arrival_index, so the delivered audio "
            "cannot be split into before and after the drop without comparing "
            "timestamps across two independent streams; the verdict is withheld"
        )

    runs = speech_runs(
        recording.arrivals,
        threshold_rms=threshold,
        min_frames=min_run_frames,
        max_gap_s=max_gap_s,
    )
    # Three buckets, not two. A run that began before the drop and was still
    # arriving at it is the turn that was in flight, and it belongs to neither
    # "before" nor "after": with only two buckets it disappears, and the row
    # reports that nothing was interrupted.
    before = [run for run in runs if run.end_index < drop.arrival_index]
    straddling = [
        run
        for run in runs
        if run.start_index < drop.arrival_index <= run.end_index
    ]
    after = [run for run in runs if run.start_index >= drop.arrival_index]
    in_flight = sum(run.end_index - drop.arrival_index + 1 for run in straddling)

    interrupted = recording.utterances[0] if recording.utterances else None
    attempts = max((event.attempt for event in recording.events_of("connected")), default=1)

    if not later_connects:
        verdict: LifecycleVerdict = "no-recovery"
    elif not after:
        verdict = "hung"
    else:
        # The interrupted utterance was truncated at the drop. It counts as intact
        # only if the frames it never got to push were delivered afterwards, which
        # this harness never arranges — it republishes a fresh turn instead, which
        # is exactly what a production agent does.
        verdict = "recovered-turn-lost"

    last_before = max(
        (run.offset_s for run in [*before, *straddling]), default=None
    )
    first_after = min((run.onset_s for run in after), default=None)

    findings: list[str] = []
    if interrupted is not None:
        findings.append(
            f"turn {interrupted.turn} had pushed {interrupted.pushes.n} of "
            f"{interrupted.offered_frames} frames when the transport went away; the "
            "remainder was never sent and nothing retransmits it"
        )
    if in_flight:
        findings.append(
            f"{in_flight} frame(s) of the interrupted turn arrived after the sender "
            "had already gone — audio that was sitting in the receiver's jitter "
            "buffer, which briefly outlives the connection that filled it"
        )
    if last_before is not None and first_after is not None:
        findings.append(
            f"the listener heard nothing for "
            f"{(first_after - last_before) * 1000:.0f} ms, of which "
            f"{settle_s * 1000:.0f} ms was quiet the harness left deliberately; the "
            f"transport-level figure "
            f"({(resubscribe[0].ts_s - drop.ts_s) * 1000:.0f} ms from drop to "
            "re-subscription) understates the hole in the conversation"
            if resubscribe
            else "no re-subscription was recorded, so the transport-level recovery "
            "figure is unavailable"
        )
    if verdict == "recovered-turn-lost":
        findings.append(
            "the connection recovered and audio flowed again, but the utterance in "
            "flight died at the drop: a reconnect metric that stops at 'the "
            "participant came back' scores a lost sentence as a success"
        )
    if verdict == "hung":
        findings.append(
            "the participant reconnected and republished, and no audio ever reached "
            "the listener again — the session reports healthy while the call is dead"
        )

    return LifecycleObservation(
        row=recording.row,
        reportable=True,
        refusal=None,
        verdict=verdict,
        disconnect_s=drop.ts_s,
        reconnect_s=later_connects[0].ts_s if later_connects else None,
        republish_s=republish[0].ts_s if republish else None,
        resubscribe_s=resubscribe[0].ts_s if resubscribe else None,
        last_audio_before_s=last_before,
        first_audio_after_s=first_after,
        settle_s=settle_s,
        interrupted_turn=interrupted.turn if interrupted else None,
        frames_offered_before_drop=interrupted.offered_frames if interrupted else 0,
        frames_pushed_before_drop=interrupted.pushes.n if interrupted else 0,
        runs_before_drop=len(before),
        runs_straddling_drop=len(straddling),
        runs_after_reconnect=len(after),
        frames_after_drop_in_flight=in_flight,
        attempts=attempts,
        findings=findings,
    )
