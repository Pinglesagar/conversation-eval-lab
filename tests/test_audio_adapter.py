"""The audio adapter: boundary discipline, provenance, and two refusals.

WHAT THIS DEMONSTRATES
----------------------
Four claims the adapter makes about itself, each turned into a test that would
fail if the claim stopped being true:

1.  **Harness compute stays out of the reported latency.** The same session is
    run twice, once with half a second of synthesis and transcription cost
    injected per turn, and the recovered latencies must be *bit-identical*. This
    is the same mutation-style proof `lab.voice.calibration` uses on the text
    path, applied to a pipeline with three more stages in it.
2.  **No latency without a passing calibration gate.** A failing gate and a
    skipped gate both produce a refusal, and the refusal is decided from the
    trace, so it survives being written to disk and read back.
3.  **No word error rate against reference transcripts.** With a real recorded
    transcript the same call answers, and answers with a non-zero rate.
4.  **The perturbation that ran is in the trace, with its achieved strength** —
    not the requested strength, and not only in a log line.

Plus the one ordering decision the adapter makes differently from the text
driver, tested directly: `caller_utterance` is emitted before `transcript_in`, and
the test shows what the other order does to a word error rate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lab.clock import Clock, FakeClock
from lab.simulator import AgentTurn, Handoff, ScriptedCaller, ToolInvocation
from lab.trace.build import TraceBuilder
from lab.trace.io import read_jsonl, write_jsonl
from lab.trace.schema import EventKind, Trace
from lab.voice.adapter import (
    AUDIO_ADAPTER,
    GATE_PAYLOAD_KEY,
    REPLAY_ADAPTER,
    AudioAdapter,
    LatencyGate,
    LatencyUnproven,
    WERUnproven,
    audio_latency_report,
    audio_wer_report,
    latency_gate_verdict,
    load_audio_trace,
    transcript_provenances,
)
from lab.voice.calibration import CalibrationTolerance, recover_response_latencies
from lab.voice.engines.base import DEFAULT_SAMPLE_RATE, SynthesisResult, Transcription
from lab.voice.engines.stt import RecordedSTT, TranscriptCassette
from lab.voice.engines.tts import ClipManifest, FixtureTTS
from lab.voice.metrics import speaking_times
from tablemate import build_agent
from tests.audio_doubles import (
    SECONDS_PER_WORD,
    EchoSTT,
    ScriptedSTT,
    ToneTTS,
    expected_duration_s,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "audio"

SCRIPT = ["Table for two on Friday at eight, please.", "Marta Reyes."]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class SlowEngine:
    """Wraps an engine and spends `seconds` of clock time on every call.

    The instrument for claim 1. Injecting harness cost through the *injected
    clock* rather than through `time.sleep` means the overhead is exact and free
    under a `FakeClock`, so the comparison is between two deterministic runs and
    not between two samples of scheduler noise.
    """

    def __init__(self, inner: Any, clock: Clock, seconds: float) -> None:
        self.inner = inner
        self.clock = clock
        self.seconds = seconds
        self.name = inner.name
        self.is_replay = getattr(inner, "is_replay", False)

    def available(self) -> bool:
        return self.inner.available()

    def describe(self) -> str:
        return f"slow({self.seconds}s) over {self.inner.describe()}"

    def synthesise(self, text: str, **kwargs: Any) -> SynthesisResult:
        self.clock.sleep(self.seconds)
        return self.inner.synthesise(text, **kwargs)

    def transcribe(self, audio: Any, **kwargs: Any) -> Transcription:
        self.clock.sleep(self.seconds)
        return self.inner.transcribe(audio, **kwargs)


def run_session(
    *,
    stt: Any = None,
    perturbations: Any = (),
    gate: LatencyGate | None = None,
    script: list[str] | None = None,
    overhead_s: float = 0.0,
    clip_dir: Path | None = None,
    synthesise_agent_audio: bool = True,
    scenario_id: str = "audio-unit",
    max_turns: int = 12,
) -> tuple[Trace, AudioAdapter]:
    """One deterministic audio session against the real TableMate agent."""
    lines = script if script is not None else SCRIPT
    clock = FakeClock()
    caller_tts: Any = ToneTTS(name="tts:test-tone-caller", hz=180.0)
    agent_tts: Any = ToneTTS(name="tts:test-tone-agent", hz=240.0)
    transcriber: Any = stt if stt is not None else ScriptedSTT(lines * 6)
    if overhead_s:
        caller_tts = SlowEngine(caller_tts, clock, overhead_s)
        agent_tts = SlowEngine(agent_tts, clock, overhead_s)
        transcriber = SlowEngine(transcriber, clock, overhead_s)
    adapter = AudioAdapter(
        tts=caller_tts,
        stt=transcriber,
        agent_tts=agent_tts,
        perturbations=perturbations,
        gate=gate if gate is not None else LatencyGate.skipped(),
        clip_dir=clip_dir,
        synthesise_agent_audio=synthesise_agent_audio,
    )
    trace = adapter.run(
        scenario_id=scenario_id,
        agent=build_agent(clock=clock),
        caller=ScriptedCaller(lines),
        clock=clock,
        session_id="unit-session",
        max_turns=max_turns,
    )
    return trace, adapter


# --------------------------------------------------------------------------- #
# Shape of the trace
# --------------------------------------------------------------------------- #


def test_a_session_emits_the_audio_boundary_events() -> None:
    trace, _ = run_session()
    kinds = {event.kind for event in trace.events}
    assert {
        EventKind.SESSION_START,
        EventKind.AUDIO_EMITTED,
        EventKind.TRANSCRIPT_IN,
        EventKind.CALLER_UTTERANCE,
        EventKind.AGENT_AUDIO_FIRST_BYTE,
        EventKind.TRANSCRIPT_OUT,
        EventKind.AGENT_AUDIO_COMPLETE,
        EventKind.AGENT_UTTERANCE,
        EventKind.SESSION_END,
    } <= kinds


def test_the_trace_is_ordered() -> None:
    """The schema's core invariant. Every stamp is a clock read or arithmetic on one."""
    trace, _ = run_session(perturbations=[("add_noise", {"snr_db": 10.0})])
    assert trace.is_ordered()


