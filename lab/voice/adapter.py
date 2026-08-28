"""The real audio adapter: speech in, speech out, and two refusals.

WHAT THIS DEMONSTRATES
----------------------
Everything else in `lab` measures a conversation. This module produces one out of
actual sound: a scripted line becomes synthesised speech, the speech is degraded
on purpose, the degraded audio is written to a file and transcribed, the
transcript is what the system under test actually receives, and the reply is
synthesised back into audio. The trace it emits is the same `Trace` the text path
emits, so every contract, judge, metric and report in this repo consumes it
without knowing the difference.

Two behaviours are the point of the file, and both are refusals:

1.  **It runs the timing calibration gate before a session and refuses to report
    latency if the gate failed.** Not a warning in a log — `audio_latency_report`
    raises. The gate verdict is written into the trace, so the refusal survives
    serialisation: a reviewer who downloads a trace file and never runs the
    harness gets the same refusal from the same data.
2.  **It refuses to report word error rate against reference transcripts.** With
    no STT engine installed, the committed fixtures carry the known ground truth
    in place of a transcription. WER against ground truth is exactly zero by
    construction, for every clip, at every SNR — a confident, fabricated 0.0%.
    `audio_wer_report` reads the provenance out of the trace and refuses.

An eval harness earns trust by declining to print numbers it cannot justify, and
those declines have tests.

WHY HALF-DUPLEX, FILE-BASED, PRE-SYNTHESISED AND POST-HOC
---------------------------------------------------------
This is the central design decision, and it is a trade, not a shortcut. A duplex
streaming adapter — caller audio and agent audio flowing concurrently over a real
transport — would buy one thing this cannot do: *discover* a barge-in rather
than be handed its timings. It would cost three things this depends on.

**1. Attributable latency.** The system under test here is text-in, text-out, so
the STT and TTS legs belong to the *harness*, not to the agent. In a streaming
duplex design those legs are interleaved with the agent's own work and the
boundary between "what the agent spent" and "what we spent" becomes a matter of
opinion. File-based and half-duplex makes it a matter of arithmetic: synthesis
happens strictly before the request leaves, transcription happens strictly before
the request leaves, encoding and decoding happen strictly outside the window, and
what remains between the two clock reads is the agent alone. That is the same
discipline `lab.voice.calibration` validates, applied to a pipeline with three
more stages in it.

**2. Reproducibility.** Pre-synthesised caller audio is a file with a content
digest. The perturbation applied to it is seeded and described. The transcript
recovered from it is keyed by that digest. Run the suite twice and the bytes are
identical, which means a failure is reproducible and a regression is real. A
live duplex session is never bit-identical twice, so every result carries an
unquantified variance that gets attributed to the agent.

**3. Billing shape.** Batch, post-hoc transcription is the cheap shape: one
request per utterance, no open socket, no per-minute streaming premium, no
partial hypotheses being billed and discarded. It is also the *retryable* shape —
a rate limit costs a retry rather than a lost session — and the shape whose cost
is predictable from the corpus before the run starts. A streaming-realtime
evaluation of 55 scenarios at k=5 is a bill nobody estimated.

So: half-duplex, and honest about it. Barge-in here is constructed, not
discovered: `lab.voice.interaction.emit_barge_in` writes `interruption_started`
and `interruption_acknowledged` and `barge_in_report` scores them, but from
timings a scenario hands in — *this* adapter emits neither kind, because its turn
loop plays the agent and then the caller and no moment exists in which both are
sounding. A v2 duplex adapter can discover one and emit it without a schema
change (see `lab.trace.schema`).

THE TURN, INSTANT BY INSTANT
----------------------------
Every one of these is either a real clock read or an exact arithmetic
consequence of one. Nothing is invented::

    synthesise caller line                       harness cost (synthesis_s)
    apply the perturbation chain                 harness cost, seeded, described
    write the clip to a file                     harness cost
    audio_emitted (caller)                       clip size, duration, chain
    advance the clock by the clip duration       the caller is speaking
    transcribe the clip                          harness cost (transcribe_s)
    ---------------------------------------------------------------------------
    t0  = clock.now()                            BOUNDARY OUT
    reply = agent(transcribed_text)              the system under test, alone
    t1  = clock.now()                            BOUNDARY IN
    ---------------------------------------------------------------------------
    caller_utterance      ts=t0                  what the caller really said
    transcript_in         ts=t0                  what the agent really heard
    tool_call/tool_result interpolated in (t0,t1), flagged ts_estimated
    agent_audio_first_byte ts=t1                 the response reached the harness
    transcript_out        ts=t1                  text handed to synthesis
    synthesise the reply                         harness cost (synthesis_s)
    advance the clock to t1 + reply duration     the agent is speaking
    audio_emitted (agent) · agent_audio_complete ts=t1+duration
    agent_utterance                              the harness finished the turn

Three consequences worth stating out loud.

*`agent_audio_first_byte` is stamped at `t1`, not after synthesis.* With a
text-in/text-out SUT the TTS is ours, and charging our synthesiser to the agent
is precisely the error the calibration module exists to prevent. The synthesis
cost is recorded as its own payload figure (`synthesis_s`) on the audio events,
so it is visible — which is the only way it can be reliably excluded.

*`agent_audio_complete - agent_audio_first_byte` is exactly the audible length of
the reply,* because the clock is advanced by that length. So
`lab.voice.metrics.speaking_times` returns real speaking time and not a synthesis
duration, and `completion_latencies` is response time plus speaking time, which
is what a caller experiences.

*`caller_utterance` is emitted before `transcript_in`, which is the opposite of
the text driver's order, and it is deliberate.* `lab.voice.wer.trace_wer` pairs
`caller_utterance -> transcript_in` with `Trace.event_pairs`, which walks the
event list and greedily takes the next closer. With transcript-first ordering
every pair is off by one turn — turn N's reference against turn N+1's hypothesis.
On the text path the two strings are identical so nothing is visible; on the audio
path they differ, and a silently misaligned WER is the worst possible outcome for
the one metric this adapter exists to produce. Both events carry `ts=t0`, so the
trace stays ordered and no timing figure changes.

GRACEFUL DEGRADATION
--------------------
No models installed is the normal case for a fresh clone, and it is not an error.
`FixtureTTS` and `RecordedSTT` replay committed fixtures; the whole path — chain,
digests, files, events, gate — runs with no downloads, no network and no keys. Ask
for a real engine that is absent and you get an `EngineUnavailable` naming
`scripts/setup_audio.sh` and the command that fixes it, raised at the point of
use, never an `ImportError` from three frames down.

WHAT THIS DOES NOT DO
---------------------
*   No barge-in *discovery*, for the reasons above: this adapter emits neither
    interruption kind. The constructed measurement lives in
    `lab.voice.interaction`.
*   No agent-side loop-back transcription by default. Transcribing our own
    synthesis of the agent's known words measures our TTS's intelligibility,
    which is a real question and not a question about the agent; it is available
    behind `transcribe_agent_audio=True` and lands on the `agent_audio_complete`
    payload rather than inventing an event kind.
*   No voice-activity detection, no endpointing, no turn-taking model. The turn
    boundaries are the script's, which is what makes them reproducible and also
    what makes this adapter unable to tell you anything about endpointing.
*   No echo, no double-talk, no jitter buffer. `lab.voice.perturb` models the
    channel; nothing here models the network stack.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.clock import Clock, FakeClock
from lab.simulator.driver import (
    DEFAULT_MAX_TURNS,
    AgentUnderTest,
    Caller,
    Handoff,
    ToolInvocation,
    coerce_turn,
)

# A private import, deliberately. `_WindowStamper` is the implementation of the
# trace-ordering invariant for events the adapter can only place *inside* the
# measured window: it hands out evenly-spaced estimates, flags them
# `ts_estimated`, clamps observed timestamps into the window, and enforces a
# running maximum. Re-implementing it here would create a second copy of that
# invariant to keep in agreement forever, and the first time they diverged the
# symptom would be an unordered trace produced by the harness itself. Sharing one
# implementation is worth reaching across an underscore for.
from lab.simulator.driver import _WindowStamper
from lab.trace.build import TraceBuilder
from lab.trace.io import read_jsonl
from lab.trace.schema import EventKind, Trace
from lab.voice.calibration import (
    DEFAULT_DELAYS_S,
    DEFAULT_REPEATS,
    CalibrationReport,
    CalibrationTolerance,
    run_calibration,
)
from lab.voice.engines.audiofile import write_audio
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    STTEngine,
    SynthesisResult,
    Transcription,
    TTSEngine,
)
from lab.voice.metrics import ResponseLatencyReport, response_latency_report
from lab.voice.perturb import PerturbationDescriptor, apply_chain, perturbation_payload
from lab.voice.wer import CorpusWER, corpus_wer, normalise, trace_wer

__all__ = [
    "AUDIO_ADAPTER",
    "REPLAY_ADAPTER",
    "GATE_PAYLOAD_KEY",
    "TTS_OWNER_KEY",
    "TTS_OWNER_HARNESS",
    "TTS_OWNER_SUT",
    "GateVerdict",
    "LatencyGate",
    "LatencyUnproven",
    "DeliveredLatencyUnavailable",
    "WERUnproven",
    "AudioTurn",
    "AudioAdapter",
    "FieldCapture",
    "ReadBackReport",
    "SilentCorrection",
    "SilentCorrectionReport",
    "TTSIntelligibilityProbe",
    "audio_delivered_latency_report",
    "audio_latency_report",
    "audio_wer_report",
    "latency_gate_verdict",
    "readback_report",
    "silent_correction_report",
    "transcript_formattings",
    "transcript_provenances",
    "tts_intelligibility_probe",
    "tts_owner",
    "wer_reference_sources",
    "load_audio_trace",
]

#: Adapter tag for a session driven by live engines.
AUDIO_ADAPTER: str = "voice:audio"

#: Adapter tag for a session driven entirely by committed fixtures. A different
#: tag because a replayed session is evidence about the harness, not evidence
#: that the engines still work, and a report should not have to guess which it is
#: looking at.
REPLAY_ADAPTER: str = "voice:replay"

#: Where the calibration verdict lives in the `session_start` payload. One
#: constant, because the writer and the refusal must agree, and a typo in either
#: would turn the refusal into a silent pass.
GATE_PAYLOAD_KEY: str = "latency_gate"

#: Where "whose synthesiser produced the agent's voice?" lives on the trace.
#:
#: This is the field that decides whether an end-to-end, audio-inclusive latency
#: figure means anything at all, and it is written both onto `session_start` and
#: onto every `agent_audio_first_byte` event. Both, because the two answer
#: different questions: the session-level value is the configuration, and the
#: per-event value survives a trace being merged, filtered or sliced by engine.
TTS_OWNER_KEY: str = "tts_owner"

#: The harness synthesised the agent's replies. Its speech is a *fixture*, so any
#: figure that includes synthesis or playback time is measuring this repo.
TTS_OWNER_HARNESS: str = "harness"

#: The product synthesised its own replies — a real voice agent with its own TTS
#: inside the session. Only then is audio-inclusive latency the product's cost.
TTS_OWNER_SUT: str = "sut"

#: Verdict vocabulary. "NOT_RUN" is a real value and not a synonym for PASS: a
#: session whose gate was skipped has unproven latency in exactly the same way a
#: session whose gate failed does, and both are refused.
GateVerdict = str
GATE_PASS: GateVerdict = "PASS"
GATE_FAIL: GateVerdict = "FAIL"
GATE_NOT_RUN: GateVerdict = "NOT_RUN"


class LatencyUnproven(RuntimeError):
    """Latency was asked for from a trace whose timing gate did not pass.

    Raised, not warned. A warning is a line in a log that nobody reads, and the
    number gets published anyway; an exception is a number that does not exist
    until the instrument has been shown to work.
    """


class WERUnproven(RuntimeError):
    """Word error rate was asked for from transcripts no engine produced.

    See `lab.voice.engines.stt` for the argument. The short version: WER against
    the reference is zero by construction, so reporting it would be publishing a
    fabricated result about the audio channel.

    Also raised for smart-formatted transcripts, which measure the vendor's
    formatting policy rather than its recognition — measured at 0.556 to 0.800
    apparent error on flawless transcripts. See `engines/WER_NORMALISATION.md`.
    """


class DeliveredLatencyUnavailable(RuntimeError):
    """An audio-inclusive latency figure was asked for from a harness-voiced session.

    The distinction this enforces is the single most consequential one in the
    suite, and it comes straight off a production incident: **`e2e_latency` as
    dashboards usually report it is agent-side and excludes network delivery**, so
    the number on the wall is not the number the user lived through.

    For a live in-call coaching product that gap *is* the product risk. A
    correction or a compliance reminder that arrives after the adviser has already
    moved on is not a slow success, it is a failure — the moment it was for has
    passed. So "how long until the human heard it?" is a different and more
    important question than "how long until the model finished?", and the two must
    not share a name.

    This harness can answer the second honestly. It cannot answer the first,
    because when `tts_owner` is `harness` the voice the caller "hears" was
    synthesised by this repo on this machine: the synthesis time is our compute
    and the playback time is a clock we advanced ourselves. Reporting either as
    delivery latency would be measuring the harness and labelling it the product.

    Answering it needs the product to own its own voice — a real agent with TTS
    inside the session, `tts_owner="sut"` — and then the figure is real. Until
    then this raises, because a plausible number for the most important question
    is worse than no number at all.
    """


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


class LatencyGate:
    """Runs `lab.voice.calibration` once, caches the verdict, and answers for it.

    The gate is a *precondition* of reporting latency, not of running a session.
    That distinction is deliberate: a harness whose stopwatch is broken still
    produces a perfectly valid transcript, and the contracts, the judges and the
    tool ledger are all still worth having. Refusing to run the session would
    throw away good behavioural evidence to protect a timing figure. So the
    session runs, the verdict is recorded in the trace, and only the latency
    figure is withheld.

    Cached because the gate is a property of the harness and the machine, not of
    the scenario: sweeping five delays twenty times for every one of 55 scenarios
    would be 5,500 needless sessions to answer a question whose answer cannot
    change mid-run.
    """

    def __init__(
        self,
        *,
        tolerance: CalibrationTolerance | None = None,
        delays_s: Sequence[float] = DEFAULT_DELAYS_S,
        repeats: int = DEFAULT_REPEATS,
        clock_factory: Callable[[], Clock] = FakeClock,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            tolerance: Pass criteria; defaults to the calibration module's.
            delays_s: Delay sweep. The default spans an order of magnitude on
                purpose — see `lab.voice.calibration` on why a single-delay
                calibration can certify a broken measurement.
            repeats: Turns per delay.
            clock_factory: Defaults to `FakeClock`, where the ground truth is
                exact and the result is identical on every machine.
            enabled: False produces a `NOT_RUN` verdict, which is refused just as
                a failure is. Present so that "we skipped the gate" is a state the
                trace can record rather than a state it cannot express.
        """
        self.tolerance = tolerance
        self.delays_s = tuple(delays_s)
        self.repeats = repeats
        self.clock_factory = clock_factory
        self.enabled = enabled
        self._report: CalibrationReport | None = None
        self._verdict: GateVerdict | None = None

    @classmethod
    def skipped(cls) -> "LatencyGate":
        """A gate that does not run. Its verdict is NOT_RUN, and NOT_RUN is refused."""
        return cls(enabled=False)

    @property
    def report(self) -> CalibrationReport | None:
        """The calibration report, running the sweep on first access."""
        if not self.enabled:
            return None
        if self._report is None:
            result = run_calibration(
                delays_s=self.delays_s,
                repeats=self.repeats,
                clock_factory=self.clock_factory,
                tolerance=self.tolerance,
            )
            assert isinstance(result, CalibrationReport)  # collect_traces=False
            self._report = result
        return self._report

    @property
    def verdict(self) -> GateVerdict:
        """PASS, FAIL or NOT_RUN. Runs the sweep on first access."""
        if self._verdict is None:
            report = self.report
            self._verdict = (
                GATE_NOT_RUN if report is None else (GATE_PASS if report.passed else GATE_FAIL)
            )
        return self._verdict

    @property
    def cached_verdict(self) -> GateVerdict:
        """The verdict if it has already been computed, else `"PENDING"`.

        Exists so that `AudioAdapter.describe()` — and anything else that only
        wants to print the adapter's configuration — cannot accidentally trigger a
        hundred calibration turns. A read-only accessor that starts a
        computation is a trap, and a `__repr__` that starts one is worse.
        """
        return self._verdict if self._verdict is not None else "PENDING"

    def detail(self) -> str:
        """One line for the trace and for the refusal message."""
        report = self.report
        if report is None:
            return "calibration gate not run for this session"
        passing = sum(1 for delay in report.delays if delay.passed)
        return (
            f"{passing}/{len(report.delays)} delays within tolerance "
            f"({report.tolerance.describe()}) on {report.clock}"
        )

    def payload(self) -> dict[str, Any]:
        """Trace payload recording the verdict, so a trace file can be audited alone."""
        return {
            GATE_PAYLOAD_KEY: self.verdict,
            "latency_gate_detail": self.detail(),
        }

    def __repr__(self) -> str:
        return f"LatencyGate(enabled={self.enabled}, verdict={self.cached_verdict})"


