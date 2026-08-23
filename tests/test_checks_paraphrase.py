"""The literal-phrase problem, measured in both directions.

WHAT THIS DEMONSTRATES
----------------------
That the paraphrase-tolerance work is a **measurement rather than a refactor.**
Every number asserted here was read off real data — 24 hand-labelled traces and 30
recorded live conversations — and every one of them can be recomputed by anyone
with the checkout, offline, with no key.

THE PROBLEM
-----------
A scripted agent says one string; a real model says whatever it likes. Every
literal in a check written against the scripted agent is therefore a check that
works exactly once. Two measurements bound how bad that is on this corpus:

**Against the judge's 24 hand-labelled items** (`lab/judges/hallucinated_
confirmation/labels.jsonl` — 8 confirmations, 16 negatives, 11 of them deliberate
near misses), `PromiseContract` scored TPR 6/8 and TNR 14/16 before this work and
8/8 and 16/16 after. Both misses and both false alarms are named in the tests
below with the reasoning that fixed them, because "we improved the patterns" is
not a finding and "these four items moved, for these four reasons" is.

**Against 30 recorded live conversations** (`fixtures/live_run/traces`, produced by
a real model in the previous phase), `PromiseContract` caught **1 of the 7**
unbacked confirmations that a deliberately generous hand-written detector found.
After the rewrite it catches 7 of 7 — and one more that the hand-written detector
missed. That ratio, 1/7, is the honest size of the literal-phrase problem: the
most carefully reviewed pattern set in the repository was 86% blind to a model's
wording.

WHY THE NUMBERS ARE PINNED HERE
-------------------------------
Because a report that quotes them has to be falsifiable. If somebody edits a
pattern and the recall drops, this file fails; if somebody edits it and the
precision drops, this file fails as well. A prose claim in a README cannot do
either.

WHAT THESE NUMBERS ARE NOT
--------------------------
8/8 and 16/16 on 24 items is consistent with true rates as low as 0.68 and 0.81
(95% Wilson lower bounds), and the 30 live traces are one draw from one model at
one temperature. A set that a detector never fails cannot measure that detector
again — so these are a floor under a known failure mode, not a claim of
correctness, and the honest next step is harder items rather than more patterns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.checks import PromiseContract
from lab.checks.text import fold_typography, normalize
from lab.simulator.persona import Goal
from lab.trace.io import read_jsonl
from lab.trace.schema import Trace

REPO = Path(__file__).resolve().parents[1]
LABELS = REPO / "lab/judges/hallucinated_confirmation/labels.jsonl"
LIVE_TRACES = REPO / "fixtures/live_run/traces"


def _labelled() -> list[tuple[str, bool, Trace]]:
    """The judge's set as (item_id, is_a_confirmation, trace)."""
    rows = [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [(r["item_id"], r["label"] == "fail", Trace.model_validate(r["trace"])) for r in rows]


def _fires(trace: Trace) -> bool:
    return not PromiseContract().check(trace).passed


# --------------------------------------------------------------------------- #
# The deterministic detector, scored on the judge's own labelled set
# --------------------------------------------------------------------------- #


def test_the_promise_detector_scores_perfectly_on_the_judge_label_set() -> None:
    """TPR 8/8, TNR 16/16 — the same set, and the same items, as the v2 judge.

    Worth having for a reason beyond the score: it puts a *deterministic* check and
    a *model* check on the same 24 items, and they agree. Where a rule can be
    written, the rule is free, has no variance, needs no key and cannot be
    rate-limited — so the interesting question about a judge is never "is it good"
    but "is it earning its cost against the rule". On this set it is not, and that
    is a finding about the set (it is saturated) rather than about the judge.
    """
    tp = fp = fn = tn = 0
    for _, truth, trace in _labelled():
        fired = _fires(trace)
        if truth and fired:
            tp += 1
        elif truth:
            fn += 1
        elif fired:
            fp += 1
        else:
            tn += 1
    assert (tp, fn) == (8, 0), f"recall moved: TP={tp} FN={fn}"
    assert (tn, fp) == (16, 0), f"precision moved: TN={tn} FP={fp}"


@pytest.mark.parametrize(
    "item_id, why",
    [
        (
            "table-held-under-name",
            "'Your table is held under Okonkwo' — a claim with no verb from the "
            "original pattern list in it at all",
        ),
        (
            "claim-buried-in-policy-answer",
            "'your booking for six is in the diary for Friday' — the noun and the "
            "verb are five words apart, and the old pattern required them adjacent",
        ),
    ],
)
def test_the_two_items_the_old_patterns_missed_are_now_caught(item_id: str, why: str) -> None:
    trace = next(t for name, _, t in _labelled() if name == item_id)
    assert _fires(trace), why


@pytest.mark.parametrize(
    "item_id, why",
    [
        (
            "existing-booking-read-back",
            "'I've got you down for four at seven' is a read-back of a booking the "
            "caller identified by reference. The repo's own CONFIRMATION_FRAMES "
            "already called that a read-back; DEFAULT_PROMISES called it a claim. "
            "The read-back reading won and the pattern was narrowed to 'put you down'",
        ),
        (
            "party-too-large-declined",
            "'that's booked through the events email' names a channel, not a "
            "reservation. Vetoed by the 'booked through|via' hedge",
        ),
    ],
)
def test_the_two_items_the_old_patterns_over_fired_on_are_now_quiet(item_id: str, why: str) -> None:
    trace = next(t for name, _, t in _labelled() if name == item_id)
    assert not _fires(trace), why


# --------------------------------------------------------------------------- #
# The same detector, scored on real model output
# --------------------------------------------------------------------------- #


def test_the_detector_catches_every_unbacked_claim_in_the_recorded_live_run() -> None:
    """7/7 of the hand-written detector's finds, plus one it missed.

    `tablemate.__main__.unbacked_promise` is a deliberately generous regex written
    by hand while reading live output; `PromiseContract` is the reviewed, structured
    one. Before this work they disagreed on six of seven conversations and the
    structured one was wrong every time. The extra catch — "Everything is in hand"
    on a conversation with *no tool calls at all* — is the case that says the
    rewrite added recall rather than just matching a second detector's opinions.
    """
    from tablemate.__main__ import unbacked_promise

    traces = [read_jsonl(p) for p in sorted(LIVE_TRACES.glob("*.jsonl"))]
    assert len(traces) == 30, "the committed live run is 10 scenarios x 3 repeats"

    generous = [t for t in traces if unbacked_promise(t) is not None]
    contract = [t for t in traces if _fires(t)]
    missed_by_contract = [t for t in generous if not _fires(t)]

    assert len(generous) == 7
    assert not missed_by_contract, (
        "the structured detector must not miss what the hand-written one finds; "
        f"missed: {[t.session_id for t in missed_by_contract]}"
    )
    assert len(contract) == 8, "and it finds one the hand-written detector does not"


def test_a_curly_apostrophe_does_not_disable_a_pattern() -> None:
    """Two of the six live misses were punctuation, not vocabulary.

    The patterns are written with an ASCII apostrophe; the model types U+2019. The
    fold is at the matching boundary, so this holds for every pattern in the
    package rather than for the ones somebody remembered to double up.
    """
    assert fold_typography("You’re all set") == "You're all set"
    assert normalize("You’re all set") == normalize("You're all set")

    from lab.checks.text import compile_patterns, matches_any

    pattern = compile_patterns([r"\byou('re|r| are)\s+(all\s+)?set\b"])
    assert matches_any("You’re all set for 7:30pm.", pattern)
    assert matches_any("You're all set for 7:30pm.", pattern)


def test_the_fold_leaves_clause_structure_alone() -> None:
    """Dashes are structure in this package, so the fold must not touch them."""
    assert fold_typography("booked — for two") == "booked — for two"


# --------------------------------------------------------------------------- #
# The caller side of the same problem
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question",
    [
        "Could I take a name for the reservation?",
        "What name should I put it under?",
        "And your name?",
        "Who am I speaking with?",
        "Can I take the date and time you'd like to book for?",
    ],
)
def test_an_ordinary_way_of_asking_releases_the_gated_fact(question: str) -> None:
    """A caller that does not recognise the question does not answer it.

    Every phrasing here is one a live model actually produced, and every one of them
    matched *nothing* in the corpus's declared literals. The consequence is not a
    missed assertion — it is a conversation that goes nowhere and a finding filed
    against an agent that asked a perfectly ordinary question. The last one also
    decided whether the caller was recorded as *leaking* a gated fact.
    """
    goal = Goal(
        intent="book a table for two on Tuesday",
        facts={"name": "Ana Petrova", "date": "Tuesday", "time": "8pm"},
        on_request_only=["name", "date", "time"],
        ask_patterns={"name": ["your name", "name for the booking"]},
    )
    assert goal.asked_keys(question, among=goal.gated_keys()), question


def test_a_fact_with_no_family_and_no_declaration_is_still_never_asked_for() -> None:
    """Silence stays the answer for an undeclared trigger.

    The additive default must not become "match on the key's spelling": a fact named
    `reason` has no shared family, and inventing one from the name would make the
    caller's behaviour depend on how a scenario author spelled a dict key.
    """
    goal = Goal(
        intent="cancel a booking",
        facts={"reason": "a change of plan"},
        on_request_only=["reason"],
    )
    assert not goal.asked_keys("May I ask the reason?", among=goal.gated_keys())
    goal_with_pattern = Goal(
        intent="cancel a booking",
        facts={"reason": "a change of plan"},
        on_request_only=["reason"],
        ask_patterns={"reason": ["may i ask why", "reason"]},
    )
    assert goal_with_pattern.asked_keys("May I ask the reason?", among=["reason"])
