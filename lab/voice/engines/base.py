"""The engine interfaces: two roles, several backends, one identity string each.

WHAT THIS DEMONSTRATES
----------------------
A voice evaluation is a measurement of a *pipeline*, and an aggregate that cannot
be attributed to a stage of that pipeline is not actionable. "WER went up" is a
sentence; "WER went up when we swapped the STT model, and TTS is unchanged" is a
finding. So every engine in this package is required to answer three questions
before it is allowed anywhere near a trace:

    available()   can you run at all, on this machine, right now?
    name          who exactly are you, precisely enough to pin a regression on?
    describe()    what would a human need to know to reproduce you?

`name` is written into the `engine` field of every trace event the engine
produced (`lab.trace.schema` makes it a first-class field for exactly this
reason), so any figure derived from those events can always say which engine
produced it. That is the whole point of the interface: not polymorphism for its
own sake, but attribution.

TWO ROLES, NOT ONE ABSTRACTION
------------------------------
`TTSEngine` turns text into samples; `STTEngine` turns samples into text. They
are separate protocols with separate result types rather than one "AudioEngine",
because the interesting failure modes are asymmetric: a TTS failure is a bad
fixture, an STT failure is a bad measurement, and the honesty machinery each one
needs is different. TTS results carry synthesis cost (harness compute, which must
stay out of latency figures); STT results carry *provenance*, which is what stops
a stand-in transcript from being quoted as an engine's output.

PROVENANCE IS THE HONESTY FIELD
-------------------------------
`Transcription.provenance` has three values and they are not interchangeable:

    "engine"     a real STT engine ran on these samples in this process
    "recorded"   a real STT engine ran on these samples once, and the answer was
                 committed to a cassette; this is a replay of that answer
    "reference"  no STT ever ran; the text is the known ground truth, standing in
                 so the rest of the pipeline can be exercised

The third is a legitimate and useful mode — it lets the adapter, the perturbation
chain, the trace and every contract be tested with no models installed — and it
is also radioactive, because word error rate against the reference *is exactly
zero by construction*. A harness that let that number reach a report would be
publishing a fabricated 0% WER. So provenance travels on the result, is written
into the trace, and `lab.voice.adapter.audio_wer_report` refuses to compute WER
from a trace whose transcripts are references. See that module for the refusal.

WHY `available()` INSTEAD OF JUST RAISING
-----------------------------------------
The adapter needs to *choose*, and a report needs to say what was chosen. A
backend that only signals its absence by raising forces every caller into
try/except-as-control-flow, and makes "which engines could this machine have
used?" unanswerable without attempting all of them. `available()` is cheap and
side-effect-free; `require_available()` turns a False into the actionable error,
naming `scripts/setup_audio.sh`, because "model not found" without the command
that fixes it is a support ticket rather than a message.

SCOPE
-----
Engines are synchronous and whole-buffer. No streaming, no partial hypotheses, no
async. That is a deliberate consequence of the half-duplex, file-based design
argued for in `lab.voice.adapter`: streaming would buy barge-in measurement and
cost the ability to attribute latency to a stage, and this repo has chosen
attribution. Nothing here pretends otherwise.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Audio",
    "DEFAULT_SAMPLE_RATE",
    "SETUP_SCRIPT",
    "EngineUnavailable",
    "Provenance",
    "SynthesisResult",
    "Transcription",
    "TTSEngine",
    "STTEngine",
    "require_available",
    "audio_digest",
    "text_digest",
    "pcm16_bytes",
    "quantise_pcm16",
    "duration_s",
]

#: Mono float audio in the nominal range [-1.0, 1.0]. The same alias
#: `lab.voice.perturb` uses, so an engine's output feeds a perturbation with no
#: conversion step and no opportunity for a shape mistake.
Audio = NDArray[np.float64]

#: 16 kHz mono is the working rate for the whole audio path. It is what
#: whisper.cpp consumes natively, it is above the Nyquist rate for the 3400 Hz
#: telephone passband `lab.voice.perturb` models, and it halves the fixture size
#: of the 32 kHz alternative for no measurable difference to either STT accuracy
#: or the perturbations. Engines that synthesise at another rate resample to it
#: and say so in their descriptor.
DEFAULT_SAMPLE_RATE: int = 16_000

#: Named in every "engine not installed" message. A missing-model error whose
#: text does not contain the command that fixes it is a message that costs the
#: reader a directory tour.
SETUP_SCRIPT: str = "scripts/setup_audio.sh"

#: See the module docstring. "reference" is the value that makes WER meaningless.
Provenance = Literal["engine", "recorded", "reference"]


class EngineUnavailable(RuntimeError):
    """An engine was asked to work and cannot, with the remedy in the message.

    Carries the parts separately as well as formatted, so a report can group
    failures by engine without parsing prose.
    """

    def __init__(self, engine: str, reason: str, remedy: str | None = None) -> None:
        self.engine = engine
        self.reason = reason
        self.remedy = remedy or f"run {SETUP_SCRIPT} to install the local models"
        super().__init__(f"{engine} is unavailable: {reason}. Remedy: {self.remedy}")


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #


def pcm16_bytes(audio: Audio) -> int:
    """Byte count of `audio` as 16-bit PCM — the size that crossed the boundary.

    Reported on `audio_emitted` rather than the float64 in-memory size, because
    float64 is an implementation detail of this harness and 16-bit PCM is what a
    telephony leg actually carries. Two bytes per sample, mono.
    """
    return int(np.asarray(audio).size) * 2


def duration_s(audio: Audio, sample_rate: int) -> float:
    """Audible length of `audio` in seconds. Exact: it is a division, not a clock read."""
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate!r}")
    return float(np.asarray(audio).size) / float(sample_rate)


def quantise_pcm16(audio: Audio) -> NDArray[np.int16]:
    """Clip to [-1, 1] and quantise to int16 — the canonical byte form of a clip."""
    array = np.asarray(audio, dtype=np.float64)
    clipped = np.clip(array, -1.0, 1.0)
    return np.round(clipped * 32767.0).astype(np.int16)


def audio_digest(audio: Audio, sample_rate: int) -> str:
    """Stable content digest of a clip: sha256 over its 16-bit PCM bytes.

    The cassette key for a recorded transcript. Two properties make it the right
    key rather than a convenience:

    *   It is a function of the *audio*, so a change to the perturbation chain,
        its seed, or the TTS voice produces a different key and the cassette
        misses instead of replaying a transcript recorded from different sound.
        A fixture that silently answers for audio it never heard is worse than no
        fixture at all.
    *   It quantises to int16 first, so it is stable across platforms and across
        float rounding in the resampler, which a digest of the float64 buffer
        would not be.

    The sample rate is mixed in because the same samples at 8 kHz and 16 kHz are
    different sounds.
    """
    hasher = hashlib.sha256()
    hasher.update(f"pcm16|{int(sample_rate)}|".encode())
    hasher.update(quantise_pcm16(audio).tobytes())
    return hasher.hexdigest()


def text_digest(text: str, sample_rate: int) -> str:
    """Stable key for a pre-synthesised clip of `text` at `sample_rate`.

    Deliberately *not* keyed on the engine or the voice: a fixture directory holds
    one recorded voice, recorded once, and its manifest states which engine and
    voice produced it. Keying on them too would let a directory hold two voices
    of the same line and hand back whichever the caller happened to name, which
    is a way to get an unnoticed A/B in your fixtures.
    """
    hasher = hashlib.sha256()
    hasher.update(f"text|{int(sample_rate)}|".encode())
    hasher.update(text.encode("utf-8"))
    return hasher.hexdigest()


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


class SynthesisResult(BaseModel):
    """Samples produced from text, plus who produced them and what it cost.

    `synthesis_s` is recorded because it is harness compute and must be visible
    in order to be *excluded*. With a text-in/text-out system under test the TTS
    belongs to the harness, so charging its cost to the agent would be precisely
    the error `lab.voice.calibration` exists to prevent. It is reported as its own
    figure on the trace, never folded into response latency.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    audio: Audio = Field(description="Mono float samples, nominal range [-1, 1].")
    sample_rate: int = Field(gt=0)
    engine: str = Field(min_length=1, description="Engine identity, written to the trace.")
    voice: str | None = None
    synthesis_s: float | None = Field(
        default=None,
        description="Wall time the synthesis itself took, seconds. Harness cost.",
    )
    replayed: bool = Field(
        default=False,
        description="True when these samples were read from a fixture, not synthesised now.",
    )
    text: str = ""

    @property
    def duration_s(self) -> float:
        """Audible length of the clip in seconds."""
        return duration_s(self.audio, self.sample_rate)

    @property
    def num_bytes(self) -> int:
        """Size of the clip as 16-bit PCM."""
        return pcm16_bytes(self.audio)

    def describe(self) -> str:
        return (
            f"{self.engine} voice={self.voice or '-'} "
            f"{self.duration_s:.3f}s {self.num_bytes}B @ {self.sample_rate}Hz"
        )


