"""The transport tier, tested entirely offline against its committed recordings.

WHAT THIS DEMONSTRATES
----------------------
That a tier whose numbers came from a live network can still be regression-tested
by a machine with no credentials. Every figure the tier reports is recomputed here
from the committed ledgers — the same code path the report uses — so this file is
the thing that keeps the cardinal rule true: a fresh clone with every key unset
runs `pytest` and exercises the whole measurement path without dialling out.

Three groups of tests carry most of the weight, and they exist because each one
corresponds to a mistake that was actually made while building this tier:

1.  **Segmentation across a burst.** The first segmenter counted quiet *positions*
    in the arrival ledger. A live session delivered 24 frames inside 128 ms after
    a stall, which reads as a long silence if you are counting positions, so one
    utterance split into two runs and a good session was refused. The gap is now
    measured in time, and `test_a_delivery_burst_does_not_split_a_run` is that
    session in miniature.
2.  **Refusals.** Every measurement can decline, and a refusal path that is never
    executed is a comment. Each one is driven here.
3.  **The trace projection agrees with the ledgers.** The delivery gap is computed
    twice by different code — once from push/arrival ledgers, once as a pairing
    over trace events — and the two must agree to a nanosecond. That is what makes
    the trace a projection of the measurement rather than a second, drifting
    implementation of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.trace.io import read_jsonl
from lab.trace.schema import EventKind
from lab.voice.calibration import CalibrationReport, CalibrationTolerance, run_calibration
from lab.voice.transport.measure import (
    DEFAULT_MAX_GAP_S,
    DEFAULT_THRESHOLD_RMS,
    channel_stats,
    degradation_comparison,
    delivery_gap,
    jitter_stats,
    lifecycle_observation,
    speech_runs,
    threshold_sensitivity,
)
from lab.voice.transport.records import (
    ArrivalLedger,
    PushLedger,
    TransportEventRecord,
    TransportRecording,
    UtteranceRecord,
    url_digest,
)
from lab.voice.transport.report import FIXTURE_DIR, build_report, ladder_side
from lab.voice.transport.rows import (
    TRANSPORT_CATEGORIES,
    TRANSPORT_TAG_VOCABULARY,
    TransportRow,
    coverage,
    evaluate_degradation,
    evaluate_delivery_gap,
    evaluate_lifecycle,
    load_rows,
)
from lab.voice.transport.trace import trace_from_recording

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_ENV_VARS = (
    "LAB_LIVE_TRANSPORT",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def gate() -> CalibrationReport:
    """The timing gate. Offline, deterministic, milliseconds — so it runs for real."""
    report = run_calibration()
    assert report.passed, "the timing gate must pass before any latency test below means anything"
    return report


@pytest.fixture(scope="module")
def rows() -> list[TransportRow]:
    return load_rows()


@pytest.fixture(scope="module")
def delivery_recording() -> TransportRecording:
    return TransportRecording.read(FIXTURE_DIR / "delivery-gap.json")


@pytest.fixture(scope="module")
def degradation_recording() -> TransportRecording:
    return TransportRecording.read(FIXTURE_DIR / "degradation.json")


@pytest.fixture(scope="module")
def lifecycle_recording() -> TransportRecording:
    return TransportRecording.read(FIXTURE_DIR / "lifecycle.json")


def _ledger(pattern: str, *, step: float = 0.01) -> ArrivalLedger:
    """A ledger from a string: `#` is speech, `.` is quiet. One character per frame."""
    return ArrivalLedger(
        ts_s=[index * step for index in range(len(pattern))],
        rms=[0.2 if char == "#" else 0.0 for char in pattern],
        frame_samples=int(round(step * 16_000)),
        sample_rate=16_000,
    )


# --------------------------------------------------------------------------- #
# The rows themselves
# --------------------------------------------------------------------------- #


def test_the_tier_is_exactly_three_rows(rows: list[TransportRow]) -> None:
    """Three things only exist in transport, so three rows. Not a soft target."""
    assert len(rows) == 3
    assert {row.category for row in rows} == set(TRANSPORT_CATEGORIES)


def test_every_row_carries_one_category_and_its_own_assertions(
    rows: list[TransportRow],
) -> None:
    for row in rows:
        blocks = {
            "delivery-gap": row.delivery_gap,
            "transport-degradation": row.degradation,
            "connection-lifecycle": row.lifecycle,
        }
        assert blocks.pop(row.category) is not None, f"{row.id} asserts nothing"
        assert all(block is None for block in blocks.values()), (
            f"{row.id} declares another category's assertions"
        )


def test_every_row_justifies_needing_a_real_network(rows: list[TransportRow]) -> None:
    """The tier's admission rule. A row that cannot say why belongs in process.

    Asserted as length and distinctness, not as a keyword: a test that greps for
    the phrase "in process" is a test that rewards keyword stuffing in the one
    field whose whole value is that somebody thought about it. Distinctness is the
    property worth machine-checking — three rows must have three different
    reasons, or one of them is riding on another's justification.
    """
    justifications = {row.id: row.why_transport for row in rows}
    for row_id, text in justifications.items():
        assert len(text.split()) >= 20, f"{row_id}: why_transport is too short to be an argument"
    assert len(set(justifications.values())) == len(justifications), (
        "two rows share a justification, so one of them has not made its own case"
    )


def test_the_transport_vocabulary_is_disjoint_from_the_audio_tier(
    rows: list[TransportRow],
) -> None:
    """If `webrtc` were legal on an in-process row, that row could claim this tier's
    coverage. One-directional separation, same argument as the audio tier's own
    vocabulary makes against the text suites."""
    loader = pytest.importorskip("scenarios.loader")
    # This test has already earned its place: `control-arm` was defined in both
    # dictionaries with different meanings, and this is what caught it.
    overlap = set(TRANSPORT_TAG_VOCABULARY) & set(loader.AUDIO_TAG_VOCABULARY)
    assert not overlap, f"tag(s) {sorted(overlap)} mean two things in two vocabularies"
    overlap_categories = set(TRANSPORT_CATEGORIES) & set(loader.AUDIO_TAG_VOCABULARY)
    assert not overlap_categories


def test_every_transport_tag_is_used_by_a_row(rows: list[TransportRow]) -> None:
    """An aspirational tag makes a coverage table read better than the tier is."""
    counts = coverage(rows)["tags"]
    unused = sorted(tag for tag, count in counts.items() if count == 0)
    assert not unused, f"tag(s) defined but unused: {unused}"


def test_a_row_declaring_the_wrong_assertion_block_is_rejected() -> None:
    body = {
        "id": "audio-transport-mismatched",
        "title": "A row whose assertions belong to another category",
        "category": "delivery-gap",
        "tags": ["webrtc"],
        "measures": "something that is at least thirty characters long to pass the field",
        "why_transport": (
            "an in process adapter cannot do this because there is nothing between the "
            "agent and the listener except a function call, which is the whole point"
        ),
        "duration_cap_s": 30,
        "fixture": "nowhere.json",
        "notes": "a deliberately invalid row",
        "lifecycle": {"expected_verdict": "recovered-turn-lost"},
    }
    with pytest.raises(ValueError, match="must declare its own assertion block"):
        TransportRow.model_validate(body)


def test_an_unknown_tag_is_rejected_with_the_legal_values() -> None:
    body = {
        "id": "audio-transport-typo",
        "title": "A row with a tag typo",
        "category": "delivery-gap",
        "tags": ["webrtcc"],
        "measures": "something that is at least thirty characters long to pass the field",
        "why_transport": (
            "an in process adapter cannot do this because there is nothing between the "
            "agent and the listener except a function call, which is the whole point"
        ),
        "duration_cap_s": 30,
        "fixture": "nowhere.json",
        "notes": "a deliberately invalid row",
        "delivery_gap": {},
    }
    with pytest.raises(ValueError, match="vocabulary is closed"):
        TransportRow.model_validate(body)


def test_a_row_id_must_name_its_tier(rows: list[TransportRow]) -> None:
    for row in rows:
        assert row.id.startswith("audio-transport-")
    body = json.loads(json.dumps(rows[0].model_dump(mode="json")))
    body["id"] = "audio-something-else"
    with pytest.raises(ValueError, match="must start with 'audio-transport-'"):
        TransportRow.model_validate(body)


def test_row_files_are_named_after_their_ids(rows: list[TransportRow]) -> None:
    """A failing row has to name its own file without a lookup."""
    for row in rows:
        assert (REPO_ROOT / "scenarios" / "audio" / "transport" / f"{row.id}.yaml").exists()


def test_transport_rows_are_invisible_to_the_scenario_corpus() -> None:
    """They live under `scenarios/audio/transport/` and are NOT `Scenario`s.

    The corpus loader globs one level, so the subdirectory is not parsed as
    conversational rows — which it would fail, having no contracts. This test
    pins that, because the day the loader recurses, three files that cannot be
    `Scenario`s would start breaking a corpus that has nothing to do with them.
    """
    loader = pytest.importorskip("scenarios.loader")
    paths = list(loader.iter_scenario_paths(suites=(loader.AUDIO_TIER,)))
    assert paths, "the audio tier should have rows of its own"
    assert not any("transport" in path.parts for path in paths)


# --------------------------------------------------------------------------- #
# Records: the evidence format
# --------------------------------------------------------------------------- #


def test_a_url_digest_is_not_the_url() -> None:
    """The deployment is identifiable across runs and not disclosed."""
    url = "wss://example-project.livekit.cloud"
    digest = url_digest(url)
    assert len(digest) == 12
    assert digest == url_digest(url)
    assert digest != url_digest(url + "x")
    assert "livekit" not in digest and "example" not in digest


def test_committed_recordings_carry_no_credential_and_no_url(
    delivery_recording: TransportRecording,
) -> None:
    """The fixture is committed to a public repository. Check, do not assume."""
    for name in ("delivery-gap.json", "degradation.json", "lifecycle.json"):
        text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
        assert "wss://" not in text and "ws://" not in text
        assert "livekit.cloud" not in text
        assert "API_KEY" not in text and "secret" not in text.lower()
    assert len(delivery_recording.url_digest) == 12


def test_a_push_ledger_must_have_one_reading_per_frame() -> None:
    with pytest.raises(ValueError, match="one timestamp, one energy reading"):
        PushLedger(ts_s=[0.0, 0.1], rms=[0.2], source_index=[0, 1])
    with pytest.raises(ValueError, match="queued_s must be empty or carry one"):
        PushLedger(ts_s=[0.0], rms=[0.2], source_index=[0], queued_s=[0.0, 0.0])


def test_an_arrival_ledger_must_have_one_energy_per_timestamp() -> None:
    with pytest.raises(ValueError, match="one energy reading per timestamp"):
        ArrivalLedger(ts_s=[0.0, 0.01], rms=[0.0])


def test_lifecycle_records_must_be_in_observation_order() -> None:
    """Consumers slice this list positionally, so append order has to be real."""
    with pytest.raises(ValueError, match="observation order"):
        TransportRecording(
            row="r",
            room="rm",
            url_digest="a" * 12,
            recorded_at="now",
            lifecycle=[
                TransportEventRecord(
                    ts_s=1.0, kind="connected", participant="a", observer="publisher"
                ),
                TransportEventRecord(
                    ts_s=0.5, kind="disconnected", participant="a", observer="publisher"
                ),
            ],
        )


def test_a_loss_rate_with_no_denominator_is_none_not_zero() -> None:
    """A naked rate is a defect in this repo; an empty utterance has no rate."""
    empty = UtteranceRecord(
        turn=1,
        clip="c.opus",
        frame_ms=20.0,
        sample_rate=16_000,
        pushes=PushLedger(),
    )
    assert empty.offered_frames == 0
    assert empty.nominal_loss is None

    loaded = UtteranceRecord(
        turn=1,
        clip="c.opus",
        frame_ms=20.0,
        sample_rate=16_000,
        pushes=PushLedger(ts_s=[0.0, 0.02, 0.04], rms=[0.2] * 3, source_index=[0, 1, 3]),
        withheld_source_index=[2],
    )
    assert loaded.offered_frames == 4
    assert loaded.nominal_loss == pytest.approx(0.25)


def test_a_recording_round_trips_through_disk(tmp_path: Path) -> None:
    original = TransportRecording(
        row="audio-transport-delivery-gap",
        room="rm-1",
        url_digest="b" * 12,
        recorded_at="2026-08-23T00:00:00+00:00",
        utterances=[
            UtteranceRecord(
                turn=1,
                clip="c.opus",
                frame_ms=20.0,
                sample_rate=16_000,
                pushes=PushLedger(
                    ts_s=[0.123456789], rms=[0.25], source_index=[0], queued_s=[0.004]
                ),
            )
        ],
        arrivals=_ledger("..###.."),
    )
    path = original.write(tmp_path / "r.json")
    reloaded = TransportRecording.read(path)
    # Rounded to microseconds on write, deliberately, so a diff stays readable.
    assert reloaded.utterances[0].pushes.ts_s[0] == pytest.approx(0.123457, abs=1e-9)
    assert reloaded.arrivals.rms == original.arrivals.rms
    assert reloaded.row == original.row


# --------------------------------------------------------------------------- #
# Segmentation — the primitive everything else is built on
# --------------------------------------------------------------------------- #


def test_runs_are_found_by_position_and_bounded_by_quiet() -> None:
    ledger = _ledger("...####...........................####...")
    runs = speech_runs(ledger, threshold_rms=0.1, min_frames=3, max_gap_s=0.05)
    assert len(runs) == 2
    assert (runs[0].start_index, runs[0].end_index) == (3, 6)
    assert runs[0].onset_s == pytest.approx(0.03)


def test_a_short_blip_is_not_an_utterance() -> None:
    """One concealment artefact must not invent a turn and break the pairing."""
    ledger = _ledger("...#...........................######...")
    runs = speech_runs(ledger, threshold_rms=0.1, min_frames=3, max_gap_s=0.05)
    assert len(runs) == 1
    assert runs[0].frames == 6


def test_a_delivery_burst_does_not_split_a_run() -> None:
    """The regression test for the bug a second live session found.

    24 frames arriving inside 128 ms is a jitter-buffer burst after a stall. A
    segmenter counting quiet *positions* reads it as a long silence and splits one
    utterance in two; the pairing then refuses a perfectly good session. Measured
    in time, the same frames are one run.
    """
    # Ten loud frames at 10 ms, then a stall, then a burst of 24 quiet-then-loud
    # frames compressed into 128 ms — the shape the live ledger had.
    ts = [index * 0.01 for index in range(10)]
    rms = [0.2] * 10
    burst_start = ts[-1]
    for index in range(24):
        ts.append(burst_start + 0.128 * (index + 1) / 24)
        rms.append(0.0 if index < 12 else 0.2)
    ledger = ArrivalLedger(ts_s=ts, rms=rms, frame_samples=160, sample_rate=16_000)

    by_time = speech_runs(ledger, threshold_rms=0.1, min_frames=3, max_gap_s=0.2)
    assert len(by_time) == 1, "128 ms of quiet is inside the 200 ms tolerance"

    # And the same ledger under a tolerance shorter than the real gap does split,
    # so the test above is not passing for lack of a gap to find.
    by_shorter = speech_runs(ledger, threshold_rms=0.1, min_frames=3, max_gap_s=0.02)
    assert len(by_shorter) == 2


def test_the_default_gap_tolerance_sits_between_the_two_constraints() -> None:
    """Shorter than the silence between utterances, longer than a pause inside one."""
    assert 0.06 < DEFAULT_MAX_GAP_S < 0.4


def test_a_negative_gap_tolerance_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        speech_runs(_ledger("###"), max_gap_s=-1.0)


# --------------------------------------------------------------------------- #
# Row 1 — the delivery gap
# --------------------------------------------------------------------------- #


def test_the_delivery_gap_is_measurable_and_far_above_the_agent_side_figure(
    delivery_recording: TransportRecording, gate: CalibrationReport
) -> None:
    measurement = delivery_gap(delivery_recording, calibration=gate)
    assert measurement.reportable, measurement.refusal
    assert measurement.distribution is not None
    assert measurement.distribution.n == 12
    mean_ms = measurement.mean_ms
    assert mean_ms is not None
    # A band, not a point. This came off a live network and a second session of
    # the same row landed 48 ms away; what the tier claims is an order of
    # magnitude, and the assertion says exactly that much.
    assert 20.0 < mean_ms < 400.0
    assert measurement.agent_side_figure_s == 0.0
    assert all(sample.gap_s > 0 for sample in measurement.samples)


def test_p95_is_refused_at_twelve_samples(
    delivery_recording: TransportRecording, gate: CalibrationReport
) -> None:
    """A real-time room cannot be sped up, so the tail is honestly out of reach."""
    measurement = delivery_gap(delivery_recording, calibration=gate)
    assert measurement.distribution is not None
    assert measurement.distribution.quantile(0.50).reported
    assert measurement.distribution.quantile(0.90).reported
    assert not measurement.distribution.quantile(0.95).reported
    assert measurement.distribution.quantile(0.95).min_n == 20


def test_the_local_send_queue_is_separated_from_the_transport(
    delivery_recording: TransportRecording, gate: CalibrationReport
) -> None:
    """The harness's own buffering is measured, not assumed away."""
    measurement = delivery_gap(delivery_recording, calibration=gate)
    assert measurement.net_distribution is not None
    assert measurement.queue_correlation is not None
    raw_mean = measurement.mean_ms
    net_mean = measurement.net_mean_ms
    assert raw_mean is not None and net_mean is not None
    assert net_mean <= raw_mean, "subtracting our own queue cannot increase the gap"
    for sample in measurement.samples:
        assert sample.queue_at_onset_s is not None
        assert sample.net_gap_s == pytest.approx(sample.gap_s - sample.queue_at_onset_s)


