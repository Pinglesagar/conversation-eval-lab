"""The calibration gate's own tests.

WHAT THIS DEMONSTRATES
----------------------
`lab.voice.calibration` claims the harness recovers a known delay accurately and
does not charge its own compute to the agent. These tests are where that claim is
made falsifiable rather than merely asserted in a docstring.

The three that carry the weight:

* `test_recovered_latency_is_invariant_to_harness_overhead` — the same sweep run
  with zero harness overhead and with 500 ms of it must recover the same numbers.
  500 ms is five times the shortest delay under test, so if any harness compute
  leaked into the measured window this test would fail by a mile.
* `test_naive_control_absorbs_exactly_the_injected_overhead` — the control figure
  must be wrong by precisely the overhead that was injected. This proves the
  overhead really was applied, which is what makes the invariance test above
  mean something: without it, both runs could be passing because the overhead
  was silently never injected at all.
* `test_gate_can_fail` — a gate that cannot fail is not a gate. Tightening the
  tolerance below the simulated engine jitter must produce a FAIL verdict and a
  non-zero exit code.

Everything here runs offline, on a fake clock, with no sleeping.
"""

from __future__ import annotations

import json
import math

import pytest

from lab.clock import FakeClock, MonotonicClock
from lab.trace.build import TraceBuilder
from lab.trace.io import read_jsonl
from lab.voice.calibration import (
    DEFAULT_DELAYS_S,
    DEFAULT_HARNESS_OVERHEAD_S,
    DEFAULT_REPEATS,
    CalibrationReport,
    CalibrationTolerance,
    MockDelayedAgent,
    main,
    percentile,
    recover_response_latencies,
    recover_turn_wall_times,
    run_calibration,
    write_calibration_artifacts,
)

# Nanosecond-level agreement. Any real leakage of harness compute into the
# measured window would be milliseconds, six orders of magnitude larger; what
# remains at this scale is float64 rounding from adding to a larger accumulator.
FLOAT_NOISE_S = 1e-9


@pytest.fixture(scope="module")
def report() -> CalibrationReport:
    """The default gate run: fake clock, default delays, default tolerance."""
    result = run_calibration()
    assert isinstance(result, CalibrationReport)
    return result


# --------------------------------------------------------------------------- #
# The gate itself
# --------------------------------------------------------------------------- #


def test_gate_passes_with_the_fake_clock(report: CalibrationReport) -> None:
    assert report.verdict == "PASS"
    assert report.passed is True
    assert report.clock == "FakeClock"
    assert len(report.delays) == len(DEFAULT_DELAYS_S)


def test_every_delay_is_within_the_stated_tolerance(report: CalibrationReport) -> None:
    tol = report.tolerance
    for measurement in report.delays:
        assert abs(measurement.rel_error) <= tol.max_rel_error, (
            f"{measurement.nominal_delay_s * 1000:.0f} ms: recovered "
            f"{measurement.mean_s * 1000:.3f} ms, relative error "
            f"{measurement.rel_error:+.3%} exceeds {tol.max_rel_error:.1%}"
        )
        assert measurement.stdev_s <= tol.max_stdev_s
        assert measurement.passed is True


def test_sweep_covers_an_order_of_magnitude(report: CalibrationReport) -> None:
    """A single-delay calibration hides additive bias; the spread is the point."""
    nominals = [m.nominal_delay_s for m in report.delays]
    assert max(nominals) / min(nominals) >= 10


def test_every_sample_is_retained_for_audit(report: CalibrationReport) -> None:
    """Aggregates must be recomputable from the report, not taken on trust."""
    for measurement in report.delays:
        assert len(measurement.samples_s) == measurement.n == DEFAULT_REPEATS
        recomputed = math.fsum(measurement.samples_s) / measurement.n
        assert recomputed == pytest.approx(measurement.mean_s, abs=FLOAT_NOISE_S)
        assert percentile(measurement.samples_s, 0.5) == pytest.approx(measurement.p50_s)
        assert percentile(measurement.samples_s, 0.95) == pytest.approx(measurement.p95_s)


def test_run_is_deterministic() -> None:
    """Same seed, same numbers — the precondition for any of this being evidence."""
    first = run_calibration(repeats=6)
    second = run_calibration(repeats=6)
    assert isinstance(first, CalibrationReport) and isinstance(second, CalibrationReport)
    assert first.model_dump() == second.model_dump()


# --------------------------------------------------------------------------- #
# The claim that the harness excludes its own compute
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("overhead_s", [0.0, 0.005, 0.030, 0.500])
def test_recovered_latency_is_invariant_to_harness_overhead(overhead_s: float) -> None:
    """The load-bearing test for the whole repo's timing story.

    500 ms of injected harness compute is five times the shortest delay in the
    sweep. If the harness timestamped its events at construction instead of
    capturing boundary floats and back-dating them, the 100 ms row would come
    back at 600 ms. The recovered samples must instead be untouched.
    """
    baseline = run_calibration(harness_overhead_s=0.0)
    candidate = run_calibration(harness_overhead_s=overhead_s)
    assert isinstance(baseline, CalibrationReport)
    assert isinstance(candidate, CalibrationReport)

    for base, cand in zip(baseline.delays, candidate.delays, strict=True):
        assert base.nominal_delay_s == cand.nominal_delay_s
        for b_sample, c_sample in zip(base.samples_s, cand.samples_s, strict=True):
            assert c_sample == pytest.approx(b_sample, abs=FLOAT_NOISE_S)
        assert cand.mean_s == pytest.approx(base.mean_s, abs=FLOAT_NOISE_S)
        assert cand.stdev_s == pytest.approx(base.stdev_s, abs=FLOAT_NOISE_S)
    assert candidate.verdict == "PASS"


@pytest.mark.parametrize("overhead_s", [0.005, 0.030, 0.500])
def test_naive_control_absorbs_exactly_the_injected_overhead(overhead_s: float) -> None:
    """Proves the overhead was really injected, which is what makes the
    invariance test above meaningful rather than vacuous.

    The control pairs `transcript_in -> agent_utterance`, spanning both sides of
    the boundary, so its error must equal the injected overhead to within float
    noise — no more (nothing else is in there) and no less (it was not skipped).
    """
    result = run_calibration(harness_overhead_s=overhead_s)
    assert isinstance(result, CalibrationReport)
    for measurement in result.delays:
        assert measurement.control_abs_error_s == pytest.approx(
            overhead_s + measurement.abs_error_s, abs=FLOAT_NOISE_S
        )


def test_naive_control_fails_the_gate_at_the_default_overhead(
    report: CalibrationReport,
) -> None:
    """The whole-turn wall time is a real number and the wrong one."""
    assert report.control_verdict == "FAIL"
    shortest = min(report.delays, key=lambda m: m.nominal_delay_s)
    assert shortest.control_passed is False
    # ~30 ms of overhead on a 100 ms delay is a ~30% overstatement.
    assert shortest.control_rel_error > 0.25


def test_naive_control_error_hides_at_long_delays(report: CalibrationReport) -> None:
    """Why the sweep spans an order of magnitude.

    A fixed additive bias shrinks in relative terms as the delay grows, so the
    broken method would have been certified by a calibration run only at 2 s.
    """
    by_delay = sorted(report.delays, key=lambda m: m.nominal_delay_s)
    shortest, longest = by_delay[0], by_delay[-1]
    assert shortest.control_rel_error > longest.control_rel_error * 10
    assert longest.control_passed is True, (
        "expected the additive bias to be invisible at 2 s — that is the trap "
        "this sweep exists to avoid"
    )


def test_a_gate_that_cannot_fail_is_not_a_gate() -> None:
    """Tighten the tolerance below the simulated jitter and the verdict must flip."""
    result = run_calibration(
        tolerance=CalibrationTolerance(max_rel_error=0.05, max_stdev_s=0.0001),
        jitter_sigma_s=0.004,
    )
    assert isinstance(result, CalibrationReport)
    assert result.verdict == "FAIL"
    assert result.passed is False


def test_gate_catches_a_harness_that_reports_the_control_figure() -> None:
    """Simulate the bug the gate exists to catch, and confirm it is caught.

    A harness whose stopwatch included its own compute would produce the control
    numbers. Scoring those against the tolerance must fail.
    """
    result = run_calibration(harness_overhead_s=0.030)
    assert isinstance(result, CalibrationReport)
    tol = result.tolerance
    would_pass = all(
        abs(m.control_rel_error) <= tol.max_rel_error for m in result.delays
    )
    assert would_pass is False


# --------------------------------------------------------------------------- #
# Recovery reads the trace and nothing but the trace
# --------------------------------------------------------------------------- #


def test_latency_is_recovered_from_trace_events_alone() -> None:
    """Hand-build a trace with chosen timestamps; recovery must return the deltas.

    No mock agent, no clock, no calibration run — just the invariant that a
    timing figure is a subtraction over two events.
    """
    builder = TraceBuilder(scenario_id="unit", adapter="unit", clock=FakeClock())
    builder.session_start(ts=0.0)
    builder.caller_utterance("first", ts=1.00)
    builder.agent_audio_first_byte(turn=1, ts=1.40)
    builder.caller_utterance("second", ts=2.00)
    builder.agent_audio_first_byte(turn=2, ts=2.25)
    builder.session_end(ts=3.0)

    assert recover_response_latencies(builder.build()) == pytest.approx([0.40, 0.25])


def test_recovery_ignores_a_turn_that_never_got_a_response() -> None:
    """An unanswered turn is dropped, not paired across to the next answer.

    Pairing across would invent a latency that no turn ever had — the kind of
    silently-plausible number this repo is built to refuse.
    """
    builder = TraceBuilder(scenario_id="unit", adapter="unit", clock=FakeClock())
    builder.session_start(ts=0.0)
    builder.caller_utterance("unanswered", ts=1.0)
    builder.caller_utterance("answered", ts=5.0)
    builder.agent_audio_first_byte(turn=1, ts=5.5)
    assert recover_response_latencies(builder.build()) == pytest.approx([0.5])


def test_calibration_traces_are_well_formed() -> None:
    result = run_calibration(repeats=4, collect_traces=True)
    assert isinstance(result, tuple)
    report, traces = result
    assert len(traces) == len(DEFAULT_DELAYS_S)

    for trace, measurement in zip(traces, report.delays, strict=True):
        assert trace.is_ordered(), "out-of-order timestamps make every duration junk"
        assert trace.unknown_kinds() == set()
        assert trace.events[0].kind == "session_start"
        assert trace.events[0].ts == 0.0, "ts is seconds since *this* session's start"
        assert trace.events[-1].kind == "session_end"
        assert len(recover_response_latencies(trace)) == measurement.n
        assert len(recover_turn_wall_times(trace)) == measurement.n


# --------------------------------------------------------------------------- #
# The mock agent is a trustworthy ground truth
# --------------------------------------------------------------------------- #


def test_mock_agent_delay_is_exact_without_jitter() -> None:
    clock = FakeClock()
    agent = MockDelayedAgent(0.25, clock=clock)
    before = clock.now()
    agent.respond("hello")
    assert clock.now() - before == pytest.approx(0.25)
    assert agent.calls == 1


def test_mock_agent_jitter_is_bounded_and_never_negative() -> None:
    clock = FakeClock()
    agent = MockDelayedAgent(0.01, clock=clock, jitter_sigma_s=0.004)
    for _ in range(200):
        before = clock.now()
        agent.respond("hello")
        elapsed = clock.now() - before
        assert 0.0 <= elapsed <= 0.01 + 3 * 0.004 + FLOAT_NOISE_S


def test_mock_agent_rejects_a_negative_delay() -> None:
    with pytest.raises(ValueError):
        MockDelayedAgent(-0.1, clock=FakeClock())


def test_mock_agent_runs_on_a_real_clock_too() -> None:
    """The measured code path must not be a test-only special case.

    A short real delay, so the suite stays fast; the point is that the same
    object works in real time, not that the real clock is precise.
    """
    clock = MonotonicClock()
    agent = MockDelayedAgent(0.01, clock=clock)
    before = clock.now()
    agent.respond("hello")
    assert clock.now() - before >= 0.01


