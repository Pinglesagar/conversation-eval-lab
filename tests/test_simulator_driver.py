"""Tests for the scenario driver, the scripted caller and the record/replay caller.

WHAT THIS DEMONSTRATES
----------------------
The two properties that make the rest of the repo's numbers meaningful, tested
rather than asserted in a docstring:

1.  **The trace's boundary events are exactly the instants the agent saw.** The
    agent under test records `clock.now()` on entry and exit itself, and the test
    compares those values against `caller_utterance.ts` and
    `agent_audio_first_byte.ts` in the finished trace. That is the strongest
    available statement of "the harness does not charge its own compute to the
    agent": not "the number looks about right", but "the two timestamps are the
    agent's own, to the bit".
2.  **Harness-side work outside the window does not move the measurement.** The
    same scenario is run with a caller that burns half a second of clock per turn
    and one that burns none, and the recovered latencies are identical.

Latency is recovered with `lab.voice.calibration.recover_response_latencies` — the
function real evaluations use — rather than by re-deriving the subtraction here,
so this test cannot pass while the shipped definition is broken.
"""

from __future__ import annotations

import json

import pytest

from lab.clock import FakeClock
from lab.simulator.driver import (
    AgentTurn,
    Handoff,
    LLMCaller,
    ScriptedCaller,
    ToolInvocation,
    coerce_turn,
    run_scenario,
)
from lab.simulator.persona import CallerProfile, Goal, Persona
from lab.trace.schema import EventKind
from lab.voice.calibration import recover_response_latencies

AGENT_DELAY_S = 0.4


class RecordingAgent:
    """An agent that times itself, so the test can check the harness's arithmetic.

    It writes down the clock on entry and exit, waits out a known delay through
    the injected clock, and optionally reports tools and a handoff. Because it
    holds its own view of when it ran, the assertions never have to trust the
    harness's account of it.
    """

    def __init__(
        self,
        clock: FakeClock,
        *,
        delay_s: float = AGENT_DELAY_S,
        tools: list[ToolInvocation] | None = None,
        handoff: Handoff | None = None,
        replies: list[str] | None = None,
        end_after: int | None = None,
    ) -> None:
        self.clock = clock
        self.delay_s = delay_s
        self.tools = tools or []
        self.handoff = handoff
        self.replies = replies
        self.end_after = end_after
        self.entered: list[float] = []
        self.exited: list[float] = []
        self.heard: list[str] = []

    def __call__(self, utterance: str) -> AgentTurn:
        self.heard.append(utterance)
        self.entered.append(self.clock.now())
        self.clock.sleep(self.delay_s)
        self.exited.append(self.clock.now())
        turn = len(self.heard)
        text = (
            self.replies[turn - 1]
            if self.replies and turn <= len(self.replies)
            else f"reply {turn}"
        )
        return AgentTurn(
            text=text,
            agent="BookingAgent",
            tools=list(self.tools) if turn == 1 else [],
            handoff=self.handoff if turn == 1 else None,
            end_call=self.end_after is not None and turn >= self.end_after,
        )


def _profile(cooperativeness: float = 1.0) -> CallerProfile:
    return CallerProfile(
        persona=Persona(
            name="p", style="Plain and direct.", cooperativeness=cooperativeness
        ),
        goal=Goal(
            intent="book a table",
            facts={"party_size": "four", "dietary": "no gluten"},
            on_request_only=["dietary"],
            ask_patterns={"dietary": ["dietary", "allergies"]},
        ),
    )


# --------------------------------------------------------------------------- #
# The measurement boundary
# --------------------------------------------------------------------------- #


def test_boundary_events_carry_the_agents_own_timestamps() -> None:
    clock = FakeClock()
    agent = RecordingAgent(clock)
    trace = run_scenario(
        scenario_id="booking/simple",
        agent=agent,
        caller=ScriptedCaller(["a table for four", "yes please", "thanks"]),
        clock=clock,
    )

    openers = trace.events_of_kind(EventKind.CALLER_UTTERANCE)
    closers = trace.events_of_kind(EventKind.AGENT_AUDIO_FIRST_BYTE)
    assert [e.ts for e in openers] == agent.entered
    assert [e.ts for e in closers] == agent.exited
    assert recover_response_latencies(trace) == pytest.approx([AGENT_DELAY_S] * 3)