def test_no_latency_figure_without_a_passing_gate(
    delivery_recording: TransportRecording,
) -> None:
    """The refusal is the point: an uncalibrated stopwatch has no meaning."""
    absent = delivery_gap(delivery_recording, calibration=None)
    assert not absent.reportable
    assert absent.calibration_verdict == "ABSENT"
    assert "calibration" in (absent.refusal or "")
    assert absent.distribution is None

    # An impossible tolerance makes the real gate fail, so the failing branch runs
    # against a genuine report rather than a hand-built one.
    failing = run_calibration(tolerance=CalibrationTolerance(max_rel_error=1e-9))
    assert failing.verdict == "FAIL"
    refused = delivery_gap(delivery_recording, calibration=failing)
    assert not refused.reportable
    assert refused.calibration_verdict == "FAIL"
    assert "FAIL" in (refused.refusal or "")


def test_a_pairing_mismatch_is_refused_rather_than_guessed(
    gate: CalibrationReport,
) -> None:
    """Two utterances, one delivered run: the missing turn is the finding."""
    recording = TransportRecording(
        row="audio-transport-delivery-gap",
        room="rm",
        url_digest="c" * 12,
        recorded_at="now",
        utterances=[
            UtteranceRecord(
                turn=turn,
                clip="c.opus",
                frame_ms=10.0,
                sample_rate=16_000,
                pushes=PushLedger(
                    ts_s=[turn * 1.0, turn * 1.0 + 0.01],
                    rms=[0.0, 0.3],
                    source_index=[0, 1],
                ),
            )
            for turn in (1, 2)
        ],
        arrivals=_ledger("....####...."),
    )
    measurement = delivery_gap(recording, calibration=gate)
    assert not measurement.reportable
    assert "2 utterance(s) were pushed but 1 speech run(s) arrived" in (
        measurement.refusal or ""
    )


