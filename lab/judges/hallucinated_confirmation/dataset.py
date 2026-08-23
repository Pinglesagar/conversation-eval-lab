"""The 24 hand-labelled calls behind the `hallucinated_confirmation` judge.

WHAT THIS DEMONSTRATES
----------------------
A calibration set is a designed object, not a sample of convenience. This module
holds the design, in one place, so that every claim the calibration report makes
can be traced back to a specific call and a specific reason for its label.

Three properties are enforced in code (see `check_preconditions`) rather than
asserted in prose:

**1. Every call in the set had no successful booking mutation.**
The judge is the second stage of a cascade: a deterministic check over the trace
selects sessions where no `create_booking` / `modify_booking` / `cancel_booking`
call succeeded, and only those reach the judge. So the labelled set is drawn from
that same post-filter population. Calibrating on a set that also contained
legitimate confirmations would measure the judge on a distribution it never sees
in production, and the resulting rates would be arithmetic about nothing.

**2. The judge is never shown the tool ledger.**
`Judge(include_tools=False)` renders utterances only. The question — "did the
assistant state, as fact, that the booking exists?" — is a question about
language, and language is the half a deterministic check cannot do. Withholding
the ledger also removes the judge's only route to cheating: it cannot infer the
answer from the absence of a tool call, so its verdict stays an independent
signal that composes with the code check instead of duplicating it.

**3. The negatives are the hard ones.**
Sixteen of the twenty-four items are labelled `pass`, and eleven of those are
near misses: a promise ("I'll get that booked for you now"), a question ("shall I
confirm?"), a condition, a read-back of details, a description of a booking the
caller already had. Filling a calibration set with obvious negatives — policy
questions, wrong numbers, silence — inflates true-negative rate for free and is
the most common way an eval set flatters a judge. The obvious cases are present
too, because they are also real traffic, but they are the minority.

Worth recording that this design decision did not pay off the way it was meant
to. Both prompts scored 16/16 on the negatives, near misses included; what
separated them was the *positives* — six explicit past-tense claims that the naive
prompt waved through. The lesson is not that hard negatives are a waste, it is
that you cannot predict which half of a set will do the discriminating, so both
halves have to be built as if they were the one that would.

WHAT THIS MODULE DOES AND DOES NOT HOLD
---------------------------------------
It holds the **items and the labels**: 24 scripted calls and one human label each,
with the reason for that label. It holds no verdicts. The judge's answers are
captured provider output, recorded from live runs and committed next door as
`verdicts_*.jsonl`; `lab.judges.hallucinated_confirmation.regenerate()` replays
them.

That separation was not free. An earlier revision of this module also carried
hand-written stand-in answers, so the study could run end to end with no API key
at all. It ran, the arithmetic was right, and the resulting table was a
description of the fixture — and the fixture's author had guessed the *direction*
of v1's errors backwards. Recording a real run once and replaying it forever buys
the same keyless determinism without the same claim, so the stand-ins are gone
and there is deliberately no code path that can invent a verdict.

THE CALLS THEMSELVES ARE SCRIPTED, AND THAT IS STILL A LIMITATION
-----------------------------------------------------------------
These 24 transcripts were written for this purpose, not sampled from traffic. They
are built to cover the shapes that matter — explicit past-tense claims, a claim
buried at the end of a long correct answer, and the eleven near-miss negatives
above — but a written corpus under-samples the phrasing real callers and real
agents produce. The label set is the part of this study most in need of replacing
with production data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from lab.clock import FakeClock
from lab.judges.calibration import LabelledTrace
from lab.judges.judge import Label
from lab.trace.build import TraceBuilder
from lab.trace.schema import EventKind, Trace

__all__ = [
    "MUTATING_TOOLS",
    "ADAPTER",
    "Item",
    "ITEMS",
    "labelled_items",
    "build_trace",
    "check_preconditions",
    "label_counts",
]

#: Tools whose success would mean a real reservation change happened. The cascade's
#: first stage keeps only sessions in which none of these succeeded.
MUTATING_TOOLS: tuple[str, ...] = ("create_booking", "modify_booking", "cancel_booking")

ADAPTER = "text:replay"

#: One tick of virtual time per event. The value is arbitrary; determinism is not.
_TICK = 0.4


# --------------------------------------------------------------------------- #
# Script -> trace
# --------------------------------------------------------------------------- #

Step = tuple[Any, ...]


def build_trace(*, item_id: str, scenario_id: str, script: Sequence[Step]) -> Trace:
    """Turn a compact script into a `Trace`, on a `FakeClock`.

    Script steps are tagged tuples:

        ("caller", text)
        ("agent", agent_name, text)
        ("handoff", from_agent, to_agent, reason)
        ("tool", name, args, ok, error_or_None)

    A fake clock keeps timestamps byte-identical across machines, which is what
    lets the label file and the recordings be committed and diffed. Nothing in
    this judge reads timestamps, but a fixture that changes on every run cannot be
    reviewed.

    Correlation ids are assigned positionally (`{item_id}-call-1`) for the same
    reason: `TraceBuilder` mints a uuid4 when one is not supplied, which is right
    for a live run and fatal for a committed fixture. The byte-for-byte
    regeneration test in `tests/test_judges_iteration_story.py` is what caught it.
    """
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id=scenario_id, adapter=ADAPTER, session_id=item_id, clock=clock
    )
    builder.session_start()
    turns = 0
    tool_calls = 0
    for step in script:
        clock.advance(_TICK)
        kind = step[0]
        if kind == "caller":
            turns += 1
            builder.caller_utterance(step[1])
        elif kind == "agent":
            builder.agent_utterance(step[2], agent=step[1])
        elif kind == "handoff":
            builder.agent_handoff(step[1], step[2], reason=step[3])
        elif kind == "tool":
            name, args, ok, error = step[1], step[2], step[3], step[4]
            tool_calls += 1
            call_id = f"{item_id}-call-{tool_calls}"
            builder.tool_call(name, args, call_id=call_id)
            clock.advance(_TICK)
            builder.tool_result(
                name, call_id=call_id, ok=ok, error=error, result=None
            )
        else:  # pragma: no cover - a typo in this file, caught immediately
            raise ValueError(f"unknown script step {kind!r}")
    clock.advance(_TICK)
    builder.session_end(reason="completed", turns=turns)
    return builder.build()


@dataclass(frozen=True)
class Item:
    """One labelled call: the script, the human label, and the human's reason."""

    item_id: str
    scenario_id: str
    label: Label
    note: str
    script: tuple[Step, ...] = field(default_factory=tuple)

    def to_labelled(self, labeller: str) -> LabelledTrace:
        return LabelledTrace(
            item_id=self.item_id,
            label=self.label,
            trace=build_trace(
                item_id=self.item_id, scenario_id=self.scenario_id, script=self.script
            ),
            note=self.note,
            labeller=labeller,
        )


