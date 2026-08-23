"""The committed audio fixtures: do they still reproduce, and do they still add up?

WHAT THIS DEMONSTRATES
----------------------
That "we committed a fixture" is a claim with a proof. Every voice scenario in the
corpus has a trace under `fixtures/audio/traces/`, and this module re-runs the
generator's final pass against the committed clips and the committed transcript
cassette and asserts the result is **identical, event for event and float for
float**. A fixture that has drifted from the code is therefore a failing test
rather than a stale file nobody noticed.

Everything here is offline: no model, no network, no API key. The only inputs are
the JSON manifest, the transcript cassette, the Ogg Opus clips and the code. The
one dependency is `soundfile`, which decodes Ogg Opus and which `[dev]` installs
as a prebuilt wheel — declared there precisely so this module produces a verdict
instead of a skip. See `lab/voice/engines/audiofile.py` for why the committed
clips are Opus and not WAV.

WHY EXACT EQUALITY RATHER THAN "CLOSE ENOUGH"
---------------------------------------------
Because it can be. The clock is fake, the perturbations are seeded, the digests
are content-addressed and the agent's latency model is deterministic, so there is
no legitimate source of variation left. A tolerance would only be hiding one, and
the first thing it would hide is the thing worth catching: an adapter change that
quietly moved a timestamp.

WHAT THE FIXTURES CANNOT TELL YOU
---------------------------------
Every committed transcript has `provenance="reference"` — the ground truth
standing in for a speech engine nobody has installed — so the word error rate
these sessions would report is zero by construction and the harness refuses to
report it. That refusal is asserted here too, on the committed data, because it
is the single most important thing about this fixture set: it runs the whole audio
path honestly, and it is explicit about the one number it has not earned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.trace.io import read_jsonl
from lab.trace.schema import EventKind, Trace
from lab.voice.adapter import (
    REPLAY_ADAPTER,
    LatencyUnproven,
    WERUnproven,
    audio_latency_report,
    audio_wer_report,
    latency_gate_verdict,
    load_audio_trace,
    transcript_provenances,
)
from lab.voice.engines.audiofile import read_audio, soundfile_available, wav_bytes_for
from lab.voice.engines.base import audio_digest
from lab.voice.engines.stt import TranscriptCassette
from lab.voice.engines.tts import ClipManifest, FixtureTTS
from lab.voice.metrics import response_latency_report, speaking_times
from lab.voice.silence import silence_report
from scenarios.loader import load_corpus
from scripts.make_audio_fixtures import (
    SHOWCASE_SCENARIO,
    clip_is_unchanged,
    record_traces,
    voice_scenarios,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "audio"
TRACES = FIXTURES / "traces"


@pytest.fixture(scope="module")
def corpus():  # type: ignore[no-untyped-def]
    return load_corpus()


@pytest.fixture(scope="module")
def committed() -> dict[str, Trace]:
    """Every committed audio trace, keyed by scenario id."""
    return {path.stem: read_jsonl(path) for path in sorted(TRACES.glob("*.jsonl"))}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((FIXTURES / ClipManifest.FILENAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cassette() -> dict:
    return json.loads((FIXTURES / TranscriptCassette.FILENAME).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Coverage: every voice scenario has a trace
# --------------------------------------------------------------------------- #


def test_every_voice_scenario_has_a_committed_trace(corpus, committed) -> None:  # type: ignore[no-untyped-def]
    expected = {scenario.id for scenario in voice_scenarios(corpus)}
    assert expected, "the corpus has no voice suite; these fixtures would be vacuous"
    assert set(committed) == expected, (
        f"missing {sorted(expected - set(committed))}, "
        f"orphaned {sorted(set(committed) - expected)}"
    )


def test_the_fixture_set_covers_only_the_voice_suite(corpus, committed) -> None:  # type: ignore[no-untyped-def]
    """Stated rather than left to be wondered about.

    The other 47 rows are text scenarios; the loader forbids them from declaring
    perturbations, and an audio trace for a row with no audio conditions would be
    fiction dressed as a fixture.
    """
    suites = {s.suite for s in corpus.scenarios if s.id in committed}
    assert suites == {"voice"}


# --------------------------------------------------------------------------- #
# The headline: replay reproduces the committed bytes
# --------------------------------------------------------------------------- #


def test_replaying_the_fixtures_reproduces_the_committed_traces_exactly(corpus, committed) -> None:  # type: ignore[no-untyped-def]
    """Run the generator's final pass and diff against what is on disk."""
    produced = record_traces(
        voice_scenarios(corpus), corpus, root=FIXTURES, showcase=SHOWCASE_SCENARIO, write=False
    )
    assert set(produced) == set(committed)
    for scenario_id, trace in produced.items():
        assert trace.model_dump() == committed[scenario_id].model_dump(), (
            f"{scenario_id} no longer replays to its committed trace; "
            "run `make audio-fixtures` if the change was intended"
        )


def test_replay_is_stable_across_two_runs_in_one_process(corpus) -> None:  # type: ignore[no-untyped-def]
    """No hidden state: the second run of the same fixtures is the first run."""
    scenarios = voice_scenarios(corpus)[:2]
    first = record_traces(scenarios, corpus, root=FIXTURES, showcase=SHOWCASE_SCENARIO, write=False)
    second = record_traces(scenarios, corpus, root=FIXTURES, showcase=SHOWCASE_SCENARIO, write=False)
    for scenario_id in first:
        assert first[scenario_id].model_dump() == second[scenario_id].model_dump()


# --------------------------------------------------------------------------- #
# Trace integrity
# --------------------------------------------------------------------------- #


def test_every_committed_trace_is_ordered_and_tagged_as_a_replay(committed) -> None:  # type: ignore[no-untyped-def]
    for scenario_id, trace in committed.items():
        assert trace.is_ordered(), f"{scenario_id} has out-of-order timestamps"
        assert trace.adapter == REPLAY_ADAPTER, f"{scenario_id} is tagged {trace.adapter}"
        assert not trace.unknown_kinds(), f"{scenario_id} carries unrecognised event kinds"


def test_every_committed_trace_passed_the_timing_gate(committed) -> None:  # type: ignore[no-untyped-def]
    """These are the traces the case study draws latency from, so the gate had to run."""
    for scenario_id, trace in committed.items():
        assert latency_gate_verdict(trace) == "PASS", f"{scenario_id} has an unproven stopwatch"


def test_latency_is_reportable_for_the_whole_fixture_set(committed) -> None:  # type: ignore[no-untyped-def]
    report = audio_latency_report(list(committed.values()), scope="audio fixtures")
    assert report.sessions == len(committed)
    assert report.answered_turns == report.caller_turns
    assert report.time_to_first_byte.n == report.answered_turns
    # Every rate the report prints carries both terms; spot-check the text.
    text = report.to_text()
    assert f"{report.answered_turns}/{report.caller_turns}" in text


def test_the_scenario_latency_budget_is_checkable_from_the_trace(corpus, committed) -> None:  # type: ignore[no-untyped-def]
    """The corpus declares a budget per voice row; the fixture makes it measurable."""
    budgeted = [
        s for s in voice_scenarios(corpus) if s.voice and s.voice.latency_budget_ms is not None
    ]
    assert budgeted, "no voice row declares a latency budget; this test would be vacuous"
    for scenario in budgeted:
        trace = committed[scenario.id]
        report = response_latency_report(trace)
        p50 = report.time_to_first_byte.quantile(0.50)
        assert p50.reported, f"{scenario.id} has too few turns to report a median"
        # Not asserting the budget is met — that is a finding for the case study,
        # not a property of the fixture. Asserting only that it is answerable.
        assert p50.value_s is not None


def test_every_committed_trace_records_engine_identity(committed) -> None:  # type: ignore[no-untyped-def]
    for scenario_id, trace in committed.items():
        attributed = trace.events_of_kind(
            EventKind.AUDIO_EMITTED, EventKind.TRANSCRIPT_IN, EventKind.TRANSCRIPT_OUT
        )
        assert attributed, f"{scenario_id} has no attributable audio events"
        for event in attributed:
            assert event.engine, f"{scenario_id} has an unattributed {event.kind}"
            assert event.engine.startswith("replay:"), (
                f"{scenario_id} claims a live engine {event.engine!r} in a replay"
            )


