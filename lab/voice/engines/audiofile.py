"""Reading and writing clips — and the arithmetic behind "never commit WAV".

WHAT THIS DEMONSTRATES
----------------------
The audio path is file-based (see `lab.voice.adapter` for why), so clips get
written to disk, and a handful of them get committed to git as the proof that the
path really runs. Which makes the file format a repository-design decision rather
than a detail, and it is worth showing the numbers instead of asserting a
preference.

THE ARITHMETIC
--------------
Uncompressed 16 kHz mono PCM16 costs

    16000 samples/s x 2 bytes/sample = 32000 bytes/s = 31.25 KiB per second

so the 29 clips committed under `fixtures/audio/clips/` — 84.1 seconds of real
speech — would be **2628.7 KiB** of WAV. The same clips as 16 kHz mono Ogg Opus
are **296.9 KiB** in total, an 8.9x reduction, at a bitrate where the degradation
is inaudible to a listener and, more to the point, invisible to an STT engine.

Those are measured figures, not estimates: `scripts/make_audio_fixtures.py`
prints both totals when it regenerates, and `fixtures/audio/audio_fixtures.md`
records them alongside the manifest.

Git makes the difference worse than it looks. A binary blob is stored per
revision with no useful delta, so a regenerated fixture set does not replace the
old bytes, it appends to history forever. 2.6 MiB per regeneration of a fixture
set someone will regenerate a dozen times is how a "small" eval repo becomes a
tens-of-megabytes clone. Opus at 296.9 KiB makes that a non-question, which is the
real reason for the rule: not disk space, but the fact that nobody has to think
about it again.

For scale, the eight committed *traces* — which is what every check, metric and
report actually consumes — total 51.0 KiB of JSONL for the same eight sessions.
The audio is the expensive part and the least often needed, which is exactly why
the fixture strategy commits traces for every scenario and audio for as few as
will still prove the path.

WHY OPUS AND NOT MP3, AAC OR VORBIS
-----------------------------------
Opus is royalty-free and IETF-standardised (RFC 6716), it is the codec WebRTC
mandates — so it is what a real voice agent's audio actually travels as — and it
is the only one of the four that is designed for speech at low bitrate rather
than music. Vorbis is also free but is a music codec and sounds worse than Opus
at 24 kbps; MP3 and AAC carry patent and licensing questions that have no place
in an MIT-licensed portfolio repo.

BACKENDS, AND WHY WAV IS STILL SUPPORTED
----------------------------------------
Ogg Opus needs libsndfile (via `soundfile`), which ships prebuilt in the wheel.
WAV is handled by the standard library's `wave` module with no dependency at all,
and it is kept for two reasons that are not "committing it":

*   whisper.cpp reads 16-bit PCM WAV and nothing else, so the STT backend writes
    a temporary WAV to hand over. Temporary, in a temp directory, never committed.
*   A machine with no `soundfile` can still run the whole audio path end to end
    against WAV files it writes and deletes itself. The dependency buys fixture
    *distribution*, not functionality, and it should be possible to see that.

`write_audio` picks the backend from the file extension, and refuses an extension
it cannot honour rather than silently writing a WAV named `.opus`.

WHAT THIS DOES NOT DO
---------------------
No resampling (`lab.voice.perturb.resample_speed` changes speed, which is a
different operation), no multi-channel handling, no metadata tags, no streaming
reads. One mono buffer in, one mono buffer out.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np

from lab.voice.engines.base import Audio, quantise_pcm16

__all__ = [
    "OPUS_SUFFIXES",
    "WAV_SUFFIXES",
    "SUPPORTED_SUFFIXES",
    "AudioFileError",
    "soundfile_available",
    "soundfile_diagnosis",
    "write_audio",
    "read_audio",
    "wav_bytes_for",
]

#: Extensions written as Ogg Opus. `.ogg` included because an Ogg container is
#: what Opus is packaged in for files; `.opus` is the conventional name for
#: exactly that container with an Opus payload.
OPUS_SUFFIXES: frozenset[str] = frozenset({".opus", ".ogg"})

#: Extensions written as 16-bit PCM RIFF WAV via the standard library.
WAV_SUFFIXES: frozenset[str] = frozenset({".wav"})

SUPPORTED_SUFFIXES: frozenset[str] = OPUS_SUFFIXES | WAV_SUFFIXES


class AudioFileError(RuntimeError):
    """A clip could not be read or written, with the reason and the remedy."""


def soundfile_available() -> bool:
    """True when Ogg Opus can be read and written in this interpreter.

    Checks the *capability*, not just the import: libsndfile only grew Ogg Opus
    support in 1.1.0, so an old bundled library imports fine and then fails on
    write. Asking `available_subtypes` is the difference between a clear message
    now and a confusing one later.
    """
    try:
        import soundfile  # noqa: PLC0415 - optional dependency, imported on demand
    except Exception:
        return False
    try:
        return "OPUS" in soundfile.available_subtypes("OGG")
    except Exception:  # pragma: no cover - a libsndfile too old to be asked
        return False


def soundfile_diagnosis() -> str:
    """One line naming the version in play, or why there is none. For error text."""
    try:
        import soundfile  # noqa: PLC0415
    except Exception as exc:
        return f"soundfile is not importable ({exc.__class__.__name__}: {exc})"
    try:
        subtypes = sorted(soundfile.available_subtypes("OGG"))
    except Exception as exc:  # pragma: no cover
        return f"soundfile {soundfile.__version__} could not list OGG subtypes ({exc})"
    return (
        f"soundfile {soundfile.__version__} with libsndfile "
        f"{soundfile.__libsndfile_version__}; OGG subtypes: {', '.join(subtypes) or 'none'}"
    )


def wav_bytes_for(num_samples: int) -> int:
    """Size a 16-bit mono WAV of `num_samples` would occupy, header included.

    Exposed so the fixture generator can print the comparison the module
    docstring makes, computed rather than remembered.
    """
    return 44 + 2 * int(num_samples)


def _require_soundfile(action: str) -> Any:
    if not soundfile_available():
        raise AudioFileError(
            f"cannot {action} Ogg Opus: {soundfile_diagnosis()}. "
            'Install the audio extra (pip install -e ".[audio]") or write a .wav '
            "instead, which needs no dependency."
        )
    import soundfile  # noqa: PLC0415

    return soundfile


def write_audio(
    path: str | Path,
    audio: Audio,
    sample_rate: int,
    *,
    bitrate_mode: str = "VARIABLE",
) -> Path:
    """Write mono `audio` to `path`, choosing the format from the extension.

    Args:
        path: Destination. `.opus`/`.ogg` -> Ogg Opus; `.wav` -> 16-bit PCM WAV.
        audio: Mono float samples. Clipped to [-1, 1] and quantised to int16 —
            for WAV because that is the format, and for Opus so that the bytes on
            disk match the digest in `lab.voice.engines.base.audio_digest`.
        sample_rate: Samples per second.
        bitrate_mode: Passed to libsndfile for Opus. Variable by default, which
            is what a real speech leg uses.

    Returns:
        The path written.

    Raises:
        AudioFileError: unsupported extension, or Opus asked for without a
            libsndfile that can produce it.
    """
    destination = Path(path)
    suffix = destination.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AudioFileError(
            f"unsupported audio extension {suffix or '(none)'} for {destination}; "
            f"supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}. Refusing to guess — "
            "a WAV written under an .opus name is a fixture nobody can trust."
        )
    if sample_rate <= 0:
        raise AudioFileError(f"sample_rate must be positive, got {sample_rate!r}")
    samples = quantise_pcm16(audio)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if suffix in WAV_SUFFIXES:
        with wave.open(str(destination), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(int(sample_rate))
            handle.writeframes(samples.tobytes())
        return destination

    soundfile = _require_soundfile("write")
    soundfile.write(
        str(destination),
        samples,
        int(sample_rate),
        format="OGG",
        subtype="OPUS",
        **{"bitrate_mode": bitrate_mode} if bitrate_mode else {},
    )
    return destination


def read_audio(path: str | Path) -> tuple[Audio, int]:
    """Read a mono clip, returning `(float64 samples in [-1, 1], sample_rate)`.

    Multi-channel input is an error rather than being silently mixed down: a
    stereo fixture in a mono pipeline is a mistake in whatever produced it, and
    averaging the channels here would hide it.
    """
    source = Path(path)
    if not source.is_file():
        raise AudioFileError(f"no such audio file: {source}")
    suffix = source.suffix.lower()

    if suffix in WAV_SUFFIXES:
        with wave.open(str(source), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
        if channels != 1:
            raise AudioFileError(f"{source} has {channels} channels; this path is mono only")
        if width != 2:
            raise AudioFileError(
                f"{source} is {width * 8}-bit; only 16-bit PCM WAV is read here"
            )
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
        return samples, int(rate)

    if suffix in OPUS_SUFFIXES:
        soundfile = _require_soundfile("read")
        data, rate = soundfile.read(str(source), dtype="float64", always_2d=False)
        array = np.asarray(data, dtype=np.float64)
        if array.ndim != 1:
            raise AudioFileError(
                f"{source} has shape {array.shape}; this path is mono only"
            )
        return array, int(rate)

    raise AudioFileError(
        f"unsupported audio extension {suffix or '(none)'} for {source}; "
        f"supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )
