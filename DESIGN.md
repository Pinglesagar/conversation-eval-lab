# Design

Nine principles, and why each one is here rather than the obvious alternative.
Every one of them is enforced somewhere in the code, and the enforcement point is
named — a principle a repository cannot break is a principle; one it merely
believes in is a preference.

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
and the agent factory lazily, by dotted path, so `import lab` does not pull in
`tablemate` or `scenarios`, and the seam that will become a plugin point when
`lab` moves to its own repository is already the seam the defaults sit behind.

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

The restaurant is real state: six tables, a diary, a policy sheet, availability
computed from what is actually booked (`tablemate/store.py`). A scenario that
needs a full sitting fills the sitting (`book_out`); one that names a reference
puts it in the diary (`ensure_booking`).

**The alternative** — stubbing `search_tables` to return "no availability" — tests
the stub. The agent's next move depends on the *shape* of a real refusal: which
alternatives came back, whether any table is big enough, whether the reference it
was given exists. Stub that and every branch downstream of it is measuring your
mock's imagination. The same rule is why "did the booking happen" is answered by
looking in the diary, not by checking a flag.

## 6. Failures are classified before they are believed

Four classes: **product** (the system under test is wrong), **harness** (the
driver, the caller model or the trace is wrong), **label** (the check or the
scenario that declares it is wrong), **variance** (the same input behaved
differently between repeats). `error_analysis/codes.csv` carries the class for
every coded occurrence; `error_analysis/axial_coding.md` argues the two that were
re-classified.

**Because a red row is not a defect, it is a disagreement**, and the cheapest
possible source of that disagreement is your own check. This repository's
`happy-saturday-lunch-four` row fails because a tracked value says `high chair`
and the caller said `high chairs`: the note reached the diary, the check's matcher
anchors on word boundaries, and the agent is innocent. That row is left failing,
in the committed baseline, coded `label`. A taxonomy that deletes its author's
mistakes is not a taxonomy — and if I had "fixed" it by editing the caller's
wording, the repository would contain a green row and a hidden matcher bug.

The classification is human work by definition, so it lives in
`error_analysis/`, not in the report. The report says what failed and quotes the
evidence; it makes no claim about why.

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
verifies it instead: 47/47 scenarios produced byte-identical repeats apart from
the session id, and the report says that is what k bought.

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
which of the two happened. `make reference` regenerates the baseline and prints
the diff; that diff is the record of what the suite learned.

---

## Two things this design costs

**Committed artefacts are a maintenance burden.** The reference report, 47 traces,
the calibration reports, the judge recordings and the coded failure modes are all
in the repository, and all of them can go stale. That is paid for with tests:
`tests/test_cli.py` asserts the report reloads and re-derives its own verdict,
that the hand-written error analysis agrees with the machine-written report in
both directions, and that every coded occurrence cites a trace that exists. CI
regenerates the report and fails if a byte moved.

**The caller is part of the instrument, and it is scripted.** Caller lines live in
`fixtures/caller_scripts.yaml` rather than being generated per run. A model-driven
caller is the more realistic instrument and the worse one to measure with: its
variance lands in the results as agent variance, and the pass^k machinery then
reports the caller's flakiness as the agent's. The cost is that the corpus
exercises one phrasing per row, so it under-samples the space of ways people say
things — which is precisely how two of the five findings were missed until
somebody read a transcript and probed the parser by hand. `LLMCaller` exists for
that exploration, behind `LAB_LIVE_CALLER`, with cassettes that key each turn on a
hash of the conversation so far so a stale fixture raises instead of answering a
question that was never asked.