# --------------------------------------------------------------------------- #
# Statistics and report plumbing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([1.0], 0.5, 1.0),
        ([1.0, 2.0, 3.0, 4.0], 0.0, 1.0),
        ([1.0, 2.0, 3.0, 4.0], 1.0, 4.0),
        ([1.0, 2.0, 3.0, 4.0], 0.5, 2.5),
        # position = (n-1)*q = 3*0.95 = 2.85 -> 3.0 + 0.85*(4.0-3.0)
        ([1.0, 2.0, 3.0, 4.0], 0.95, 3.85),
        ([4.0, 1.0, 3.0, 2.0], 0.5, 2.5),  # unsorted input
    ],
)
def test_percentile_matches_linear_interpolation(
    values: list[float], q: float, expected: float
) -> None:
    assert percentile(values, q) == pytest.approx(expected)


@pytest.mark.parametrize(("values", "q"), [([], 0.5), ([1.0], 1.5), ([1.0], -0.1)])
def test_percentile_rejects_undefined_input(values: list[float], q: float) -> None:
    with pytest.raises(ValueError):
        percentile(values, q)


def test_tolerance_is_printed_with_the_verdict(report: CalibrationReport) -> None:
    """An unstated tolerance is a preference, not a criterion."""
    markdown = report.to_markdown()
    assert report.tolerance.describe() in markdown
    assert "**Verdict: PASS**" in markdown
    assert "5/5 delays within tolerance" in markdown
    for text in (markdown, report.to_text()):
        assert "FakeClock" in text


def test_markdown_prints_rates_with_numerator_and_denominator(
    report: CalibrationReport,
) -> None:
    assert f"{report.repeats * len(report.delays)} measured turns" in report.to_markdown()


def test_run_rejects_input_that_makes_the_statistics_meaningless() -> None:
    with pytest.raises(ValueError):
        run_calibration(repeats=1)  # no standard deviation exists
    with pytest.raises(ValueError):
        run_calibration(delays_s=())
    with pytest.raises(ValueError):
        run_calibration(delays_s=(0.0,))  # no relative error exists


# --------------------------------------------------------------------------- #
# Artifacts and CLI
# --------------------------------------------------------------------------- #


def test_write_calibration_artifacts(tmp_path) -> None:
    result = run_calibration(repeats=3, collect_traces=True)
    assert isinstance(result, tuple)
    small_report, traces = result

    written = write_calibration_artifacts(
        small_report, tmp_path / "out", sample_trace=traces[0]
    )
    assert set(written) == {"json", "markdown", "trace"}

    reloaded = CalibrationReport.model_validate(
        json.loads(written["json"].read_text(encoding="utf-8"))
    )
    assert reloaded.model_dump() == small_report.model_dump()
    assert written["markdown"].read_text(encoding="utf-8").startswith(
        "# Timing calibration report"
    )

    # The evidence trace round-trips, so a reader can recompute a latency by hand.
    trace = read_jsonl(written["trace"])
    assert trace.adapter == "calibration:mock"
    assert len(recover_response_latencies(trace)) == 3


def test_cli_exits_zero_on_pass(tmp_path) -> None:
    exit_code = main(["--out", str(tmp_path), "--repeats", "4"])
    assert exit_code == 0
    assert (tmp_path / "calibration_report.json").exists()
    assert (tmp_path / "calibration_report.md").exists()
    assert (tmp_path / "calibration_sample_trace.jsonl").exists()


def test_cli_exits_nonzero_on_fail(tmp_path) -> None:
    """CI must be able to stop a pipeline that is about to publish decoration."""
    exit_code = main(
        ["--out", str(tmp_path), "--repeats", "4", "--max-stdev-ms", "0.0001"]
    )
    assert exit_code == 1


def test_cli_no_write_leaves_the_directory_alone(tmp_path) -> None:
    assert main(["--out", str(tmp_path), "--repeats", "4", "--no-write"]) == 0
    assert list(tmp_path.iterdir()) == []


def test_default_overhead_is_non_zero() -> None:
    """A gate that only passes when the harness does no work proves nothing."""
    assert DEFAULT_HARNESS_OVERHEAD_S > 0