def test_audio_arriving_before_it_was_sent_is_refused(gate: CalibrationReport) -> None:
    """A negative gap is a mis-pairing or a broken clock, never a fast network."""
    recording = TransportRecording(
        row="audio-transport-delivery-gap",
        room="rm",
        url_digest="d" * 12,
        recorded_at="now",
        utterances=[
            UtteranceRecord(
                turn=1,
                clip="c.opus",
                frame_ms=10.0,
                sample_rate=16_000,
                pushes=PushLedger(ts_s=[5.0], rms=[0.3], source_index=[0]),
            )
        ],
        arrivals=_ledger("...###..."),  # arrives around 0.03 s, long before 5.0 s
    )
    measurement = delivery_gap(recording, calibration=gate)
    assert not measurement.reportable
    assert "before it was sent" in (measurement.refusal or "")


def test_an_utterance_with_no_audible_onset_is_refused(gate: CalibrationReport) -> None:
    recording = TransportRecording(
        row="audio-transport-delivery-gap",
        room="rm",
        url_digest="e" * 12,
        recorded_at="now",
        utterances=[
            UtteranceRecord(
                turn=1,
                clip="c.opus",
                frame_ms=10.0,
                sample_rate=16_000,
                pushes=PushLedger(ts_s=[0.0], rms=[0.0], source_index=[0]),
            )
        ],
        arrivals=_ledger("...###..."),
    )
    measurement = delivery_gap(recording, calibration=gate)
    assert not measurement.reportable
    assert "no frame above" in (measurement.refusal or "")


