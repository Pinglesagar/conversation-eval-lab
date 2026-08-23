# Findings — TableMate 0.1.0

Five defects, from 47 driven scenarios and a pass through the traces by hand.
Three of them are the ones that would decide whether this system could take a
real telephone call; two more turned up while reading and are worth a ticket.

Everything below reproduces offline, with no API keys, from the committed
artefacts. Every quote is copied out of a trace file in
`fixtures/replay_run/traces/`, and every reproduction has a control — a call that
differs in one detail and behaves correctly — because a finding without a control
is a symptom, and a finding with one names a boundary.

Ordered by what it would cost the restaurant, not by how easy it was to find.

---

## Finding 1 — A party of six is told the table is booked, and it is not

**Severity: critical.** The caller hangs up believing they have a reservation.
Nothing exists in the diary, and nothing in the transcript would tell anyone.

### What happens

At six covers or more, the booking desk speaks the confirmation and never calls
the tool that would make it true.

From `fixtures/replay_run/traces/edge-large-party-of-six.jsonl`:

```
caller       Hi, can I book a table for six on Friday at 8pm?
             -> search_tables({"date": "friday", "party_size": 6, "time": "8pm"})
BookingAgent Yes, we have a table for six on Friday at 8pm free. […] And can I
             take your name for the booking?
caller       Rachel Okonkwo
BookingAgent That is all booked in — a table for six on Friday at 8pm, in the
             name of Rachel Okonkwo. For six you are in the private room, which
             is held with a deposit and a pre-order two days before. The events
             team sends those out.
```

There is no `create_booking` anywhere in the trace. The availability search is
real, the private-room explanation is plausible, and the call reads as one of the
most competent in the corpus.

### Reproduce

```bash
evallab run --scenario edge-large-party-of-six --transcript -k 1
evallab replay fixtures/replay_run/traces/edge-large-party-of-six.jsonl
```

Or against the agent directly, with the control beside it:

```python
from lab.clock import FakeClock
from tablemate.runtime import build_agent

def talk(lines):
    agent = build_agent(clock=FakeClock())
    return agent, [agent(line) for line in lines]

agent, turns = talk(["Table for six on Friday at 8pm please.", "Okonkwo."])
assert "booked in" in turns[-1].text                 # the claim is made
assert "create_booking" not in agent.tool_names()    # nothing was created

agent, _ = talk(["Table for five on Friday at 8pm please.", "Okonkwo."])
assert "create_booking" in agent.tool_names()        # the control: five books
```

Five books, six does not. The threshold is the boundary; group bookings in
general are not broken.

### What catches it

Two contracts, from opposite directions, on `edge-large-party-of-six` and
`edge-large-party-eight-with-note`:

- `tools` — `expected create_booking, never called; create_booking called 0x,
  minimum 1`
- `promise-kept` — `0/1 spoken commitments backed by the required tool call`,
  quoting the sentence above and noting `tools called: search_tables`

Neither channel alone finds it. A transcript review passes the call; a tool-ledger
review sees an absence with no way to know it mattered. The finding is the
disagreement between what was said and what was done, which is why the trace has
to carry both.

### The one place it hides

`edge-correction-during-read-back` contains the same defect and `promise-kept`
does **not** fire, because that trace has a `create_booking` in it — for an
earlier, different table. The promise is matched to a call of the right name
rather than to a call with the right arguments. That is a gap in my check, and it
is written up as code 5 in `axial_coding.md`.

---

## Finding 2 — A dietary requirement is lost when the call passes through the policy desk

**Severity: critical, and worse than it looks.** The kitchen never hears about the
allergy, and the caller has been told that the note is what protects them.

### What happens

The requirement is stated in the opening turn, discussed correctly mid-call, and
absent from the booking.

From `fixtures/replay_run/traces/edge-coeliac-then-menu-policy.jsonl`:

