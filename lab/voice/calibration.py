"""The timing calibration gate.

WHY THIS EXISTS
---------------
An uncalibrated latency number is decoration.

Every voice-agent evaluation report in circulation quotes a response time, and
almost none of them say what that number was measured against. That is a
problem, because the plausible failure modes are large and silent. A harness
that builds a pydantic model, serialises a payload and writes a line of JSONL
between starting and stopping its stopwatch reports its own overhead as agent
latency. A harness on `time.time()` reports an NTP correction as a regression. A
harness that averages over a p95-shaped distribution reports a number no caller
ever experienced. None of these announce themselves: the output looks like
"average response time: 840 ms" either way.

So this module measures the measuring instrument. It drives an agent whose
latency is known exactly by construction, then asks the harness what it thinks
that latency was — reading the answer back out of the trace, through the same
code path a real evaluation uses, with no privileged access to the truth. If the
recovered figure does not match the known figure within a stated tolerance, the
gate fails and every latency number downstream is treated as unproven.

This gate is meant to run *before* any metric in this repo is believed. It is the
difference between "our p95 is 1.2 s" and "our p95 is 1.2 s, and here is the
evidence that our stopwatch is accurate to within 5% across an order of
magnitude of delays."

HOW THE HARNESS EXCLUDES ITS OWN COMPUTE
----------------------------------------
This is the part that is easy to get wrong, so it is stated precisely.

A turn is instrumented at four instants:

    t_turn_start   the caller's transcribed text becomes available to the harness
    t0             BOUNDARY OUT — the last instant before the request is handed
                   to the agent, after all harness-side preparation is finished
    t1             BOUNDARY IN  — the first instant after the agent's first
                   response byte returns, before any harness-side processing
    t_turn_end     the harness has finished materialising the turn

The reported response latency is `t1 - t0`, and three rules keep that window
clean:

1.  **Timestamps are captured as bare floats, not as events.** `t0` and `t1` are
    two `clock.now()` reads into local variables. The `TraceEvent`s carrying them
    are constructed *after* `t1` and back-dated via `TraceBuilder`'s `ts=`
    parameter. Model construction, payload assembly and list append therefore all
    happen outside the window. Instrumentation that builds its event at the
    boundary charges the cost of the instrument to the thing being measured.

2.  **Nothing but the call under test sits between the two reads.** No logging,
    no validation, no retry bookkeeping, no scenario logic. Preparation happens
    before `t0`; interpretation happens after `t1`.

3.  **The clock is monotonic and session-relative** (`lab.clock`), so the
    subtraction cannot be corrupted by a wall-clock adjustment, and every `ts` in
    a trace shares one origin.

The proof is empirical, not rhetorical. `run_calibration` injects a configurable
slab of artificial harness overhead — split either side of the boundary — and the
gate still has to pass. `tests/test_timing_calibration.py` runs the calibration
twice, with zero overhead and with overhead an order of magnitude larger than the
smallest delay under test, and asserts the recovered samples are *bit-identical*.
If the harness were charging any of its own compute to the agent, that assertion
would fail immediately.

The same run also records a deliberately naive control: `t_turn_end -
t_turn_start`, the wall-clock time for the whole turn including harness compute.
Both figures are recovered from the very same trace, by pairing different event
kinds:

    response latency (reported) : caller_utterance -> agent_audio_first_byte
    turn wall time   (control)  : transcript_in    -> agent_utterance

The control is a real and useful number — it is what an end-to-end turn costs —
but it is not agent latency, and the report shows it failing the gate by tens of
percent to make the distinction concrete. Same trace, two pairings, two answers:
choosing the wrong pair is how a harness ends up measuring the laptop.

WHY THE DEFAULT CLOCK IS A FAKE ONE
-----------------------------------
The gate's job is to validate the harness's *measurement logic* against a known
ground truth. Under `FakeClock` the ground truth is exact — the mock agent
advances virtual time by precisely the delay it was asked for — so any discrepancy
in the recovered figure is unambiguously the harness's fault, and the result is
identical on every machine, offline, in milliseconds. Under `MonotonicClock` the
"known" delay is really `time.sleep`, whose own error is OS scheduling noise of a
few milliseconds; at a 100 ms nominal delay that noise alone can approach the 5%
tolerance, so a real-clock run measures the operating system as much as the
harness. Both are available (`--clock real`); the fake clock is the gate, and the
real clock is a smoke test whose expected noise is documented rather than
asserted.

WHAT THIS DOES NOT CLAIM
------------------------
Passing this gate says the harness recovers a known delay faithfully. It says
nothing about whether a particular vendor adapter puts its `agent_audio_first_byte`
event in the right place — that is a per-adapter obligation, and an adapter that
emits it late will produce a trustworthy measurement of the wrong instant. The
gate makes the instrument credible; correct wiring per adapter is a separate
argument that belongs with each adapter.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.clock import Clock, FakeClock, MonotonicClock
from lab.trace.build import TraceBuilder
from lab.trace.io import write_jsonl
from lab.trace.schema import EventKind, Trace

__all__ = [
    "DEFAULT_DELAYS_S",
    "DEFAULT_REPEATS",
    "MockDelayedAgent",
    "CalibrationTolerance",
    "DelayMeasurement",
    "CalibrationReport",
    "run_calibration",
    "recover_response_latencies",
    "recover_turn_wall_times",
    "write_calibration_artifacts",
    "percentile",
    "main",
]

#: The delays the gate sweeps. Spanning 0.1 s to 2.0 s — a factor of twenty —
#: matters: an additive bias (a fixed overhead leaking into the window) shows up
#: as a large relative error at the short end and a negligible one at the long
#: end, so a single-delay calibration can hide it entirely.
DEFAULT_DELAYS_S: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0, 2.0)

#: Repeats per delay. Enough that the mean is not dominated by a single draw of
#: engine jitter, few enough that a real-clock run finishes in seconds.
DEFAULT_REPEATS: int = 20

#: Artificial harness compute injected per turn, to prove the boundary discipline
#: works. Split either side of the measurement boundary.
DEFAULT_HARNESS_OVERHEAD_S: float = 0.030
_OVERHEAD_PRE_FRACTION: float = 0.3  # before BOUNDARY OUT: prompt assembly, bookkeeping
_OVERHEAD_POST_FRACTION: float = 0.7  # after BOUNDARY IN: validation, event build, write

#: Standard deviation of the simulated per-response engine jitter. Real engines
#: are not constant-latency, and a gate that only ever sees a constant would not
#: exercise the spread statistics (p50, p95, stdev) at all.
DEFAULT_JITTER_SIGMA_S: float = 0.004

DEFAULT_SEED: int = 20260822

_ADAPTER_NAME = "calibration:mock"
_AGENT_NAME = "MockDelayedAgent"
_STT_ENGINE = "mock-stt"
_TTS_ENGINE = "mock-tts"


# --------------------------------------------------------------------------- #
# Statistics (pure standard library on purpose)
# --------------------------------------------------------------------------- #


def percentile(values: Sequence[float], q: float) -> float:
    """Linearly interpolated percentile, `q` in [0, 1].

    Implemented here rather than pulled from numpy because numpy lives in the
    optional `[audio]` extra, and the cardinal rule of this repo is that
    `pip install -e ".[dev]" && pytest` passes on a clean machine. The timing
    gate is the last thing that should need an optional dependency to run.

    Matches `numpy.percentile(..., method="linear")`: the position is
    `(n - 1) * q` and the result interpolates between the two neighbouring
    order statistics.
    """
    if not values:
        raise ValueError("percentile of an empty sequence is undefined")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q!r}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _stdev(values: Sequence[float]) -> float:
    """Sample standard deviation; 0.0 for fewer than two values."""
    return statistics.stdev(values) if len(values) > 1 else 0.0


# --------------------------------------------------------------------------- #
# The instrument under calibration needs something with a known delay
# --------------------------------------------------------------------------- #


class MockDelayedAgent:
    """An agent that responds after a known delay. The calibration ground truth.

    It waits through the injected `Clock`, so the exact same object is usable in
    virtual time (instant, exact, reproducible) and in real time (actually
    sleeps). That is the whole reason `Clock` has a `sleep` method: the code being
    measured must not know or care which kind of time it is living in, or the
    calibration would be validating a special test-only path.

    `jitter_sigma_s` adds seeded Gaussian noise to each response, clamped to
    +/- 3 sigma so a pathological draw cannot make the delay negative. It models
    engine-side variance. The nominal delay remains the ground truth the gate
    compares against: the mean of many jittered responses must recover it.
    """

    def __init__(
        self,
        delay_s: float,
        *,
        clock: Clock,
        jitter_sigma_s: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        if delay_s < 0:
            raise ValueError(f"delay_s must be non-negative, got {delay_s!r}")
        self.delay_s = float(delay_s)
        self.clock = clock
        self.jitter_sigma_s = float(jitter_sigma_s)
        self._rng = rng if rng is not None else random.Random(DEFAULT_SEED)
        self.calls: int = 0

    def _next_delay(self) -> float:
        if self.jitter_sigma_s <= 0:
            return self.delay_s
        limit = 3.0 * self.jitter_sigma_s
        jitter = max(-limit, min(limit, self._rng.gauss(0.0, self.jitter_sigma_s)))
        return max(0.0, self.delay_s + jitter)

    def respond(self, text: str) -> str:
        """Wait out this response's delay, then reply. Nothing else happens here.

        Deliberately trivial: anything else in this method would be work inside
        the measured window that is neither the delay nor the harness, which
        would make the gate's arithmetic ambiguous.
        """
        self.calls += 1
        self.clock.sleep(self._next_delay())
        return f"[mock reply {self.calls}] {text}"

    def __repr__(self) -> str:
        return (
            f"MockDelayedAgent(delay_s={self.delay_s}, "
            f"jitter_sigma_s={self.jitter_sigma_s}, calls={self.calls})"
        )


# --------------------------------------------------------------------------- #
# Recovering timings from a trace, and only from a trace
# --------------------------------------------------------------------------- #


def recover_response_latencies(trace: Trace) -> list[float]:
    """Response latency per turn, in seconds, derived from the trace alone.

    Pairs each `caller_utterance` (BOUNDARY OUT) with the following
    `agent_audio_first_byte` (BOUNDARY IN). This is the reported figure, and this
    function is the only definition of it in the repo — the calibration gate
    validates exactly the function real evaluations call, not a parallel
    implementation that happens to agree today.
    """
    return [
        b.ts - a.ts
        for a, b in trace.event_pairs(
            EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_FIRST_BYTE
        )
    ]


def recover_turn_wall_times(trace: Trace) -> list[float]:
    """Whole-turn wall time per turn, in seconds — the naive control.

    Pairs `transcript_in` (caller text available to the harness) with the
    following `agent_utterance` (harness finished with the turn), so it includes
    every millisecond of harness compute either side of the boundary. Useful as a
    cost figure; wrong as an agent-latency figure. Reported alongside the real
    measurement to make the size of the difference visible.
    """
    return [
        b.ts - a.ts
        for a, b in trace.event_pairs(EventKind.TRANSCRIPT_IN, EventKind.AGENT_UTTERANCE)
    ]


# --------------------------------------------------------------------------- #
# Report models
# --------------------------------------------------------------------------- #


class CalibrationTolerance(BaseModel):
    """The pass criteria. Configurable, and always printed with the result.

    A tolerance that is not stated alongside the verdict is not a tolerance, it
    is a preference. Both bounds are needed: relative error catches systematic
    bias (the harness is consistently 30 ms slow), while the standard-deviation
    bound catches instability (the harness is right on average but individual
    samples scatter, which makes any single-run figure unusable).
    """

    model_config = ConfigDict(extra="forbid")

    max_rel_error: float = Field(
        default=0.05,
        gt=0.0,
        description="Maximum |measured mean - nominal| / nominal, as a fraction.",
    )
    max_stdev_s: float = Field(
        default=0.015,
        gt=0.0,
        description="Maximum sample standard deviation of recovered samples, seconds.",
    )

    def describe(self) -> str:
        return (
            f"|relative error| <= {self.max_rel_error:.1%} "
            f"and stdev <= {self.max_stdev_s * 1000:.1f} ms"
        )


class DelayMeasurement(BaseModel):
    """What the harness recovered at one nominal delay."""

    model_config = ConfigDict(extra="forbid")

    nominal_delay_s: float
    n: int

    mean_s: float
    p50_s: float
    p95_s: float
    stdev_s: float
    abs_error_s: float = Field(description="mean_s - nominal_delay_s, signed.")
    rel_error: float = Field(description="abs_error_s / nominal_delay_s, signed fraction.")
    passed: bool

    # The naive control, recovered from the same trace by pairing different kinds.
    control_mean_s: float
    control_abs_error_s: float
    control_rel_error: float
    control_passed: bool

    samples_s: list[float] = Field(
        description="Every recovered sample, so the aggregate can be audited."
    )

    @property
    def error_ms(self) -> float:
        return self.abs_error_s * 1000.0


class CalibrationReport(BaseModel):
    """The full gate result: per-delay measurements plus one overall verdict."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "FAIL"]
    control_verdict: Literal["PASS", "FAIL"] = Field(
        description=(
            "Verdict the naive whole-turn figure would have scored. Expected to be "
            "FAIL whenever harness overhead is non-zero; that is the point of it."
        )
    )
    tolerance: CalibrationTolerance
    delays: list[DelayMeasurement]

    repeats: int
    clock: str = Field(description="Which clock produced the timings.")
    jitter_sigma_s: float
    harness_overhead_s: float
    seed: int
    notes: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def table_rows(self) -> list[tuple[str, ...]]:
        """The main table as plain strings, header first."""
        rows: list[tuple[str, ...]] = [
            (
                "nominal",
                "n",
                "mean",
                "p50",
                "p95",
                "stdev",
                "abs err",
                "rel err",
                "verdict",
            )
        ]
        for d in self.delays:
            rows.append(
                (
                    f"{d.nominal_delay_s * 1000:.0f} ms",
                    str(d.n),
                    f"{d.mean_s * 1000:.3f} ms",
                    f"{d.p50_s * 1000:.3f} ms",
                    f"{d.p95_s * 1000:.3f} ms",
                    f"{d.stdev_s * 1000:.3f} ms",
                    f"{d.abs_error_s * 1000:+.3f} ms",
                    f"{d.rel_error:+.3%}",
                    "PASS" if d.passed else "FAIL",
                )
            )
        return rows

    def control_rows(self) -> list[tuple[str, ...]]:
        """The naive-control table as plain strings, header first."""
        rows: list[tuple[str, ...]] = [
            ("nominal", "naive mean", "abs err", "rel err", "verdict")
        ]
        for d in self.delays:
            rows.append(
                (
                    f"{d.nominal_delay_s * 1000:.0f} ms",
                    f"{d.control_mean_s * 1000:.3f} ms",
                    f"{d.control_abs_error_s * 1000:+.3f} ms",
                    f"{d.control_rel_error:+.3%}",
                    "PASS" if d.control_passed else "FAIL",
                )
            )
        return rows

    def to_markdown(self) -> str:
        """A self-contained markdown summary — verdict, method, and the numbers."""
        total = len(self.delays)
        n_pass = sum(1 for d in self.delays if d.passed)

        def table(rows: list[tuple[str, ...]]) -> list[str]:
            header, *body = rows
            out = ["| " + " | ".join(header) + " |"]
            out.append("|" + "|".join("---" for _ in header) + "|")
            out.extend("| " + " | ".join(r) + " |" for r in body)
            return out

        lines: list[str] = [
            "# Timing calibration report",
            "",
            f"**Verdict: {self.verdict}** — {n_pass}/{total} delays within tolerance.",
            "",
            f"- Tolerance: {self.tolerance.describe()}",
            f"- Clock: `{self.clock}`",
            f"- Repeats per delay: {self.repeats} "
            f"({self.repeats * total} measured turns in total)",
            f"- Simulated engine jitter: sigma = "
            f"{self.jitter_sigma_s * 1000:.1f} ms",
            f"- Injected harness overhead per turn: "
            f"{self.harness_overhead_s * 1000:.1f} ms "
            f"({_OVERHEAD_PRE_FRACTION:.0%} before the boundary, "
            f"{_OVERHEAD_POST_FRACTION:.0%} after)",
            f"- Seed: {self.seed}",
            "",
            "## Recovered response latency",
            "",
            "Measured as `agent_audio_first_byte.ts - caller_utterance.ts`, read back "
            "out of the trace by `recover_response_latencies()`.",
            "",
            *table(self.table_rows()),
            "",
            "## Control: naive whole-turn wall time",
            "",
            "The same turns, measured as `agent_utterance.ts - transcript_in.ts` — the "
            f"figure a harness gets when it charges its own {self.harness_overhead_s * 1000:.0f} ms "
            "of compute to the agent. Scored against the same tolerance to show what "
            "the boundary discipline is worth.",
            "",
            *table(self.control_rows()),
            "",
            f"**Control verdict: {self.control_verdict}**",
            "",
            "## Notes",
            "",
            *[f"- {note}" for note in self.notes],
            "",
        ]
        return "\n".join(lines)

    def to_text(self) -> str:
        """A fixed-width rendering for terminals."""
        rows = self.table_rows()
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        out = [
            f"Timing calibration: {self.verdict}",
            f"  tolerance : {self.tolerance.describe()}",
            f"  clock     : {self.clock}",
            f"  repeats   : {self.repeats} per delay",
            "",
        ]
        for index, row in enumerate(rows):
            out.append("  " + "  ".join(cell.rjust(widths[i]) for i, cell in enumerate(row)))
            if index == 0:
                out.append("  " + "  ".join("-" * widths[i] for i in range(len(widths))))
        out.append("")
        out.append(f"  naive whole-turn control verdict: {self.control_verdict}")
        return "\n".join(out)