# --------------------------------------------------------------------------- #
# Per-turn record
# --------------------------------------------------------------------------- #


class AudioTurn(BaseModel):
    """What one audio turn produced, returned alongside the trace for inspection.

    The trace is the contract and the only thing downstream consumes; this is a
    convenience for a human debugging a session or a generator writing fixtures,
    which is why it holds digests and paths rather than samples.
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    caller_text: str
    heard_text: str
    agent_text: str
    caller_digest: str
    caller_duration_s: float
    caller_bytes: int
    caller_clip: str | None = None
    agent_duration_s: float | None = None
    agent_bytes: int | None = None
    agent_clip: str | None = None
    provenance: str = "engine"
    perturbations: list[PerturbationDescriptor] = Field(default_factory=list)
    response_latency_s: float = 0.0


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #


class AudioAdapter:
    """Drives a caller against an agent through real speech, and emits a `Trace`.

    Satisfies the same driver contract as `lab.simulator.run_scenario`: the agent
    is an `AgentUnderTest` callable, the caller is a `Caller`, and the output is a
    `Trace`. Swapping this adapter in changes what the agent *hears* and nothing
    about what the checks read, which is the property that lets the voice suite be
    a layer over the text corpus rather than a separate corpus.
    """

    def __init__(
        self,
        *,
        tts: TTSEngine,
        stt: STTEngine,
        agent_tts: TTSEngine | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        perturbations: Sequence[tuple[str, Mapping[str, Any]]] = (),
        gate: LatencyGate | None = None,
        clip_dir: str | Path | None = None,
        clip_format: str = ".wav",
        synthesise_agent_audio: bool = True,
        transcribe_agent_audio: bool = False,
        caller_voice: str | None = None,
        agent_voice: str | None = None,
        tts_owner: str = TTS_OWNER_HARNESS,
    ) -> None:
        """
        Args:
            tts: Synthesises the caller's lines.
            stt: Transcribes the caller's (perturbed) audio. This transcript is
                what the agent receives — the agent never sees the script.
            agent_tts: Synthesises the agent's replies. Defaults to `tts`. Kept
                separate because caller and agent should not share a voice, and
                because a report that cannot tell the two synthesisers apart
                cannot attribute a TTS regression to a side.
            sample_rate: Working rate for the whole path.
            perturbations: Default chain, as `(name, params)` pairs in the order
                they are applied — these do not commute, so the order is data.
                Overridable per session.
            gate: The timing gate. Defaults to a real one; pass
                `LatencyGate.skipped()` to record NOT_RUN instead.
            clip_dir: Where clips are written. `None` uses a temporary directory
                that is removed when the session ends — the path is still a real
                file write, because file-based is the design and a design claim
                that is not exercised is a comment.
            clip_format: `.wav` (no dependency) or `.opus` (needs `soundfile`).
                Committed fixtures are Opus; scratch clips are WAV.
            synthesise_agent_audio: Synthesise the agent's replies. True gives
                real `agent_audio_complete` durations and real speaking times.
            transcribe_agent_audio: Also transcribe the agent's own audio, to
                measure the intelligibility of the harness's synthesiser. Off by
                default: it doubles the STT bill and measures us, not the agent.
            caller_voice, agent_voice: Voice overrides passed to the engines.
            tts_owner: Who synthesised the agent's speech — `"harness"` (the
                default, and the truth whenever `agent_tts` is one of this repo's
                engines) or `"sut"` (a real voice agent that speaks for itself).
                It gates `audio_delivered_latency_report`, which refuses on
                `"harness"`. The default is the conservative one on purpose: a
                caller who forgets to set this gets a refusal, not a number that
                silently measures the wrong thing.
        """
        self.tts = tts
        self.stt = stt
        self.agent_tts = agent_tts if agent_tts is not None else tts
        self.sample_rate = sample_rate
        self.perturbations = [(name, dict(params)) for name, params in perturbations]
        self.gate = gate if gate is not None else LatencyGate()
        self.clip_dir = Path(clip_dir) if clip_dir is not None else None
        self.clip_format = clip_format if clip_format.startswith(".") else f".{clip_format}"
        self.synthesise_agent_audio = synthesise_agent_audio
        self.transcribe_agent_audio = transcribe_agent_audio
        self.caller_voice = caller_voice
        self.agent_voice = agent_voice
        self.tts_owner = tts_owner
        self.turns: list[AudioTurn] = []

    # ------------------------------------------------------------- properties

    @property
    def is_replay(self) -> bool:
        """True when every engine in the path is replaying a fixture."""
        return bool(
            getattr(self.tts, "is_replay", False)
            and getattr(self.stt, "is_replay", False)
            and getattr(self.agent_tts, "is_replay", False)
        )

    @property
    def adapter_tag(self) -> str:
        """`voice:replay` for a fully fixture-driven session, else `voice:audio`."""
        return REPLAY_ADAPTER if self.is_replay else AUDIO_ADAPTER

    def describe(self) -> str:
        return (
            f"{self.adapter_tag}: caller={self.tts.name} agent={self.agent_tts.name} "
            f"stt={self.stt.name} @ {self.sample_rate} Hz, gate={self.gate.cached_verdict}"
        )

    # ------------------------------------------------------------------- run

    def run(
        self,
        *,
        scenario_id: str,
        agent: AgentUnderTest,
        caller: Caller,
        clock: Clock | None = None,
        session_id: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        perturbations: Sequence[tuple[str, Mapping[str, Any]]] | None = None,
        profile: Any = None,
    ) -> Trace:
        """Run one audio session and return its trace.

        Args:
            scenario_id: Written into the trace and every report row.
            agent: The system under test. Called exactly once per turn, and never
                handed the script — only the transcript.
            caller: Supplies the caller's lines.
            clock: Time source. Pass the *same* clock the agent spends time on,
                or the trace will record an agent that answered instantly.
            session_id: Defaults to a fresh uuid4 hex.
            max_turns: Hard stop; reaching it ends the session with
                `reason="max_turns"` rather than raising.
            perturbations: Overrides the adapter's default chain for this session.
            profile: Caller profile for `session_start` attribution; falls back to
                `caller.profile`.

        Returns:
            The `Trace`. Per-turn detail is also left on `self.turns`.
        """
        if max_turns < 1:
            raise ValueError(f"max_turns must be at least 1, got {max_turns!r}")
        chain = (
            [(name, dict(params)) for name, params in perturbations]
            if perturbations is not None
            else list(self.perturbations)
        )

        # The gate runs *before* the session, so that a machine whose stopwatch is
        # broken finds out before spending a suite's worth of synthesis on
        # unusable timings. Its verdict goes into session_start below.
        gate_payload = self.gate.payload()

        if self.clip_dir is not None:
            self.clip_dir.mkdir(parents=True, exist_ok=True)
            scratch: tempfile.TemporaryDirectory[str] | None = None
            clip_root = self.clip_dir
        else:
            scratch = tempfile.TemporaryDirectory(prefix="lab-audio-session-")
            clip_root = Path(scratch.name)

        try:
            return self._run_session(
                scenario_id=scenario_id,
                agent=agent,
                caller=caller,
                clock=clock,
                session_id=session_id,
                max_turns=max_turns,
                chain=chain,
                profile=profile,
                gate_payload=gate_payload,
                clip_root=clip_root,
                persist_clips=self.clip_dir is not None,
            )
        finally:
            if scratch is not None:
                scratch.cleanup()

    # --------------------------------------------------------------- internals

    def _run_session(
        self,
        *,
        scenario_id: str,
        agent: AgentUnderTest,
        caller: Caller,
        clock: Clock | None,
        session_id: str | None,
        max_turns: int,
        chain: list[tuple[str, dict[str, Any]]],
        profile: Any,
        gate_payload: dict[str, Any],
        clip_root: Path,
        persist_clips: bool,
    ) -> Trace:
        builder = TraceBuilder(
            scenario_id=scenario_id,
            adapter=self.adapter_tag,
            session_id=session_id,
            clock=clock,
        )
        self.turns = []
        effective_profile = profile if profile is not None else getattr(caller, "profile", None)

        metadata: dict[str, Any] = {
            "caller": type(caller).__name__,
            "max_turns": max_turns,
            "sample_rate": self.sample_rate,
            "tts_engine": self.tts.name,
            "agent_tts_engine": self.agent_tts.name,
            "stt_engine": self.stt.name,
            "replay": self.is_replay,
            "clip_format": self.clip_format,
            TTS_OWNER_KEY: self.tts_owner,
            **gate_payload,
        }
        # The *plan*, not the achieved strength. The achieved strength is measured
        # per clip and recorded on each `audio_emitted` event by
        # `perturbation_payload`, because a requested 10 dB SNR and a delivered
        # one are different numbers and only one of them is a result.
        metadata["perturbation_plan"] = (
            " -> ".join(
                f"{name}({', '.join(f'{k}={v}' for k, v in params.items())})"
                for name, params in chain
            )
            or "clean"
        )
        trace_metadata = getattr(effective_profile, "trace_metadata", None)
        if callable(trace_metadata):
            metadata.update(trace_metadata())
        builder.session_start(**metadata)

        utterance: str | None = caller.opening()
        turns = 0
        reason = "caller_hung_up"

        while utterance is not None:
            if turns >= max_turns:
                reason = "max_turns"
                break
            turns += 1

            # ---- caller leg: synthesise, degrade, write, transcribe. All of it
            # ---- harness cost, all of it strictly before BOUNDARY OUT.
            clean = self.tts.synthesise(
                utterance, sample_rate=self.sample_rate, voice=self.caller_voice
            )
            perturbed, descriptors = self._perturb(clean, chain)
            clip_path, encoded_bytes = self._write_clip(
                clip_root, f"turn{turns:02d}-caller", perturbed, persist=persist_clips
            )
            builder.audio_emitted(
                actor="caller",
                num_bytes=perturbed.num_bytes,
                duration_s=perturbed.duration_s,
                engine=perturbed.engine,
                encoded_bytes=encoded_bytes,
                encoded_format=self.clip_format,
                clip=clip_path.name if persist_clips else None,
                synthesis_s=(
                    round(clean.synthesis_s, 6) if clean.synthesis_s is not None else None
                ),
                replayed=clean.replayed,
                **perturbation_payload(descriptors),
            )
            # The caller is speaking. Advancing the clock by the clip's own
            # duration is what makes the left edge of the latency window the
            # instant the caller stopped talking, which is the instant a real
            # caller starts waiting.
            self._advance_to(builder, builder.now() + perturbed.duration_s)

            heard = self.stt.transcribe(perturbed.audio, sample_rate=self.sample_rate)

            t0 = builder.now()  # ---- BOUNDARY OUT
            reply = agent(heard.text)  # the system under test, and nothing else
            t1 = builder.now()  # ---- BOUNDARY IN
            # ---- window closed; everything below is harness compute

            agent_turn = coerce_turn(reply)

            # `caller_utterance` first, then `transcript_in` — see the module
            # docstring. Reversing these two lines silently shifts every WER
            # pairing by one turn.
            #
            # The spoken form rides along whenever the synthesiser published one,
            # with `reference_source` naming which string a WER must be scored
            # against. Both go on the event rather than being recomputed later,
            # because a trace has to be auditable on its own: a reviewer holding
            # only the JSONL must be able to see which reference produced a
            # figure, and to score it again themselves.
            builder.caller_utterance(
                utterance,
                ts=t0,
                engine=perturbed.engine,
                reference_source=clean.reference_source,
                **(
                    {"spoken_text": clean.spoken_text}
                    if clean.spoken_text and clean.spoken_text != utterance
                    else {}
                ),
            )
            builder.transcript_in(
                heard.text,
                confidence=heard.confidence,
                ts=t0,
                engine=heard.engine,
                **heard.trace_payload(),
            )

            self._emit_inner_events(builder, agent_turn, t0, t1)

            builder.agent_audio_first_byte(
                turn=turns,
                ts=t1,
                engine=self.agent_tts.name,
                # Repeated per event, not only on session_start. `metrics.py`
                # slices distributions by engine and a sliced trace loses its
                # header; an ownership claim that only exists in the header is an
                # ownership claim that a filter can drop.
                **{TTS_OWNER_KEY: self.tts_owner},
            )
            builder.transcript_out(agent_turn.text, ts=t1, engine=self.agent_tts.name)

            agent_audio = self._speak_agent(
                builder, agent_turn.text, turn=turns, t1=t1, clip_root=clip_root,
                persist=persist_clips,
            )
            builder.agent_utterance(agent_turn.text, agent=agent_turn.agent)

            self.turns.append(
                AudioTurn(
                    index=turns,
                    caller_text=utterance,
                    heard_text=heard.text,
                    agent_text=agent_turn.text,
                    caller_digest=self._digest(perturbed),
                    caller_duration_s=perturbed.duration_s,
                    caller_bytes=perturbed.num_bytes,
                    caller_clip=clip_path.name if persist_clips else None,
                    agent_duration_s=agent_audio[0],
                    agent_bytes=agent_audio[1],
                    agent_clip=agent_audio[2],
                    provenance=heard.provenance,
                    perturbations=descriptors,
                    response_latency_s=t1 - t0,
                )
            )

            if agent_turn.end_call:
                reason = "agent_ended"
                break
            utterance = caller.reply(agent_turn)

        builder.session_end(reason=reason, turns=turns)
        return builder.build()

    # ------------------------------------------------------------ turn pieces

    def _perturb(
        self, clean: SynthesisResult, chain: list[tuple[str, dict[str, Any]]]
    ) -> tuple[SynthesisResult, list[PerturbationDescriptor]]:
        """Apply the chain, returning a result that still knows its own engine.

        An empty chain is not a special case: `apply_chain` returns the audio and
        an empty descriptor list, and `perturbation_payload(())` records
        `"clean"` explicitly. An absent key is indistinguishable from an adapter
        that forgot to record one.
        """
        if not chain:
            return clean, []
        audio, descriptors = apply_chain(
            clean.audio, [(name, params) for name, params in chain], sample_rate=clean.sample_rate
        )
        return clean.model_copy(update={"audio": audio}), descriptors

    def _write_clip(
        self, root: Path, stem: str, result: SynthesisResult, *, persist: bool
    ) -> tuple[Path, int]:
        """Write the clip and return its path and encoded size.

        Always writes, even when the directory is temporary. The file-based claim
        in the module docstring is only true if the file is real, and an encoder
        that is never exercised is an encoder that is broken.
        """
        path = root / f"{stem}{self.clip_format}"
        write_audio(path, result.audio, result.sample_rate)
        size = path.stat().st_size
        if not persist:
            # The path was exercised; the bytes are not a fixture. Removing them
            # keeps a suite run from leaving a hundred megabytes in /tmp.
            path.unlink(missing_ok=True)
        return path, size

    def _digest(self, result: SynthesisResult) -> str:
        from lab.voice.engines.base import audio_digest  # noqa: PLC0415 - local, cheap

        return audio_digest(result.audio, result.sample_rate)

    def _advance_to(self, builder: TraceBuilder, target: float) -> None:
        """Advance the clock to `target` if it is not already past it.

        Playback is simulated by spending time on the injected clock, which is
        free and exact under a `FakeClock` and a real wait under a real one. A
        real wait is the honest behaviour: a call really does take as long as the
        speech in it, and pretending otherwise would let `agent_audio_complete`
        be stamped in a future the rest of the trace never reaches.
        """
        remaining = target - builder.now()
        if remaining > 0:
            builder.clock.sleep(remaining)

    def _emit_inner_events(
        self, builder: TraceBuilder, agent_turn: Any, t0: float, t1: float
    ) -> None:
        """Emit handoffs and tool calls inside the measured window.

        Identical placement rules to the text driver, via the same
        `_WindowStamper`: ordering is faithful, instants are interpolated and
        flagged `ts_estimated`, and no timing figure in this repo is derived from
        a flagged event.
        """
        inner: list[Handoff | ToolInvocation] = []
        if agent_turn.handoff is not None:
            inner.append(agent_turn.handoff)
        inner.extend(agent_turn.tools)
        slots = sum(2 if isinstance(item, ToolInvocation) else 1 for item in inner)
        stamper = _WindowStamper(t0, t1, slots)
        for item in inner:
            if isinstance(item, Handoff):
                ts, extra = stamper.take(item.ts)
                builder.agent_handoff(
                    item.from_agent, item.to_agent, reason=item.reason, ts=ts, **extra
                )
                continue
            call_ts, call_extra = stamper.take(item.ts)
            result_ts, result_extra = stamper.take(item.ts)
            call_event = builder.tool_call(
                item.name, item.args, call_id=item.call_id, ts=call_ts, **call_extra
            )
            builder.tool_result(
                item.name,
                item.result,
                call_id=call_event.get("call_id"),
                ok=item.ok,
                error=item.error,
                ts=result_ts,
                **result_extra,
            )

    def _speak_agent(
        self,
        builder: TraceBuilder,
        text: str,
        *,
        turn: int,
        t1: float,
        clip_root: Path,
        persist: bool,
    ) -> tuple[float | None, int | None, str | None]:
        """Synthesise the reply, emit its audio events, return (duration, bytes, clip).

        A blank reply is a silent turn: no synthesis, no `audio_emitted`, no
        `agent_audio_complete`. Synthesising silence would put a zero-length clip
        in the speaking-time distribution and a real event in the trace for
        something that never happened.
        """
        if not self.synthesise_agent_audio or not text.strip():
            return None, None, None
        spoken = self.agent_tts.synthesise(
            text, sample_rate=self.sample_rate, voice=self.agent_voice
        )
        clip_path, encoded_bytes = self._write_clip(
            clip_root, f"turn{turn:02d}-agent", spoken, persist=persist
        )
        loopback: Transcription | None = None
        if self.transcribe_agent_audio:
            loopback = self.stt.transcribe(spoken.audio, sample_rate=spoken.sample_rate)

        # The clock is advanced to the end of playback so that
        # `agent_audio_complete - agent_audio_first_byte` is exactly the audible
        # length of the reply, and `lab.voice.metrics.speaking_times` means what
        # its name says.
        complete_at = t1 + spoken.duration_s
        builder.audio_emitted(
            actor="agent",
            num_bytes=spoken.num_bytes,
            duration_s=spoken.duration_s,
            engine=spoken.engine,
            encoded_bytes=encoded_bytes,
            encoded_format=self.clip_format,
            clip=clip_path.name if persist else None,
            synthesis_s=(round(spoken.synthesis_s, 6) if spoken.synthesis_s is not None else None),
            replayed=spoken.replayed,
            ts=t1,
        )
        self._advance_to(builder, complete_at)
        extra: dict[str, Any] = {}
        if loopback is not None:
            extra = {
                "loopback_text": loopback.text,
                "loopback_engine": loopback.engine,
                "loopback_provenance": loopback.provenance,
            }
        builder.agent_audio_complete(
            turn=turn,
            num_bytes=spoken.num_bytes,
            ts=complete_at,
            engine=spoken.engine,
            duration_s=spoken.duration_s,
            **extra,
        )
        return spoken.duration_s, spoken.num_bytes, (clip_path.name if persist else None)

    def __repr__(self) -> str:
        return f"AudioAdapter({self.describe()})"


# --------------------------------------------------------------------------- #
# Reading a trace back — including the refusals
# --------------------------------------------------------------------------- #


def latency_gate_verdict(trace: Trace) -> GateVerdict:
    """The calibration verdict recorded in the trace, or NOT_RUN if absent.

    Absence is NOT_RUN and never PASS. A trace produced by an adapter that forgot
    to record a verdict is a trace whose instrument was never checked, which is
    exactly the situation the gate exists to catch — defaulting the other way
    would make the refusal opt-in, and a safety check nobody remembered to switch
    on is not a safety check.
    """
    start = trace.first(EventKind.SESSION_START)
    if start is None:
        return GATE_NOT_RUN
    return str(start.get(GATE_PAYLOAD_KEY, GATE_NOT_RUN))


def transcript_provenances(trace: Trace) -> dict[str, int]:
    """Count of `transcript_in` events by provenance. Absent provenance is `unknown`."""
    counts: dict[str, int] = {}
    for event in trace.events_of_kind(EventKind.TRANSCRIPT_IN):
        key = str(event.get("provenance", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def audio_latency_report(
    traces: Trace | Sequence[Trace], *, scope: str | None = None
) -> ResponseLatencyReport:
    """Latency for one or more audio traces — or a refusal.

    Raises:
        LatencyUnproven: if any trace's recorded calibration verdict is not PASS.
            All of them, not a sample: pooling one unproven session into a
            distribution contaminates every quantile drawn from it, and a p95 is
            precisely the statistic a single bad session can own.
    """
    collection = [traces] if isinstance(traces, Trace) else list(traces)
    if not collection:
        raise LatencyUnproven(
            "no traces were given, so there is no latency to report and no gate to check"
        )
    unproven = [
        (trace.session_id, latency_gate_verdict(trace))
        for trace in collection
        if latency_gate_verdict(trace) != GATE_PASS
    ]
    if unproven:
        listing = ", ".join(f"{session}={verdict}" for session, verdict in unproven)
        raise LatencyUnproven(
            f"refusing to report latency for {len(unproven)}/{len(collection)} session(s) "
            f"whose timing calibration did not pass: {listing}. The measurement may be "
            "accurate and there is no evidence that it is. Run `make calibrate` to see "
            "which delays failed, fix the harness, and re-run the sessions."
        )
    return response_latency_report(collection, scope=scope)


def audio_wer_report(trace: Trace, *, backend: str = "auto") -> CorpusWER:
    """Word error rate for one audio trace — or a refusal.

    Raises:
        WERUnproven: if the trace has no transcripts, or if any of them carry a
            provenance other than `engine` or `recorded`. A reference transcript
            *is* the reference, so scoring against it yields zero errors on every
            utterance regardless of how badly the channel was degraded. Reporting
            that as a word error rate would be publishing a fabricated finding
            about the audio pipeline — and it would look like unusually good news.
    """
    counts = transcript_provenances(trace)
    if not counts:
        raise WERUnproven(
            f"session {trace.session_id!r} has no transcript_in events; there is nothing "
            "to score. A voice session with no transcripts did not run the STT leg."
        )
    unusable = {key: n for key, n in counts.items() if key not in ("engine", "recorded")}
    if unusable:
        listing = ", ".join(f"{n} {key}" for key, n in sorted(unusable.items()))
        total = sum(counts.values())
        raise WERUnproven(
            f"refusing to report WER for session {trace.session_id!r}: {listing} of {total} "
            "transcript(s) were not produced by a speech engine. A reference transcript is "
            "the ground truth, so the WER against it is 0.0% by construction on every "
            "channel, however degraded. Install a local engine with scripts/setup_audio.sh "
            "and re-record with `make audio-fixtures` to get a real figure."
        )
    smart = transcript_formattings(trace).get("smart", 0)
    if smart:
        raise WERUnproven(
            f"refusing to report WER for session {trace.session_id!r}: {smart} scored "
            "transcript(s) are smart-formatted. Smart formatting rewrites spoken numerals "
            "into written ones — 'seven thirty' becomes '07:30', spelled letters are joined "
            "— so scoring it against a spoken-form reference measures the vendor's "
            "formatting policy, not its recognition. Measured on this repo's own round "
            "trip, transcripts that were correct to the last digit scored 0.556 to 0.800 "
            "that way. Re-run the STT leg with smart_format=false (DeepgramSTT's default) "
            "and keep the formatted string as display_text_unscored."
        )
    return trace_wer(trace, backend=backend)  # type: ignore[arg-type]


def transcript_formattings(trace: Trace) -> dict[str, int]:
    """Count of `transcript_in` events by formatting. Absent formatting is `unknown`.

    `unknown` rather than a default of `raw`: a trace written before the field
    existed cannot be assumed verbatim, and quietly assuming the safe value is how
    an old fixture ends up certifying a number nobody checked. It is counted, and
    the refusal above only fires on a positive `smart` count, so old traces keep
    working while new ones are held to the rule.
    """
    counts: dict[str, int] = {}
    for event in trace.events_of_kind(EventKind.TRANSCRIPT_IN):
        key = str(event.get("formatting", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def wer_reference_sources(trace: Trace) -> dict[str, int]:
    """Count of `caller_utterance` events by which reference they offer.

    Printed beside any WER in a report. "WER 4.1%" is a number; "WER 4.1%, scored
    against the synthesiser's own spoken form on 9 of 9 utterances" is a result.
    A corpus that silently mixed the two references would average a recognition
    rate with a formatting-mismatch rate and call the result recognition.
    """
    counts: dict[str, int] = {}
    for event in trace.events_of_kind(EventKind.CALLER_UTTERANCE):
        key = str(event.get("reference_source", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def tts_owner(trace: Trace) -> str:
    """Who synthesised the agent's voice, per the trace. Absent means `harness`.

    Defaulting to `harness` is the conservative direction: it makes the delivered
    latency refusal fire on any trace that does not explicitly claim the product
    owned its own voice. A safety check that defaults to "permitted" is not one.
    """
    start = trace.first(EventKind.SESSION_START)
    if start is None:
        return TTS_OWNER_HARNESS
    return str(start.get(TTS_OWNER_KEY, TTS_OWNER_HARNESS))


def audio_delivered_latency_report(
    traces: Trace | Sequence[Trace], *, scope: str | None = None
) -> ResponseLatencyReport:
    """Latency *including* audio delivery — or a refusal. Reference bug 2.

    The metric a live in-call coaching product is actually judged on: not "when did
    the model finish thinking?" but "when did the human hear it?". A factual
    correction that lands after the adviser has moved on has failed, however fast
    the model was.

    Raises:
        DeliveredLatencyUnavailable: if any trace's `tts_owner` is not `sut`. When
            this harness synthesised the agent's speech, both the synthesis time
            and the playback time belong to this repo — the playback is a clock we
            advanced ourselves — so the figure would measure the harness and wear
            the product's name. See the exception's docstring.
        LatencyUnproven: if any trace's calibration verdict is not PASS. The gate
            applies here exactly as it does to the agent-side figure; a stopwatch
            that has not been shown to work does not become trustworthy by being
            pointed at a more important question.
    """
    collection = [traces] if isinstance(traces, Trace) else list(traces)
    if not collection:
        raise DeliveredLatencyUnavailable(
            "no traces were given, so there is no delivered latency to report"
        )
    harness_voiced = [
        (trace.session_id, tts_owner(trace))
        for trace in collection
        if tts_owner(trace) != TTS_OWNER_SUT
    ]
    if harness_voiced:
        listing = ", ".join(f"{session}={owner}" for session, owner in harness_voiced)
        raise DeliveredLatencyUnavailable(
            f"refusing to report delivered latency for {len(harness_voiced)}/"
            f"{len(collection)} session(s) whose agent voice was not the product's own: "
            f"{listing}. The agent-side figure from audio_latency_report is real and is "
            "available; this one is not, because the audio it would measure was "
            "synthesised here. Production dashboards make the mirror-image mistake — they "
            "report an agent-side e2e_latency that excludes network delivery and read it "
            "as what the user experienced. Getting a real number needs the agent to own "
            "its own TTS inside the session and the adapter built with tts_owner='sut'."
        )
    unproven = [
        (trace.session_id, latency_gate_verdict(trace))
        for trace in collection
        if latency_gate_verdict(trace) != GATE_PASS
    ]
    if unproven:
        listing = ", ".join(f"{session}={verdict}" for session, verdict in unproven)
        raise LatencyUnproven(
            f"refusing to report delivered latency for {len(unproven)}/{len(collection)} "
            f"session(s) whose timing calibration did not pass: {listing}."
        )
    return response_latency_report(collection, scope=scope)


# --------------------------------------------------------------------------- #
# The metric that must never be called WER — reference bug: silent corrections
# --------------------------------------------------------------------------- #


class TTSIntelligibilityProbe(BaseModel):
    """How well the *harness's own* synthesiser can be transcribed. Never agent WER.

    When `AudioAdapter(transcribe_agent_audio=True)` runs, the STT engine is
    pointed at audio this repo synthesised, and the resulting error rate is a
    perfectly useful number: it is the noise floor of the instrument. If the
    harness's own voice cannot be transcribed cleanly, then a caller-side WER is
    partly measuring the caller's synthesiser, and every row in the suite inherits
    that error.

    It is also a number that must never be quoted as the agent's word error rate,
    because the agent had nothing to do with it — both ends of the comparison are
    ours. So it does not share a class, a function or a field name with WER. The
    `metric` field spells out the prohibition and travels with the number into
    any report that serialises it, because a figure copied out of a table loses
    its column heading long before it loses its value.
    """

    model_config = ConfigDict(extra="forbid")

    #: Fixed. Written into every serialisation so the name cannot be lost.
    metric: str = "tts_intelligibility_probe"

    #: Spelled out for a reader who found this number in a table with no context.
    caveat: str = (
        "Both sides of this comparison were produced by the harness: the audio by "
        "our TTS, the transcript by our STT. It is the instrument's noise floor, "
        "not the agent's word error rate, and it must not be reported as WER."
    )

    turns: int = 0
    engine: str | None = None
    stt_engine: str | None = None
    error_rate: float | None = None
    reference_words: int = 0
    errors: int = 0

    def describe(self) -> str:
        if self.error_rate is None:
            return (
                "TTS intelligibility probe: no loopback turns "
                "(run the adapter with transcribe_agent_audio=True)"
            )
        return (
            f"TTS intelligibility probe: {self.error_rate:.1%} over {self.turns} turn(s), "
            f"{self.errors}/{self.reference_words} words — the harness measuring itself, "
            f"not the agent ({self.engine} -> {self.stt_engine})"
        )


def tts_intelligibility_probe(trace: Trace) -> TTSIntelligibilityProbe:
    """Score the harness's loopback transcripts. Reference bug: silent corrections.

    Deliberately *not* named `..._wer` and deliberately not returning a
    `CorpusWER`, so that no caller can hand this to something expecting an agent
    figure and no reader can mistake the two in a report. `audio_wer_report` does
    not read the loopback fields at all, and a test asserts that.
    """
    references: list[str] = []
    hypotheses: list[str] = []
    tts_engine: str | None = None
    stt_engine: str | None = None
    for event in trace.events_of_kind(EventKind.AGENT_AUDIO_COMPLETE):
        heard = event.get("loopback_text")
        if heard is None:
            continue
        if event.get("loopback_provenance") not in ("engine", "recorded"):
            # A reference-provenance loopback would score 0.0 by construction, for
            # the same reason the caller-side refusal exists.
            continue
        spoken = _agent_text_for_turn(trace, event.get("turn"))
        if spoken is None:
            continue
        references.append(spoken)
        hypotheses.append(str(heard))
        tts_engine = tts_engine or (event.engine if hasattr(event, "engine") else None)
        stt_engine = stt_engine or str(event.get("loopback_engine") or "") or None
    if not references:
        return TTSIntelligibilityProbe(engine=tts_engine, stt_engine=stt_engine)
    scored = corpus_wer(list(zip(references, hypotheses)))
    errors, words = 0, 0
    for item in scored.utterances:
        errors += item.normalised.errors
        words += item.normalised.reference_words
    return TTSIntelligibilityProbe(
        turns=len(references),
        engine=tts_engine,
        stt_engine=stt_engine,
        error_rate=(errors / words if words else None),
        reference_words=words,
        errors=errors,
    )


def _agent_text_for_turn(trace: Trace, turn: Any) -> str | None:
    """The agent's reply text for `turn`, from the ordered `transcript_out` stream.

    Positional, not timestamp-based: `agent_audio_complete` carries a 1-based turn
    number and `transcript_out` events appear once per turn in order. Matching on
    time would be the regression `lab/voice` deliberately fixed once already —
    ordering here is decided on event-stream position.
    """
    if turn is None:
        return None
    try:
        index = int(turn) - 1
    except (TypeError, ValueError):
        return None
    outs = trace.events_of_kind(EventKind.TRANSCRIPT_OUT)
    if not 0 <= index < len(outs):
        return None
    return str(outs[index].get("text", "")) or None


# --------------------------------------------------------------------------- #
# Silent STT corrections, with attribution — reference bug 3
# --------------------------------------------------------------------------- #


class SilentCorrection(BaseModel):
    """One place where what the recogniser wrote down differs from what was said.

    "Silent" because nothing in a production pipeline flags it: the agent receives
    the transcript, acts on it, and no component ever learns that a word changed.
    A production reconciliation of this exact failure found roughly 17 to 18
    corrections per 100 calls with **68.7% of them unattributable** — because
    production never knows what the caller really said, so there is nothing to
    compare against.

    An audio harness does not have that problem, and this is the single clearest
    demonstration of why the suite exists: *we synthesised the audio*, so the
    ground truth is not inferred, it is an input. Attribution is 100% by
    construction. Every difference has a known before and a known after.
    """

    model_config = ConfigDict(extra="forbid")

    turn: int
    kind: str = Field(description="substitution, deletion or insertion")
    spoken: str = Field(description="What the synthesiser said. Ground truth, not a guess.")
    heard: str = Field(description="What the recogniser wrote down.")
    confidence: float | None = Field(
        default=None, description="The recogniser's own confidence for the turn, if reported."
    )
    lowest_word: str | None = None
    lowest_word_confidence: float | None = None

    def describe(self) -> str:
        detail = f"{self.spoken!r} -> {self.heard!r}" if self.kind == "substitution" else (
            f"{self.spoken!r} dropped" if self.kind == "deletion" else f"{self.heard!r} added"
        )
        conf = f" (conf {self.confidence:.3f})" if self.confidence is not None else ""
        return f"turn {self.turn}: {self.kind} {detail}{conf}"


class SilentCorrectionReport(BaseModel):
    """Every difference between what was spoken and what was heard, all attributed.

    `unattributable` exists and is expected to be zero. It is reported anyway,
    because the whole claim of this report is "attribution is 100% here, unlike in
    production", and a claim of completeness that is not accompanied by the count
    it depends on is an assertion rather than evidence. If it is ever non-zero,
    the harness has lost its own ground truth and the number says so.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    turns_compared: int = 0
    corrections: list[SilentCorrection] = Field(default_factory=list)
    unattributable: int = 0
    reference_source: str = "unknown"

    @property
    def per_hundred_turns(self) -> float | None:
        """Corrections per 100 turns. None when nothing was compared.

        A rate needs its denominator visible — `turns_compared` is right here, and
        `lab.report` is denominator-safe for the same reason. A naked percentage is
        a defect in this repo.
        """
        if not self.turns_compared:
            return None
        return 100.0 * len(self.corrections) / self.turns_compared

    @property
    def attributed_fraction(self) -> float | None:
        """Share of corrections whose before-and-after are both known. Expect 1.0."""
        total = len(self.corrections) + self.unattributable
        return (len(self.corrections) / total) if total else None

    def describe(self) -> str:
        if not self.turns_compared:
            return "silent corrections: nothing compared"
        rate = self.per_hundred_turns
        attributed = self.attributed_fraction
        return (
            f"silent corrections: {len(self.corrections)} over {self.turns_compared} turn(s) "
            f"= {rate:.1f} per 100 turns, "
            f"{'100' if attributed == 1.0 else f'{(attributed or 0) * 100:.1f}'}% attributable "
            f"(reference: {self.reference_source}). Production's equivalent "
            "reconciliation could attribute 31.3%, because production never knows what was "
            "really said; here the audio was synthesised, so ground truth is an input."
        )


