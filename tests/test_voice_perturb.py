"""Tests for `lab.voice.perturb` — shape, finiteness, and measured strength.

WHAT THIS DEMONSTRATES
----------------------
A perturbation is an instrument, so the tests check the *reading on the dial*,
not just that the function ran:

* `test_add_noise_hits_the_requested_snr_exactly` — the achieved SNR reported in
  the descriptor is checked against the request across four orders of loudness.
  A perturbation whose real strength is unknown cannot be an axis on a chart.
* `test_telephone_band_removes_out_of_band_tones_and_keeps_in_band_ones` — a
  100 Hz tone must all but vanish and a 1 kHz tone must survive. That is a direct
  measurement of the filter doing the thing it claims, rather than a smoke test
  that the array came back the right length.
* `test_chain_order_changes_the_result` — noise-then-band and band-then-noise
  produce different audio, which is why the chain is recorded in order and why
  `apply_chain` returns descriptors rather than a set.

Every random draw is seeded, and `test_seeding_is_reproducible` asserts it, so a
failure found on a perturbed case is a failure someone else can reproduce.
"""

from __future__ import annotations

import numpy as np
import pytest

from lab.voice.perturb import (
    DEFAULT_SEED,
    PERTURBATIONS,
    TELEPHONE_HIGH_HZ,
    TELEPHONE_LOW_HZ,
    add_noise,
    apply,
    apply_chain,
    packet_loss,
    perturbation_payload,
    resample_speed,
    shift_pitch,
    telephone_band,
)

SAMPLE_RATE = 16_000

#: Minimal parameters for every registry entry, used by the sweep tests.
MINIMAL_PARAMS: dict[str, dict[str, object]] = {
    "add_noise": {"snr_db": 10.0},
    "resample_speed": {"factor": 1.1},
    "shift_pitch": {"semitones": 2.0},
    "telephone_band": {},
    "packet_loss": {"loss_rate": 0.1},
}


def tone(frequency: float, *, seconds: float = 1.0, amplitude: float = 0.5) -> np.ndarray:
    """A sine at an integer frequency over a whole number of seconds.

    Integer frequency and whole-second duration together mean the tone lands on
    an exact FFT bin, so the spectral tests measure the filter rather than
    windowing leakage.
    """
    samples = int(SAMPLE_RATE * seconds)
    time = np.arange(samples, dtype=np.float64) / SAMPLE_RATE
    return amplitude * np.sin(2.0 * np.pi * frequency * time)


def speechlike() -> np.ndarray:
    """A few harmonics — not speech, but broadband and non-trivial to filter."""
    return sum(tone(frequency, amplitude=0.2) for frequency in (220, 440, 1320, 2640))


# --------------------------------------------------------------------------- #
# Noise
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("snr_db", [20.0, 10.0, 0.0, -6.0])
@pytest.mark.parametrize("kind", ["white", "pink"])
def test_add_noise_hits_the_requested_snr_exactly(snr_db: float, kind: str) -> None:
    clean = speechlike()
    noisy, descriptor = add_noise(
        clean, snr_db=snr_db, sample_rate=SAMPLE_RATE, kind=kind, seed=7  # type: ignore[arg-type]
    )
    assert noisy.shape == clean.shape
    assert np.all(np.isfinite(noisy))
    assert descriptor.measured["achieved_snr_db"] == pytest.approx(snr_db, abs=1e-9)
    assert descriptor.params["snr_db"] == snr_db
    assert descriptor.params["kind"] == kind
    # Lower SNR really is louder noise, measured on the returned signal.
    assert descriptor.measured["noise_rms"] > 0.0


def test_lower_snr_means_more_noise() -> None:
    clean = speechlike()
    quiet, quiet_descriptor = add_noise(clean, snr_db=20.0, seed=1)
    loud, loud_descriptor = add_noise(clean, snr_db=0.0, seed=1)
    assert loud_descriptor.measured["noise_rms"] > quiet_descriptor.measured["noise_rms"]
    assert float(np.std(loud - clean)) > float(np.std(quiet - clean))