class Transcription(BaseModel):
    """Text recovered from samples, plus who recovered it and how much to trust it.

    `provenance` is the field that matters — see the module docstring. `confidence`
    is `None` unless the engine really reports one: inventing 1.0 for an engine
    that has no opinion is how a confidence-gated check ends up gated on nothing.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    engine: str = Field(min_length=1)
    provenance: Provenance = "engine"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    language: str | None = None
    transcribe_s: float | None = Field(
        default=None, description="Wall time the transcription took, seconds. Harness cost."
    )

    @property
    def is_measurable(self) -> bool:
        """True when a word error rate computed from this transcript means anything.

        False for `provenance == "reference"`: that text *is* the reference, so
        the WER against it is zero by construction and says nothing about any
        engine. Callers that report WER must consult this rather than assuming.
        """
        return self.provenance in ("engine", "recorded")

    def trace_payload(self) -> dict[str, Any]:
        """Extra payload keys for the `transcript_in` event carrying this result.

        Provenance goes into the trace, not just into a log line, so that a
        refusal to report WER can be decided from a trace file alone — including
        one a reviewer downloaded without ever running the harness.
        """
        payload: dict[str, Any] = {"provenance": self.provenance}
        if self.language is not None:
            payload["language"] = self.language
        if self.transcribe_s is not None:
            payload["transcribe_s"] = round(self.transcribe_s, 6)
        return payload


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #


@runtime_checkable
class TTSEngine(Protocol):
    """Text in, samples out. The entire contract on a synthesis backend."""

    #: Identity written into the trace's `engine` field. Stable and specific:
    #: "tts:kokoro/af_heart", not "kokoro".
    name: str

    #: True when the samples came from a fixture rather than a live synthesis.
    #: The adapter reads it to tag the trace as a replay, which is what stops a
    #: replayed session being mistaken for evidence that the engines still work.
    is_replay: bool

    def available(self) -> bool:
        """Can this engine run here and now? Cheap, and no side effects."""
        ...

    def describe(self) -> str:
        """One line a human could use to reproduce this engine."""
        ...

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult: ...


@runtime_checkable
class STTEngine(Protocol):
    """Samples in, text out. The entire contract on a transcription backend."""

    name: str
    is_replay: bool

    def available(self) -> bool: ...

    def describe(self) -> str: ...

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription: ...


def require_available(engine: TTSEngine | STTEngine) -> None:
    """Raise `EngineUnavailable` unless `engine.available()`.

    Called at the top of `synthesise` / `transcribe` in every backend, so the
    error arrives at the moment of use with the engine's own remedy attached,
    rather than as an `ImportError` or a `FileNotFoundError` from three frames
    down inside somebody else's library.
    """
    if not engine.available():
        raise EngineUnavailable(engine.name, "not installed or not configured")
