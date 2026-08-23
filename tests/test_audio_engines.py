"""The engine layer: identity, availability, clip I/O, and replay strictness.

WHAT THIS DEMONSTRATES
----------------------
That the two things an engine is *for* — being identifiable and being honest
about whether it can run — are tested rather than assumed. Specifically:

*   every backend reports a specific identity string, and a replay backend
    reports the identity of whatever recorded the fixture rather than its own;
*   an absent engine raises a message that names `scripts/setup_audio.sh`, at the
    point of use, instead of an `ImportError` from inside somebody's library;
*   the audio digest is a function of the audio and changes when the audio does,
    which is the property that stops a cassette answering for sound it never
    heard;
*   the clip round trip really is lossless enough to be a fixture, measured on
    the committed Opus clips rather than asserted.

Everything here is offline. No model is downloaded, no network is touched, and
the only real audio involved is the committed sample under `fixtures/audio/`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from lab.voice.engines import audiofile
from lab.voice.engines.audiofile import (
    AudioFileError,
    read_audio,
    soundfile_available,
    wav_bytes_for,
    write_audio,
)
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    SETUP_SCRIPT,
    EngineUnavailable,
    STTEngine,
    SynthesisResult,
    Transcription,
    TTSEngine,
    audio_digest,
    duration_s,
    pcm16_bytes,
    quantise_pcm16,
    require_available,
    text_digest,
)
from lab.voice.engines.stt import (
    DEFAULT_WHISPER_MODEL,
    REFERENCE_ENGINE,
    LiteLLMSTT,
    MissingTranscriptError,
    RecordedSTT,
    TranscriptCassette,
    WhisperCppSTT,
)
from lab.voice.engines.tts import (
    KOKORO_NATIVE_RATE,
    ClipManifest,
    FixtureTTS,
    KokoroTTS,
    LiteLLMTTS,
    MissingClipError,
    SystemSayTTS,
    resample_rate,
)
from tests.audio_doubles import ToneTTS, UnavailableTTS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "audio"


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "engine",
    [
        KokoroTTS(),
        SystemSayTTS(),
        LiteLLMTTS(),
        pytest.param(FixtureTTS(FIXTURES), id="FixtureTTS"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_every_tts_satisfies_the_protocol(engine: object) -> None:
    assert isinstance(engine, TTSEngine)
    assert isinstance(engine.name, str) and engine.name  # type: ignore[attr-defined]
    assert isinstance(engine.available(), bool)  # type: ignore[attr-defined]
    assert engine.describe()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "engine",
    [WhisperCppSTT(), LiteLLMSTT(), RecordedSTT(TranscriptCassette.from_entries({}))],
    ids=lambda e: type(e).__name__,
)
def test_every_stt_satisfies_the_protocol(engine: object) -> None:
    assert isinstance(engine, STTEngine)
    assert isinstance(engine.name, str) and engine.name  # type: ignore[attr-defined]
    assert isinstance(engine.available(), bool)  # type: ignore[attr-defined]
    assert engine.describe()  # type: ignore[attr-defined]


def test_identities_are_specific_enough_to_pin_a_regression_on() -> None:
    """A regression has to be attributable to a model and a voice, not a vendor."""
    assert KokoroTTS(voice="af_bella").name == "tts:kokoro/af_bella"
    assert SystemSayTTS(voice="Daniel").name == "tts:system-say/Daniel"
    assert LiteLLMTTS(model="openai/tts-1-hd", voice="nova").name == (
        "tts:litellm/openai/tts-1-hd/nova"
    )
    assert WhisperCppSTT(model="/tmp/ggml-small.en.bin").name == "stt:whisper.cpp/ggml-small.en"


def test_identities_are_distinct_across_backends() -> None:
    names = {
        KokoroTTS().name,
        SystemSayTTS().name,
        LiteLLMTTS().name,
        WhisperCppSTT().name,
        LiteLLMSTT().name,
    }
    assert len(names) == 5, f"two backends share an identity: {sorted(names)}"


# --------------------------------------------------------------------------- #
# Availability and the degradation path
# --------------------------------------------------------------------------- #


def test_require_available_names_the_setup_script() -> None:
    """An unavailable engine must hand back the command that fixes it."""
    with pytest.raises(EngineUnavailable) as caught:
        require_available(UnavailableTTS())
    message = str(caught.value)
    assert SETUP_SCRIPT in message
    assert caught.value.engine == "tts:test-absent"
    assert caught.value.remedy


def test_unavailable_engine_raises_at_the_point_of_use() -> None:
    with pytest.raises(EngineUnavailable):
        UnavailableTTS().synthesise("anything")


def test_kokoro_distinguishes_a_missing_package_from_missing_weights() -> None:
    """Two different remedies, so they must be two different states."""
    engine = KokoroTTS()
    assert isinstance(engine.package_available(), bool)
    assert isinstance(engine.weights_present(), bool)
    # available() is the conjunction unless a download has been permitted.
    assert engine.available() == (engine.package_available() and engine.weights_present())
    permissive = KokoroTTS(allow_download=True)
    assert permissive.available() == permissive.package_available()


def test_kokoro_refuses_to_download_weights_by_default() -> None:
    """A 330 MB fetch in the middle of an evaluation is a network test, not a run."""
    engine = KokoroTTS()
    if engine.available():  # pragma: no cover - only on a machine with the model
        pytest.skip("Kokoro is installed here, so there is nothing to refuse")
    with pytest.raises(EngineUnavailable) as caught:
        engine.synthesise("a table for two")
    assert SETUP_SCRIPT in str(caught.value)


def test_whisper_says_which_half_is_missing() -> None:
    engine = WhisperCppSTT(binary="/nonexistent/whisper-cli", model="/nonexistent/model.bin")
    assert not engine.available()
    with pytest.raises(EngineUnavailable) as caught:
        engine.transcribe(np.zeros(16, dtype=float))
    assert "binary" in str(caught.value)
    assert SETUP_SCRIPT in str(caught.value)


def test_whisper_default_model_is_base_not_tiny() -> None:
    """tiny.en hallucinates on degraded audio, which a perturbation study cannot use."""
    assert DEFAULT_WHISPER_MODEL == "ggml-base.en.bin"


@pytest.mark.parametrize("engine", [LiteLLMTTS(), LiteLLMSTT()], ids=lambda e: type(e).__name__)
def test_api_backends_are_opt_in_by_env_var_not_by_api_key(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key exported for other work is not consent to bill a test run."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.delenv(engine.env_var, raising=False)  # type: ignore[attr-defined]
    assert engine.available() is False  # type: ignore[attr-defined]
    with pytest.raises(EngineUnavailable) as caught:
        if hasattr(engine, "synthesise"):
            engine.synthesise("hello")  # type: ignore[attr-defined]
        else:
            engine.transcribe(np.zeros(16, dtype=float))  # type: ignore[attr-defined]
    assert engine.env_var in str(caught.value)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Digests
# --------------------------------------------------------------------------- #


def test_audio_digest_is_a_function_of_the_audio() -> None:
    a = np.linspace(-0.5, 0.5, 400)
    assert audio_digest(a, 16_000) == audio_digest(a.copy(), 16_000)
    assert audio_digest(a, 16_000) != audio_digest(a * 0.5, 16_000)


def test_audio_digest_separates_rates() -> None:
    """The same samples at two rates are two different sounds."""
    a = np.linspace(-0.5, 0.5, 400)
    assert audio_digest(a, 16_000) != audio_digest(a, 8_000)


def test_audio_digest_ignores_float_noise_far_below_the_quantiser() -> None:
    """Stable across platforms: the digest is over int16, not over float64."""
    audio = 0.4 * np.sin(2 * np.pi * 220 * np.arange(2000) / 16_000)
    assert audio_digest(audio, 16_000) == audio_digest(audio + 1e-9, 16_000)


def test_audio_digest_can_flip_on_a_sample_sitting_exactly_on_a_tie() -> None:
    """A known, accepted property — recorded here rather than discovered later.

    Any quantiser has boundaries, and a float sample landing exactly on one can
    round either way under the smallest perturbation. `np.linspace(-0.5, 0.5,
    400)` manufactures such samples on purpose.

    This is tolerable because of what a digest miss *does*: `RecordedSTT` raises
    and names `make audio-fixtures`. The failure mode is a loud refusal with a
    remedy, not a wrong transcript — which is the trade a content-addressed
    cassette is making. The alternative, keying on the line of dialogue, would
    never miss and would therefore answer for audio it had never heard.
    """
    contrived = np.linspace(-0.5, 0.5, 400)
    assert audio_digest(contrived, 16_000) != audio_digest(contrived + 1e-9, 16_000)


def test_audio_digest_changes_when_a_perturbation_changes() -> None:
    """The property the transcript cassette depends on."""
    from lab.voice.perturb import add_noise

    clean = np.sin(2 * np.pi * 220 * np.arange(8000) / 16_000)
    quiet, _ = add_noise(clean, snr_db=20.0, sample_rate=16_000, seed=1)
    loud, _ = add_noise(clean, snr_db=5.0, sample_rate=16_000, seed=1)
    assert audio_digest(quiet, 16_000) != audio_digest(loud, 16_000)


def test_text_digest_ignores_engine_and_voice_but_not_rate() -> None:
    assert text_digest("a table for two", 16_000) == text_digest("a table for two", 16_000)
    assert text_digest("a table for two", 16_000) != text_digest("a table for two", 24_000)
    assert text_digest("a table for two", 16_000) != text_digest("a table for three", 16_000)


def test_pcm16_bytes_and_duration_are_arithmetic_not_estimates() -> None:
    audio = np.zeros(16_000)
    assert pcm16_bytes(audio) == 32_000
    assert duration_s(audio, 16_000) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        duration_s(audio, 0)


def test_quantise_pcm16_clips_rather_than_wrapping() -> None:
    """A sample above 1.0 must saturate; int16 overflow would invert the waveform."""
    quantised = quantise_pcm16(np.array([-4.0, -1.0, 0.0, 1.0, 4.0]))
    assert quantised.tolist() == [-32767, -32767, 0, 32767, 32767]


# --------------------------------------------------------------------------- #
# Clip I/O
# --------------------------------------------------------------------------- #


def test_wav_round_trip_needs_no_dependency(tmp_path: Path) -> None:
    audio = 0.4 * np.sin(2 * np.pi * 220 * np.arange(1600) / 16_000)
    path = write_audio(tmp_path / "clip.wav", audio, 16_000)
    read_back, rate = read_audio(path)
    assert rate == 16_000
    assert read_back.size == audio.size
    # Quantisation to int16 is the only loss; one LSB is 1/32768.
    assert np.max(np.abs(read_back - audio)) < 2 / 32768


def test_write_audio_refuses_an_extension_it_cannot_honour(tmp_path: Path) -> None:
    """A WAV written under an .opus name is a fixture nobody can trust."""
    with pytest.raises(AudioFileError) as caught:
        write_audio(tmp_path / "clip.mp3", np.zeros(16), 16_000)
    assert ".mp3" in str(caught.value)


def test_read_audio_reports_a_missing_file_by_path(tmp_path: Path) -> None:
    with pytest.raises(AudioFileError) as caught:
        read_audio(tmp_path / "absent.wav")
    assert "absent.wav" in str(caught.value)


def test_wav_bytes_for_matches_a_real_wav(tmp_path: Path) -> None:
    """The arithmetic in the module docstring is checked against the encoder."""
    audio = np.zeros(4321)
    path = write_audio(tmp_path / "clip.wav", audio, 16_000)
    assert path.stat().st_size == wav_bytes_for(audio.size)


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_opus_round_trip_survives_a_digest_of_speech(tmp_path: Path) -> None:
    """Opus is lossy, so a re-encode changes the digest; a re-read does not.

    This is exactly why the fixture generator records its cassette from the
    *replayed* audio: the digest that matters is the one the replay path produces,
    and it is stable because replay only ever decodes.
    """
    clip = sorted((FIXTURES / "clips").glob("*.opus"))[0]
    first, rate = read_audio(clip)
    second, rate_again = read_audio(clip)
    assert rate == rate_again == DEFAULT_SAMPLE_RATE
    assert audio_digest(first, rate) == audio_digest(second, rate_again)

    re_encoded = write_audio(tmp_path / "again.opus", first, rate)
    round_two, _ = read_audio(re_encoded)
    assert audio_digest(round_two, rate) != audio_digest(first, rate)


@pytest.mark.skipif(not soundfile_available(), reason="Ogg Opus needs soundfile/libsndfile>=1.1")
def test_committed_clips_are_smaller_than_wav_by_the_documented_factor() -> None:
    """The 'never commit WAV' arithmetic, measured on what is actually committed."""
    manifest = json.loads((FIXTURES / ClipManifest.FILENAME).read_text(encoding="utf-8"))
    clips = list(manifest["clips"].values())
    opus = sum(int(c["bytes"]) for c in clips)
    wav = sum(wav_bytes_for(int(c["samples"])) for c in clips)
    assert opus > 0 and wav > opus
    assert wav / opus > 5.0, f"only {wav / opus:.1f}x; the docstring claims far more"
    # A guard on the committed size, not a preference: fixtures grow silently.
    assert opus < 512 * 1024, f"committed clips are {opus / 1024:.0f} KiB; keep them small"


def test_soundfile_diagnosis_is_a_single_informative_line() -> None:
    line = audiofile.soundfile_diagnosis()
    assert line and "\n" not in line


# --------------------------------------------------------------------------- #
# Resampling
# --------------------------------------------------------------------------- #


def test_resample_rate_preserves_duration() -> None:
    """Rate conversion, not speed change — the distinction perturb.py owns."""
    seconds = 0.5
    source = np.sin(2 * np.pi * 300 * np.arange(int(KOKORO_NATIVE_RATE * seconds)) / KOKORO_NATIVE_RATE)
    out = resample_rate(source, KOKORO_NATIVE_RATE, DEFAULT_SAMPLE_RATE)
    assert out.size == pytest.approx(DEFAULT_SAMPLE_RATE * seconds, abs=1)
    assert duration_s(out, DEFAULT_SAMPLE_RATE) == pytest.approx(seconds, abs=1e-3)


def test_resample_rate_preserves_amplitude() -> None:
    """A downsample that quietly scales the signal corrupts every SNR figure."""
    source = 0.5 * np.sin(2 * np.pi * 300 * np.arange(2400) / 24_000)
    out = resample_rate(source, 24_000, 16_000)
    assert float(np.max(np.abs(out))) == pytest.approx(0.5, abs=0.02)


def test_resample_rate_does_not_alias_a_tone_above_the_new_nyquist() -> None:
    """The point of resampling in the frequency domain rather than decimating.

    A 10 kHz tone cannot exist at 16 kHz. Naive decimation would fold it down to
    6 kHz — audible, and indistinguishable from fricative energy to an STT
    engine. Discarding the bins removes it instead.
    """
    rate = 24_000
    t = np.arange(rate) / rate
    above_nyquist = resample_rate(np.sin(2 * np.pi * 10_000 * t), rate, 16_000)
    survives = resample_rate(np.sin(2 * np.pi * 3_000 * t), rate, 16_000)

    # The 10 kHz tone is gone entirely rather than folded down to 6 kHz.
    assert float(np.max(np.abs(above_nyquist))) < 1e-6
    # And the resampler is not simply zeroing everything: 3 kHz comes through.
    assert float(np.max(np.abs(survives))) == pytest.approx(1.0, abs=0.02)


def test_resample_rate_is_identity_at_the_same_rate() -> None:
    source = np.linspace(-1, 1, 100)
    assert np.array_equal(resample_rate(source, 16_000, 16_000), source)


@pytest.mark.parametrize("bad", [(0, 16_000), (16_000, 0), (-1, 16_000)])
def test_resample_rate_rejects_impossible_rates(bad: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        resample_rate(np.zeros(10), *bad)


# --------------------------------------------------------------------------- #
# The replay engines
# --------------------------------------------------------------------------- #


def test_fixture_tts_reports_the_engine_that_recorded_the_clip() -> None:
    """A replay must never claim a live synthesis it did not perform."""
    engine = FixtureTTS(FIXTURES)
    assert engine.is_replay is True
    assert engine.name.startswith("replay:")
    assert engine.manifest.engine in engine.name


def test_fixture_tts_replays_a_committed_line() -> None:
    engine = FixtureTTS(FIXTURES)
    line = engine.manifest.texts()[0]
    result = engine.synthesise(line, sample_rate=engine.manifest.sample_rate)
    assert isinstance(result, SynthesisResult)
    assert result.replayed is True
    assert result.synthesis_s is None, "a file read is not a synthesis and must not be timed"
    assert result.duration_s > 0


def test_fixture_tts_is_strict_on_an_unknown_line() -> None:
    """Silence or a fallback voice would let a session complete on audio nobody chose."""
    engine = FixtureTTS(FIXTURES)
    with pytest.raises(MissingClipError) as caught:
        engine.synthesise("a line that was never recorded", sample_rate=16_000)
    message = str(caught.value)
    assert "a line that was never recorded" in message
    assert "make audio-fixtures" in message


def test_fixture_tts_refuses_a_rate_it_did_not_record() -> None:
    engine = FixtureTTS(FIXTURES)
    line = engine.manifest.texts()[0]
    with pytest.raises(MissingClipError):
        engine.synthesise(line, sample_rate=8_000)


def test_clip_manifest_reports_a_missing_directory_actionably(tmp_path: Path) -> None:
    with pytest.raises(MissingClipError) as caught:
        ClipManifest.load(tmp_path)
    assert "make audio-fixtures" in str(caught.value)


def test_recorded_stt_carries_provenance_and_the_recording_engine() -> None:
    audio = np.linspace(-0.2, 0.2, 320)
    digest = audio_digest(audio, 16_000)
    cassette = TranscriptCassette.from_entries(
        {digest: {"text": "table for two", "engine": "stt:whisper.cpp/ggml-base.en",
                  "provenance": "recorded", "confidence": 0.9}}
    )
    result = RecordedSTT(cassette).transcribe(audio, sample_rate=16_000)
    assert result.text == "table for two"
    assert result.engine == "replay:stt:whisper.cpp/ggml-base.en"
    assert result.provenance == "recorded"
    assert result.confidence == pytest.approx(0.9)
    assert result.is_measurable is True


def test_recorded_stt_marks_a_reference_transcript_unmeasurable() -> None:
    audio = np.linspace(-0.2, 0.2, 320)
    cassette = TranscriptCassette.from_entries(
        {
            audio_digest(audio, 16_000): {
                "text": "table for two",
                "engine": REFERENCE_ENGINE,
                "provenance": "reference",
            }
        }
    )
    result = RecordedSTT(cassette).transcribe(audio, sample_rate=16_000)
    assert result.provenance == "reference"
    assert result.is_measurable is False
    assert "reference" in result.trace_payload()["provenance"]


def test_recorded_stt_misses_loudly_when_the_audio_changed() -> None:
    """A cassette that answered for audio it never heard would be worse than none."""
    recorded = np.linspace(-0.2, 0.2, 320)
    cassette = TranscriptCassette.from_entries(
        {audio_digest(recorded, 16_000): {"text": "x", "provenance": "recorded"}}
    )
    engine = RecordedSTT(cassette)
    with pytest.raises(MissingTranscriptError) as caught:
        engine.transcribe(recorded * 0.5, sample_rate=16_000)
    assert "make audio-fixtures" in str(caught.value)


def test_recorded_stt_non_strict_placeholder_is_still_refused_downstream() -> None:
    """The lenient mode is loud: its output is provenance 'reference', which is refused."""
    engine = RecordedSTT(TranscriptCassette.from_entries({}), strict=False)
    result = engine.transcribe(np.zeros(320), sample_rate=16_000)
    assert result.provenance == "reference"
    assert result.is_measurable is False
    assert "missing" in result.engine


def test_transcript_cassette_load_is_actionable_when_absent(tmp_path: Path) -> None:
    with pytest.raises(MissingTranscriptError) as caught:
        TranscriptCassette.load(tmp_path)
    assert "make audio-fixtures" in str(caught.value)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


def test_synthesis_result_derives_duration_and_bytes_from_the_samples() -> None:
    result = ToneTTS().synthesise("one two three four", sample_rate=16_000)
    assert result.duration_s == pytest.approx(1.0)
    assert result.num_bytes == 2 * result.audio.size
    assert math.isclose(result.duration_s, result.audio.size / 16_000)


def test_transcription_confidence_is_absent_unless_an_engine_reports_one() -> None:
    """Inventing 1.0 would leave a confidence-gated check gated on nothing."""
    assert Transcription(text="x", engine="e").confidence is None
    with pytest.raises(ValueError):
        Transcription(text="x", engine="e", confidence=1.5)


def test_transcription_trace_payload_always_states_provenance() -> None:
    payload = Transcription(text="x", engine="e", provenance="engine").trace_payload()
    assert payload["provenance"] == "engine"
