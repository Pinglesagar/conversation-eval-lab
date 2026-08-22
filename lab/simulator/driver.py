"""The scenario driver: run a caller against an agent, emit a trace.

WHAT THIS DEMONSTRATES
----------------------
Three things, in order of how much they matter.

**1. The measurement boundary is honoured here too.**
`lab.voice.calibration` proves the harness can recover a known delay; that proof
is only worth anything if ordinary runs use the same discipline. So the loop in
`run_scenario` captures `t0` and `t1` as bare floats either side of the single
call into the system under test, and builds every event afterwards, back-dated
with `ts=`. Nothing else sits between the two clock reads — not coercion, not
logging, not the caller's next line. The calibration gate and the real driver are
the same shape on purpose: a gate that validates a code path nobody runs is
theatre.

**2. The agent under test is a callable, not a subclass.**
`AgentUnderTest` is a `Protocol`: anything that takes an utterance and returns a
reply qualifies. No base class to inherit, no registry to join, no import of the
harness inside the system being measured. That is what makes `lab` reusable —
the harness depends on a call signature, and a call signature is something a
wrapper can produce for a framework `lab` has never heard of.

**3. Two callers, one interface.**
`ScriptedCaller` is deterministic and drives every offline test. `LLMCaller`
generates its turns from a model, records them to a cassette on first run, and
replays from that cassette forever after — so a live-generated conversation
becomes a fixture that reproduces with no API key. Both satisfy `Caller`, so
switching between them changes nothing about the driver, the trace, or the
checks. The recorded path additionally verifies the *context* it is replaying
into (a hash of the conversation so far), which means a stale cassette raises
instead of quietly answering a question it was never asked.

TOOL-EVENT TIMESTAMPS ARE ESTIMATED, AND SAY SO
-----------------------------------------------
An `AgentUnderTest` reports its tool calls when it returns, so the harness knows
they happened between `t0` and `t1` but not when. Rather than pretend, the driver
spaces them evenly across the window and stamps every such event with
`ts_estimated: true` in its payload. Two consequences, both deliberate:

*   Ordering is faithful (tools precede the response), so any check that reads
    sequence is correct.
*   No timing figure in this repo may be derived from an event carrying
    `ts_estimated`. Latency comes from `caller_utterance -> agent_audio_first_byte`,
    both of which are real captured instants. A streaming adapter that observes
    each tool call as it happens should pass real timestamps via
    `ToolInvocation.ts`, and then the flag is absent.

Marking the estimate in the data, instead of in a comment, is the difference
between a documented approximation and a lie with a footnote.

WHAT THIS DOES NOT DO
---------------------
No barge-in. Interrupting the agent mid-utterance needs duplex audio, the v1
adapters are turn-based, and `interruption_started` / `interruption_acknowledged`
are reserved but unemitted (see `lab.trace.schema`). A turn-based driver cannot
measure interruption handling and this one does not claim to.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from lab.clock import Clock
from lab.simulator.persona import END_OF_CALL_RE, CallerProfile
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace

__all__ = [
    "ToolInvocation",
    "Handoff",
    "AgentTurn",
    "AgentReply",
    "AgentUnderTest",
    "Caller",
    "ScriptedCaller",
    "LLMCaller",
    "coerce_turn",
    "run_scenario",
    "DEFAULT_MAX_TURNS",
    "LIVE_CALLER_ENV_VAR",
    "STT_ENGINE",
    "TTS_ENGINE",
]

#: Hard stop on conversation length. A loop between a stubborn caller and a
#: stubborn agent is a real failure mode (and a real cloud bill), so the driver
#: refuses to run forever and records `reason="max_turns"` in `session_end`
#: rather than raising — a truncated conversation is evidence, not an error.
DEFAULT_MAX_TURNS: int = 12

#: Set this to a truthy value to allow `LLMCaller` to reach a live provider.
#: Absent, the caller replays from its cassette and raises on a miss. Opt-in, so
#: a clean clone cannot spend money by accident.
LIVE_CALLER_ENV_VAR: str = "LAB_LIVE_CALLER"

#: Engine tags for the pseudo-STT/TTS legs of a text run. Named so that a text
#: trace and a voice trace carry the same shape of attribution and one latency
#: function serves both.
STT_ENGINE: str = "harness:passthrough-stt"
TTS_ENGINE: str = "harness:passthrough-tts"

_ESTIMATED = {"ts_estimated": True}


# --------------------------------------------------------------------------- #
# What an agent hands back
# --------------------------------------------------------------------------- #


class ToolInvocation(BaseModel):
    """One tool call the agent made during a turn, and what came back.

    Call and result are one object here because a callable agent reports them
    together; the driver still emits them as the two separate trace events the
    schema defines, so a check can distinguish "never called" from "called and
    failed" exactly as it would on a streaming adapter.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    ok: bool = True
    error: str | None = None
    call_id: str | None = None
    ts: float | None = Field(
        default=None,
        description=(
            "Real observed timestamp, session-relative seconds, when the adapter "
            "saw the call happen. Leave None and the driver interpolates and "
            "flags the event as estimated."
        ),
    )


