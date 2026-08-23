"""The restaurant's state: tables, the booking diary, and the policy sheet.

WHAT THIS DEMONSTRATES
----------------------
That the system under test has *real state*, and that none of its behaviour
depends on a clock, a random seed or a network. Every seeded bug in this package
is a bug in how information moves between agents, and a claim like "the booking
was never created" is only checkable if there is somewhere for a booking to fail
to appear. So this module is a small honest data layer: an in-memory restaurant
with six tables, three bookings already in the diary, and a policy sheet.

Three properties are deliberate:

*   **No randomness, no wall clock.** Booking references come from a counter that
    starts at a fixed value, so the reference minted by the fourth booking of a
    session is `TM-2001` on every machine, forever. A fixture that replays a
    conversation containing a reference must be able to match it exactly.
*   **Dates and times are stored as the caller said them.** `"friday"` and
    `"7pm"`, lower-cased and nothing more. This package does not parse dates,
    because `lab.checks.text` deliberately refuses to equate `"7pm"` with
    `"19:00"` and inventing a normaliser here would produce agreement the
    checks are entitled to disbelieve. One surface form per value, end to end.
*   **Availability is a real constraint, not a stub.** A table is taken if a
    confirmed booking holds it for that date and time, so "no availability" is a
    reachable path with a reachable alternative list rather than a branch that
    only a mock can enter.

WHAT THIS DOES NOT DO
---------------------
No persistence, no concurrency control, no service windows, no table-joining for
large parties, and no date arithmetic (so "next Friday" and "Friday" are two
different days as far as this store is concerned). Those are all real features of
a real booking system and all irrelevant to what the harness is measuring; adding
them would grow the system under test without growing what it can demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

__all__ = [
    "RESTAURANT_NAME",
    "SERVICE_TIMES",
    "Table",
    "Booking",
    "UnknownBooking",
    "Restaurant",
    "default_restaurant",
    "POLICIES",
    "SEED_TABLES",
    "SEED_BOOKINGS",
    "canonical",
]

#: The restaurant. Named so that transcripts read like transcripts.
RESTAURANT_NAME: str = "Lumen"

#: Bookable slots, in the surface form the agents and callers use. Only consulted
#: when suggesting alternatives for a slot that is full.
SERVICE_TIMES: tuple[str, ...] = (
    "6pm",
    "6:30pm",
    "7pm",
    "7:30pm",
    "8pm",
    "8:30pm",
    "9pm",
)


def canonical(value: Any) -> str:
    """The comparison form of a date, time or reference: trimmed and lower-cased.

    Casing and stray whitespace are the only variation this store bridges. It
    does *not* reconcile notations — see the module docstring.
    """
    return str(value).strip().lower()


@dataclass(frozen=True)
class Table:
    """One physical table. Frozen: the floor plan does not change mid-call."""

    id: str
    seats: int
    area: str

    def describe(self) -> str:
        """How an agent would refer to this table out loud."""
        return f"a table for {self.seats} in the {self.area}"

    def as_dict(self) -> dict[str, Any]:
        """The shape a tool result exposes."""
        return {"table_id": self.id, "seats": self.seats, "area": self.area}


@dataclass
class Booking:
    """One reservation in the diary.

    `notes` is the field the third seeded bug is about: it is where a dietary
    requirement is supposed to end up, and an empty `notes` on a booking whose
    caller declared an allergy is the whole finding.
    """

    ref: str
    name: str
    date: str
    time: str
    party_size: int
    table_id: str
    notes: str = ""
    status: str = "confirmed"
    cancel_reason: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "confirmed"

    def as_dict(self) -> dict[str, Any]:
        """The shape a tool result exposes."""
        return {
            "booking_ref": self.ref,
            "name": self.name,
            "date": self.date,
            "time": self.time,
            "party_size": self.party_size,
            "table_id": self.table_id,
            "notes": self.notes,
            "status": self.status,
        }


class UnknownBooking(KeyError):
    """Raised when a reference is not in the diary. Surfaced to the caller as a tool error."""


#: The policy sheet, as the `check_policy` tool serves it. Keys are topic slugs;
#: an agent maps the caller's words onto one of them and reads the value back.
POLICIES: Mapping[str, str] = {
    "cancellation": (
        "You can cancel or change any booking up to four hours before the sitting "
        "with no charge. Inside four hours we ask for a note in the diary so the "
        "kitchen can plan."
    ),
    "dogs": (
        "Assistance dogs are welcome everywhere in the building, and well-behaved "
        "dogs are welcome on the terrace."
    ),
    "children": (
        "Children are welcome until nine in the evening. We have four high chairs "
        "and a shorter menu for under-tens."
    ),
    "dress_code": "There is no dress code. Most people come as they are.",
    "corkage": "You are welcome to bring your own wine; corkage is charged per bottle.",
    "accessibility": (
        "The dining room and the accessible toilet are step-free from the street "
        "entrance. The terrace is up one shallow step."
    ),
    "deposit": (
        "Deposits are only taken for parties of six or more, and are set against "
        "the final bill."
    ),
    "parking": "There is no car park; the multi-storey on Mercer Street is two minutes' walk.",
    "large_groups": (
        "Parties of six or more are seated in the private room, which is booked "
        "with a deposit and a pre-order two days before."
    ),
    "menu": (
        "The a la carte menu changes monthly. There is a set menu of three "
        "courses, and parties of six or more choose from it in advance."
    ),
    "allergies": (
        "Every dish is marked for the fourteen major allergens, and the kitchen "
        "will cook around an allergy if it is noted on the booking."
    ),
}

#: The floor plan. Two-, four-, six- and eight-covers, so party size genuinely
#: changes which tables are candidates.
SEED_TABLES: tuple[Table, ...] = (
    Table(id="T1", seats=2, area="window"),
    Table(id="T2", seats=2, area="bar"),
    Table(id="T3", seats=4, area="window"),
    Table(id="T4", seats=4, area="dining room"),
    Table(id="T5", seats=6, area="dining room"),
    Table(id="T6", seats=8, area="private room"),
)

#: Bookings already in the diary — the ones an amendment or cancellation scenario
#: can name. `TM-1042` carries a note so that "this system can store notes" is
#: demonstrably true, which is what makes an empty `notes` elsewhere a defect
#: rather than an unimplemented field.
#:
#: A scenario that needs a reference this diary does not hold should seed it with
#: `Restaurant.ensure_booking`, and one that needs a full house should call
#: `Restaurant.book_out`. Both exist so that "there is nothing free" and "that
#: reference is ours" are properties of the fixture rather than of a mock.
SEED_BOOKINGS: tuple[Booking, ...] = (
    Booking(
        ref="TM-1041",
        name="Ferreira",
        date="friday",
        time="7pm",
        party_size=2,
        table_id="T1",
    ),
    Booking(
        ref="TM-1042",
        name="Okonkwo",
        date="saturday",
        time="8pm",
        party_size=4,
        table_id="T3",
        notes="birthday cake at the end of the meal",
    ),
    Booking(
        ref="TM-1043",
        name="Lindqvist",
        date="sunday",
        time="1pm",
        party_size=6,
        table_id="T5",
    ),
    Booking(
        ref="TM-2098",
        name="Bhattacharya",
        date="friday",
        time="7pm",
        party_size=2,
        table_id="T2",
    ),
    Booking(
        ref="TM-3364",
        name="Aldridge",
        date="thursday",
        time="7pm",
        party_size=4,
        table_id="T4",
    ),
    Booking(
        ref="TM-4417",
        name="Okonkwo",
        date="tuesday",
        time="7:30pm",
        party_size=2,
        table_id="T1",
    ),
    Booking(
        ref="TM-7731",
        name="Okonkwo",
        date="friday",
        time="8pm",
        party_size=4,
        table_id="T3",
    ),
)

#: The first reference a fresh store will mint. Well clear of the seeded 1041-1043
#: block so a new booking is obvious at a glance in a transcript.
FIRST_NEW_REF: int = 2001


class Restaurant:
    """Tables, the booking diary and the policy sheet, in memory.

    Construct a fresh one per session. Nothing here is shared, static or
    module-level mutable state: two conversations running in one process must not
    be able to see each other's bookings, or a `pass^k` repeat would inherit the
    previous repeat's diary and measure history instead of behaviour.
    """

    def __init__(
        self,
        *,
        tables: Iterable[Table] = SEED_TABLES,
        bookings: Iterable[Booking] = SEED_BOOKINGS,
        policies: Mapping[str, str] = POLICIES,
        first_ref: int = FIRST_NEW_REF,
        ref_prefix: str = "TM",
    ) -> None:
        self.tables: tuple[Table, ...] = tuple(tables)
        # Copies, so a caller cannot mutate the module-level seed data by booking.
        self.bookings: list[Booking] = [replace(b) for b in bookings]
        self.policies: dict[str, str] = dict(policies)
        self._next_ref = int(first_ref)
        self._ref_prefix = ref_prefix

    # ------------------------------------------------------------------ tables

    @property
    def max_party_size(self) -> int:
        """The largest party any single table can seat."""
        return max((t.seats for t in self.tables), default=0)

    def table(self, table_id: str) -> Table | None:
        return next((t for t in self.tables if t.id == table_id), None)

    def held_table_ids(self, date: str, time: str) -> set[str]:
        """Tables already committed for this slot by an active booking."""
        slot = (canonical(date), canonical(time))
        return {
            b.table_id
            for b in self.bookings
            if b.is_active and (canonical(b.date), canonical(b.time)) == slot
        }

    def free_tables(self, date: str, time: str, party_size: int) -> list[Table]:
        """Tables that fit `party_size` and are free at this slot, smallest first.

        Smallest-sufficient-first is the ordinary restaurant rule: seating two
        people at the eight-cover in the private room is how you lose the eight.
        """
        taken = self.held_table_ids(date, time)
        candidates = [
            t for t in self.tables if t.id not in taken and t.seats >= int(party_size)
        ]
        return sorted(candidates, key=lambda t: (t.seats, t.id))

    def alternative_times(
        self, date: str, time: str, party_size: int, *, limit: int = 3
    ) -> list[str]:
        """Other slots on the same date that could seat this party.

        Nearest-first by position in `SERVICE_TIMES`, because "half an hour later"
        is a useful offer and "four hours earlier" is not.
        """
        wanted = canonical(time)
        options = [
            t for t in SERVICE_TIMES if canonical(t) != wanted and self.free_tables(date, t, party_size)
        ]
        if wanted in [canonical(t) for t in SERVICE_TIMES]:
            index = [canonical(t) for t in SERVICE_TIMES].index(wanted)
            options.sort(key=lambda t: abs([canonical(x) for x in SERVICE_TIMES].index(canonical(t)) - index))
        return options[:limit]

    # ---------------------------------------------------------------- bookings

    def booking(self, ref: str) -> Booking:
        """The booking with this reference, or `UnknownBooking`."""
        wanted = canonical(ref)
        for candidate in self.bookings:
            if canonical(candidate.ref) == wanted:
                return candidate
        raise UnknownBooking(
            f"no booking with reference {ref!r}; the diary holds "
            f"{', '.join(b.ref for b in self.bookings)}"
        )

    def has_booking(self, ref: str) -> bool:
        try:
            self.booking(ref)
        except UnknownBooking:
            return False
        return True

    def active_bookings(self) -> list[Booking]:
        return [b for b in self.bookings if b.is_active]

    def mint_ref(self) -> str:
        """The next booking reference. Deterministic, sequential, no clock."""
        ref = f"{self._ref_prefix}-{self._next_ref}"
        self._next_ref += 1
        return ref

    def add_booking(
        self,
        *,
        name: str,
        date: str,
        time: str,
        party_size: int,
        notes: str = "",
        table: Table,
    ) -> Booking:
        """Commit a booking to the diary and return it."""
        booking = Booking(
            ref=self.mint_ref(),
            name=str(name),
            date=canonical(date),
            time=canonical(time),
            party_size=int(party_size),
            table_id=table.id,
            notes=str(notes or ""),
        )
        self.bookings.append(booking)
        return booking

    # ------------------------------------------------------------------ fixtures

    def ensure_booking(
        self,
        *,
        ref: str,
        name: str,
        date: str,
        time: str,
        party_size: int,
        notes: str = "",
        table_id: str | None = None,
    ) -> Booking:
        """Put a specific reference in the diary, for a scenario that names one.

        Returns the existing booking if the reference is already there, so
        seeding is idempotent. The table is chosen the same way a real booking
        would choose it unless one is named.
        """
        if self.has_booking(ref):
            return self.booking(ref)
        if table_id is not None:
            table = self.table(table_id)
            if table is None:
                raise ValueError(f"no such table: {table_id!r}")
        else:
            free = self.free_tables(date, time, party_size)
            if not free:
                raise ValueError(
                    f"cannot seed {ref}: nothing free for {party_size} at "
                    f"{canonical(time)} on {canonical(date)}"
                )
            table = free[0]
        booking = Booking(
            ref=str(ref),
            name=str(name),
            date=canonical(date),
            time=canonical(time),
            party_size=int(party_size),
            table_id=table.id,
            notes=notes,
        )
        self.bookings.append(booking)
        return booking

    def book_out(self, date: str, time: str, *, name: str = "House") -> list[Booking]:
        """Fill every free table at one slot, so that slot is genuinely full.

        The honest way to write a "nothing available" scenario: the tools then
        report no availability because there is none, and the alternatives they
        offer are the slots that really are free. Returns the bookings it added.
        """
        added: list[Booking] = []
        for table in self.free_tables(date, time, 1):
            added.append(
                Booking(
                    ref=self.mint_ref(),
                    name=name,
                    date=canonical(date),
                    time=canonical(time),
                    party_size=table.seats,
                    table_id=table.id,
                    notes="held",
                )
            )
            self.bookings.append(added[-1])
        return added

    # ---------------------------------------------------------------- policies

    def policy(self, topic: str) -> str | None:
        return self.policies.get(canonical(topic).replace(" ", "_"))

    def policy_topics(self) -> list[str]:
        return sorted(self.policies)


def default_restaurant() -> Restaurant:
    """A fresh restaurant with the seeded floor plan and diary.

    A function rather than a module-level instance on purpose: an instance would
    be shared mutable state, and every repeat of a scenario needs its own diary.
    """
    return Restaurant()
