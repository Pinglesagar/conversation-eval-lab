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
    clipcache   content-addressed clips: why a re-run of the paid suite costs nothing
    tts         KokoroTTS (default) · SystemSayTTS · LiteLLMTTS · FixtureTTS
    stt         WhisperCppSTT · LiteLLMSTT · RecordedSTT
    elevenlabs_tts  the real synthesiser, and the spoken-form WER reference
    deepgram_stt    the real recogniser, verbatim by default, word-level detail
    coverage    which of the 24 markets can be audio-tested at all, computed

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
        CALLER_INPUT_REFERENCE,
        DEFAULT_SAMPLE_RATE,
        SCORABLE_FORMATTING,
        SETUP_SCRIPT,
        SPOKEN_FORM_REFERENCE,
        Audio,
        EngineUnavailable,
        Formatting,
        Provenance,
        ReferenceSource,
        WordTiming,
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
    from lab.voice.engines.clipcache import (  # noqa: F401
        CACHE_DIR_ENV_VAR,
        COMMITTED_CACHE_DIR,
        SCRATCH_CACHE_DIR,
        CacheEntry,
        ClipCache,
        clip_cache_key,
    )
    from lab.voice.engines.coverage import (  # noqa: F401
        CANTONESE,
        MARKETS,
        SYNTHESISABLE_LANGUAGES,
        YUE_REMEDIATION,
        Market,
        MarketCoverage,
        coverage_for,
        coverage_table,
        untestable_markets,
    )
    from lab.voice.engines.deepgram_stt import (  # noqa: F401
        DEEPGRAM_ENDPOINT,
        DEEPGRAM_KEY_ENV_VAR,
        DEFAULT_DEEPGRAM_MODEL,
        MONOLINGUAL_ONLY_LANGUAGES,
        MULTI_LANGUAGE,
        MULTI_LANGUAGES,
        CodeSwitchingUnsupported,
        DeepgramSTT,
        wav_container,
    )
    from lab.voice.engines.elevenlabs_tts import (  # noqa: F401
        CHARACTER_COST_MULTIPLIERS,
        CREDIT_BUDGET_ENV_VAR,
        DEFAULT_AGENT_VOICE,
        DEFAULT_CALLER_VOICE,
        DEFAULT_ELEVENLABS_MODEL,
        ELEVENLABS_KEY_ENV_VAR,
        ELEVENLABS_PREMADE_VOICES,
        SPOKEN_FORM_MODELS,
        CreditBudgetExceeded,
        ElevenLabsTTS,
        VoiceNotPermitted,
        credits_for,
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
    "CALLER_INPUT_REFERENCE": "lab.voice.engines.base",
    "Formatting": "lab.voice.engines.base",
    "ReferenceSource": "lab.voice.engines.base",
    "SCORABLE_FORMATTING": "lab.voice.engines.base",
    "SPOKEN_FORM_REFERENCE": "lab.voice.engines.base",
    "WordTiming": "lab.voice.engines.base",
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
    # clipcache — the content-addressed clip store
    "CACHE_DIR_ENV_VAR": "lab.voice.engines.clipcache",
    "COMMITTED_CACHE_DIR": "lab.voice.engines.clipcache",
    "CacheEntry": "lab.voice.engines.clipcache",
    "ClipCache": "lab.voice.engines.clipcache",
    "SCRATCH_CACHE_DIR": "lab.voice.engines.clipcache",
    "clip_cache_key": "lab.voice.engines.clipcache",
    # elevenlabs_tts — the real synthesiser
    "CHARACTER_COST_MULTIPLIERS": "lab.voice.engines.elevenlabs_tts",
    "CREDIT_BUDGET_ENV_VAR": "lab.voice.engines.elevenlabs_tts",
    "CreditBudgetExceeded": "lab.voice.engines.elevenlabs_tts",
    "DEFAULT_AGENT_VOICE": "lab.voice.engines.elevenlabs_tts",
    "DEFAULT_CALLER_VOICE": "lab.voice.engines.elevenlabs_tts",
    "DEFAULT_ELEVENLABS_MODEL": "lab.voice.engines.elevenlabs_tts",
    "ELEVENLABS_KEY_ENV_VAR": "lab.voice.engines.elevenlabs_tts",
    "ELEVENLABS_PREMADE_VOICES": "lab.voice.engines.elevenlabs_tts",
    "ElevenLabsTTS": "lab.voice.engines.elevenlabs_tts",
    "SPOKEN_FORM_MODELS": "lab.voice.engines.elevenlabs_tts",
    "VoiceNotPermitted": "lab.voice.engines.elevenlabs_tts",
    "credits_for": "lab.voice.engines.elevenlabs_tts",
    # deepgram_stt — the real recogniser
    "CodeSwitchingUnsupported": "lab.voice.engines.deepgram_stt",
    "DEEPGRAM_ENDPOINT": "lab.voice.engines.deepgram_stt",
    "DEEPGRAM_KEY_ENV_VAR": "lab.voice.engines.deepgram_stt",
    "DEFAULT_DEEPGRAM_MODEL": "lab.voice.engines.deepgram_stt",
    "DeepgramSTT": "lab.voice.engines.deepgram_stt",
    "MONOLINGUAL_ONLY_LANGUAGES": "lab.voice.engines.deepgram_stt",
    "MULTI_LANGUAGE": "lab.voice.engines.deepgram_stt",
    "MULTI_LANGUAGES": "lab.voice.engines.deepgram_stt",
    "wav_container": "lab.voice.engines.deepgram_stt",
    # coverage — the market matrix
    "CANTONESE": "lab.voice.engines.coverage",
    "MARKETS": "lab.voice.engines.coverage",
    "Market": "lab.voice.engines.coverage",
    "MarketCoverage": "lab.voice.engines.coverage",
    "SYNTHESISABLE_LANGUAGES": "lab.voice.engines.coverage",
    "YUE_REMEDIATION": "lab.voice.engines.coverage",
    "coverage_for": "lab.voice.engines.coverage",
    "coverage_table": "lab.voice.engines.coverage",
    "untestable_markets": "lab.voice.engines.coverage",
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
