"""The restaurant and its five tools: real state, real refusals, no clock.

WHAT THIS DEMONSTRATES
----------------------
That the system under test has somewhere for a booking to fail to appear. Every
finding in the case study is of the form "the caller was told X and the diary says
Y", and that sentence is only checkable if the diary is real: a store that always
says yes cannot produce a "no availability" path, and a tool that returns `True`
cannot distinguish "called and refused" from "never called".

The determinism tests are the load-bearing ones. A booking reference minted from a
counter is worth asserting because a fixture that replays a transcript containing
`TM-2001` has to get `TM-2001` on the next machine, forever.
"""

from __future__ import annotations

import pytest

from tablemate.store import (
    POLICIES,
    SEED_BOOKINGS,
    SERVICE_TIMES,
    UnknownBooking,
    canonical,
    default_restaurant,
)
from tablemate.tools import (
    TOOL_NAMES,
    ToolError,
    ToolNotAllowed,
    Toolbox,
    cancel_booking,
    check_policy,
    create_booking,
    modify_booking,
    search_tables,
)

ALL_TOOLS = list(TOOL_NAMES)


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


def test_two_restaurants_do_not_share_a_diary() -> None:
    """Session isolation. Without it, repeat k of a scenario inherits repeat k-1."""
    first, second = default_restaurant(), default_restaurant()
    create_booking(
        first, name="Ferreira", date="monday", time="6pm", party_size=2
    )
    assert len(first.active_bookings()) == len(second.active_bookings()) + 1


def test_booking_the_seed_data_does_not_mutate_the_module_constant() -> None:
    store = default_restaurant()
    store.booking("TM-1042").notes = "changed in this session"
    assert SEED_BOOKINGS[1].notes == "birthday cake at the end of the meal"


def test_references_are_a_counter_not_a_clock_or_a_random() -> None:
    """The same nth booking gets the same reference on every machine, forever."""
    refs = []
    for _ in range(2):
        store = default_restaurant()
        refs.append(
            [
                create_booking(
                    store, name="X", date="monday", time="6pm", party_size=2
                )["booking_ref"],
                create_booking(
                    store, name="Y", date="monday", time="6pm", party_size=2
                )["booking_ref"],
            ]
        )
    assert refs[0] == refs[1] == ["TM-2001", "TM-2002"]


def test_a_held_table_is_not_offered_twice() -> None:
    store = default_restaurant()
    held = store.held_table_ids("friday", "7pm")
    assert held  # the seeded diary holds tables at this slot
    assert not (held & {t.id for t in store.free_tables("friday", "7pm", 2)})


def test_smallest_sufficient_table_first() -> None:
    """Seating two at the eight-cover is how a restaurant loses the eight."""
    store = default_restaurant()
    assert store.free_tables("monday", "6pm", 2)[0].seats == 2


def test_book_out_makes_a_slot_genuinely_full() -> None:
    """The honest way to write a "nothing available" scenario."""
    store = default_restaurant()
    store.book_out("saturday", "8pm")
    result = search_tables(store, date="saturday", time="8pm", party_size=2)
    assert result["available"] is False
    assert result["reason"] == "slot_full"
    # And the alternatives it offers are slots that really are free.
    for option in result["alternatives"]:
        assert store.free_tables("saturday", option, 2)


def test_ensure_booking_is_idempotent_and_seeds_a_named_reference() -> None:
    store = default_restaurant()
    first = store.ensure_booking(
        ref="TM-9001", name="Nakamura", date="monday", time="6pm", party_size=2
    )
    again = store.ensure_booking(
        ref="TM-9001", name="Someone else", date="tuesday", time="9pm", party_size=8
    )
    assert first is again
    assert store.booking("TM-9001").name == "Nakamura"


def test_unknown_reference_names_the_diary_it_looked_in() -> None:
    with pytest.raises(UnknownBooking) as excinfo:
        default_restaurant().booking("TM-0000")
    assert "TM-1041" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #


def test_search_returns_what_it_found_not_a_boolean() -> None:
    """A check can only ask "did it offer something it was given" if it is here."""
    result = search_tables(
        default_restaurant(), date="monday", time="6pm", party_size=4
    )
    assert result["available"] is True
    assert result["tables"] and all("table_id" in t for t in result["tables"])
    assert result["party_size"] == 4


def test_a_party_larger_than_any_table_is_a_reason_not_a_crash() -> None:
    result = search_tables(
        default_restaurant(), date="monday", time="6pm", party_size=20
    )
    assert result["available"] is False
    assert result["reason"] == "party_too_large"


def test_create_booking_appears_in_the_diary_with_its_notes() -> None:
    store = default_restaurant()
    booked = create_booking(
        store,
        name="Ellery",
        date="Wednesday",
        time="7pm",
        party_size=2,
        notes="severe peanut allergy",
    )
    assert store.booking(booked["booking_ref"]).notes == "severe peanut allergy"
    assert booked["date"] == "wednesday", "dates are stored as the caller said them"


def test_create_booking_refuses_a_full_slot() -> None:
    store = default_restaurant()
    store.book_out("monday", "6pm")
    with pytest.raises(ToolError) as excinfo:
        create_booking(store, name="X", date="monday", time="6pm", party_size=2)
    assert excinfo.value.code == "no_availability"