# --------------------------------------------------------------------------- #
# The calibration run
# --------------------------------------------------------------------------- #


def _calibrate_one_delay(
    delay_s: float,
    *,
    repeats: int,
    clock: Clock,
    jitter_sigma_s: float,
    harness_overhead_s: float,
    seed: int,
) -> Trace:
    """Drive a `MockDelayedAgent` for `repeats` turns and return the trace.

    This function contains the boundary discipline the module docstring
    describes. Read the marked lines together: everything between BOUNDARY OUT
    and BOUNDARY IN is the agent, and nothing else.
    """
    builder = TraceBuilder(
        scenario_id=f"calibration/delay_{int(round(delay_s * 1000))}ms",
        adapter=_ADAPTER_NAME,
        session_id=f"calib-{int(round(delay_s * 1000))}ms",
        clock=clock,
    )
    agent = MockDelayedAgent(
        delay_s,
        clock=clock,
        jitter_sigma_s=jitter_sigma_s,
        # Seeded per delay so that the jitter draws for a given delay are
        # independent of the other delays in the sweep, and identical across
        # runs regardless of how much harness overhead is configured. String
        # seeding is stable across processes and platforms (CPython hashes it
        # with sha512), unlike anything derived from `hash()`.
        rng=random.Random(f"calibration:{seed}:{round(delay_s * 1e6)}"),
    )
    pre_overhead = harness_overhead_s * _OVERHEAD_PRE_FRACTION
    post_overhead = harness_overhead_s * _OVERHEAD_POST_FRACTION

    builder.session_start(delay_s=delay_s, repeats=repeats)

    for turn in range(1, repeats + 1):
        prompt = f"turn {turn}: a table for two at eight, please"

        # The caller's transcribed text lands in the harness. Start of the naive
        # control window.
        builder.transcript_in(prompt, confidence=1.0, engine=_STT_ENGINE)

        # Harness-side preparation: prompt assembly, scenario bookkeeping. Real
        # work that a real harness really does, and that must fall OUTSIDE the
        # measured window.
        clock.sleep(pre_overhead)

        t0 = clock.now()  # ---- BOUNDARY OUT: request leaves the harness
        reply = agent.respond(prompt)  # the system under test, alone
        t1 = clock.now()  # ---- BOUNDARY IN: first response byte returns

        # Harness-side processing: validation, event construction, serialisation.
        clock.sleep(post_overhead)
        t_turn_end = clock.now()

        # Events are built here, after the window has closed, and back-dated to
        # the captured instants. This is the mechanism that keeps the cost of the
        # instrument out of the reading.
        builder.caller_utterance(prompt, ts=t0)
        builder.agent_audio_first_byte(turn=turn, ts=t1, engine=_TTS_ENGINE)
        builder.agent_utterance(reply, agent=_AGENT_NAME, ts=t_turn_end, engine=_TTS_ENGINE)

    builder.session_end(reason="completed", turns=repeats)
    return builder.build()