def test_the_gap_does_not_depend_on_where_speech_is_said_to_start(
    delivery_recording: TransportRecording, gate: CalibrationReport
) -> None:
    """The one judgement call in the measurement, swept over a 16x range."""
    sweep = threshold_sensitivity(delivery_recording, calibration=gate)
    means = [
        result.mean_ms
        for _, result in sweep
        if result.reportable and result.mean_ms is not None
    ]
    assert len(means) == len(sweep), "every threshold in the sweep should report"
    # Same order of magnitude across the sweep, and never anywhere near zero —
    # which is the comparison the row exists to make.
    assert max(means) / min(means) < 2.0
    assert min(means) > 10.0


def test_the_row_verdict_reads_the_measurement(
    rows: list[TransportRow], delivery_recording: TransportRecording, gate: CalibrationReport
) -> None:
    row = next(r for r in rows if r.category == "delivery-gap")
    outcome = evaluate_delivery_gap(row, delivery_gap(delivery_recording, calibration=gate))
    assert outcome.verdict == "PASS", outcome.reasons
    assert outcome.findings

    assert evaluate_delivery_gap(row, None).verdict == "NOT-RUN"
    refused = evaluate_delivery_gap(
        row, delivery_gap(delivery_recording, calibration=None)
    )
    assert refused.verdict == "FAIL"


