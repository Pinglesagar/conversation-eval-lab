"""Injectable clocks.

WHAT THIS DEMONSTRATES
----------------------
Latency claims are only as trustworthy as the clock behind them, and a clock you
cannot control is a clock you cannot test. Every timing measurement in this
harness reads its time from a `Clock` passed in by the caller, never from
`time.*` directly. That buys three things:

1.  **Determinism.** Tests drive the harness with `FakeClock`, where time only
    advances when something explicitly asks it to. The timing calibration gate
    (`lab.voice.calibration`) therefore produces byte-identical numbers on every
    machine, offline, with no sleeping.
2.  **Correct monotonicity.** `MonotonicClock` is built on `time.perf_counter`,
    not `time.time`. Wall-clock time can step backwards (NTP correction, DST);
    a duration computed across such a step is silently wrong.
3.  **A single definition of t=0.** Trace timestamps are "monotonic seconds from
    session start", so the clock is zeroed when it is created and every event in
    a trace is relative to the same origin.

`Clock.sleep()` exists so that code under measurement (for example the mock
agents used to calibrate the harness) can wait without knowing whether it is
running in real time or in virtual time. Under `MonotonicClock` it really
sleeps; under `FakeClock` it advances virtual time instantly. Same code path,
same trace, no `time.sleep` in the test suite.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "MonotonicClock", "FakeClock"]


@runtime_checkable
class Clock(Protocol):
    """A source of monotonic seconds, measured from the clock's own creation."""

    def now(self) -> float:
        """Seconds elapsed since this clock's origin. Never decreases."""
        ...

    def sleep(self, seconds: float) -> None:
        """Advance time by `seconds`, really or virtually."""
        ...


class MonotonicClock:
    """A real clock, zeroed at construction, backed by `time.perf_counter`.

    Use this in production runs. `perf_counter` is the highest-resolution
    monotonic counter Python exposes and is immune to wall-clock adjustments.
    """

    def __init__(self) -> None:
        self._origin: float = time.perf_counter()

    def now(self) -> float:
        return time.perf_counter() - self._origin

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def __repr__(self) -> str:
        return f"MonotonicClock(now={self.now():.6f}s)"


class FakeClock:
    """A virtual clock that moves only when told to.

    Time advances via `advance()` or `sleep()` (they are the same operation seen
    from two sides: the harness *advances* simulated overhead, the code under
    measurement *sleeps*). Because nothing else moves the clock, a measurement
    taken with a `FakeClock` is exactly reproducible.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t: float = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        """Move virtual time forward. Returns the new value of `now()`."""
        if seconds < 0:
            raise ValueError(f"a monotonic clock cannot go backwards (got {seconds!r})")
        self._t += float(seconds)
        return self._t

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def __repr__(self) -> str:
        return f"FakeClock(now={self._t:.6f}s)"