def test_the_perturbation_each_scenario_declares_is_the_one_in_its_trace(corpus, committed) -> None:  # type: ignore[no-untyped-def]
    """The corpus is the plan; the trace is the record. They have to agree."""
    for scenario in voice_scenarios(corpus):
        assert scenario.voice is not None
        declared = [name for name, _params in scenario.voice.chain()]
        trace = committed[scenario.id]
        caller_audio = [
            e for e in trace.events_of_kind(EventKind.AUDIO_EMITTED) if e.actor == "caller"
        ]
        assert caller_audio, f"{scenario.id} has no caller audio"
        for event in caller_audio:
            recorded = [d["name"] for d in event.get("perturbations", [])]
            assert recorded == declared, f"{scenario.id}: declared {declared}, recorded {recorded}"


def test_achieved_perturbation_strength_is_recorded_next_to_the_request(committed) -> None:  # type: ignore[no-untyped-def]
    """A target is not a result, so both are in the trace."""
    for scenario_id, trace in committed.items():
        for event in trace.events_of_kind(EventKind.AUDIO_EMITTED):
            for descriptor in event.get("perturbations", []):
                assert descriptor["measured"], (
                    f"{scenario_id}: {descriptor['name']} recorded no measured strength"
                )


def test_the_showcase_scenario_is_the_only_one_with_agent_audio(committed) -> None:  # type: ignore[no-untyped-def]
    """The documented size trade, asserted so it cannot drift silently."""
    with_agent_audio = {
        scenario_id
        for scenario_id, trace in committed.items()
        if trace.first(EventKind.AGENT_AUDIO_COMPLETE) is not None
    }
    assert with_agent_audio == {SHOWCASE_SCENARIO}


def test_the_showcase_scenario_yields_real_speaking_times(committed) -> None:  # type: ignore[no-untyped-def]
    times = speaking_times(committed[SHOWCASE_SCENARIO])
    assert times and all(t > 0 for t in times)


def test_the_showcase_scenario_reaches_a_booking(committed) -> None:  # type: ignore[no-untyped-def]
    """Chosen for the most evidence per committed byte; this is the evidence."""
    trace = committed[SHOWCASE_SCENARIO]
    assert "create_booking" in trace.tool_names()
    assert trace.handoffs(), "a booking that involved no handoff is not the multi-agent path"


def test_dead_air_is_analysable_from_the_committed_traces(committed) -> None:  # type: ignore[no-untyped-def]
    """A downstream consumer that has never heard of audio still works on these."""
    report = silence_report(committed[SHOWCASE_SCENARIO])
    assert report is not None


# --------------------------------------------------------------------------- #
# The refusal, on the committed data
# --------------------------------------------------------------------------- #


def test_the_committed_transcripts_are_references_and_wer_is_refused(committed) -> None:  # type: ignore[no-untyped-def]
    for scenario_id, trace in committed.items():
        counts = transcript_provenances(trace)
        assert set(counts) == {"reference"}, f"{scenario_id} claims {sorted(counts)}"
        with pytest.raises(WERUnproven) as caught:
            audio_wer_report(trace)
        assert "setup_audio.sh" in str(caught.value)


def test_the_cassette_agrees_with_the_traces_about_provenance(cassette) -> None:  # type: ignore[no-untyped-def]
    provenances = {entry["provenance"] for entry in cassette["entries"].values()}
    assert provenances == {"reference"}


def test_a_reference_transcript_really_would_score_zero(committed) -> None:  # type: ignore[no-untyped-def]
    """The reason for the refusal, demonstrated rather than asserted.

    Bypassing the refusal and scoring the committed session directly gives 0.0%
    word error on the row that was recorded through a telephone band with pink
    noise at 6 dB SNR. That is the number the refusal exists to suppress.
    """
    from lab.voice.wer import trace_wer

    noisy = committed["voice-chain-telephone-then-noise"]
    micro = trace_wer(noisy).micro_wer(normalised=False)
    assert micro == 0.0, "the reference-transcript identity no longer holds"


