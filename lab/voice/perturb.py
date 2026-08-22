"""Caller-audio perturbations: the same scenario, made harder on purpose.

WHAT THIS DEMONSTRATES
----------------------
An agent that only ever hears a clean studio-quality synthetic voice has not been
evaluated for the phone. The interesting question is not "does it work?" but
"where does it stop working?", and answering that needs a *controlled* way to
degrade the input: one axis at a time, by a stated amount, reproducibly.

Three properties make these usable as evaluation instruments rather than demos:

**1. Every perturbation returns a descriptor, and the descriptor carries what was
actually achieved — not only what was asked for.** A target of 10 dB SNR is a
request; `achieved_snr_db` is a measurement taken on the returned signal. A 5%
packet-loss request over 40 packets cannot produce 5%, and the descriptor says so
by reporting `2/40` rather than the 0.05 it was handed. Any result computed from
perturbed audio can therefore state which perturbation was active and at what
real strength, which is the difference between "the agent fails in noise" and
"WER doubles between 15 dB and 10 dB SNR".

**2. They are seeded.** Noise and packet loss draw from an explicitly seeded
`numpy.random.Generator`, so a failing perturbed case is a case that can be
handed to someone else and reproduced exactly. An unreproducible failure in an
eval suite is indistinguishable from flakiness and gets ignored.

**3. They are pure functions of arrays.** No file I/O, no audio library, no
sample-rate guessing. The adapter owns loading and writing audio; this module
owns the maths. That split is what lets the perturbations be tested for shape,
finiteness and measured strength without any audio fixtures at all.

ORDER MATTERS, SO ORDER IS RECORDED
-----------------------------------
These operations do not commute, and the difference is not subtle.
Band-limiting and *then* adding noise leaves full-band noise that a real
telephone circuit would never deliver; adding noise and *then* band-limiting
filters the noise along with the speech, which is what actually happens on a
phone call. `apply_chain` therefore records the chain in order in its
descriptors, and the payload helper writes that order into the trace.

SCOPE — STATED, NOT DISCOVERED LATER
------------------------------------
* Speed and pitch are coupled. Both `resample_speed` and `shift_pitch` are
  resampling, so changing one changes the other, exactly as playing a tape faster
  does. Duration-preserving pitch shift needs a phase vocoder or overlap-add
  time-stretch; that is real DSP with real artefacts of its own, and a
  half-hearted version would perturb the audio in ways nobody could attribute.
  It is out of scope, and `shift_pitch` says so in its docstring rather than
  pretending.
* `telephone_band` is a zero-phase FFT-domain mask with raised-cosine edges, not
  a model of a real telephony codec. It reproduces the 300-3400 Hz passband that
  removes the low-frequency energy of a male voice and the fricative energy of
  sibilants, which is the part that matters for STT. It does not reproduce G.711
  companding, jitter-buffer behaviour, or codec quantisation noise.
* It is offline and non-streaming: the whole buffer is transformed at once.
* Reverberation, competing-talker babble and dropout bursts with realistic
  length distributions are not implemented.

REQUIREMENTS
------------
This is the one module in `lab.voice` that needs numpy. It is imported at module
scope here because "pure numpy on float arrays" is the module's entire contract —
but nothing else in `lab` imports this module at load time, so an install without
the `[audio]` extra is unaffected until it asks for a perturbation.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Audio",
    "DEFAULT_PACKET_MS",
    "DEFAULT_SEED",
    "PERTURBATIONS",
    "PerturbationDescriptor",
    "TELEPHONE_HIGH_HZ",
    "TELEPHONE_LOW_HZ",
    "add_noise",
    "apply",
    "apply_chain",
    "packet_loss",
    "perturbation_payload",
    "resample_speed",
    "shift_pitch",
    "telephone_band",
]

#: Mono float audio in the nominal range [-1.0, 1.0]. Nothing here enforces the
#: range — adding noise can legitimately exceed it — but the descriptors always
#: report the resulting peak so a caller can see when it happened.
Audio = NDArray[np.float64]

#: Seeded by default. A perturbation whose default is "random" produces eval
#: failures nobody can reproduce, which are failures nobody investigates.
DEFAULT_SEED: int = 20260822

#: 20 ms is the standard RTP payload duration for narrowband speech, so it is the
#: unit a real network actually loses.
DEFAULT_PACKET_MS: float = 20.0

#: The classic analogue telephone passband.
TELEPHONE_LOW_HZ: float = 300.0
TELEPHONE_HIGH_HZ: float = 3400.0

NoiseKind = Literal["white", "pink"]
FillMode = Literal["zero", "hold"]


class PerturbationDescriptor(BaseModel):
    """What was done to the audio, what was requested, and what was achieved.

    Three separate dicts on purpose. `params` is the request and is enough to
    reproduce the perturbation; `measured` is what the output actually contains
    and is the honest strength to report a result against; `name` is the stable
    key a chart can group by. Collapsing request and measurement into one field
    is how a suite ends up plotting nominal SNR while the real SNR was something
    else.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    params: dict[str, Any] = Field(
        default_factory=dict, description="The requested settings; reproduces the call."
    )
    measured: dict[str, Any] = Field(
        default_factory=dict, description="Measured properties of the returned audio."
    )

    def label(self) -> str:
        """Short stable label for a chart axis or a result row."""
        if self.name == "add_noise":
            return f"noise@{self.params.get('snr_db')}dB/{self.params.get('kind')}"
        if self.name == "resample_speed":
            return f"speed@{self.params.get('factor')}x"
        if self.name == "shift_pitch":
            return f"pitch@{self.params.get('semitones')}st"
        if self.name == "telephone_band":
            return f"band@{self.params.get('low_hz')}-{self.params.get('high_hz')}Hz"
        if self.name == "packet_loss":
            dropped = self.measured.get("packets_dropped")
            total = self.measured.get("packets")
            return f"loss@{dropped}/{total}pkt"
        return self.name

    def describe(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in self.params.items())
        measured = ", ".join(f"{k}={v}" for k, v in self.measured.items())
        return f"{self.name}({parts})  measured: {measured}"

    def __repr__(self) -> str:
        return f"PerturbationDescriptor({self.label()})"


