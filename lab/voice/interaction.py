"""The two things a text harness cannot see: real silence, and real overlap.

WHAT THIS DEMONSTRATES
----------------------
Everything else in this repository could, at a push, be done over text. Not this.
A text transcript has no silence in it and no simultaneity: turns are discrete,
adjacent and non-overlapping by construction. So two entire classes of production
failure are *structurally invisible* to a text harness, and both of them have cost
real teams real weeks.

    silence misattribution   a call is labelled "silence timed out" when the
                             caller was in fact talking
    barge-in                 the caller interrupts and the agent keeps going

This module measures both, and it can only do so because the audio was
synthesised here: the harness knows exactly how long the pause was, and exactly
where speech was present.

REFERENCE BUG: THE SILENCE THAT WAS NOT SILENCE
-----------------------------------------------
A production voice agent ended calls with `callEndReason = "silence-timed-out"`.
The label was believed, because it is a plausible and self-describing label, and
the investigation went looking for quiet callers. It was misdiagnosed for weeks.

The real cause was two things compounding:

1.  Under `turn_detection="stt"`, the voice-activity detector was driving
    `user_state` — so the agent's belief about whether the user was speaking came
    from a component that was not supposed to be authoritative, and which
    disagreed with the transcriber. The upstream fix was unmerged.
2.  `away_timeout` was 6 seconds, aggressive enough that ordinary thinking pauses
    reached it.

So one label covered two completely different events, requiring opposite
responses: *the caller really did go quiet* (raise the timeout, or prompt) and
*the caller was speaking and we could not tell* (fix the turn detector; changing
the timeout would only delay the same bug).

**Distinguishing them requires knowing whether there was speech in the window.**
Production cannot know — it has only the detector whose failure is the thing in
question, which is why the diagnosis took weeks and why the eventual answer came
from reading an upstream issue rather than from the telemetry. An audio harness
*can* know, because it synthesised the audio and can measure its energy
independently of any detector. `attribute_silence` is that comparison, and the
verdict vocabulary has three values instead of one label because there were
always three situations.

REFERENCE BUG: THE INTERRUPTION EVENTS NOBODY USED
--------------------------------------------------
`lab.trace.schema` has defined `interruption_started` and
`interruption_acknowledged` from the beginning. Nothing emitted them and nothing
consumed them. `lab.voice.metrics` says so explicitly — barge-in latency is
"deliberately absent: it needs the `interruption_*` events" — and
`lab.voice.adapter` explains why its half-duplex turn loop cannot produce them.

Both of those statements were honest and both left a real metric on the floor.
The events were unusable *for a turn-based adapter*, which is not the same as
unusable. Given two clips and the instant the second one starts, the overlap is
arithmetic — and for a live in-call coaching product, barge-in is not a nicety:
an adviser talking over a suggestion is the product's core interaction, and an
agent that does not yield is one that talks over a client.

So this module constructs the overlap explicitly, emits the two events, and
reports the latency. It does not pretend the adapter's turn loop discovered them.

WHY ENERGY AND NOT A VAD
------------------------
Speech presence here is measured with a short-time RMS threshold, not with a
voice-activity detector. That is deliberate to the point of being the whole
argument: the failure under investigation *is a VAD failure*. Using a VAD to
adjudicate a VAD's mistake would be measuring the instrument with itself. RMS
over a synthesised clip is crude, has no opinions, and is right about the
question actually being asked — "was there sound here?" — which is the only
question needed to tell the two silence verdicts apart.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from lab.voice.engines.base import Audio, DEFAULT_SAMPLE_RATE

__all__ = [
    "DEFAULT_AWAY_TIMEOUT_S",
    "DEFAULT_FRAME_MS",
    "DEFAULT_SPEECH_RMS",
    "PRODUCTION_AWAY_TIMEOUT_S",
    "BargeIn",
    "BargeInReport",
    "SilenceVerdict",
    "SilenceAttribution",
    "SpeechActivity",
    "attribute_silence",
    "barge_in",
    "barge_in_report",
    "emit_barge_in",
    "insert_pause",
    "pause_for_silence",
    "speech_activity",
]

#: The production value that was too aggressive. Kept as a named constant so a
#: test can demonstrate the failure at the setting that actually shipped, rather
#: than at a setting chosen to make the demonstration work.
PRODUCTION_AWAY_TIMEOUT_S: float = 6.0

#: A less aggressive default. Not presented as the right answer — the point of
#: the reference bug is that tuning this number was the *wrong* fix — but it is
#: the value at which an ordinary thinking pause stops tripping the timeout.
DEFAULT_AWAY_TIMEOUT_S: float = 10.0

#: Analysis frame for the RMS envelope. 20 ms is short enough to place the edge
#: of a pause to within a fifth of a syllable and long enough that one glottal
#: closure does not read as silence.
DEFAULT_FRAME_MS: float = 20.0

#: Frame RMS above which a frame counts as speech. Synthesised speech sits well
#: above this and digital silence sits at exactly zero, so the threshold is not a
#: delicate choice; it exists so that dither or a noise perturbation floor does
#: not read as speech.
DEFAULT_SPEECH_RMS: float = 0.01

#: What one "silence timed out" label can actually mean. Three values, because
#: there were always three situations and the production label had one name.
#:
#:     "caller_silent"      the caller genuinely stopped speaking for at least the
#:                          threshold. The label was correct. Remedy: raise the
#:                          timeout, or prompt the caller.
#:     "vad_false_silence"  the timeout fired while speech was present in the
#:                          window. The label was WRONG. Remedy: fix turn
#:                          detection; changing the timeout only postpones it.
#:     "would_not_fire"     the longest silent run is below the threshold, so this
#:                          configuration would not have ended the call at all.
SilenceVerdict = Literal["caller_silent", "vad_false_silence", "would_not_fire"]


class SpeechActivity(BaseModel):
    """Where sound is, measured from the samples rather than believed from a label."""

    model_config = ConfigDict(extra="forbid")

    duration_s: float = Field(ge=0.0)
    speech_s: float = Field(ge=0.0)
    longest_silence_s: float = Field(ge=0.0)
    longest_silence_start_s: float = Field(ge=0.0)
    trailing_silence_s: float = Field(
        default=0.0,
        ge=0.0,
        description="Silence at the very end of the clip. See `pause_for_silence`.",
    )
    frame_ms: float
    threshold_rms: float

    @property
    def silence_s(self) -> float:
        return max(0.0, self.duration_s - self.speech_s)

    @property
    def has_speech(self) -> bool:
        return self.speech_s > 0.0

    def describe(self) -> str:
        return (
            f"{self.duration_s:.3f}s of audio: {self.speech_s:.3f}s speech, "
            f"longest silent run {self.longest_silence_s:.3f}s at "
            f"{self.longest_silence_start_s:.3f}s "
            f"(RMS>{self.threshold_rms} over {self.frame_ms:.0f}ms frames)"
        )


def speech_activity(
    audio: Audio,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    frame_ms: float = DEFAULT_FRAME_MS,
    threshold_rms: float = DEFAULT_SPEECH_RMS,
) -> SpeechActivity:
    """Short-time RMS envelope, reduced to the facts the silence verdict needs.

    Returns the total speaking time and, more importantly, the **longest
    contiguous silent run** and where it starts. The longest run is what a timeout
    actually races against: total silence spread over a call in half-second gaps
    never trips a 6 second timer, and a harness that compared totals would call a
    chatty caller silent.
    """
    samples = np.asarray(audio, dtype=np.float64)
    if samples.size == 0 or sample_rate <= 0:
        return SpeechActivity(
            duration_s=0.0,
            speech_s=0.0,
            longest_silence_s=0.0,
            longest_silence_start_s=0.0,
            frame_ms=frame_ms,
            threshold_rms=threshold_rms,
        )
    frame = max(1, int(round(sample_rate * frame_ms / 1000.0)))
    usable = (samples.size // frame) * frame
    if usable == 0:
        frames = samples.reshape(1, -1)
    else:
        frames = samples[:usable].reshape(-1, frame)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    voiced = rms > threshold_rms
    frame_s = frame / float(sample_rate)

    longest = 0
    longest_start = 0
    run = 0
    for index, is_voiced in enumerate(voiced):
        if is_voiced:
            run = 0
            continue
        run += 1
        if run > longest:
            longest = run
            longest_start = index - run + 1
    trailing = 0
    for is_voiced in reversed(voiced.tolist()):
        if is_voiced:
            break
        trailing += 1
    return SpeechActivity(
        duration_s=samples.size / float(sample_rate),
        speech_s=float(np.count_nonzero(voiced)) * frame_s,
        longest_silence_s=longest * frame_s,
        longest_silence_start_s=longest_start * frame_s,
        trailing_silence_s=trailing * frame_s,
        frame_ms=frame_ms,
        threshold_rms=threshold_rms,
    )


def pause_for_silence(
    audio: Audio,
    *,
    target_silence_s: float,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    frame_ms: float = DEFAULT_FRAME_MS,
    threshold_rms: float = DEFAULT_SPEECH_RMS,
) -> tuple[Audio, float]:
    """Pad `audio` so its longest silent run is exactly `target_silence_s`.

    Returns the padded clip and the pause actually appended.

    **This exists because a synthesised clip is not silent-terminated at zero
    length.** Measured on the committed ElevenLabs clips (`eleven_flash_v2_5`,
    pcm_16000): every one ends with about **0.200 s** of trailing silence. Append a
    declared 5.9 second pause to such a clip and the resulting silent run is 6.1
    seconds — over the 6 second production threshold.

    That 200 ms is small and it points the wrong way. A threshold test built on
    `insert_pause(clip, seconds=5.9)` and asserting "does not fire" would fail; the
    tempting repair is to widen the tolerance, and the result would be a suite that
    could no longer tell 5.9 from 6.1 and would therefore certify a timeout
    threshold it had never actually measured. The boundary is the whole subject of
    the test, so the padding has to be computed against the clip in hand rather
    than assumed.

    Use `insert_pause` when the question is "add a pause of this length". Use this
    when the question is "make the silence reach this length", which is what a
    threshold assertion is really asking.
    """
    if target_silence_s < 0:
        raise ValueError(f"target silence must not be negative, got {target_silence_s!r}")
    existing = speech_activity(
        audio, sample_rate=sample_rate, frame_ms=frame_ms, threshold_rms=threshold_rms
    ).trailing_silence_s
    pause = max(0.0, target_silence_s - existing)
    return insert_pause(audio, seconds=pause, sample_rate=sample_rate), pause


def insert_pause(
    audio: Audio,
    *,
    seconds: float,
    at_s: float | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> Audio:
    """Insert `seconds` of digital silence, returning the lengthened clip.

    `at_s` defaults to the end of the clip, which is where a caller's thinking
    pause lives — after they have finished a sentence and before they start the
    next one. That is the position the away-timeout races against.

    Digital silence rather than room tone, because the declared duration has to be
    exactly recoverable by measurement for the threshold assertion to mean
    anything. Room tone would make the measured pause a function of the noise
    floor and the RMS threshold, and the test would then be measuring its own
    parameters.
    """
    if seconds < 0:
        raise ValueError(f"pause must not be negative, got {seconds!r}")
    samples = np.asarray(audio, dtype=np.float64)
    pad = np.zeros(int(round(seconds * sample_rate)), dtype=np.float64)
    if at_s is None:
        return np.concatenate([samples, pad])
    index = max(0, min(samples.size, int(round(at_s * sample_rate))))
    return np.concatenate([samples[:index], pad, samples[index:]])


class SilenceAttribution(BaseModel):
    """Whether a silence timeout would fire, and whether its label would be true.

    The two questions are separate and both matter. "Did it fire at the right
    threshold?" is a timing question about the configuration. "Was the reason
    right?" is a diagnostic question about the turn detector, and it is the one
    that cost weeks — because the answer was assumed rather than measured.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: SilenceVerdict
    threshold_s: float
    declared_pause_s: float | None = None
    measured_silence_s: float
    fires: bool
    fires_at_s: float | None = None
    speech_present_in_window: bool
    reason_label: str = "silence-timed-out"
    reason_is_accurate: bool

    @property
    def declared_matches_measured(self) -> bool:
        """True when the pause we injected is the pause the envelope found.

        The instrument checking itself. If a declared 8 second pause measures as
        5.9, then the threshold assertion below is testing the harness's padding
        arithmetic rather than the timeout, and any conclusion drawn from it is
        void. Tolerance is two analysis frames.
        """
        if self.declared_pause_s is None:
            return True
        return abs(self.measured_silence_s - self.declared_pause_s) <= 2 * (
            DEFAULT_FRAME_MS / 1000.0
        )

    def describe(self) -> str:
        if self.verdict == "would_not_fire":
            return (
                f"no timeout: longest silent run {self.measured_silence_s:.2f}s is under "
                f"the {self.threshold_s:.1f}s threshold"
            )
        if self.verdict == "caller_silent":
            return (
                f"timeout at {self.threshold_s:.1f}s and the label is CORRECT: "
                f"{self.measured_silence_s:.2f}s of measured silence with no speech in "
                "the window. Remedy: raise the timeout or prompt the caller"
            )
        return (
            f"timeout at {self.threshold_s:.1f}s and the label "
            f"{self.reason_label!r} is WRONG: speech was present in the window. The "
            "caller was talking and the agent could not tell. Remedy: fix turn "
            "detection — raising the timeout only postpones this"
        )


