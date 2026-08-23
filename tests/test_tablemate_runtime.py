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
    LIVE_AGENT_ENV_VAR,
    LatencyModel,
    LLMBackend,
    MissingPhrasingError,
    PhraseCassette,
    ScriptedBackend,
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
    recording = LLMBackend(
        cassette=cassette, completion=lambda system, line: f"[{line}]"
    )
    agent = build_agent(clock=FakeClock(), backend=recording)
    said = [agent(line).text for line in BOOKING]
    assert all(text.startswith("[") for text in said)
    assert recording.save() == cassette

    replaying = LLMBackend(cassette=cassette)
    assert replaying.live_enabled is False
    replayed = build_agent(clock=FakeClock(), backend=replaying)
    assert [replayed(line).text for line in BOOKING] == said
    assert replaying.save() is None, "a pure replay must record nothing"


def test_a_missing_phrasing_raises_rather_than_falling_back(tmp_path) -> None:
    """A silent fallback to the scripted line would make the comparison a lie."""
    backend = LLMBackend(cassette=tmp_path / "absent.json")
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
    backend = LLMBackend(cassette=cassette, completion=lambda system, line: "Sure.")
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

    backend = LLMBackend(cassette=tmp_path / "c.json", completion=capture)
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
