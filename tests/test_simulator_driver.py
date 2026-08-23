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
    VERBOSITY_TOKEN_BUDGET,
    AgentTurn,
    CassetteKey,
    DisclosureLeakError,
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


# --------------------------------------------------------------------------- #
# LLMCaller: the guarantees a prompt cannot give
# --------------------------------------------------------------------------- #


def _live(monkeypatch, utterances: list[str]) -> None:
    """Turn the live switch on and stub the one method that reaches a provider."""
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    generated = iter(utterances)
    monkeypatch.setattr(LLMCaller, "_complete", lambda self: next(generated))


def _run(caller, agent_replies: list[str], *, max_turns: int = 8):
    clock = FakeClock()
    return run_scenario(
        scenario_id="booking/llm",
        agent=RecordingAgent(clock, replies=agent_replies),
        caller=caller,
        clock=clock,
        max_turns=max_turns,
    )


def test_the_cassette_key_is_written_into_the_fixture_and_shapes_its_path(
    tmp_path, monkeypatch
) -> None:
    _live(monkeypatch, ["a table for four please", "lovely, thanks [END OF CALL]"])
    caller = LLMCaller.for_scenario(
        _profile(),
        scenario_id="happy-two-covers",
        root=tmp_path,
        model="stub/route",
        model_label="stub-family",
        temperature=0.7,
        variant=3,
        max_utterances=6,
    )
    _run(caller, ["for when?", "booked"])
    written = caller.save()

    # The path is derived from the identity, not chosen by hand: one directory per
    # scenario, one file per (persona, prompt, repeat).
    assert written is not None
    assert written.parent.name == "happy-two-covers"
    assert written.name.startswith("p-")
    # The repeat index and the turn budget are both in the name: two readings of
    # the same scenario under different settings must not overwrite each other.
    assert written.name.endswith("-r3-b6.json")

    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["key"]["scenario_id"] == "happy-two-covers"
    assert document["key"]["variant"] == 3
    assert document["key"]["temperature"] == 0.7
    assert document["key"]["turn_budget"] == 6
    # The label, not the route. A route can name a private deployment; a committed
    # fixture is public, and the model family is the part a reader needs.
    assert document["key"]["model"] == "stub-family"
    assert "stub/route" not in written.read_text(encoding="utf-8")


def test_two_repeats_of_one_scenario_get_two_cassettes(tmp_path) -> None:
    """The property that makes a stability measurement possible at all.

    k repeats at a non-zero temperature are k different conversations. One
    cassette for all of them would replay the first k times and report a flake
    rate of zero — pass^k machinery proving the absence of the variance it exists
    to find.
    """
    profile = _profile()
    paths = {
        LLMCaller.for_scenario(
            profile, scenario_id="s", root=tmp_path, model="m", variant=i
        ).cassette_path
        for i in range(5)
    }
    assert len(paths) == 5


def test_a_cassette_recorded_for_another_conversation_is_refused(
    tmp_path, monkeypatch
) -> None:
    _live(monkeypatch, ["a table for four please", "thanks [END OF CALL]"])
    recorder = LLMCaller.for_scenario(
        _profile(), scenario_id="s", root=tmp_path, model="m", temperature=0.7
    )
    _run(recorder, ["certainly", "booked"])
    path = recorder.save()
    assert path is not None
    monkeypatch.delenv("LAB_LIVE_CALLER", raising=False)

    # Same file, but this run wants a different temperature. Replaying it would
    # report one distribution's variance as another's. Construction is where the
    # identity is checked, so the refusal lands before a single turn can be
    # replayed out of context — and the message names the field that differs.
    with pytest.raises(ValueError, match="temperature: fixture has 0.7"):
        LLMCaller(
            _profile(),
            cassette=path,
            model="m",
            temperature=0.0,
            key=CassetteKey.build(
                scenario_id="s",
                profile=_profile(),
                model="m",
                temperature=0.0,
                turn_budget=12,
            ),
        )