def test_seeding_is_reproducible_and_different_seeds_differ() -> None:
    clean = speechlike()
    first, _ = add_noise(clean, snr_db=10.0, seed=42)
    again, _ = add_noise(clean, snr_db=10.0, seed=42)
    other, _ = add_noise(clean, snr_db=10.0, seed=43)
    assert np.array_equal(first, again)
    assert not np.allclose(first, other)


def test_default_seed_is_recorded_so_a_run_can_be_reproduced() -> None:
    _, descriptor = add_noise(speechlike(), snr_db=10.0)
    assert descriptor.params["seed"] == DEFAULT_SEED


def test_pink_noise_is_weighted_to_low_frequencies() -> None:
    """Recover the noise by subtracting the clean signal, then compare spectra."""
    clean = speechlike()
    white = add_noise(clean, snr_db=0.0, kind="white", seed=3)[0] - clean
    pink = add_noise(clean, snr_db=0.0, kind="pink", seed=3)[0] - clean

    def low_band_fraction(signal: np.ndarray) -> float:
        power = np.abs(np.fft.rfft(signal)) ** 2
        cutoff = max(1, power.size // 20)  # lowest 5% of bins
        return float(power[:cutoff].sum() / power.sum())

    assert low_band_fraction(pink) > 3.0 * low_band_fraction(white)


def test_add_noise_refuses_a_silent_signal() -> None:
    with pytest.raises(ValueError, match="silent signal"):
        add_noise(np.zeros(1000), snr_db=10.0)


def test_clipping_is_opt_in_and_counted() -> None:
    loud = tone(440.0, amplitude=0.99)
    unclipped, unclipped_descriptor = add_noise(loud, snr_db=0.0, seed=5)
    clipped, clipped_descriptor = add_noise(loud, snr_db=0.0, seed=5, clip=True)
    assert unclipped_descriptor.measured["peak"] > 1.0
    assert unclipped_descriptor.measured["clipped_samples"] == 0
    assert clipped_descriptor.measured["clipped_samples"] > 0
    assert clipped_descriptor.measured["peak"] <= 1.0
    assert np.max(np.abs(clipped)) <= 1.0


def test_unknown_noise_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown noise kind"):
        add_noise(speechlike(), snr_db=10.0, kind="brown")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Speed and pitch
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("factor", "expected_samples"), [(2.0, 500), (0.5, 2000), (1.0, 1000)]
)
def test_resample_speed_changes_length_predictably(
    factor: float, expected_samples: int
) -> None:
    audio = np.linspace(-1.0, 1.0, 1000)
    resampled, descriptor = resample_speed(audio, factor=factor, sample_rate=SAMPLE_RATE)
    assert resampled.size == expected_samples
    assert descriptor.measured["output_samples"] == expected_samples
    assert descriptor.measured["input_samples"] == 1000
    assert np.all(np.isfinite(resampled))
    # Endpoints are preserved exactly: no phantom silence at either edge.
    assert resampled[0] == pytest.approx(audio[0])
    assert resampled[-1] == pytest.approx(audio[-1])


def test_resample_speed_reports_both_durations() -> None:
    _, descriptor = resample_speed(
        np.linspace(-1.0, 1.0, SAMPLE_RATE), factor=2.0, sample_rate=SAMPLE_RATE
    )
    assert descriptor.measured["input_duration_s"] == pytest.approx(1.0)
    assert descriptor.measured["output_duration_s"] == pytest.approx(0.5)


@pytest.mark.parametrize("factor", [0.0, -1.0])
def test_resample_speed_rejects_non_positive_factor(factor: float) -> None:
    with pytest.raises(ValueError, match="factor must be positive"):
        resample_speed(np.linspace(-1.0, 1.0, 100), factor=factor)


def test_resample_speed_refuses_to_collapse_the_signal() -> None:
    with pytest.raises(ValueError, match="too few to resample"):
        resample_speed(np.linspace(-1.0, 1.0, 10), factor=100.0)


