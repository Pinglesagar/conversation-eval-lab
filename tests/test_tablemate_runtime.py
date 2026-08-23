"""The adapter: protocol conformance, produced latency, and record/replay.

WHAT THIS DEMONSTRATES
----------------------
That the system under test is measurable *through the harness's own front door*.
`lab.simulator` promises that an agent under test needs only a call signature; this
file is where that promise is exercised against a real multi-agent system, including
the parts that are easy to get wrong — that the time a turn takes is time the agent
actually spent on the injected clock, and that the model-backed path cannot reach a
provider unless somebody asked for it.
"""

from __future__ import annotations

import json

import pytest

from lab.clock import FakeClock, MonotonicClock
from lab.simulator import AgentTurn, AgentUnderTest, ScriptedCaller, run_scenario
from lab.trace.io import read_jsonl, write_jsonl
from tablemate.runtime import (
    DEFAULT_LATENCY,
    END_CALL_TOOL,
    LIVE_AGENT_ENV_VAR,
    LLMBackend,
    LatencyModel,
    MissingExchangeError,
    MissingPhrasingError,
    ModelClient,
    NotLiveError,
    PhraseCassette,
    PhrasingBackend,
    ScriptedBackend,
    SessionCassette,
    TableMate,
    build_agent,
)

BOOKING = [
    "Hello, I'd like a table for two on Thursday at 7:30pm.",
    "Okonkwo.",
    "That's lovely, thanks.",
]


# --------------------------------------------------------------------------- #
# The protocol
# --------------------------------------------------------------------------- #


def test_tablemate_satisfies_the_agent_under_test_protocol() -> None:
    assert isinstance(build_agent(), AgentUnderTest)


def test_one_call_is_one_turn_and_returns_an_agent_turn() -> None:
    agent = build_agent(clock=FakeClock())
    turn = agent("A table for two on Monday at 6pm please.")
    assert isinstance(turn, AgentTurn)
    assert turn.text and turn.agent
    assert [t.name for t in turn.tools] == ["search_tables"]


def test_the_factory_gives_every_conversation_its_own_diary() -> None:
    """`pass^k` needs this: repeat two must not inherit repeat one's bookings."""
    first, second = build_agent(clock=FakeClock()), build_agent(clock=FakeClock())
    for line in BOOKING:
        first(line)
    assert len(first.store.active_bookings()) == len(second.store.active_bookings()) + 1


def test_the_seed_hook_runs_before_the_conversation_starts() -> None:
    agent = build_agent(
        clock=FakeClock(), seed=lambda store: store.book_out("monday", "6pm")
    )
    turn = agent("A table for two on Monday at 6pm please.")
    assert turn.tools[0].result["available"] is False


def test_a_full_run_produces_a_trace_that_reads_back_unchanged(tmp_path) -> None:
    clock = FakeClock()
    trace = run_scenario(
        scenario_id="happy-two-covers-thursday",
        agent=build_agent(clock=clock),
        caller=ScriptedCaller(BOOKING),
        clock=clock,
    )
    path = write_jsonl(trace, tmp_path / "trace.jsonl")
    assert read_jsonl(path) == trace
    assert trace.is_ordered()
    assert trace.unknown_kinds() == set()
    assert trace.tool_names() == ["search_tables", "create_booking"]
    assert trace.handoff_pairs() == [("GreeterAgent", "BookingAgent")]


def test_handoffs_reach_the_trace_as_events_with_both_ends_named() -> None:
    clock = FakeClock()
    trace = run_scenario(
        scenario_id="handoffs",
        agent=build_agent(clock=clock),
        caller=ScriptedCaller(
            [
                "A table for two on Friday at 7pm.",
                "Are dogs allowed?",
                "Yes, go ahead.",
                "Ellery.",
            ]
        ),
        clock=clock,
    )
    assert trace.handoff_pairs() == [
        ("GreeterAgent", "BookingAgent"),
        ("BookingAgent", "PolicyAgent"),
        ("PolicyAgent", "BookingAgent"),
    ]


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #


def test_latency_is_spent_on_the_injected_clock() -> None:
    clock = FakeClock()
    agent = build_agent(clock=clock, latency=LatencyModel(think_s=1.0, per_tool_s=0.0, per_char_s=0.0))
    before = clock.now()
    agent("A table for two on Monday at 6pm please.")
    assert clock.now() == pytest.approx(before + 1.0)