def attribute_silence(
    audio: Audio,
    *,
    threshold_s: float = PRODUCTION_AWAY_TIMEOUT_S,
    declared_pause_s: float | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    speech_during_timeout: bool = False,
    frame_ms: float = DEFAULT_FRAME_MS,
    threshold_rms: float = DEFAULT_SPEECH_RMS,
) -> SilenceAttribution:
    """Decide whether a silence timeout fires, and whether its reason is true.

    Args:
        audio: The caller-side clip, including any injected pause.
        threshold_s: The agent's away timeout. Defaults to the 6 seconds that
            shipped.
        declared_pause_s: The pause this clip was built with, if any. Supplied so
            the measurement can be checked against the intent — see
            `SilenceAttribution.declared_matches_measured`.
        sample_rate: Rate of `audio`.
        speech_during_timeout: Models the turn detector's failure. True means the
            agent's `user_state` flipped to away *while the caller was audibly
            speaking* — the unmerged-upstream-fix condition. It is an input rather
            than something inferred, because it describes the agent's internal
            belief, which no amount of listening to the audio can reveal. What the
            audio *can* reveal is whether that belief was false, which is the
            comparison this function performs.
        frame_ms, threshold_rms: Envelope parameters.

    Returns:
        The verdict, the timing, and whether the label would have been accurate.
    """
    activity = speech_activity(
        audio, sample_rate=sample_rate, frame_ms=frame_ms, threshold_rms=threshold_rms
    )
    # Two independent ways the timer can expire: a genuine silent run that reaches
    # the threshold, or a detector that claims one that is not there.
    genuine = activity.longest_silence_s >= threshold_s
    fires = genuine or speech_during_timeout
    if not fires:
        verdict: SilenceVerdict = "would_not_fire"
    elif speech_during_timeout:
        verdict = "vad_false_silence"
    else:
        verdict = "caller_silent"
    return SilenceAttribution(
        verdict=verdict,
        threshold_s=threshold_s,
        declared_pause_s=declared_pause_s,
        measured_silence_s=activity.longest_silence_s,
        fires=fires,
        fires_at_s=(
            activity.longest_silence_start_s + threshold_s if fires and genuine else None
        ),
        speech_present_in_window=speech_during_timeout,
        reason_is_accurate=(verdict == "caller_silent"),
    )