def test_harness_compute_outside_the_window_does_not_move_the_measurement() -> None:
    """A caller that burns clock between turns must not inflate agent latency."""

    class ExpensiveCaller(ScriptedCaller):
        """A caller whose own bookkeeping costs half a second per turn.

        Prompt assembly, scenario logic and result validation are all real harness
        work. This models it as bluntly as possible: 500 ms is more than the
        agent's entire 400 ms delay, so any leak would be unmissable.
        """

        def __init__(self, clock: FakeClock, script: list[str]) -> None:
            super().__init__(script)
            self._clock = clock

        def reply(self, agent_turn: AgentTurn) -> str | None:
            self._clock.advance(0.5)
            return super().reply(agent_turn)

    script = ["one", "two", "three"]

    cheap_clock = FakeClock()
    cheap = run_scenario(
        scenario_id="booking/simple",
        agent=RecordingAgent(cheap_clock),
        caller=ScriptedCaller(list(script)),
        clock=cheap_clock,
    )

    dear_clock = FakeClock()
    dear = run_scenario(
        scenario_id="booking/simple",
        agent=RecordingAgent(dear_clock),
        caller=ExpensiveCaller(dear_clock, list(script)),
        clock=dear_clock,
    )

    assert recover_response_latencies(cheap) == recover_response_latencies(dear)
    # ...and the injected overhead really was injected, so the equality above is
    # not vacuous: the sessions have very different total durations.
    assert dear.duration() > cheap.duration() + 0.9


def test_tool_calls_do_not_leak_into_the_latency_figure() -> None:
    clock = FakeClock()
    tools = [
        ToolInvocation(name="search_tables", args={"party_size": 4}, result={"ok": True}),
        ToolInvocation(name="create_booking", args={"name": "Ada"}, result={"ref": "R1"}),
    ]
    trace = run_scenario(
        scenario_id="booking/tools",
        agent=RecordingAgent(clock, tools=tools),
        caller=ScriptedCaller(["a table for four"]),
        clock=clock,
    )
    assert trace.tool_names() == ["search_tables", "create_booking"]
    assert recover_response_latencies(trace) == pytest.approx([AGENT_DELAY_S])


# --------------------------------------------------------------------------- #
# Estimated timestamps are flagged as estimated
# --------------------------------------------------------------------------- #


def test_interpolated_tool_timestamps_are_flagged_and_stay_inside_the_window() -> None:
    clock = FakeClock()
    agent = RecordingAgent(
        clock,
        tools=[ToolInvocation(name="check_policy", args={"topic": "dogs"})],
        handoff=Handoff(from_agent="GreeterAgent", to_agent="PolicyAgent"),
    )
    trace = run_scenario(
        scenario_id="policy/dogs",
        agent=agent,
        caller=ScriptedCaller(["do you allow dogs"]),
        clock=clock,
    )

    inner = trace.events_of_kind(
        EventKind.AGENT_HANDOFF, EventKind.TOOL_CALL, EventKind.TOOL_RESULT
    )
    assert len(inner) == 3
    t0, t1 = agent.entered[0], agent.exited[0]
    for event in inner:
        # Flagged in the data, not in a comment: no timing figure in this repo
        # may be derived from an event carrying ts_estimated.
        assert event.get("ts_estimated") is True
        assert t0 < event.ts < t1
    assert trace.is_ordered()


def test_observed_tool_timestamps_are_kept_and_not_flagged() -> None:
    clock = FakeClock()
    # A streaming adapter that really saw the call happen passes the instant it
    # saw it; the driver then has nothing to estimate.
    observed = 0.25
    agent = RecordingAgent(
        clock, tools=[ToolInvocation(name="search_tables", ts=observed)]
    )
    trace = run_scenario(
        scenario_id="booking/observed",
        agent=agent,
        caller=ScriptedCaller(["a table for four"]),
        clock=clock,
    )
    call = trace.first(EventKind.TOOL_CALL)
    assert call is not None
    assert call.ts == observed
    assert call.get("ts_estimated") is None


def test_out_of_window_observed_timestamps_are_clamped_to_keep_the_trace_ordered() -> None:
    clock = FakeClock()
    # An adapter reporting a nonsense instant must not be able to produce a trace
    # whose timestamps run backwards — every duration computed from one would be
    # meaningless, and the corruption would be invisible in a summary.
    agent = RecordingAgent(
        clock,
        tools=[
            ToolInvocation(name="search_tables", ts=99.0),
            ToolInvocation(name="create_booking"),
        ],
    )
    trace = run_scenario(
        scenario_id="booking/clamped",
        agent=agent,
        caller=ScriptedCaller(["a table for four"]),
        clock=clock,
    )
    assert trace.is_ordered()
    assert all(event.ts <= agent.exited[0] for event in trace.events)


# --------------------------------------------------------------------------- #
# The conversation loop
# --------------------------------------------------------------------------- #


def test_session_end_records_why_the_call_stopped() -> None:
    clock = FakeClock()
    trace = run_scenario(
        scenario_id="booking/hangup",
        agent=RecordingAgent(clock),
        caller=ScriptedCaller(["one", "two"]),
        clock=clock,
    )
    end = trace.last(EventKind.SESSION_END)
    assert end is not None
    assert end.get("reason") == "caller_hung_up"
    assert end.get("turns") == 2


