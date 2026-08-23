"""Speech engines: one protocol per role, several backends, identity everywhere.

WHAT THIS DEMONSTRATES
----------------------
That "the agent got slower" and "the agent got worse" are not measurements until
you can say which stage of the pipeline moved. A voice turn passes through three
independent vendors — synthesis, transcription, and the model in between — and an
aggregate that cannot be attributed to one of them cannot be acted on. So every
backend here reports a specific identity string, that string lands in the
`engine` field of every trace event it produced, and `lab.voice.metrics`
(`latencies_by_engine`) can slice any distribution by it.

    base        the two protocols, the two result types, the provenance vocabulary
    audiofile   clip I/O, plus the arithmetic behind committing Opus and not WAV
    tts         KokoroTTS (default) · SystemSayTTS · LiteLLMTTS · FixtureTTS
    stt         WhisperCppSTT · LiteLLMSTT · RecordedSTT

THE DEFAULTS ARE LOCAL AND PERMISSIVE
-------------------------------------
Kokoro-82M for synthesis (Apache-2.0; Piper's maintained fork is GPL-3.0, which
is the wrong obligation to attach to a test fixture in an MIT repo) and
whisper.cpp for transcription (the only local option with a real Metal backend —
faster-whisper's CTranslate2 has none and quietly falls back to CPU on Apple
Silicon). Both are one-time downloads via `scripts/setup_audio.sh`. Neither is
required to run the test suite: `FixtureTTS` and `RecordedSTT` replay committed
fixtures, which is what keeps `pip install -e ".[dev]" && pytest` green offline
with no models and no keys.

THE ONE THING TO READ IF YOU READ NOTHING ELSE
----------------------------------------------
`Transcription.provenance`. It distinguishes a transcript a real engine produced
from one that is the known ground truth standing in for an engine nobody has
installed. Word error rate against the latter is zero by construction, so the
value travels into the trace and `lab.voice.adapter.audio_wer_report` refuses to
report WER when it says `reference`. See `stt` for the argument.

Re-exports below are lazy (PEP 562), matching `lab.voice`: importing the
submodules eagerly would pull numpy — and, worse, put them in `sys.modules`
before `python -m` could run one as a script.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - for type checkers only, never at runtime
    from lab.voice.engines.audiofile import (  # noqa: F401
        AudioFileError,
        read_audio,
        soundfile_available,
        soundfile_diagnosis,
        wav_bytes_for,
        write_audio,
    )
    from lab.voice.engines.base import (  # noqa: F401
        DEFAULT_SAMPLE_RATE,
        SETUP_SCRIPT,
        Audio,
        EngineUnavailable,
        Provenance,
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
    from lab.voice.engines.stt import (  # noqa: F401
        DEFAULT_WHISPER_MODEL,
        LIVE_STT_ENV_VAR,
        REFERENCE_ENGINE,
        LiteLLMSTT,
        MissingTranscriptError,
        RecordedSTT,
        TranscriptCassette,
        WhisperCppSTT,
    )
    from lab.voice.engines.tts import (  # noqa: F401
        DEFAULT_KOKORO_VOICE,
        DEFAULT_SAY_VOICE,
        LIVE_TTS_ENV_VAR,
        ClipManifest,
        FixtureTTS,
        KokoroTTS,
        LiteLLMTTS,
        MissingClipError,
        SystemSayTTS,
        resample_rate,
    )

_LAZY: dict[str, str] = {
    # base — protocols, results, provenance
    "Audio": "lab.voice.engines.base",
    "DEFAULT_SAMPLE_RATE": "lab.voice.engines.base",
    "SETUP_SCRIPT": "lab.voice.engines.base",
    "EngineUnavailable": "lab.voice.engines.base",
    "Provenance": "lab.voice.engines.base",
    "STTEngine": "lab.voice.engines.base",
    "SynthesisResult": "lab.voice.engines.base",
    "TTSEngine": "lab.voice.engines.base",
    "Transcription": "lab.voice.engines.base",
    "audio_digest": "lab.voice.engines.base",
    "duration_s": "lab.voice.engines.base",
    "pcm16_bytes": "lab.voice.engines.base",
    "quantise_pcm16": "lab.voice.engines.base",
    "require_available": "lab.voice.engines.base",
    "text_digest": "lab.voice.engines.base",
    # audiofile — clip I/O
    "AudioFileError": "lab.voice.engines.audiofile",
    "read_audio": "lab.voice.engines.audiofile",
    "soundfile_available": "lab.voice.engines.audiofile",
    "soundfile_diagnosis": "lab.voice.engines.audiofile",
    "wav_bytes_for": "lab.voice.engines.audiofile",
    "write_audio": "lab.voice.engines.audiofile",
    # tts
    "ClipManifest": "lab.voice.engines.tts",
    "DEFAULT_KOKORO_VOICE": "lab.voice.engines.tts",
    "DEFAULT_SAY_VOICE": "lab.voice.engines.tts",
    "FixtureTTS": "lab.voice.engines.tts",
    "KokoroTTS": "lab.voice.engines.tts",
    "LIVE_TTS_ENV_VAR": "lab.voice.engines.tts",
    "LiteLLMTTS": "lab.voice.engines.tts",
    "MissingClipError": "lab.voice.engines.tts",
    "SystemSayTTS": "lab.voice.engines.tts",
    "resample_rate": "lab.voice.engines.tts",
    # stt
    "DEFAULT_WHISPER_MODEL": "lab.voice.engines.stt",
    "LIVE_STT_ENV_VAR": "lab.voice.engines.stt",
    "LiteLLMSTT": "lab.voice.engines.stt",
    "MissingTranscriptError": "lab.voice.engines.stt",
    "REFERENCE_ENGINE": "lab.voice.engines.stt",
    "RecordedSTT": "lab.voice.engines.stt",
    "TranscriptCassette": "lab.voice.engines.stt",
    "WhisperCppSTT": "lab.voice.engines.stt",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name on first access."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
