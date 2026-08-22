"""Tests for the langfuse and promptfoo exporters.

WHAT THIS DEMONSTRATES
----------------------
Two different obligations, tested differently on purpose.

The langfuse export is a **serialisation**, so the test is a round trip on a
trace exercising every v1 event kind: export, re-import, and require equality
with the original. A lossy export would mean the copy in the observability tool
is a different artifact from the one the verdicts were computed on, and any
disagreement between them would be unresolvable.

The promptfoo export is a **projection** — "what happened" turned into "what must
keep happening" — so there is nothing to round-trip and the tests pin the emitted
shape instead. Testing it as if it round-tripped would be claiming a property it
does not have.

Neither package is imported anywhere here or in the module under test.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lab.clock import FakeClock
from lab.report.interop import (
    EPOCH,
    from_langfuse_batch,
    promptfoo_assertions_for,
    to_langfuse_batch,
    to_promptfoo_config,
    to_promptfoo_tests,
)
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace


def _full_trace() -> Trace:
    """A trace exercising every v1 event kind, on a fake clock for exact ts."""
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
    # A failed call whose result is not adjacent to its own call: exported as two
    # events rather than a span, which is what keeps the round trip exact.
    builder.tool_result("create_booking", None, ok=False, error="upstream timeout")
    builder.session_end(reason="completed", turns=1)
    return builder.build()


# --------------------------------------------------------------------------- #
# langfuse: a serialisation, so it round-trips
# --------------------------------------------------------------------------- #


def test_langfuse_export_round_trips_exactly() -> None:
    original = _full_trace()
    assert from_langfuse_batch(to_langfuse_batch(original)) == original


def test_round_trip_survives_a_wall_clock_origin() -> None:
    original = _full_trace()
    start = datetime(2026, 8, 22, 9, 30, tzinfo=timezone.utc)
    # The ISO timestamps change; the recovered trace does not, because
    # reconstruction reads the embedded events rather than the derived times.
    assert from_langfuse_batch(to_langfuse_batch(original, start_time=start)) == original


def test_batch_shape_matches_the_documented_ingestion_api() -> None:
    batch = to_langfuse_batch(_full_trace())["batch"]
    assert batch[0]["type"] == "trace-create"
    body = batch[0]["body"]
    assert body["id"] == "sess-001"
    assert body["sessionId"] == "sess-001"
    assert body["name"] == "booking/large_party"
    assert body["tags"] == ["text"]
    assert body["input"]["caller"] == ["table for six at seven"]
    assert body["output"]["agent"] == ["that is booked for you"]

    types = {item["type"] for item in batch[1:]}
    assert types == {"span-create", "event-create"}
    for item in batch[1:]:
        assert item["body"]["traceId"] == "sess-001"


def test_an_adjacent_tool_pair_becomes_one_span_with_a_duration() -> None:
    batch = to_langfuse_batch(_full_trace())["batch"]
    spans = [item for item in batch if item["type"] == "span-create"]
    assert len(spans) == 1
    span = spans[0]["body"]
    assert span["name"] == "tool:search_tables"
    assert span["input"] == {"party_size": 6, "time": "19:00"}
    assert span["output"] == {"available": True}
    # A span is how an observability UI shows duration, and a tool call is the one
    # thing in a v1 trace that genuinely has a start and an end.
    assert span["startTime"] < span["endTime"]


def test_a_failed_tool_result_is_marked_as_an_error() -> None:
    batch = to_langfuse_batch(_full_trace())["batch"]
    orphan = next(
        item
        for item in batch
        if item["type"] == "event-create" and item["body"]["name"] == "tool_result"
    )
    assert orphan["body"]["input"]["ok"] is False
    assert orphan["body"]["input"]["error"] == "upstream timeout"


def test_timestamps_are_absolute_and_derived_from_the_supplied_origin() -> None:
    start = datetime(2026, 8, 22, 9, 30, tzinfo=timezone.utc)
    batch = to_langfuse_batch(_full_trace(), start_time=start)["batch"]
    assert batch[0]["timestamp"] == "2026-08-22T09:30:00Z"
    default_batch = to_langfuse_batch(_full_trace())["batch"]
    assert default_batch[0]["timestamp"] == "1970-01-01T00:00:00Z"
    assert EPOCH.year == 1970


def test_export_is_deterministic_so_a_re_upload_is_not_a_new_set_of_spans() -> None:
    # One trace, exported twice: observation ids are derived from the session id
    # and the event's position, never from uuid4, so re-uploading a trace updates
    # the same spans instead of duplicating them.
    trace = _full_trace()
    first = json.dumps(to_langfuse_batch(trace), sort_keys=True)
    second = json.dumps(to_langfuse_batch(trace), sort_keys=True)
    assert first == second
    ids = [item["id"] for item in to_langfuse_batch(trace)["batch"]]
    assert ids[0] == "sess-001-trace"
    assert ids[1:3] == ["sess-001-0000", "sess-001-0001"]
    assert len(set(ids)) == len(ids)


def test_a_foreign_payload_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(ValueError, match="not a langfuse ingestion batch"):
        from_langfuse_batch({})
    with pytest.raises(ValueError, match="no trace-create entry"):
        from_langfuse_batch({"batch": [{"type": "span-create", "body": {}}]})
    with pytest.raises(ValueError, match="not produced by lab.report.interop"):
        from_langfuse_batch(
            {"batch": [{"type": "trace-create", "body": {"id": "x", "metadata": {}}}]}
        )
    with pytest.raises(ValueError, match="cannot be reconstructed losslessly"):
        from_langfuse_batch(
            {
                "batch": [
                    {
                        "type": "trace-create",
                        "body": {
                            "metadata": {
                                "lab": {
                                    "session_id": "s",
                                    "scenario_id": "sc",
                                    "adapter": "text",
                                }
                            }
                        },
                    },
                    {"id": "obs-1", "type": "event-create", "body": {"metadata": {}}},
                ]
            }
        )


# --------------------------------------------------------------------------- #
# promptfoo: a projection, so the shape is pinned instead
# --------------------------------------------------------------------------- #


def test_assertions_cover_every_tool_and_handoff_observed() -> None:
    assertions = promptfoo_assertions_for(_full_trace())
    metrics = [a.get("metric") for a in assertions]
    assert "tool_called:search_tables" in metrics
    assert "handoff:GreeterAgent->BookingAgent" in metrics
    javascript = [a for a in assertions if a["type"] == "javascript"]
    # JavaScript over structured output, not a substring of the reply text: an
    # agent that merely *mentions* create_booking must not pass a tool assertion.
    assert all("JSON.parse(output)" in a["value"] for a in javascript)
    assert "'search_tables'" in javascript[0]["value"]


def test_a_latency_assertion_is_emitted_only_when_the_trace_defines_one() -> None:
    with_boundary = promptfoo_assertions_for(_full_trace())
    latency = [a for a in with_boundary if a["type"] == "latency"]
    assert len(latency) == 1
    # caller_utterance at 0.1 s, agent_audio_first_byte at 0.5 s: 400 ms observed,
    # rounded up to the next 100 ms as a regression guard.
    assert latency[0]["threshold"] == 400

    clock = FakeClock()
    builder = TraceBuilder(scenario_id="s", adapter="text", session_id="s1", clock=clock)
    builder.session_start()
    builder.caller_utterance("hello")
    builder.agent_utterance("hi", agent="GreeterAgent")
    # No boundary event, so no latency is defined and none is invented.
    assert [a for a in promptfoo_assertions_for(builder.build()) if a["type"] == "latency"] == []


def test_a_custom_latency_budget_overrides_the_observed_one() -> None:
    assertions = promptfoo_assertions_for(_full_trace(), latency_budget_ms=1500)
    latency = next(a for a in assertions if a["type"] == "latency")
    assert latency["threshold"] == 1500


def test_a_rubric_is_only_emitted_when_the_caller_supplies_one() -> None:
    assert not any(a["type"] == "llm-rubric" for a in promptfoo_assertions_for(_full_trace()))
    with_rubric = promptfoo_assertions_for(
        _full_trace(), rubric="the agent confirmed the booking reference out loud"
    )
    rubric = next(a for a in with_rubric if a["type"] == "llm-rubric")
    assert rubric["value"] == "the agent confirmed the booking reference out loud"


def test_test_cases_carry_the_caller_turns_so_they_are_replayable() -> None:
    tests = to_promptfoo_tests([_full_trace()])
    assert len(tests) == 1
    case = tests[0]
    assert case["description"] == "booking/large_party (sess-001)"
    assert case["vars"]["caller_turns"] == ["table for six at seven"]
    assert case["vars"]["scenario_id"] == "booking/large_party"
    assert case["assert"]


def test_config_omits_providers_rather_than_inventing_one() -> None:
    config = to_promptfoo_config([_full_trace()])
    assert set(config) == {"description", "tests"}
    # A config that looks runnable and is not would be worse than one that is
    # obviously incomplete.
    assert "providers" not in config
    assert "tool_names" in config["description"]