def test_agent_ending_the_call_stops_the_loop() -> None:
    clock = FakeClock()
    caller = ScriptedCaller(["one", "two", "three", "four"])
    trace = run_scenario(
        scenario_id="booking/agent_ends",
        agent=RecordingAgent(clock, end_after=2),
        caller=caller,
        clock=clock,
    )
    end = trace.last(EventKind.SESSION_END)
    assert end is not None
    assert end.get("reason") == "agent_ended"
    assert end.get("turns") == 2
    # The caller had more to say: a short trace is explained by the reason field
    # rather than looking like a complete conversation.
    assert caller.lines_used == 2


def test_max_turns_is_recorded_not_raised() -> None:
    clock = FakeClock()

    class NeverEndingCaller:
        """Always has one more thing to say."""

        def opening(self) -> str:
            return "hello"

        def reply(self, agent_turn: AgentTurn) -> str:
            return "and another thing"

    trace = run_scenario(
        scenario_id="booking/loop",
        agent=RecordingAgent(clock),
        caller=NeverEndingCaller(),
        clock=clock,
        max_turns=3,
    )
    end = trace.last(EventKind.SESSION_END)
    assert end is not None
    # A truncated conversation is evidence, not an error: raising here would
    # throw away the three turns that show the loop happening.
    assert end.get("reason") == "max_turns"
    assert end.get("turns") == 3


def test_session_start_records_the_caller_profile_for_attribution() -> None:
    clock = FakeClock()
    profile = _profile()
    trace = run_scenario(
        scenario_id="booking/attributed",
        agent=RecordingAgent(clock),
        caller=ScriptedCaller(["hello"], profile=profile),
        clock=clock,
    )
    start = trace.first(EventKind.SESSION_START)
    assert start is not None
    assert start.get("persona") == "p"
    assert start.get("goal_gated_keys") == ["dietary"]
    assert start.get("caller") == "ScriptedCaller"


def test_transcripts_bracket_every_turn() -> None:
    clock = FakeClock()
    trace = run_scenario(
        scenario_id="booking/transcripts",
        agent=RecordingAgent(clock),
        caller=ScriptedCaller(["one", "two"]),
        clock=clock,
    )
    assert len(trace.events_of_kind(EventKind.TRANSCRIPT_IN)) == 2
    assert len(trace.events_of_kind(EventKind.TRANSCRIPT_OUT)) == 2
    assert trace.is_ordered()


# --------------------------------------------------------------------------- #
# ScriptedCaller behaviour
# --------------------------------------------------------------------------- #


def test_scripted_caller_answers_a_direct_question_about_a_gated_fact() -> None:
    clock = FakeClock()
    profile = _profile()
    caller = ScriptedCaller(
        ["a table for four", "that's all"], profile=profile, closing="thanks, bye"
    )
    agent = RecordingAgent(
        clock, replies=["any dietary requirements?", "booked", "goodbye"]
    )
    trace = run_scenario(
        scenario_id="booking/gated",
        agent=agent,
        caller=caller,
        clock=clock,
    )
    # The caller answered the question in front of it instead of reading the next
    # scripted line, and the release is recorded for a propagation check to use.
    assert "no gluten" in agent.heard[1]
    assert caller.released_facts == ["dietary"]
    assert trace.texts("caller")[1] == "no gluten"


def test_a_reluctant_persona_stalls_before_releasing_a_gated_fact() -> None:
    clock = FakeClock()
    caller = ScriptedCaller(["a table for four"], profile=_profile(cooperativeness=0.2))
    agent = RecordingAgent(
        clock, replies=["any allergies?", "sorry — any allergies?", "booked"]
    )
    run_scenario(
        scenario_id="booking/reluctant",
        agent=agent,
        caller=caller,
        clock=clock,
        max_turns=4,
    )
    assert agent.heard[1] == "Sorry, what was that?"
    assert agent.heard[2] == "no gluten"
    assert caller.ask_counts == {"dietary": 2}


def test_a_re_ask_is_answered_again_so_the_trace_can_record_it() -> None:
    """An agent that re-asks must not deadlock the caller — the trace is the record."""
    clock = FakeClock()
    caller = ScriptedCaller(["a table for four"], profile=_profile())
    agent = RecordingAgent(
        clock, replies=["any allergies?", "sorry, any allergies?", "booked"]
    )
    run_scenario(
        scenario_id="booking/reask",
        agent=agent,
        caller=caller,
        clock=clock,
        max_turns=4,
    )
    # Answered both times. Deciding that the second ask was a defect is a check's
    # job, not the caller's: the caller's job is to keep the conversation real
    # enough that the defect gets into the trace at all.
    assert agent.heard[1] == "no gluten"
    assert agent.heard[2] == "no gluten"
    assert caller.ask_counts == {"dietary": 2}