class BargeIn(BaseModel):
    """One interruption: when the caller started over the agent, and when it stopped.

    `yielded` is the outcome that matters to a live in-call coaching product. An
    agent that keeps speaking through an interruption is talking over the person
    it is meant to be helping, and no amount of good content redeems that.
    """

    model_config = ConfigDict(extra="forbid")

    agent_started_s: float
    agent_would_end_s: float
    caller_started_s: float
    agent_stopped_s: float | None = None
    overlap_s: float = Field(ge=0.0)

    @property
    def yielded(self) -> bool:
        """True when the agent stopped before it would have finished anyway."""
        return self.agent_stopped_s is not None and (
            self.agent_stopped_s < self.agent_would_end_s
        )

    @property
    def latency_s(self) -> float | None:
        """Time from the caller starting to the agent stopping. The barge-in metric.

        None when the agent never stopped — and None is the honest value there. A
        non-yield is not a slow yield, and folding it into a latency distribution
        as some large number would let a row that failed outright be averaged with
        rows that merely lagged.
        """
        if self.agent_stopped_s is None:
            return None
        return max(0.0, self.agent_stopped_s - self.caller_started_s)

    def describe(self) -> str:
        if self.agent_stopped_s is None:
            return (
                f"barge-in at {self.caller_started_s:.3f}s: agent did NOT yield, "
                f"{self.overlap_s:.3f}s of overlap — it talked over the caller"
            )
        latency = self.latency_s or 0.0
        state = "yielded" if self.yielded else "finished anyway"
        return (
            f"barge-in at {self.caller_started_s:.3f}s: agent {state} after "
            f"{latency * 1000:.0f}ms, {self.overlap_s:.3f}s of overlap"
        )