def test_a_tool_using_turn_costs_more_than_a_silent_one() -> None:
    model = LatencyModel(think_s=0.1, per_tool_s=0.5, per_char_s=0.0)
    assert model.seconds_for(text="x", tool_calls=2) == pytest.approx(1.1)
    assert model.seconds_for(text="x", tool_calls=0) == pytest.approx(0.1)


def test_no_latency_model_means_no_delay_and_says_why_that_is_a_choice() -> None:
    """Every event on one instant: any check comparing timestamps has nothing to do."""
    clock = FakeClock()
    agent = build_agent(clock=clock, latency=None)
    agent("A table for two on Monday at 6pm please.")
    assert clock.now() == 0.0


def test_timestamps_advance_across_a_default_run() -> None:
    clock = FakeClock()
    trace = run_scenario(
        scenario_id="timing",
        agent=build_agent(clock=clock),
        caller=ScriptedCaller(BOOKING),
        clock=clock,
    )
    stamps = [event.ts for event in trace.events]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) > 1, "a run where nothing moves cannot be timed"
    assert trace.duration() > 0


def test_the_default_latency_is_the_order_of_magnitude_a_voice_call_lives_at() -> None:
    seconds = DEFAULT_LATENCY.seconds_for(text="x" * 120, tool_calls=1)
    assert 0.5 <= seconds <= 2.0


def test_a_real_clock_is_accepted_without_being_required() -> None:
    agent = TableMate(clock=MonotonicClock(), latency=None)
    assert agent("Do you take dogs?").text


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


def test_the_scripted_backend_speaks_the_line_it_was_given() -> None:
    from tablemate.agents import Speech

    assert ScriptedBackend().phrase(
        Speech(act="x", text="Hello."), agent="GreeterAgent"
    ) == "Hello."


def test_the_model_backend_records_then_replays_with_no_provider(tmp_path) -> None:
    """Record/replay is how "runs with zero API keys" and "uses a model" coexist."""
    cassette = tmp_path / "phrasing.json"
    recording = PhrasingBackend(
        cassette=cassette, completion=lambda system, line: f"[{line}]"
    )
    agent = build_agent(clock=FakeClock(), backend=recording)
    said = [agent(line).text for line in BOOKING]
    assert all(text.startswith("[") for text in said)
    assert recording.save() == cassette

    replaying = PhrasingBackend(cassette=cassette)
    assert replaying.live_enabled is False
    replayed = build_agent(clock=FakeClock(), backend=replaying)
    assert [replayed(line).text for line in BOOKING] == said
    assert replaying.save() is None, "a pure replay must record nothing"


def test_a_missing_phrasing_raises_rather_than_falling_back(tmp_path) -> None:
    """A silent fallback to the scripted line would make the comparison a lie."""
    backend = PhrasingBackend(cassette=tmp_path / "absent.json")
    agent = build_agent(clock=FakeClock(), backend=backend)
    with pytest.raises(MissingPhrasingError) as excinfo:
        agent("A table for two on Monday at 6pm please.")
    message = str(excinfo.value)
    assert LIVE_AGENT_ENV_VAR in message
    assert "ScriptedBackend" in message


def test_a_cassette_entry_cannot_be_replayed_into_a_different_line(tmp_path) -> None:
    """Keyed by what was asked for, so a stale fixture misses instead of lying."""
    first = PhraseCassette.key(
        model="m", agent="BookingAgent", act="ask.name", text="Your name?"
    )
    second = PhraseCassette.key(
        model="m", agent="BookingAgent", act="ask.name", text="And your name?"
    )
    third = PhraseCassette.key(
        model="m", agent="PolicyAgent", act="ask.name", text="Your name?"
    )
    assert len({first, second, third}) == 3


