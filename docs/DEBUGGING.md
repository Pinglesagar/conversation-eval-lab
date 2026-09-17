# Debugging — what to do when a row goes red

Every failure on this page is real. The worked example in §2 was induced on purpose,
by deleting one line from `roleplay/register.py`, and every line of output below was
copied from the run that followed. Nothing here is illustrative.

The order of the page is the order of the work: read the exit code, read the
evidence the check already printed, read the conversation, then — last — open the
code.

---

## 1. First: read the exit code

The exit codes are load-bearing and they mean different things.

| exit | meaning |
| --- | --- |
| **0** | the thing the command gates on held |
| **1** | the gate failed, or the command ran and the answer was no — a judge below threshold, a corpus that did not load |
| **2** | a check failed that no scenario declared. `make roleplay-demo` uses this for *undeclared* failures specifically |

**A report that says FAIL is not the same as a red build.** The system under test
carries three deliberately seeded defects, so a healthy tree produces a great many
failing rows. `make roleplay-demo` prints both numbers side by side:

```
  human verdicts: 38 pass, 32 fail (70 rows)
  rows with a declared expected failure: 38
```

38 of the 70 rows exist *in order to fail*. The build is red only when something
fails that no row declared, and those lines are marked:

```
  ! locale-eu-three-disclosures-in-one-turn: tools failed and no expected_failure declares it
```

If you are staring at a page of failures and a green build, that is why, and it is
deliberate. A suite in which everything passes is a suite that is not trying.

---

## 2. A check failed — how do I find out why?

The worked example: deleting the registered phrasing `"annual management charge"`
from `REGISTERED_PHRASINGS["en"]["fees_and_charges"]` in `roleplay/register.py` — a
plausible tidy-up, one line, no test in the file itself objects.

### Step 1 — the run names the rows and the contract

```console
$ make roleplay-demo
  ! pitch-exemplary-eu-retail-run: tools failed and no expected_failure declares it
  ! compliance-guaranteed-return-caught: tools failed and no expected_failure declares it
  ! objection-aggressive-fee-challenge: tools failed and no expected_failure declares it
  ! locale-eu-three-disclosures-in-one-turn: tools failed and no expected_failure declares it
  ... 39 rows
make: *** [roleplay-demo] Error 1
```

**Thirty-nine rows, one contract.** That shape is the first piece of diagnosis: one
contract failing across many rows is one cause, not thirty-nine bugs. Two *different*
contracts failing on the same rows would be one cause seen from two sides. Many
contracts failing on many rows is usually the harness.

### Step 2 — the output already carries the evidence

Do not open the code yet. The failing row prints what the contract was looking at:

```
  pitch-ask-before-the-cost-disclosure           human=pass scorer=pass (18/20) agrees  checks 3/4 pass, 1 fail, 0 vacuous
      [UNDECLARED] tools: 6/7 tool clauses satisfied -- record_disclosure called 2x, minimum 3
            --  [absence] record_disclosure called 2x   <- minimum is 3
```

That is the diagnosis before an editor is open: three disclosures were required, two
were recorded, and the contract is reporting an **absence** — not a wrong value, a
missing event. `[absence]` is its own evidence kind for exactly this reason: "the
thing that should have happened did not" needs different evidence from "the thing
that happened was wrong", and a check that cannot express the first one will pass a
silent failure.

### Step 3 — read what the *score* said about the same session

This is the step that pays for the whole design, and it is worth doing every time:

```
t=6.600s [agent] score_session({..., "criteria": {..., "mandatory_disclosure": 4, ...},
                                "mandatory_disclosure_given": true, "total": 16, "verdict": "pass"})
```

**The score did not move.** Still 18/20. Still `mandatory_disclosure: 4`. Still
`mandatory_disclosure_given: true`. The grader is counting English keywords in the
transcript, and the transcript still contains the words — only the *ledger* changed.

So: the contract caught it and the rubric did not. That is not a coincidence in this
worked example; it is the finding the whole repository is built around, reproduced
here by a one-line change you can make yourself in thirty seconds.

### Step 4 — narrow to one row

`make roleplay-demo` runs all 70. To read the one row that is failing, open its YAML
— the id is the filename:

```bash
python -m roleplay.corpus --coverage --list      # every row, with its suite and tags
cat scenarios/roleplay/locale/locale-eu-three-disclosures-in-one-turn.yaml
```

For an advisory row, `python -m roleplay.regime_eval --row <id>` prints that row
alone, decided from the cited registers.

Everything above still ran the customer, the runtime and the scenario. To take all
of that out of the picture, go to §4.

