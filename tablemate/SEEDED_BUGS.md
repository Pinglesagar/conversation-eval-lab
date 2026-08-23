# Seeded bugs — the answer key

**Read the case study first.** This file is the answer key to it. Everything in
`../README.md`, in `scenarios/`, and in every report the suite produces can be read
without it, which is the point: an eval suite that only finds the bugs its author
pointed it at has demonstrated nothing. TableMate carries three planted defects,
they are documented here and nowhere else, and nothing in `lab/` knows they exist.

They are real code paths, not switches. There is no `if BUG_1_ENABLED`, no random
seed, no injected fault. Each one is a plausible mistake made in a plausible place,
reachable by ordinary conversation, and identical on every machine — which is what
lets a recorded fixture stand as evidence.

## The shape of all three

TableMate is a greeter routing to three specialists (`tablemate/agents.py`). A
specialist does not share the orchestrator's memory: it is *briefed* with a
projection of the conversation record onto the fields it declares an interest in
(`AgentSpec.inbound`), and that brief becomes the record while it holds the turn.
Alongside the record, the orchestrator keeps its own notebook (`Session`) — what it
has already asked, what it has searched for, whether a booking has been claimed —
which is *not* part of the brief and therefore survives every handoff.

That split is where all three defects live. Two of them are the record and the
notebook disagreeing; the third is the notebook believing a booking exists.

None of them raise. None of them produce a tool error. All three leave a transcript
that reads as a competent, courteous call. That is the property that makes them
worth planting: they are only visible if you check the words against the actions,
or the actions against the whole conversation.

---

## BUG-1 — phantom confirmation on a party of six

**Where** `tablemate/agents.py`, `BookingAgent._commit`, the branch guarded by
`size >= LARGE_PARTY_THRESHOLD`.

**What happens** For a party of six or more, the agent tells the caller *"That is
all booked in"* and explains the private room, the deposit and the pre-order — and
never calls `create_booking`. No booking exists. No reference is given, and no
reference is asked for, because the group-booking patter accounts for its own
absence ("the events team sends those out"). The caller hangs up believing they have
a table for eight on Saturday.

**Why it is plausible** The group path was added later, for a real reason: a party
that size is a private-room booking with a deposit and a pre-order, and that flow
belongs to the events team. Whoever wrote the branch wrote the words the caller
needed to hear and left the commit to the flow that was going to replace it.
Reviewing this function, the branch looks *fuller* than the one below it, not
emptier.

**Reachable by** Asking for six or more covers, on any date, with any name. There
is no other condition.

**What should find it** The decision-versus-action check: `PromiseContract`. A
spoken commitment in the perfect or present-stative ("is booked in") requires
`create_booking` somewhere in the session, and there is none. `ToolContract` finds
the same defect from the other side, as a missing expected call.

**What must not find it** Anything reading only the transcript. The words are
fluent, warm, internally consistent and specific. Any judge, human or model, that
is shown the conversation and not the tool ledger should score this call a success —
and if a text-only judge *does* flag it, that is worth understanding, because on
this trace it cannot have flagged it for the right reason.

**The boundary is the evidence** `happy-party-of-five-boundary` and
`edge-large-party-of-six` differ in exactly one digit. Five books; six does not. A
suite that reports a difference between those two rows has localised the defect to
the threshold without anybody telling it where to look.

**The one-line fix** Call `create_booking` in the group branch. The confirmation
text is fine.

---

## BUG-2 — the amendment desk asks what it already knows

**Where** `tablemate/agents.py`, `ModificationAgent._amend`, the block guarded by
`session.headcount_checked`.

**What happens** Every amendment begins with *"How many people will be dining?"* —
asked as a fresh question, unconditionally, however recently the caller said it. The
change is then applied correctly. Nothing fails; the call is just one round trip
longer than it needed to be, and the caller has repeated themselves for no reason.

**Why it is plausible** The reasoning in the code is sound as far as it goes. Moving
a booking may mean re-seating the party, re-seating needs the head count, and the
head count is not part of the *change request* — "can we move it to half seven"
says nothing about how many people are coming. So the amendment flow establishes it.
The mistake is that it establishes it from the caller instead of from the brief it
was handed, which already has it. Two sources of truth for one fact, and the code
consults the wrong one.

**Reachable by** Any amendment: a booking made earlier in the same call and then
moved, or an existing reference amended from the start. Not by a cancellation — a
cancellation needs no re-seat, so the ask never fires, which is why
`happy-cancel-then-rebook` passes.

**What should find it** `NoReAskContract`, which quotes the caller's original answer
*and* the later question, and names `ModificationAgent` as the asker.

