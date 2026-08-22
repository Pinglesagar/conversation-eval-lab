"""Tests for the transition-failure matrix and its PNG.

WHAT THIS DEMONSTRATES
----------------------
The matrix is the part that carries the finding, so it is tested with no
dependency at all: attempts, failures, the two attribution modes, and the
distinction between "never attempted" and "never failed" — which are opposite
findings and, on a naive heatmap, the same colour.

The PNG test is skipped when `matplotlib` is absent, because the plotting backend
lives in the optional `[charts]` extra and the cardinal rule is that
`pip install -e ".[dev]" && pytest` passes with nothing else installed. The chart
is a rendering of the matrix; the matrix is the evidence.
"""

from __future__ import annotations

import pytest

from lab.clock import FakeClock
from lab.report.heatmap import (
    TransitionMatrix,
    default_failure_predicate,
    matrix_from_failures,
    render_heatmap,
    transition_key,
    transition_matrix,
)
from lab.report.report import FailureRecord
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace


def _trace(
    session_id: str,
    handoffs: list[tuple[str, str]],
    *,
    reason: str = "completed",
    tool_ok: bool = True,
) -> Trace:
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id="booking/handoffs",
        adapter="text",
        session_id=session_id,
        clock=clock,
    )
    builder.session_start()
    clock.advance(0.1)
    builder.caller_utterance("a table for six on Friday")
    for source, target in handoffs:
        clock.advance(0.1)
        builder.agent_handoff(source, target, reason="intent")
    clock.advance(0.1)
    call = builder.tool_call("create_booking", {"party_size": 6})
    builder.tool_result(
        "create_booking",
        {"ref": "R1"} if tool_ok else None,
        call_id=call.get("call_id"),
        ok=tool_ok,
        error=None if tool_ok else "upstream timeout",
    )
    builder.agent_utterance("you're booked", agent="BookingAgent")
    builder.session_end(reason=reason, turns=1)
    return builder.build()


G_TO_B = ("GreeterAgent", "BookingAgent")
B_TO_M = ("BookingAgent", "ModificationAgent")
G_TO_P = ("GreeterAgent", "PolicyAgent")


# --------------------------------------------------------------------------- #
# The trace-only path
# --------------------------------------------------------------------------- #


def test_the_default_predicate_reads_only_the_trace() -> None:
    assert default_failure_predicate(_trace("ok", [G_TO_B])) is False
    assert default_failure_predicate(_trace("loop", [G_TO_B], reason="max_turns")) is True
    assert default_failure_predicate(_trace("tool", [G_TO_B], tool_ok=False)) is True
    # A caller hanging up is a normal ending, not a failure.
    assert default_failure_predicate(_trace("bye", [G_TO_B], reason="caller_hung_up")) is False


def test_matrix_counts_attempts_and_attributes_failures_per_session() -> None:
    traces = [
        _trace("s1", [G_TO_B]),
        _trace("s2", [G_TO_B, B_TO_M], reason="max_turns"),
        _trace("s3", [G_TO_B, B_TO_M]),
        _trace("s4", [G_TO_P], tool_ok=False),
    ]
    matrix = transition_matrix(traces)

    assert matrix.sessions == 4
    assert matrix.failing_sessions == 2
    assert matrix.agents == [
        "BookingAgent",
        "GreeterAgent",
        "ModificationAgent",
        "PolicyAgent",
    ]
    assert matrix.attempt_count(*G_TO_B) == 3
    assert matrix.failure_count(*G_TO_B) == 1
    assert matrix.rate(*G_TO_B).text == "1/3 (33.3%)"
    assert matrix.rate(*B_TO_M).text == "1/2 (50.0%)"
    assert matrix.rate(*G_TO_P).text == "1/1 (100.0%)"
    # A transition nobody ever attempted.
    assert matrix.attempt_count("PolicyAgent", "BookingAgent") == 0
    assert matrix.attribution == "whole-session"


def test_whole_session_attribution_blames_every_transition_it_crossed() -> None:
    """Documented over-attribution: one failing session, two hot cells.

    The matrix is a map of where to look, not a per-transition probability, and
    the annotated counts are what stop it from being read as one.
    """
    matrix = transition_matrix([_trace("s1", [G_TO_B, B_TO_M], reason="max_turns")])
    assert matrix.failure_count(*G_TO_B) == 1
    assert matrix.failure_count(*B_TO_M) == 1
    assert matrix.total_failures == 2  # from a single failing session
    assert matrix.failing_sessions == 1


def test_a_custom_verdict_replaces_the_default_predicate() -> None:
    traces = [_trace("s1", [G_TO_B]), _trace("s2", [G_TO_B])]
    # A check that knows the second session was wrong even though it completed
    # smoothly — the shape of the most interesting bugs, and invisible to the
    # blunt default.
    matrix = transition_matrix(traces, is_failure=lambda t: t.session_id == "s2")
    assert matrix.rate(*G_TO_B).text == "1/2 (50.0%)"


def test_an_empty_matrix_says_so_rather_than_implying_health() -> None:
    matrix = transition_matrix([_trace("s1", [])])
    assert matrix.is_empty is True
    assert "No handoffs observed" in matrix.to_markdown()
    with pytest.raises(ValueError, match="no agent_handoff events were observed"):
        render_heatmap(matrix, "unused.png")


