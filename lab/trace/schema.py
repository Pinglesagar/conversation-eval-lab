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
    audio_delivered          the agent's audio ARRIVED at the far participant,
                             observed at the receiving end of a real transport
    transcript_in            STT output for inbound caller audio
    transcript_out           text handed to TTS for synthesis
    transport_connected      a participant joined the session's transport
    transport_disconnected   a participant left it, gracefully or otherwise
    session_end              the session finished

WHY `audio_delivered` IS A SEPARATE KIND FROM `agent_audio_first_byte`
---------------------------------------------------------------------
Because they are two different instants, and the difference between them is a
product risk rather than a rounding error.

`agent_audio_first_byte` is agent-side: it fires when the response exists at the
harness boundary. Every in-process adapter in this repo emits it, and for those
adapters it is the only instant there is — the "network" is a function call. It
is also the instant a voice framework's own `e2e_latency` is built on, which is
why a dashboard can report a healthy number while a caller waits.

`audio_delivered` is receiver-side: it fires when that audio arrives at the other
participant, measured by a harness sitting where the listener sits. Pairing the
two gives the delivery gap::

    trace.event_pairs("agent_audio_first_byte", "audio_delivered")

Measured over real WebRTC on this repo's own transport tier, that gap is tens of
milliseconds and stable — see `docs/AUDIO_TRANSPORT.md`. It is not visible to
any in-process adapter, by construction. Keeping it as its own kind means a
report can never quietly present one as the other, and a trace from an
in-process adapter is *honestly* missing the receiver-side event rather than
silently reusing the agent-side one.

`transport_connected` / `transport_disconnected` exist for the same reason one
level down: a turn can be lost because a participant dropped, and a trace that
cannot say "the transport went away here" has to blame the agent for it. Both
carry `participant`, and `transport_connected` carries `attempt` so a reconnect
is a second connect rather than a third event kind.

All three are attributed to the `system` actor, deliberately. Nobody *said*
them: the agent had already finished generating when its audio arrived, so
attributing the arrival to the agent would put an agent action at an instant the
agent was not acting. The transport is the thing that acted.

CONSTRUCTED, NOT DISCOVERED — RESERVED UNTIL A v2 ADAPTER DISCOVERS THEM
------------------------------------------------------------------------
    interruption_started        caller began speaking over the agent (barge-in)
    interruption_acknowledged   agent actually stopped speaking in response

    These two are named here, and only here, on purpose. Barge-in handling is
    the single most common voice-agent failure mode, and the metric that matters
    is the gap between those two events: how long the agent keeps talking after
    the caller has started.

    The one true sentence about them, repeated wherever they are documented:
    **barge-in in this repository is constructed, not discovered** —
    `lab.voice.interaction.emit_barge_in` writes both kinds and
    `barge_in_report` reads them back, both under test, but their timings are
    handed in by a scenario rather than observed by an adapter; nothing outside
    the tests calls the emitter, so no committed trace contains either kind; and
    discovering a real overlap needs the duplex streaming path the v1 adapters
    do not implement.

    That is why they stay out of `KNOWN` rather than joining it: reserved means
    "no adapter discovers this yet", not "no code touches this". The audio row
    `audio-barge-in-not-discovered` derives its blocked status from
    `V2_RESERVED` rather than from a hand-kept list, so the day an adapter does
    discover an interruption and these kinds are promoted, the row becomes
    runnable on its own.

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
    AUDIO_DELIVERED = "audio_delivered"
    TRANSCRIPT_IN = "transcript_in"
    TRANSCRIPT_OUT = "transcript_out"
    TRANSPORT_CONNECTED = "transport_connected"
    TRANSPORT_DISCONNECTED = "transport_disconnected"
    SESSION_END = "session_end"

    # Reserved until an adapter *discovers* an interruption. `emit_barge_in` and
    # `barge_in_report` already write and read them from constructed timings — see
    # the module docstring for the one true sentence.
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
            AUDIO_DELIVERED,
            TRANSCRIPT_IN,
            TRANSCRIPT_OUT,
            TRANSPORT_CONNECTED,
            TRANSPORT_DISCONNECTED,
            SESSION_END,
        }
    )

    V2_RESERVED: frozenset[str] = frozenset(
        {INTERRUPTION_STARTED, INTERRUPTION_ACKNOWLEDGED}
    )


#: Payload keys each event kind is expected to carry. Checks may rely on these;
#: anything else in a payload is adapter-specific extra detail. Keys in
#: parentheses are optional.
#:
#: The two `V2_RESERVED` kinds are documented here too, because they are emitted
#: today — from constructed timings — and `barge_in_report` pairs them on `turn`.
#: A kind with a live emitter and a live reader whose payload contract is absent
#: from the contract table would be exactly the gap this table exists to close.
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
    EventKind.AUDIO_DELIVERED: ("turn", "participant"),
    EventKind.TRANSCRIPT_IN: ("text", "confidence"),
    EventKind.TRANSCRIPT_OUT: ("text",),
    EventKind.TRANSPORT_CONNECTED: ("participant", "attempt"),
    EventKind.TRANSPORT_DISCONNECTED: ("participant", "reason"),
    EventKind.SESSION_END: ("reason", "turns"),
    EventKind.INTERRUPTION_STARTED: ("turn", "agent_started_s", "agent_would_end_s"),
    EventKind.INTERRUPTION_ACKNOWLEDGED: ("turn", "overlap_s", "yielded"),
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
