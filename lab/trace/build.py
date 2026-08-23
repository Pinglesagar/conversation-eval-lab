"""TraceBuilder — the only sanctioned way to construct trace events.

WHAT THIS DEMONSTRATES
----------------------
Two ideas, both of which exist to keep measurement honest.

**1. The clock is injected.**
`TraceBuilder` never reads `time.*`. It is handed a `Clock` (`lab.clock`), so the
same adapter code produces real timings in a live run and exact, reproducible
timings under a `FakeClock` in tests. A harness that cannot be run on a fake
clock cannot be regression-tested on its own numbers, which means its numbers are
asserted rather than verified.

**2. Timestamps are captured separately from events.**
Every method takes an optional `ts=`. That parameter is not a convenience — it is
the mechanism that keeps the harness's own compute out of the figures it reports.
The pattern for anything being measured is:

    t0 = clock.now()                      # boundary: request leaves the harness
    reply = agent.respond(text)           # the system under test, and nothing else
    t1 = clock.now()                      # boundary: first response re-enters
    # ---- everything below is harness compute and must not be inside the window
    builder.caller_utterance(text, ts=t0)
    builder.agent_audio_first_byte(ts=t1)

Constructing a pydantic model, serialising a payload and appending to a list all
cost real microseconds-to-milliseconds. If the event were built *at* the boundary
instead of timestamped at it, that cost would land inside the measured interval
and the harness would be reporting itself plus the agent. Capturing two floats
and building the events afterwards makes the measured window contain the agent
and nothing else. `lab.voice.calibration` proves this empirically rather than
asserting it: it deliberately injects harness overhead and shows the recovered
figure is unmoved by it.

Every method returns the `TraceEvent` it appended, so a caller can keep a
reference (for correlating a `tool_call` with its `tool_result`, say) without
re-scanning the trace.

PAYLOAD CONVENTIONS
-------------------
One method per event kind, with the canonical payload keys as named parameters.
This is deliberate: it is the difference between a schema that is documented and
a schema that is enforced. An adapter cannot emit a `tool_call` without a name,
and cannot invent `tool` where the rest of the repo reads `name`, because the
only route to an event is a typed method signature. Adapter-specific extras go
through `**extra`.
"""

from __future__ import annotations

import uuid
from typing import Any

from lab.clock import Clock, MonotonicClock
from lab.trace.schema import Actor, EventKind, Trace, TraceEvent

__all__ = ["TraceBuilder"]