def test_this_adapter_emits_no_interruption_events() -> None:
    """Barge-in is constructed, not discovered — and *this* adapter constructs nothing.

    `lab.voice.interaction.emit_barge_in` does write the two kinds, from timings a
    scenario hands in. The claim under test is narrower and is the one that
    matters: a half-duplex turn loop cannot find an overlap, so it must not put
    one on a trace it produced.
    """
    trace, _ = run_session()
    assert not trace.events_of_kind(
        EventKind.INTERRUPTION_STARTED, EventKind.INTERRUPTION_ACKNOWLEDGED
    )


def test_the_agent_hears_the_transcript_and_never_the_script() -> None:
    """The whole point of the STT leg: the SUT sees what was heard, not what was said."""
    heard = ["Table for two on Friday at ate, please.", "Marta Rays."]
    trace, _ = run_session(stt=ScriptedSTT(heard))
    assert trace.texts(actor="caller") == SCRIPT[: len(heard)]
    transcripts = [str(e.get("text")) for e in trace.events_of_kind(EventKind.TRANSCRIPT_IN)]
    assert transcripts == heard


def test_session_end_records_how_the_call_ended() -> None:
    trace, _ = run_session()
    end = trace.last(EventKind.SESSION_END)
    assert end is not None
    assert end.get("reason") == "caller_hung_up"
    assert end.get("turns") == len(SCRIPT)


def test_max_turns_truncates_rather_than_raising() -> None:
    trace, _ = run_session(script=["one two", "three four", "five six"], max_turns=2)
    end = trace.last(EventKind.SESSION_END)
    assert end is not None and end.get("reason") == "max_turns"
    assert end.get("turns") == 2


