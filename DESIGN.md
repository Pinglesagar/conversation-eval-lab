# Design

Ten principles, and why each one is here rather than the obvious alternative.
Every one of them is enforced somewhere in the code, and the enforcement point is
named — a principle a repository cannot break is a principle; one it merely
believes in is a preference.

> **One note before you start.** Sections 9 and 10 are the record of a study run
> against a second system under test that has since been removed from this
> repository, so their worked examples name a subject you will not find in the
> tree. They are kept because what the study *produced* is still here and still
> load-bearing — `fold_typography()` in `lab/checks/text.py`, `DEFAULT_PROMISES`
> in `lab/checks/`, and the `strict_because` rule the corpus loader enforces.
> Rewriting the history to match the current subject would make it a better read
> and a worse record.

---

## 1. The trace is the product; everything else is a function of it

One JSONL file per session, one event per line, timestamps monotonic from session
start (`lab/trace/schema.py`, reference in [`docs/trace_schema.md`](docs/trace_schema.md)).
Every check, judge, latency figure and report row in this repository reads traces
and nothing else.

**The alternative** is what most harnesses do: assertions that run inside the loop
that drives the agent, against objects that exist only while it runs. That is
faster to write and it makes three things impossible. You cannot re-check last
week's run against this week's check. You cannot hand a failure to somebody as a
file and have them reach the same verdict. And you cannot swap the adapter — text
today, audio tomorrow — without rewriting the assertions, because the assertions
are coupled to the driver rather than to the conversation.

The test of whether this is real is `evallab replay`: contracts over committed
JSONL, no agent, no runner, no clock, same verdicts. If a number in a report
cannot be recomputed from the file on disk, it was never evidence.

