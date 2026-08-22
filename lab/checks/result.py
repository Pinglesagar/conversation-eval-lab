"""CheckResult — the structured verdict every contract returns.

WHAT THIS DEMONSTRATES
----------------------
A check that answers `True`/`False` is not an evaluation, it is a coin flip you
have chosen to trust. When a suite of two hundred checks runs against a nightly
build, the only results that get acted on are the ones that arrive with enough
context to triage without re-running anything. So every contract in this package
returns the same shape:

    name       which contract spoke
    passed     the verdict
    detail     one human sentence saying what was and was not found
    evidence   the actual trace events that justify the verdict, quoted

`evidence` is the load-bearing field. It is what turns "no-re-ask failed" into
"at t=18.4s BookingAgent said 'And how many people will that be?' after the
caller supplied party_size=6 at t=4.1s" — a claim a reviewer can check against
the trace file without believing anything the harness says.

TWO STATES THAT ARE NOT PASS OR FAIL
------------------------------------
**Vacuous passes** (`applicable=False`). A field-propagation contract on a trace
where no handoff ever happened has not been satisfied; it has been *skipped*, and
reporting it as a pass inflates the suite's green count with checks that asserted
nothing. This is how eval suites rot: a scenario changes, half the contracts stop
applying, the dashboard stays green. So inapplicability is a first-class state.
It does not fail a run, and it is counted and printed separately.

**Errors** (`error` set). A contract that raises is a bug in the harness, not a
verdict about the agent. The engine catches it, records it as a failure with the
exception text, and keeps running the rest of the suite — one broken contract must
not take down the report that would have told you about the other nineteen.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from lab.trace.schema import EventKind, TraceEvent

__all__ = ["Evidence", "CheckResult", "quote_event"]


def quote_event(event: TraceEvent) -> str:
    """Render one event as a short quotable string for evidence.

    Utterances quote their text; tool calls render as `name(json args)` with keys
    sorted so that two runs of the same scenario produce byte-identical evidence
    and a diff of two reports shows behaviour changes rather than dict ordering.
    """
    if event.kind in (
        EventKind.CALLER_UTTERANCE,
        EventKind.AGENT_UTTERANCE,
        EventKind.TRANSCRIPT_IN,
        EventKind.TRANSCRIPT_OUT,
    ):
        return str(event.get("text", ""))
    if event.kind == EventKind.TOOL_CALL:
        args = event.get("args", {})
        rendered = json.dumps(args, sort_keys=True, default=str)
        return f"{event.get('name')}({rendered})"
    if event.kind == EventKind.TOOL_RESULT:
        status = "ok" if event.get("ok", True) else f"error: {event.get('error')}"
        return f"{event.get('name')} -> {status}"
    if event.kind == EventKind.AGENT_HANDOFF:
        return f"{event.get('from')} -> {event.get('to')}"
    return json.dumps(event.payload, sort_keys=True, default=str)


class Evidence(BaseModel):
    """One quoted trace event (or fragment of one) supporting a verdict."""

    model_config = ConfigDict(extra="forbid")

    ts: float | None = Field(
        default=None, description="Timestamp of the event this quotes, if it has one."
    )
    kind: str = Field(description="Event kind, or a pseudo-kind such as 'absence'.")
    actor: str | None = Field(default=None, description="Who the quoted event belongs to.")
    quote: str = Field(description="The offending text, tool call, or fact.")
    note: str | None = Field(
        default=None, description="Why this event is evidence — the harness's reading of it."
    )

    @classmethod
    def from_event(
        cls, event: TraceEvent, *, quote: str | None = None, note: str | None = None
    ) -> Evidence:
        """Build evidence from a real event.

        `quote` overrides the rendered event text, which is how a sentence-level
        check quotes the one offending sentence out of a five-sentence turn
        instead of making a reviewer hunt for it.
        """
        return cls(
            ts=event.ts,
            kind=event.kind,
            actor=event.actor,
            quote=quote if quote is not None else quote_event(event),
            note=note,
        )

    @classmethod
    def absence(cls, what: str, *, note: str | None = None) -> Evidence:
        """Evidence that something is *missing*.

        Absence is the most common finding in this package — the agent said it
        booked the table and no `create_booking` exists — and it has no event to
        point at, so it gets an explicit pseudo-kind rather than being smuggled
        in as a note on an unrelated event.
        """
        return cls(kind="absence", quote=what, note=note)

    def render(self) -> str:
        stamp = "  --  " if self.ts is None else f"t={self.ts:7.3f}s "
        who = f"[{self.actor or self.kind}] "
        tail = f"   <- {self.note}" if self.note else ""
        return f"{stamp}{who}{self.quote}{tail}"


class CheckResult(BaseModel):
    """The verdict of one contract against one trace."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Contract name, stable across runs; used as the report key.")
    passed: bool
    detail: str = Field(description="One sentence: what was checked and what was found.")
    evidence: list[Evidence] = Field(default_factory=list)
    applicable: bool = Field(
        default=True,
        description=(
            "False when the contract had nothing to assert on this trace (a vacuous "
            "pass). Counted separately so a suite cannot go green by going silent."
        ),
    )
    error: str | None = Field(
        default=None,
        description="Set when the contract itself raised; the failure is in the harness.",
    )
    contract: str | None = Field(
        default=None, description="Class name of the contract that produced this."
    )

    @property
    def status(self) -> str:
        """Short status token for reports: PASS, FAIL, VACUOUS or ERROR."""
        if self.error is not None:
            return "ERROR"
        if not self.passed:
            return "FAIL"
        return "PASS" if self.applicable else "VACUOUS"

    def __bool__(self) -> bool:
        """Truthiness is the verdict, so `assert contract.check(trace)` reads naturally."""
        return self.passed

    def render(self, *, indent: str = "") -> str:
        """Multi-line human rendering: verdict line then quoted evidence."""
        lines = [f"{indent}{self.status:<8} {self.name}: {self.detail}"]
        if self.error:
            lines.append(f"{indent}    raised: {self.error}")
        for item in self.evidence:
            lines.append(f"{indent}    {item.render()}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"CheckResult(name={self.name!r}, status={self.status!r})"