def test_the_scatter_ceiling_has_actually_caught_a_session(
    rows: list[TransportRow], gate: CalibrationReport
) -> None:
    """An assertion no recorded session has ever tripped is untested.

    The second committed session of this row has a mid-call stall: a mean that
    looks usable and a per-turn spread that is not. It is kept, and it fails.
    """
    second = FIXTURE_DIR / "delivery-gap-second-session.json"
    if not second.exists():  # pragma: no cover - the fixture is committed
        pytest.skip("the second session recording is not committed")
    row = next(r for r in rows if r.category == "delivery-gap")
    measurement = delivery_gap(TransportRecording.read(second), calibration=gate)
    assert measurement.reportable, measurement.refusal
    outcome = evaluate_delivery_gap(row, measurement)
    assert outcome.verdict == "FAIL"
    assert any("scatter" in reason for reason in outcome.reasons)


def test_the_median_reproduces_across_sessions_and_the_mean_does_not(
    gate: CalibrationReport,
) -> None:
    """Which statistic this row should be quoted by, asserted rather than asserted-in-prose.

    Two live sessions of the same row, both committed. Their means differ by tens
    of milliseconds because one contained a stall; their medians differ by well
    under a frame. So the typical delivery gap is reproducible and the mean is
    not, which is why the report quotes the median and reads the spread as the
    risk. If a future pair of recordings breaks this, the claim in
    docs/AUDIO_TRANSPORT.md is wrong and this test says so.
    """
    second = FIXTURE_DIR / "delivery-gap-second-session.json"
    if not second.exists():  # pragma: no cover - the fixture is committed
        pytest.skip("the second session recording is not committed")
    first = delivery_gap(
        TransportRecording.read(FIXTURE_DIR / "delivery-gap.json"), calibration=gate
    )
    other = delivery_gap(TransportRecording.read(second), calibration=gate)
    assert first.distribution is not None and other.distribution is not None

    medians = [
        distribution.quantile(0.50).value_s
        for distribution in (first.distribution, other.distribution)
    ]
    assert all(value is not None for value in medians)
    median_spread_ms = abs(medians[0] - medians[1]) * 1000.0  # type: ignore[operator]
    mean_spread_ms = abs((first.mean_ms or 0.0) - (other.mean_ms or 0.0))

    assert median_spread_ms < 10.0, "the typical gap should reproduce across sessions"
    assert mean_spread_ms > median_spread_ms, (
        "the mean is the statistic a stall moves; if it were the stabler of the two, "
        "the report is quoting the wrong one"
    )
    # And both sessions still make the row's point, on either statistic.
    for value in (*medians, first.mean_ms, other.mean_ms):
        assert value is not None
    assert min(medians) * 1000.0 > 10.0  # type: ignore[operator]


# --------------------------------------------------------------------------- #
# Row 2 — degradation
# --------------------------------------------------------------------------- #


def test_the_file_ladder_and_the_transport_do_not_agree(
    degradation_recording: TransportRecording,
) -> None:
    """The tier's finding about an instrument the rest of the voice suite uses.

    Both of `packet_loss`'s fill modes are compared, normalised for each channel's
    own silence floor and for the loss each actually applied. They bracket reality
    rather than matching it: `zero` is harsher than a real transport, `hold` is
    gentler, and neither is within tolerance.
    """
    pytest.importorskip("numpy")
    ratios: dict[str, float] = {}
    for fill in ("zero", "hold"):
        perturbed, baseline, realised, refusal = ladder_side(
            degradation_recording, fill=fill
        )
        assert refusal is None, refusal
        comparison = degradation_comparison(
            degradation_recording,
            file_rms=perturbed,
            file_baseline_rms=baseline,
            file_realised_loss=realised,
            fill=fill,
        )
        assert comparison.reportable, comparison.refusal
        assert comparison.control is not None, "the control arm is what makes it readable"
        assert comparison.ratio is not None
        ratios[fill] = comparison.ratio
        assert comparison.agree is False

    assert ratios["zero"] > 1.0, "zero-fill should be harsher than a real transport"
    assert ratios["hold"] < 1.0, "hold-fill should be gentler than a real transport"


def test_the_comparison_is_refused_without_a_baseline_or_a_realised_dose(
    degradation_recording: TransportRecording,
) -> None:
    """Skip either correction and the row reaches the opposite conclusion."""
    pytest.importorskip("numpy")
    perturbed, baseline, realised, _ = ladder_side(degradation_recording, fill="hold")
    no_baseline = degradation_comparison(
        degradation_recording,
        file_rms=perturbed,
        file_baseline_rms=None,
        file_realised_loss=realised,
        fill="hold",
    )
    assert not no_baseline.reportable
    assert "baseline" in (no_baseline.refusal or "")

    no_dose = degradation_comparison(
        degradation_recording,
        file_rms=perturbed,
        file_baseline_rms=baseline,
        file_realised_loss=None,
        fill="hold",
    )
    assert not no_dose.reportable


def test_a_recording_with_no_withheld_frames_has_nothing_to_compare(
    delivery_recording: TransportRecording,
) -> None:
    comparison = degradation_comparison(delivery_recording, file_rms=None)
    assert not comparison.reportable
    assert "withheld" in (comparison.refusal or "")