def test_a_cassette_that_is_not_a_cassette_is_refused(tmp_path) -> None:
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps({"turns": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="phrasing cassette"):
        PhraseCassette.load(path, model="m")


def test_the_recorded_cassette_keeps_the_source_line_beside_the_paraphrase(
    tmp_path,
) -> None:
    """So a reviewer can see what the model was given, not just what it said."""
    cassette = tmp_path / "phrasing.json"
    backend = PhrasingBackend(cassette=cassette, completion=lambda system, line: "Sure.")
    build_agent(clock=FakeClock(), backend=backend)("Do you take dogs?")
    backend.save()
    entries = json.loads(cassette.read_text(encoding="utf-8"))["phrasings"]
    entry = next(iter(entries.values()))
    assert entry["agent"] == "PolicyAgent"
    assert entry["phrased"] == "Sure."
    assert "dogs" in entry["source"].lower() or entry["source"]


def test_the_model_is_told_the_remit_of_the_agent_it_is_speaking_for(tmp_path) -> None:
    seen: list[str] = []

    def capture(system: str, line: str) -> str:
        seen.append(system)
        return line

    backend = PhrasingBackend(cassette=tmp_path / "c.json", completion=capture)
    build_agent(clock=FakeClock(), backend=backend)("Do you take dogs?")
    assert "policy sheet" in seen[0]
    assert "keep every fact" in seen[0].lower()


# --------------------------------------------------------------------------- #
# Reading the agent
# --------------------------------------------------------------------------- #


def test_the_diary_is_readable_as_ground_truth() -> None:
    agent = build_agent(clock=FakeClock())
    for line in BOOKING:
        agent(line)
    created = [b for b in agent.bookings() if b["booking_ref"] == "TM-2001"]
    assert created and created[0]["party_size"] == 2


def test_repr_says_what_it_is_without_dumping_the_conversation() -> None:
    agent = build_agent(clock=FakeClock())
    agent("Do you take dogs?")
    text = repr(agent)
    assert "ScriptedBackend" in text and "check_policy" in text


# --------------------------------------------------------------------------- #
# The live engine: a model in the decision seat
# --------------------------------------------------------------------------- #
#
# Every test below drives the whole live path — prompts, tool schemas, the tool
# loop, handoffs, hang-ups, the record and its projection — with an injected
# provider stand-in. No key, no network, no cassette needed. The one thing a
# stand-in cannot tell you is whether a real model behaves this way, which is why
# `fixtures/live_sessions.json` exists and why `tests/test_tablemate_bugs.py`
# replays it.


def _tool_call(name: str, args: dict, *, call_id: str = "c1", raw: str | None = None):
    """One assistant message asking for one tool call."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw if raw is not None else json.dumps(args),
                },
            }
        ],
    }


def _spoken(text: str) -> dict:
    return {"role": "assistant", "content": text}


class Replies:
    """A provider stand-in: a queue of assistant messages, and a log of the asks.

    The log is the interesting half. It is how a test asserts on what the *model
    was told* — which desk's remit, which tools were on the table, what the brief
    carried — and the brief is where two of the three seeded defects live.
    """

    def __init__(self, *messages: dict) -> None:
        self.queue = list(messages)
        self.asks: list[dict] = []

    def __call__(self, *, model, messages, tools):
        self.asks.append(
            {
                "system": messages[0]["content"],
                "tools": [t["function"]["name"] for t in tools],
                "messages": messages,
            }
        )
        return self.queue.pop(0) if self.queue else _spoken("Anything else?")


def _live(tmp_path, *messages: dict, **kwargs):
    """A live-backed agent over a stand-in, plus the stand-in and the backend."""
    replies = Replies(*messages)
    backend = LLMBackend(
        cassette=tmp_path / "sessions.json", completion=replies, **kwargs
    )
    return build_agent(clock=FakeClock(), backend=backend), replies, backend


def test_the_live_backend_supplies_an_engine_instead_of_a_phrasing_step(tmp_path) -> None:
    """The one variable that changes: who decides. There is no line left to phrase."""
    agent, _, _ = _live(tmp_path, _spoken("Lumen, good afternoon."))
    assert agent.phrases is False
    assert type(agent.orchestrator).__name__ == "LLMEngine"
    assert agent("Hello?").text == "Lumen, good afternoon."
    # And the default is still the scripted one, byte-identically.
    assert build_agent(clock=FakeClock()).phrases is True


def test_the_live_trace_is_the_same_shape_as_the_scripted_trace(tmp_path) -> None:
    """The property trace-first exists for. A contract cannot tell the two apart."""
    lines = ["Hello, a table for two on Thursday at 7:30pm.", "Lovely, thank you."]

    def drive(backend) -> object:
        clock = FakeClock()
        return run_scenario(
            scenario_id="shape",
            agent=build_agent(clock=clock, backend=backend),
            caller=ScriptedCaller(lines),
            clock=clock,
            session_id="s",
            max_turns=6,
        )

    scripted = drive(ScriptedBackend())
    _, replies, backend = _live(
        tmp_path,
        _tool_call("transfer_to_booking_desk", {"reason": "a new booking"}),
        _tool_call(
            "search_tables", {"date": "Thursday", "time": "7:30pm", "party_size": 2}
        ),
        _tool_call(
            "create_booking",
            {
                "name": "Okonkwo",
                "date": "Thursday",
                "time": "7:30pm",
                "party_size": 2,
                "notes": "",
            },
            call_id="c2",
        ),
        _spoken("You are booked — the reference is TM-2001."),
    )
    live = drive(backend)

    def shape(trace) -> tuple:
        return (
            sorted({e.kind for e in trace.events}),
            sorted({e.actor for e in trace.events}),
            sorted({key for e in trace.events for key in e.payload}),
        )

    assert shape(live) == shape(scripted)
    # And only the five real tools ever reach the trace, whichever backend ran.
    from tablemate.tools import TOOL_NAMES

    for trace in (scripted, live):
        names = {
            e.payload["name"]
            for e in trace.events
            if e.kind == "tool_call"
        }
        assert names <= set(TOOL_NAMES)


def test_a_transfer_is_a_handoff_event_and_never_a_tool_call(tmp_path) -> None:
    """A control decision belongs in the trace's own vocabulary, or nowhere."""
    agent, _, _ = _live(
        tmp_path,
        _tool_call("transfer_to_policy_desk", {"reason": "dogs"}),
        _tool_call("check_policy", {"topic": "dogs"}),
        _spoken("Dogs are very welcome in the bar area."),
    )
    turn = agent("Do you take dogs?")
    assert turn.handoff is not None
    assert (turn.handoff.from_agent, turn.handoff.to_agent) == (
        "GreeterAgent",
        "PolicyAgent",
    )
    assert [t.name for t in turn.tools] == ["check_policy"]
    assert agent.tool_names() == ["check_policy"]


def test_the_handoff_reason_is_the_string_the_scripted_orchestrator_writes(
    tmp_path,
) -> None:
    """The reason lands in the trace and is grouped on. Two spellings, two columns."""
    from tablemate.agents import POLICY, remit

    agent, _, _ = _live(
        tmp_path,
        _tool_call("transfer_to_policy_desk", {}),
        _spoken("Dogs are welcome."),
    )
    turn = agent("Do you take dogs?")
    assert turn.handoff.reason == f"caller needs {remit(POLICY)}"


def test_only_one_handoff_per_turn_and_the_model_is_told_why(tmp_path) -> None:
    """`AgentTurn` holds one handoff; a second would be an event nobody observed."""
    agent, replies, _ = _live(
        tmp_path,
        _tool_call("transfer_to_policy_desk", {}),
        _tool_call("transfer_to_booking_desk", {}, call_id="c2"),
        _spoken("Dogs are welcome — shall I take a booking?"),
    )
    turn = agent("Do you take dogs, and can I book?")
    assert turn.handoff.to_agent == "PolicyAgent"
    refusals = [
        m
        for ask in replies.asks
        for m in ask["messages"]
        if m.get("role") == "tool" and "already handed" in str(m.get("content"))
    ]
    assert refusals, "the model must be told its second transfer was refused"


def test_ending_the_call_is_a_flag_on_the_turn_not_a_tool_event(tmp_path) -> None:
    agent, _, _ = _live(
        tmp_path,
        _tool_call("transfer_to_policy_desk", {}),
        _tool_call(END_CALL_TOOL, {"reason": "caller said goodbye"}),
        _spoken("Thanks for calling — goodbye."),
    )
    turn = agent("That is all, thanks — bye.")
    assert turn.end_call is True
    assert turn.tools == []


def test_a_tool_outside_the_allow_list_is_refused_counted_and_left_out(tmp_path) -> None:
    """The model reaching for a colleague's tool is a measurement, not a crash.

    `Toolbox.invoke` raises `ToolNotAllowed` because in the deterministic build an
    off-list call can only be a wiring defect. Here it is the model being wrong,
    so it is refused, reported back, and counted — and it stays out of the trace,
    because a call that was never dispatched did not happen.
    """
    agent, _, backend = _live(
        tmp_path,
        _tool_call("transfer_to_policy_desk", {}),
        _tool_call(
            "create_booking",
            {"name": "X", "date": "Friday", "time": "7pm", "party_size": 2},
        ),
        _spoken("I will pass you to the booking desk."),
    )
    agent("Do you take dogs? And book me a table.")
    assert agent.tool_names() == []
    assert backend.diagnostics()["blocked_calls"] == ["PolicyAgent:create_booking"]


def test_each_desk_is_offered_its_own_allow_list_and_nothing_more(tmp_path) -> None:
    agent, replies, _ = _live(
        tmp_path,
        _tool_call("transfer_to_booking_desk", {}),
        _spoken("How many will there be?"),
    )
    agent("I would like to book a table.")
    greeter, booking = replies.asks[0], replies.asks[1]
    assert greeter["tools"] == [
        "transfer_to_booking_desk",
        "transfer_to_amendment_desk",
        "transfer_to_policy_desk",
    ]
    assert set(booking["tools"]) == {
        "search_tables",
        "create_booking",
        "transfer_to_amendment_desk",
        "transfer_to_policy_desk",
        END_CALL_TOOL,
    }


def test_malformed_tool_arguments_are_not_repaired(tmp_path) -> None:
    """Guessing what the model meant would hide the defect the trace should show."""
    agent, _, _ = _live(
        tmp_path,
        _tool_call("transfer_to_booking_desk", {}),
        _tool_call("search_tables", {}, raw="{not json at all"),
        _spoken("Let me check that again."),
    )
    turn = agent("A table for two, please.")
    assert [(c.name, c.ok) for c in turn.tools] == [("search_tables", False)]
    assert "bad arguments" in (turn.tools[0].error or "")


def test_a_turn_that_would_be_silent_asks_once_more_with_no_tools_offered(
    tmp_path,
) -> None:
    """An empty turn ends the conversation and reads as a short call, not a fault."""
    agent, replies, backend = _live(
        tmp_path,
        _tool_call("transfer_to_policy_desk", {}),
        _tool_call("check_policy", {"topic": "dogs"}),
        _spoken(""),
        _spoken("Dogs are welcome in the bar."),
    )
    turn = agent("Do you take dogs?")
    assert turn.text == "Dogs are welcome in the bar."
    assert backend.diagnostics()["silent_turns"] == 1
    assert replies.asks[-1]["tools"] == []


def test_the_tool_loop_is_capped_and_the_truncation_is_counted(tmp_path) -> None:
    """A model that will not stop calling tools is a real failure mode and a real bill."""
    search = _tool_call(
        "search_tables", {"date": "Friday", "time": "7pm", "party_size": 2}
    )
    agent, replies, backend = _live(
        tmp_path,
        _tool_call("transfer_to_booking_desk", {}),
        search,
        search,
        search,
        _spoken("Still checking, sorry."),
        max_tool_steps=1,
    )
    turn = agent("A table for two on Friday at 7pm.")
    assert backend.diagnostics()["truncated_turns"] == 1
    assert turn.text == "Still checking, sorry."
    assert replies.asks[-1]["tools"] == [], "the last ask must force a sentence"


# ------------------------------------------------------- record, replay, refuse


def test_the_live_backend_records_then_replays_with_no_provider(tmp_path) -> None:
    """The cardinal rule, for the path that costs money."""
    cassette = tmp_path / "sessions.json"
    lines = ["Do you take dogs?", "Thanks, bye."]
    messages = (
        _tool_call("transfer_to_policy_desk", {}),
        _tool_call("check_policy", {"topic": "dogs"}),
        _spoken("Dogs are welcome in the bar."),
        _tool_call(END_CALL_TOOL, {}),
        _spoken("Goodbye."),
    )
    recording = LLMBackend(cassette=cassette, completion=Replies(*messages))
    agent = build_agent(clock=FakeClock(), backend=recording)
    said = [agent(line).text for line in lines]
    assert recording.save() == cassette

    replaying = LLMBackend(cassette=cassette)
    assert replaying.live_enabled is False
    replayed = build_agent(clock=FakeClock(), backend=replaying)
    assert [replayed(line).text for line in lines] == said
    assert replaying.save() is None, "a pure replay must record nothing"


def test_a_missing_exchange_raises_rather_than_falling_back(tmp_path) -> None:
    backend = LLMBackend(cassette=tmp_path / "absent.json")
    agent = build_agent(clock=FakeClock(), backend=backend)
    with pytest.raises(MissingExchangeError) as excinfo:
        agent("A table for two on Monday at 6pm please.")
    message = str(excinfo.value)
    assert LIVE_AGENT_ENV_VAR in message
    assert "ScriptedBackend" in message


def test_a_recorded_exchange_cannot_be_replayed_into_a_different_request() -> None:
    """Keyed on the whole request, so a stale cassette misses instead of lying."""
    base = {
        "model": "m",
        "agent": "BookingAgent",
        "messages": [{"role": "user", "content": "table for two"}],
        "tools": ["search_tables"],
    }
    keys = {
        SessionCassette.key(**base),
        SessionCassette.key(**{**base, "agent": "PolicyAgent"}),
        SessionCassette.key(**{**base, "tools": ["create_booking"]}),
        SessionCassette.key(**{**base, "model": "n"}),
        SessionCassette.key(
            **{**base, "messages": [{"role": "user", "content": "table for three"}]}
        ),
    }
    assert len(keys) == 5


def test_a_phrase_cassette_is_not_a_session_cassette(tmp_path) -> None:
    """Two cassettes, two shapes. Loading one as the other raises, both ways."""
    phrases = tmp_path / "phrases.json"
    phrases.write_text(json.dumps({"phrasings": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="session cassette"):
        SessionCassette.load(phrases, model="m")
    sessions = tmp_path / "sessions.json"
    sessions.write_text(json.dumps({"exchanges": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="phrasing cassette"):
        PhraseCassette.load(sessions, model="m")


def test_the_cassette_records_how_it_was_sampled(tmp_path) -> None:
    """A rate measured at one temperature is not a rate at another. Say which."""
    cassette = tmp_path / "sessions.json"
    backend = LLMBackend(
        cassette=cassette,
        completion=Replies(_spoken("Hello.")),
        temperature=0.7,
        replay=False,
    )
    build_agent(clock=FakeClock(), backend=backend)("Hello?")
    backend.save()
    provenance = json.loads(cassette.read_text(encoding="utf-8"))["recorded_with"]
    assert provenance["temperature"] == 0.7
    assert provenance["replayed_during_recording"] is False


def test_replay_off_asks_again_and_keeps_both_answers(tmp_path) -> None:
    """Without this, k>1 measures the cassette's determinism, not the model's."""
    cassette = tmp_path / "sessions.json"
    replies = Replies(_spoken("First."), _spoken("Second."))
    backend = LLMBackend(cassette=cassette, completion=replies, replay=False)
    first = build_agent(clock=FakeClock(), backend=backend)("Hello?").text
    second = build_agent(clock=FakeClock(), backend=backend)("Hello?").text
    assert (first, second) == ("First.", "Second.")
    client = backend.client
    key = SessionCassette.key(
        model=client.model,
        agent="GreeterAgent",
        messages=replies.asks[0]["messages"],
        tools=replies.asks[0]["tools"],
    )
    assert client.cassette.variants(key) == 2
    assert client.cassette.get(key)["content"] == "First.", "replay takes the first"


def test_it_refuses_to_run_live_without_the_flag_and_says_which_is_missing(
    tmp_path, monkeypatch
) -> None:
    """The refusal the requirement asks for, in both of its two forms."""
    for name in ModelClient.KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(LIVE_AGENT_ENV_VAR, raising=False)

    client = ModelClient(cassette=tmp_path / "c.json")
    with pytest.raises(NotLiveError, match=LIVE_AGENT_ENV_VAR):
        client.require_live()

    monkeypatch.setenv(LIVE_AGENT_ENV_VAR, "1")
    flagged = ModelClient(cassette=tmp_path / "c.json")
    with pytest.raises(NotLiveError, match="no provider key"):
        flagged.require_live()
    assert flagged.live_enabled is False

    monkeypatch.setenv("LAB_KEY", "not-a-real-key")
    keyed = ModelClient(cassette=tmp_path / "c.json")
    assert keyed.refusal() is None
    assert keyed.live_enabled is True


def test_a_rate_limit_is_retried_and_a_refusal_is_not(tmp_path, monkeypatch) -> None:
    """A retried call inside a measured window is a discarded sample, not a slow one.

    So the retry budget is capped and exhaustion raises. What must never happen is
    the opposite: a 400 retried five times, or a 429 turned into an exception the
    run reports as agent behaviour.
    """
    from tablemate.runtime import _is_rate_limit

    class RateLimitError(Exception):
        pass

    assert _is_rate_limit(RateLimitError("slow down")) is True
    assert _is_rate_limit(ValueError("bad request")) is False

    class WithStatus(Exception):
        status_code = 429

    assert _is_rate_limit(WithStatus()) is True
    WithStatus.status_code = 400
    assert _is_rate_limit(WithStatus()) is False
