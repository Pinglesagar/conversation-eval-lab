"""Transcription backends — and the one that admits it is not transcribing.

WHAT THIS DEMONSTRATES
----------------------
Three backends behind one protocol:

    WhisperCppSTT   local, the correct choice on Apple Silicon. See below.
    LiteLLMSTT      an OpenAI-compatible API, opt-in behind an env var.
    RecordedSTT     replays a committed cassette of transcripts, keyed by the
                    *audio content*, and carries the provenance of each entry so
                    that a stand-in can never be quoted as an engine result.

WHY WHISPER.CPP AND NOT FASTER-WHISPER
--------------------------------------
faster-whisper is the usual recommendation and it is the wrong one on a Mac.
It runs on CTranslate2, which has no Metal backend: on Apple Silicon it falls
back to CPU, and the 4-8x speedup that motivated the choice does not exist.
whisper.cpp has first-class Metal and Core ML support and is the fastest local
option on exactly the hardware most people will clone this on. It is also a
single self-contained binary with GGML weights, which makes `available()` a
question about two files rather than about a Python dependency tree.

The cost is that it is a subprocess and not a library, so every transcription
pays a process spawn and a temporary WAV write. That is fine here and would not
be fine in production: this is a batch, post-hoc, file-based pipeline by design
(see `lab.voice.adapter`), and a few tens of milliseconds of spawn overhead sits
in *harness* time, outside every measured window, where it cannot corrupt a
figure.

THE THIRD BACKEND IS THE INTERESTING ONE
----------------------------------------
`RecordedSTT` replays transcripts and reports what each one really is:

    provenance="recorded"    a real engine produced this text from these samples
    provenance="reference"   nothing transcribed anything; this is the ground
                             truth standing in so the pipeline can run

The second mode is how this repository ships. No STT engine is committed and none
is downloaded at test time, so the committed traces carry reference transcripts —
and a word error rate computed against them is exactly zero by construction, for
every clip, on every channel, no matter how badly the audio was degraded. That
number is worse than useless; it is a confident lie about the noisiest scenario
in the corpus.

So the provenance travels with the text into the trace, and
`lab.voice.adapter.audio_wer_report` refuses to compute WER from a trace whose
transcripts are references. Install whisper.cpp with `scripts/setup_audio.sh`,
re-record with `make audio-fixtures`, and the same call starts answering. The
refusal is not a limitation being apologised for — it is the feature. A harness
that prints "WER: 0.0%" for a call recorded at 6 dB SNR has told you something
false about your system, and it did so without a single failing test.

WHAT THIS DOES NOT DO
---------------------
No streaming or partial hypotheses, no diarisation, no word-level timestamps, no
language identification beyond what an engine volunteers, no confidence
calibration. Notably, whisper.cpp's per-segment `avg_logprob` is *not* converted
into a confidence: mapping a log-probability to a [0, 1] confidence needs a
calibration study of its own, and an uncalibrated number that a check might gate
on is exactly the kind of decoration this repo argues against. `confidence` stays
`None` unless an engine reports a real one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from lab.voice.engines.audiofile import write_audio
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    SETUP_SCRIPT,
    Audio,
    EngineUnavailable,
    Provenance,
    Transcription,
    audio_digest,
)

__all__ = [
    "LIVE_STT_ENV_VAR",
    "WHISPER_BIN_ENV_VAR",
    "WHISPER_MODEL_ENV_VAR",
    "DEFAULT_WHISPER_HOME",
    "DEFAULT_WHISPER_MODEL",
    "REFERENCE_ENGINE",
    "WhisperCppSTT",
    "LiteLLMSTT",
    "RecordedSTT",
    "TranscriptCassette",
    "MissingTranscriptError",
]

#: Set this to a truthy value to allow `LiteLLMSTT` to reach a live provider.
LIVE_STT_ENV_VAR: str = "LAB_LIVE_STT"

#: Where `scripts/setup_audio.sh` puts the binary and the weights, overridable.
WHISPER_BIN_ENV_VAR: str = "LAB_WHISPER_CPP_BIN"
WHISPER_MODEL_ENV_VAR: str = "LAB_WHISPER_CPP_MODEL"

#: Default install root. Under the user's cache directory rather than the repo,
#: so that a 150 MB model is shared between checkouts and can never be committed
#: by an over-broad `git add`.
DEFAULT_WHISPER_HOME: Path = Path.home() / ".cache" / "lab-audio" / "whisper.cpp"

#: `base.en` is the default because it is the smallest model whose errors are
#: still *speech* errors rather than nonsense: `tiny.en` hallucinates on degraded
#: audio, which would make a perturbation study measure the model's imagination.
#: 148 MB, and fast enough on Metal that it is not the bottleneck.
DEFAULT_WHISPER_MODEL: str = "ggml-base.en.bin"

#: The identity recorded for a transcript that no engine produced. Deliberately
#: not shaped like an engine name — nothing should be able to read this and think
#: a model was involved.
REFERENCE_ENGINE: str = "reference-text"


# --------------------------------------------------------------------------- #
# whisper.cpp
# --------------------------------------------------------------------------- #


class WhisperCppSTT:
    """Local transcription via the whisper.cpp CLI. Metal-accelerated on a Mac.

    Availability is a question about two paths — the binary and the weights — and
    the error says which one is missing, because "STT unavailable" sends the
    reader to the wrong half of the setup script half the time.
    """

    def __init__(
        self,
        *,
        binary: str | Path | None = None,
        model: str | Path | None = None,
        language: str = "en",
        threads: int | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        """
        Args:
            binary: Path to `whisper-cli`. Defaults to `$LAB_WHISPER_CPP_BIN`,
                then the install root, then whatever is on `$PATH`.
            model: Path to a GGML model. Defaults to `$LAB_WHISPER_CPP_MODEL`,
                then `base.en` under the install root.
            language: Forced language. Forced rather than auto-detected: on a
                degraded clip whisper's language detection can pick another
                language and then "transcribe" fluent nonsense, which is a
                spectacular way to corrupt a word error rate.
            threads: `-t`, defaults to whisper.cpp's own choice.
            extra_args: Passed through verbatim, for a flag this wrapper has no
                opinion about.
        """
        self.binary = Path(binary) if binary else self._default_binary()
        self.model = Path(model) if model else self._default_model()
        self.language = language
        self.threads = threads
        self.extra_args = tuple(extra_args)
        self.name = f"stt:whisper.cpp/{self.model.stem}"
        self.is_replay = False

    # ------------------------------------------------------------- discovery

    @staticmethod
    def _default_binary() -> Path:
        override = os.environ.get(WHISPER_BIN_ENV_VAR)
        if override:
            return Path(override)
        installed = DEFAULT_WHISPER_HOME / "build" / "bin" / "whisper-cli"
        if installed.is_file():
            return installed
        found = shutil.which("whisper-cli")
        return Path(found) if found else installed

    @staticmethod
    def _default_model() -> Path:
        override = os.environ.get(WHISPER_MODEL_ENV_VAR)
        if override:
            return Path(override)
        return DEFAULT_WHISPER_HOME / "models" / DEFAULT_WHISPER_MODEL

    def binary_present(self) -> bool:
        return self.binary.is_file()

    def model_present(self) -> bool:
        return self.model.is_file()

    def available(self) -> bool:
        return self.binary_present() and self.model_present()

    def describe(self) -> str:
        return (
            f"{self.name} (whisper.cpp CLI at {self.binary}, model {self.model}, "
            f"language={self.language}; Metal/Core ML on Apple Silicon)"
        )

    # ---------------------------------------------------------- transcription

    def _require(self) -> None:
        if not self.binary_present():
            raise EngineUnavailable(
                self.name,
                f"no whisper.cpp binary at {self.binary}",
                f"run {SETUP_SCRIPT} (it clones and builds whisper.cpp with Metal), "
                f"or set {WHISPER_BIN_ENV_VAR} to an existing build",
            )
        if not self.model_present():
            raise EngineUnavailable(
                self.name,
                f"no GGML model at {self.model}",
                f"run {SETUP_SCRIPT} to fetch {DEFAULT_WHISPER_MODEL}, "
                f"or set {WHISPER_MODEL_ENV_VAR} to a model you already have",
            )

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        self._require()
        with tempfile.TemporaryDirectory(prefix="lab-whisper-") as directory:
            wav = Path(directory) / "clip.wav"
            # whisper.cpp reads 16-bit PCM WAV and nothing else. Temporary, and
            # deleted with the directory — the "never commit WAV" rule is about
            # what goes into git, not about what a subprocess needs on the way in.
            write_audio(wav, audio, sample_rate)
            command: list[str] = [
                str(self.binary),
                "-m",
                str(self.model),
                "-f",
                str(wav),
                "-l",
                self.language,
                "--output-json",
                "--no-prints",
                "-of",
                str(Path(directory) / "out"),
            ]
            if self.threads is not None:
                command += ["-t", str(self.threads)]
            command += list(self.extra_args)
            started = time.perf_counter()
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            elapsed = time.perf_counter() - started
            if completed.returncode != 0:
                raise EngineUnavailable(
                    self.name,
                    f"whisper.cpp exited {completed.returncode}: "
                    f"{(completed.stderr or completed.stdout or '').strip()[:400]}",
                    "check the model file is intact and the binary runs standalone",
                )
            payload = Path(directory) / "out.json"
            text = (
                self._text_from_json(json.loads(payload.read_text(encoding="utf-8")))
                if payload.is_file()
                else completed.stdout
            )
        return Transcription(
            text=" ".join(text.split()),
            engine=self.name,
            provenance="engine",
            language=self.language,
            transcribe_s=elapsed,
        )

    @staticmethod
    def _text_from_json(document: Mapping[str, Any]) -> str:
        segments = document.get("transcription") or []
        return " ".join(str(segment.get("text", "")).strip() for segment in segments)

    def __repr__(self) -> str:
        return (
            f"WhisperCppSTT(binary={str(self.binary)!r}, model={self.model.name!r}, "
            f"available={self.available()})"
        )


# --------------------------------------------------------------------------- #
# An OpenAI-compatible API — opt-in
# --------------------------------------------------------------------------- #


class LiteLLMSTT:
    """Any OpenAI-compatible transcription endpoint, through litellm. Opt-in only.

    Gated on `$LAB_LIVE_STT` and not on the presence of a key, for the same
    reason as the TTS side: a key exported for other work is not consent to bill
    a test run.
    """

    def __init__(
        self,
        *,
        model: str = "openai/whisper-1",
        language: str | None = "en",
        env_var: str = LIVE_STT_ENV_VAR,
        timeout_s: float = 120.0,
    ) -> None:
        self.model = model
        self.language = language
        self.env_var = env_var
        self.timeout_s = timeout_s
        self.name = f"stt:litellm/{model}"
        self.is_replay = False

    @property
    def live_enabled(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def available(self) -> bool:
        return self.live_enabled

    def describe(self) -> str:
        state = "enabled" if self.live_enabled else f"disabled (set {self.env_var}=1)"
        return f"{self.name} (litellm, network, {state})"

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        if not self.live_enabled:
            raise EngineUnavailable(
                self.name,
                f"live transcription is not enabled; {self.env_var} is unset",
                f"export {self.env_var}=1 to permit a billed API call, or use "
                "WhisperCppSTT locally, or replay a cassette with RecordedSTT",
            )
        import litellm  # noqa: PLC0415 - heavy, and only needed on the live path

        with tempfile.TemporaryDirectory(prefix="lab-stt-") as directory:
            wav = Path(directory) / "clip.wav"
            write_audio(wav, audio, sample_rate)
            started = time.perf_counter()
            with wav.open("rb") as handle:
                response = litellm.transcription(
                    model=self.model,
                    file=handle,
                    language=self.language,
                    timeout=self.timeout_s,
                )
            elapsed = time.perf_counter() - started
        text = getattr(response, "text", None)
        if text is None:  # pragma: no cover - provider shape drift
            text = str(response)
        return Transcription(
            text=" ".join(str(text).split()),
            engine=self.name,
            provenance="engine",
            language=self.language,
            transcribe_s=elapsed,
        )

    def __repr__(self) -> str:
        return f"LiteLLMSTT(model={self.model!r}, live={self.live_enabled})"


# --------------------------------------------------------------------------- #
# Replay — and the honesty machinery
# --------------------------------------------------------------------------- #


class MissingTranscriptError(EngineUnavailable):
    """The cassette has no entry for these samples.

    Raised rather than falling back to anything, because every available fallback
    is a lie: returning the empty string invents a total-loss turn, returning the
    reference invents a perfect one, and synthesising a plausible mishearing
    invents data. A miss means the audio changed — a new perturbation seed, a new
    voice, a re-recorded clip — and the cassette needs re-recording against it.
    """


class TranscriptCassette:
    """Transcripts recorded once, keyed by the audio they were produced from.

    The key is `lab.voice.engines.base.audio_digest`, a digest of the 16-bit PCM
    of the *perturbed* clip. That is a stricter key than the line of dialogue, and
    the strictness is the whole value: change the SNR, the seed, the voice or the
    perturbation order and the key changes, so the cassette misses instead of
    replaying a transcript that was recorded from different sound. Keying on the
    text would have made every one of those changes silently invisible.
    """

    FILENAME = "transcripts.json"

    def __init__(self, *, path: Path | None, data: Mapping[str, Any]) -> None:
        self.path = path
        self.data = dict(data)
        self.entries: dict[str, dict[str, Any]] = dict(self.data.get("entries", {}))

    @classmethod
    def load(cls, path: str | Path) -> "TranscriptCassette":
        source = Path(path)
        if source.is_dir():
            source = source / cls.FILENAME
        if not source.is_file():
            raise MissingTranscriptError(
                "stt:recorded",
                f"no transcript cassette at {source}",
                "run `make audio-fixtures` to record one",
            )
        return cls(path=source, data=json.loads(source.read_text(encoding="utf-8")))

    @classmethod
    def from_entries(cls, entries: Mapping[str, Mapping[str, Any]]) -> "TranscriptCassette":
        """Build in memory. Used by tests, and by the generator before it writes."""
        return cls(path=None, data={"entries": {k: dict(v) for k, v in entries.items()}})

    def get(self, digest: str) -> dict[str, Any] | None:
        return self.entries.get(digest)

    def provenances(self) -> set[str]:
        """Every provenance value present. A mixed cassette is legal and visible."""
        return {str(entry.get("provenance", "recorded")) for entry in self.entries.values()}

    def __len__(self) -> int:
        return len(self.entries)


class RecordedSTT:
    """Replays a `TranscriptCassette`, carrying each entry's provenance forward.

    Reports its identity as `replay:<the engine that recorded the entry>`, per
    entry, so a trace made from a mixed cassette says which turns came from a
    real engine and which are reference stand-ins. There is no aggregate identity
    that could paper over the difference.
    """

    def __init__(self, cassette: TranscriptCassette, *, strict: bool = True) -> None:
        """
        Args:
            cassette: The recorded transcripts.
            strict: Miss handling. True (the default) raises. False is provided
                only for exploring a partially recorded cassette by hand: it
                returns an empty transcript with `provenance="reference"` and a
                `missing` marker, which is a *loud* placeholder rather than a
                quiet one, and which the WER refusal will reject.
        """
        self.cassette = cassette
        self.strict = strict
        self.name = "replay:stt-cassette"
        self.is_replay = True

    def available(self) -> bool:
        return len(self.cassette) > 0

    def describe(self) -> str:
        kinds = ", ".join(sorted(self.cassette.provenances())) or "empty"
        where = str(self.cassette.path) if self.cassette.path else "in-memory"
        return f"{self.name} ({len(self.cassette)} entry/entries from {where}; provenance: {kinds})"

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        digest = audio_digest(audio, sample_rate)
        entry = self.cassette.get(digest)
        if entry is None:
            if self.strict:
                raise MissingTranscriptError(
                    self.name,
                    f"no transcript for audio {digest[:16]}... at {sample_rate} Hz "
                    f"({len(self.cassette)} entry/entries in the cassette). The audio "
                    "reaching the engine is not the audio the cassette was recorded "
                    "from — a perturbation, seed or voice has changed",
                    "run `make audio-fixtures` to re-record against the current audio",
                )
            return Transcription(
                text="",
                engine=f"{self.name}(missing)",
                provenance="reference",
                language=None,
            )
        provenance: Provenance = str(entry.get("provenance", "recorded"))  # type: ignore[assignment]
        recorded_engine = str(entry.get("engine", REFERENCE_ENGINE))
        confidence = entry.get("confidence")
        return Transcription(
            text=str(entry.get("text", "")),
            engine=f"replay:{recorded_engine}",
            provenance=provenance,
            confidence=float(confidence) if confidence is not None else None,
            language=entry.get("language"),
        )

    def __repr__(self) -> str:
        return f"RecordedSTT(entries={len(self.cassette)}, strict={self.strict})"