def test_jitter_is_measured_and_the_file_ladder_has_no_equivalent(
    degradation_recording: TransportRecording,
) -> None:
    stats = jitter_stats(degradation_recording.arrivals)
    assert stats.frames > 100
    assert stats.gaps == stats.frames - 1
    assert stats.nominal_frame_ms == pytest.approx(10.0)
    assert stats.mean_abs_deviation_ms is not None
    assert stats.max_gap_ms is not None and stats.max_gap_ms > 0
    # Carries its denominator, never a naked percentage.
    assert f"/{stats.gaps}" in stats.late_rate


def test_jitter_on_a_single_frame_reports_no_intervals() -> None:
    """No intervals observed and intervals of zero are different states."""
    stats = jitter_stats(ArrivalLedger(ts_s=[0.0], rms=[0.1]))
    assert stats.gaps == 0
    assert stats.mean_gap_ms is None
    assert stats.late_rate.startswith("0/0")


def test_channel_stats_refuse_a_rate_with_no_denominator() -> None:
    empty = channel_stats([], source="transport", frame_s=0.01)
    assert empty.frames == 0
    assert empty.silent_fraction is None
    assert empty.silent_rate.startswith("0/0")


def test_the_degradation_row_verdict_reads_the_comparison(
    rows: list[TransportRow], degradation_recording: TransportRecording
) -> None:
    pytest.importorskip("numpy")
    row = next(r for r in rows if r.category == "transport-degradation")
    perturbed, baseline, realised, _ = ladder_side(degradation_recording, fill="zero")
    comparison = degradation_comparison(
        degradation_recording,
        file_rms=perturbed,
        file_baseline_rms=baseline,
        file_realised_loss=realised,
        fill="zero",
    )
    outcome = evaluate_degradation(row, comparison)
    assert outcome.verdict == "PASS", outcome.reasons
    # The agreement verdict is a finding, not a grade: the row passes while
    # reporting that the two instruments disagree.
    assert comparison.agree is False
    assert any("do NOT agree" in finding for finding in outcome.findings)
    assert evaluate_degradation(row, None).verdict == "NOT-RUN"


# --------------------------------------------------------------------------- #
# Row 3 — connection lifecycle
# --------------------------------------------------------------------------- #


def test_the_connection_recovers_and_the_turn_does_not(
    lifecycle_recording: TransportRecording,
) -> None:
    observation = lifecycle_observation(lifecycle_recording, settle_s=0.6)
    assert observation.reportable, observation.refusal
    assert observation.verdict == "recovered-turn-lost"
    assert observation.attempts == 2
    assert observation.runs_after_reconnect >= 1
    # The interrupted turn: pushed part way, and the remainder never sent.
    assert observation.frames_pushed_before_drop == 40
    assert observation.frames_offered_before_drop > observation.frames_pushed_before_drop


def test_the_in_flight_turn_is_counted_as_straddling_the_drop(
    lifecycle_recording: TransportRecording,
) -> None:
    """With only 'before' and 'after' buckets it vanishes, and the row reports
    that nothing was interrupted."""
    observation = lifecycle_observation(lifecycle_recording, settle_s=0.6)
    assert observation.runs_straddling_drop == 1
    assert observation.frames_after_drop_in_flight > 0, (
        "audio already in the jitter buffer outlives the connection that filled it"
    )


def test_the_listener_heard_a_longer_hole_than_the_transport_figure_admits(
    lifecycle_recording: TransportRecording,
) -> None:
    observation = lifecycle_observation(lifecycle_recording, settle_s=0.6)
    recovery = observation.transport_recovery_s
    heard = observation.audio_silence_s
    assert recovery is not None and heard is not None
    assert heard > recovery, (
        "the transport-level recovery figure understates the hole in the conversation"
    )
    assert observation.settle_s == pytest.approx(0.6)
    assert observation.downtime_s is not None and observation.downtime_s < recovery


def test_a_session_that_never_dropped_cannot_report_a_recovery(
    delivery_recording: TransportRecording,
) -> None:
    observation = lifecycle_observation(delivery_recording)
    assert not observation.reportable
    assert "no disconnect" in (observation.refusal or "")


def test_a_drop_without_a_stream_position_is_refused() -> None:
    """Ordering audio against a lifecycle event needs the position, not two clocks."""
    recording = TransportRecording(
        row="audio-transport-lifecycle",
        room="rm",
        url_digest="f" * 12,
        recorded_at="now",
        arrivals=_ledger("###....###"),
        lifecycle=[
            TransportEventRecord(
                ts_s=0.05, kind="disconnected", participant="agent", observer="publisher"
            )
        ],
    )
    observation = lifecycle_observation(recording)
    assert not observation.reportable
    assert "arrival_index" in (observation.refusal or "")


def test_a_reconnect_with_no_audio_afterwards_is_a_hang() -> None:
    """The failure mode a reconnect metric misses: healthy session, dead call."""
    recording = TransportRecording(
        row="audio-transport-lifecycle",
        room="rm",
        url_digest="0" * 12,
        recorded_at="now",
        arrivals=_ledger("###......."),
        lifecycle=[
            TransportEventRecord(
                ts_s=0.04,
                kind="disconnected",
                participant="agent",
                observer="publisher",
                arrival_index=5,
            ),
            TransportEventRecord(
                ts_s=0.06,
                kind="connected",
                participant="agent",
                observer="publisher",
                attempt=2,
                arrival_index=7,
            ),
        ],
    )
    observation = lifecycle_observation(recording, threshold_rms=0.1)
    assert observation.reportable
    assert observation.verdict == "hung"
    assert any("healthy" in finding for finding in observation.findings)