class Handoff(BaseModel):
    """Control passing from one sub-agent to another during a turn."""

    model_config = ConfigDict(extra="forbid")

    from_agent: str = Field(min_length=1)
    to_agent: str = Field(min_length=1)
    reason: str | None = None
    ts: float | None = None


class AgentTurn(BaseModel):
    """Everything the system under test did in response to one caller utterance."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="What the agent said. May be empty for a silent turn.")
    agent: str | None = Field(
        default=None, description="Which sub-agent spoke, for multi-agent systems."
    )
    tools: list[ToolInvocation] = Field(default_factory=list)
    handoff: Handoff | None = None
    end_call: bool = Field(
        default=False, description="True if the agent considers the conversation over."
    )


#: What an `AgentUnderTest` may return. `str` and `(str, tools)` are accepted so
#: that wrapping a third-party agent is a one-line lambda; `coerce_turn`
#: normalises. Convenience at the boundary, one type inside.
AgentReply = AgentTurn | str | tuple[str, Sequence[ToolInvocation | dict[str, Any]]]


@runtime_checkable
class AgentUnderTest(Protocol):
    """Anything that answers an utterance. The entire contract on the SUT.

    Deliberately narrow. A system under test should not have to know it is being
    evaluated, and anything wider (session objects, lifecycle hooks, a base
    class) would make `lab` a framework the SUT has to adopt instead of an
    instrument pointed at it.

    Statefulness is the implementer's business: a multi-turn agent keeps its own
    conversation state across calls. `lab.simulator.passk` therefore takes a
    *factory*, so each repeat of a scenario starts from a clean agent.
    """

    def __call__(self, utterance: str) -> AgentReply: ...


def coerce_turn(reply: AgentReply) -> AgentTurn:
    """Normalise whatever the agent returned into an `AgentTurn`.

    Called strictly *after* the measurement boundary closes — validation of the
    reply is harness compute and must not land inside the measured window.
    """
    if isinstance(reply, AgentTurn):
        return reply
    if isinstance(reply, str):
        return AgentTurn(text=reply)
    if isinstance(reply, tuple) and len(reply) == 2:
        text, tools = reply
        return AgentTurn(
            text=str(text),
            tools=[
                t if isinstance(t, ToolInvocation) else ToolInvocation.model_validate(t)
                for t in tools
            ],
        )
    raise TypeError(
        "an AgentUnderTest must return an AgentTurn, a str, or a (text, tools) "
        f"tuple; got {type(reply).__name__}"
    )


# --------------------------------------------------------------------------- #
# What a caller looks like
# --------------------------------------------------------------------------- #


@runtime_checkable
class Caller(Protocol):
    """A simulated caller: an opening line, then a reply to each agent turn.

    `reply` returning `None` means the caller hung up, which is how a scenario
    ends normally. Two implementations ship: `ScriptedCaller` (deterministic, used
    by every offline test) and `LLMCaller` (opt-in, records and replays).
    """

    def opening(self) -> str: ...

    def reply(self, agent_turn: AgentTurn) -> str | None: ...


class ScriptedCaller:
    """A deterministic caller: a fixed script, plus answers to direct questions.

    WHY A SCRIPT AT ALL
    -------------------
    A model-driven caller is the more realistic instrument and the worse test
    fixture: its variance shows up in the results as agent variance, and the
    pass^k machinery then reports the caller's flakiness as the agent's. So every
    offline test in this repo drives the agent with a script, and the LLM caller
    is reserved for exploration and for generating the fixtures the scripts are
    written against.

    HOW A TURN IS CHOSEN
    --------------------
    1.  If the agent's utterance asks for a fact the goal marks `on_request_only`,
        answer it — a real caller answers the question in front of them rather
        than reading from a list. A reluctant persona (`cooperativeness` below
        `RELUCTANT_BELOW`) stalls once before answering.
    2.  Otherwise say the next line of the script.
    3.  Script exhausted: `closing` if one was given and not yet used, else hang
        up by returning None.

    Rule 1 taking precedence is what makes an agent that re-asks a question
    observable rather than fatal: the caller answers again, the conversation
    continues, and the *trace* records that the same question was asked twice —
    which is where a check, not the caller, is entitled to have an opinion.

    Ask counts are kept per fact for the whole call, so the second ask of a
    reluctant persona can arrive many turns after the first.
    """

    def __init__(
        self,
        script: Sequence[str],
        *,
        profile: CallerProfile | None = None,
        closing: str | None = None,
        stall_line: str = "Sorry, what was that?",
    ) -> None:
        """
        Args:
            script: The caller's lines, in order. The first is the opening.
            profile: Persona and goal. Optional — a bare script is a legitimate
                minimal caller — but gated-fact answering needs it.
            closing: Said once after the script runs out; then the caller hangs
                up. Use it for a sign-off the agent's last turn should get.
            stall_line: What a reluctant persona says on the first ask.
        """
        if not script:
            raise ValueError("a ScriptedCaller needs at least one line to open with")
        self.script: list[str] = list(script)
        self.profile = profile
        self.closing = closing
        self.stall_line = stall_line
        self._index = 0
        self._closed = False
        self._ask_counts: dict[str, int] = {}
        self._released: list[str] = []

    # ---------------------------------------------------------------- reading

    @property
    def released_facts(self) -> list[str]:
        """Gated fact keys this caller has actually spoken, in release order.

        The ground truth for an information-loss check: a fact that was never
        released cannot have been dropped by the agent, and a check that ignores
        this distinction will blame the agent for the scenario's silence.
        """
        return list(self._released)

    @property
    def ask_counts(self) -> dict[str, int]:
        """How many times each gated fact was asked for across the call."""
        return dict(self._ask_counts)

    @property
    def lines_used(self) -> int:
        """How much of the script was consumed. Fewer than `len(script)` means the
        agent ended the call early — worth knowing when reading a short trace."""
        return self._index

    # ---------------------------------------------------------------- speaking

    def opening(self) -> str:
        self._index = 1
        return self.script[0]

    def reply(self, agent_turn: AgentTurn) -> str | None:
        answer = self._answer_to_question(agent_turn.text)
        if answer is not None:
            return answer
        if self._index < len(self.script):
            line = self.script[self._index]
            self._index += 1
            return line
        if self.closing is not None and not self._closed:
            self._closed = True
            return self.closing
        return None

    def _answer_to_question(self, agent_text: str) -> str | None:
        """Answer a direct question about a gated fact, or None if not asked."""
        if self.profile is None or not agent_text:
            return None
        goal = self.profile.goal
        asked = goal.asked_keys(agent_text, among=goal.gated_keys())
        if not asked:
            return None
        # One fact per turn, in declaration order: a caller who answers three
        # questions in one breath makes it impossible to attribute which ask
        # produced which release.
        key = asked[0]
        self._ask_counts[key] = self._ask_counts.get(key, 0) + 1
        if self._ask_counts[key] < self.profile.asks_required:
            return self.stall_line
        if key not in self._released:
            self._released.append(key)
        return goal.spoken(key)

    def __repr__(self) -> str:
        return (
            f"ScriptedCaller(lines={len(self.script)}, used={self._index}, "
            f"released={self._released})"
        )


class LLMCaller:
    """A model-driven caller that records to a cassette and replays from it.

    WHAT THIS DEMONSTRATES
    ----------------------
    The cardinal rule of this repo is that everything runs with zero API keys, and
    the honest way to keep that rule while still using a model is record/replay.
    First run (with `LAB_LIVE_CALLER` set) calls the provider and appends each
    generated turn to a JSON cassette. Every run afterwards reads the cassette and
    never touches the network, which is why a clean clone reproduces a
    model-generated conversation exactly.

    THE CASSETTE VERIFIES ITS CONTEXT
    ---------------------------------
    Each entry stores a sha256 of the conversation that preceded it, and replay
    checks it. Positional replay — turn 3 gets whatever was recorded third —
    would silently keep working after the agent's behaviour changed, and the
    caller would answer a question nobody asked while the suite stayed green.
    On a mismatch this raises, naming the fixture and the env var to re-record
    with. A fixture that cannot go stale loudly is not a fixture, it is a decoy.

    `litellm` is imported inside the request method, so importing this module
    costs nothing and offline test collection never touches a provider SDK.
    """

    def __init__(
        self,
        profile: CallerProfile,
        *,
        cassette: str | Path,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 120,
        live_env_var: str = LIVE_CALLER_ENV_VAR,
    ) -> None:
        self.profile = profile
        self.cassette_path = Path(cassette)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.live_env_var = live_env_var
        self._history: list[dict[str, str]] = []
        self._recorded: list[dict[str, Any]] = []
        self._cassette: dict[str, Any] = self._load_cassette()
        self._dirty = False

    # ---------------------------------------------------------------- cassette

    def _load_cassette(self) -> dict[str, Any]:
        if not self.cassette_path.exists():
            return {"model": self.model, "turns": []}
        loaded = json.loads(self.cassette_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or "turns" not in loaded:
            raise ValueError(
                f"{self.cassette_path}: not a caller cassette (expected a mapping "
                "with a 'turns' list)"
            )
        return loaded

    @property
    def live_enabled(self) -> bool:
        """True when the opt-in env var permits a real provider call."""
        return bool(os.environ.get(self.live_env_var))

    def _context_digest(self) -> str:
        """sha256 over the conversation so far — the replay key."""
        payload = json.dumps(
            {"system": self.profile.system_prompt(), "history": self._history},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _replay(self, index: int, digest: str) -> str | None:
        turns = self._cassette.get("turns", [])
        if index >= len(turns):
            return None
        entry = turns[index]
        recorded_digest = entry.get("context_sha256")
        if recorded_digest != digest:
            raise ValueError(
                f"{self.cassette_path}: cassette is stale at turn {index}. The "
                "conversation leading into this turn does not match what was "
                f"recorded (context sha256 {digest[:12]} vs {str(recorded_digest)[:12]}). "
                "The agent's behaviour changed, so replaying this turn would answer "
                f"a question that was never asked. Re-record with {self.live_env_var}=1."
            )
        return str(entry["utterance"])

    def save(self) -> Path | None:
        """Write the cassette if this run generated anything new.

        Returns the path written, or None when the run was a pure replay — so a
        test can assert that replaying recorded nothing.
        """
        if not self._dirty:
            return None
        self.cassette_path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "model": self.model,
            "persona": self.profile.persona.name,
            "goal_intent": self.profile.goal.intent,
            "turns": self._recorded,
        }
        self.cassette_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self.cassette_path

    # ---------------------------------------------------------------- speaking

    def _next_utterance(self, user_message: str | None) -> str | None:
        if user_message is not None:
            self._history.append({"role": "user", "content": user_message})
        index = sum(1 for m in self._history if m["role"] == "assistant")
        digest = self._context_digest()

        replayed = self._replay(index, digest)
        if replayed is None:
            if not self.live_enabled:
                raise RuntimeError(
                    f"LLMCaller has no recorded turn {index} in {self.cassette_path} "
                    f"and live calls are off. Set {self.live_env_var}=1 to record it, "
                    "or drive this scenario with a ScriptedCaller — every offline "
                    "test in this repo does."
                )
            replayed = self._complete()
            self._recorded.append(
                {"index": index, "context_sha256": digest, "utterance": replayed}
            )
            self._dirty = True
        else:
            self._recorded.append(
                {"index": index, "context_sha256": digest, "utterance": replayed}
            )

        self._history.append({"role": "assistant", "content": replayed})
        if END_OF_CALL_RE.search(replayed):
            return None
        return replayed

    def _complete(self) -> str:
        """One live completion. The only method here that touches the network."""
        from litellm import completion  # imported lazily, on purpose

        response = completion(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": self.profile.system_prompt()},
                *self._history,
            ],
        )
        text = response["choices"][0]["message"]["content"]
        return str(text).strip()

    def opening(self) -> str:
        first = self._next_utterance(
            "You have just been connected. Say your opening line."
        )
        if first is None:
            raise RuntimeError(
                "the LLM caller ended the call on its opening turn; check the "
                f"cassette at {self.cassette_path}"
            )
        return first

    def reply(self, agent_turn: AgentTurn) -> str | None:
        return self._next_utterance(agent_turn.text or "(the agent said nothing)")

    def __repr__(self) -> str:
        return (
            f"LLMCaller(model={self.model!r}, cassette={str(self.cassette_path)!r}, "
            f"live={self.live_enabled}, turns_recorded={len(self._recorded)})"
        )


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def _interpolated(t0: float, t1: float, count: int) -> list[float]:
    """`count` instants evenly spaced strictly inside (t0, t1).

    Strictly inside, so an estimated tool event can never collide with — or
    precede — the real captured boundary instants, and `Trace.is_ordered()` holds.
    A zero-width window (a `FakeClock` run where the agent did not sleep) yields
    t0 repeatedly, which is still non-decreasing and still correctly ordered.
    """
    span = t1 - t0
    return [t0 + span * ((i + 1) / (count + 1)) for i in range(count)]


class _WindowStamper:
    """Hands out in-window timestamps for the events of one turn.

    Two jobs, both about keeping the trace's ordering invariant true no matter
    what an adapter reports:

    *   An event with no observed timestamp gets the next evenly-spaced estimate
        and is flagged `ts_estimated`.
    *   An event *with* an observed timestamp keeps it, clamped into `[t0, t1]`
        and to the running maximum. A mix of observed and estimated instants
        could otherwise emit a late observed call before an early estimate, and
        `Trace.is_ordered()` would fail on a trace the harness produced itself.
        Clamping is silent because the clamp only ever moves an event to the edge
        of the window it is already known to belong to.
    """

    def __init__(self, t0: float, t1: float, slots: int) -> None:
        self._t0 = t0
        self._t1 = t1
        self._estimates = _interpolated(t0, t1, slots)
        self._cursor = 0
        self._last = t0

    def take(self, observed: float | None) -> tuple[float, dict[str, Any]]:
        """Return `(ts, payload_extra)` for one event."""
        if observed is None:
            ts = self._estimates[self._cursor]
            self._cursor += 1
            extra: dict[str, Any] = dict(_ESTIMATED)
        else:
            ts = min(max(observed, self._t0), self._t1)
            extra = {}
        ts = max(ts, self._last)
        self._last = ts
        return ts, extra


def run_scenario(
    *,
    scenario_id: str,
    agent: AgentUnderTest,
    caller: Caller,
    adapter: str = "text",
    clock: Clock | None = None,
    session_id: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    profile: CallerProfile | None = None,
    emit_transcripts: bool = True,
    emit_response_boundary: bool = True,
    stt_engine: str = STT_ENGINE,
    tts_engine: str = TTS_ENGINE,
) -> Trace:
    """Drive `caller` against `agent` for up to `max_turns` turns; return the trace.

    The trace is the only output. Everything a check, a judge, a latency figure or
    a report needs is in it, which is the invariant the whole repo rests on: if a
    result cannot be recomputed from the trace on disk, it cannot be audited.

    Args:
        scenario_id: Identifier written into the trace and every report row.
        agent: The system under test. Called exactly once per turn.
        caller: The simulated caller. `ScriptedCaller` for tests.
        adapter: Free-form tag for what drove the run, e.g. "text",
            "voice:replay". Recorded, never interpreted.
        clock: Time source; defaults to `TraceBuilder`'s `MonotonicClock`. Pass a
            `FakeClock` for exact, reproducible timestamps.
        session_id: Defaults to a fresh uuid4 hex.
        max_turns: Hard stop. Reaching it ends the session with
            `reason="max_turns"` rather than raising.
        profile: Caller profile for `session_start` attribution. Defaults to
            `caller.profile` when the caller has one.
        emit_transcripts: Emit `transcript_in` / `transcript_out` around each
            turn. True even for text runs: it keeps text and voice traces the
            same shape, so one analysis works on both, and it distinguishes what
            the agent *heard* from what the caller *said* — which is where a
            voice failure gets attributed to STT instead of to the model.
        emit_response_boundary: Emit `agent_audio_first_byte` at `t1`. This is the
            event `lab.voice.calibration.recover_response_latencies` pairs on. For
            a text adapter it marks the instant the first response byte reached
            the harness; the kind is shared so one latency definition covers both.
        stt_engine, tts_engine: Engine tags for the transcript legs.

    Returns:
        The `Trace`. Turn count and stop reason are in the `session_end` payload.
    """
    if max_turns < 1:
        raise ValueError(f"max_turns must be at least 1, got {max_turns!r}")

    effective_profile = profile if profile is not None else getattr(caller, "profile", None)
    builder = TraceBuilder(
        scenario_id=scenario_id,
        adapter=adapter,
        session_id=session_id,
        clock=clock,
    )
    metadata: dict[str, Any] = {"caller": type(caller).__name__, "max_turns": max_turns}
    if isinstance(effective_profile, CallerProfile):
        metadata.update(effective_profile.trace_metadata())
    builder.session_start(**metadata)

    utterance: str | None = caller.opening()
    turns = 0
    reason = "caller_hung_up"

    while utterance is not None:
        if turns >= max_turns:
            reason = "max_turns"
            break
        turns += 1

        if emit_transcripts:
            # What the agent heard. In a text run this is the caller's words
            # unchanged; a voice adapter puts real STT output here, and the gap
            # between the two is transcription error rather than model error.
            builder.transcript_in(utterance, confidence=1.0, engine=stt_engine)

        t0 = builder.now()  # ---- BOUNDARY OUT
        reply = agent(utterance)  # the system under test, and nothing else
        t1 = builder.now()  # ---- BOUNDARY IN
        # ---- the measured window is closed; everything below is harness compute

        agent_turn = coerce_turn(reply)
        builder.caller_utterance(utterance, ts=t0)

        inner: list[Handoff | ToolInvocation] = []
        if agent_turn.handoff is not None:
            inner.append(agent_turn.handoff)
        inner.extend(agent_turn.tools)
        # Two trace events per tool (call + result), one per handoff.
        slots = sum(2 if isinstance(item, ToolInvocation) else 1 for item in inner)
        stamper = _WindowStamper(t0, t1, slots)

        for item in inner:
            if isinstance(item, Handoff):
                ts, extra = stamper.take(item.ts)
                builder.agent_handoff(
                    item.from_agent, item.to_agent, reason=item.reason, ts=ts, **extra
                )
                continue
            call_ts, call_extra = stamper.take(item.ts)
            result_ts, result_extra = stamper.take(item.ts)
            call_event = builder.tool_call(
                item.name, item.args, call_id=item.call_id, ts=call_ts, **call_extra
            )
            builder.tool_result(
                item.name,
                item.result,
                call_id=call_event.get("call_id"),
                ok=item.ok,
                error=item.error,
                ts=result_ts,
                **result_extra,
            )

        if emit_response_boundary:
            builder.agent_audio_first_byte(turn=turns, ts=t1, engine=tts_engine)
        if emit_transcripts:
            builder.transcript_out(agent_turn.text, ts=t1, engine=tts_engine)
        # Not back-dated: this is the instant the harness finished with the turn,
        # which is what makes `transcript_in -> agent_utterance` the honest
        # whole-turn cost figure (and, under a FakeClock, identical to t1).
        builder.agent_utterance(agent_turn.text, agent=agent_turn.agent)

        if agent_turn.end_call:
            reason = "agent_ended"
            break

        utterance = caller.reply(agent_turn)

    builder.session_end(reason=reason, turns=turns)
    return builder.build()