def test_a_blank_agent_turn_emits_no_agent_audio() -> None:
    """Synthesising silence would put a zero-length clip in the speaking distribution."""

    def silent_agent(_utterance: str) -> AgentTurn:
        return AgentTurn(text="", agent="SilentAgent")

    clock = FakeClock()
    adapter = AudioAdapter(tts=ToneTTS(), stt=ScriptedSTT(["hello"]), gate=LatencyGate.skipped())
    trace = adapter.run(
        scenario_id="silent", agent=silent_agent, caller=ScriptedCaller(["hello"]), clock=clock
    )
    assert trace.first(EventKind.AGENT_AUDIO_COMPLETE) is None
    # The response boundary is still there: the agent did reply, with nothing.
    assert trace.first(EventKind.AGENT_AUDIO_FIRST_BYTE) is not None
    assert trace.is_ordered()


# --------------------------------------------------------------------------- #
# Claim 1: harness compute stays out of the reported latency
# --------------------------------------------------------------------------- #


def test_recovered_latency_is_unchanged_by_half_a_second_of_harness_cost() -> None:
    """The mutation-style proof, on the audio path.

    500 ms of synthesis plus 500 ms of transcription plus 500 ms of reply
    synthesis, per turn, injected either side of the measurement boundary. If any
    of it leaked into the window the samples would move by hundreds of
    milliseconds. They must be identical to the last bit.
    """
    fast, _ = run_session(overhead_s=0.0)
    slow, _ = run_session(overhead_s=0.5)
    assert recover_response_latencies(fast) == recover_response_latencies(slow)
    # And the overhead really was injected: the session got much longer.
    assert slow.duration() > fast.duration() + 1.0


def test_the_caller_utterance_marks_the_end_of_the_callers_speech() -> None:
    """The left edge of the window is when the caller stopped talking.

    So the clip's own duration sits *before* `caller_utterance`, not inside the
    latency window — otherwise a wordier caller would look like a slower agent.
    """
    trace, _ = run_session()
    emitted = trace.events_of_kind(EventKind.AUDIO_EMITTED)[0]
    first_caller = trace.events_of_kind(EventKind.CALLER_UTTERANCE)[0]
    assert first_caller.ts - emitted.ts == pytest.approx(
        expected_duration_s(SCRIPT[0]), abs=1e-9
    )


def test_speaking_time_is_the_audible_length_of_the_reply() -> None:
    """`agent_audio_complete - agent_audio_first_byte` must mean speaking time."""
    trace, _ = run_session()
    spoken = [str(e.get("text")) for e in trace.events_of_kind(EventKind.AGENT_UTTERANCE)]
    expected = [expected_duration_s(text) for text in spoken]
    assert speaking_times(trace) == pytest.approx(expected, abs=1e-9)


def test_synthesis_cost_is_recorded_separately_so_it_can_be_excluded() -> None:
    trace, _ = run_session(overhead_s=0.25)
    emitted = trace.events_of_kind(EventKind.AUDIO_EMITTED)
    assert emitted
    for event in emitted:
        assert "synthesis_s" in event.payload


def test_tool_and_handoff_events_are_flagged_as_estimated() -> None:
    """No timing figure in this repo may be derived from an interpolated instant."""

    def tooling_agent(_utterance: str) -> AgentTurn:
        return AgentTurn(
            text="Let me look.",
            agent="BookingAgent",
            handoff=Handoff(from_agent="GreeterAgent", to_agent="BookingAgent"),
            tools=[ToolInvocation(name="search_tables", args={"party_size": 2}, result="ok")],
        )

    clock = FakeClock()
    adapter = AudioAdapter(tts=ToneTTS(), stt=ScriptedSTT(["hello"]), gate=LatencyGate.skipped())
    trace = adapter.run(
        scenario_id="tools", agent=tooling_agent, caller=ScriptedCaller(["hello"]), clock=clock
    )
    inner = trace.events_of_kind(
        EventKind.AGENT_HANDOFF, EventKind.TOOL_CALL, EventKind.TOOL_RESULT
    )
    assert len(inner) == 3
    assert all(event.get("ts_estimated") is True for event in inner)
    t0 = trace.events_of_kind(EventKind.CALLER_UTTERANCE)[0].ts
    t1 = trace.events_of_kind(EventKind.AGENT_AUDIO_FIRST_BYTE)[0].ts
    assert all(t0 <= event.ts <= t1 for event in inner)
    assert trace.is_ordered()


