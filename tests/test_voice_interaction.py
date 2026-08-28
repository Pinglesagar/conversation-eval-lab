"""Silence misattribution and barge-in: the two failures text cannot reach.

These run on the **real committed clips** from `fixtures/audio/tts_cache/`, not on
synthetic tones. That matters for the silence rows in particular: a pause measured
in a sine wave proves the arithmetic, and a pause measured in real synthesised
speech proves the arithmetic survives breath, sibilance and the decay at the end
of a sentence — which is where an energy threshold would go wrong if it were going
to. The clips cost characters once and are free forever after, so the honest
version of this test is also the cheap one.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from lab.trace.build import TraceBuilder
from lab.voice.engines.base import DEFAULT_SAMPLE_RATE
from lab.voice.engines.clipcache import ClipCache, clip_cache_key
from lab.voice.engines.elevenlabs_tts import DEFAULT_AGENT_VOICE, DEFAULT_CALLER_VOICE
from lab.voice.interaction import (
    DEFAULT_AWAY_TIMEOUT_S,
    PRODUCTION_AWAY_TIMEOUT_S,
    attribute_silence,
    barge_in,
    barge_in_report,
    emit_barge_in,
    insert_pause,
    pause_for_silence,
    speech_activity,
)

REPO = Path(__file__).resolve().parents[1]
CLOUD = REPO / "fixtures" / "audio" / "cloud"
COMMITTED_CACHE = REPO / "fixtures" / "audio" / "tts_cache"


# --------------------------------------------------------------------------- #
# Real clips
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def evidence() -> dict:
    return json.loads((CLOUD / "round_trip_evidence.json").read_text(encoding="utf-8"))


def _clip(row: dict) -> tuple[np.ndarray, int]:
    """The committed clip for an evidence row, straight out of the digest cache."""
    cache = ClipCache(committed=COMMITTED_CACHE, scratch=COMMITTED_CACHE)
    key = clip_cache_key(
        text=row["sent_text"],
        voice=row["voice"],
        model=row["model"],
        output_format="pcm_16000",
        normalisation="on",
    )
    entry = cache.get(key)
    assert entry is not None, (
        f"no committed clip for {row['row_id']}; the cache and the evidence have "
        "drifted apart, so run scripts/make_cloud_fixtures.py"
    )
    return entry.audio, entry.sample_rate


@pytest.fixture(scope="module")
def caller_clip(evidence: dict) -> tuple[np.ndarray, int]:
    row = next(r for r in evidence["rows"] if r["row_id"] == "advisory-drawdown")
    return _clip(row)


@pytest.fixture(scope="module")
def agent_clip(evidence: dict) -> tuple[np.ndarray, int]:
    row = next(r for r in evidence["rows"] if r["row_id"] == "agent-confirmation")
    return _clip(row)


def test_the_committed_clips_are_present_and_are_real_speech(
    caller_clip: tuple[np.ndarray, int], agent_clip: tuple[np.ndarray, int]
) -> None:
    """A fresh clone must find these, or every test below is vacuous."""
    for audio, rate in (caller_clip, agent_clip):
        assert rate == DEFAULT_SAMPLE_RATE
        assert audio.size > rate  # over a second
        activity = speech_activity(audio, sample_rate=rate)
        assert activity.has_speech
        # Real speech, not a tone: mostly voiced, with some gaps between words.
        assert 0.5 < activity.speech_s / activity.duration_s < 1.0


def test_the_caller_and_agent_clips_are_different_voices(evidence: dict) -> None:
    caller = next(r for r in evidence["rows"] if r["row_id"] == "advisory-drawdown")
    agent = next(r for r in evidence["rows"] if r["row_id"] == "agent-confirmation")
    assert caller["voice"] == DEFAULT_CALLER_VOICE
    assert agent["voice"] == DEFAULT_AGENT_VOICE
    assert caller["voice"] != agent["voice"]


# --------------------------------------------------------------------------- #
# The instrument checks itself first
# --------------------------------------------------------------------------- #


def test_the_committed_clips_carry_their_own_trailing_silence(
    caller_clip: tuple[np.ndarray, int], agent_clip: tuple[np.ndarray, int]
) -> None:
    """The measured fact that makes `pause_for_silence` necessary.

    ElevenLabs ends a clip with roughly 200 ms of silence. Appending a declared
    5.9 second pause to that gives a 6.1 second silent run, which is over the
    6 second production threshold — so a naive threshold test would fail, and the
    tempting fix (widen the tolerance) would destroy the test's ability to see the
    boundary it exists to check.
    """
    for audio, rate in (caller_clip, agent_clip):
        activity = speech_activity(audio, sample_rate=rate)
        assert 0.05 < activity.trailing_silence_s < 0.5


@pytest.mark.parametrize("target_s", [1.0, 3.0, 5.9, 6.0, 8.0, 12.0])
def test_a_target_silence_is_reached_exactly_in_real_speech(
    caller_clip: tuple[np.ndarray, int], target_s: float
) -> None:
    """Declared in, measured out, within two analysis frames.

    This has to hold before any threshold claim means anything: if a target of
    5.9 seconds measured as 6.1, the timeout assertions would be testing the
    padding arithmetic instead of the timeout. Same argument as
    `lab/voice/calibration.py` makes for latency — prove the instrument recovers a
    known quantity, then use it.
    """
    audio, rate = caller_clip
    padded, appended = pause_for_silence(audio, target_silence_s=target_s, sample_rate=rate)
    activity = speech_activity(padded, sample_rate=rate)
    assert activity.longest_silence_s == pytest.approx(target_s, abs=0.04)
    # And the pause appended is *less* than the target, by the clip's own tail.
    assert appended < target_s
    assert padded.size == audio.size + int(round(appended * rate))


@pytest.mark.parametrize("pause_s", [1.0, 3.0, 8.0])
def test_insert_pause_appends_exactly_what_it_was_asked_for(
    caller_clip: tuple[np.ndarray, int], pause_s: float
) -> None:
    """The lower-level primitive still does the simple thing, and says so."""
    audio, rate = caller_clip
    padded = insert_pause(audio, seconds=pause_s, sample_rate=rate)
    assert padded.size == audio.size + int(round(pause_s * rate))


def test_the_pause_lands_where_it_was_asked_for(caller_clip: tuple[np.ndarray, int]) -> None:
    audio, rate = caller_clip
    padded = insert_pause(audio, seconds=7.0, at_s=0.5, sample_rate=rate)
    activity = speech_activity(padded, sample_rate=rate)
    assert activity.longest_silence_start_s == pytest.approx(0.5, abs=0.06)


def test_a_negative_pause_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        insert_pause(np.zeros(100), seconds=-1.0)


def test_real_speech_alone_never_reaches_the_timeout(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """The control. Without an injected pause there is no long silent run."""
    audio, rate = caller_clip
    activity = speech_activity(audio, sample_rate=rate)
    assert activity.longest_silence_s < PRODUCTION_AWAY_TIMEOUT_S


# --------------------------------------------------------------------------- #
# Reference bug: the silence that was not silence
# --------------------------------------------------------------------------- #


def test_the_timeout_fires_at_the_threshold_and_not_before(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """Both halves of the threshold claim: 5.9 s does not fire, 6.0 s does."""
    audio, rate = caller_clip
    under_clip, _ = pause_for_silence(audio, target_silence_s=5.9, sample_rate=rate)
    just_under = attribute_silence(
        under_clip,
        threshold_s=PRODUCTION_AWAY_TIMEOUT_S,
        declared_pause_s=5.9,
        sample_rate=rate,
    )
    assert just_under.fires is False
    assert just_under.verdict == "would_not_fire"

    over_clip, _ = pause_for_silence(audio, target_silence_s=6.1, sample_rate=rate)
    just_over = attribute_silence(
        over_clip,
        threshold_s=PRODUCTION_AWAY_TIMEOUT_S,
        declared_pause_s=6.1,
        sample_rate=rate,
    )
    assert just_over.fires is True
    assert just_over.verdict == "caller_silent"


def test_the_measurement_is_checked_against_the_declared_pause(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    audio, rate = caller_clip
    padded, _ = pause_for_silence(audio, target_silence_s=8.0, sample_rate=rate)
    result = attribute_silence(padded, declared_pause_s=8.0, sample_rate=rate)
    assert result.declared_matches_measured is True


def test_a_wrongly_declared_pause_is_caught(caller_clip: tuple[np.ndarray, int]) -> None:
    """The self-check has to be able to fail, or it is decoration."""
    audio, rate = caller_clip
    padded, _ = pause_for_silence(audio, target_silence_s=8.0, sample_rate=rate)
    result = attribute_silence(padded, declared_pause_s=3.0, sample_rate=rate)  # a lie
    assert result.declared_matches_measured is False


def test_a_genuine_pause_is_attributed_to_the_caller(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """Verdict one: the label was right, and the remedy is the timeout."""
    audio, rate = caller_clip
    padded, _ = pause_for_silence(audio, target_silence_s=8.0, sample_rate=rate)
    result = attribute_silence(
        padded,
        threshold_s=PRODUCTION_AWAY_TIMEOUT_S,
        declared_pause_s=8.0,
        sample_rate=rate,
    )
    assert result.verdict == "caller_silent"
    assert result.reason_is_accurate is True
    assert "raise the timeout" in result.describe()


def test_a_vad_driven_timeout_is_attributed_to_the_turn_detector(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """Verdict two, and the reference bug itself.

    The clip has **no long pause at all** — the caller talks throughout — and the
    timeout fires anyway, because the detector said the user was away. The label
    `silence-timed-out` is then a false statement about a call in which nobody was
    silent, and the remedy is the turn detector rather than the timer.

    This is the row a text harness cannot have. There is no silence in a
    transcript, so there is nothing to disagree with the label about.
    """
    audio, rate = caller_clip
    result = attribute_silence(
        audio,
        threshold_s=PRODUCTION_AWAY_TIMEOUT_S,
        sample_rate=rate,
        speech_during_timeout=True,
    )
    assert result.fires is True
    assert result.verdict == "vad_false_silence"
    assert result.reason_is_accurate is False
    assert result.measured_silence_s < PRODUCTION_AWAY_TIMEOUT_S
    assert "fix turn detection" in result.describe()
    assert "postpones" in result.describe()


def test_the_two_verdicts_are_distinguishable_at_the_same_label(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """The whole point: one production label, two causes, opposite remedies."""
    audio, rate = caller_clip
    padded, _ = pause_for_silence(audio, target_silence_s=8.0, sample_rate=rate)
    genuine = attribute_silence(
        padded, threshold_s=PRODUCTION_AWAY_TIMEOUT_S, sample_rate=rate
    )
    false_alarm = attribute_silence(
        audio,
        threshold_s=PRODUCTION_AWAY_TIMEOUT_S,
        sample_rate=rate,
        speech_during_timeout=True,
    )
    # Same outcome, same label, different truth.
    assert genuine.fires is false_alarm.fires is True
    assert genuine.reason_label == false_alarm.reason_label == "silence-timed-out"
    assert genuine.reason_is_accurate is not false_alarm.reason_is_accurate
    assert genuine.verdict != false_alarm.verdict


def test_the_shipped_timeout_trips_an_ordinary_thinking_pause(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """The second half of the production cause: 6 seconds was too aggressive.

    A 7 second pause is a long but entirely ordinary hesitation from somebody
    looking up an account number. At the shipped 6 seconds the call ends; at a less
    aggressive threshold it does not. Both verdicts are computed from the same
    audio, so the difference is the setting and nothing else.
    """
    audio, rate = caller_clip
    padded, _ = pause_for_silence(audio, target_silence_s=7.0, sample_rate=rate)
    shipped = attribute_silence(
        padded, threshold_s=PRODUCTION_AWAY_TIMEOUT_S, sample_rate=rate
    )
    relaxed = attribute_silence(padded, threshold_s=DEFAULT_AWAY_TIMEOUT_S, sample_rate=rate)
    assert shipped.fires is True
    assert relaxed.fires is False


def test_silence_on_an_empty_clip_is_zero_rather_than_an_error() -> None:
    activity = speech_activity(np.array([], dtype=np.float64))
    assert activity.duration_s == 0.0
    assert activity.longest_silence_s == 0.0
    assert activity.has_speech is False


def test_scattered_gaps_do_not_add_up_to_a_timeout(
    caller_clip: tuple[np.ndarray, int]
) -> None:
    """The longest *run* is the quantity, not the total.

    Eight one-second pauses total eight seconds of silence and trip nothing, which
    is correct: a caller pausing between every word is not an absent caller. A
    harness comparing totals against the threshold would end that call.
    """
    audio, rate = caller_clip
    scattered = audio
    # Each insert lengthens the clip by 1.0 s, so the next position has to advance
    # by more than that or the pauses merge into one long run — which is exactly
    # the mistake this test is about, made by the test itself.
    for index in range(8):
        scattered = insert_pause(
            scattered, seconds=1.0, at_s=0.25 + index * 1.3, sample_rate=rate
        )
    activity = speech_activity(scattered, sample_rate=rate)
    result = attribute_silence(
        scattered, threshold_s=PRODUCTION_AWAY_TIMEOUT_S, sample_rate=rate
    )
    assert activity.silence_s > PRODUCTION_AWAY_TIMEOUT_S  # plenty of total silence
    assert result.fires is False  # and no single run reaches the threshold


# --------------------------------------------------------------------------- #
# Reference bug: the interruption events nobody used
# --------------------------------------------------------------------------- #


def test_a_barge_in_is_measured_from_the_two_real_clip_durations(
    caller_clip: tuple[np.ndarray, int], agent_clip: tuple[np.ndarray, int]
) -> None:
    """Real durations, so the overlap is a real quantity rather than a chosen one."""
    caller_audio, rate = caller_clip
    agent_audio, _ = agent_clip
    agent_duration = agent_audio.size / rate
    # The caller cuts in one third of the way through the agent's reply.
    cut_in = agent_duration / 3.0
    event = barge_in(
        agent_started_s=0.0,
        agent_duration_s=agent_duration,
        caller_started_s=cut_in,
        agent_stopped_s=cut_in + 0.18,
    )
    assert event.yielded is True
    assert event.latency_s == pytest.approx(0.18)
    assert event.overlap_s == pytest.approx(0.18)
    assert caller_audio.size > 0


def test_an_agent_that_never_stops_has_no_latency_and_is_flagged(
    agent_clip: tuple[np.ndarray, int]
) -> None:
    """None, not a large number. A non-yield is not a slow yield."""
    agent_audio, rate = agent_clip
    duration = agent_audio.size / rate
    event = barge_in(agent_started_s=0.0, agent_duration_s=duration, caller_started_s=0.5)
    assert event.yielded is False
    assert event.latency_s is None
    assert event.overlap_s == pytest.approx(duration - 0.5)
    assert "did NOT yield" in event.describe()
    assert "talked over the caller" in event.describe()


def test_an_agent_that_merely_finished_is_not_counted_as_yielding(
    agent_clip: tuple[np.ndarray, int]
) -> None:
    """Stopping because the clip ended is not a response to the interruption."""
    agent_audio, rate = agent_clip
    duration = agent_audio.size / rate
    event = barge_in(
        agent_started_s=0.0,
        agent_duration_s=duration,
        caller_started_s=duration - 0.05,
        agent_stopped_s=duration,
    )
    assert event.yielded is False
    assert event.latency_s == pytest.approx(0.05)


def test_a_turn_that_does_not_overlap_is_refused() -> None:
    """Recording a normal turn as a barge-in would populate the metric with non-events."""
    with pytest.raises(ValueError, match="not a barge-in"):
        barge_in(agent_started_s=0.0, agent_duration_s=2.0, caller_started_s=2.5)
    with pytest.raises(ValueError, match="not a barge-in"):
        barge_in(agent_started_s=1.0, agent_duration_s=2.0, caller_started_s=0.5)


# --------------------------------------------------------------------------- #
# The events reach a trace, and something reads them
# --------------------------------------------------------------------------- #


def test_the_interruption_events_are_emitted_and_consumed() -> None:
    """The gap this closed: defined in the schema, emitted by nothing, read by nothing."""
    builder = TraceBuilder(scenario_id="barge", adapter="voice:audio", session_id="b1")
    builder.session_start(latency_gate="PASS")
    yielded = barge_in(
        agent_started_s=0.0, agent_duration_s=3.0, caller_started_s=1.0, agent_stopped_s=1.2
    )
    emit_barge_in(builder, yielded, turn=1, engine="tts:probe")
    trace = builder.build()

    kinds = [event.kind for event in trace.events]
    assert "interruption_started" in kinds
    assert "interruption_acknowledged" in kinds

    report = barge_in_report(trace)
    assert report.interruptions == 1
    assert report.yields == 1
    assert report.yield_rate == 1.0
    assert report.latencies_s == [pytest.approx(0.2)]


def test_the_emitted_payloads_match_the_schemas_contract() -> None:
    """Two live emitters, so the schema must describe what they write.

    `PAYLOAD_KEYS` is the repo's payload contract. A kind that is emitted but
    absent from it — or present in it with keys the emitter does not write —
    would leave a downstream check written against a key that is never there.
    """
    from lab.trace.schema import PAYLOAD_KEYS

    builder = TraceBuilder(scenario_id="barge", adapter="voice:audio", session_id="b6")
    emit_barge_in(
        builder,
        barge_in(
            agent_started_s=0.0, agent_duration_s=3.0, caller_started_s=1.0, agent_stopped_s=1.2
        ),
        turn=1,
    )
    for event in builder.build().events:
        assert event.kind in PAYLOAD_KEYS, f"{event.kind} is emitted but has no payload contract"
        assert set(PAYLOAD_KEYS[event.kind]) <= set(event.payload), event.kind


def test_no_acknowledgement_is_emitted_when_the_agent_did_not_yield() -> None:
    """An acknowledgement for a yield that never happened would be a lie in the trace."""
    builder = TraceBuilder(scenario_id="barge", adapter="voice:audio", session_id="b2")
    builder.session_start(latency_gate="PASS")
    emit_barge_in(
        builder,
        barge_in(agent_started_s=0.0, agent_duration_s=3.0, caller_started_s=1.0),
        turn=1,
    )
    trace = builder.build()
    kinds = [event.kind for event in trace.events]
    assert "interruption_started" in kinds
    assert "interruption_acknowledged" not in kinds

    report = barge_in_report(trace)
    assert report.interruptions == 1
    assert report.yields == 0
    assert report.yield_rate == 0.0
    assert report.talked_over_turns == [1]
    assert "talked over the caller" in report.describe()


def test_an_ignored_interruption_is_not_paired_with_a_later_one() -> None:
    """Why pairing is by turn and position, not by zipping the two streams.

    Turn 1 is ignored and turn 2 yields. A zip would marry turn 1's start to turn
    2's acknowledgement and report a confident latency for the interruption that
    was talked straight through — turning the one real failure in the trace into a
    slightly slow success.
    """
    builder = TraceBuilder(scenario_id="barge", adapter="voice:audio", session_id="b3")
    builder.session_start(latency_gate="PASS")
    emit_barge_in(
        builder,
        barge_in(agent_started_s=0.0, agent_duration_s=3.0, caller_started_s=1.0),
        turn=1,
    )
    emit_barge_in(
        builder,
        barge_in(
            agent_started_s=10.0,
            agent_duration_s=3.0,
            caller_started_s=11.0,
            agent_stopped_s=11.15,
        ),
        turn=2,
    )
    report = barge_in_report(builder.build())
    assert report.interruptions == 2
    assert report.yields == 1
    assert report.yield_rate == 0.5
    assert report.talked_over_turns == [1]
    assert report.latencies_s == [pytest.approx(0.15)]


def test_the_yield_rate_carries_its_denominator() -> None:
    builder = TraceBuilder(scenario_id="barge", adapter="voice:audio", session_id="b4")
    builder.session_start(latency_gate="PASS")
    report = barge_in_report(builder.build())
    assert report.interruptions == 0
    assert report.yield_rate is None
    assert report.median_latency_s is None
    assert "no interruptions" in report.describe()


def test_the_median_latency_is_reported_over_several_interruptions() -> None:
    builder = TraceBuilder(scenario_id="barge", adapter="voice:audio", session_id="b5")
    builder.session_start(latency_gate="PASS")
    for turn, (start, stop) in enumerate(
        [(1.0, 1.1), (11.0, 11.3), (21.0, 21.2)], start=1
    ):
        emit_barge_in(
            builder,
            barge_in(
                agent_started_s=start - 1.0,
                agent_duration_s=3.0,
                caller_started_s=start,
                agent_stopped_s=stop,
            ),
            turn=turn,
        )
    report = barge_in_report(builder.build())
    assert report.interruptions == 3
    assert report.yields == 3
    assert report.median_latency_s == pytest.approx(0.2)
