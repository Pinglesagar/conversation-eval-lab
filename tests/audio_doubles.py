"""Test doubles for the audio path: deterministic engines with no models.

WHAT THIS DEMONSTRATES
----------------------
Where the seam between the harness and a vendor actually is. Every engine in
`lab.voice.engines` satisfies a four-method protocol — `name`, `is_replay`,
`available()`, `describe()`, and one of `synthesise`/`transcribe` — and that is
narrow enough that a complete, honest substitute fits in a dozen lines. These
doubles are that substitute, and their existence is the evidence that the
protocol is the right size: a seam you cannot fake in a dozen lines is a seam
that will not be tested.

They are *doubles*, not fakes-pretending-to-be-real. `ToneTTS` produces a tone
whose length is a stated function of the text, so every duration in a trace is
predictable arithmetic rather than a property of a synthesiser; `ScriptedSTT`
returns transcripts a test chose, with the provenance the test chose, so both the
"real engine" and "reference stand-in" branches of the WER refusal can be
exercised on demand. Nothing here claims to be Kokoro or whisper.cpp, and every
identity string says so.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    Audio,
    Provenance,
    SynthesisResult,
    Transcription,
)

__all__ = [
    "SECONDS_PER_WORD",
    "ToneTTS",
    "ScriptedSTT",
    "EchoSTT",
    "UnavailableTTS",
    "expected_duration_s",
]

#: One word of text becomes this much audio. A round number, so a duration in a
#: trace can be checked by counting words instead of by trusting the double.
SECONDS_PER_WORD: float = 0.25


def expected_duration_s(text: str) -> float:
    """The exact duration `ToneTTS` will produce for `text`. Used by assertions."""
    return max(1, len(text.split())) * SECONDS_PER_WORD


class ToneTTS:
    """Synthesises a sine tone whose length is `SECONDS_PER_WORD` per word.

    Deterministic in both length and content, which is what lets a test assert on
    speaking time, byte counts and audio digests without a model. The pitch is a
    parameter so that a caller voice and an agent voice produce genuinely
    different audio — a double where both sides synthesise identical samples
    would let a digest collision pass unnoticed.
    """

    def __init__(self, *, name: str = "tts:test-tone", hz: float = 180.0) -> None:
        self.name = name
        self.hz = hz
        self.is_replay = False
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name} (test double, {self.hz} Hz tone, {SECONDS_PER_WORD}s per word)"

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        self.calls.append(text)
        samples = int(round(expected_duration_s(text) * sample_rate))
        t = np.arange(samples) / sample_rate
        return SynthesisResult(
            audio=0.3 * np.sin(2 * np.pi * self.hz * t),
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice or "double",
            synthesis_s=0.0,
            text=text,
        )


class ScriptedSTT:
    """Returns pre-chosen transcripts in order, with a pre-chosen provenance.

    The knob that matters is `provenance`: it is what the WER refusal reads, so a
    test can drive both sides of that decision — a cassette a real engine
    recorded, and a reference stand-in — without installing anything.
    """

    def __init__(
        self,
        transcripts: Sequence[str],
        *,
        provenance: Provenance = "recorded",
        engine: str = "stt:test-scripted",
        confidence: float | None = None,
    ) -> None:
        self.transcripts = list(transcripts)
        self.provenance = provenance
        self.name = engine
        self.confidence = confidence
        self.is_replay = False
        self.index = 0

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name} (test double, {len(self.transcripts)} scripted transcript(s))"

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        if self.index >= len(self.transcripts):
            raise AssertionError(
                f"{self.name} ran out of scripted transcripts after {self.index}; "
                "the session made more caller turns than the test scripted"
            )
        text = self.transcripts[self.index]
        self.index += 1
        return Transcription(
            text=text,
            engine=self.name,
            provenance=self.provenance,
            confidence=self.confidence,
            language="en",
        )


class EchoSTT:
    """Transcribes by echoing the number of samples it was handed.

    Useless as a transcript and perfect as a probe: it proves the STT leg really
    receives the *perturbed* audio, because the sample count changes when a
    length-changing perturbation is in the chain.
    """

    def __init__(self, *, provenance: Provenance = "recorded") -> None:
        self.name = "stt:test-echo"
        self.is_replay = False
        self.provenance = provenance
        self.seen: list[int] = []

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name} (test double, reports sample counts)"

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        count = int(np.asarray(audio).size)
        self.seen.append(count)
        return Transcription(
            text=f"{count} samples", engine=self.name, provenance=self.provenance
        )


class UnavailableTTS:
    """An engine that is installed and cannot run. Exercises the degradation path."""

    def __init__(self, *, name: str = "tts:test-absent") -> None:
        self.name = name
        self.is_replay = False

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return f"{self.name} (test double, always unavailable)"

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        from lab.voice.engines.base import require_available  # noqa: PLC0415

        require_available(self)
        raise AssertionError("unreachable: require_available must have raised")