def perturbation_payload(
    descriptors: Sequence[PerturbationDescriptor],
) -> dict[str, Any]:
    """Trace payload recording which perturbations were active, in order.

    Intended for the audio adapter to splat into a trace event, e.g.
    `builder.session_start(**perturbation_payload(descriptors))` or onto the
    `audio_emitted` event for the perturbed caller turn. Recording it in the
    trace — rather than in a filename or a test id — is what lets a result say
    which perturbation was active without anyone having to remember.

    An empty sequence still produces `{"perturbations": [], "perturbation_chain":
    "clean"}`, because "this run was unperturbed" is a fact worth recording
    explicitly; an absent key is indistinguishable from an adapter that forgot.
    """
    return {
        "perturbations": [d.model_dump() for d in descriptors],
        "perturbation_chain": " -> ".join(d.label() for d in descriptors) or "clean",
    }


# --------------------------------------------------------------------------- #
# Input hygiene
# --------------------------------------------------------------------------- #


def _as_audio(audio: Any, *, name: str = "audio") -> Audio:
    """Coerce to 1-D float64 and reject anything that would poison the maths.

    NaN and infinity are rejected up front rather than allowed to propagate. A
    single NaN silently turns every downstream RMS, SNR and WER figure into NaN,
    and by the time that surfaces the cause is many modules away.
    """
    array = np.asarray(audio, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(
            f"{name} must be 1-D mono float audio, got shape {array.shape}. "
            "Mix or select a channel in the adapter, not here."
        )
    if array.size == 0:
        raise ValueError(f"{name} is empty; there is nothing to perturb")
    if not np.all(np.isfinite(array)):
        bad = int(np.count_nonzero(~np.isfinite(array)))
        raise ValueError(
            f"{name} contains {bad} non-finite sample(s) of {array.size}; "
            "refusing to perturb audio that is already broken"
        )
    return array


def _check_sample_rate(sample_rate: int) -> int:
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate!r}")
    return int(sample_rate)


def _rms(array: Audio) -> float:
    return float(np.sqrt(np.mean(np.square(array))))