**Why it is harder to detect than it looks** The fix is not "never ask about party
size after a handoff". A careful agent *should* confirm a head count before moving a
table — *"still four of you?"* — and a detector that flags any interrogative
mentioning the party size fires on that too, gets called noisy, and gets switched
off. The distinction the check draws is the one that matters: an ask requests
information it does not state, a confirmation states what it is checking. This code
asks. Change the line to *"still four of you?"* and the same check passes, correctly.

**Cost, in the scenario that measures it** `edge-modification-after-booking` uses
the `distracted_parent` persona, whose cooperativeness sits below the reluctance
threshold, so every question costs two turns rather than one. The transcript makes
this look like one wasted exchange. For that caller it is two.

**The one-line fix** Read the head count from the brief; ask only when the brief
does not have it.

---

## BUG-3 — the dietary note falls out of the record at the policy desk

**Where** `tablemate/agents.py`, `SPECS[POLICY].inbound`, in combination with
`Orchestrator.turn`'s single `project(...)` call.

**What happens** The caller states a dietary requirement — a severe peanut allergy,
a coeliac daughter — and then asks a question about the restaurant. The policy desk
takes the turn. Its brief covers the shape of the booking (party size, date, time,
name, reference, topic) and not the caller's free text, so `dietary` and `notes` are
not in the projection, and the brief *is* the record from that moment on. The
question is answered well. Control returns to the booking desk, which books the
table with `notes=""`. The allergy is not on the booking, the kitchen never hears
about it, and nobody is told anything untrue.

**Why it is plausible** Narrow briefs are good practice and are chosen deliberately:
a short prompt, and a sub-agent that cannot act on data it was never given. The
policy desk genuinely does not need to know about an allergy in order to answer a
question about parking. Every line of the projection is defensible; the failure is
in the composition — that the projection is *destructive*, and that a desk with a
narrow brief sits on the path back to the desk that needs the wide one.

**Why it leaves no trace in the transcript** The dietary prompt is a courtesy
question the orchestrator asks once, and the notebook — which survives the handoff —
records that it has been dealt with. So the booking desk does not ask about
allergies again. If it did, the caller would notice, repeat themselves, and the note
would be recovered; the bug would be an annoyance rather than a silent data loss.
The bookkeeping that makes the system feel attentive is what makes this defect
invisible. This is the one to read twice: the *reason* nothing looks wrong is a
feature working as designed.

**Reachable by** Any conversation where a note is given before a policy question and
the booking is committed after it. The order matters and nothing else does.

**What should find it** `FieldPropagationContract` with `require_handoff=True`,
which quotes the caller's supplying utterance, every handoff the value had to
survive, and the `create_booking` arguments that do not carry it.

**The control that makes it evidence** `happy-dietary-note-single-agent` is the same
allergy, the same booking, no policy question — and the note arrives. So the finding
is not "this agent loses dietary requirements", which would be a guess about the
model. It is "this agent loses dietary requirements *across a handoff to the policy
desk*", which is a statement about a boundary, and it names the line to change.

**The one-line fix** Add the note fields to the policy desk's brief — or, better,
stop the projection being destructive and let a specialist read a narrow brief
without narrowing the record.

---

## What the suite should *not* find

Deliberately absent, so that a suite reporting them is over-firing rather than
thorough:

- **No wrong values.** Nothing here writes a party of four into a booking for two,
  or transposes a date. Every value that arrives is correct; the failures are
  omissions.
- **No tool errors on the seeded paths.** All three defects run green at the tool
  layer. `search_tables` and `create_booking` succeed whenever they are called.
- **No non-determinism.** No `random`, no wall clock, no network in the decision
  path. The same conversation produces byte-identical traces, which is why
  `pass^k` on these rows should report STABLE_FAIL and never FLAKY. A FLAKY verdict
  on a seeded row is a bug in the harness, not in TableMate.
- **No fourth bug.** Anything else the suite reports is either a real emergent
  defect worth writing up on its merits, or a false positive worth fixing in the
  check. Both are more interesting than the three above; neither is planted here.

## Where each one is exercised

| Bug | Fires in | Controls that must stay green |
|---|---|---|
| BUG-1 | `edge-large-party-of-six`, `edge-large-party-eight-with-note` | `happy-party-of-five-boundary` |
| BUG-2 | `edge-modification-after-booking`, `edge-modify-party-size-upward` | `happy-cancel-then-rebook`, `happy-move-booking-later` |
| BUG-3 | `edge-dietary-then-policy-detour`, `edge-coeliac-then-menu-policy` | `happy-dietary-note-single-agent`, `happy-parking-question-midbooking` |

The controls are the load-bearing half of that table. A finding without one is a
description of a symptom; a finding with one names a boundary.