# --------------------------------------------------------------------------- #
# Claim 2: no latency without a passing gate
# --------------------------------------------------------------------------- #


def test_a_passing_gate_is_recorded_and_latency_is_reported() -> None:
    trace, _ = run_session(gate=LatencyGate(delays_s=(0.1, 1.0), repeats=5))
    assert latency_gate_verdict(trace) == "PASS"
    report = audio_latency_report(trace)
    assert report.answered_turns == len(SCRIPT)


def test_a_failing_gate_refuses_latency_but_still_produces_the_trace() -> None:
    """A broken stopwatch does not invalidate a transcript, so the session runs."""
    impossible = LatencyGate(
        tolerance=CalibrationTolerance(max_rel_error=1e-9, max_stdev_s=1e-9),
        delays_s=(0.1,),
        repeats=4,
    )
    assert impossible.verdict == "FAIL"
    trace, _ = run_session(gate=impossible)
    assert latency_gate_verdict(trace) == "FAIL"
    assert trace.texts(actor="agent"), "the conversation is still evidence"
    with pytest.raises(LatencyUnproven) as caught:
        audio_latency_report(trace)
    assert "make calibrate" in str(caught.value)


def test_a_skipped_gate_is_not_a_pass() -> None:
    trace, _ = run_session(gate=LatencyGate.skipped())
    assert latency_gate_verdict(trace) == "NOT_RUN"
    with pytest.raises(LatencyUnproven):
        audio_latency_report(trace)


def test_a_trace_with_no_recorded_verdict_defaults_to_refusal() -> None:
    """Absence is NOT_RUN, never PASS: a check nobody switched on is not a check."""
    builder = TraceBuilder(scenario_id="bare", adapter=AUDIO_ADAPTER, clock=FakeClock())
    builder.session_start()
    builder.session_end()
    assert latency_gate_verdict(builder.build()) == "NOT_RUN"


def test_one_unproven_session_refuses_the_whole_pooled_report() -> None:
    """A p95 is exactly the statistic one contaminated session can own."""
    good, _ = run_session(gate=LatencyGate(delays_s=(0.1,), repeats=4))
    bad, _ = run_session(gate=LatencyGate.skipped())
    with pytest.raises(LatencyUnproven) as caught:
        audio_latency_report([good, bad])
    assert "1/2" in str(caught.value)


def test_an_empty_trace_list_is_refused_rather_than_averaged() -> None:
    with pytest.raises(LatencyUnproven):
        audio_latency_report([])


def test_the_refusal_survives_a_round_trip_to_disk(tmp_path: Path) -> None:
    """The gate verdict is in the data, so a downloaded trace refuses too."""
    trace, _ = run_session(gate=LatencyGate.skipped())
    path = write_jsonl(trace, tmp_path / "session.jsonl")
    reloaded = load_audio_trace(path)
    assert latency_gate_verdict(reloaded) == "NOT_RUN"
    with pytest.raises(LatencyUnproven):
        audio_latency_report(reloaded)
    assert reloaded.model_dump() == read_jsonl(path).model_dump()


# --------------------------------------------------------------------------- #
# Claim 3: no WER against reference transcripts
# --------------------------------------------------------------------------- #


def test_reference_transcripts_are_refused_and_the_message_says_why() -> None:
    trace, _ = run_session(stt=ScriptedSTT(SCRIPT, provenance="reference"))
    assert transcript_provenances(trace) == {"reference": len(SCRIPT)}
    with pytest.raises(WERUnproven) as caught:
        audio_wer_report(trace)
    message = str(caught.value)
    assert "0.0%" in message
    assert "setup_audio.sh" in message