```
caller       Could I book a table for three on Sunday at 12:30pm? My daughter is
             coeliac.
             ~~ handoff GreeterAgent -> BookingAgent
             -> search_tables({"date": "sunday", "party_size": 3, "time": "12:30pm"})
caller       What is on the gluten-free menu?
             ~~ handoff BookingAgent -> PolicyAgent
             -> check_policy({"topic": "allergies"})
PolicyAgent  Every dish is marked for the fourteen major allergens, and the
             kitchen will cook around an allergy if it is noted on the booking.
             Shall I carry on with the booking?
caller       Edward Naylor.
             ~~ handoff PolicyAgent -> BookingAgent
             -> create_booking({"date": "sunday", "name": "Edward Naylor",
                                "notes": "", "party_size": 3, "time": "12:30pm"})
```

`"notes": ""`. The policy answer is the aggravating factor: the caller has just
been told that the kitchen will accommodate an allergy *if it is noted on the
booking*, and the note is gone.

Two mechanisms compound. The brief handed to the policy desk does not include the
dietary field, so the value does not come back across the return handoff. And the
booking desk skips its "any allergies or dietary requirements?" courtesy question
whenever it already holds a requirement — so the one question that would have
recovered the fact is suppressed by the fact it is about to lose.

### Reproduce

```python
agent, _ = talk([
    "A table for two on Friday at 7pm. One of us has a severe peanut allergy.",
    "How does the kitchen avoid cross-contamination?",
    "Ellery.",
])
booked = [c for c in agent.toolbox.calls if c.name == "create_booking"]
assert booked[0].args["notes"] == ""             # lost across the detour

agent, _ = talk([                                # the control: no detour
    "A table for two on Friday at 7pm. One of us has a severe peanut allergy.",
    "Ellery.",
])
booked = [c for c in agent.toolbox.calls if c.name == "create_booking"]
assert "peanut" in booked[0].args["notes"]       # arrives intact
```

The same allergy, the same caller, one fewer question: the note arrives. So the
system can carry free text to the diary — three other traces prove it — and the
loss is specific to the detour.

### What catches it

`FieldPropagationContract` with `require_handoff: true`, on three rows
(`edge-coeliac-then-menu-policy`, `edge-dietary-then-policy-detour`,
`edge-three-facts-one-turn`), each printing the whole chain:

```
0/1 values reached create_booking.notes: 'coeliac' = 'coeliac' was supplied at
t=0.000s and lost across 3 handoff(s)
  t=  0.000s [caller] Could I book a table for three on Sunday at 12:30pm? My daughter is coeliac.
  t=  0.274s [system] GreeterAgent -> BookingAgent   <- the boundary the value had to survive
  …
  t=  3.310s [agent] create_booking({… "notes": "", …})   <- does not carry coeliac
```

Three allergens, three callers, three routes. One row would have been an anecdote.

---

## Finding 3 — The amendment desk asks again for something the caller has already said

**Severity: moderate on its own; it is the compounding that earns it a place.**

### What happens

The caller books a table for four, then asks to move it. The amendment desk opens
with the head count.

From `fixtures/replay_run/traces/edge-modification-after-booking.jsonl`:

```
caller            I would like to book a table for four on Wednesday at 7pm.
…
BookingAgent      That is all booked in — a table for four on Wednesday at 7pm,
                  in the name of Ruth Kelleher, reference TM-2001.
caller            Now could you move that booking to 7:30pm?
                  ~~ handoff BookingAgent -> ModificationAgent
ModificationAgent I can change TM-2001 for you. How many people will be dining?
caller            That is everything, thanks.
                  -> modify_booking({"booking_ref": "TM-2001", "changes": {…}})
```

Then the compounding. The caller's sign-off is consumed as the answer to the
unnecessary question, so the call ends with no closing turn. The re-ask is not
merely discourteous: it creates a pending slot for the next utterance to fall
into, whatever that utterance was going to be. Five traces end this way.

### Reproduce

```python
agent, turns = talk([
    "I would like a table for four on Wednesday at 7pm.",
    "Kelleher.",
    "Could you move that booking to 7:30pm?",
])
assert "How many people" in turns[-1].text
```

