"""Speech synthesis backends: local-first, permissively licensed, replayable.

WHAT THIS DEMONSTRATES
----------------------
Four backends behind one protocol, chosen so that the same evaluation can run in
three quite different situations without any of them being the special case:

    KokoroTTS      the default. Local, offline, Apache-2.0, 82M parameters.
    SystemSayTTS   real local speech on macOS with nothing to download at all.
    LiteLLMTTS     an OpenAI-compatible API, opt-in behind an env var.
    FixtureTTS     pre-synthesised clips read from disk — how a clean clone with
                   no models installed still runs the whole audio path.

WHY KOKORO IS THE DEFAULT, AND WHY NOT PIPER
--------------------------------------------
Piper was the obvious local choice and is no longer available on acceptable
terms: the maintained implementation moved to OHF-Voice/piper1-gpl and is
GPL-3.0. GPL is a perfectly good licence and the wrong one to make a default
dependency of an MIT-licensed portfolio repo — a reviewer cloning this should not
inherit a copyleft obligation from a test fixture. Kokoro-82M
(hexgrad/Kokoro-82M) is Apache-2.0, runs on CPU, is about 330 MB on disk, and is
good enough that the synthesised caller is not the limiting factor in any
measurement here. So the default is permissive and local, and the API backend is
opt-in rather than assumed.

`SystemSayTTS` exists because it removes the last excuse. It is real speech from
a real synthesiser with a zero-byte download on any Mac, which is what makes it
the engine that generated the committed sample clips: the repo can show a real
audio path end to end without asking a reviewer to fetch 330 MB first.

RESAMPLING IS DONE PROPERLY, AND SAID SO
----------------------------------------
Kokoro synthesises at 24 kHz and the pipeline works at 16 kHz. Naive decimation
would fold everything above 8 kHz back into the speech band as aliasing, which
would then be measured as STT error and blamed on the agent. `resample_rate`
therefore resamples in the frequency domain (truncate the rfft, inverse it),
which is exact for a band-limited signal and inherently anti-aliased on the way
down. It is duration-preserving — unlike `lab.voice.perturb.resample_speed`,
which deliberately changes speed and says so.

WHAT THIS DOES NOT DO
---------------------
No SSML, no prosody control, no per-word timing, no phoneme alignment. Forced
alignment would let the harness locate a specific word in the audio and measure a
barge-in against it — a *discovered* one rather than the constructed measurement
in `lab.voice.interaction`. That is a v2 concern that needs duplex audio to be
worth anything (see `lab.trace.schema` on the reserved interruption events).
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from lab.voice.engines.audiofile import read_audio
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    SETUP_SCRIPT,
    Audio,
    EngineUnavailable,
    SynthesisResult,
    require_available,
    text_digest,
)

__all__ = [
    "LIVE_TTS_ENV_VAR",
    "KOKORO_VOICE_ENV_VAR",
    "DEFAULT_KOKORO_VOICE",
    "DEFAULT_SAY_VOICE",
    "KokoroTTS",
    "SystemSayTTS",
    "LiteLLMTTS",
    "FixtureTTS",
    "ClipManifest",
    "MissingClipError",
    "resample_rate",
]

#: Set this to a truthy value to allow `LiteLLMTTS` to reach a live provider.
#: Absent, it refuses rather than spending money on a clean clone.
LIVE_TTS_ENV_VAR: str = "LAB_LIVE_TTS"

#: Overrides the Kokoro voice without touching code, for a quick A/B.
KOKORO_VOICE_ENV_VAR: str = "LAB_KOKORO_VOICE"

#: Kokoro's American-English female voice. Named explicitly rather than left to
#: the library's default, because "the default voice" is not a reproducible
#: description of a fixture.
DEFAULT_KOKORO_VOICE: str = "af_heart"

#: A macOS system voice that exists on a stock install.
DEFAULT_SAY_VOICE: str = "Samantha"

#: Kokoro's native output rate. Resampled to the pipeline rate on the way out.
KOKORO_NATIVE_RATE: int = 24_000


def resample_rate(audio: Audio, source_rate: int, target_rate: int) -> Audio:
    """Change the sample rate while preserving duration and pitch.

    Frequency-domain resampling: take the real FFT, keep (or zero-pad to) the
    number of bins the target length needs, invert. For a band-limited signal
    this is exact, and on the way *down* it is inherently anti-aliased, because
    the bins above the new Nyquist frequency are discarded rather than folded
    back into the audible band.

    That distinction matters more than it sounds. Dropping every third sample of
    24 kHz audio to reach 16 kHz mirrors everything between 8 and 12 kHz down
    into the speech band, where it lands on exactly the fricative energy an STT
    engine uses to tell "s" from "f". The resulting transcription errors are
    indistinguishable from engine errors, so the harness would be measuring its
    own resampler and reporting it as agent quality.

    Contrast `lab.voice.perturb.resample_speed`, which resamples in order to
    *change* the speed and leaves the declared sample rate alone. Same maths,
    opposite intent; both are explicit about which one they are.
    """
    array = np.asarray(audio, dtype=np.float64)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError(
            f"sample rates must be positive, got source={source_rate!r} target={target_rate!r}"
        )
    if source_rate == target_rate or array.size == 0:
        return array
    target_length = int(round(array.size * (target_rate / source_rate)))
    if target_length < 1:
        raise ValueError(
            f"resampling {array.size} samples from {source_rate} Hz to {target_rate} Hz "
            "would leave nothing; the clip is shorter than one output sample"
        )
    spectrum = np.fft.rfft(array)
    keep = min(spectrum.size, target_length // 2 + 1)
    resized = np.zeros(target_length // 2 + 1, dtype=complex)
    resized[:keep] = spectrum[:keep]
    resampled = np.fft.irfft(resized, n=target_length)
    # irfft normalises by the *output* length, so the amplitude scales with the
    # length ratio. Undo it, or a downsampled clip arrives two-thirds as loud and
    # every SNR figure computed from it is wrong.
    return resampled * (target_length / array.size)


def _decode_wav_bytes(payload: bytes) -> tuple[Audio, int]:
    """Decode 16-bit PCM WAV bytes in memory. Used by the API backend."""
    with wave.open(io.BytesIO(payload), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise EngineUnavailable(
            "tts:litellm", f"provider returned {width * 8}-bit WAV; only 16-bit is decoded here"
        )
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, int(rate)


# --------------------------------------------------------------------------- #
# Kokoro — the default
# --------------------------------------------------------------------------- #


class KokoroTTS:
    """Kokoro-82M, local and Apache-2.0. The default caller voice.

    Availability is checked in two parts, and the distinction is worth keeping:
    the `kokoro` package being importable, and the weights being on disk. A
    machine with the package and no weights would otherwise start a 330 MB
    download in the middle of an evaluation run, which turns a test into a
    network dependency and a timing measurement into nonsense. So an absent model
    is an `EngineUnavailable` naming the setup script, never an implicit fetch.
    """

    def __init__(
        self,
        *,
        voice: str | None = None,
        lang_code: str = "a",
        model_id: str = "hexgrad/Kokoro-82M",
        speed: float = 1.0,
        allow_download: bool = False,
    ) -> None:
        """
        Args:
            voice: Kokoro voice id; defaults to `$LAB_KOKORO_VOICE` or `af_heart`.
            lang_code: Kokoro's pipeline language code ("a" is American English).
            model_id: Hugging Face repo id, recorded in the identity string.
            speed: Kokoro's own speaking-rate multiplier. Left at 1.0 by default —
                speed perturbation belongs to `lab.voice.perturb`, where it is
                declared per scenario and recorded in a descriptor, rather than
                baked invisibly into the voice.
            allow_download: Explicitly permit fetching weights on first use. Off
                by default; see the class docstring.
        """
        self.voice = voice or os.environ.get(KOKORO_VOICE_ENV_VAR) or DEFAULT_KOKORO_VOICE
        self.lang_code = lang_code
        self.model_id = model_id
        self.speed = speed
        self.allow_download = allow_download
        self.name = f"tts:kokoro/{self.voice}"
        self.is_replay = False
        self._pipeline: Any = None

    # ----------------------------------------------------------- availability

    def package_available(self) -> bool:
        """True when the `kokoro` package can be imported."""
        from importlib.util import find_spec  # noqa: PLC0415

        try:
            return find_spec("kokoro") is not None
        except (ImportError, ValueError):  # pragma: no cover - broken install
            return False

    def weights_present(self) -> bool:
        """True when the weights look present in a local Hugging Face cache.

        A heuristic on a cache layout, deliberately: the alternative is to
        instantiate the pipeline, which is the thing that would trigger the
        download this check exists to avoid.
        """
        home = os.environ.get("HF_HOME") or os.path.join(Path.home(), ".cache", "huggingface")
        slug = "models--" + self.model_id.replace("/", "--")
        return (Path(home) / "hub" / slug).is_dir()

    def available(self) -> bool:
        if not self.package_available():
            return False
        return self.allow_download or self.weights_present()

    def describe(self) -> str:
        return (
            f"{self.name} (Apache-2.0, local; model={self.model_id}, "
            f"lang={self.lang_code}, native {KOKORO_NATIVE_RATE} Hz, speed={self.speed})"
        )

    # -------------------------------------------------------------- synthesis

    def _ensure_pipeline(self) -> Any:
        if self._pipeline is None:
            if not self.package_available():
                raise EngineUnavailable(
                    self.name,
                    "the 'kokoro' package is not installed",
                    f"run {SETUP_SCRIPT} (it pip-installs kokoro and fetches the weights)",
                )
            if not (self.allow_download or self.weights_present()):
                raise EngineUnavailable(
                    self.name,
                    f"no local weights for {self.model_id} and downloads are not permitted",
                    "run scripts/setup_audio.sh, or pass allow_download=True to accept "
                    "a ~330 MB fetch mid-run",
                )
            from kokoro import KPipeline  # noqa: PLC0415 - optional heavy dependency

            self._pipeline = KPipeline(lang_code=self.lang_code)
        return self._pipeline

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        require_available(self)
        pipeline = self._ensure_pipeline()
        started = time.perf_counter()
        chunks: list[Audio] = []
        for _graphemes, _phonemes, audio in pipeline(
            text, voice=voice or self.voice, speed=self.speed
        ):
            chunks.append(np.asarray(audio, dtype=np.float64).reshape(-1))
        elapsed = time.perf_counter() - started
        if not chunks:
            raise EngineUnavailable(self.name, f"produced no audio for {text!r}")
        joined = np.concatenate(chunks)
        return SynthesisResult(
            audio=resample_rate(joined, KOKORO_NATIVE_RATE, sample_rate),
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice or self.voice,
            synthesis_s=elapsed,
            text=text,
        )

    def __repr__(self) -> str:
        return f"KokoroTTS(voice={self.voice!r}, available={self.available()})"


# --------------------------------------------------------------------------- #
# macOS `say` — real speech with nothing to download
# --------------------------------------------------------------------------- #


class SystemSayTTS:
    """macOS `say(1)`: a real local synthesiser already installed on every Mac.

    Present because the committed sample clips had to come from somewhere, and
    "download 330 MB before this repo can show you an audio path" is a bad first
    impression. It synthesises straight to 16 kHz 16-bit WAV, so no resampling is
    involved and the clip that reaches the perturbation chain is exactly what the
    synthesiser produced.

    Not the default: it exists on one operating system, and a default that is
    silently unavailable on Linux CI would be a worse choice than one that is
    explicitly a download.
    """

    def __init__(self, *, voice: str = DEFAULT_SAY_VOICE, binary: str = "/usr/bin/say") -> None:
        self.voice = voice
        self.binary = binary
        self.name = f"tts:system-say/{voice}"
        self.is_replay = False

    def available(self) -> bool:
        if platform.system() != "Darwin":
            return False
        return shutil.which(self.binary) is not None or Path(self.binary).is_file()

    def describe(self) -> str:
        return f"{self.name} (macOS AVSpeechSynthesis via {self.binary}; no download)"

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        if not self.available():
            raise EngineUnavailable(
                self.name,
                f"`say` is only available on macOS (this is {platform.system()})",
                "use KokoroTTS (scripts/setup_audio.sh) on other platforms",
            )
        chosen = voice or self.voice
        with tempfile.TemporaryDirectory(prefix="lab-say-") as directory:
            destination = Path(directory) / "clip.wav"
            command = [
                self.binary,
                "-v",
                chosen,
                "--file-format=WAVE",
                f"--data-format=LEI16@{int(sample_rate)}",
                "-o",
                str(destination),
                text,
            ]
            started = time.perf_counter()
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            elapsed = time.perf_counter() - started
            if completed.returncode != 0 or not destination.is_file():
                raise EngineUnavailable(
                    self.name,
                    f"`say` exited {completed.returncode}: "
                    f"{(completed.stderr or completed.stdout or '').strip() or 'no output'}",
                    f"check that the voice {chosen!r} is installed (`say -v '?'`)",
                )
            audio, rate = read_audio(destination)
        return SynthesisResult(
            audio=audio if rate == sample_rate else resample_rate(audio, rate, sample_rate),
            sample_rate=sample_rate,
            engine=self.name,
            voice=chosen,
            synthesis_s=elapsed,
            text=text,
        )

    def __repr__(self) -> str:
        return f"SystemSayTTS(voice={self.voice!r}, available={self.available()})"


# --------------------------------------------------------------------------- #
# An OpenAI-compatible API — opt-in
# --------------------------------------------------------------------------- #


class LiteLLMTTS:
    """Any OpenAI-compatible speech endpoint, through litellm. Opt-in only.

    Gated on `$LAB_LIVE_TTS` rather than on the presence of an API key. The
    difference matters: a developer with a key exported for unrelated work should
    not discover that the test suite has been billing them. Opting in is one
    environment variable and it is never set by anything in this repo.

    WAV is requested rather than the provider default (usually MP3) because
    decoding WAV needs no dependency and no codec guess, and because a lossy
    round trip has no place between a fixture and a word error rate.
    """

    def __init__(
        self,
        *,
        model: str = "openai/tts-1",
        voice: str = "alloy",
        env_var: str = LIVE_TTS_ENV_VAR,
        timeout_s: float = 60.0,
    ) -> None:
        self.model = model
        self.voice = voice
        self.env_var = env_var
        self.timeout_s = timeout_s
        self.name = f"tts:litellm/{model}/{voice}"
        self.is_replay = False

    @property
    def live_enabled(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def available(self) -> bool:
        return self.live_enabled

    def describe(self) -> str:
        state = "enabled" if self.live_enabled else f"disabled (set {self.env_var}=1)"
        return f"{self.name} (litellm, network, {state})"

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        if not self.live_enabled:
            raise EngineUnavailable(
                self.name,
                f"live synthesis is not enabled; {self.env_var} is unset",
                f"export {self.env_var}=1 to permit a billed API call, or use a "
                "local engine (KokoroTTS) or the committed fixtures (FixtureTTS)",
            )
        import litellm  # noqa: PLC0415 - heavy, and only needed on the live path

        started = time.perf_counter()
        response = litellm.speech(
            model=self.model,
            voice=voice or self.voice,
            input=text,
            response_format="wav",
            timeout=self.timeout_s,
        )
        payload = getattr(response, "content", None)
        if payload is None:  # pragma: no cover - provider shape drift
            payload = bytes(response.read())  # type: ignore[attr-defined]
        elapsed = time.perf_counter() - started
        audio, rate = _decode_wav_bytes(payload)
        return SynthesisResult(
            audio=audio if rate == sample_rate else resample_rate(audio, rate, sample_rate),
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice or self.voice,
            synthesis_s=elapsed,
            text=text,
        )

    def __repr__(self) -> str:
        return f"LiteLLMTTS(model={self.model!r}, live={self.live_enabled})"


# --------------------------------------------------------------------------- #
# Pre-synthesised clips on disk — the replay engine
# --------------------------------------------------------------------------- #


class MissingClipError(EngineUnavailable):
    """A line was asked for and the fixture directory has no clip of it.

    A subclass of `EngineUnavailable` so that a caller which handles "no engine
    here" also handles "no clip here", but distinct so that a test can assert on
    the specific failure — the two have different remedies, and conflating them
    is how "regenerate the fixtures" gets misread as "install the models".
    """


class ClipManifest:
    """The index of a fixture clip directory: digest -> clip, plus provenance.

    A manifest rather than a naming convention, for one reason: the clips are
    committed binaries and the interesting question about a committed binary is
    always *where did this come from*. The manifest answers it per clip — engine
    identity, voice, sample rate, duration, byte count — so a reviewer reading a
    trace that says `replay:tts:system-say/Samantha` can find the clip, the line
    it speaks, and the command that made it.
    """

    FILENAME = "manifest.json"

    def __init__(self, *, root: Path, data: dict[str, Any]) -> None:
        self.root = root
        self.data = data
        self.clips: dict[str, dict[str, Any]] = dict(data.get("clips", {}))

    @classmethod
    def load(cls, directory: str | Path) -> "ClipManifest":
        root = Path(directory)
        path = root / cls.FILENAME
        if not path.is_file():
            raise MissingClipError(
                "tts:fixture",
                f"no clip manifest at {path}",
                "run `make audio-fixtures` to synthesise and index the sample clips",
            )
        return cls(root=root, data=json.loads(path.read_text(encoding="utf-8")))

    @property
    def engine(self) -> str:
        """Identity of the engine that recorded this directory."""
        return str(self.data.get("engine", "unknown"))

    @property
    def voice(self) -> str | None:
        voice = self.data.get("voice")
        return str(voice) if voice is not None else None

    @property
    def sample_rate(self) -> int:
        return int(self.data.get("sample_rate", DEFAULT_SAMPLE_RATE))

    def entry(self, text: str, sample_rate: int) -> dict[str, Any] | None:
        return self.clips.get(text_digest(text, sample_rate))

    def texts(self) -> list[str]:
        """Every line this directory can speak, in manifest order."""
        return [str(entry.get("text", "")) for entry in self.clips.values()]

    def total_bytes(self) -> int:
        return sum(int(entry.get("bytes", 0)) for entry in self.clips.values())

    def total_samples(self) -> int:
        return sum(int(entry.get("samples", 0)) for entry in self.clips.values())


class FixtureTTS:
    """Reads pre-synthesised clips from disk. How a clean clone runs the audio path.

    This is the engine that makes the cardinal rule of the repo hold for voice:
    `pytest` with no models, no API keys and no network exercises the real
    perturbation chain, the real file I/O, the real digests and the real trace
    events, because the only thing it replaces is the synthesiser.

    It is strict on a miss. A fixture engine that quietly synthesised a fallback,
    or returned silence, would let a session complete with audio nobody chose,
    and every number derived from it would be sincere and wrong. So an unknown
    line raises `MissingClipError` naming the line and the regeneration command.

    The identity it reports is `replay:<the engine that recorded the clip>`, so a
    trace never claims a live synthesis it did not perform, and never loses track
    of which synthesiser the audio actually came from.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        manifest: ClipManifest | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.manifest = manifest if manifest is not None else ClipManifest.load(self.directory)
        self.name = f"replay:{self.manifest.engine}"
        self.is_replay = True
        self.voice = self.manifest.voice

    def available(self) -> bool:
        return bool(self.manifest.clips)

    def describe(self) -> str:
        return (
            f"{self.name} ({len(self.manifest.clips)} clip(s) from {self.directory}, "
            f"{self.manifest.sample_rate} Hz, voice={self.voice or '-'})"
        )

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        entry = self.manifest.entry(text, sample_rate)
        if entry is None:
            raise MissingClipError(
                self.name,
                f"no clip for {text!r} at {sample_rate} Hz in {self.directory} "
                f"({len(self.manifest.clips)} clip(s) indexed)",
                "run `make audio-fixtures` on a machine with a TTS engine to record it",
            )
        audio, rate = read_audio(self.directory / str(entry["file"]))
        if rate != sample_rate:
            raise MissingClipError(
                self.name,
                f"clip {entry['file']} is {rate} Hz but {sample_rate} Hz was asked for",
                "regenerate the fixtures at the rate the scenario declares",
            )
        return SynthesisResult(
            audio=audio,
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice or entry.get("voice") or self.voice,
            synthesis_s=None,  # a file read is not a synthesis and must not be timed as one
            replayed=True,
            text=text,
        )

    def __repr__(self) -> str:
        return f"FixtureTTS({self.directory}, clips={len(self.manifest.clips)})"