def test_a_recorded_transcript_is_scored_and_the_rate_is_not_zero() -> None:
    """The positive path: a real mishearing produces a real word error rate."""
    misheard = ["Table for two on Friday at ate, please.", "Marta Rays."]
    trace, _ = run_session(stt=ScriptedSTT(misheard, provenance="recorded"))
    corpus = audio_wer_report(trace)
    assert corpus.n == len(misheard)
    micro = corpus.micro_wer(normalised=True)
    assert micro is not None and micro > 0.0


def test_a_session_with_no_transcripts_is_refused_rather_than_scored_zero() -> None:
    builder = TraceBuilder(scenario_id="bare", adapter=AUDIO_ADAPTER, clock=FakeClock())
    builder.session_start(**{GATE_PAYLOAD_KEY: "PASS"})
    builder.caller_utterance("hello")
    builder.session_end()
    with pytest.raises(WERUnproven) as caught:
        audio_wer_report(builder.build())
    assert "no transcript_in" in str(caught.value)


def test_a_mixed_cassette_is_refused_on_the_reference_turns() -> None:
    """Partial honesty is not honesty: one stand-in contaminates the corpus figure."""

    class MixedSTT(ScriptedSTT):
        def transcribe(self, audio: Any, **kwargs: Any) -> Transcription:
            result = super().transcribe(audio, **kwargs)
            provenance = "recorded" if self.index == 1 else "reference"
            return result.model_copy(update={"provenance": provenance})

    trace, _ = run_session(stt=MixedSTT(SCRIPT))
    counts = transcript_provenances(trace)
    assert counts == {"recorded": 1, "reference": 1}
    with pytest.raises(WERUnproven):
        audio_wer_report(trace)


# --------------------------------------------------------------------------- #
# The ordering decision, and what the other order costs
# --------------------------------------------------------------------------- #


def test_caller_utterance_is_emitted_before_its_transcript() -> None:
    trace, _ = run_session()
    kinds = [
        event.kind
        for event in trace.events
        if event.kind in (EventKind.CALLER_UTTERANCE, EventKind.TRANSCRIPT_IN)
    ]
    assert kinds == [EventKind.CALLER_UTTERANCE, EventKind.TRANSCRIPT_IN] * len(SCRIPT)


def test_the_adapters_order_pairs_each_reference_with_its_own_hypothesis() -> None:
    misheard = ["Table for two on Friday at ate, please.", "Marta Rays."]
    trace, _ = run_session(stt=ScriptedSTT(misheard, provenance="recorded"))
    pairs = [
        (str(a.get("text")), str(b.get("text")))
        for a, b in trace.event_pairs(EventKind.CALLER_UTTERANCE, EventKind.TRANSCRIPT_IN)
    ]
    assert pairs == list(zip(SCRIPT, misheard, strict=True))


def test_transcript_first_ordering_would_shift_every_pair_by_one_turn() -> None:
    """Why the adapter emits `caller_utterance` first — demonstrated, not asserted.

    `Trace.event_pairs` walks the event list and greedily takes the next closer.
    Emit the transcript before the utterance it belongs to and turn N's reference
    is scored against turn N+1's hypothesis: a wrong WER, on every turn, with
    nothing in the trace looking out of place. On a text adapter the two strings
    are identical so it is invisible; on an audio adapter it is the metric.
    """
    reversed_order = TraceBuilder(
        scenario_id="reversed", adapter=AUDIO_ADAPTER, clock=FakeClock()
    )
    reversed_order.session_start()
    for said, heard in (("one two three", "one two free"), ("four five six", "four five sicks")):
        reversed_order.transcript_in(heard, ts=0.0)
        reversed_order.caller_utterance(said, ts=0.0)
    reversed_order.session_end()
    pairs = [
        (str(a.get("text")), str(b.get("text")))
        for a, b in reversed_order.build().event_pairs(
            EventKind.CALLER_UTTERANCE, EventKind.TRANSCRIPT_IN
        )
    ]
    assert pairs == [("one two three", "four five sicks")], "the off-by-one is real"
    assert len(pairs) == 1, "and it silently drops the last turn from the denominator"