def test_scripted_caller_needs_at_least_one_line() -> None:
    with pytest.raises(ValueError, match="at least one line"):
        ScriptedCaller([])


# --------------------------------------------------------------------------- #
# Reply coercion
# --------------------------------------------------------------------------- #


def test_coerce_turn_accepts_the_documented_shorthands() -> None:
    assert coerce_turn("hello").text == "hello"
    turn = coerce_turn(("hello", [{"name": "check_policy", "args": {"topic": "dogs"}}]))
    assert turn.tools[0].name == "check_policy"
    already = AgentTurn(text="hello")
    assert coerce_turn(already) is already


def test_coerce_turn_rejects_anything_else() -> None:
    with pytest.raises(TypeError, match="must return an AgentTurn"):
        coerce_turn(42)  # type: ignore[arg-type]


def test_an_agent_may_be_a_bare_callable_returning_a_string() -> None:
    clock = FakeClock()
    trace = run_scenario(
        scenario_id="booking/minimal",
        agent=lambda utterance: f"you said {utterance}",
        caller=ScriptedCaller(["hello"]),
        clock=clock,
    )
    assert trace.texts("agent") == ["you said hello"]


# --------------------------------------------------------------------------- #
# LLMCaller: record once, replay forever, fail loudly when stale
# --------------------------------------------------------------------------- #


def _record_a_cassette(tmp_path, monkeypatch, agent_replies: list[str]) -> tuple:
    """Record a two-turn cassette with a stubbed provider call.

    `_complete` is the only method that touches the network, so stubbing it is
    exactly the seam a live run would use — the recording path under test is the
    real one, minus the provider.
    """
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    cassette = tmp_path / "caller.json"
    caller = LLMCaller(_profile(), cassette=cassette, model="stub-model")

    generated = iter(["hello, a table for four please", "great, thanks. [END OF CALL]"])
    monkeypatch.setattr(LLMCaller, "_complete", lambda self: next(generated))

    clock = FakeClock()
    agent = RecordingAgent(clock, replies=agent_replies)
    trace = run_scenario(
        scenario_id="booking/llm",
        agent=agent,
        caller=caller,
        clock=clock,
        max_turns=4,
    )
    assert caller.save() == cassette
    return cassette, trace


def test_llm_caller_records_then_replays_with_no_network_and_no_key(
    tmp_path, monkeypatch
) -> None:
    replies = ["certainly, for when?", "booked"]
    cassette, recorded = _record_a_cassette(tmp_path, monkeypatch, replies)

    stored = json.loads(cassette.read_text(encoding="utf-8"))
    assert [turn["index"] for turn in stored["turns"]] == [0, 1]
    assert all(turn["context_sha256"] for turn in stored["turns"])

    # Now the important half: no env var, no provider, and any attempt to reach
    # one is a hard failure rather than a silent live call.
    monkeypatch.delenv("LAB_LIVE_CALLER", raising=False)

    def explode(self):  # pragma: no cover - must never be reached
        raise AssertionError("replay must not touch the provider")

    monkeypatch.setattr(LLMCaller, "_complete", explode)

    replayer = LLMCaller(_profile(), cassette=cassette, model="stub-model")
    clock = FakeClock()
    replayed = run_scenario(
        scenario_id="booking/llm",
        agent=RecordingAgent(clock, replies=replies),
        caller=replayer,
        clock=clock,
        max_turns=4,
    )
    assert replayed.texts("caller") == recorded.texts("caller")
    # A pure replay records nothing, so a fixture cannot drift by being read.
    assert replayer.save() is None


def test_a_stale_cassette_raises_instead_of_answering_the_wrong_question(
    tmp_path, monkeypatch
) -> None:
    cassette, _ = _record_a_cassette(tmp_path, monkeypatch, ["certainly, for when?", "ok"])
    monkeypatch.delenv("LAB_LIVE_CALLER", raising=False)

    replayer = LLMCaller(_profile(), cassette=cassette, model="stub-model")
    clock = FakeClock()
    # The agent's behaviour changed. Positional replay would happily feed the
    # caller's old second line into a conversation that never asked for it, and
    # the suite would stay green while testing a fiction.
    with pytest.raises(ValueError, match="cassette is stale"):
        run_scenario(
            scenario_id="booking/llm",
            agent=RecordingAgent(clock, replies=["actually we're closed", "ok"]),
            caller=replayer,
            clock=clock,
            max_turns=4,
        )


def test_a_missing_recording_with_live_calls_off_names_the_way_out(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("LAB_LIVE_CALLER", raising=False)
    caller = LLMCaller(_profile(), cassette=tmp_path / "absent.json")
    with pytest.raises(RuntimeError, match="LAB_LIVE_CALLER"):
        caller.opening()