def silent_correction_report(trace: Trace) -> SilentCorrectionReport:
    """Attribute every spoken-vs-heard difference in a trace. Reference bug 3.

    Raises:
        WERUnproven: for the same two reasons `audio_wer_report` refuses — a
            reference-provenance transcript makes every comparison trivially
            identical, and a smart-formatted one manufactures differences that are
            formatting rather than recognition. Either would corrupt this report in
            the direction that flatters it, which is the direction that matters.
    """
    counts = transcript_provenances(trace)
    unusable = {key: n for key, n in counts.items() if key not in ("engine", "recorded")}
    if unusable:
        raise WERUnproven(
            f"refusing to reconcile session {trace.session_id!r}: "
            f"{', '.join(f'{n} {k}' for k, n in sorted(unusable.items()))} transcript(s) were "
            "not produced by a speech engine, so every comparison would be identical by "
            "construction and the report would show zero corrections on any channel."
        )
    if transcript_formattings(trace).get("smart", 0):
        raise WERUnproven(
            f"refusing to reconcile session {trace.session_id!r}: the scored transcripts are "
            "smart-formatted, so this report would attribute the vendor's formatting policy "
            "as recognition corrections — inventing defects rather than finding them."
        )

    sources = wer_reference_sources(trace)
    reference_source = (
        "spoken-form"
        if sources.get("spoken-form") and not sources.get("caller-input")
        else "caller-input"
        if sources.get("caller-input") and not sources.get("spoken-form")
        else "mixed"
        if sources
        else "unknown"
    )
    corrections: list[SilentCorrection] = []
    compared = 0
    unattributable = 0
    pairs = trace.event_pairs(EventKind.CALLER_UTTERANCE, EventKind.TRANSCRIPT_IN)
    for index, (utterance, transcript) in enumerate(pairs, start=1):
        spoken = str(utterance.get("spoken_text") or utterance.get("text", ""))
        heard = str(transcript.get("text", ""))
        if not spoken:
            # No ground truth for this turn. This is the only way the count can
            # rise, and it means the harness lost its own reference.
            unattributable += 1
            continue
        compared += 1
        confidence = transcript.get("confidence")
        for kind, before, after in _align_tokens(normalise(spoken), normalise(heard)):
            corrections.append(
                SilentCorrection(
                    turn=index,
                    kind=kind,
                    spoken=before,
                    heard=after,
                    confidence=(float(confidence) if confidence is not None else None),
                    lowest_word=(
                        str(transcript.get("lowest_confidence_word"))
                        if transcript.get("lowest_confidence_word")
                        else None
                    ),
                    lowest_word_confidence=(
                        float(transcript.get("lowest_word_confidence"))
                        if transcript.get("lowest_word_confidence") is not None
                        else None
                    ),
                )
            )
    return SilentCorrectionReport(
        session_id=trace.session_id,
        turns_compared=compared,
        corrections=corrections,
        unattributable=unattributable,
        reference_source=reference_source,
    )


