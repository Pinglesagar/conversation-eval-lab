"""The trace schema — the one artifact everything else in this repo consumes.

WHAT THIS DEMONSTRATES
----------------------
An evaluation harness lives or dies on its intermediate representation. If each
check re-derives its own view of "what happened in the conversation" from raw
provider output, the checks disagree with each other and none of them can be
audited. So this repo defines exactly one representation — a `Trace`: an ordered
list of typed events with monotonic timestamps — and holds a hard invariant:

    *Every timing figure and every behavioural verdict in this project must be
    derivable from trace events alone.*

That is what makes results reproducible and reviewable. A trace can be written
to disk (`lab.trace.io`), replayed offline, diffed between two runs, and handed
to a check that has never heard of the provider that produced it. Adapters are
the only code that knows about vendors; everything downstream sees events.

The event stream is deliberately dumb — `ts`, `kind`, `actor`, `payload`,
`engine` — because a schema that encodes interpretation ages badly. Judgement
lives in `lab/checks` and `lab/judges`, never here.

WHY `engine` IS A FIRST-CLASS FIELD
-----------------------------------
In a voice pipeline, "the agent was slow" is not one number. STT, the LLM, and
TTS are separate vendors with separate latency profiles, and an aggregate that
cannot be attributed to a component is not actionable. Tagging each event with
the engine that produced it lets a regression be pinned to a swap of one stage.

TIMESTAMPS
----------
`ts` is monotonic seconds elapsed since session start (see `lab.clock`), as a
float. Not wall-clock, not a datetime: durations are the quantity of interest,
and wall-clock time can step backwards mid-session. Events in a `Trace` are
expected to be non-decreasing in `ts`; `Trace.is_ordered()` checks it.

EVENT KINDS EMITTED IN v1
-------------------------
    session_start            a session begins; payload carries the identifiers
    caller_utterance         the simulated caller said something
    agent_utterance          the agent said something
    tool_call                the agent invoked a tool
    tool_result              a tool returned (or failed)
    agent_handoff            control passed from one sub-agent to another
    audio_emitted            an audio chunk crossed the boundary
    agent_audio_first_byte   first byte of the agent's audio response — the
                             timestamp that time-to-first-response is built on
    agent_audio_complete     the agent finished speaking
    transcript_in            STT output for inbound caller audio
    transcript_out           text handed to TTS for synthesis
    session_end              the session finished

DECLARED BUT NOT EMITTED IN v1 — PLANNED FOR v2
-----------------------------------------------
    interruption_started        caller began speaking over the agent (barge-in)
    interruption_acknowledged   agent actually stopped speaking in response

    These two are named here, and only here, on purpose. Barge-in handling is
    the single most common voice-agent failure mode, and the metric that matters
    is the gap between those two events: how long the agent keeps talking after
    the caller has started. Measuring it needs duplex audio streaming, which the
    v1 adapters do not implement. They are reserved rather than invented, so that
    a v2 adapter can emit them without a schema migration, and so that no check
    in this repo can silently pretend to measure barge-in today. Nothing in v1
    emits, consumes, or asserts on them.

CANONICAL PAYLOAD KEYS
----------------------
`payload` is an open dict — adapters add what they have — but checks can only be
written against keys they can rely on. `PAYLOAD_KEYS` below records the keys each
kind is expected to carry, and `TraceBuilder` (`lab.trace.build`) is the enforcing
implementation: it is the only sanctioned way to construct events, so payload
conventions live in one place instead of being copy-pasted across adapters.
"""

from __future__ import annotations

from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Actor",
    "EventKind",
    "PAYLOAD_KEYS",
    "TraceEvent",
    "Trace",
]

Actor = Literal["caller", "agent", "system"]


class EventKind:
    """String constants for event kinds.

    `TraceEvent.kind` is typed as a plain `str`, not an enum, so that a future
    adapter can emit a kind this version has never heard of without the trace
    failing validation — forward compatibility matters more here than closed
    membership. These constants exist so first-party code never spells a kind by
    hand, and `KNOWN` / `V2_RESERVED` let tooling report unrecognised kinds
    instead of crashing on them.
    """

    SESSION_START = "session_start"
    CALLER_UTTERANCE = "caller_utterance"
    AGENT_UTTERANCE = "agent_utterance"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_HANDOFF = "agent_handoff"
    AUDIO_EMITTED = "audio_emitted"
    AGENT_AUDIO_FIRST_BYTE = "agent_audio_first_byte"
    AGENT_AUDIO_COMPLETE = "agent_audio_complete"
    TRANSCRIPT_IN = "transcript_in"
    TRANSCRIPT_OUT = "transcript_out"
    SESSION_END = "session_end"

    # Declared for v2. Nothing in v1 emits or consumes these — see module docstring.
    INTERRUPTION_STARTED = "interruption_started"
    INTERRUPTION_ACKNOWLEDGED = "interruption_acknowledged"

    KNOWN: frozenset[str] = frozenset(
        {
            SESSION_START,
            CALLER_UTTERANCE,
            AGENT_UTTERANCE,
            TOOL_CALL,
            TOOL_RESULT,
            AGENT_HANDOFF,
            AUDIO_EMITTED,
            AGENT_AUDIO_FIRST_BYTE,
            AGENT_AUDIO_COMPLETE,
            TRANSCRIPT_IN,
            TRANSCRIPT_OUT,
            SESSION_END,
        }
    )

    V2_RESERVED: frozenset[str] = frozenset(
        {INTERRUPTION_STARTED, INTERRUPTION_ACKNOWLEDGED}
    )


#: Payload keys each event kind is expected to carry. Checks may rely on these;
#: anything else in a payload is adapter-specific extra detail. Keys in
#: parentheses are optional.
PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    EventKind.SESSION_START: ("session_id", "scenario_id", "adapter"),
    EventKind.CALLER_UTTERANCE: ("text",),
    EventKind.AGENT_UTTERANCE: ("text", "agent"),
    EventKind.TOOL_CALL: ("name", "args", "call_id"),
    EventKind.TOOL_RESULT: ("name", "call_id", "ok", "result"),
    EventKind.AGENT_HANDOFF: ("from", "to", "reason"),
    EventKind.AUDIO_EMITTED: ("num_bytes", "duration_s"),
    EventKind.AGENT_AUDIO_FIRST_BYTE: ("turn",),
    EventKind.AGENT_AUDIO_COMPLETE: ("turn", "num_bytes"),
    EventKind.TRANSCRIPT_IN: ("text", "confidence"),
    EventKind.TRANSCRIPT_OUT: ("text",),
    EventKind.SESSION_END: ("reason", "turns"),
}


class TraceEvent(BaseModel):
    """One thing that happened, at one instant, attributed to one actor."""

    model_config = ConfigDict(extra="forbid")

    ts: float = Field(
        ...,
        description="Monotonic seconds elapsed since session start.",
    )
    kind: str = Field(
        ...,
        min_length=1,
        description="Event kind; see EventKind for the first-party vocabulary.",
    )
    actor: Actor = Field(
        ...,
        description="Who the event is attributed to: caller, agent, or system.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific detail; see PAYLOAD_KEYS for the conventions.",
    )
    engine: str | None = Field(
        default=None,
        description=(
            "Which concrete engine produced this event, e.g. a TTS, STT or LLM "
            "identifier. None when no single engine owns the event."
        ),
    )

    @property
    def is_known_kind(self) -> bool:
        """True if `kind` is in this version's vocabulary (v1 or reserved v2)."""
        return self.kind in EventKind.KNOWN or self.kind in EventKind.V2_RESERVED

    def get(self, key: str, default: Any = None) -> Any:
        """Read a payload key. Shorthand for `event.payload.get(key, default)`."""
        return self.payload.get(key, default)

    def __repr__(self) -> str:
        return f"TraceEvent(ts={self.ts:.4f}, kind={self.kind!r}, actor={self.actor!r})"