def _rng_for(seed: int | None, rng: np.random.Generator | None) -> np.random.Generator:
    if rng is not None:
        return rng
    return np.random.default_rng(DEFAULT_SEED if seed is None else seed)


# --------------------------------------------------------------------------- #
# Perturbations
# --------------------------------------------------------------------------- #


def add_noise(
    audio: Audio,
    *,
    snr_db: float,
    sample_rate: int = 16_000,
    kind: NoiseKind = "white",
    seed: int | None = None,
    rng: np.random.Generator | None = None,
    clip: bool = False,
) -> tuple[Audio, PerturbationDescriptor]:
    """Add noise scaled to hit a target signal-to-noise ratio.

    The noise is scaled from the *measured* power of this particular signal, so
    the SNR is honoured for quiet and loud recordings alike. A fixed noise
    amplitude would instead mean "loud clips are easy, quiet clips are
    impossible", which makes the perturbation a confound rather than an axis.

    Args:
        audio: Mono float samples.
        snr_db: Target ratio of signal power to noise power, in decibels. Lower
            is harder; 20 dB is a quiet office, 10 dB is a busy street, 0 dB is
            noise as loud as the speech.
        sample_rate: Recorded in the descriptor. The noise itself is
            sample-rate-independent, and pink shaping is defined over FFT bins.
        kind: `"white"` (flat spectrum) or `"pink"` (1/f, which concentrates
            energy at low frequencies and is a better stand-in for room and
            traffic noise).
        seed: Seeds a fresh generator. Ignored if `rng` is given.
        rng: Bring your own generator, e.g. to draw a whole sweep from one stream.
        clip: Clipping to [-1, 1] is itself a distortion, so it is opt-in. When
            it happens, the descriptor reports how many samples were affected.

    Raises:
        ValueError: if the signal is silent — there is no signal power to set a
            ratio against, and returning "noise at 10 dB above nothing" would be
            a fabricated number.
    """
    samples = _as_audio(audio)
    rate = _check_sample_rate(sample_rate)
    signal_rms = _rms(samples)
    if signal_rms == 0.0:
        raise ValueError(
            "cannot set an SNR against a silent signal (RMS is exactly 0); "
            "the ratio is undefined"
        )

    generator = _rng_for(seed, rng)
    if kind == "white":
        noise = generator.standard_normal(samples.size)
    elif kind == "pink":
        noise = _pink_noise(samples.size, generator)
    else:  # pragma: no cover - guarded by the Literal, kept for runtime misuse
        raise ValueError(f"unknown noise kind {kind!r}; expected 'white' or 'pink'")

    noise_rms = _rms(noise)
    target_noise_rms = signal_rms / (10.0 ** (snr_db / 20.0))
    scaled = noise * (target_noise_rms / noise_rms) if noise_rms > 0 else noise
    noisy = samples + scaled

    clipped = 0
    if clip:
        clipped = int(np.count_nonzero(np.abs(noisy) > 1.0))
        noisy = np.clip(noisy, -1.0, 1.0)

    achieved_noise_rms = _rms(scaled)
    achieved = (
        20.0 * np.log10(signal_rms / achieved_noise_rms)
        if achieved_noise_rms > 0
        else float("inf")
    )
    descriptor = PerturbationDescriptor(
        name="add_noise",
        params={
            "snr_db": snr_db,
            "kind": kind,
            "sample_rate": rate,
            "seed": DEFAULT_SEED if (seed is None and rng is None) else seed,
            "clip": clip,
        },
        measured={
            "signal_rms": signal_rms,
            "noise_rms": achieved_noise_rms,
            "achieved_snr_db": float(achieved),
            "peak": float(np.max(np.abs(noisy))),
            "clipped_samples": clipped,
            "samples": int(noisy.size),
        },
    )
    return noisy, descriptor


def _pink_noise(size: int, generator: np.random.Generator) -> Audio:
    """1/f noise by shaping white noise in the FFT domain.

    The bin at DC is zeroed instead of being divided by a frequency of zero; a
    DC offset is not noise a microphone would produce, and keeping it would bias
    the RMS used to set the SNR.
    """
    white = generator.standard_normal(size)
    spectrum = np.fft.rfft(white)
    frequencies = np.arange(spectrum.size, dtype=np.float64)
    scaling = np.ones_like(frequencies)
    scaling[1:] = 1.0 / np.sqrt(frequencies[1:])
    scaling[0] = 0.0
    shaped = np.fft.irfft(spectrum * scaling, n=size)
    return np.asarray(shaped, dtype=np.float64)