def run_calibration(
    *,
    delays_s: Sequence[float] = DEFAULT_DELAYS_S,
    repeats: int = DEFAULT_REPEATS,
    clock_factory: Callable[[], Clock] = FakeClock,
    tolerance: CalibrationTolerance | None = None,
    jitter_sigma_s: float = DEFAULT_JITTER_SIGMA_S,
    harness_overhead_s: float = DEFAULT_HARNESS_OVERHEAD_S,
    seed: int = DEFAULT_SEED,
    collect_traces: bool = False,
) -> CalibrationReport | tuple[CalibrationReport, list[Trace]]:
    """Run the gate: sweep the delays, recover them from traces, score the result.

    Args:
        delays_s: Nominal delays to sweep, seconds.
        repeats: Turns per delay.
        clock_factory: Called once per delay to make that session's clock. A
            factory rather than a single clock because trace timestamps are
            defined as seconds since *session* start: sharing one clock across
            five sessions would leave four of them starting at an arbitrary
            offset and quietly break the schema's contract. Defaults to
            `FakeClock` — see the module docstring for why virtual time is the
            right default for a gate.
        tolerance: Pass criteria; defaults to 5% relative error and 15 ms stdev.
        jitter_sigma_s: Simulated per-response engine variance.
        harness_overhead_s: Artificial harness compute injected per turn, split
            either side of the measurement boundary. Non-zero by default: a gate
            that only passes when the harness does no work proves nothing.
        seed: Makes the jitter draws reproducible.
        collect_traces: Also return the traces, for fixture writing and tests.

    Returns:
        A `CalibrationReport`, or `(report, traces)` when `collect_traces`.
    """
    if repeats < 2:
        raise ValueError("repeats must be at least 2 for a standard deviation to exist")
    if not delays_s:
        raise ValueError("delays_s must not be empty")

    tol = tolerance if tolerance is not None else CalibrationTolerance()

    measurements: list[DelayMeasurement] = []
    traces: list[Trace] = []
    clock_name = "unknown"

    for delay in delays_s:
        if delay <= 0:
            raise ValueError(f"delays must be positive to have a relative error, got {delay!r}")

        session_clock = clock_factory()
        clock_name = type(session_clock).__name__

        trace = _calibrate_one_delay(
            delay,
            repeats=repeats,
            clock=session_clock,
            jitter_sigma_s=jitter_sigma_s,
            harness_overhead_s=harness_overhead_s,
            seed=seed,
        )
        traces.append(trace)

        samples = recover_response_latencies(trace)
        controls = recover_turn_wall_times(trace)
        if len(samples) != repeats:
            raise AssertionError(
                f"recovered {len(samples)} samples from the trace but ran {repeats} turns; "
                "the trace is not a faithful record of the run"
            )

        mean = statistics.fmean(samples)
        stdev = _stdev(samples)
        abs_error = mean - delay
        rel_error = abs_error / delay

        control_mean = statistics.fmean(controls)
        control_abs_error = control_mean - delay
        control_rel_error = control_abs_error / delay

        measurements.append(
            DelayMeasurement(
                nominal_delay_s=delay,
                n=len(samples),
                mean_s=mean,
                p50_s=percentile(samples, 0.50),
                p95_s=percentile(samples, 0.95),
                stdev_s=stdev,
                abs_error_s=abs_error,
                rel_error=rel_error,
                passed=abs(rel_error) <= tol.max_rel_error and stdev <= tol.max_stdev_s,
                control_mean_s=control_mean,
                control_abs_error_s=control_abs_error,
                control_rel_error=control_rel_error,
                control_passed=(
                    abs(control_rel_error) <= tol.max_rel_error
                    and _stdev(controls) <= tol.max_stdev_s
                ),
                samples_s=samples,
            )
        )

    report = CalibrationReport(
        verdict="PASS" if all(m.passed for m in measurements) else "FAIL",
        control_verdict=(
            "PASS" if all(m.control_passed for m in measurements) else "FAIL"
        ),
        tolerance=tol,
        delays=measurements,
        repeats=repeats,
        clock=clock_name,
        jitter_sigma_s=jitter_sigma_s,
        harness_overhead_s=harness_overhead_s,
        seed=seed,
        notes=[
            "Response latency is agent_audio_first_byte.ts - caller_utterance.ts, "
            "recovered from the trace by lab.voice.calibration."
            "recover_response_latencies() — the same function real evaluations use.",
            "Boundary timestamps are captured as bare floats; the TraceEvents "
            "carrying them are constructed after the window closes and back-dated, "
            "so the harness never charges its own compute to the agent.",
            f"{harness_overhead_s * 1000:.0f} ms of artificial harness compute is "
            "injected per turn and must not move the recovered figure. The naive "
            "control table shows what including it would have cost.",
            "The control's error is a near-constant additive offset, so its "
            "relative error shrinks as the delay grows and it can pass the gate "
            "at the long end while failing badly at the short end. That is the "
            "reason the sweep spans an order of magnitude: a single-delay "
            "calibration at 2 s would have certified this broken method.",
            "Percentiles are linearly interpolated (numpy's default method), "
            "implemented in the standard library so the gate never needs an "
            "optional dependency.",
            "Under FakeClock the ground truth is exact and the run is "
            "deterministic and offline; under MonotonicClock the nominal delay is "
            "time.sleep(), whose own scheduling noise is a few milliseconds and "
            "can approach the tolerance at the shortest delay.",
        ],
    )

    return (report, traces) if collect_traces else report


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #


def write_calibration_artifacts(
    report: CalibrationReport,
    out_dir: str | Path = "fixtures",
    *,
    sample_trace: Trace | None = None,
) -> dict[str, Path]:
    """Write the report as JSON and markdown, plus an optional evidence trace.

    The JSON keeps every individual sample, not just the aggregates, so a reader
    can recompute the mean and the percentiles and check the arithmetic. A
    summary that cannot be recomputed is a claim, not a result.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}

    json_path = directory / "calibration_report.json"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    written["json"] = json_path

    md_path = directory / "calibration_report.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    written["markdown"] = md_path

    if sample_trace is not None:
        written["trace"] = write_jsonl(
            sample_trace, directory / "calibration_sample_trace.jsonl"
        )

    return written


# --------------------------------------------------------------------------- #
# CLI — `make calibrate`
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lab.voice.calibration",
        description=(
            "Run the timing calibration gate: recover known agent delays from "
            "traces and score the harness against a stated tolerance."
        ),
    )
    parser.add_argument(
        "--out",
        default="fixtures",
        help="Directory for calibration_report.json / .md (default: fixtures).",
    )
    parser.add_argument(
        "--repeats", type=int, default=DEFAULT_REPEATS, help="Turns per delay."
    )
    parser.add_argument(
        "--delays",
        default=",".join(str(d) for d in DEFAULT_DELAYS_S),
        help="Comma-separated nominal delays in seconds.",
    )
    parser.add_argument(
        "--clock",
        choices=("fake", "real"),
        default="fake",
        help=(
            "'fake' (default) is the gate: exact ground truth, deterministic, "
            "instant. 'real' additionally exercises OS scheduling and is expected "
            "to be noisier at short delays."
        ),
    )
    parser.add_argument(
        "--max-rel-error",
        type=float,
        default=CalibrationTolerance().max_rel_error,
        help="Tolerance on |relative error| as a fraction.",
    )
    parser.add_argument(
        "--max-stdev-ms",
        type=float,
        default=CalibrationTolerance().max_stdev_s * 1000,
        help="Tolerance on sample standard deviation, milliseconds.",
    )
    parser.add_argument(
        "--overhead-ms",
        type=float,
        default=DEFAULT_HARNESS_OVERHEAD_S * 1000,
        help="Artificial harness compute injected per turn, milliseconds.",
    )
    parser.add_argument(
        "--jitter-ms",
        type=float,
        default=DEFAULT_JITTER_SIGMA_S * 1000,
        help="Sigma of simulated engine jitter, milliseconds.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--no-write", action="store_true", help="Print the report without writing files."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate and write its artifacts. Exit code 0 on PASS, 1 on FAIL.

    A non-zero exit on FAIL is the point: the gate is meant to be wired into CI
    ahead of anything that reports a latency, so an untrustworthy stopwatch stops
    the pipeline instead of quietly publishing decoration.
    """
    args = _build_parser().parse_args(argv)

    delays = tuple(float(part) for part in args.delays.split(",") if part.strip())
    clock_factory: Callable[[], Clock] = (
        FakeClock if args.clock == "fake" else MonotonicClock
    )

    result = run_calibration(
        delays_s=delays,
        repeats=args.repeats,
        clock_factory=clock_factory,
        tolerance=CalibrationTolerance(
            max_rel_error=args.max_rel_error,
            max_stdev_s=args.max_stdev_ms / 1000.0,
        ),
        jitter_sigma_s=args.jitter_ms / 1000.0,
        harness_overhead_s=args.overhead_ms / 1000.0,
        seed=args.seed,
        collect_traces=True,
    )
    assert isinstance(result, tuple)  # collect_traces=True
    report, traces = result

    print(report.to_text())

    if not args.no_write:
        # The middle delay's trace is kept as evidence: a reader can open it and
        # recompute a latency by hand from two timestamps.
        sample = traces[len(traces) // 2] if traces else None
        written = write_calibration_artifacts(report, args.out, sample_trace=sample)
        print()
        for label, path in written.items():
            print(f"  wrote {label}: {path}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