---

## 3. How to read a trace

A trace is JSONL: one event per line, ordered, typed, with an injected clock. It is
the only artefact any number in this repository is computed from, so reading one is
the ground truth of every disagreement.

```bash
python -c 'import sys, json
for i, line in enumerate(open(sys.argv[1])):
    e = json.loads(line)
    print(i, round(e["ts"], 3), e["actor"], e["kind"], str(e["payload"])[:52])' \
  fixtures/audio/spoken_call/trace.jsonl
```

```
0 0.0 system session_start {'adapter': 'roleplay:spoken', 'customer_suspicion':
1 0.0 caller audio_emitted {'audio_sha256': '9f53eb3e228c241b82226ce9a76d8c68e0
2 0.0 caller transcript_in {'confidence': 1.0, 'display_text_unscored': "Good m
3 0.0 caller caller_utterance {'text': "good morning mister novak thank you for co
4 0.0 agent tool_call {'args': {'jurisdiction': 'eu-retail', 'language': '
5 0.0 system tool_result {'call_id': 'c004', 'name': 'load_customer_profile',
6 0.0 agent tool_call {'args': {'key': 'fees', 'topic': 'charges', 'turn':
7 0.0 system tool_result {'call_id': 'c006', 'name': 'raise_objection', 'ok':
8 1.106 agent agent_audio_first_byte {'turn': 1}
...
```

Four things to know while reading one:

- **Lines 2 and 3 are the whole architecture in two lines.** `transcript_in` is the
  recogniser's view and carries `display_text_unscored` — the punctuated, capitalised
  version a human would read. `caller_utterance` is the conversation's view, and it
  is what gets graded: *"good morning mister novak thank you for co…"*, no
  punctuation at all. The gap between those two lines is where the headline defect
  lives.
- **The contracts decide on event-stream *position*, never on timestamps.** Two
  events sharing a `ts` are still strictly ordered by their position in the file.
  Several events above are all at `0.0` and their order still matters.
- **The event kinds are a closed set.** `lab/trace/schema.py` holds all fifteen,
  plus the two reserved kinds nothing emits yet.
- **Every event has exactly four fields**: `ts`, `kind`, `actor`, `payload`. If you
  find yourself wanting a fifth, the thing you want is a payload key.

To recompute a verdict from a trace file and nothing else:

```bash
python -m lab.cli replay <trace.jsonl>
```

No customer, no runtime, no clock. Note it exits **0 even when it finds something**
and prints `N with unexpected findings`; add `--ci` to make it exit 1.

---

## 4. Product, harness, scenario, or stale expectation

Before believing a red, decide which of these it is. They look identical in a summary
and require opposite responses.

| you are looking at | how it looks | what to do |
| --- | --- | --- |
| **a product failure** | the committed trace passes, a fresh run fails, on the same contract | fix it, or accept it and declare it in the scenario's `expected_failure` with a written expectation |
| **a harness failure** | the row reports that the run raised, and the finding count is **0** | a stable failure with nothing behind it is the harness fingerprint. Re-run with the exception allowed to propagate |
| **a stale expectation** | a row with an `expected_failure` that did not reproduce | somebody fixed something, or a check stopped applying. Say which, in the diff |
| **variance** | some of the k repeats passed | offline this should be impossible and is a harness bug. In a live rig it is the measurement, not a defect |

### The discriminator: replay the committed trace

This is the single most useful move on the page. The committed trace was recorded
from behaviour that was correct; the contracts are the same in both cases.

```bash
python -m lab.cli replay fixtures/audio/spoken_call/trace.jsonl --ci
```

Same checker, two traces, opposite verdicts ⇒ **the product changed**. If instead the
*committed* trace has started failing, the checker changed, and it is the contract you
should be reading, not the product.

### Why "vanished" is as serious as "new"

A fix and a check that quietly stopped applying are indistinguishable from outside —
both are one fewer failure. So a finding that **disappears** is a build failure too,
until somebody writes down in a reviewable diff which of the two happened. Stage 3 of
`make gate` enforces the same rule on artefacts: `git diff --exit-code` over
`fixtures/` and `lab/judges/`, so a recalibration that moves a number has to travel in
the same commit as the change that moved it.

---

## 5. Vacuous is not a pass

The first distinction to make about a *green* row is whether the check had anything
to assert on. `make roleplay-demo` prints the count on every row:

```
  locale-es-apac-suitability-in-spanish   human=pass scorer=fail ( 8/20) DIFFERS  checks 2/3 pass, 1 fail, 1 vacuous
```