def test_a_cassette_with_no_identity_block_is_refused_when_one_is_declared(
    tmp_path,
) -> None:
    path = tmp_path / "anonymous.json"
    path.write_text(json.dumps({"turns": []}), encoding="utf-8")
    key = CassetteKey.build(
        scenario_id="s", profile=_profile(), model="m", temperature=0.0, turn_budget=12
    )
    with pytest.raises(ValueError, match="carries no identity block"):
        LLMCaller(_profile(), cassette=path, key=key)


def test_a_hand_written_cassette_still_works_when_no_identity_is_claimed(
    tmp_path,
) -> None:
    """Provenance is demanded only of fixtures that claim it.

    A unit test writing three turns by hand is a legitimate cassette. Requiring an
    identity block from it would make the strict path unreachable in the tests
    that matter most.
    """
    path = tmp_path / "hand.json"
    path.write_text(json.dumps({"turns": []}), encoding="utf-8")
    caller = LLMCaller(_profile(), cassette=path)
    assert caller.key is None


def test_a_looping_caller_stops_instead_of_burning_the_turn_budget(
    tmp_path, monkeypatch
) -> None:
    """The guard behind `CALLER_RULES`' anti-loop clause.

    A caller that keeps rephrasing against an agent that keeps declining exhausts
    max_turns, and a max_turns stop reads in a report exactly like an agent that
    could not finish the job. Two identical lines in a row is a loop with no
    reading under which it is progress.
    """
    line = "I would like a table for four"
    _live(monkeypatch, [line, line, line])
    caller = LLMCaller(_profile(), cassette=tmp_path / "loop.json", model="m")
    trace = _run(caller, ["nothing available", "still nothing", "still nothing"])

    assert caller.stop_reason == "repeated_line"
    assert caller.utterances == [line, line]
    end = trace.first(EventKind.SESSION_END)
    assert end is not None and end.get("reason") == "caller_hung_up"


def test_a_stall_may_recur_before_the_repeat_guard_fires(tmp_path, monkeypatch) -> None:
    """`REPEAT_LIMIT` is two, and the reason is the reluctant persona.

    "Sorry, what was that?" legitimately recurs across a long call. A guard that
    fired on the second outing would end exactly the scenarios that exist to test
    re-asking.
    """
    stall = "sorry, what was that?"
    _live(monkeypatch, [stall, "four", stall, "Friday", stall])
    caller = LLMCaller(
        _profile(cooperativeness=0.2), cassette=tmp_path / "stall.json", model="m"
    )
    _run(caller, ["how many?", "which day?", "what time?", "and the name?", "ok"])
    # Said twice and tolerated; the third outing is the loop.
    assert caller.utterances.count(stall) == 3
    assert caller.stop_reason == "repeated_line"


