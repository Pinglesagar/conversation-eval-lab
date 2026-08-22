"""Tests for `lab.voice.metrics` — latency percentiles on traces with known timings.

WHAT THIS DEMONSTRATES
----------------------
Every trace here is constructed with timings chosen by hand, so each expected
percentile is arithmetic a reader can verify without running the code. The three
tests that carry the weight:

* `test_percentiles_match_hand_computed_values` — 100 samples at known
  millisecond spacings, with the four expected percentiles worked out in the test
  body. If the percentile definition ever drifts, this fails with a number a
  human can check against the comment.
* `test_p95_is_refused_per_session_and_reported_when_pooled` — the statistical
  claim the module is built on: eight-turn conversations cannot support a p95, and
  pooling them can. Both halves are asserted, so neither the refusal nor the
  pooling can silently stop working.
* `test_verbosity_regression_does_not_look_like_a_latency_regression` — the same
  first-byte timings with longer answers must leave time-to-first-byte untouched
  while time-to-complete moves. This is the confusion the two-distribution design
  exists to prevent.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace
from lab.voice.calibration import recover_response_latencies
from lab.voice.metrics import (
    DEFAULT_QUANTILES,
    Distribution,
    completion_latencies,
    first_byte_latencies,
    iter_turn_latencies,
    latencies_by_engine,
    min_samples_for_quantile,
    response_latency_report,
    speaking_times,
)

TOLERANCE_S = 1e-12


def make_trace(
    turns: list[tuple[float, float, float | None]],
    *,
    scenario_id: str = "booking-happy-path",
    session_id: str = "s0",
    engines: list[str | None] | None = None,
) -> Trace:
    """A trace from explicit `(caller_ts, first_byte_ts, complete_ts)` triples.

    `first_byte_ts` of None means the agent never answered that turn; that is how
    the unanswered-turn tests are built. Every timestamp is passed explicitly, so
    the fake clock is never read and the expected latencies are exactly the
    differences written in the test.
    """
    builder = TraceBuilder(
        scenario_id=scenario_id,
        adapter="test:synthetic",
        session_id=session_id,
        clock=FakeClock(),
    )
    builder.session_start(ts=0.0)
    for index, (caller_ts, first_byte_ts, complete_ts) in enumerate(turns):
        engine = engines[index] if engines is not None else None
        builder.caller_utterance(f"caller turn {index}", ts=caller_ts)
        if first_byte_ts is None:
            continue
        builder.agent_audio_first_byte(turn=index, ts=first_byte_ts, engine=engine)
        if complete_ts is not None:
            builder.agent_audio_complete(turn=index, ts=complete_ts)
    last = max(
        [t for turn in turns for t in turn if t is not None],
        default=0.0,
    )
    builder.session_end(turns=len(turns), ts=last + 0.1)
    return builder.build()


# --------------------------------------------------------------------------- #
# Sample extraction
# --------------------------------------------------------------------------- #


def test_first_byte_latency_equals_the_constructed_differences() -> None:
    trace = make_trace(
        [
            (1.0, 1.10, 1.60),  # 100 ms
            (3.0, 3.25, 4.00),  # 250 ms
            (5.0, 5.50, 6.10),  # 500 ms
        ]
    )
    assert first_byte_latencies(trace) == pytest.approx(
        [0.10, 0.25, 0.50], abs=TOLERANCE_S
    )


def test_first_byte_latencies_is_the_calibrated_definition() -> None:
    """The module reuses the function the calibration gate validates.

    Not a tautology: if someone re-implements the pairing here for convenience,
    the reported figure stops being the one the timing gate certified, and this
    test is what notices.
    """
    trace = make_trace([(1.0, 1.4, 2.0), (3.0, 3.2, 3.9)])
    assert first_byte_latencies(trace) == recover_response_latencies(trace)


def test_verbosity_regression_does_not_look_like_a_latency_regression() -> None:
    """Longer answers must move time-to-complete and leave time-to-first-byte alone."""
    terse = make_trace([(1.0, 1.3, 1.8), (3.0, 3.3, 3.8), (5.0, 5.3, 5.8)])
    verbose = make_trace([(1.0, 1.3, 4.8), (5.0, 5.3, 8.8), (9.0, 9.3, 12.8)])

    assert first_byte_latencies(terse) == pytest.approx(
        first_byte_latencies(verbose), abs=TOLERANCE_S
    )
    assert completion_latencies(terse) == pytest.approx([0.8, 0.8, 0.8], abs=TOLERANCE_S)
    assert completion_latencies(verbose) == pytest.approx(
        [3.8, 3.8, 3.8], abs=TOLERANCE_S
    )
    # And the third distribution is exactly what explains the difference.
    assert speaking_times(verbose) == pytest.approx([3.5, 3.5, 3.5], abs=TOLERANCE_S)
    for ttfb, ttc, speaking in zip(
        first_byte_latencies(verbose),
        completion_latencies(verbose),
        speaking_times(verbose),
        strict=True,
    ):
        assert ttc == pytest.approx(ttfb + speaking, abs=TOLERANCE_S)


def test_unfinished_utterance_produces_no_completion_sample() -> None:
    """A first byte with no completion contributes to TTFB only, not to TTC.

    Turn 0 is answered (first byte at 1.2) but never finishes, so the only
    completion pair is turn 1: caller at 3.0 to complete at 4.0. Pairing is
    non-overlapping and greedy, so the unfinished turn is dropped rather than
    paired across into the next turn's completion.
    """
    trace = make_trace([(1.0, 1.2, None), (3.0, 3.4, 4.0)])
    assert len(first_byte_latencies(trace)) == 2
    assert completion_latencies(trace) == pytest.approx([1.0], abs=TOLERANCE_S)
    assert speaking_times(trace) == pytest.approx([0.6], abs=TOLERANCE_S)


def test_iter_turn_latencies_indexes_the_answered_turns() -> None:
    trace = make_trace([(1.0, 1.1, 1.5), (3.0, 3.9, 4.5)])
    assert [(index, round(value, 6)) for index, value in iter_turn_latencies(trace)] == [
        (0, 0.1),
        (1, 0.9),
    ]


# --------------------------------------------------------------------------- #
# The refusal rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("quantile", "expected"),
    [(0.0, 1), (0.5, 2), (0.9, 10), (0.95, 20), (0.99, 100), (1.0, 1)],
)
def test_min_samples_rule(quantile: float, expected: int) -> None:
    """`ceil(1 / (1 - q))`: at least one observed sample above the quantile."""
    assert min_samples_for_quantile(quantile) == expected


@pytest.mark.parametrize("quantile", [-0.01, 1.01, 2.0])
def test_min_samples_rejects_impossible_quantiles(quantile: float) -> None:
    with pytest.raises(ValueError, match="quantile must be in"):
        min_samples_for_quantile(quantile)


def test_percentile_below_the_minimum_is_refused_with_the_minimum_stated() -> None:
    six = Distribution.from_samples("t", [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])

    p50 = six.quantile(0.50)
    assert p50.reported and p50.value_s == pytest.approx(0.35, abs=TOLERANCE_S)

    for quantile, needed in ((0.90, 10), (0.95, 20), (0.99, 100)):
        refused = six.quantile(quantile)
        assert not refused.reported
        assert refused.value_s is None
        assert refused.n == 6
        assert refused.min_n == needed
        # The refusal states both numbers, so a reader knows how short it fell.
        assert "n=6" in refused.describe()
        assert f"n>={needed}" in refused.describe()


def test_reported_percentile_always_prints_its_sample_count() -> None:
    dist = Distribution.from_samples("t", [0.1] * 20)
    for quantile in dist.quantiles:
        if quantile.reported:
            assert f"(n={quantile.n})" in quantile.describe()


def test_percentiles_match_hand_computed_values() -> None:
    """100 samples at 0..99 ms. Positions are `(n - 1) * q` with linear interpolation.

    p50 -> position 49.5 -> 49.5 ms      p90 -> position 89.1 -> 89.1 ms
    p95 -> position 94.05 -> 94.05 ms    p99 -> position 98.01 -> 98.01 ms
    """
    samples = [index / 1000.0 for index in range(100)]
    dist = Distribution.from_samples("t", samples)
    assert dist.quantile(0.50).value_s == pytest.approx(0.0495, abs=1e-9)
    assert dist.quantile(0.90).value_s == pytest.approx(0.0891, abs=1e-9)
    assert dist.quantile(0.95).value_s == pytest.approx(0.09405, abs=1e-9)
    assert dist.quantile(0.99).value_s == pytest.approx(0.09801, abs=1e-9)
    assert dist.max_s == pytest.approx(0.099, abs=1e-9)
    assert dist.mean_s == pytest.approx(0.0495, abs=1e-9)


def test_max_is_reported_as_max_and_never_as_a_percentile() -> None:
    """One sample: `max` exists, every quantile above p50 is refused."""
    dist = Distribution.from_samples("t", [0.42])
    assert dist.max_s == pytest.approx(0.42, abs=TOLERANCE_S)
    assert dist.min_s == pytest.approx(0.42, abs=TOLERANCE_S)
    assert not dist.quantile(0.50).reported
    assert dist.stdev_s is None  # undefined below two samples, not zero
    assert "p100" not in dist.describe()


def test_empty_distribution_reports_nothing_rather_than_zero() -> None:
    dist = Distribution.from_samples("t", [])
    assert dist.n == 0
    assert dist.mean_s is None and dist.max_s is None and dist.min_s is None
    assert all(not q.reported for q in dist.quantiles)
    assert "no samples (n=0/0)" in dist.describe()


# --------------------------------------------------------------------------- #
# Pooling — the statistical claim
# --------------------------------------------------------------------------- #


def _eight_turn_trace(session_id: str, base_latency: float) -> Trace:
    turns = [
        (
            float(index * 4),
            index * 4 + base_latency + index * 0.01,
            index * 4 + base_latency + index * 0.01 + 1.0,
        )
        for index in range(8)
    ]
    return make_trace(turns, session_id=session_id)


def test_p95_is_refused_per_session_and_reported_when_pooled() -> None:
    traces = [
        _eight_turn_trace("s1", 0.30),
        _eight_turn_trace("s2", 0.40),
        _eight_turn_trace("s3", 0.50),
    ]

    for trace in traces:
        single = response_latency_report(trace)
        assert single.time_to_first_byte.n == 8
        assert not single.time_to_first_byte.quantile(0.95).reported

    pooled = response_latency_report(traces)
    assert pooled.sessions == 3
    assert pooled.time_to_first_byte.n == 24
    assert pooled.time_to_first_byte.quantile(0.95).reported
    # Pooling concatenates samples; it does not average per-session summaries.
    assert sorted(pooled.time_to_first_byte.samples) == sorted(
        sample
        for trace in traces
        for sample in first_byte_latencies(trace)
    )


def test_a_single_trace_and_a_one_element_list_agree() -> None:
    trace = _eight_turn_trace("s1", 0.3)
    assert (
        response_latency_report(trace).time_to_first_byte.samples
        == response_latency_report([trace]).time_to_first_byte.samples
    )


# --------------------------------------------------------------------------- #
# Coverage and attribution
# --------------------------------------------------------------------------- #


def test_unanswered_turns_are_visible_in_the_coverage_line() -> None:
    trace = make_trace(
        [
            (1.0, 1.3, 1.9),
            (3.0, 3.3, 3.9),
            (5.0, None, None),  # agent never answered
            (7.0, 7.3, 7.9),
            (9.0, None, None),
        ]
    )
    report = response_latency_report(trace)
    assert report.caller_turns == 5
    assert report.answered_turns == 3
    assert report.unanswered_turns == 2
    assert "answered 3/5 caller turns" in report.coverage()
    assert "60.0%" in report.coverage()


def test_coverage_of_an_empty_report_is_zero_over_zero() -> None:
    empty = make_trace([])
    report = response_latency_report(empty)
    assert report.coverage() == "answered 0/0 caller turns"


def test_latencies_by_engine_partitions_every_sample() -> None:
    trace = make_trace(
        [
            (1.0, 1.2, 1.8),
            (3.0, 3.4, 3.9),
            (5.0, 5.9, 6.4),
        ],
        engines=["tts-fast", "tts-fast", None],
    )
    buckets = latencies_by_engine(trace)
    assert set(buckets) == {"tts-fast", "unattributed"}
    assert buckets["tts-fast"].n == 2
    assert buckets["unattributed"].n == 1
    # Untagged events are grouped, never dropped: the counts still add up.
    assert sum(dist.n for dist in buckets.values()) == len(first_byte_latencies(trace))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_report_text_names_all_three_distributions() -> None:
    report = response_latency_report(_eight_turn_trace("s1", 0.3))
    text = report.to_text()
    for name in ("time_to_first_byte", "time_to_complete", "agent_speaking_time"):
        assert name in text
    assert "answered 8/8 caller turns" in text


def test_report_markdown_shows_refusals_and_explains_them() -> None:
    report = response_latency_report(_eight_turn_trace("s1", 0.3))
    markdown = report.to_markdown()
    assert "n/a (n<20)" in markdown  # p95 on eight samples
    assert "the quantile was refused" in markdown
    assert markdown.startswith("### Response latency")
    assert markdown.count("|") > 20  # a real table, not a stub


def test_default_quantiles_are_the_documented_four() -> None:
    assert DEFAULT_QUANTILES == (0.50, 0.90, 0.95, 0.99)
    report = response_latency_report(_eight_turn_trace("s1", 0.3))
    assert [q.label for q in report.time_to_first_byte.quantiles] == [
        "p50",
        "p90",
        "p95",
        "p99",
    ]
