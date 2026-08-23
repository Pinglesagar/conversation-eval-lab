"""The five tools, and the toolbox that records every call.

WHAT THIS DEMONSTRATES
----------------------
The tool surface is the half of the conversation a transcript cannot show you, and
the seeded bugs in this package are all about the gap between the two channels. So
the tools are written to make that gap *legible*:

*   **Structured results, not booleans.** `search_tables` returns which tables it
    found, how big they are and what else was free at other times. A check that
    wants to ask "did the agent offer an alternative it had actually been given?"
    can only do that if the result is in the trace.
*   **Failures are results too.** A tool that cannot do the thing raises
    `ToolError`, which the toolbox records as a call with `ok=False` and a
    message. That distinction — called-and-failed versus never-called — is one a
    contract is entitled to make, and it evaporates if a failing tool simply
    returns nothing.
*   **Allow-lists are enforced, not documented.** Each sub-agent declares the
    tools it may use and `Toolbox.invoke` raises `ToolNotAllowed` on anything
    else. A permission model that is only a comment is not a permission model,
    and "the policy agent quietly created a booking" is exactly the kind of
    multi-agent failure this repo is about.

Every call is appended to `Toolbox.calls` in order, with a deterministic
`call_id`, so the runtime can hand the harness a faithful ledger for the turn
without the tools knowing that a harness exists.

WHAT THIS DOES NOT DO
---------------------
No retries, no timeouts, no partial failures, and no latency: tool timing is
simulated by the runtime's injected clock, not here. Tools that fail
intermittently are a real and interesting failure class, and modelling them here
would make the seeded bugs non-deterministic, which is the one thing they may not
be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from tablemate.store import (
    Booking,
    Restaurant,
    Table,
    UnknownBooking,
    canonical,
)

__all__ = [
    "TOOL_NAMES",
    "ToolError",
    "ToolNotAllowed",
    "ToolCall",
    "Toolbox",
    "search_tables",
    "create_booking",
    "modify_booking",
    "cancel_booking",
    "check_policy",
    "MODIFIABLE_FIELDS",
]

#: The tool vocabulary, exactly as scenarios and contracts name it. One source of
#: truth: a scenario that expects `create_booking` and an agent that calls
#: `make_booking` is a test that can never pass and never says why.
TOOL_NAMES: tuple[str, ...] = (
    "search_tables",
    "create_booking",
    "modify_booking",
    "cancel_booking",
    "check_policy",
)

#: Fields `modify_booking` will change. Anything else in a `changes` payload is a
#: caller error, reported rather than silently ignored — a modification that
#: claims success while dropping half the request is the shape of bug this whole
#: repository exists to surface.
MODIFIABLE_FIELDS: tuple[str, ...] = ("date", "time", "party_size", "name", "notes")


class ToolError(RuntimeError):
    """A tool could not do what was asked. Recorded as a failed call, not a crash."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolNotAllowed(ToolError):
    """A sub-agent reached for a tool outside its allow-list.

    Deliberately *not* recorded as an ordinary failed call: this is a defect in
    the agent's wiring rather than a thing the world refused to do, and it should
    stop the run loudly instead of becoming one more `ok=False` row that nobody
    reads.
    """

    def __init__(self, agent: str, tool: str, allowed: Sequence[str]) -> None:
        super().__init__(
            "tool_not_allowed",
            f"{agent} may not call {tool!r}; its allow-list is "
            f"{', '.join(allowed) if allowed else '(no tools)'}",
        )
        self.agent = agent
        self.tool = tool
        self.allowed = tuple(allowed)


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #


def search_tables(store: Restaurant, *, date: str, time: str, party_size: Any) -> dict[str, Any]:
    """Is there a table for this party, at this date and time?

    Returns availability plus the tables that would fit and the nearest other
    slots that would work, so the agent has something concrete to offer when the
    answer is no.
    """
    size = _as_party_size(party_size)
    free = store.free_tables(date, time, size)
    result: dict[str, Any] = {
        "date": canonical(date),
        "time": canonical(time),
        "party_size": size,
        "available": bool(free),
        "tables": [t.as_dict() for t in free],
        "max_party_size": store.max_party_size,
    }
    if not free:
        result["alternatives"] = store.alternative_times(date, time, size)
        result["reason"] = (
            "party_too_large"
            if size > store.max_party_size
            else "slot_full"
        )
    return result