def test_the_turn_budget_is_checked_before_a_completion_is_paid_for(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    calls = {"n": 0}

    def counted(self):
        calls["n"] += 1
        return f"line {calls['n']}"

    monkeypatch.setattr(LLMCaller, "_complete", counted)
    caller = LLMCaller(
        _profile(), cassette=tmp_path / "budget.json", model="m", max_utterances=3
    )
    _run(caller, ["a", "b", "c", "d", "e", "f"], max_turns=10)
    # Three utterances bought, and the fourth turn declined without spending —
    # the point of the check is the money, so it happens before the request.
    assert calls["n"] == 3
    assert caller.stop_reason == "turn_budget"


def test_a_gated_fact_said_before_it_was_asked_for_is_recorded_as_a_leak(
    tmp_path, monkeypatch
) -> None:
    """The fault that would most quietly ruin a result.

    `on_request_only` is what makes "did the agent ask?" answerable. A caller that
    volunteers the answer makes every check downstream pass for the wrong reason,
    and nothing in the trace looks unusual. A prompt can only ask; this measures.
    """
    _live(monkeypatch, ["a table for four, and no gluten please", "thanks [END OF CALL]"])
    caller = LLMCaller(_profile(), cassette=tmp_path / "leak.json", model="m")
    _run(caller, ["certainly", "booked"])

    assert [leak.fact for leak in caller.leaks] == ["dietary"]
    assert caller.leaks[0].turn_index == 0
    # Released, but never asked for: the two lists are what distinguish "the agent
    # asked and got it" from "the agent was handed it".
    assert caller.released_facts == ["dietary"]
    assert caller.asked_facts == []
    # The fixture carries the leak, so a replayed run reports the same fault.
    path = caller.save()
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["leaks"] == [
        {"turn_index": 0, "fact": "dietary"}
    ]


def test_a_gated_fact_given_when_asked_is_not_a_leak(tmp_path, monkeypatch) -> None:
    _live(monkeypatch, ["a table for four", "no gluten [END OF CALL]"])
    caller = LLMCaller(_profile(), cassette=tmp_path / "clean.json", model="m")
    _run(caller, ["any dietary requirements?", "booked"])
    assert caller.leaks == []
    assert caller.asked_facts == ["dietary"]
    assert caller.released_facts == ["dietary"]


def test_leak_detection_does_not_fire_on_a_value_inside_another_word(
    tmp_path, monkeypatch
) -> None:
    """A leak detector with false positives gets switched off, which is worse."""
    profile = CallerProfile(
        persona=Persona(name="p", style="Plain."),
        goal=Goal(
            intent="book",
            facts={"party_size": "2"},
            on_request_only=["party_size"],
            ask_patterns={"party_size": ["how many"]},
        ),
    )
    _live(monkeypatch, ["can I book for 20:00 or thereabouts", "ta [END OF CALL]"])
    caller = LLMCaller(profile, cassette=tmp_path / "bounded.json", model="m")
    _run(caller, ["certainly", "booked"])
    assert caller.leaks == []


def test_on_leak_raise_stops_the_run(tmp_path, monkeypatch) -> None:
    _live(monkeypatch, ["four of us, and no gluten"])
    caller = LLMCaller(
        _profile(), cassette=tmp_path / "strict.json", model="m", on_leak="raise"
    )
    with pytest.raises(DisclosureLeakError, match="dietary"):
        _run(caller, ["certainly"])


def test_verbosity_becomes_an_enforced_token_budget(tmp_path) -> None:
    """A prompt instruction the API enforces, not only one the model is asked for."""
    for verbosity, expected in VERBOSITY_TOKEN_BUDGET.items():
        profile = CallerProfile(
            persona=Persona(name="p", style="s", verbosity=verbosity),
            goal=Goal(intent="book"),
        )
        caller = LLMCaller(profile, cassette=tmp_path / f"{verbosity}.json")
        assert caller.max_tokens == expected
    # Explicit beats derived.
    assert (
        LLMCaller(_profile(), cassette=tmp_path / "x.json", max_tokens=7).max_tokens == 7
    )


def test_a_rate_limit_is_waited_out_rather_than_reported_as_a_failure(
    tmp_path, monkeypatch
) -> None:
    """A 429 is an instruction to wait, not a result.

    Not retrying it turns the provider's queue depth into a flaky agent — a
    measurement of somebody else's load, reported as this agent's variance.
    """
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    monkeypatch.setenv("LAB_CALLER_MODEL", "stub/route")
    slept: list[float] = []
    attempts = {"n": 0}

    class RateLimitError(Exception):
        pass

    def flaky_completion(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimitError("429 slow down")
        return {"choices": [{"message": {"content": "hello there"}}]}

    import litellm

    monkeypatch.setattr(litellm, "completion", flaky_completion)
    caller = LLMCaller(
        _profile(),
        cassette=tmp_path / "retry.json",
        retry_base_s=1.0,
        sleep=slept.append,
    )
    assert caller._complete() == "hello there"
    assert attempts["n"] == 3
    # Exponential, from the declared base.
    assert slept == [1.0, 2.0]


def test_a_non_rate_limit_error_is_not_retried(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    monkeypatch.setenv("LAB_CALLER_MODEL", "stub/route")
    attempts = {"n": 0}

    def broken(**kwargs):
        attempts["n"] += 1
        raise ValueError("bad request")

    import litellm

    monkeypatch.setattr(litellm, "completion", broken)
    caller = LLMCaller(_profile(), cassette=tmp_path / "hard.json", sleep=lambda _: None)
    with pytest.raises(ValueError, match="bad request"):
        caller._complete()
    assert attempts["n"] == 1


def test_no_model_id_is_hardcoded_in_the_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    monkeypatch.delenv("LAB_CALLER_MODEL", raising=False)
    caller = LLMCaller(_profile(), cassette=tmp_path / "nomodel.json")
    with pytest.raises(RuntimeError, match="LAB_CALLER_MODEL"):
        caller._complete()


def test_the_live_switch_is_checked_inside_the_request_too(tmp_path, monkeypatch) -> None:
    """Belt and braces on the one method that can spend money."""
    monkeypatch.delenv("LAB_LIVE_CALLER", raising=False)
    caller = LLMCaller(_profile(), cassette=tmp_path / "off.json", model="stub/route")
    with pytest.raises(RuntimeError, match="LAB_LIVE_CALLER"):
        caller._complete()


def test_a_sentinel_in_the_same_breath_as_the_last_answer_still_gets_the_agent_a_turn(
    tmp_path, monkeypatch
) -> None:
    """The instrument bug the first live flake band found, and its fix.

    Two conversations in forty appended `[END OF CALL]` to the turn carrying the
    caller's last answer — the name the agent had just asked for. Ending the call
    there is the harness hanging up on a caller who had said everything the agent
    needed: no `create_booking` follows, the contract fails, and the finding is
    filed against the agent. So the sentinel is stripped, the words are delivered,
    the agent gets its turn, and the line goes dead on the *next* one.
    """
    _live(monkeypatch, ["a table for four", "no gluten, and it's Ada Rowe [END OF CALL]"])
    caller = LLMCaller(_profile(), cassette=tmp_path / "same-breath.json", model="m")
    trace = _run(caller, ["any dietary requirements, and your name?", "booked, thanks"])

    # The last answer was heard, with the marker removed from what was said.
    said = trace.texts("caller")
    assert said[-1] == "no gluten, and it's Ada Rowe"
    assert "[END OF CALL]" not in " ".join(said)
    # And the agent got a turn after it — the whole point.
    assert trace.texts("agent")[-1] == "booked, thanks"
    assert caller.stop_reason == "goal_reached"


def test_the_deferred_hang_up_costs_nothing(tmp_path, monkeypatch) -> None:
    """The turn after a same-breath sentinel is decided, so it is not bought."""
    monkeypatch.setenv("LAB_LIVE_CALLER", "1")
    calls = {"n": 0}
    lines = iter(["hello there", "that's everything, thanks [END OF CALL]"])

    def counted(self):
        calls["n"] += 1
        return next(lines)

    monkeypatch.setattr(LLMCaller, "_complete", counted)
    caller = LLMCaller(_profile(), cassette=tmp_path / "deferred.json", model="m")
    _run(caller, ["yes?", "noted", "still here?"])
    assert calls["n"] == 2


def test_a_sentinel_on_its_own_ends_the_call_at_once(tmp_path, monkeypatch) -> None:
    """The compliant case is unchanged: nothing was said, so nothing is delivered."""
    _live(monkeypatch, ["a table for four", "[END OF CALL]"])
    caller = LLMCaller(_profile(), cassette=tmp_path / "clean-end.json", model="m")
    trace = _run(caller, ["certainly", "anything else?"])
    assert trace.texts("caller") == ["a table for four"]
    assert caller.stop_reason == "goal_reached"


def test_an_empty_completion_is_not_recorded_as_a_satisfied_caller(
    tmp_path, monkeypatch
) -> None:
    """A provider returning nothing ends the call, but for a different reason.

    Both cases produce no words and both stop the conversation. Filing them under
    one label would let a broken completion be counted as a caller who got what it
    came for, which is a silent hole in exactly the column a reader trusts.
    """
    # `_complete` strips its result, so an all-whitespace completion arrives here
    # as the empty string.
    _live(monkeypatch, ["a table for four", ""])
    caller = LLMCaller(_profile(), cassette=tmp_path / "empty.json", model="m")
    _run(caller, ["certainly", "and?"])
    assert caller.stop_reason == "empty_utterance"
