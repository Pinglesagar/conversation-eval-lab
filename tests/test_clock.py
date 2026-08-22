"""Tests for the injectable clocks.

WHAT THIS DEMONSTRATES
----------------------
Small module, but every timing number in the repo is a subtraction over two
values it produced, so its properties are worth pinning: monotonicity, an origin
at construction, and a `sleep` that means "advance time" in both real and virtual
implementations so the code under measurement never needs to know which it is in.
"""

from __future__ import annotations

import time

import pytest

from lab.clock import Clock, FakeClock, MonotonicClock


def test_fake_clock_starts_at_zero_and_only_moves_when_told() -> None:
    clock = FakeClock()
    assert clock.now() == 0.0
    assert clock.now() == 0.0  # reading the time does not advance it
    clock.advance(1.5)
    assert clock.now() == 1.5


def test_fake_clock_sleep_and_advance_are_the_same_operation() -> None:
    """Two names for one thing: the harness *advances* simulated overhead, the
    code under measurement *sleeps*. Same clock, same effect."""
    a, b = FakeClock(), FakeClock()
    a.advance(0.25)
    b.sleep(0.25)
    assert a.now() == b.now() == 0.25


def test_fake_clock_accepts_a_start_offset() -> None:
    assert FakeClock(start=10.0).now() == 10.0


def test_fake_clock_refuses_to_go_backwards() -> None:
    """A monotonic clock that can be rewound is not a monotonic clock, and a
    negative duration is the shape of a bug that should surface immediately."""
    clock = FakeClock()
    with pytest.raises(ValueError):
        clock.advance(-1.0)
    assert clock.now() == 0.0


def test_fake_clock_advance_returns_the_new_time() -> None:
    clock = FakeClock()
    assert clock.advance(0.5) == 0.5
    assert clock.advance(0.5) == 1.0


def test_monotonic_clock_is_zeroed_at_construction() -> None:
    """Trace timestamps are seconds since session start, so t=0 is the clock's
    birth rather than an epoch."""
    clock = MonotonicClock()
    assert 0.0 <= clock.now() < 1.0


def test_monotonic_clock_never_decreases() -> None:
    clock = MonotonicClock()
    readings = [clock.now() for _ in range(200)]
    assert all(a <= b for a, b in zip(readings, readings[1:], strict=False))


def test_monotonic_clock_sleep_advances_at_least_the_requested_time() -> None:
    clock = MonotonicClock()
    before = clock.now()
    clock.sleep(0.01)
    assert clock.now() - before >= 0.01


def test_monotonic_clock_ignores_a_non_positive_sleep() -> None:
    clock = MonotonicClock()
    start = time.perf_counter()
    clock.sleep(0.0)
    clock.sleep(-1.0)
    assert time.perf_counter() - start < 0.05


def test_monotonic_clock_is_not_wall_clock() -> None:
    """`time.time()` can step backwards under an NTP correction, and a duration
    computed across such a step is silently wrong. `perf_counter` cannot."""
    clock = MonotonicClock()
    assert clock.now() < time.time() / 2


@pytest.mark.parametrize("factory", [FakeClock, MonotonicClock])
def test_both_implementations_satisfy_the_protocol(factory: type) -> None:
    """Anything taking a `Clock` must accept either without special-casing."""
    clock = factory()
    assert isinstance(clock, Clock)
    assert isinstance(clock.now(), float)
    clock.sleep(0.0)


@pytest.mark.parametrize("factory", [FakeClock, MonotonicClock])
def test_repr_shows_the_current_time(factory: type) -> None:
    assert "now=" in repr(factory())