# --------------------------------------------------------------------------- #
# Clips and manifest
# --------------------------------------------------------------------------- #


def test_the_manifest_indexes_every_committed_clip(manifest) -> None:  # type: ignore[no-untyped-def]
    on_disk = {path.name for path in (FIXTURES / "clips").glob("*.opus")}
    indexed = {Path(entry["file"]).name for entry in manifest["clips"].values()}
    assert indexed == on_disk, (
        f"unindexed on disk: {sorted(on_disk - indexed)}; missing: {sorted(indexed - on_disk)}"
    )


def test_the_manifest_records_where_the_audio_came_from(manifest) -> None:  # type: ignore[no-untyped-def]
    """The interesting question about a committed binary is always its provenance."""
    assert manifest["engine"]
    assert manifest["sample_rate"] == 16_000
    for entry in manifest["clips"].values():
        assert entry["text"]
        assert entry["role"] in ("caller", "agent")
        assert entry["samples"] > 0
        assert entry["bytes"] > 0


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_each_clip_decodes_to_the_length_the_manifest_claims(manifest) -> None:  # type: ignore[no-untyped-def]
    for entry in manifest["clips"].values():
        audio, rate = read_audio(FIXTURES / entry["file"])
        assert rate == entry["sample_rate"]
        assert audio.size == entry["samples"], f"{entry['file']} decoded to a different length"
        assert audio_digest(audio, rate)  # decodes deterministically


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_the_committed_clips_are_real_speech_not_silence(manifest) -> None:  # type: ignore[no-untyped-def]
    """A fixture set of silent files would pass every other test in this file."""
    import numpy as np

    for entry in manifest["clips"].values():
        audio, _rate = read_audio(FIXTURES / entry["file"])
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(np.square(audio))))
        assert peak > 0.05, f"{entry['file']} is effectively silent (peak {peak:.4f})"
        assert rms > 0.005, f"{entry['file']} has no energy (rms {rms:.5f})"
        # Speech is not a constant tone: its short-term energy varies a lot.
        frames = audio[: (audio.size // 1600) * 1600].reshape(-1, 1600)
        frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
        assert frame_rms.std() > 1e-3, f"{entry['file']} looks like a steady tone, not speech"


def test_the_fixtures_stay_small(manifest) -> None:  # type: ignore[no-untyped-def]
    """A size budget, because binary fixtures grow quietly and forever in git."""
    clip_bytes = sum(int(entry["bytes"]) for entry in manifest["clips"].values())
    trace_bytes = sum(path.stat().st_size for path in TRACES.glob("*.jsonl"))
    wav_equivalent = sum(wav_bytes_for(int(e["samples"])) for e in manifest["clips"].values())
    assert clip_bytes < 512 * 1024, f"clips are {clip_bytes / 1024:.0f} KiB"
    assert trace_bytes < 256 * 1024, f"traces are {trace_bytes / 1024:.0f} KiB"
    # The traces are what every check consumes, and they are the cheap half.
    assert trace_bytes < clip_bytes
    assert wav_equivalent > 5 * clip_bytes


# --------------------------------------------------------------------------- #
# Ogg is not byte-reproducible, and the generator is anyway
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_encoding_the_same_samples_twice_gives_different_bytes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The property that would otherwise make `make audio-fixtures` churn git.

    An Ogg bitstream carries a randomly chosen stream serial number, so two
    encodes of identical samples are two different files with identical audio.
    Discovered by regenerating the fixtures twice and diffing, which is why the
    generator compares decoded audio instead of bytes.
    """
    from lab.voice.engines.audiofile import write_audio

    audio, rate = read_audio(next(iter((FIXTURES / "clips").glob("*.opus"))))
    first = write_audio(tmp_path / "a.opus", audio, rate)
    second = write_audio(tmp_path / "b.opus", audio, rate)
    assert first.read_bytes() != second.read_bytes(), "Ogg became byte-reproducible; simplify"

    # And the thing that matters is stable: the decoded audio is the same.
    decoded_first, rate_first = read_audio(first)
    decoded_second, rate_second = read_audio(second)
    assert audio_digest(decoded_first, rate_first) == audio_digest(decoded_second, rate_second)


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_clip_is_unchanged_recognises_a_clip_it_just_wrote(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The generator's actual question: would encoding this again change anything?

    Answered against a clip written from the same pre-encode samples, which is
    exactly how `record_clips` uses it — a fresh synthesis compared with the
    committed encode of the previous synthesis of the same line.
    """
    import numpy as np

    from lab.voice.engines.audiofile import write_audio

    rate = 16_000
    t = np.arange(rate) / rate
    synthesised = 0.4 * np.sin(2 * np.pi * 240 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
    path = write_audio(tmp_path / "clip.opus", synthesised, rate)
    assert clip_is_unchanged(path, synthesised, rate) is True
    assert clip_is_unchanged(path, synthesised * 0.5, rate) is False
    assert clip_is_unchanged(path, synthesised, 8_000) is False


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_a_decoded_clip_is_not_the_same_question_as_the_samples_that_made_it(manifest) -> None:  # type: ignore[no-untyped-def]
    """Generational loss, recorded so nobody 'fixes' `clip_is_unchanged` into a bug.

    Opus is lossy, so re-encoding audio that has already been through the codec
    loses more. `clip_is_unchanged(path, decode(path))` is therefore False, and
    correctly so: it is asking whether encoding *these samples* reproduces the
    committed clip, and encoding a decoded clip does not.

    The consequence for the generator is that its comparison only works when it
    is handed a fresh synthesis, which is the only thing it ever hands it.
    """
    entry = next(iter(manifest["clips"].values()))
    path = FIXTURES / entry["file"]
    decoded, rate = read_audio(path)
    assert clip_is_unchanged(path, decoded, rate) is False


def test_clip_is_unchanged_is_false_for_a_file_that_does_not_exist(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import numpy as np

    assert clip_is_unchanged(tmp_path / "absent.opus", np.zeros(160), 16_000) is False


def test_the_manifest_records_how_many_clips_were_reused(manifest) -> None:  # type: ignore[no-untyped-def]
    """A regenerated-from-scratch set reports 0; a no-op regeneration reports all."""
    assert manifest["clips_unchanged"] == len(manifest["clips"]), (
        "the committed manifest should be the product of a no-op regeneration, "
        "so every clip was matched rather than rewritten"
    )


def test_the_write_up_exists_and_states_the_measured_arithmetic() -> None:
    text = (FIXTURES / "audio_fixtures.md").read_text(encoding="utf-8")
    assert "Ogg Opus (committed)" in text
    assert "reference" in text
    assert "make audio-fixtures" in text


def test_the_generator_is_importable_without_a_tts_engine() -> None:
    """Importing the generator must not require the models it exists to drive."""
    import scripts.make_audio_fixtures as generator

    assert generator.SHOWCASE_SCENARIO == SHOWCASE_SCENARIO
    assert generator.FIXTURE_ROOT == Path("fixtures/audio")


def test_fixture_tts_can_speak_every_line_the_traces_contain(committed) -> None:  # type: ignore[no-untyped-def]
    """No trace may reference audio the committed clips cannot produce."""
    engine = FixtureTTS(FIXTURES)
    for scenario_id, trace in committed.items():
        for text in trace.texts(actor="caller"):
            result = engine.synthesise(text, sample_rate=16_000)
            assert result.replayed, f"{scenario_id}: {text!r} was not a replay"


def test_load_audio_trace_is_the_documented_front_door() -> None:
    path = TRACES / f"{SHOWCASE_SCENARIO}.jsonl"
    assert load_audio_trace(path).model_dump() == read_jsonl(path).model_dump()


def test_an_unproven_trace_mixed_into_the_set_refuses_the_report(committed, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Guard on the guard: the pooled refusal has to fire on real fixture data."""
    from lab.trace.build import TraceBuilder
    from lab.clock import FakeClock

    builder = TraceBuilder(scenario_id="unproven", adapter=REPLAY_ADAPTER, clock=FakeClock())
    builder.session_start()
    builder.session_end()
    with pytest.raises(LatencyUnproven):
        audio_latency_report([*committed.values(), builder.build()])