def resample_speed(
    audio: Audio,
    *,
    factor: float,
    sample_rate: int = 16_000,
) -> tuple[Audio, PerturbationDescriptor]:
    """Play the audio `factor` times faster by resampling.

    `factor > 1` is faster and shorter and higher-pitched; `factor < 1` is slower
    and longer and lower. Speed and pitch move together because this is
    resampling — the tape-speed analogy is exact. Interpolation is linear, which
    is adequate for a perturbation whose purpose is to stress an STT engine and
    which nobody is going to listen to for fidelity.

    Rate perturbation is worth having because it is the axis real callers vary on
    most: an agent tuned on evenly-paced synthetic speech can fall apart on a
    caller who talks fast, and no amount of noise testing would reveal it.

    Raises:
        ValueError: for a non-positive factor, or one so extreme that the output
            would be shorter than two samples (there would be nothing left to
            interpolate).
    """
    samples = _as_audio(audio)
    rate = _check_sample_rate(sample_rate)
    if factor <= 0:
        raise ValueError(f"factor must be positive, got {factor!r}")

    output_size = int(round(samples.size / factor))
    if output_size < 2:
        raise ValueError(
            f"factor={factor} would reduce {samples.size} samples to {output_size}; "
            "too few to resample"
        )

    # Map output index -> fractional input index across the full span, so the
    # first and last samples are preserved exactly and no phantom silence is
    # introduced at either end.
    source_positions = np.linspace(0.0, samples.size - 1, output_size)
    resampled = np.interp(
        source_positions, np.arange(samples.size, dtype=np.float64), samples
    )
    descriptor = PerturbationDescriptor(
        name="resample_speed",
        params={"factor": factor, "sample_rate": rate},
        measured={
            "input_samples": int(samples.size),
            "output_samples": int(resampled.size),
            "input_duration_s": samples.size / rate,
            "output_duration_s": resampled.size / rate,
            "peak": float(np.max(np.abs(resampled))),
        },
    )
    return np.asarray(resampled, dtype=np.float64), descriptor


def shift_pitch(
    audio: Audio,
    *,
    semitones: float,
    sample_rate: int = 16_000,
) -> tuple[Audio, PerturbationDescriptor]:
    """Shift pitch by `semitones`, **also changing the duration**.

    Implemented as `resample_speed` with `factor = 2 ** (semitones / 12)`, which
    is the same operation expressed in the unit a voice is naturally described in:
    +4 semitones approximates a lighter voice, -4 a deeper one, and that is the
    axis an STT engine trained on one demographic tends to fail along.

    Duration-preserving pitch shift is deliberately **not** implemented — see the
    module docstring. If duration matters for the comparison being made, hold
    pitch fixed and note that this perturbation moves two variables at once. The
    descriptor records both the semitone request and the resulting length so the
    coupling is visible in the artifact rather than remembered.
    """
    factor = float(2.0 ** (semitones / 12.0))
    shifted, resample_descriptor = resample_speed(
        audio, factor=factor, sample_rate=sample_rate
    )
    descriptor = PerturbationDescriptor(
        name="shift_pitch",
        params={"semitones": semitones, "sample_rate": _check_sample_rate(sample_rate)},
        measured={
            **resample_descriptor.measured,
            "resample_factor": factor,
            "duration_changed": resample_descriptor.measured["input_samples"]
            != resample_descriptor.measured["output_samples"],
        },
    )
    return shifted, descriptor