def test_shift_pitch_is_resampling_and_says_so() -> None:
    """An octave up is exactly a factor-of-two resample, duration included."""
    audio = np.linspace(-1.0, 1.0, 1000)
    shifted, descriptor = shift_pitch(audio, semitones=12.0, sample_rate=SAMPLE_RATE)
    expected, _ = resample_speed(audio, factor=2.0, sample_rate=SAMPLE_RATE)
    assert np.allclose(shifted, expected)
    assert descriptor.measured["resample_factor"] == pytest.approx(2.0)
    assert descriptor.measured["duration_changed"] is True
    assert descriptor.params["semitones"] == 12.0
    # The out-of-scope statement is part of the contract, not decoration.
    assert "Duration-preserving pitch shift is deliberately" in (
        shift_pitch.__doc__ or ""
    )


def test_shift_pitch_by_zero_semitones_is_a_no_op_of_the_same_length() -> None:
    audio = np.linspace(-1.0, 1.0, 1000)
    shifted, descriptor = shift_pitch(audio, semitones=0.0)
    assert shifted.size == audio.size
    assert descriptor.measured["duration_changed"] is False


# --------------------------------------------------------------------------- #
# Telephone band
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("frequency", "retained_at_most", "retained_at_least"),
    [
        (100.0, 0.01, None),  # well below the passband: gone
        (1000.0, None, 0.99),  # mid-band: kept
        (2500.0, None, 0.99),  # still in band
        (6000.0, 0.01, None),  # above the passband: gone
    ],
)
def test_telephone_band_removes_out_of_band_tones_and_keeps_in_band_ones(
    frequency: float, retained_at_most: float | None, retained_at_least: float | None
) -> None:
    filtered, descriptor = telephone_band(tone(frequency), sample_rate=SAMPLE_RATE)
    retained = descriptor.measured["energy_retained"]
    assert filtered.size == SAMPLE_RATE
    assert np.all(np.isfinite(filtered))
    if retained_at_most is not None:
        assert retained < retained_at_most
    if retained_at_least is not None:
        assert retained > retained_at_least


def test_telephone_band_defaults_are_the_documented_passband() -> None:
    _, descriptor = telephone_band(speechlike(), sample_rate=SAMPLE_RATE)
    assert descriptor.params["low_hz"] == TELEPHONE_LOW_HZ == 300.0
    assert descriptor.params["high_hz"] == TELEPHONE_HIGH_HZ == 3400.0
    assert descriptor.measured["nyquist_hz"] == SAMPLE_RATE / 2
    assert descriptor.measured["high_hz_above_nyquist"] is False


def test_telephone_band_flags_a_passband_the_sample_rate_cannot_carry() -> None:
    """At 8 kHz sampling, 3400 Hz is under Nyquist; at 6 kHz it is not.

    Reported rather than raised: the filter still does something sensible, but a
    result computed at that sample rate should not be compared with one that had
    the full passband available.
    """
    _, descriptor = telephone_band(tone(500.0, seconds=0.5), sample_rate=6_000)
    assert descriptor.measured["high_hz_above_nyquist"] is True


@pytest.mark.parametrize(
    ("low_hz", "high_hz"), [(3400.0, 300.0), (300.0, 300.0), (-100.0, 3400.0)]
)
def test_telephone_band_rejects_an_impossible_passband(
    low_hz: float, high_hz: float
) -> None:
    with pytest.raises(ValueError, match="low_hz < high_hz"):
        telephone_band(speechlike(), low_hz=low_hz, high_hz=high_hz)


def test_telephone_band_rejects_a_negative_transition() -> None:
    with pytest.raises(ValueError, match="transition_hz must be non-negative"):
        telephone_band(speechlike(), transition_hz=-1.0)


# --------------------------------------------------------------------------- #
# Packet loss
# --------------------------------------------------------------------------- #


def test_total_loss_blanks_everything_and_reports_it() -> None:
    audio = np.linspace(0.1, 1.0, 1000)
    damaged, descriptor = packet_loss(audio, loss_rate=1.0, sample_rate=SAMPLE_RATE)
    assert np.array_equal(damaged, np.zeros_like(audio))
    assert descriptor.measured["packets_dropped"] == descriptor.measured["packets"]
    assert descriptor.measured["realised_loss_rate"] == pytest.approx(1.0)
    assert descriptor.measured["samples_lost"] == audio.size


