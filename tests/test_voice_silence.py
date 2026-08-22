"""Tests for `lab.voice.silence` — synthetic gaps with known contents.

WHAT THIS DEMONSTRATES
----------------------
The module's central claim is a claim about *restraint*: it attributes a gap to
the operations inside it and refuses to split the gap between them. The tests are
written to make that restraint falsifiable rather than aspirational:

* `test_two_tools_in_one_gap_are_not_apportioned` — the by-tool mapping is
  asserted to double count, and the sum of its seconds is asserted to *exceed*
  the total dead air. A future "improvement" that made the numbers add up neatly
  would be an invented split, and it would fail here.
* `test_overlapping_tool_intervals_use_the_union_not_the_sum` — two concurrent
  2 s calls overlapping by 1.5 s account for 2.5 s, not 4 s. Summing durations is
  the standard way a tool-time percentage ends up over 100.
* `test_measured_tool_time_is_clipped_to_the_gap` — a tool whose result lands
  after speech resumes keeps its true 3.4 s duration while contributing only the
  2.6 s that actually fell inside the silence.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace
from lab.voice.silence import (
    AGENT_SPEECH,
    DEFAULT_GAP_THRESHOLD_S,
    STAGE_HANDOFF,
    STAGE_TOOL,
    STAGE_TOOL_AND_HANDOFF,
    STAGE_UNATTRIBUTED,
    STAGES,
    find_gaps,
    silence_report,
    speech_spans,
)


def builder(session_id: str = "s0") -> TraceBuilder:
    return TraceBuilder(
        scenario_id="booking-with-handoff",
        adapter="test:synthetic",
        session_id=session_id,
        clock=FakeClock(),
    )


def one_tool_one_handoff_trace() -> Trace:
    """A booking turn whose answer is delayed by a table search and a handoff.

    Timeline (seconds):
        0.0  session_start
        0.2  agent first byte  ("Good evening, TableMate")
        1.2  agent complete
        1.4  caller turn ends  ("a table for four at seven thirty")
        1.6  tool_call search_tables
        3.6  tool_result search_tables      <- 2.0 s of measured tool time
        3.8  handoff Greeter -> Booking
        4.2  agent first byte
        5.0  agent complete
        5.2  session_end

    The only gap above a 0.5 s threshold is 1.4 -> 4.2, which is 2.8 s and
    encloses both operations. 2.0 s of it is measured; 0.8 s is not.
    """
    trace = builder()
    trace.session_start(ts=0.0)
    trace.agent_audio_first_byte(turn=0, ts=0.2)
    trace.agent_audio_complete(turn=0, ts=1.2)
    trace.caller_utterance("a table for four at seven thirty", ts=1.4)
    trace.tool_call("search_tables", {"party_size": 4}, call_id="c1", ts=1.6)
    trace.tool_result("search_tables", {"available": True}, call_id="c1", ts=3.6)
    trace.agent_handoff("GreeterAgent", "BookingAgent", reason="booking", ts=3.8)
    trace.agent_audio_first_byte(turn=1, ts=4.2)
    trace.agent_audio_complete(turn=1, ts=5.0)
    trace.session_end(turns=1, ts=5.2)
    return trace.build()


# --------------------------------------------------------------------------- #
# Spans
# --------------------------------------------------------------------------- #


def test_speech_spans_pair_first_byte_with_completion() -> None:
    spans = speech_spans(one_tool_one_handoff_trace())
    agent_spans = [s for s in spans if s.actor == "agent"]
    assert [(s.start_s, s.end_s) for s in agent_spans] == [(0.2, 1.2), (4.2, 5.0)]
    caller_spans = [s for s in spans if s.actor == "caller"]
    # Caller speech is a point: the harness records the end of the turn only.
    assert [(s.start_s, s.end_s) for s in caller_spans] == [(1.4, 1.4)]
    assert [s.start_s for s in spans] == sorted(s.start_s for s in spans)


def test_uncompleted_agent_utterance_becomes_a_point_span() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.agent_audio_first_byte(turn=0, ts=1.0)  # cut off, never completes
    trace.session_end(reason="caller_hung_up", ts=2.0)
    spans = speech_spans(trace.build())
    agent = [s for s in spans if s.actor == "agent"]
    assert len(agent) == 1
    assert agent[0].start_s == agent[0].end_s == 1.0
    assert "never completed" in agent[0].source


def test_session_bounds_can_be_excluded() -> None:
    spans = speech_spans(one_tool_one_handoff_trace(), include_session_bounds=False)
    assert all(span.actor != "system" for span in spans)


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #


def test_gap_is_attributed_to_the_operations_inside_it() -> None:
    gaps = find_gaps(one_tool_one_handoff_trace(), threshold_s=0.5)
    assert len(gaps) == 1
    gap = gaps[0]

    assert gap.start_s == pytest.approx(1.4)
    assert gap.end_s == pytest.approx(4.2)
    assert gap.duration_s == pytest.approx(2.8)
    assert gap.preceded_by == "caller_utterance"
    assert gap.followed_by == AGENT_SPEECH
    assert gap.stage == STAGE_TOOL_AND_HANDOFF
    assert gap.attributed_to() == ["search_tables", "GreeterAgent->BookingAgent"]
    assert gap.tool_names == ["search_tables"]
    assert gap.handoff_labels == ["GreeterAgent->BookingAgent"]


def test_measured_tool_time_and_unaccounted_remainder_add_up() -> None:
    gap = find_gaps(one_tool_one_handoff_trace(), threshold_s=0.5)[0]
    assert gap.accounted_s == pytest.approx(2.0)
    assert gap.unaccounted_s == pytest.approx(0.8)
    assert gap.accounted_s + gap.unaccounted_s == pytest.approx(gap.duration_s)
    # The handoff has no end event, so it gets no duration rather than a guess.
    handoff = next(op for op in gap.operations if op.label.startswith("Greeter"))
    assert handoff.duration_s is None
    assert "no measured duration" in handoff.describe()


def test_multi_operation_gap_says_it_is_not_split() -> None:
    gap = find_gaps(one_tool_one_handoff_trace(), threshold_s=0.5)[0]
    described = gap.describe()
    assert "attributed to all of them, not split between them" in described
    assert "unaccounted" in described


def test_two_tools_in_one_gap_are_not_apportioned() -> None:
    """The by-tool mapping double counts on purpose; summing it must exceed the total."""
    trace = builder()
    trace.session_start(ts=0.0)
    trace.caller_utterance("change my booking to six people", ts=0.2)
    trace.tool_call("modify_booking", {}, call_id="c1", ts=0.4)
    trace.tool_result("modify_booking", {"ok": True}, call_id="c1", ts=1.4)
    trace.tool_call("check_policy", {"topic": "large parties"}, call_id="c2", ts=1.6)
    trace.tool_result("check_policy", {"ok": True}, call_id="c2", ts=2.6)
    trace.agent_audio_first_byte(turn=0, ts=3.2)
    trace.agent_audio_complete(turn=0, ts=3.8)
    trace.session_end(ts=4.0)
    report = silence_report(trace.build(), threshold_s=0.5)

    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.stage == STAGE_TOOL
    assert gap.duration_s == pytest.approx(3.0)
    assert gap.tool_names == ["modify_booking", "check_policy"]

    by_tool = report.by_tool()
    assert by_tool["modify_booking"] == (1, pytest.approx(3.0))
    assert by_tool["check_policy"] == (1, pytest.approx(3.0))
    # 3.0 + 3.0 > 3.0: the same silence appears under both names because the
    # trace cannot say which tool owned it. A neat sum would be a fabrication.
    assert sum(seconds for _, seconds in by_tool.values()) > report.total_gap_s


def test_overlapping_tool_intervals_use_the_union_not_the_sum() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.caller_utterance("anything for four tonight", ts=0.4)
    trace.tool_call("search_tables", {}, call_id="a", ts=0.6)
    trace.tool_call("check_policy", {}, call_id="b", ts=1.1)
    trace.tool_result("search_tables", {}, call_id="a", ts=2.6)
    trace.tool_result("check_policy", {}, call_id="b", ts=3.1)
    trace.agent_audio_first_byte(turn=0, ts=3.5)
    trace.agent_audio_complete(turn=0, ts=4.0)
    trace.session_end(ts=4.2)
    gap = find_gaps(trace.build(), threshold_s=0.5)[0]

    assert gap.duration_s == pytest.approx(3.1)  # 0.4 -> 3.5
    assert gap.accounted_s == pytest.approx(2.5)  # union of [0.6,2.6] and [1.1,3.1]
    assert gap.accounted_s != pytest.approx(4.0)  # the sum, which would be wrong
    assert gap.unaccounted_s == pytest.approx(0.6)


def test_measured_tool_time_is_clipped_to_the_gap() -> None:
    """A tool that outlives the silence keeps its real duration but contributes less."""
    trace = builder()
    trace.session_start(ts=0.0)
    trace.caller_utterance("table for two", ts=0.4)
    trace.tool_call("search_tables", {}, call_id="c1", ts=0.6)
    trace.agent_audio_first_byte(turn=0, ts=3.2)  # starts talking before the result
    trace.tool_result("search_tables", {}, call_id="c1", ts=4.0)
    trace.agent_audio_complete(turn=0, ts=4.4)
    trace.session_end(ts=4.6)
    gap = find_gaps(trace.build(), threshold_s=0.5)[0]

    operation = gap.operations[0]
    assert operation.duration_s == pytest.approx(3.4)  # the true tool duration
    assert gap.duration_s == pytest.approx(2.8)  # 0.4 -> 3.2
    assert gap.accounted_s == pytest.approx(2.6)  # only 0.6 -> 3.2 was silent
    assert gap.accounted_s <= gap.duration_s


def test_unattributed_gap_is_labelled_rather_than_hidden() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.caller_utterance("hello?", ts=0.4)
    trace.agent_audio_first_byte(turn=0, ts=4.4)  # four seconds of nothing at all
    trace.agent_audio_complete(turn=0, ts=4.9)
    trace.session_end(ts=5.1)
    gap = find_gaps(trace.build(), threshold_s=0.5)[0]

    assert gap.stage == STAGE_UNATTRIBUTED
    assert gap.operations == []
    assert gap.accounted_s == pytest.approx(0.0)
    assert gap.unaccounted_s == pytest.approx(4.0)
    assert "unexplained by the trace" in gap.describe()


def test_handoff_only_gap_is_its_own_stage() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.agent_audio_first_byte(turn=0, ts=0.1)
    trace.agent_audio_complete(turn=0, ts=1.0)
    trace.agent_handoff("BookingAgent", "PolicyAgent", reason="policy question", ts=1.5)
    trace.agent_audio_first_byte(turn=1, ts=3.0)
    trace.agent_audio_complete(turn=1, ts=3.5)
    trace.session_end(ts=3.7)
    gap = find_gaps(trace.build(), threshold_s=0.5)[0]

    assert gap.stage == STAGE_HANDOFF
    # A mid-answer handoff with no caller turn either side: a silence the
    # response-latency distribution in lab.voice.metrics never sees.
    assert gap.preceded_by == AGENT_SPEECH
    assert gap.attributed_to() == ["BookingAgent->PolicyAgent"]


# --------------------------------------------------------------------------- #
# Session edges, thresholds, degenerate traces
# --------------------------------------------------------------------------- #


def test_silence_before_the_greeting_is_measured() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.agent_audio_first_byte(turn=0, ts=3.0)
    trace.agent_audio_complete(turn=0, ts=3.5)
    trace.session_end(ts=3.6)
    gaps = find_gaps(trace.build(), threshold_s=0.5)

    assert len(gaps) == 1
    assert gaps[0].preceded_by == "session_start"
    assert gaps[0].duration_s == pytest.approx(3.0)


def test_silence_before_the_line_drops_is_measured() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.agent_audio_first_byte(turn=0, ts=0.2)
    trace.agent_audio_complete(turn=0, ts=1.0)
    trace.session_end(reason="caller_hung_up", ts=6.0)
    gaps = find_gaps(trace.build(), threshold_s=0.5)

    assert len(gaps) == 1
    assert gaps[0].followed_by == "session_end"
    assert gaps[0].duration_s == pytest.approx(5.0)


def test_threshold_is_strictly_greater_than() -> None:
    trace = builder()
    trace.session_start(ts=0.0)
    trace.agent_audio_first_byte(turn=0, ts=0.0)
    trace.agent_audio_complete(turn=0, ts=1.0)
    trace.agent_audio_first_byte(turn=1, ts=2.0)  # exactly 1.0 s of silence
    trace.agent_audio_complete(turn=1, ts=2.5)
    trace.session_end(ts=2.5)
    built = trace.build()

    assert find_gaps(built, threshold_s=1.0) == []
    assert len(find_gaps(built, threshold_s=0.999)) == 1


def test_negative_threshold_is_rejected() -> None:
    with pytest.raises(ValueError, match="threshold_s must be non-negative"):
        find_gaps(one_tool_one_handoff_trace(), threshold_s=-0.1)


def test_trace_without_speech_has_no_gaps() -> None:
    trace = builder()
    trace.tool_call("check_policy", {}, call_id="c1", ts=1.0)
    assert find_gaps(trace.build()) == []


def test_default_threshold_is_documented_and_used() -> None:
    assert DEFAULT_GAP_THRESHOLD_S == 0.8
    report = silence_report(one_tool_one_handoff_trace())
    assert report.threshold_s == DEFAULT_GAP_THRESHOLD_S
    assert "0.800 s" in report.to_text()


# --------------------------------------------------------------------------- #
# Report shape
# --------------------------------------------------------------------------- #


def test_stage_shares_cover_every_stage_and_print_denominators() -> None:
    report = silence_report(one_tool_one_handoff_trace(), threshold_s=0.5)
    shares = report.stages()
    assert [share.stage for share in shares] == list(STAGES)
    assert sum(share.gaps for share in shares) == len(report.gaps)
    assert sum(share.total_s for share in shares) == pytest.approx(report.total_gap_s)

    populated = next(share for share in shares if share.stage == STAGE_TOOL_AND_HANDOFF)
    assert "1/1 gaps" in populated.describe()
    assert "2.800 s/2.800 s" in populated.describe()
    assert "100.0%" in populated.describe()

    empty = next(share for share in shares if share.stage == STAGE_HANDOFF)
    assert empty.gaps == 0
    assert empty.time_share == pytest.approx(0.0)


def test_stage_share_of_an_empty_report_has_no_denominator() -> None:
    report = silence_report(one_tool_one_handoff_trace(), threshold_s=99.0)
    assert report.gaps == []
    assert report.worst_gap is None
    for share in report.stages():
        assert share.time_share is None
        assert "n/a" in share.describe()
    assert "no gap exceeded" in report.to_text()


def test_report_records_the_worst_gap_and_the_session_length() -> None:
    report = silence_report(one_tool_one_handoff_trace(), threshold_s=0.5)
    assert report.worst_gap is not None
    assert report.worst_gap.duration_s == pytest.approx(2.8)
    assert report.session_duration_s == pytest.approx(5.2)
    assert report.total_gap_s == pytest.approx(2.8)


def test_markdown_states_the_attribution_limit() -> None:
    markdown = silence_report(one_tool_one_handoff_trace(), threshold_s=0.5).to_markdown()
    assert "not split" in markdown
    assert "search_tables" in markdown
    assert "union of real" in markdown
    assert markdown.startswith("### Silence")
