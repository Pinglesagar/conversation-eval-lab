"""Tests for the trace schema and its read helpers.

WHAT THIS DEMONSTRATES
----------------------
The helpers on `Trace` are the shared vocabulary every check will be written
against, so their edge cases are pinned down here rather than rediscovered
independently by each consumer. The pairing semantics of `event_pairs` get the
most attention: it is the primitive under every latency figure in the repo, and
its behaviour on an unanswered turn is the difference between a dropped sample
and an invented one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import PAYLOAD_KEYS, EventKind, Trace, TraceEvent


def _event(ts: float, kind: str, actor: str = "system", **payload: object) -> TraceEvent:
    return TraceEvent(ts=ts, kind=kind, actor=actor, payload=payload)  # type: ignore[arg-type]


def _trace(*events: TraceEvent) -> Trace:
    return Trace(session_id="s", scenario_id="sc", adapter="test", events=list(events))


# --------------------------------------------------------------------------- #
# TraceEvent
# --------------------------------------------------------------------------- #


def test_event_requires_ts_kind_and_actor() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(kind="session_start", actor="system")  # type: ignore[call-arg]


def test_actor_is_a_closed_set() -> None:
    """Three actors, and a typo must not silently become a fourth."""
    with pytest.raises(ValidationError):
        TraceEvent(ts=0.0, kind="session_start", actor="assistant")  # type: ignore[arg-type]


def test_unexpected_top_level_field_is_rejected() -> None:
    """`extra="forbid"`: a misspelled field belongs in `payload` or nowhere.

    Silently accepting `timestamp=` next to `ts=` is how a field goes missing
    from every downstream calculation without anything failing.
    """
    with pytest.raises(ValidationError):
        TraceEvent(ts=0.0, kind="x", actor="system", timestamp=1.0)  # type: ignore[call-arg]


def test_kind_stays_open_for_forward_compatibility() -> None:
    """An unknown kind loads, and is reportable rather than fatal."""
    event = _event(0.0, "some_future_kind")
    assert event.is_known_kind is False
    assert _trace(event).unknown_kinds() == {"some_future_kind"}


def test_v2_kinds_are_declared_but_not_emitted() -> None:
    """Barge-in events are reserved, not implemented — see the schema docstring.

    They must be recognised by the vocabulary (so a v2 adapter needs no schema
    migration) while nothing in v1 emits them. Anchoring that here stops a check
    being written today against a metric this version cannot measure.
    """
    assert EventKind.V2_RESERVED == {
        "interruption_started",
        "interruption_acknowledged",
    }
    assert EventKind.V2_RESERVED.isdisjoint(EventKind.KNOWN)

    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    emitters = [
        name
        for name in dir(builder)
        if not name.startswith("_") and "interruption" in name
    ]
    assert emitters == [], "v1 must have no way to emit a barge-in event"


def test_payload_keys_documents_every_v1_kind() -> None:
    assert set(PAYLOAD_KEYS) == EventKind.KNOWN


def test_event_get_reads_the_payload() -> None:
    event = _event(0.0, EventKind.TOOL_CALL, "agent", name="search_tables")
    assert event.get("name") == "search_tables"
    assert event.get("missing", "fallback") == "fallback"


# --------------------------------------------------------------------------- #
# Trace helpers
# --------------------------------------------------------------------------- #


def test_tool_names_preserves_order_and_repeats() -> None:
    trace = _trace(
        _event(0.0, EventKind.TOOL_CALL, "agent", name="search_tables"),
        _event(1.0, EventKind.TOOL_RESULT, "system", name="search_tables"),
        _event(2.0, EventKind.TOOL_CALL, "agent", name="search_tables"),
        _event(3.0, EventKind.TOOL_CALL, "agent", name="create_booking"),
    )
    assert trace.tool_names() == ["search_tables", "search_tables", "create_booking"]


def test_tool_names_reads_calls_not_results() -> None:
    """Attempts and outcomes are separate facts.

    A check must be able to tell "never tried" from "tried and failed", so
    `tool_names()` reports what the agent attempted and says nothing about
    whether it worked.
    """
    trace = _trace(
        _event(0.0, EventKind.TOOL_CALL, "agent", name="create_booking"),
        _event(1.0, EventKind.TOOL_RESULT, "system", name="create_booking", ok=False),
    )
    assert trace.tool_names() == ["create_booking"]


def test_utterances_returns_events_in_order_from_both_actors() -> None:
    trace = _trace(
        _event(0.0, EventKind.SESSION_START),
        _event(1.0, EventKind.CALLER_UTTERANCE, "caller", text="hello"),
        _event(1.5, EventKind.TRANSCRIPT_OUT, "agent", text="hi"),
        _event(2.0, EventKind.AGENT_UTTERANCE, "agent", text="hi", agent="Greeter"),
    )
    utterances = trace.utterances()
    assert [e.actor for e in utterances] == ["caller", "agent"]
    assert trace.texts() == ["hello", "hi"]
    assert trace.texts(actor="caller") == ["hello"]


def test_handoffs_and_handoff_pairs() -> None:
    trace = _trace(
        _event(0.0, EventKind.AGENT_HANDOFF, **{"from": "Greeter", "to": "Booking"}),
        _event(1.0, EventKind.AGENT_HANDOFF, **{"from": "Booking", "to": "Policy"}),
    )
    assert len(trace.handoffs()) == 2
    assert trace.handoff_pairs() == [("Greeter", "Booking"), ("Booking", "Policy")]


def test_duration_is_a_subtraction_over_the_trace() -> None:
    """A property of the recorded events, so replay reproduces it exactly."""
    trace = _trace(_event(0.5, EventKind.SESSION_START), _event(4.25, EventKind.SESSION_END))
    assert trace.duration() == pytest.approx(3.75)


@pytest.mark.parametrize("count", [0, 1])
def test_duration_of_a_degenerate_trace_is_zero(count: int) -> None:
    events = [_event(2.0, EventKind.SESSION_START)][:count]
    assert _trace(*events).duration() == 0.0


def test_event_pairs_matches_each_opener_with_the_next_closer() -> None:
    trace = _trace(
        _event(0.0, EventKind.CALLER_UTTERANCE, "caller"),
        _event(0.4, EventKind.AGENT_AUDIO_FIRST_BYTE, "agent"),
        _event(1.0, EventKind.CALLER_UTTERANCE, "caller"),
        _event(1.9, EventKind.AGENT_AUDIO_FIRST_BYTE, "agent"),
    )
    pairs = trace.event_pairs(
        EventKind.CALLER_UTTERANCE, EventKind.AGENT_AUDIO_FIRST_BYTE
    )
    assert [round(b.ts - a.ts, 6) for a, b in pairs] == [0.4, 0.9]


def test_event_pairs_are_non_overlapping() -> None:
    """One closer cannot close two openers, so samples are never double counted."""
    trace = _trace(
        _event(0.0, EventKind.CALLER_UTTERANCE, "caller"),
        _event(0.5, EventKind.AGENT_AUDIO_FIRST_BYTE, "agent"),
        _event(0.6, EventKind.AGENT_AUDIO_FIRST_BYTE, "agent"),
    )
    assert len(trace.event_pairs("caller_utterance", "agent_audio_first_byte")) == 1


def test_event_pairs_drops_an_unanswered_opener() -> None:
    """The alternative — pairing across a dead turn — invents a latency.

    Here the caller spoke at 0.0 and got nothing, then spoke again at 5.0 and was
    answered at 5.2. The only real sample is 0.2 s. A naive pairing would report
    5.2 s, a number no turn ever had.
    """
    trace = _trace(
        _event(0.0, EventKind.CALLER_UTTERANCE, "caller"),
        _event(5.0, EventKind.CALLER_UTTERANCE, "caller"),
        _event(5.2, EventKind.AGENT_AUDIO_FIRST_BYTE, "agent"),
    )
    pairs = trace.event_pairs("caller_utterance", "agent_audio_first_byte")
    assert len(pairs) == 1
    assert pairs[0][0].ts == 5.0
    assert pairs[0][1].ts - pairs[0][0].ts == pytest.approx(0.2)


def test_event_pairs_on_an_empty_trace() -> None:
    assert _trace().event_pairs("a", "b") == []


def test_events_of_kind_first_and_last() -> None:
    trace = _trace(
        _event(0.0, EventKind.TOOL_CALL, "agent", name="a"),
        _event(1.0, EventKind.AGENT_UTTERANCE, "agent", text="x"),
        _event(2.0, EventKind.TOOL_CALL, "agent", name="b"),
    )
    assert len(trace.events_of_kind(EventKind.TOOL_CALL)) == 2
    assert len(trace.events_of_kind(EventKind.TOOL_CALL, EventKind.AGENT_UTTERANCE)) == 3
    assert trace.first(EventKind.TOOL_CALL) is not None
    assert trace.first(EventKind.TOOL_CALL).get("name") == "a"  # type: ignore[union-attr]
    assert trace.last(EventKind.TOOL_CALL).get("name") == "b"  # type: ignore[union-attr]
    assert trace.first(EventKind.SESSION_END) is None


def test_is_ordered_detects_a_broken_clock() -> None:
    assert _trace(_event(0.0, "a"), _event(1.0, "a")).is_ordered() is True
    assert _trace(_event(1.0, "a"), _event(0.0, "a")).is_ordered() is False


def test_trace_is_iterable_and_sized() -> None:
    trace = _trace(_event(0.0, "a"), _event(1.0, "b"))
    assert len(trace) == 2
    assert [e.kind for e in trace] == ["a", "b"]