# --------------------------------------------------------------------------- #
# Claim 4: the perturbation that ran is in the trace
# --------------------------------------------------------------------------- #


def test_perturbation_descriptors_reach_the_trace_with_measured_strength() -> None:
    trace, _ = run_session(
        perturbations=[("add_noise", {"snr_db": 12.0, "kind": "pink", "seed": 7})]
    )
    caller_audio = [
        event for event in trace.events_of_kind(EventKind.AUDIO_EMITTED) if event.actor == "caller"
    ]
    assert caller_audio
    for event in caller_audio:
        descriptors = event.get("perturbations")
        assert descriptors, "the chain that ran must be recorded per clip"
        first = descriptors[0]
        assert first["name"] == "add_noise"
        assert first["params"]["snr_db"] == 12.0
        # The measured value, not only the request: a target is not a result.
        assert "achieved_snr_db" in first["measured"]
        assert first["measured"]["achieved_snr_db"] == pytest.approx(12.0, abs=1.0)
        assert "noise@12.0dB/pink" in str(event.get("perturbation_chain"))


def test_an_unperturbed_session_says_clean_explicitly() -> None:
    """An absent key is indistinguishable from an adapter that forgot."""
    trace, _ = run_session(perturbations=())
    caller_audio = [
        e for e in trace.events_of_kind(EventKind.AUDIO_EMITTED) if e.actor == "caller"
    ]
    assert all(event.get("perturbation_chain") == "clean" for event in caller_audio)
    assert all(event.get("perturbations") == [] for event in caller_audio)


def test_the_chain_order_is_preserved_because_it_does_not_commute() -> None:
    trace, _ = run_session(
        perturbations=[("add_noise", {"snr_db": 15.0}), ("telephone_band", {})]
    )
    event = [e for e in trace.events_of_kind(EventKind.AUDIO_EMITTED) if e.actor == "caller"][0]
    assert [d["name"] for d in event.get("perturbations")] == ["add_noise", "telephone_band"]
    assert " -> " in str(event.get("perturbation_chain"))


def test_the_session_records_the_plan_as_well_as_the_achieved_strength() -> None:
    trace, _ = run_session(perturbations=[("add_noise", {"snr_db": 6.0})])
    start = trace.first(EventKind.SESSION_START)
    assert start is not None
    assert "add_noise(snr_db=6.0)" == start.get("perturbation_plan")


def test_the_stt_leg_receives_the_perturbed_audio_not_the_clean_synthesis() -> None:
    """A length-changing perturbation is the probe: the sample count must move."""
    echo = EchoSTT()
    clean_echo = EchoSTT()
    run_session(stt=echo, perturbations=[("resample_speed", {"factor": 1.5})])
    run_session(stt=clean_echo, perturbations=())
    assert echo.seen and clean_echo.seen
    assert echo.seen[0] != clean_echo.seen[0]
    assert echo.seen[0] == pytest.approx(clean_echo.seen[0] / 1.5, rel=0.01)


# --------------------------------------------------------------------------- #
# Engine identity, replay tagging and files
# --------------------------------------------------------------------------- #


def test_every_audio_and_transcript_event_names_the_engine_that_made_it() -> None:
    trace, _ = run_session()
    attributed = trace.events_of_kind(
        EventKind.AUDIO_EMITTED,
        EventKind.TRANSCRIPT_IN,
        EventKind.TRANSCRIPT_OUT,
        EventKind.AGENT_AUDIO_FIRST_BYTE,
        EventKind.AGENT_AUDIO_COMPLETE,
    )
    assert attributed
    assert all(event.engine for event in attributed)


def test_caller_and_agent_engines_are_distinguishable_in_the_trace() -> None:
    """A report that cannot tell the two synthesisers apart cannot attribute a regression."""
    trace, _ = run_session()
    engines = {
        event.actor: event.engine
        for event in trace.events_of_kind(EventKind.AUDIO_EMITTED)
    }
    assert engines["caller"] != engines["agent"]