def barge_in(
    *,
    agent_started_s: float,
    agent_duration_s: float,
    caller_started_s: float,
    agent_stopped_s: float | None = None,
) -> BargeIn:
    """Construct one barge-in from two clip timings. Arithmetic, not detection.

    Args:
        agent_started_s: When the agent's audio began.
        agent_duration_s: How long the agent's clip would run uninterrupted.
        caller_started_s: When the caller started speaking over it.
        agent_stopped_s: When the agent's audio actually stopped. `None` means it
            never did — it played to the end regardless.

    Raises:
        ValueError: if the caller did not start during the agent's playback. That
            is not an interruption, and returning a zero-overlap `BargeIn` for it
            would put non-events into a barge-in distribution.
    """
    would_end = agent_started_s + agent_duration_s
    if not (agent_started_s <= caller_started_s < would_end):
        raise ValueError(
            f"caller started at {caller_started_s:.3f}s, outside the agent's playback "
            f"[{agent_started_s:.3f}, {would_end:.3f}). That is a normal turn, not a "
            "barge-in; recording it as one would populate the metric with non-events"
        )
    stopped = would_end if agent_stopped_s is None else agent_stopped_s
    return BargeIn(
        agent_started_s=agent_started_s,
        agent_would_end_s=would_end,
        caller_started_s=caller_started_s,
        agent_stopped_s=agent_stopped_s,
        overlap_s=max(0.0, min(stopped, would_end) - caller_started_s),
    )