`0/0` is printed rather than `100%`, everywhere, for the same reason. If you are
surprised that a check is green, look at the vacuous count **first** — a contract
that never applied and a contract that always passed are the same colour and opposite
findings.

This is also why the corpus loader refuses to load a scenario whose `expected_failure`
names a contract the row does not declare. An expectation that can never fire is a
vacuous pass with a comment on it.

---

## 6. A byte-for-byte step failed

Stage 3 of the gate runs the calibration and then diffs the working tree:

```
== 3/6  the calibration gates, then the artefacts they wrote ==
        (this diff reads the WORKING TREE: uncommitted work of your own
         under fixtures/ or lab/judges/ shows up here.)
```

Two causes, and they need opposite responses:

1. **You changed something that feeds the artefact.** The diff is the record of what
   your change did. Read it, decide whether it is right, and commit it *with* the
   change that caused it.
2. **Somebody's work in progress is uncommitted under `fixtures/` or `lab/judges/`.**
   The diff is theirs, not yours. Run the gate on a tree that holds your change and
   nothing else.

The stage prints the warning before it runs precisely because cause 2 surprises
people once each.

---

## 7. A judge stopped clearing its gate

```
judge 'claim_support' (v1) is below the calibration thresholds
  (TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate):
  TPR 0.800 (4/5) is below the required 0.85.
  Its report lists 2 disagreement(s) to read.
```

Read the disagreements before touching the prompt. The report names each one and
which direction it went:

```
  [false_negative] c13#claim2: human=fail judge=pass
  [false_positive] probe-paraphrase: human=pass judge=fail
```

Those two errors are not equally bad, and the gate scores TPR and TNR separately
for that reason: a false negative ships the defect, a false positive wastes an
afternoon. A judge with 1.00 TNR and 0.20 TPR has a respectable average and misses
four fifths of what it exists to find.

**The trap to avoid:** tuning the prompt until the calibration set comes back clean
fits the prompt to the set. What you have then measured is the fit, not the judge. If
you change the prompt, the honest move is new labelled items the new prompt has never
seen.

`make ragcheck` reproduces the whole example above, offline, in under a second.

---

## 8. A live path refuses to run

Every live seam is off by default and refuses rather than quietly replaying:

```
LAB_LIVE_SPOKEN is not set; refusing to spend at a vendor.
```

Ten of these exist — `LAB_LIVE_SPOKEN`, `LAB_LIVE_TTS`, `LAB_LIVE_STT`,
`LAB_LIVE_TRANSPORT`, `LAB_LIVE_TRAINEE`, `LAB_LIVE_CUSTOMER`, `LAB_LIVE_JUDGE`,
`LAB_LIVE_SCORER`, `LAB_LIVE_CALLER`, `LAB_LIVE_MODEL_LABEL` — and the refusal is the
feature. A harness that silently falls back to a recording when a key is missing will
one day tell you a live run passed when no live run happened.

If a *replay* refuses, the cause is usually the opposite of what it looks like: the
recorded engine is keyed on the sha256 of the decoded audio, so if the audio changed
underneath it the lookup misses and it raises. A cassette that answers for sound it
has never heard is worth nothing, so it raises instead.

---

## 9. The corpus itself is wrong

The loader validates before anything runs, and a typo is a **load error**, not a
silent pass:

```bash
python -m roleplay.corpus --coverage --list
```

It refuses a tool name outside the closed vocabulary, a tag that is not in the tag
list, and an `expected_failure` naming a contract the row does not declare. All three
of those produce a check that can never fire, which is the most expensive kind of
wrong: it is green, forever, and it is measuring nothing.

---

## 10. Four things not to do

1. **Do not delete or weaken a check to make a build green.** If the expected
   behaviour genuinely changed, change the *expectation* — the scenario's
   `expected_failure`, or the test — and say so in the commit message. A check that
   was quietly loosened and a check that was quietly deleted are indistinguishable six
   months later.
2. **Do not re-record to make a failure go away.** Re-recording draws new samples: it
   produces a *different measurement*, not a repaired one. Read the diff as a new
   measurement, decide, and commit the decision.
3. **Do not update an artefact in a separate commit from the code that moved it.**
   The byte-for-byte gate only works if the artefact and its cause travel together.
4. **Do not read a percentage without its denominator.** Every rate in every artefact
   here is printed as `n/N (percent)`. A number that has lost its denominator on the
   way into a summary — a slide, a ticket, a message — has lost the only thing that
   said how much to believe it.