def telephone_band(
    audio: Audio,
    *,
    sample_rate: int = 16_000,
    low_hz: float = TELEPHONE_LOW_HZ,
    high_hz: float = TELEPHONE_HIGH_HZ,
    transition_hz: float = 100.0,
) -> tuple[Audio, PerturbationDescriptor]:
    """Band-limit to the telephone passband (300-3400 Hz by default).

    A zero-phase FFT-domain mask with raised-cosine edges. The transition band
    matters: a brick wall in the frequency domain is a sinc in the time domain,
    which smears energy across the whole buffer and adds ringing that has nothing
    to do with telephony. A 100 Hz raised-cosine roll-off keeps the artefacts
    small enough that a WER change can be attributed to the missing band rather
    than to the filter.

    This is the perturbation most likely to matter and least likely to be tested:
    wideband synthetic speech carries energy an actual phone call never delivers,
    so an agent evaluated only on clean 16 kHz TTS has been evaluated on audio it
    will never receive.

    `energy_retained` in the descriptor is the fraction of total signal power
    surviving the filter — the direct measurement of how much this perturbation
    actually removed from *this* recording, which depends on the voice.
    """
    samples = _as_audio(audio)
    rate = _check_sample_rate(sample_rate)
    if not 0.0 <= low_hz < high_hz:
        raise ValueError(
            f"need 0 <= low_hz < high_hz, got low_hz={low_hz!r} high_hz={high_hz!r}"
        )
    if transition_hz < 0:
        raise ValueError(f"transition_hz must be non-negative, got {transition_hz!r}")

    spectrum = np.fft.rfft(samples)
    frequencies = np.fft.rfftfreq(samples.size, d=1.0 / rate)
    mask = np.zeros_like(frequencies)
    mask[(frequencies >= low_hz) & (frequencies <= high_hz)] = 1.0

    if transition_hz > 0:
        lower = (frequencies > low_hz - transition_hz) & (frequencies < low_hz)
        mask[lower] = 0.5 * (
            1.0
            - np.cos(
                np.pi * (frequencies[lower] - (low_hz - transition_hz)) / transition_hz
            )
        )
        upper = (frequencies > high_hz) & (frequencies < high_hz + transition_hz)
        mask[upper] = 0.5 * (
            1.0 + np.cos(np.pi * (frequencies[upper] - high_hz) / transition_hz)
        )

    filtered = np.fft.irfft(spectrum * mask, n=samples.size)
    input_power = float(np.sum(np.square(samples)))
    output_power = float(np.sum(np.square(filtered)))
    descriptor = PerturbationDescriptor(
        name="telephone_band",
        params={
            "low_hz": low_hz,
            "high_hz": high_hz,
            "transition_hz": transition_hz,
            "sample_rate": rate,
        },
        measured={
            "samples": int(filtered.size),
            "energy_retained": output_power / input_power if input_power > 0 else 0.0,
            "peak": float(np.max(np.abs(filtered))),
            "nyquist_hz": rate / 2.0,
            "high_hz_above_nyquist": high_hz > rate / 2.0,
        },
    )
    return np.asarray(filtered, dtype=np.float64), descriptor


