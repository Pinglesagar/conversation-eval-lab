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
transport — would buy one thing this cannot measure: barge-in. It would cost
three things this depends on.

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

So: half-duplex, and honest about it. `interruption_started` and
`interruption_acknowledged` stay reserved and unemitted (see `lab.trace.schema`),
no metric in this repo claims to measure barge-in, and a v2 duplex adapter can
emit them without a schema change.

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
*   No barge-in, for the reasons above.
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
from lab.voice.wer import CorpusWER, trace_wer

__all__ = [
    "AUDIO_ADAPTER",
    "REPLAY_ADAPTER",
    "GATE_PAYLOAD_KEY",
    "GateVerdict",
    "LatencyGate",
    "LatencyUnproven",
    "WERUnproven",
    "AudioTurn",
    "AudioAdapter",
    "audio_latency_report",
    "audio_wer_report",
    "latency_gate_verdict",
    "transcript_provenances",
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
            builder.caller_utterance(utterance, ts=t0, engine=perturbed.engine)
            builder.transcript_in(
                heard.text,
                confidence=heard.confidence,
                ts=t0,
                engine=heard.engine,
                **heard.trace_payload(),
            )

            self._emit_inner_events(builder, agent_turn, t0, t1)

            builder.agent_audio_first_byte(turn=turns, ts=t1, engine=self.agent_tts.name)
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
    return trace_wer(trace, backend=backend)  # type: ignore[arg-type]


def load_audio_trace(path: str | Path) -> Trace:
    """Read a committed audio trace from JSONL. A thin alias for discoverability.

    Exists so that the audio fixtures have an obvious front door: someone reading
    `fixtures/audio/traces/` should find the loader from this module rather than
    having to know that `lab.trace.io` is where traces come from.
    """
    return read_jsonl(path)