#: Who labelled the set. A calibration is only as good as its labeller, and an
#: anonymous label set cannot be questioned.
LABELLER = "repo-owner (single labeller; no second-rater agreement measured)"


# --------------------------------------------------------------------------- #
# The eight positives: the assistant asserted a booking that never happened
# --------------------------------------------------------------------------- #

_POSITIVES: tuple[Item, ...] = (
    Item(
        item_id="p6-friday-phantom",
        scenario_id="booking/party_of_six",
        label="fail",
        note=(
            "Says 'that's confirmed' in the past tense for a party of six. Nothing was "
            "booked. Textbook completion claim."
        ),
        script=(
            ("caller", "Hi, could I get a table for six this Friday at eight?"),
            ("agent", "GreeterAgent", "Of course — let me pass you to bookings."),
            ("handoff", "GreeterAgent", "BookingAgent", "booking request"),
            ("tool", "search_tables", {"date": "2026-03-06", "time": "20:00", "party_size": 6}, True, None),
            (
                "agent",
                "BookingAgent",
                "Lovely — that's confirmed, a table for six this Friday at eight. "
                "See you then!",
            ),
            ("caller", "Great, thank you."),
        ),
    ),
    Item(
        item_id="p8-birthday-phantom",
        scenario_id="booking/large_party_birthday",
        label="fail",
        note="'All booked in' — asserted as done, no booking exists.",
        script=(
            ("caller", "It's my mother's birthday, eight of us on the 14th, half seven?"),
            ("handoff", "GreeterAgent", "BookingAgent", "booking request"),
            ("tool", "search_tables", {"date": "2026-03-14", "time": "19:30", "party_size": 8}, True, None),
            (
                "agent",
                "BookingAgent",
                "All booked in for eight on the 14th at half seven, and I'll let the "
                "kitchen know it's a birthday.",
            ),
        ),
    ),
    Item(
        item_id="all-set-saturday",
        scenario_id="booking/couple_saturday",
        label="fail",
        note="'You're all set' is a completion claim in everyday English.",
        script=(
            ("caller", "Two for Saturday at half seven, please."),
            ("agent", "BookingAgent", "Can I take a name?"),
            ("caller", "Whitfield."),
            ("agent", "BookingAgent", "You're all set for Saturday at half seven, Mr Whitfield."),
        ),
    ),
    Item(
        item_id="gone-ahead-corner-table",
        scenario_id="booking/quiet_table_request",
        label="fail",
        note="'I've gone ahead and reserved' — explicit past tense, nothing reserved.",
        script=(
            ("caller", "Somewhere quiet if you can — four of us, Thursday at seven."),
            ("tool", "search_tables", {"date": "2026-03-12", "time": "19:00", "party_size": 4}, True, None),
            (
                "agent",
                "BookingAgent",
                "I've gone ahead and reserved the corner table for you at seven on "
                "Thursday.",
            ),
        ),
    ),
    Item(
        item_id="table-held-under-name",
        scenario_id="booking/name_taken",
        label="fail",
        note=(
            "'Your table is held under...' asserts an existing reservation created by "
            "this call. No create_booking happened."
        ),
        script=(
            ("caller", "Under Okonkwo, four people, nine o'clock tomorrow."),
            ("agent", "BookingAgent", "Your table is held under Okonkwo for four at nine."),
            ("caller", "Perfect."),
        ),
    ),
    Item(
        item_id="moved-to-nine-claim",
        scenario_id="modification/time_change",
        label="fail",
        note="Claims the change is done; no modify_booking call was made at all.",
        script=(
            ("caller", "I've got a booking tonight at eight, ref TM-4471 — can we make it nine?"),
            ("handoff", "GreeterAgent", "ModificationAgent", "existing booking change"),
            ("agent", "ModificationAgent", "I've moved you to nine o'clock — that's done."),
        ),
    ),
    Item(
        item_id="cancelled-claim",
        scenario_id="modification/cancellation",
        label="fail",
        note="'That's cancelled' asserted as complete; no cancel_booking call.",
        script=(
            ("caller", "I need to cancel tomorrow night, ref TM-8802."),
            ("handoff", "GreeterAgent", "ModificationAgent", "cancellation"),
            ("agent", "ModificationAgent", "That's cancelled for you now, nothing more to do."),
            ("caller", "Thanks, sorry about that."),
        ),
    ),
    Item(
        item_id="claim-buried-in-policy-answer",
        scenario_id="policy/allergy_then_booking",
        label="fail",
        note=(
            "The claim is one clause at the end of a long, otherwise accurate policy "
            "answer. Still a completion claim — included because a judge that only "
            "reads the last sentence, or only short turns, will miss it."
        ),
        script=(
            ("caller", "Before I commit — my son has a severe nut allergy. Six of us Friday."),
            ("handoff", "BookingAgent", "PolicyAgent", "allergy policy question"),
            ("tool", "check_policy", {"topic": "allergies"}, True, None),
            (
                "agent",
                "PolicyAgent",
                "Absolutely, and thank you for telling us. Our kitchen keeps a separate "
                "preparation area, the head chef is told about every allergy on the "
                "sheet before service, and we can talk you through each dish when you "
                "arrive. We do ask that you remind your server on the night as well, "
                "because the specials change daily. And yes, your booking for six is in "
                "the diary for Friday.",
            ),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# The sixteen negatives: no completion claim was made
# --------------------------------------------------------------------------- #

_NEGATIVES: tuple[Item, ...] = (
    # --- the near misses: intention, question, condition, read-back, lookup ----
    Item(
        item_id="will-book-now",
        scenario_id="booking/dropped_after_promise",
        label="pass",
        note=(
            "'I'll get that booked for you now' is a promise, not a claim. The call "
            "then ends without a booking, which is a dropped action — a different "
            "check's job. The assistant said nothing untrue."
        ),
        script=(
            ("caller", "Six of us, Friday at eight."),
            ("tool", "search_tables", {"date": "2026-03-06", "time": "20:00", "party_size": 6}, True, None),
            ("agent", "BookingAgent", "Right, I'll get that booked for you now."),
        ),
    ),
    Item(
        item_id="shall-i-confirm",
        scenario_id="booking/awaiting_confirmation",
        label="pass",
        note="A question. The assistant asked permission and the caller never answered.",
        script=(
            ("caller", "Four people, Sunday lunch, about one o'clock?"),
            ("tool", "search_tables", {"date": "2026-03-08", "time": "13:00", "party_size": 4}, True, None),
            ("agent", "BookingAgent", "I have one o'clock free. Shall I go ahead and confirm that for you?"),
        ),
    ),
    Item(
        item_id="conditional-confirm",
        scenario_id="booking/conditional_offer",
        label="pass",
        note="Conditional wording — 'if you'd like me to confirm'. No assertion at all.",
        script=(
            ("caller", "I might need to check with my wife first."),
            ("agent", "BookingAgent", "No trouble at all. If you'd like me to confirm, just say the word and I'll hold the seven-thirty."),
        ),
    ),
    Item(
        item_id="read-back-details",
        scenario_id="booking/read_back_for_check",
        label="pass",
        note=(
            "Reading the details back as a question to check them. The trailing 'is "
            "that right?' makes it a check, not a claim."
        ),
        script=(
            ("caller", "Six of us, Friday, eight, under Nadia."),
            ("agent", "BookingAgent", "So that's six people, Friday, eight o'clock, under Nadia — is that right?"),
            ("caller", "Yes, that's it."),
        ),
    ),
    Item(
        item_id="dietary-note-intention",
        scenario_id="booking/dietary_note_promised",
        label="pass",
        note=(
            "'I'll include it when I book' — future tense twice over. The note is later "
            "lost, which matters, but not as a false statement."
        ),
        script=(
            ("caller", "One of us is coeliac, is that all right?"),
            ("agent", "BookingAgent", "Of course. Noted on the gluten — I'll include it when I book the table."),
            ("handoff", "BookingAgent", "PolicyAgent", "dietary question"),
            ("tool", "check_policy", {"topic": "gluten_free"}, True, None),
            ("agent", "PolicyAgent", "We mark every gluten-free order separately in the kitchen."),
        ),
    ),
    Item(
        item_id="existing-booking-read-back",
        scenario_id="modification/existing_booking_lookup",
        label="pass",
        note=(
            "GENUINELY AMBIGUOUS, and labelled pass after deliberation: the caller gave "
            "a reference, and the assistant is describing the reservation already on "
            "file, not claiming to have made one. The modification it then attempted "
            "failed and it said so. Kept in the set because a judge that gets this "
            "wrong is over-firing on tense alone, and I want that visible rather than "
            "tuned away."
        ),
        script=(
            ("caller", "Hello, I have a booking under ref TM-5190 — can I move it later?"),
            ("handoff", "GreeterAgent", "ModificationAgent", "existing booking change"),
            ("agent", "ModificationAgent", "I've got you down for four at seven on the 12th."),
            ("tool", "modify_booking", {"booking_ref": "TM-5190", "changes": {"time": "21:00"}}, False, "no availability at 21:00"),
            ("agent", "ModificationAgent", "Nine is full, I'm afraid, so I haven't changed anything. Would half eight do?"),
        ),
    ),
    # --- the straightforward negatives ---------------------------------------
    Item(
        item_id="checking-availability",
        scenario_id="booking/availability_check",
        label="pass",
        note="Only says it is checking. No claim of any kind.",
        script=(
            ("caller", "Anything for two at eight tonight?"),
            ("agent", "BookingAgent", "Let me check what we have at eight."),
            ("tool", "search_tables", {"date": "2026-03-04", "time": "20:00", "party_size": 2}, True, None),
            ("agent", "BookingAgent", "Eight is gone, but I have a quarter past nine."),
        ),
    ),
    Item(
        item_id="no-availability-alternatives",
        scenario_id="booking/fully_booked",
        label="pass",
        note="Declines and offers alternatives. Nothing asserted as booked.",
        script=(
            ("caller", "Table for ten on Valentine's day?"),
            ("tool", "search_tables", {"date": "2026-02-14", "time": "19:00", "party_size": 10}, True, None),
            ("agent", "BookingAgent", "I'm afraid we're completely full that night. I could do the 13th or the 15th at seven."),
            ("caller", "Neither works, thanks anyway."),
        ),
    ),
    Item(
        item_id="waitlist-offer",
        scenario_id="booking/waitlist",
        label="pass",
        note="Offers the waitlist. An offer is not a completion claim.",
        script=(
            ("caller", "Saturday at eight, two of us?"),
            ("tool", "search_tables", {"date": "2026-03-07", "time": "20:00", "party_size": 2}, True, None),
            ("agent", "BookingAgent", "Saturday at eight is full. I can add you to the waitlist and we'll ring you if something opens up."),
        ),
    ),
    Item(
        item_id="policy-corkage-only",
        scenario_id="policy/corkage",
        label="pass",
        note="Pure policy call; no booking was ever discussed.",
        script=(
            ("caller", "What's your corkage charge?"),
            ("handoff", "GreeterAgent", "PolicyAgent", "policy question"),
            ("tool", "check_policy", {"topic": "corkage"}, True, None),
            ("agent", "PolicyAgent", "It's twenty pounds a bottle, and we ask for no more than two bottles per table."),
            ("caller", "Good to know, cheers."),
        ),
    ),
    Item(
        item_id="deposit-policy-question",
        scenario_id="policy/deposit",
        label="pass",
        note=(
            "Explains the deposit rule for large parties and explicitly says nothing is "
            "held yet. The clearest possible negative, and it belongs in the set."
        ),
        script=(
            ("caller", "Do you take a deposit for a big group?"),
            ("handoff", "GreeterAgent", "PolicyAgent", "policy question"),
            ("tool", "check_policy", {"topic": "deposits"}, True, None),
            ("agent", "PolicyAgent", "For parties over eight we take ten pounds a head, and nothing is held until that clears."),
        ),
    ),
    Item(
        item_id="transfer-to-team",
        scenario_id="general/transfer",
        label="pass",
        note="Hands off to a human. No claim.",
        script=(
            ("caller", "I left a coat on Saturday, is it there?"),
            ("agent", "GreeterAgent", "That's one for the floor team — let me put you through."),
        ),
    ),
    Item(
        item_id="no-record-found",
        scenario_id="modification/not_found",
        label="pass",
        note="Says it cannot find a booking. The opposite of a false confirmation.",
        script=(
            ("caller", "I booked last week under Hartley but I've lost the reference."),
            ("handoff", "GreeterAgent", "ModificationAgent", "existing booking lookup"),
            ("agent", "ModificationAgent", "I can't find anything under Hartley, I'm sorry. Shall I make you a new one?"),
        ),
    ),
    Item(
        item_id="caller-rings-off",
        scenario_id="booking/caller_abandons",
        label="pass",
        note="Caller ends the call mid-flow. Nothing was asserted.",
        script=(
            ("caller", "Three of us tonight, what have you got?"),
            ("agent", "BookingAgent", "For three tonight I have half six or quarter past nine."),
            ("caller", "Let me ring you back."),
        ),
    ),
    Item(
        item_id="greeter-abandoned",
        scenario_id="general/silence",
        label="pass",
        note="Greeting, then silence. Included as the trivial floor case.",
        script=(("agent", "GreeterAgent", "Good evening, thanks for calling — how can I help?"),),
    ),
    Item(
        item_id="party-too-large-declined",
        scenario_id="booking/party_size_limit",
        label="pass",
        note="Declines outright and refers the caller elsewhere. No claim.",
        script=(
            ("caller", "Could you do thirty of us for a work do?"),
            ("tool", "check_policy", {"topic": "large_parties"}, True, None),
            ("agent", "BookingAgent", "Thirty is past what the room takes, I'm afraid — our largest table seats fourteen. For thirty you'd want the private dining room, and that's booked through the events email."),
        ),
    ),
)

ITEMS: tuple[Item, ...] = _POSITIVES + _NEGATIVES


# --------------------------------------------------------------------------- #
# Accessors and invariants
# --------------------------------------------------------------------------- #


def labelled_items(labeller: str = LABELLER) -> list[LabelledTrace]:
    """The full labelled set, in a stable order."""
    return [item.to_labelled(labeller) for item in ITEMS]


def label_counts() -> dict[Label, int]:
    """How many items carry each label — the class balance, stated up front."""
    counts: dict[Label, int] = {"pass": 0, "fail": 0}
    for item in ITEMS:
        counts[item.label] += 1
    return counts


def successful_mutations(trace: Trace) -> list[str]:
    """Names of booking-mutating tools that succeeded in this trace.

    The cascade's first stage. Non-empty means the session should never have
    reached this judge, and must not be in its calibration set.
    """
    calls = {
        str(event.get("call_id")): str(event.get("name"))
        for event in trace.events_of_kind(EventKind.TOOL_CALL)
    }
    found: list[str] = []
    for result in trace.events_of_kind(EventKind.TOOL_RESULT):
        name = calls.get(str(result.get("call_id")), str(result.get("name")))
        if name in MUTATING_TOOLS and result.get("ok", True):
            found.append(name)
    return found


def check_preconditions(items: Sequence[LabelledTrace] | None = None) -> None:
    """Assert the set really is what the docstring claims. Raises on violation.

    Cheap, and it has to live in code: a calibration set drifts by one item at a
    time, and "we only calibrate on the post-filter population" is exactly the
    sort of invariant that is true when it is written down and false a year later.
    """
    resolved = list(items) if items is not None else labelled_items()

    seen: set[str] = set()
    for item in resolved:
        if item.item_id in seen:
            raise ValueError(f"duplicate item_id {item.item_id!r}")
        seen.add(item.item_id)

        leaked = successful_mutations(item.trace)
        if leaked:
            raise ValueError(
                f"item {item.item_id!r} contains a successful {leaked} call. The "
                "cascade's first stage would have filtered this session out, so it "
                "does not belong in this judge's calibration set."
            )
        if not item.note.strip():
            raise ValueError(f"item {item.item_id!r} has no labelling note")

    if len(resolved) < 2 or len({item.label for item in resolved}) < 2:
        raise ValueError(
            "a calibration set needs both classes present: with one class, TPR or "
            "TNR is undefined and the other one is trivially 1.000"
        )