def test_no_reconnect_at_all_is_its_own_verdict() -> None:
    recording = TransportRecording(
        row="audio-transport-lifecycle",
        room="rm",
        url_digest="1" * 12,
        recorded_at="now",
        arrivals=_ledger("###......."),
        lifecycle=[
            TransportEventRecord(
                ts_s=0.04,
                kind="disconnected",
                participant="agent",
                observer="publisher",
                arrival_index=5,
            )
        ],
    )
    observation = lifecycle_observation(recording, threshold_rms=0.1)
    assert observation.verdict == "no-recovery"


def test_the_lifecycle_row_pins_the_behaviour_it_observed(
    rows: list[TransportRow], lifecycle_recording: TransportRecording
) -> None:
    row = next(r for r in rows if r.category == "connection-lifecycle")
    assert row.lifecycle is not None
    assert row.lifecycle.expected_verdict == "recovered-turn-lost"
    outcome = evaluate_lifecycle(
        row, lifecycle_observation(lifecycle_recording, settle_s=0.6)
    )
    assert outcome.verdict == "PASS", outcome.reasons

    # And the pin has teeth: a different verdict fails the row rather than
    # passing quietly.
    changed = lifecycle_observation(lifecycle_recording, settle_s=0.6)
    changed.verdict = "recovered-turn-intact"
    assert evaluate_lifecycle(row, changed).verdict == "FAIL"


# --------------------------------------------------------------------------- #
# The trace projection
# --------------------------------------------------------------------------- #


def test_the_trace_pairing_agrees_with_the_ledger_measurement(
    delivery_recording: TransportRecording, gate: CalibrationReport
) -> None:
    """Two routes to the same number, and they must not drift apart.

    `measure.delivery_gap` computes from push and arrival ledgers.
    `trace.event_pairs` computes from an event stream, the way every other latency
    figure in this repo is computed. Agreement to a nanosecond is what makes the
    trace a projection rather than a rival implementation.
    """
    measurement = delivery_gap(delivery_recording, calibration=gate)
    trace = trace_from_recording(delivery_recording)
    pairs = trace.event_pairs(
        EventKind.AGENT_AUDIO_FIRST_BYTE, EventKind.AUDIO_DELIVERED
    )
    assert len(pairs) == len(measurement.samples)
    for (agent, delivered), sample in zip(pairs, measurement.samples, strict=True):
        assert delivered.ts - agent.ts == pytest.approx(sample.gap_s, abs=1e-9)


def test_the_projected_trace_is_ordered_and_uses_known_kinds(
    lifecycle_recording: TransportRecording,
) -> None:
    trace = trace_from_recording(lifecycle_recording)
    assert trace.is_ordered()
    assert not trace.unknown_kinds()
    kinds = {event.kind for event in trace}
    assert EventKind.AUDIO_DELIVERED in kinds
    assert EventKind.TRANSPORT_CONNECTED in kinds
    assert EventKind.TRANSPORT_DISCONNECTED in kinds


def test_the_projected_trace_invents_no_conversation(
    delivery_recording: TransportRecording,
) -> None:
    """This tier has no caller, no recogniser and no model, so the trace says so.

    A trace carrying invented `caller_utterance` or `transcript_in` events would
    let a conversational check run against a session where no conversation
    happened.
    """
    trace = trace_from_recording(delivery_recording)
    kinds = {event.kind for event in trace}
    for absent in (
        EventKind.CALLER_UTTERANCE,
        EventKind.AGENT_UTTERANCE,
        EventKind.TRANSCRIPT_IN,
        EventKind.TRANSCRIPT_OUT,
        EventKind.TOOL_CALL,
    ):
        assert absent not in kinds
    assert not trace.tool_names()
    assert not trace.utterances()


def test_a_lost_turn_stays_unpaired_in_the_trace() -> None:
    """`event_pairs` drops an opener with no closer, so a lost turn stays lost."""
    recording = TransportRecording(
        row="audio-transport-delivery-gap",
        room="rm",
        url_digest="2" * 12,
        recorded_at="now",
        utterances=[
            UtteranceRecord(
                turn=1,
                clip="c.opus",
                frame_ms=10.0,
                sample_rate=16_000,
                pushes=PushLedger(ts_s=[0.00], rms=[0.3], source_index=[0]),
            ),
            UtteranceRecord(
                turn=2,
                clip="c.opus",
                frame_ms=10.0,
                sample_rate=16_000,
                pushes=PushLedger(ts_s=[1.00], rms=[0.3], source_index=[0]),
            ),
        ],
        # Only the second turn arrives.
        arrivals=ArrivalLedger(
            ts_s=[1.05, 1.06, 1.07, 1.08],
            rms=[0.3, 0.3, 0.3, 0.3],
            frame_samples=160,
        ),
    )
    trace = trace_from_recording(recording, threshold_rms=0.1)
    pairs = trace.event_pairs(
        EventKind.AGENT_AUDIO_FIRST_BYTE, EventKind.AUDIO_DELIVERED
    )
    assert len(pairs) == 1
    assert pairs[0][0].get("turn") == 2