class Trace(BaseModel):
    """An ordered event stream for one session, plus read helpers.

    The helpers are intentionally thin. They exist so that twenty checks written
    by different people all answer "which tools were called?" the same way, not
    to interpret the conversation.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    scenario_id: str
    adapter: str = Field(
        ...,
        description="Which adapter produced this trace, e.g. 'text' or 'voice:replay'.",
    )
    events: list[TraceEvent] = Field(default_factory=list)

    # ---------------------------------------------------------------- iteration

    def __iter__(self) -> Iterator[TraceEvent]:  # type: ignore[override]
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    # ------------------------------------------------------- the spec'd helpers

    def tool_names(self) -> list[str]:
        """Names of every tool the agent called, in call order, with repeats.

        Reads `tool_call` events only — what the agent *attempted*. Whether each
        call succeeded lives in the matching `tool_result`. Keeping them separate
        is what lets a check distinguish "never tried" from "tried and failed".
        """
        return [
            str(e.get("name"))
            for e in self.events
            if e.kind == EventKind.TOOL_CALL and e.get("name") is not None
        ]

    def utterances(self) -> list[TraceEvent]:
        """Caller and agent utterance events, in order — the conversation itself.

        Returns the events rather than bare strings so callers keep access to
        `ts`, `actor` and the speaking sub-agent.
        """
        return [
            e
            for e in self.events
            if e.kind in (EventKind.CALLER_UTTERANCE, EventKind.AGENT_UTTERANCE)
        ]

    def handoffs(self) -> list[TraceEvent]:
        """Every `agent_handoff` event, in order.

        Handoff boundaries are where multi-agent systems drop information, so
        this is the anchor for any check about context surviving a transfer.
        See `handoff_pairs()` for just the (from, to) names.
        """
        return [e for e in self.events if e.kind == EventKind.AGENT_HANDOFF]

    def duration(self) -> float:
        """Session length in seconds: last event `ts` minus first event `ts`.

        Zero for an empty or single-event trace. Because `ts` is already relative
        to session start, this is a subtraction and not a clock read — the figure
        is a property of the trace, identical on replay.
        """
        if len(self.events) < 2:
            return 0.0
        return self.events[-1].ts - self.events[0].ts

    def event_pairs(
        self, first_kind: str, second_kind: str
    ) -> list[tuple[TraceEvent, TraceEvent]]:
        """Pair each `first_kind` event with the next following `second_kind`.

        Pairs are non-overlapping and greedy from the left: once an event has
        been consumed as the right-hand side of a pair, it cannot close another.
        A `first_kind` event with no later `second_kind` is dropped rather than
        paired with something implausible.

        This is the primitive every latency measurement is built on. For example,
        time-to-first-response is::

            trace.event_pairs("caller_utterance", "agent_audio_first_byte")

        and each pair's `b.ts - a.ts` is one sample. Expressing latency as a
        pairing over a shared event stream — rather than as a stopwatch buried in
        an adapter — is what makes the figure auditable: the evidence for every
        number ships with the number.
        """
        pairs: list[tuple[TraceEvent, TraceEvent]] = []
        pending: TraceEvent | None = None
        for event in self.events:
            if pending is None:
                if event.kind == first_kind:
                    pending = event
                continue
            if event.kind == second_kind:
                pairs.append((pending, event))
                pending = None
            elif event.kind == first_kind:
                # A second opener before any closer: the earlier one never
                # completed. Advance to the later opener rather than pairing
                # across an unanswered turn.
                pending = event
        return pairs

    # ------------------------------------------------------- small conveniences

    def events_of_kind(self, *kinds: str) -> list[TraceEvent]:
        """Every event whose kind is one of `kinds`, in order."""
        wanted = set(kinds)
        return [e for e in self.events if e.kind in wanted]

    def first(self, kind: str) -> TraceEvent | None:
        """The earliest event of `kind`, or None."""
        return next((e for e in self.events if e.kind == kind), None)

    def last(self, kind: str) -> TraceEvent | None:
        """The latest event of `kind`, or None."""
        return next((e for e in reversed(self.events) if e.kind == kind), None)

    def handoff_pairs(self) -> list[tuple[str | None, str | None]]:
        """(from_agent, to_agent) for each handoff, read from the payload."""
        return [(e.get("from"), e.get("to")) for e in self.handoffs()]

    def texts(self, actor: Actor | None = None) -> list[str]:
        """Utterance text, optionally filtered to one actor."""
        return [
            str(e.get("text", ""))
            for e in self.utterances()
            if actor is None or e.actor == actor
        ]

    def is_ordered(self) -> bool:
        """True if timestamps are non-decreasing — the schema's core invariant.

        Out-of-order events mean a broken clock or a racing adapter, and any
        duration computed from them is meaningless. Verified in tests rather than
        enforced in the validator, so that a real-world trace can still be loaded
        and inspected after it has gone wrong.
        """
        return all(
            a.ts <= b.ts for a, b in zip(self.events, self.events[1:], strict=False)
        )

    def unknown_kinds(self) -> set[str]:
        """Kinds in this trace that this version does not recognise."""
        return {e.kind for e in self.events if not e.is_known_kind}

    def __repr__(self) -> str:
        return (
            f"Trace(session_id={self.session_id!r}, scenario_id={self.scenario_id!r}, "
            f"adapter={self.adapter!r}, events={len(self.events)}, "
            f"duration={self.duration():.3f}s)"
        )