**Consequences that follow whether you like them or not.** Event ids and
timestamps have to be deterministic or committed traces churn on every run
(`session_id` is passed in rather than minted from uuid4; correlation ids come
from the toolbox's own counter). Paths written into a report have to be
repo-relative or the artefact is machine-specific — there is a test for that
(`test_the_committed_report_holds_no_absolute_paths`).

## 2. Adapters are interchangeable; the engine is vendor-agnostic

`lab.simulator.AgentUnderTest` is a callable taking an utterance and returning a
turn. `--agent-factory pkg.mod:factory` points the whole harness at something
else. `lab` never imports the case study: `lab/cli.py` resolves the corpus loader
lazily, by dotted path, so `import lab` does not pull in `roleplay` or
`scenarios`, and the seam that will become a plugin point when `lab` moves to its
own repository is already the seam the defaults sit behind.

The same argument applies one level down. Provider access goes through litellm,
imported inside the functions that need it, and every live path is opt-in behind
an environment variable with a recorded fixture standing in for it. The reason is
not portability for its own sake: it is that a harness which can only measure one
vendor's model cannot answer the question people actually ask, which is whether
to switch.

## 3. Every rate carries its denominator

`3/4`, never `75%` alone. `Rate` in `lab/report/report.py` stores the numerator
and the denominator and refuses to be constructed with a numerator larger than
its denominator; `CheckStat.rate` returns a string like `"3/4"`; the run report
prints "Every rate below is printed as `n/N (percent)`. A percentage without its
denominator is a defect, not a style choice."

**Because the denominator is where the lie lives.** "TPR 100%" over four labelled
positives and over sixty are different claims and identical strings. A contract
that "passed" on zero applicable runs is not passing. This is the cheapest
discipline in the repository and the one that catches the most nonsense.

## 4. Absence is a first-class result, and it is not a pass

A check that ran but had nothing to assert on is `VACUOUS`, counted separately
from `PASS` (`lab/checks/result.py`). A contract that was vacuous everywhere is
reported as a gap, not as green (`RunReport.integrity_gaps`). The committed report
does exactly this for `no-progress-loop` and `propagation:seating`.

**Because this is how eval suites rot.** The scenarios drift, half the contracts
stop applying, the dashboard stays green, and nobody can tell the difference
between "we check this and it is fine" and "we stopped checking this". The
reference run's own integrity section says which of its contracts asserted
nothing, which costs the report some polish and buys the only property that
matters: its claims are the size of its evidence.

## 5. Mocks must replicate side-effects, or skip-logic is untestable

The register is real state: a set of disclosures required per jurisdiction and
language, and a ledger of the ones actually discharged, written turn by turn as
`record_disclosure` events (`roleplay/register.py`). The customer is real state
too — concerns not yet revealed, objections raised and not yet resolved, a
resistance level that moves (`roleplay/persona.py`).

**The alternative** — stubbing the register to return "disclosure given" — tests
the stub. What the grader does next depends on the *shape* of a real ledger: which
codes were discharged, in which language, on which turn, and which are still
outstanding. Stub that and every branch downstream is measuring your mock's
imagination. The same rule is why "was the disclosure actually given" is answered
by reading the ledger, not by checking whether the word appeared in the
transcript — which is exactly the defect the shipped rubric has.

## 6. Failures are classified before they are believed

Four classes: **product** (the system under test is wrong), **harness** (the
driver, the caller model or the trace is wrong), **label** (the check or the
scenario that declares it is wrong), **variance** (the same input behaved
differently between repeats).

**Because a red row is not a defect, it is a disagreement**, and the cheapest
possible source of that disagreement is your own check. The corpus makes that
explicit: 38 of the 70 behavioural rows carry an `expected_failure`, so a red row
there is the row working. `make roleplay-demo` prints both counts side by side —
"human verdicts: 38 pass, 32 fail (70 rows)" and "32/70 traces passed every
applicable check" — and a reader who sees only the second number has been given
the wrong impression on purpose by nobody.

The classification is human work by definition, so it stays out of the report. The
report says what failed and quotes the evidence; it makes no claim about why. The
two places a human judgement is written down instead are
`roleplay/SEEDED_DEFECTS.md`, which is the answer key, and the `notes:` field every
scenario carries.

## 7. Stability is a dimension of the verdict, not a footnote

`lab/simulator/passk.py`: a scenario is `STABLE_PASS` only if every repeat passed —
where a repeat "passed" means nothing failed that the corpus had not already
declared as a known gap, so `STABLE_PASS` reads *no undeclared failure* and not
*every check passed* (§9 is why the verdict is drawn that way, and the report's
own stability section says so above the table).
`FLAKY` is not a pass, and `StabilityVerdict.passed` is True for `STABLE_PASS`
alone so no downstream aggregation can round a flaky scenario green.
`StabilitySummary` refuses to average pass rates across scenarios — two flaky
rows would average into one healthy-looking number — and offers only counts per
verdict class.

And k is reported with what it can support. Under `--replay` the caller is
scripted and the agent's phrasing comes from a fixture, so k repeats measure
*harness determinism*, not model variance. Calling that a variance measurement
would be exactly the kind of claim this repository exists to avoid, so the run
verifies it instead rather than claiming it, and the report says what k bought.

## 8. A judge without calibration is not evidence

`JudgeSummary` in `lab/report/report.py` cannot be constructed without a
`JudgeCalibration`: the type system refuses to render a model-graded verdict that
has never been compared with a human label. `lab/judges/registry.py` gates on TPR
**and** TNR separately (≥ 0.85 each, n ≥ 10, no parse errors), raises in CI, and
its only override is ugly on purpose — it must be written at the call site and it
logs a warning, because an override that can be set from a config file becomes
permanent within a month and nobody remembers turning it on.

Gating on two rates rather than one score is the load-bearing part: a detector
with 0.99 TNR and 0.20 TPR has a respectable average and misses four fifths of
the defects. The worked example is in
`lab/judges/hallucinated_confirmation/` — a naive prompt at TNR 10/16, a rewrite
at 15/16, the same 24 labels, the delta table generated rather than typed, and the
surviving false positive left in place because a prompt tuned until its own
calibration set comes back clean has been fitted to that set.

Offline, that judge **abstains** rather than guessing. Its recorded verdicts are
keyed to the prompts of its calibration items; there is no recording for a trace
it has never seen, and inventing one would put fabricated verdicts in a report.
So the run selects the sessions the cascade would grade (13/47, the ones where no
booking mutation succeeded), records an abstention on all of them, and prints the
abstention rate next to the measured TPR and TNR. An abstention is visible; a
guess is not.

## 9. A gate answers "did anything change", not "is it correct"

The system under test has known defects, so "did every check pass" is a question
whose answer is already known and useless as a build gate. Two verdicts, printed
side by side, neither derived from the other:

```
report verdict:   FAIL — the product's own state
regression gate:  PASS — 0 new, 0 vanished, 0 stale expectations
```

The gate fails on a finding that is new, on a finding that has **disappeared**, on
a corpus `expected_failure` that stopped reproducing, and on a scenario whose
repeats were not identical.

The two middle cases are the ones people leave out, and they are the same case:
from outside, a fixed defect and a check that quietly stopped applying are
indistinguishable — one fewer failure. So a fix fails the gate until the baseline
is updated in the same change, which forces somebody to say in a reviewable diff
which of the two happened. Regenerating the baseline prints the diff, and that diff is the record of what
the suite learned.

---

## 10. A literal in a check is a check that works once

Written after running the whole corpus against a real model for the first time.

Every deterministic check in this repository asks "does this sentence count as an
instance of the thing I am looking for", and every one of them answers with a
pattern. Against the scripted build that is exact and free: the agent says *"That
is all booked in"* every time, so a check written around that string is correct
forever. Against a model it is close to worthless, and the size of "close to" was
measured rather than guessed:

| detector | scripted build | recorded live run |
| --- | --- | --- |
| `PromiseContract`, before this work | fires on every seeded case | **1 of 7** unbacked confirmations |
| `PromiseContract`, after | unchanged | **7 of 7**, plus one nobody else found |

The seven were not exotic phrasings. Four were ordinary synonyms — *"the room is
yours"*, *"everything is in hand"*, *"your booking is all set"*. **Two were not a
vocabulary problem at all: the pattern was right and the punctuation was wrong.**
The patterns are written with an ASCII apostrophe and the model types U+2019, so
`you('re| are)` never matched *"You're all set"*. Nothing about that failure is
visible in a report: the contract passes, the trace looks clean, and the defect is
in the character set of the pattern language.

Four rules came out of it, and they are the ones worth transferring:

1.  **Fold at the matching boundary, not in every pattern.** One
    `fold_typography()` on the haystack fixes every pattern in the package at once.
    Doubling an alternation in each regex fixes the ones somebody remembered.
2.  **Declare the idea once and reference it.** Fourteen scenarios each carried a
    hand-typed list of literals for "told the caller a booking exists" — fourteen
    separate guesses at one idea. They are now one named family, *derived from*
    `DEFAULT_PROMISES`, so the check that asks whether a claim was backed and the
    row that asks whether it should have been made agree by construction.
3.  **A broad pattern needs a veto, and it needs the right one.** A family broad
    enough to catch the paraphrase catches the correct *refusal* too — "I'm afraid
    I can't do anything on the house" — and, as the first live batch showed, the
    agent merely *naming* the caller's request. Hence clause scope with two veto
    lists, and hence two of them rather than reusing the promise contract's
    hedges: "I'll comp your meal" must be vetoed as intent by one check and caught
    as a concession by the other.
4.  **Keep the literals that are literal, and say why in the file.** Four blocks
    in this corpus stay exact: three customers' surnames, their booking
    references, a PA's surname, and five underscored tool identifiers. A surname
    has no paraphrase, and a refusal that names another customer has still named
    them. Each carries a `strict_because` line, and a test fails on a literal list
    that has neither a reason nor a family.

The same problem has a caller-side face that is worse, because it is silent.
Twenty-six rows gated a fact behind literals like `"your name"`; a live agent asks
*"Could I take a name for the reservation?"*; the caller does not recognise the
question, never releases the fact, the conversation goes nowhere, and the finding
is filed **against the agent**. Nothing errors. `Goal.is_asked_for` now unions the
row's patterns with the same shared families the contracts use, which is the same
rule as (2): one definition of "the agent asked for this", shared by the side that
answers and the side that scores.

---

## Two things this design costs

**Committed artefacts are a maintenance burden.** The two spoken calls, their
manifests and traces and score cards, the calibration reports and the judge
recordings are all in the repository, and all of them can go stale. That is paid
for with tests and with the gate: stage 3 of `make gate` runs the calibration and
then `git diff --exit-code` over `fixtures/` and `lab/judges/`, so an artefact that
no longer matches what produced it fails the build rather than sitting there
looking authoritative.

**The customer is part of the instrument, and the default one is scripted.** The
persona decides its moves deterministically and a separate object puts them into
words, so a run is reproducible and a finding is attributable to the grader rather
than to the customer. A model-driven customer is the more realistic instrument and
the worse one to measure with: its variance lands in the results as the system's
variance, and the pass^k machinery then reports the customer's flakiness as the
system's. The cost is that each row exercises one phrasing, so the corpus
under-samples the space of ways people say things — which is precisely how the
punctuation defect stayed invisible until somebody read a transcript and probed
the classifier by hand. The live seats exist for that exploration, behind
`LAB_LIVE_CUSTOMER` and `LAB_LIVE_TRAINEE`, against cassettes that key each turn on
a hash of the conversation so far, so a stale fixture raises instead of answering a
question that was never asked.