# --------------------------------------------------------------------------- #
# Emitting the events, and reading them back
# --------------------------------------------------------------------------- #


def emit_barge_in(builder: Any, event: BargeIn, *, turn: int, engine: str | None = None) -> None:
    """Write a barge-in onto a trace as `interruption_started` / `_acknowledged`.

    These are the two event kinds `lab.trace.schema` has always defined and that
    nothing has ever emitted. They go through `TraceBuilder.emit` rather than a
    named method because there is no named method — which is itself the evidence
    that nothing used them.

    `interruption_acknowledged` is emitted **only when the agent actually
    stopped**. An acknowledgement event for an agent that talked straight through
    would be a lie in the trace, and a downstream metric pairing the two kinds
    would then compute a latency for a yield that never happened. A missing
    acknowledgement is the correct record of a missing yield.
    """
    builder.emit(
        "interruption_started",
        "caller",
        ts=event.caller_started_s,
        engine=engine,
        turn=turn,
        agent_started_s=round(event.agent_started_s, 6),
        agent_would_end_s=round(event.agent_would_end_s, 6),
    )
    if event.agent_stopped_s is None:
        return
    builder.emit(
        "interruption_acknowledged",
        "agent",
        ts=event.agent_stopped_s,
        engine=engine,
        turn=turn,
        overlap_s=round(event.overlap_s, 6),
        yielded=event.yielded,
    )