class TraceBuilder:
    """Accumulates `TraceEvent`s for one session and hands back a `Trace`."""

    def __init__(
        self,
        *,
        scenario_id: str,
        adapter: str,
        session_id: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        """
        Args:
            scenario_id: Which scenario this session is running.
            adapter: Which adapter is driving it, e.g. "text" or "voice:replay".
            session_id: Defaults to a fresh uuid4 hex string.
            clock: Time source. Defaults to a `MonotonicClock` zeroed now, so
                the first event's `ts` is ~0.0. Pass a `FakeClock` in tests.
        """
        self.scenario_id = scenario_id
        self.adapter = adapter
        self.session_id = session_id or uuid.uuid4().hex
        self.clock: Clock = clock if clock is not None else MonotonicClock()
        self._events: list[TraceEvent] = []

    # ------------------------------------------------------------------ core

    @property
    def events(self) -> list[TraceEvent]:
        """The events appended so far (a copy; append via the emit methods)."""
        return list(self._events)

    def now(self) -> float:
        """Read the injected clock. Use this to capture boundary timestamps."""
        return self.clock.now()

    def emit(
        self,
        kind: str,
        actor: Actor,
        *,
        ts: float | None = None,
        engine: str | None = None,
        **payload: Any,
    ) -> TraceEvent:
        """Append an event of any kind.

        The escape hatch for kinds this version has no named method for — an
        adapter emitting a v2 event, or a vendor-specific signal. Prefer the
        named methods: they document the payload contract.

        `ts=None` reads the clock now; pass a captured float to timestamp an
        event at a boundary that has already gone by.
        """
        event = TraceEvent(
            ts=self.clock.now() if ts is None else ts,
            kind=kind,
            actor=actor,
            payload={k: v for k, v in payload.items() if v is not None},
            engine=engine,
        )
        self._events.append(event)
        return event

    def build(self) -> Trace:
        """Snapshot the accumulated events as a `Trace`.

        The builder stays usable afterwards; the returned trace holds its own
        copy of the event list, so a later `emit` does not mutate it.
        """
        return Trace(
            session_id=self.session_id,
            scenario_id=self.scenario_id,
            adapter=self.adapter,
            events=list(self._events),
        )

    # ------------------------------------------------- one method per v1 kind

    def session_start(
        self, *, ts: float | None = None, engine: str | None = None, **extra: Any
    ) -> TraceEvent:
        """Open the session. Carries the identifiers that make the file self-describing."""
        return self.emit(
            EventKind.SESSION_START,
            "system",
            ts=ts,
            engine=engine,
            session_id=self.session_id,
            scenario_id=self.scenario_id,
            adapter=self.adapter,
            **extra,
        )

    def caller_utterance(
        self,
        text: str,
        *,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """The simulated caller said something.

        In a voice run this marks the point the caller's turn ended and the
        request left the harness — the left edge of a response-latency window.
        """
        return self.emit(
            EventKind.CALLER_UTTERANCE, "caller", ts=ts, engine=engine, text=text, **extra
        )

    def agent_utterance(
        self,
        text: str,
        *,
        agent: str | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """The agent said something. `agent` names the sub-agent speaking."""
        return self.emit(
            EventKind.AGENT_UTTERANCE,
            "agent",
            ts=ts,
            engine=engine,
            text=text,
            agent=agent,
            **extra,
        )

    def tool_call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        call_id: str | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """The agent invoked a tool.

        `call_id` correlates this call with its `tool_result`; one is generated
        if not supplied. Correlating rather than assuming adjacency is what makes
        parallel and interleaved tool calls analysable.
        """
        return self.emit(
            EventKind.TOOL_CALL,
            "agent",
            ts=ts,
            engine=engine,
            name=name,
            args=args if args is not None else {},
            call_id=call_id or uuid.uuid4().hex[:12],
            **extra,
        )

    def tool_result(
        self,
        name: str,
        result: Any = None,
        *,
        call_id: str | None = None,
        ok: bool = True,
        error: str | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """A tool returned. `ok=False` plus `error` records a failure."""
        return self.emit(
            EventKind.TOOL_RESULT,
            "system",
            ts=ts,
            engine=engine,
            name=name,
            call_id=call_id,
            ok=ok,
            result=result,
            error=error,
            **extra,
        )

    def agent_handoff(
        self,
        from_agent: str,
        to_agent: str,
        *,
        reason: str | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """Control passed from one sub-agent to another.

        Written to the payload as `from` / `to` — reserved words in Python, so
        they cannot be method parameters, which is exactly why this wrapper
        exists rather than callers reaching for `emit`.
        """
        payload: dict[str, Any] = {"from": from_agent, "to": to_agent, "reason": reason}
        payload.update(extra)
        return self.emit(EventKind.AGENT_HANDOFF, "system", ts=ts, engine=engine, **payload)

    def audio_emitted(
        self,
        *,
        num_bytes: int | None = None,
        duration_s: float | None = None,
        actor: Actor = "agent",
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """An audio chunk crossed the harness boundary in either direction."""
        return self.emit(
            EventKind.AUDIO_EMITTED,
            actor,
            ts=ts,
            engine=engine,
            num_bytes=num_bytes,
            duration_s=duration_s,
            **extra,
        )

    def agent_audio_first_byte(
        self,
        *,
        turn: int | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """First byte of the agent's audio response arrived at the harness.

        The right edge of the response-latency window, and the single most
        important timestamp in a voice evaluation. Time to *completed* utterance
        is a property of how long the answer is, not of how responsive the system
        is.

        It is **not** when the caller starts hearing an answer, and an earlier
        version of this docstring said it was. It is when the first byte exists at
        the harness boundary — agent-side. Over a real transport the listener is
        still waiting at this instant: measured over WebRTC on this repo's own
        transport tier, by a mean of 87 ms (`docs/AUDIO_TRANSPORT.md`). For an
        in-process adapter the two are the same instant and the distinction costs
        nothing; for a live product it is the difference between a coaching
        prompt that lands and one that arrives after the moment. `audio_delivered`
        is the receiver-side event, and the pair of them is the gap.
        """
        return self.emit(
            EventKind.AGENT_AUDIO_FIRST_BYTE, "agent", ts=ts, engine=engine, turn=turn, **extra
        )

    def agent_audio_complete(
        self,
        *,
        turn: int | None = None,
        num_bytes: int | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """The agent finished speaking this turn."""
        return self.emit(
            EventKind.AGENT_AUDIO_COMPLETE,
            "agent",
            ts=ts,
            engine=engine,
            turn=turn,
            num_bytes=num_bytes,
            **extra,
        )

    def transcript_in(
        self,
        text: str,
        *,
        confidence: float | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """STT output for inbound caller audio — what the agent *heard*.

        Distinct from `caller_utterance`, which is what the caller *said*. The
        gap between the two pairs is transcription error, and keeping both in the
        trace is what lets a failure be attributed to STT rather than to the LLM.
        """
        return self.emit(
            EventKind.TRANSCRIPT_IN,
            "caller",
            ts=ts,
            engine=engine,
            text=text,
            confidence=confidence,
            **extra,
        )

    def transcript_out(
        self,
        text: str,
        *,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """Text handed to TTS for synthesis — what the agent meant to say."""
        return self.emit(
            EventKind.TRANSCRIPT_OUT, "agent", ts=ts, engine=engine, text=text, **extra
        )

    def audio_delivered(
        self,
        *,
        turn: int | None = None,
        participant: str | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """The agent's audio ARRIVED at the far participant, receiver-side.

        The counterpart to `agent_audio_first_byte`, and only meaningful when
        something real sits between the two: a transport, not a function call.
        Pairing the two kinds gives the delivery gap — the interval a framework's
        agent-side `e2e_latency` cannot see, because it stops its stopwatch at
        the moment this event has not happened yet.

        Attributed to `system` rather than to `agent`: the agent had finished
        generating well before this instant, and putting an agent action here
        would misattribute a transport fact to a speaker. `participant` names
        *whose* receiving end observed it, so a multi-listener session can say
        which listener waited.

        Emit it only from an adapter that genuinely measured arrival at a
        receiver. An in-process adapter must leave it out: a trace honestly
        missing this event is a trace that cannot be misread, whereas one that
        re-emits the agent-side instant under this kind reports a delivery gap of
        zero and looks like good news.
        """
        return self.emit(
            EventKind.AUDIO_DELIVERED,
            "system",
            ts=ts,
            engine=engine,
            turn=turn,
            participant=participant,
            **extra,
        )

    def transport_connected(
        self,
        *,
        participant: str | None = None,
        attempt: int = 1,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """A participant joined the session's transport.

        `attempt` is 1 for the initial join and increments on each reconnect, so
        a recovery is a second connect rather than a separate event kind, and
        "did it come back?" is answered by counting rather than by parsing a
        reason string.
        """
        return self.emit(
            EventKind.TRANSPORT_CONNECTED,
            "system",
            ts=ts,
            engine=engine,
            participant=participant,
            attempt=attempt,
            **extra,
        )

    def transport_disconnected(
        self,
        *,
        participant: str | None = None,
        reason: str = "unknown",
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """A participant left the transport, gracefully or otherwise.

        `reason` defaults to `"unknown"` rather than to `"closed"`, because a
        transport that vanished and a transport that was closed on purpose lead
        to opposite conclusions about the agent, and defaulting to the benign one
        would make every unexplained drop look intentional.
        """
        return self.emit(
            EventKind.TRANSPORT_DISCONNECTED,
            "system",
            ts=ts,
            engine=engine,
            participant=participant,
            reason=reason,
            **extra,
        )

    def session_end(
        self,
        *,
        reason: str = "completed",
        turns: int | None = None,
        ts: float | None = None,
        engine: str | None = None,
        **extra: Any,
    ) -> TraceEvent:
        """Close the session. `reason` records *how* it ended, not just that it did."""
        return self.emit(
            EventKind.SESSION_END,
            "system",
            ts=ts,
            engine=engine,
            reason=reason,
            turns=turns,
            **extra,
        )

    def __repr__(self) -> str:
        return (
            f"TraceBuilder(session_id={self.session_id!r}, "
            f"scenario_id={self.scenario_id!r}, adapter={self.adapter!r}, "
            f"events={len(self._events)})"
        )