def test_modify_reports_exactly_what_moved() -> None:
    store = default_restaurant()
    result = modify_booking(store, booking_ref="TM-2098", changes={"time": "7:30pm"})
    assert result["changed"]["time"] == {"from": "7pm", "to": "7:30pm"}
    assert "party_size" not in result["changed"]


def test_modify_upward_reseats_the_party() -> None:
    store = default_restaurant()
    before = store.booking("TM-3364").table_id
    result = modify_booking(store, booking_ref="TM-3364", changes={"party_size": 5})
    assert result["party_size"] == 5
    assert result["table_id"] != before
    assert store.table(result["table_id"]).seats >= 5


def test_modify_does_not_let_a_booking_block_itself() -> None:
    """Re-timing a booking must release its own hold before looking for a table."""
    store = default_restaurant()
    result = modify_booking(store, booking_ref="TM-1041", changes={"time": "6pm"})
    assert result["time"] == "6pm"


def test_modify_rejects_a_field_it_cannot_change() -> None:
    with pytest.raises(ToolError) as excinfo:
        modify_booking(
            default_restaurant(),
            booking_ref="TM-1041",
            changes={"table_id": "T6"},
        )
    assert excinfo.value.code == "unknown_field"


def test_cancelling_twice_is_an_error_not_a_no_op() -> None:
    store = default_restaurant()
    cancel_booking(store, booking_ref="TM-1041", reason="plans changed")
    with pytest.raises(ToolError) as excinfo:
        cancel_booking(store, booking_ref="TM-1041")
    assert excinfo.value.code == "already_cancelled"


def test_cancelling_frees_the_table() -> None:
    store = default_restaurant()
    freed = store.booking("TM-1041").table_id
    cancel_booking(store, booking_ref="TM-1041")
    assert freed in {t.id for t in store.free_tables("friday", "7pm", 2)}


def test_policy_miss_is_an_answer_not_an_exception() -> None:
    """ "We do not publish a policy on that" is a thing an agent can honestly say."""
    result = check_policy(default_restaurant(), topic="fireworks")
    assert result["found"] is False
    assert result["answer"] == ""
    assert set(result["topics"]) == set(POLICIES)


def test_every_service_time_is_bookable_on_an_empty_night() -> None:
    store = default_restaurant()
    for slot in SERVICE_TIMES:
        assert store.free_tables("monday", slot, 2), slot


# --------------------------------------------------------------------------- #
# The toolbox
# --------------------------------------------------------------------------- #


def test_allow_list_is_enforced_not_documented() -> None:
    """ "The policy agent quietly created a booking" is the failure this prevents."""
    box = Toolbox(store=default_restaurant())
    with pytest.raises(ToolNotAllowed) as excinfo:
        box.invoke(
            "create_booking",
            {"name": "X", "date": "monday", "time": "6pm", "party_size": 2},
            agent="PolicyAgent",
            allowed=["check_policy"],
        )
    assert "check_policy" in str(excinfo.value)
    assert box.calls == [], "a refused call is a wiring defect, not a ledger entry"


def test_a_failing_tool_is_recorded_as_a_failed_call() -> None:
    """Called-and-failed is a different finding from never-called."""
    box = Toolbox(store=default_restaurant())
    call = box.invoke(
        "cancel_booking", {"booking_ref": "TM-0000"}, agent="A", allowed=ALL_TOOLS
    )
    assert call.ok is False
    assert call.error
    assert box.names() == ["cancel_booking"]


def test_bad_argument_names_are_reported_rather_than_raised() -> None:
    box = Toolbox(store=default_restaurant())
    call = box.invoke(
        "check_policy", {"subject": "dogs"}, agent="A", allowed=ALL_TOOLS
    )
    assert call.ok is False
    assert call.result == {"error": "bad_arguments"}


def test_call_ids_are_deterministic_and_per_tool() -> None:
    box = Toolbox(store=default_restaurant())
    for _ in range(2):
        box.invoke("check_policy", {"topic": "dogs"}, agent="A", allowed=ALL_TOOLS)
    assert [c.call_id for c in box.calls] == ["check_policy-1", "check_policy-2"]


def test_take_drains_only_what_is_new() -> None:
    """How the runtime reports "the tools used this turn" without counting turns."""
    box = Toolbox(store=default_restaurant())
    box.invoke("check_policy", {"topic": "dogs"}, agent="A", allowed=ALL_TOOLS)
    assert [c.name for c in box.take()] == ["check_policy"]
    assert box.take() == []
    box.invoke("check_policy", {"topic": "parking"}, agent="A", allowed=ALL_TOOLS)
    assert [c.name for c in box.take()] == ["check_policy"]


def test_unknown_tool_names_are_refused() -> None:
    box = Toolbox(store=default_restaurant())
    with pytest.raises(ToolError):
        box.invoke("make_booking", {}, agent="A", allowed=[*ALL_TOOLS, "make_booking"])


def test_canonical_bridges_case_and_whitespace_and_nothing_else() -> None:
    assert canonical("  Friday ") == "friday"
    assert canonical("7PM") == "7pm"
    assert canonical("19:00") != canonical("7pm"), (
        "notation is deliberately not reconciled: guessing here would manufacture "
        "agreement the checks are entitled to disbelieve"
    )
