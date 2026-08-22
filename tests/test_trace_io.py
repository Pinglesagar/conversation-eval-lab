"""Tests for the JSONL codec and the TraceBuilder.

WHAT THIS DEMONSTRATES
----------------------
Round-trip safety is the load-bearing property here. Recorded fixtures are what
let this repo's live paths replay with no API key, so if serialisation dropped a
field, every offline test in the project would still be green while checking
something other than what was recorded. That is the worst available failure mode
for an evaluation harness, so it gets an explicit test on a trace that exercises
every v1 event kind rather than a convenient two-event sample.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.io import iter_jsonl, read_jsonl, read_jsonl_events, write_jsonl
from lab.trace.schema import EventKind, Trace


def _full_trace() -> Trace:
    """A trace exercising every v1 event kind, with a fake clock for exact ts."""
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id="booking/large_party",
        adapter="text",
        session_id="sess-001",
        clock=clock,
    )
    builder.session_start()
    clock.advance(0.1)
    builder.transcript_in("table for six at seven", confidence=0.93, engine="stt-a")
    builder.caller_utterance("table for six at seven")
    clock.advance(0.2)
    call = builder.tool_call("search_tables", {"party_size": 6, "time": "19:00"})
    clock.advance(0.05)
    builder.tool_result(
        "search_tables", {"available": True}, call_id=call.get("call_id"), ok=True
    )
    clock.advance(0.05)
    builder.agent_handoff("GreeterAgent", "BookingAgent", reason="booking intent")
    clock.advance(0.1)
    builder.transcript_out("that is booked for you", engine="tts-b")
    builder.agent_audio_first_byte(turn=1, engine="tts-b")
    builder.audio_emitted(num_bytes=4096, duration_s=1.4, engine="tts-b")
    clock.advance(1.4)
    builder.agent_audio_complete(turn=1, num_bytes=4096, engine="tts-b")
    builder.agent_utterance("that is booked for you", agent="BookingAgent")
    clock.advance(0.1)
    builder.tool_result("create_booking", None, ok=False, error="upstream timeout")
    builder.session_end(reason="completed", turns=1)
    return builder.build()


def test_round_trip_preserves_everything(tmp_path) -> None:
    original = _full_trace()
    path = write_jsonl(original, tmp_path / "trace.jsonl")
    assert read_jsonl(path) == original


def test_round_trip_covers_every_v1_event_kind(tmp_path) -> None:
    """A codec tested only on a two-event trace has not been tested."""
    original = _full_trace()
    kinds = {e.kind for e in original.events}
    assert kinds == EventKind.KNOWN, f"missing coverage for {EventKind.KNOWN - kinds}"
    assert read_jsonl(write_jsonl(original, tmp_path / "t.jsonl")) == original


def test_file_is_one_event_per_line(tmp_path) -> None:
    """Homogeneous lines: no header record, so `jq` over the file needs no
    special case and a diff between two runs stays readable."""
    original = _full_trace()
    path = write_jsonl(original, tmp_path / "trace.jsonl")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(original.events)
    assert len(read_jsonl_events(path)) == len(original.events)


def test_metadata_travels_in_the_session_start_payload(tmp_path) -> None:
    """The identifiers of a session are a fact about the session starting."""
    original = _full_trace()
    path = write_jsonl(original, tmp_path / "trace.jsonl")
    start = read_jsonl_events(path)[0]
    assert start.kind == EventKind.SESSION_START
    assert start.get("session_id") == "sess-001"
    assert start.get("scenario_id") == "booking/large_party"
    assert start.get("adapter") == "text"


def test_write_does_not_mutate_the_in_memory_trace(tmp_path) -> None:
    original = _full_trace()
    before = original.model_dump()
    write_jsonl(original, tmp_path / "trace.jsonl")
    assert original.model_dump() == before


def test_write_creates_missing_directories(tmp_path) -> None:
    path = write_jsonl(_full_trace(), tmp_path / "a" / "b" / "trace.jsonl")
    assert path.exists()


def test_fragment_without_session_start_still_loads(tmp_path) -> None:
    """A partial capture should be inspectable, not an exception."""
    path = tmp_path / "fragment.jsonl"
    path.write_text(
        '{"ts": 1.0, "kind": "caller_utterance", "actor": "caller", '
        '"payload": {"text": "hi"}, "engine": null}\n',
        encoding="utf-8",
    )
    trace = read_jsonl(path)
    assert trace.session_id == "fragment"
    assert trace.scenario_id == "unknown"
    assert len(trace.events) == 1


def test_explicit_arguments_override_the_embedded_metadata(tmp_path) -> None:
    path = write_jsonl(_full_trace(), tmp_path / "trace.jsonl")
    trace = read_jsonl(path, scenario_id="override", adapter="voice:replay")
    assert trace.session_id == "sess-001"
    assert trace.scenario_id == "override"
    assert trace.adapter == "voice:replay"


def test_blank_lines_are_skipped(tmp_path) -> None:
    original = _full_trace()
    path = write_jsonl(original, tmp_path / "trace.jsonl")
    path.write_text(
        path.read_text(encoding="utf-8").replace("\n", "\n\n"), encoding="utf-8"
    )
    assert len(read_jsonl_events(path)) == len(original.events)


def test_a_corrupt_line_raises_with_its_location(tmp_path) -> None:
    """A silent skip would turn a corrupt fixture into a quietly wrong result."""
    path = tmp_path / "bad.jsonl"
    path.write_text(
        '{"ts": 0.0, "kind": "session_start", "actor": "system", "payload": {}}\n'
        "{not json}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"bad\.jsonl:2"):
        read_jsonl_events(path)


def test_a_schema_violation_also_raises_with_its_location(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"ts": 0.0, "kind": "x", "actor": "nobody"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:1"):
        read_jsonl_events(path)


def test_iter_jsonl_streams(tmp_path) -> None:
    path = write_jsonl(_full_trace(), tmp_path / "trace.jsonl")
    stream = iter_jsonl(path)
    assert next(iter(stream)).kind == EventKind.SESSION_START


def test_empty_trace_round_trips(tmp_path) -> None:
    empty = Trace(session_id="s", scenario_id="sc", adapter="test")
    path = write_jsonl(empty, tmp_path / "empty.jsonl")
    assert path.read_text(encoding="utf-8") == ""
    assert read_jsonl(path, scenario_id="sc", adapter="test").events == []


# --------------------------------------------------------------------------- #
# TraceBuilder
# --------------------------------------------------------------------------- #


def test_builder_reads_the_injected_clock() -> None:
    clock = FakeClock()
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=clock)
    builder.session_start()
    clock.advance(2.5)
    builder.session_end()
    assert [e.ts for e in builder.events] == [0.0, 2.5]


def test_builder_accepts_a_back_dated_timestamp() -> None:
    """The mechanism that keeps harness compute out of the measured window.

    The clock has moved on to 9.0 by the time the event is constructed, but the
    event is stamped at the boundary instant that was captured earlier.
    """
    clock = FakeClock()
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=clock)
    captured = clock.now()
    clock.advance(9.0)
    event = builder.caller_utterance("hello", ts=captured)
    assert event.ts == 0.0
    assert clock.now() == 9.0


def test_builder_generates_a_session_id_when_not_given() -> None:
    builder = TraceBuilder(scenario_id="s", adapter="a")
    assert len(builder.session_id) == 32


def test_builder_emit_methods_return_the_appended_event() -> None:
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    event = builder.tool_call("check_policy", {"topic": "corkage"})
    assert builder.events[-1] is event
    assert event.get("name") == "check_policy"
    assert event.get("args") == {"topic": "corkage"}


def test_tool_call_correlates_with_its_result() -> None:
    """Correlate by id, not by adjacency, so interleaved calls stay analysable."""
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    first = builder.tool_call("search_tables", {})
    second = builder.tool_call("check_policy", {})
    builder.tool_result("check_policy", "ok", call_id=second.get("call_id"))
    builder.tool_result("search_tables", "ok", call_id=first.get("call_id"))

    results = builder.build().events_of_kind(EventKind.TOOL_RESULT)
    assert results[0].get("call_id") == second.get("call_id")
    assert results[1].get("call_id") == first.get("call_id")
    assert first.get("call_id") != second.get("call_id")


def test_handoff_uses_the_reserved_payload_keys() -> None:
    """`from` is a Python keyword, which is exactly why the wrapper exists."""
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    event = builder.agent_handoff("GreeterAgent", "PolicyAgent", reason="asked about pets")
    assert event.get("from") == "GreeterAgent"
    assert event.get("to") == "PolicyAgent"


def test_none_valued_payload_keys_are_dropped() -> None:
    """Absent means absent. A payload full of nulls makes a diff unreadable and
    invites a check to treat "not recorded" as "recorded as nothing"."""
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    event = builder.agent_utterance("hello")  # no agent given
    assert "agent" not in event.payload
    assert event.payload == {"text": "hello"}


def test_engine_is_recorded_per_event() -> None:
    """Aggregate latency that cannot be attributed to a component is not
    actionable, so the producing engine is a first-class field."""
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    builder.transcript_in("hi", engine="stt-a")
    builder.transcript_out("hello", engine="tts-b")
    assert [e.engine for e in builder.events] == ["stt-a", "tts-b"]


def test_emit_is_the_escape_hatch_for_unknown_kinds() -> None:
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    event = builder.emit("vendor_specific_signal", "system", detail=1)
    assert event.is_known_kind is False
    assert event.get("detail") == 1


def test_build_snapshots_the_events() -> None:
    """A later emit must not mutate an already-returned trace."""
    builder = TraceBuilder(scenario_id="s", adapter="a", clock=FakeClock())
    builder.session_start()
    trace = builder.build()
    builder.session_end()
    assert len(trace.events) == 1
    assert len(builder.build().events) == 2


def test_builder_produces_an_ordered_trace() -> None:
    assert _full_trace().is_ordered() is True


def test_builder_defaults_to_a_real_clock_starting_near_zero() -> None:
    builder = TraceBuilder(scenario_id="s", adapter="a")
    event = builder.session_start()
    assert 0.0 <= event.ts < 1.0