class BargeInReport(BaseModel):
    """Barge-in outcomes over a trace. The metric the framework had no consumer for.

    Denominator-first, as everywhere in this repo: `interruptions` is beside
    `yields`, so `yield_rate` can never be read as a naked percentage. The
    non-yields are listed rather than counted, because "the agent talked over the
    caller" is a row somebody needs to go and listen to.
    """

    model_config = ConfigDict(extra="forbid")

    interruptions: int = 0
    yields: int = 0
    latencies_s: list[float] = Field(default_factory=list)
    talked_over_turns: list[int] = Field(default_factory=list)

    @property
    def yield_rate(self) -> float | None:
        return (self.yields / self.interruptions) if self.interruptions else None

    @property
    def median_latency_s(self) -> float | None:
        if not self.latencies_s:
            return None
        ordered = sorted(self.latencies_s)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def describe(self) -> str:
        if not self.interruptions:
            return "barge-in: no interruptions in this trace"
        median = self.median_latency_s
        tail = (
            ""
            if not self.talked_over_turns
            else f"; talked over the caller on turn(s) {self.talked_over_turns}"
        )
        timing = "no yields to time" if median is None else f"median yield {median * 1000:.0f}ms"
        return (
            f"barge-in: {self.yields}/{self.interruptions} interruption(s) yielded, "
            f"{timing}{tail}"
        )


def barge_in_report(trace: Any) -> BargeInReport:
    """Read the `interruption_*` events back off a trace and score them.

    Pairing is by **event-stream position**, not by timestamp: the started events
    in order, matched against the acknowledgements in order. That is the ordering
    rule the rest of `lab/voice` follows — a deliberate past fix — and it matters
    here specifically because an acknowledgement shares a timestamp with the end
    of the agent's clip, so a timestamp sort could place it either side.

    A started event with no acknowledgement after it is a non-yield, which is why
    the walk is a merge over positions rather than a zip: zipping would silently
    pair turn 3's start with turn 5's acknowledgement and report a plausible
    latency for an interruption that was ignored.
    """
    started: list[Any] = []
    acknowledged: dict[int, Any] = {}
    for event in trace.events:
        if event.kind == "interruption_started":
            started.append(event)
        elif event.kind == "interruption_acknowledged":
            turn = event.get("turn")
            if turn is not None:
                acknowledged[int(turn)] = event
    report = BargeInReport(interruptions=len(started))
    for event in started:
        turn = event.get("turn")
        ack = acknowledged.get(int(turn)) if turn is not None else None
        if ack is None:
            if turn is not None:
                report.talked_over_turns.append(int(turn))
            continue
        report.yields += 1
        report.latencies_s.append(max(0.0, float(ack.ts) - float(event.ts)))
    return report