def test_zero_loss_leaves_the_audio_untouched() -> None:
    audio = np.linspace(0.1, 1.0, 1000)
    damaged, descriptor = packet_loss(audio, loss_rate=0.0)
    assert np.array_equal(damaged, audio)
    assert descriptor.measured["packets_dropped"] == 0
    assert descriptor.measured["realised_loss_rate"] == pytest.approx(0.0)


def test_realised_loss_is_reported_as_dropped_over_total_not_as_the_request() -> None:
    """Over a short clip the realised rate cannot equal the requested one."""
    audio = np.linspace(0.1, 1.0, SAMPLE_RATE // 4)  # 250 ms -> ~13 packets
    _, descriptor = packet_loss(
        audio, loss_rate=0.05, sample_rate=SAMPLE_RATE, packet_ms=20.0, seed=11
    )
    packets = descriptor.measured["packets"]
    dropped = descriptor.measured["packets_dropped"]
    assert packets == 13
    assert descriptor.measured["realised_loss_rate"] == pytest.approx(dropped / packets)
    assert descriptor.params["loss_rate"] == 0.05
    assert f"{dropped}/{packets}pkt" == descriptor.label().split("@")[1]


def test_loss_is_applied_per_packet_not_per_sample() -> None:
    """Whole 20 ms blocks vanish together; that is what a network actually drops."""
    audio = np.linspace(0.1, 1.0, 1000)
    packet_samples = 10
    packet_ms = 1000.0 * packet_samples / SAMPLE_RATE
    damaged, descriptor = packet_loss(
        audio,
        loss_rate=0.4,
        sample_rate=SAMPLE_RATE,
        packet_ms=packet_ms,
        fill="zero",
        seed=17,
    )
    assert descriptor.measured["packet_samples"] == packet_samples

    dropped_mask = np.random.default_rng(17).random(100) < 0.4
    for index, dropped in enumerate(dropped_mask):
        window = damaged[index * packet_samples : (index + 1) * packet_samples]
        if dropped:
            assert np.all(window == 0.0)
        else:
            assert np.array_equal(
                window, audio[index * packet_samples : (index + 1) * packet_samples]
            )


def test_hold_fill_repeats_the_last_good_sample_instead_of_silence() -> None:
    audio = np.linspace(0.1, 1.0, 1000)  # strictly positive, so silence is obvious
    packet_samples = 10
    packet_ms = 1000.0 * packet_samples / SAMPLE_RATE
    damaged, _ = packet_loss(
        audio,
        loss_rate=0.4,
        sample_rate=SAMPLE_RATE,
        packet_ms=packet_ms,
        fill="hold",
        seed=17,
    )
    dropped_mask = np.random.default_rng(17).random(100) < 0.4
    dropped_after_the_start = [
        index for index, dropped in enumerate(dropped_mask) if dropped and index > 0
    ]
    assert dropped_after_the_start, "seed produced no usable dropped packet"
    for index in dropped_after_the_start:
        start = index * packet_samples
        window = damaged[start : start + packet_samples]
        assert np.all(window == damaged[start - 1])
        assert np.all(window > 0.0)  # held, not silenced


@pytest.mark.parametrize("loss_rate", [-0.1, 1.1])
def test_packet_loss_rejects_an_impossible_rate(loss_rate: float) -> None:
    with pytest.raises(ValueError, match="loss_rate must be in"):
        packet_loss(np.linspace(0.1, 1.0, 100), loss_rate=loss_rate)


def test_packet_loss_rejects_a_non_positive_packet_length() -> None:
    with pytest.raises(ValueError, match="packet_ms must be positive"):
        packet_loss(np.linspace(0.1, 1.0, 100), loss_rate=0.1, packet_ms=0.0)


# --------------------------------------------------------------------------- #
# Input hygiene
# --------------------------------------------------------------------------- #


def test_multichannel_audio_is_rejected_rather_than_guessed_at() -> None:
    with pytest.raises(ValueError, match="must be 1-D mono"):
        telephone_band(np.zeros((2, 1000)))


def test_empty_audio_is_rejected() -> None:
    with pytest.raises(ValueError, match="nothing to perturb"):
        telephone_band(np.array([]))


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_non_finite_input_is_rejected_before_it_can_propagate(bad: float) -> None:
    audio = np.linspace(0.1, 1.0, 100)
    audio[7] = bad
    with pytest.raises(ValueError, match="non-finite sample"):
        telephone_band(audio)


@pytest.mark.parametrize("sample_rate", [0, -16_000])
def test_non_positive_sample_rate_is_rejected(sample_rate: int) -> None:
    with pytest.raises(ValueError, match="sample_rate must be positive"):
        telephone_band(np.linspace(0.1, 1.0, 100), sample_rate=sample_rate)


def test_integer_input_is_coerced_to_float() -> None:
    """An adapter handing over int16-derived samples must not silently truncate."""
    filtered, _ = telephone_band(np.arange(1, 1001), sample_rate=SAMPLE_RATE)
    assert filtered.dtype == np.float64


# --------------------------------------------------------------------------- #
# Registry and chaining
# --------------------------------------------------------------------------- #


def test_every_registered_perturbation_returns_finite_mono_float_audio() -> None:
    audio = speechlike()
    assert set(PERTURBATIONS) == set(MINIMAL_PARAMS)
    for name, params in MINIMAL_PARAMS.items():
        result, descriptor = apply(name, audio, sample_rate=SAMPLE_RATE, **params)
        assert descriptor.name == name, name
        assert result.ndim == 1, name
        assert result.dtype == np.float64, name
        assert result.size > 0, name
        assert np.all(np.isfinite(result)), name
        assert descriptor.label(), name
        assert descriptor.describe(), name


def test_unknown_perturbation_name_lists_the_available_ones() -> None:
    with pytest.raises(KeyError, match="unknown perturbation"):
        apply("reverb", speechlike())
    try:
        apply("reverb", speechlike())
    except KeyError as error:
        assert "telephone_band" in str(error)


def test_apply_chain_records_every_step_in_order() -> None:
    audio = speechlike()
    result, descriptors = apply_chain(
        audio,
        [
            ("add_noise", {"snr_db": 15.0, "seed": 2}),
            ("telephone_band", {}),
            ("packet_loss", {"loss_rate": 0.02, "seed": 3}),
        ],
        sample_rate=SAMPLE_RATE,
    )
    assert [d.name for d in descriptors] == [
        "add_noise",
        "telephone_band",
        "packet_loss",
    ]
    assert result.size == audio.size
    assert np.all(np.isfinite(result))

    payload = perturbation_payload(descriptors)
    assert payload["perturbation_chain"].startswith("noise@15.0dB")
    assert " -> " in payload["perturbation_chain"]
    assert len(payload["perturbations"]) == 3
    # The payload is plain data, ready to go straight into a trace event.
    assert all(isinstance(entry, dict) for entry in payload["perturbations"])


def test_chain_order_changes_the_result() -> None:
    """These operations do not commute, which is why the order is recorded."""
    audio = speechlike()
    noise_then_band, _ = apply_chain(
        audio,
        [("add_noise", {"snr_db": 10.0, "seed": 9}), ("telephone_band", {})],
        sample_rate=SAMPLE_RATE,
    )
    band_then_noise, _ = apply_chain(
        audio,
        [("telephone_band", {}), ("add_noise", {"snr_db": 10.0, "seed": 9})],
        sample_rate=SAMPLE_RATE,
    )
    assert not np.allclose(noise_then_band, band_then_noise)


def test_an_empty_chain_still_records_that_the_run_was_clean() -> None:
    audio = speechlike()
    result, descriptors = apply_chain(audio, [], sample_rate=SAMPLE_RATE)
    assert np.array_equal(result, audio)
    assert descriptors == []
    payload = perturbation_payload(descriptors)
    assert payload == {"perturbations": [], "perturbation_chain": "clean"}


def test_a_length_changing_step_is_visible_to_the_rest_of_the_chain() -> None:
    audio = speechlike()
    result, descriptors = apply_chain(
        audio,
        [("resample_speed", {"factor": 2.0}), ("telephone_band", {})],
        sample_rate=SAMPLE_RATE,
    )
    assert result.size == audio.size // 2
    assert descriptors[1].measured["samples"] == result.size