def test_the_committed_traces_match_the_recordings_they_came_from() -> None:
    """The committed JSONL is the projection of the committed recording, not a
    snapshot that can drift from it."""
    for name in ("delivery-gap", "degradation", "lifecycle"):
        recording = TransportRecording.read(FIXTURE_DIR / f"{name}.json")
        on_disk = read_jsonl(FIXTURE_DIR / "traces" / f"{name}.jsonl")
        recomputed = trace_from_recording(recording)
        assert [event.kind for event in on_disk] == [
            event.kind for event in recomputed
        ], f"{name}: the committed trace no longer matches the recording"
        for stored, fresh in zip(on_disk, recomputed, strict=True):
            assert stored.ts == pytest.approx(fresh.ts, abs=1e-6)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def test_the_report_builds_offline_with_no_credentials(monkeypatch) -> None:
    for name in LIVE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    report = build_report()
    assert report.calibration.passed
    assert report.calibration.control_verdict == "FAIL", (
        "the naive whole-turn control is expected to fail; that is what it is for"
    )
    assert len(report.rows) == 3
    assert report.verdict == "PASS", [
        (row.outcome.row_id, row.outcome.reasons) for row in report.rows
    ]
    markdown = report.to_markdown()
    assert "Non-gating in CI by design" in markdown
    assert "wss://" not in markdown


def test_the_report_withholds_every_latency_figure_when_the_gate_fails() -> None:
    """Not a caveated number. No number."""
    failing = run_calibration(tolerance=CalibrationTolerance(max_rel_error=1e-9))
    assert failing.verdict == "FAIL"
    report = build_report(calibration=failing)
    markdown = report.to_markdown()
    assert "it is a refusal" in markdown
    delivery = next(row for row in report.rows if row.row.category == "delivery-gap")
    assert delivery.delivery is not None
    assert not delivery.delivery.reportable
    assert delivery.outcome.verdict == "FAIL"


def test_the_report_prints_no_naked_percentage_of_three_rows() -> None:
    """Three rows make every percentage a rounding of one third."""
    markdown = build_report().to_markdown()
    assert "33%" not in markdown and "66%" not in markdown and "100% of rows" not in markdown
    assert "- rows: 3" in markdown


def test_the_report_cli_is_not_gating_by_default(capsys) -> None:
    """A live-network row that blocks a merge trains people to bypass the gate."""
    from lab.voice.transport.report import main

    assert main([]) == 0
    printed = capsys.readouterr().out
    assert "The WebRTC transport tier" in printed


# --------------------------------------------------------------------------- #
# The live path, seen from a machine with no keys
# --------------------------------------------------------------------------- #


def test_the_transport_is_unavailable_without_the_flag_and_the_credentials(
    monkeypatch,
) -> None:
    """The cardinal rule, from the instrument's own point of view."""
    session = pytest.importorskip("lab.voice.transport.session")
    for name in LIVE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    transport = session.LiveKitTransport()
    assert not transport.available()
    missing = transport.missing_requirements()
    assert "LAB_LIVE_TRANSPORT" in missing
    assert "LIVEKIT_URL" in missing
    assert "LIVEKIT_API_KEY" in missing
    assert "LIVEKIT_API_SECRET" in missing
    described = transport.describe()
    assert "unavailable" in described


def test_the_refusal_names_every_blocker_at_once(monkeypatch) -> None:
    """Being told about one blocker at a time turns setup into four failed runs."""
    session = pytest.importorskip("lab.voice.transport.session")
    from lab.voice.engines.base import EngineUnavailable

    for name in LIVE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    transport = session.LiveKitTransport()
    with pytest.raises(EngineUnavailable) as caught:
        transport._require()
    message = str(caught.value)
    for name in LIVE_ENV_VARS:
        assert name in message
    assert "transport" in message


def test_the_clip_guard_refuses_audio_that_would_split_into_two_runs() -> None:
    """Checked before a room is opened: a bad clip wastes a whole live session."""
    session = pytest.importorskip("lab.voice.transport.session")
    pytest.importorskip("soundfile")
    clips = REPO_ROOT / "fixtures" / "audio" / "clips"

    published = session.load_clip_frames(clips / "agent-4133d85a3343.opus")
    assert session.longest_quiet_ms(published, threshold_rms=DEFAULT_THRESHOLD_RMS) < 200.0
    session.require_segmentable(
        published, threshold_rms=DEFAULT_THRESHOLD_RMS, max_gap_ms=200.0
    )

    # A two-sentence clip has a sentence boundary longer than the tolerance, and
    # is refused rather than silently splitting.
    from lab.voice.engines.base import EngineUnavailable

    two_sentences = session.load_clip_frames(clips / "agent-87e4b6b31805.opus")
    assert (
        session.longest_quiet_ms(two_sentences, threshold_rms=DEFAULT_THRESHOLD_RMS)
        >= 200.0
    )
    with pytest.raises(EngineUnavailable, match="two speech runs"):
        session.require_segmentable(
            two_sentences, threshold_rms=DEFAULT_THRESHOLD_RMS, max_gap_ms=200.0
        )


def test_the_receive_loops_energy_maths_matches_numpy() -> None:
    """The receive loop hand-rolls RMS to stay cheap; check it is still right."""
    numpy = pytest.importorskip("numpy")
    session = pytest.importorskip("lab.voice.transport.session")
    samples = numpy.array([0, 1000, -2000, 32767, -32768, 5], dtype="<i2")
    expected = float(numpy.sqrt(numpy.mean((samples.astype("float64") / 32768.0) ** 2)))
    assert session._rms_pcm16_bytes(samples.tobytes()) == pytest.approx(expected)
    assert session._rms_pcm16_bytes(b"") == 0.0


def test_frame_energies_frames_both_sides_of_the_comparison_the_same_way() -> None:
    session = pytest.importorskip("lab.voice.transport.session")
    energies = session.frame_energies([0.5] * 320 + [0.0] * 160, 160)
    assert energies == pytest.approx([0.5, 0.5, 0.0])
    # A trailing partial frame is dropped rather than padded: padding would
    # invent quiet audio and move a silent-frame count.
    assert session.frame_energies([0.5] * 200, 160) == pytest.approx([0.5])