def create_booking(
    store: Restaurant,
    *,
    name: str,
    date: str,
    time: str,
    party_size: Any,
    notes: str = "",
) -> dict[str, Any]:
    """Commit a new booking and return it, reference included."""
    size = _as_party_size(party_size)
    if not str(name).strip():
        raise ToolError("missing_name", "a booking needs a name")
    free = store.free_tables(date, time, size)
    if not free:
        raise ToolError(
            "no_availability",
            f"nothing free for {size} at {canonical(time)} on {canonical(date)}",
        )
    booking = store.add_booking(
        name=str(name).strip(),
        date=date,
        time=time,
        party_size=size,
        notes=notes or "",
        table=free[0],
    )
    return {**booking.as_dict(), **_table_fields(store.table(booking.table_id))}


def modify_booking(
    store: Restaurant, *, booking_ref: str, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply `changes` to an existing booking; report exactly what moved."""
    booking = _lookup(store, booking_ref)
    if not booking.is_active:
        raise ToolError(
            "booking_cancelled", f"{booking.ref} was cancelled and cannot be amended"
        )
    if not isinstance(changes, Mapping) or not changes:
        raise ToolError("empty_changes", f"no changes given for {booking.ref}")
    unknown = [k for k in changes if k not in MODIFIABLE_FIELDS]
    if unknown:
        raise ToolError(
            "unknown_field",
            f"cannot change {', '.join(sorted(unknown))} on a booking; "
            f"modifiable fields are {', '.join(MODIFIABLE_FIELDS)}",
        )

    wanted = {**booking.as_dict(), **{k: v for k, v in changes.items()}}
    new_size = _as_party_size(wanted["party_size"])
    new_date, new_time = wanted["date"], wanted["time"]
    slot_moved = (canonical(new_date), canonical(new_time)) != (
        canonical(booking.date),
        canonical(booking.time),
    )
    table = store.table(booking.table_id)
    if slot_moved or new_size > (table.seats if table else 0):
        # The current table cannot carry the amendment: find one that can, with
        # this booking's own hold released so it does not block itself.
        held = booking.table_id
        booking.status = "released-for-amendment"
        try:
            free = store.free_tables(new_date, new_time, new_size)
        finally:
            booking.status = "confirmed"
        if not free:
            raise ToolError(
                "no_availability",
                f"nothing free for {new_size} at {canonical(new_time)} on {canonical(new_date)}",
            )
        chosen = next((t for t in free if t.id == held), free[0])
    else:
        chosen = table  # type: ignore[assignment]

    applied: dict[str, dict[str, Any]] = {}
    for key in MODIFIABLE_FIELDS:
        if key not in changes:
            continue
        before = getattr(booking, key)
        after = _as_party_size(changes[key]) if key == "party_size" else changes[key]
        if key in ("date", "time"):
            after = canonical(after)
        if before != after:
            applied[key] = {"from": before, "to": after}
            setattr(booking, key, after)
    if chosen is not None and chosen.id != booking.table_id:
        applied["table_id"] = {"from": booking.table_id, "to": chosen.id}
        booking.table_id = chosen.id

    return {
        **booking.as_dict(),
        "changed": applied,
        "unchanged": not applied,
        **_table_fields(store.table(booking.table_id)),
    }


def cancel_booking(store: Restaurant, *, booking_ref: str, reason: str = "") -> dict[str, Any]:
    """Cancel a booking. Cancelling twice is an error, not a no-op."""
    booking = _lookup(store, booking_ref)
    if not booking.is_active:
        raise ToolError(
            "already_cancelled", f"{booking.ref} was already cancelled"
        )
    booking.status = "cancelled"
    booking.cancel_reason = str(reason or "")
    return {**booking.as_dict(), "reason": booking.cancel_reason}


def check_policy(store: Restaurant, *, topic: str) -> dict[str, Any]:
    """Look a topic up on the policy sheet.

    A miss returns `found: False` and the list of topics that *do* exist, rather
    than raising: "we do not publish a policy on that" is an answer the agent can
    give a caller, and turning it into an error would push the agent towards
    inventing one.
    """
    answer = store.policy(topic)
    return {
        "topic": canonical(topic),
        "found": answer is not None,
        "answer": answer or "",
        "topics": store.policy_topics(),
    }


# --------------------------------------------------------------------------- #
# The recording toolbox
# --------------------------------------------------------------------------- #


@dataclass
class ToolCall:
    """One invocation and its outcome, as the runtime will report it.

    Mirrors `lab.simulator.ToolInvocation` field for field without importing it:
    the tool layer of a system under test has no business knowing what is
    watching it. `tablemate.runtime` does the one-line translation at the edge.
    """

    name: str
    args: dict[str, Any]
    result: Any = None
    ok: bool = True
    error: str | None = None
    call_id: str | None = None


@dataclass
class Toolbox:
    """Dispatches the five tools against one restaurant and records every call.

    The ledger (`calls`) is append-only and ordered, and `take()` drains the
    calls made since it was last drained — which is how the runtime reports "the
    tools used on this turn" without the agents having to track turn boundaries.
    """

    store: Restaurant
    calls: list[ToolCall] = field(default_factory=list)
    _drained: int = 0

    def invoke(
        self, name: str, args: Mapping[str, Any], *, agent: str, allowed: Sequence[str]
    ) -> ToolCall:
        """Run one tool on behalf of `agent`, honouring its allow-list."""
        if name not in TOOL_NAMES:
            raise ToolError("unknown_tool", f"no such tool: {name!r}")
        if name not in allowed:
            raise ToolNotAllowed(agent, name, allowed)

        call = ToolCall(
            name=name,
            args=dict(args),
            call_id=f"{name}-{sum(1 for c in self.calls if c.name == name) + 1}",
        )
        handler = _HANDLERS[name]
        try:
            call.result = handler(self.store, **dict(args))
        except UnknownBooking as exc:
            call.ok = False
            call.error = str(exc)
            call.result = {"error": "unknown_booking"}
        except ToolError as exc:
            call.ok = False
            call.error = exc.message
            call.result = {"error": exc.code}
        except TypeError as exc:
            # A bad argument name is a wiring defect in the agent, not something
            # the world refused. Recorded, but loudly enough to read in a report.
            call.ok = False
            call.error = f"bad arguments for {name}: {exc}"
            call.result = {"error": "bad_arguments"}
        self.calls.append(call)
        return call

    def take(self) -> list[ToolCall]:
        """Every call made since the last `take()`, in order."""
        fresh = self.calls[self._drained :]
        self._drained = len(self.calls)
        return list(fresh)

    def names(self) -> list[str]:
        """Every tool name called so far, in order. Handy in assertions."""
        return [c.name for c in self.calls]


_HANDLERS = {
    "search_tables": search_tables,
    "create_booking": create_booking,
    "modify_booking": modify_booking,
    "cancel_booking": cancel_booking,
    "check_policy": check_policy,
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _lookup(store: Restaurant, booking_ref: Any) -> Booking:
    if not str(booking_ref or "").strip():
        raise ToolError("missing_ref", "a booking reference is required")
    return store.booking(str(booking_ref))


def _as_party_size(value: Any) -> int:
    """Coerce a party size to a positive int, or fail with a readable message."""
    try:
        size = int(str(value).strip())
    except (TypeError, ValueError):
        raise ToolError("bad_party_size", f"party_size must be a whole number, got {value!r}") from None
    if size < 1:
        raise ToolError("bad_party_size", f"party_size must be at least 1, got {size}")
    return size


def _table_fields(table: Table | None) -> dict[str, Any]:
    return table.as_dict() if table is not None else {}