def packet_loss(
    audio: Audio,
    *,
    loss_rate: float,
    sample_rate: int = 16_000,
    packet_ms: float = DEFAULT_PACKET_MS,
    fill: FillMode = "zero",
    seed: int | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[Audio, PerturbationDescriptor]:
    """Drop whole packets of audio, as a real network does.

    Loss is applied per packet, not per sample, because that is the unit that
    actually goes missing: 20 ms of speech vanishes at once. Dropping individual
    samples would produce broadband clicks and measure the agent's tolerance for
    an artefact that no network generates.

    Args:
        loss_rate: Bernoulli probability per packet, in [0, 1]. The *realised*
            loss is reported in the descriptor as dropped over total packets,
            because over a short clip the realised rate can differ from the
            request substantially and the result must be quoted against what
            actually happened.
        packet_ms: Packet duration. 20 ms is the RTP norm for narrowband speech.
        fill: `"zero"` inserts silence (what an unprotected receiver plays);
            `"hold"` repeats the last good sample (a crude concealment, closer to
            what a codec with packet-loss concealment does). Both are worth
            testing: an STT engine can be far more upset by one than the other.

    The output has the same length as the input — packets are blanked in place,
    not removed — so a transcript can still be aligned against the reference.
    """
    samples = _as_audio(audio)
    rate = _check_sample_rate(sample_rate)
    if not 0.0 <= loss_rate <= 1.0:
        raise ValueError(f"loss_rate must be in [0, 1], got {loss_rate!r}")
    if packet_ms <= 0:
        raise ValueError(f"packet_ms must be positive, got {packet_ms!r}")

    packet_samples = max(1, int(round(rate * packet_ms / 1000.0)))
    packet_count = int(np.ceil(samples.size / packet_samples))
    generator = _rng_for(seed, rng)
    drop = generator.random(packet_count) < loss_rate

    damaged = samples.copy()
    samples_lost = 0
    for index in range(packet_count):
        if not drop[index]:
            continue
        start = index * packet_samples
        stop = min(start + packet_samples, samples.size)
        samples_lost += stop - start
        if fill == "zero":
            damaged[start:stop] = 0.0
        elif fill == "hold":
            # Repeat the last surviving sample; at the very start of the buffer
            # there is nothing to hold, so silence is the only honest fill.
            damaged[start:stop] = damaged[start - 1] if start > 0 else 0.0
        else:  # pragma: no cover - guarded by the Literal
            raise ValueError(f"unknown fill {fill!r}; expected 'zero' or 'hold'")

    dropped = int(np.count_nonzero(drop))
    descriptor = PerturbationDescriptor(
        name="packet_loss",
        params={
            "loss_rate": loss_rate,
            "packet_ms": packet_ms,
            "fill": fill,
            "sample_rate": rate,
            "seed": DEFAULT_SEED if (seed is None and rng is None) else seed,
        },
        measured={
            "packets": packet_count,
            "packets_dropped": dropped,
            "realised_loss_rate": dropped / packet_count if packet_count else 0.0,
            "packet_samples": packet_samples,
            "samples_lost": samples_lost,
            "samples": int(damaged.size),
            "peak": float(np.max(np.abs(damaged))),
        },
    )
    return damaged, descriptor


# --------------------------------------------------------------------------- #
# Registry and chaining — the interface the audio adapter calls
# --------------------------------------------------------------------------- #

#: Name -> perturbation. Every entry has the signature
#: `(audio, *, sample_rate, **params) -> (audio, descriptor)`, so an adapter or a
#: scenario file can name a perturbation as a string and pass its parameters as a
#: dict without importing anything from this module.
PERTURBATIONS: dict[
    str, Callable[..., tuple[Audio, PerturbationDescriptor]]
] = {
    "add_noise": add_noise,
    "resample_speed": resample_speed,
    "shift_pitch": shift_pitch,
    "telephone_band": telephone_band,
    "packet_loss": packet_loss,
}


def apply(
    name: str,
    audio: Audio,
    *,
    sample_rate: int = 16_000,
    **params: Any,
) -> tuple[Audio, PerturbationDescriptor]:
    """Apply one perturbation by name.

    Raises:
        KeyError: with the available names listed, because a typo in a scenario
            file should tell you the answer rather than send you to the source.
    """
    function = PERTURBATIONS.get(name)
    if function is None:
        raise KeyError(
            f"unknown perturbation {name!r}; available: "
            f"{', '.join(sorted(PERTURBATIONS))}"
        )
    return function(audio, sample_rate=sample_rate, **params)


def apply_chain(
    audio: Audio,
    steps: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    sample_rate: int = 16_000,
) -> tuple[Audio, list[PerturbationDescriptor]]:
    """Apply perturbations in sequence, returning every descriptor in order.

    The order is the caller's, and it is preserved in the returned list because
    these operations do not commute (see the module docstring). The realistic
    telephone chain is noise first, band-limiting second, loss last:

        audio, descriptors = apply_chain(
            clean,
            [
                ("add_noise", {"snr_db": 15.0}),
                ("telephone_band", {}),
                ("packet_loss", {"loss_rate": 0.02}),
            ],
            sample_rate=16_000,
        )

    Note that a step which changes the length — `resample_speed`, `shift_pitch` —
    changes what the following steps see, which is another reason the chain is
    recorded rather than assumed.
    """
    current = _as_audio(audio)
    descriptors: list[PerturbationDescriptor] = []
    for name, params in steps:
        current, descriptor = apply(
            name, current, sample_rate=sample_rate, **dict(params)
        )
        descriptors.append(descriptor)
    return current, descriptors