def test_a_fully_fixture_driven_session_is_tagged_as_a_replay() -> None:
    """A replayed session is evidence about the harness, not about the engines."""
    manifest = ClipManifest.load(FIXTURES)
    fixture_tts = FixtureTTS(FIXTURES, manifest=manifest)
    cassette = TranscriptCassette.load(FIXTURES)
    adapter = AudioAdapter(
        tts=fixture_tts, stt=RecordedSTT(cassette), gate=LatencyGate.skipped()
    )
    assert adapter.is_replay is True
    assert adapter.adapter_tag == REPLAY_ADAPTER

    live = AudioAdapter(tts=ToneTTS(), stt=ScriptedSTT([]), gate=LatencyGate.skipped())
    assert live.is_replay is False
    assert live.adapter_tag == AUDIO_ADAPTER


def test_clips_are_written_when_a_directory_is_given(tmp_path: Path) -> None:
    trace, adapter = run_session(clip_dir=tmp_path)
    written = sorted(path.name for path in tmp_path.glob("*.wav"))
    assert written, "file-based means the files exist"
    recorded = [
        str(event.get("clip"))
        for event in trace.events_of_kind(EventKind.AUDIO_EMITTED)
        if event.get("clip")
    ]
    assert sorted(recorded) == written
    assert all(turn.caller_clip for turn in adapter.turns)


def test_scratch_clips_do_not_accumulate_but_the_encoder_still_ran() -> None:
    """The file path is exercised even when nothing is kept, or it is a comment."""
    trace, _ = run_session(clip_dir=None)
    emitted = trace.events_of_kind(EventKind.AUDIO_EMITTED)
    assert emitted
    for event in emitted:
        assert event.get("clip") is None
        assert int(event.get("encoded_bytes")) > 0
        assert event.get("encoded_format") == ".wav"


def test_encoded_bytes_and_pcm_bytes_are_reported_separately() -> None:
    """PCM16 is what crossed the boundary; the encoded size is what a file costs."""
    trace, _ = run_session()
    event = trace.events_of_kind(EventKind.AUDIO_EMITTED)[0]
    assert int(event.get("num_bytes")) == pytest.approx(
        int(event.get("duration_s") * DEFAULT_SAMPLE_RATE) * 2, abs=2
    )
    assert int(event.get("encoded_bytes")) > 0


def test_the_per_turn_record_mirrors_the_trace() -> None:
    trace, adapter = run_session()
    assert [turn.caller_text for turn in adapter.turns] == trace.texts(actor="caller")
    assert [turn.agent_text for turn in adapter.turns] == trace.texts(actor="agent")
    assert all(turn.caller_duration_s > 0 for turn in adapter.turns)
    assert all(turn.response_latency_s >= 0 for turn in adapter.turns)


def test_describe_names_the_engines_and_the_gate() -> None:
    _, adapter = run_session()
    described = adapter.describe()
    assert "tts:test-tone-caller" in described
    assert "NOT_RUN" in described


def test_describing_an_adapter_does_not_run_the_calibration_sweep() -> None:
    """A read-only accessor that starts a hundred-turn computation is a trap."""
    gate = LatencyGate(delays_s=(0.1,), repeats=4)
    adapter = AudioAdapter(tts=ToneTTS(), stt=ScriptedSTT([]), gate=gate)
    assert gate.cached_verdict == "PENDING"
    assert "PENDING" in adapter.describe()
    assert repr(gate).count("PENDING") == 1
    assert gate.cached_verdict == "PENDING", "describe() computed the verdict"
    # Asking for it directly does run the sweep, once.
    assert gate.verdict == "PASS"
    assert gate.cached_verdict == "PASS"


