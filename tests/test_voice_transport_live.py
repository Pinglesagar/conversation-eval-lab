"""The transport tier against a real room. Opt-in, non-gating, and capped.

WHY THIS FILE IS SEPARATE AND WHY IT SKIPS
------------------------------------------
Everything here opens a real WebRTC session, which means it needs a credential, it
costs wall-clock seconds that cannot be compressed, and it can fail for reasons
that have nothing to do with this repository. So it is gated on
`$LAB_LIVE_TRANSPORT` and on the three LiveKit variables, and on a clean checkout
it skips with a message that names what is missing.

**This tier is non-gating in CI, and that is a design decision rather than an
omission.** A network test that blocks a merge teaches people to bypass the gate,
and a gate people bypass protects nothing. The offline suite
(`tests/test_voice_transport.py`) is what gates: it recomputes every figure the
tier reports from the committed recordings, so a regression in the *measurement*
fails a build with no key, while a regression in *somebody's wifi* does not.

WHAT THESE TESTS ARE FOR
------------------------
Not for the numbers. The numbers come from `scripts/make_transport_fixtures.py`,
are committed, and are asserted offline. These tests answer a different question:
does the live path still work at all — can it connect, publish, be subscribed to,
and produce a recording the offline measurements accept? That is the thing a
committed fixture cannot tell you, and it is exactly what silently rots.

Each test is capped. A hang becomes `TransportTimeout` naming the phase it gave up
in, because a test that hangs is worse than a test that fails.
"""

from __future__ import annotations

import os

import pytest

from lab.voice.calibration import run_calibration
from lab.voice.transport.measure import delivery_gap, lifecycle_observation

pytestmark = pytest.mark.live

REQUIRED = ("LAB_LIVE_TRANSPORT", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


def _transport_or_skip():
    """Skip with a message that names every blocker, not just the first."""
    session = pytest.importorskip(
        "lab.voice.transport.session", reason="needs the transport extra"
    )
    if not session.livekit_available():  # pragma: no cover - environment dependent
        pytest.skip(session.livekit_diagnosis())
    transport = session.LiveKitTransport()
    missing = transport.missing_requirements()
    if missing:
        pytest.skip(
            "live transport is not enabled; missing " + ", ".join(missing)
        )
    return session, transport


def _clip_or_skip(session):
    pytest.importorskip("soundfile", reason="decoding the committed clip needs soundfile")
    from pathlib import Path

    clip_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "audio"
        / "clips"
        / "agent-4133d85a3343.opus"
    )
    clip = session.load_clip_frames(clip_path)
    # The same guard the recorder applies, for the same reason: a clip that cannot
    # be segmented wastes the live session it was going to be measured in.
    session.require_segmentable(clip, threshold_rms=0.02, max_gap_ms=200.0)
    return clip


def test_the_environment_is_reported_before_anything_is_spent() -> None:
    """A describe() that says 'ready' is the precondition for the rest of this file."""
    _, transport = _transport_or_skip()
    described = transport.describe()
    assert "ready" in described
    for name in REQUIRED:
        assert os.environ.get(name), name
    # Names only, never values: this string ends up in logs. Checked against the
    # three credential-bearing variables and not against the opt-in flag, whose
    # value is "1" and appears inside the sample rate — a substring check on a
    # boolean tests nothing and fails on everything.
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        assert os.environ[name] not in described


def test_a_short_live_session_produces_a_measurable_delivery_gap() -> None:
    """Three turns, not twelve: this is a liveness check, not the measurement.

    The committed twelve-turn recording is where the reported figure comes from.
    What this asserts is that the live path still yields a recording the offline
    measurement accepts — connect, publish, subscribe, segment, pair.
    """
    session, transport = _transport_or_skip()
    clip = _clip_or_skip(session)
    recording = transport.record_delivery_gap(clip, turns=3)

    assert len(recording.utterances) == 3
    assert recording.arrivals.n > 100, "the receiver should have taken delivery of audio"
    assert recording.duration_s() < transport.session_cap_s

    gate = run_calibration()
    measurement = delivery_gap(recording, calibration=gate)
    assert measurement.reportable, measurement.refusal
    assert measurement.distribution is not None
    assert measurement.distribution.n == 3
    mean_ms = measurement.mean_ms
    assert mean_ms is not None
    # A wide band on purpose. The point is that the gap exists and is not zero —
    # an agent-side figure says zero by construction — not that a live network
    # hits a number.
    assert 5.0 < mean_ms < 1_000.0
    # p95 must still be refused at three samples.
    assert not measurement.distribution.quantile(0.95).reported


def test_a_live_drop_and_rejoin_loses_the_turn_it_was_carrying() -> None:
    """The characterised behaviour, checked against a real reconnect."""
    session, transport = _transport_or_skip()
    clip = _clip_or_skip(session)
    recording = transport.record_lifecycle(clip, drop_after_frames=30)

    observation = lifecycle_observation(recording, settle_s=transport.settle_s)
    assert observation.reportable, observation.refusal
    assert observation.verdict == "recovered-turn-lost"
    assert observation.attempts == 2
    assert observation.frames_pushed_before_drop == 30
    recovery = observation.transport_recovery_s
    assert recovery is not None and recovery > 0
    # Every lifecycle record carries the receiver's stream position, which is what
    # lets the verdict be reached without comparing two streams' timestamps.
    assert all(event.arrival_index is not None for event in recording.lifecycle)


def test_a_credential_never_reaches_the_recording() -> None:
    """The recording is committed to a public repository, so this is checked live."""
    session, transport = _transport_or_skip()
    clip = _clip_or_skip(session)
    recording = transport.record_delivery_gap(clip, turns=1)
    serialised = recording.model_dump_json()
    assert os.environ["LIVEKIT_URL"] not in serialised
    assert os.environ["LIVEKIT_API_KEY"] not in serialised
    assert os.environ["LIVEKIT_API_SECRET"] not in serialised
    assert len(recording.url_digest) == 12