def _align_tokens(reference: str, hypothesis: str) -> list[tuple[str, str, str]]:
    """Token-level edits turning `reference` into `hypothesis`, as (kind, before, after).

    A plain Levenshtein alignment over word tokens. It is written here rather than
    taken from `lab.voice.wer` because that module answers "how many errors?" and
    this one answers "which words, and in which direction?" — the same alignment
    serving two questions, and only one of them needs the identity of the tokens.
    """
    ref, hyp = reference.split(), hypothesis.split()
    rows, cols = len(ref) + 1, len(hyp) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            if ref[i - 1] == hyp[j - 1]:
                cost[i][j] = cost[i - 1][j - 1]
            else:
                cost[i][j] = 1 + min(cost[i - 1][j - 1], cost[i - 1][j], cost[i][j - 1])
    edits: list[tuple[str, str, str]] = []
    i, j = len(ref), len(hyp)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + 1:
            edits.append(("substitution", ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            edits.append(("deletion", ref[i - 1], ""))
            i -= 1
        else:
            edits.append(("insertion", "", hyp[j - 1]))
            j -= 1
    return list(reversed(edits))


# --------------------------------------------------------------------------- #
# Read-back capture: field assertions, not WER — reference bug 4
# --------------------------------------------------------------------------- #


class FieldCapture(BaseModel):
    """Whether one declared value survived the channel. The right instrument for a postcode.

    WER is the wrong instrument here and understates a perfect capture, which is
    the whole argument of `engines/WER_NORMALISATION.md`: "S W one A one A A"
    against "SW1A1AA" is a 100% word error and a flawless capture. The question a
    caller cares about is not how many tokens moved, it is **did you get my
    postcode right** — one value, one boolean.
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    expected: str
    captured: bool
    found_in_turn: int | None = None
    heard: str | None = Field(
        default=None, description="The transcript the value was looked for in, when it was missed."
    )

    def describe(self) -> str:
        if self.captured:
            return f"{self.field}: captured {self.expected!r} (turn {self.found_in_turn})"
        return f"{self.field}: MISSED {self.expected!r} — heard {self.heard!r}"


class ReadBackReport(BaseModel):
    """Per-field capture over a session. Reference bug 4: names, dates of birth, postcodes.

    Denominator first, per this repo's rule: `len(fields)` is right beside the
    count, so `captured_fraction` can never be read as a naked percentage.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    fields: list[FieldCapture] = Field(default_factory=list)

    @property
    def captured_fraction(self) -> float | None:
        if not self.fields:
            return None
        return sum(1 for f in self.fields if f.captured) / len(self.fields)

    @property
    def missed(self) -> list[FieldCapture]:
        return [f for f in self.fields if not f.captured]

    def describe(self) -> str:
        if not self.fields:
            return "read-back: no fields declared"
        captured = sum(1 for f in self.fields if f.captured)
        tail = (
            ""
            if not self.missed
            else " — missed: " + ", ".join(f.field for f in self.missed)
        )
        return f"read-back: {captured}/{len(self.fields)} field(s) captured{tail}"


#: Digit names, for collapsing a digit-by-digit readout into the number it spells.
_SPOKEN_DIGITS: dict[str, str] = {
    "zero": "0", "oh": "0", "nought": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _collapse_digit_runs(text: str) -> str:
    """Turn a spoken digit-by-digit readout into the digits it spells.

    It is needed because a digit-by-digit readout is exactly how account numbers,
    sort codes and card numbers are said out loud. Measured on the live corpus:
    "Account number 4071 9928." was spoken as "account number four zero seven one
    nine nine two eight", recognised perfectly at confidence 1.0 — and the field
    assertion for `4071` **failed**, because the transcript contains no digits at
    all. The instrument was wrong, not the engines, and it was wrong in the
    direction that reports a working pipeline as broken.

    **This deliberately does not use `lab.voice.wer.normalise`.** That function's
    cardinal parser is built for prose, and on a digit readout it does not merely
    decline the job — it corrupts it:

        "four zero seven one nine nine two eight"  ->  "4 8 9 11 8"

    It is trying to read compound numbers ("twenty six" -> 26) out of a sequence
    that is not compound numbers, and the result keeps neither the digits nor
    their count. So this path lowercases and strips punctuation itself and maps
    digit *names* only, one token at a time, with no compounding.

    `normalise` is still right for WER. Collapsing digit runs there would fuse
    "at seven thirty" into "at 730" and silently change the meaning of a time,
    which is why the collapse lives here, in the field-capture path, where the
    question is narrower and the answer is a boolean.

    Both the expected value and the transcript go through this, identically.
    """
    lowered = "".join(
        character if (character.isalnum() or character == " ") else " "
        for character in text.lower()
    )
    tokens = lowered.split()
    out: list[str] = []
    run: list[str] = []
    for token in tokens:
        digit = _SPOKEN_DIGITS.get(token)
        if digit is not None:
            run.append(digit)
            continue
        if token.isdigit():
            # An already-numeric token extends the run, so "20 45 77" and
            # "two zero four five seven seven" reduce to the same string.
            run.append(token)
            continue
        if run:
            out.append("".join(run))
            run = []
        out.append(token)
    if run:
        out.append("".join(run))
    return " ".join(out)


def readback_report(trace: Trace, expected: Mapping[str, str]) -> ReadBackReport:
    """Assert each declared field's value appears in some transcript. Reference bug 4.

    Both sides go through `lab.voice.wer.normalise`, which lowercases, strips
    punctuation and converts number words to digits — so "seven thirty" and "7:30"
    match, and "S W one A one A A" and "sw1a1aa" both reduce toward the same
    tokens. Comparison after collapsing spaces catches the spelled-letter case,
    which is the one that matters most and the one a token comparison misses.

    A value is counted as captured if it survives *either* canonicalisation: the
    plain one, or the one that additionally collapses spoken digit runs
    (`_collapse_digit_runs`). Two passes rather than one, because the digit
    collapse is the right transform for an account number and the wrong one for a
    time, and a field-level assertion is allowed to try both and take a hit — it
    is answering "is the value in there?", not measuring a distance.

    This is a *field* assertion and returns booleans. It never computes an error
    rate, because a rate would average a right postcode with a wrong name and
    report a number that describes neither.
    """
    transcripts = [
        (index, str(event.get("text", "")))
        for index, event in enumerate(trace.events_of_kind(EventKind.TRANSCRIPT_IN), start=1)
    ]
    fields: list[FieldCapture] = []
    for name, value in expected.items():
        wanted = normalise(str(value)).replace(" ", "")
        wanted_digits = _collapse_digit_runs(str(value)).replace(" ", "")
        hit: int | None = None
        for index, text in transcripts:
            plain = normalise(text).replace(" ", "")
            collapsed = _collapse_digit_runs(text).replace(" ", "")
            if (wanted and wanted in plain) or (wanted_digits and wanted_digits in collapsed):
                hit = index
                break
        fields.append(
            FieldCapture(
                field=name,
                expected=str(value),
                captured=hit is not None,
                found_in_turn=hit,
                heard=None if hit is not None else " | ".join(t for _, t in transcripts) or None,
            )
        )
    return ReadBackReport(session_id=trace.session_id, fields=fields)


def load_audio_trace(path: str | Path) -> Trace:
    """Read a committed audio trace from JSONL. A thin alias for discoverability.

    Exists so that the audio fixtures have an obvious front door: someone reading
    `fixtures/audio/traces/` should find the loader from this module rather than
    having to know that `lab.trace.io` is where traces come from.
    """
    return read_jsonl(path)