def test_agent_audio_can_be_switched_off_entirely() -> None:
    """The committed-fixture trade: latency for every row, speaking time for one."""
    trace, _ = run_session(synthesise_agent_audio=False)
    assert trace.first(EventKind.AGENT_AUDIO_COMPLETE) is None
    assert trace.first(EventKind.AGENT_AUDIO_FIRST_BYTE) is not None, (
        "the response boundary is a clock read, not a synthesis, so it survives"
    )
    assert recover_response_latencies(trace), "latency is still reportable"
    assert not [e for e in trace.events_of_kind(EventKind.AUDIO_EMITTED) if e.actor == "agent"]


def test_loopback_transcription_lands_on_the_completion_event_when_asked() -> None:
    """Measuring our own synthesiser's intelligibility, opt-in and clearly labelled.

    It gets no event kind of its own: inventing one would imply the schema has an
    opinion about a figure that is about the harness, not the agent.
    """
    clock = FakeClock()
    stt = ScriptedSTT(["heard the caller", "heard the agent"], provenance="recorded")
    adapter = AudioAdapter(
        tts=ToneTTS(),
        stt=stt,
        gate=LatencyGate.skipped(),
        transcribe_agent_audio=True,
    )
    trace = adapter.run(
        scenario_id="loopback",
        agent=lambda _u: AgentTurn(text="Certainly, that is booked.", agent="BookingAgent"),
        caller=ScriptedCaller(["hello"]),
        clock=clock,
    )
    complete = trace.first(EventKind.AGENT_AUDIO_COMPLETE)
    assert complete is not None
    assert complete.get("loopback_text") == "heard the agent"
    assert complete.get("loopback_provenance") == "recorded"
    # And the caller-side transcript is untouched by it.
    assert trace.first(EventKind.TRANSCRIPT_IN).get("text") == "heard the caller"  # type: ignore[union-attr]


def test_loopback_is_off_by_default_because_it_measures_the_harness() -> None:
    trace, _ = run_session()
    complete = trace.first(EventKind.AGENT_AUDIO_COMPLETE)
    assert complete is not None
    assert "loopback_text" not in complete.payload


def test_clips_can_be_written_as_opus_when_a_dependency_is_present(tmp_path: Path) -> None:
    """The format the committed fixtures use, exercised through the adapter."""
    from lab.voice.engines.audiofile import soundfile_available

    if not soundfile_available():  # pragma: no cover - depends on the install
        pytest.skip("Ogg Opus needs soundfile/libsndfile>=1.1")
    clock = FakeClock()
    adapter = AudioAdapter(
        tts=ToneTTS(),
        stt=ScriptedSTT(["hello"]),
        gate=LatencyGate.skipped(),
        clip_dir=tmp_path,
        clip_format="opus",  # accepted with or without the leading dot
    )
    trace = adapter.run(
        scenario_id="opus",
        agent=lambda _u: AgentTurn(text="Certainly."),
        caller=ScriptedCaller(["hello"]),
        clock=clock,
    )
    assert adapter.clip_format == ".opus"
    written = sorted(tmp_path.glob("*.opus"))
    assert written
    event = trace.events_of_kind(EventKind.AUDIO_EMITTED)[0]
    assert event.get("encoded_format") == ".opus"
    # Opus is a compression, so the encoded clip is smaller than the raw PCM.
    assert int(event.get("encoded_bytes")) < int(event.get("num_bytes"))


def test_max_turns_must_be_positive() -> None:
    adapter = AudioAdapter(tts=ToneTTS(), stt=ScriptedSTT([]), gate=LatencyGate.skipped())
    with pytest.raises(ValueError):
        adapter.run(
            scenario_id="x",
            agent=lambda _u: AgentTurn(text="hi"),
            caller=ScriptedCaller(["hi"]),
            max_turns=0,
        )


def test_seconds_per_word_double_is_what_the_assertions_assume() -> None:
    """Guard on the double itself: silent drift here would weaken every duration test."""
    assert expected_duration_s("one two") == pytest.approx(2 * SECONDS_PER_WORD)
    assert expected_duration_s("") == pytest.approx(SECONDS_PER_WORD)