# --------------------------------------------------------------------------- #
# The precise path
# --------------------------------------------------------------------------- #


def test_failures_naming_their_own_transition_avoid_over_attribution() -> None:
    traces = [_trace("s1", [G_TO_B, B_TO_M]), _trace("s2", [G_TO_B, B_TO_M])]
    failures = [
        FailureRecord(
            scenario_id="modification/party_size",
            contract="no_re_ask",
            evidence="ModificationAgent asked 'how many people?' after the caller said six",
            session_id="s1",
            from_agent="BookingAgent",
            to_agent="ModificationAgent",
        ),
        # No transition named: belongs in the failure list, not in a chart about
        # locations.
        FailureRecord(
            scenario_id="booking/simple",
            contract="decision_vs_action",
            evidence="claimed a booking with no create_booking call",
            session_id="s2",
        ),
    ]
    matrix = matrix_from_failures(traces, failures)
    assert matrix.attribution == "per-handoff"
    assert matrix.failure_count(*B_TO_M) == 1
    assert matrix.failure_count(*G_TO_B) == 0  # not blamed for a failure it did not cause
    assert matrix.rate(*B_TO_M).text == "1/2 (50.0%)"
    assert matrix.total_failures == 1


def test_hottest_transitions_are_ordered_and_stable() -> None:
    traces = [
        _trace("s1", [G_TO_B, B_TO_M], reason="max_turns"),
        _trace("s2", [G_TO_B], reason="max_turns"),
        _trace("s3", [G_TO_B]),
    ]
    matrix = transition_matrix(traces)
    hottest = matrix.hottest()
    assert hottest[0] == (transition_key(*G_TO_B), 2, 3)
    assert hottest[1] == (transition_key(*B_TO_M), 1, 1)


def test_markdown_table_shows_counts_and_marks_untried_transitions() -> None:
    matrix = transition_matrix([_trace("s1", [G_TO_B], reason="max_turns")])
    table = matrix.to_markdown()
    assert "| from \\ to | BookingAgent | GreeterAgent |" in table
    assert "1/1 (100.0%)" in table
    assert "·" in table  # never attempted, distinct from never failed
    assert "attribution: whole-session" in table


def test_a_failure_count_is_never_reported_above_its_attempts() -> None:
    # Hand-built rather than derived, so the guard is tested on its own terms:
    # a rate above 1.0 would be an arithmetic tell that the matrix is wrong.
    matrix = TransitionMatrix(
        agents=["A", "B"],
        attempts={"A->B": 2},
        failures={"A->B": 5},
        sessions=2,
        failing_sessions=2,
    )
    assert matrix.rate("A", "B").text == "2/2 (100.0%)"


# --------------------------------------------------------------------------- #
# The chart itself
# --------------------------------------------------------------------------- #


def test_heatmap_writes_a_non_empty_png(tmp_path) -> None:
    pytest.importorskip(
        "matplotlib", reason="matplotlib lives in the optional [charts] extra"
    )
    matrix = transition_matrix(
        [
            _trace("s1", [G_TO_B, B_TO_M], reason="max_turns"),
            _trace("s2", [G_TO_B]),
            _trace("s3", [G_TO_P], tool_ok=False),
        ]
    )
    path = render_heatmap(matrix, tmp_path / "charts" / "transitions.png")
    assert path.exists()
    data = path.read_bytes()
    assert len(data) > 1000
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # a real PNG, not an empty file


def test_heatmap_accepts_a_title(tmp_path) -> None:
    pytest.importorskip("matplotlib")
    matrix = transition_matrix([_trace("s1", [G_TO_B], reason="max_turns")])
    path = render_heatmap(matrix, tmp_path / "titled.png", title="Handoff failures, run 7")
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_missing_plotting_backend_names_the_extra_and_the_alternative(
    tmp_path, monkeypatch
) -> None:
    """The no-matplotlib path, tested whether or not matplotlib is installed.

    A blocking meta-path finder simulates the clean `[dev]`-only install that the
    cardinal rule promises, so this behaviour is covered on a machine that happens
    to have the extra — the usual reason an optional-dependency message is
    discovered to be wrong by a user rather than by a test.
    """
    import sys

    class Blocked:
        def find_spec(self, name, path=None, target=None):  # noqa: ANN001, ANN202
            if name.split(".")[0] == "matplotlib":
                raise ModuleNotFoundError(
                    "No module named 'matplotlib'", name="matplotlib"
                )
            return None

    for module in [n for n in list(sys.modules) if n.split(".")[0] == "matplotlib"]:
        monkeypatch.delitem(sys.modules, module)
    monkeypatch.setattr(sys, "meta_path", [Blocked(), *sys.meta_path])

    matrix = transition_matrix([_trace("s1", [G_TO_B], reason="max_turns")])
    with pytest.raises(ModuleNotFoundError, match=r"\[charts\] extra"):
        render_heatmap(matrix, tmp_path / "unrenderable.png")
    # The finding survives the missing dependency: the matrix is the evidence and
    # the chart is only a rendering of it.
    assert "1/1 (100.0%)" in matrix.to_markdown()