Controls: a cancellation never asks (`happy-cancel-with-reference`), and the
amendment itself is applied correctly once the answer arrives
(`edge-modify-party-size-upward`). So the defect is the question, not the
amendment path.

### What catches it

`NoReAskContract`, on two rows, quoting both halves and naming the speaker:

```
1/2 supplied fields were not re-asked -- party_size re-asked 1x
  t=  0.000s [caller] I would like to book a table for four on Wednesday at 7pm.   <- caller supplied party_size = '4'
```

A third trace has the same defect and stays green:
`edge-modify-across-group-threshold` re-asks the head count, but its `no_re_ask`
block tracks only `booking_ref` and `date`. The check is right and the scenario
under-declares — one line of YAML, written up as code 6 in `axial_coding.md`.

---

## Also found

Two defects that were not what I was looking for. Both came out of reading a
transcript and then probing the parser directly.

### Finding 4 — "No dairy for one of us" reaches nobody

**Severity: high.** A requirement phrased in the negative is discarded by the
heuristic that exists to ignore "no allergies, thanks".

In `fixtures/replay_run/traces/adversarial-injection-in-dietary-note.jsonl`,
`create_booking` goes out with `"notes": ""` after the caller says "No dairy for
one of us". The control is the same requirement, phrased positively:

```python
agent, _ = talk(["A table for two on Friday at 6:30pm. No dairy for one of us.", "Raman."])
assert [c.args["notes"] for c in agent.toolbox.calls if c.name == "create_booking"] == [""]

agent, _ = talk(["A table for two on Friday at 6:30pm. One of us cannot have dairy.", "Raman."])
assert "dairy" in [c.args["notes"] for c in agent.toolbox.calls if c.name == "create_booking"][0]
```

"No dairy" is how people say this. Caught by `propagation:dairy` on that row.

There is a second defect in the same sentence that nothing catches: "for one of
us" parses as a party size of 1. It is masked here because the earlier clause has
already set 2, so no verdict changes and no evidence appears anywhere in the
trace. Reorder the clauses and it is a booking for the wrong number of people.

### Finding 5 — A change is not parsed, and the caller is told there was nothing to change

**Severity: high, because the failure is reassuring.**

```python
agent = build_agent(clock=FakeClock())
agent("Could you change my booking TM-1042 — we are now six rather than four, "
      "still Saturday at 8pm.")
reply = agent("That is all, thank you.")
# -> modify_booking({"booking_ref": "TM-1042", "changes": {"date": "saturday", "time": "8pm"}})
# "Looking at TM-1042, everything you have given me is already what we hold, so
#  there was nothing to change."
```

The diary still says four. The caller has asked to add two people and been told,
in effect, that the diary already agrees with them.

The mechanism is narrow, and worth quoting exactly because it shows how thin the
line is:

```python
from tablemate.understanding import extract_slots
extract_slots("six of us now, not four")          # {'party_size': 6}
extract_slots("we are now six rather than four")  # {}
```

**Nothing in the suite fails this row.** Its tool contract requires
`modify_booking` to be called with `changes` *present*, and `changes` was present
— it just did not contain the change. That is the most valuable thing in this
document: a green row, a satisfied caller, and a diary that is wrong. The fix to
the suite is an argument predicate naming the field the caller asked to change;
the fix to the product is parser coverage, plus a refusal to report "no change"
when a change was requested.

---

## Scoreboard

| | count |
| --- | --- |
| scenarios driven | 47/55 (8 voice rows need the audio path) |
| scenarios where every check passed | 44/47 |
| findings in the committed report | 12 (scenario × contract) |
| distinct failure modes coded by hand | 13 |
| coded occurrences | 32 |
| product occurrences a contract caught | 9/31 |

The last line is the honest headline. The declarative checks caught 9 of the 31
product occurrences that reading the traces found. That is not a complaint about
the checks — the ones that fired did so with quotable evidence and needed no
investigation — it is the reason the loop exists: a suite tells you about the
failures you have already learned to describe, and reading traces is how the next
set gets described. `axial_coding.md` lists what each miss needs, and three of
them are one line of scenario YAML.
