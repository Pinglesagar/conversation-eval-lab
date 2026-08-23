# Retargeting the harness: a BFSI sales-roleplay coach

This document is the tour of `roleplay/` — a second system under test, in a
domain that shares nothing with the restaurant-booking case study except the
framework underneath it.

**The claim being tested is about `lab/`, not about the new domain.** An eval
framework earns its name by surviving a change of subject, and the only way to
show that is to change the subject. So: a different product, different actors,
different tools, a different regulator, a different failure taxonomy — and
`lab/` imported without one line of modification. `roleplay/` depends on `lab/`;
`lab/` has never heard of `roleplay/`.

```
make roleplay-validate      # the corpus against its schema, with coverage
make roleplay-demo          # everything: contracts, consistency, calibration
```

Offline, deterministic, zero API keys. Every number in this document is copied
from a run of `make roleplay-demo` on a clean checkout.

---

## 1. What the product is, and what is under test

An AI **sales-roleplay coach** for regulated financial advice. A trainee adviser
practises a pitch against an AI customer persona; when the session ends, a
**scorer** grades the trainee against a five-criterion rubric and writes a page
of feedback. Both outputs are customer-facing: the number goes on a manager's
dashboard and into a certification decision, and the prose tells a salesperson
what they did.

Two systems under test, and they fail in unrelated ways:

| | module | role | seeded defects |
|---|---|---|---|
| the conversation | `roleplay/persona.py` | the AI customer: hidden concerns, an objection bank, a manner | **none** |
| the evaluation | `roleplay/scorer.py` | the rubric, the score card, the feedback | **all three** |

Keeping the persona clean is a measurement decision, not modesty. A suite that
cannot say which half broke will report "the roleplay is bad" for a year. With
the customer correct by construction and deterministic by design, every finding
below is attributable to the scorer, and the score-consistency measurement in §4
has exactly one candidate explanation instead of two.

There is a third piece, and it is the most useful one:
`roleplay/register.py` holds the **disclosure register** — the mandatory
disclosures each market requires, as structured records keyed by jurisdiction and
language, with the registered phrasings that satisfy each one. It is
deterministic code over a data table. No model is asked whether a disclosure
"basically happened".

That separation is what makes the compliance half of this domain testable at all:
the register is a *ledger*, and a stochastic scorer's claims about compliance can
be diffed against it.

### The three seeded defects are the three named risks

| risk | defect | mechanism |
|---|---|---|
| a wrong score | **score instability** | a cohort curve applied to individual scores, with state on the service |
| hallucinated feedback | **fabricated prose** | feedback assembled from rubric templates carrying their own exemplars |
| a compliance risk | **a certified breach** | compliance criteria scored on vocabulary instead of on the ledger |

They are real code paths, not switches: no flag, no injected fault, no random
seed. Each is a plausible decision made for a plausible reason, and all three
leave output that reads as competent. The answer key is
`roleplay/SEEDED_DEFECTS.md` and nowhere else.

---

## 2. What was reused, what was written, and what was left alone

The honest accounting, because "the framework retargets" is a claim that can be
checked by reading the import graph.

**Reused from `lab/`, unmodified:**

* `lab.trace` — the schema, the builder, the payload conventions. A roleplay
  session is a `Trace` of the same twelve event kinds. The trainee is the
  `caller`; the customer persona and the scorer are both `agent`, distinguished
  by the `agent` payload key; the handoff between them is an `agent_handoff`.
* `lab.checks` — the contract engine, the vacuity accounting, the evidence model.
  `ToolContract` and `PhraseContract` are compiled straight out of the YAML.
* `lab.judges` — the `Judge`, the `PromptTemplate` and its digest, the verdict
  parser, `calibrate`, `CalibrationThresholds`, and `JudgeRegistry`'s refusal.
* `lab.simulator.passk` — `PassKPolicy`, `verdict_from_outcomes`,
  `summarise_stability`.
* `lab.clock` — `FakeClock`, so every timestamp is exact and free.

**Written for this domain (`roleplay/`, ~2,000 lines):** the register, the
persona, the scorer, the adapter, two contracts, the corpus loader, the
consistency measurement, the calibration wiring, the demo.

**Deliberately *not* reused:** `lab.simulator.run_scenario`. It drives a
conversation until someone stops talking, and a roleplay session has a second
stage that loop has no notion of — after the talking ends, a scorer reads the
whole transcript. So `roleplay/runtime.py` owns the loop. That is the right place
for it: domain shape belongs in the adapter, and everything downstream of the
trace was unaffected by the change.

**Also not reused:** `scenarios/loader.py`. The roleplay corpus has its own
loader with its own closed vocabularies, because the booking loader's tool-name
set, tag vocabulary and suite list are *booking* data. Sharing the code would
have produced one loader holding two products' vocabularies. What is shared is
the four rules — see §3.

---

## 3. The corpus: 70 rows, a schema, and a human column

```
70/70 scenario files loaded; 0 error(s), 0 warning(s)
  suites:
    pitch: 21         compliance: 13     objection: 13
    consistency: 2    locale: 21
  tags: 20/20 exercised
  human verdicts: 38 pass, 32 fail (70 rows)
  rows with a declared expected failure: 38
```

Same four rules as the booking corpus: closed vocabularies; every assertion must
be *able* to fire; `expected_failure` is an expectation about the system rather
than a note; collect every problem, then report.

Two things this corpus has that the booking one cannot:

**A human column.** Every row declares `expectation.human_verdict` — pass or
fail — and a `reason`. The golden dataset and the regression suite are one
artefact rather than two that drift apart, and a row cannot be added to the suite
without somebody stating what the right answer is. That column is what §5
measures the scorer against.

**Rule 2 in its strong form.** A row's trainee script is data *in the same file*
as its assertions, so a required trainee phrase is checkable against the script
at load time. A compliance row that claims the trainee says "no real risk" and
whose script never says it is **rejected** — before it can run, pass, and be
counted as compliance coverage. That is the class of row that makes a suite green
and empty.

```
trainee_phrases.required 'no real risk' does not appear in trainee.turns, so the
row asserts a stimulus it does not contain and the check can only ever fail for
the wrong reason
```

Note the inversion, which is worth naming: in the booking corpus a phrase
contract constrains the *agent*, and constraining the simulated caller would be
checking the harness. Here the trainee **is** the stimulus, and asserting that
the offending sentence is genuinely present is what makes a green compliance
verdict a real miss rather than an empty row.

---

## 4. The centrepiece: does the same performance get the same grade?

Not "does this session pass" — that is a question about one run, and a number
that moves between runs makes it unanswerable.

`roleplay/consistency.py` runs an identical trainee script k times and reports
**two independent verdict families**:

* the binary half, from `lab.simulator.passk` unmodified: STABLE_PASS / FLAKY /
  STABLE_FAIL;
* the magnitude, which pass^k cannot express: mean, population standard
  deviation, min–max, spread against a **stated** tolerance, and the number of
  pass/fail **flips**.

And it runs two arms:

```
score consistency -- consistency-identical-transcript-warm-k5
  warm (one long-lived scorer, the production shape)
    OUTSIDE_TOLERANCE: k=5 identical runs scored [16, 15, 14, 13, 12]
    mean 14.0/20, sd 1.414, range 12-16 (spread 4 pt, tolerance 0.0 pt),
    1 pass/fail flip at threshold 14
    FLAKY — passed 3/5 (60.0%); flake rate 2/5 — NOT a pass
  cold (a fresh scorer per repeat, the control)
    WITHIN_TOLERANCE: k=5 identical runs scored [16, 16, 16, 16, 16]
    mean 16.0/20, sd 0.0, spread 0 pt, 0 flips
    STABLE_PASS — passed 5/5
  -> the cold control is flat and the warm run is not, so the instability is in
     state the scoring service holds between sessions, not in the grading of any
     one session
```

```
score consistency -- consistency-borderline-transcript-warm-k5
  warm: [14, 13, 14, 13, 14]  mean 13.6, sd 0.49, spread 1 pt, 4 flips  FLAKY
  cold: [14, 14, 14, 14, 14]  spread 0 pt, 0 flips                 STABLE_PASS
```

Three things in there are the actual content of this section.

**Spread and flips are different findings.** The first row spreads four points
and flips once. The second spreads one point and flips four times, and it is
much the worse product behaviour — a certification decision that alternates on
identical evidence. A suite that ranked instability by standard deviation alone
would put it second. Both are reported, side by side, and neither is derived from
the other.

**The control is the half that localises anything.** Without the cold arm the
finding is "scores move", which names no cause. With it, the only difference
between the two arms is whether the scoring service was reused, so the finding is
"the instability is in cross-session state" — and `pitch-cold-scorer-single-run-control`
pins the cold grade at 16 with a zero curve adjustment as a separate row. A
boundary pair whose boundary is a process lifetime rather than a threshold value.

**A stability harness that resets more than the deployment does cannot see
state-leak instability.** `lab.simulator.passk.run_pass_k` documents, correctly,
that its `run` callable must build a fresh agent per repeat — an agent carrying
state from the last repeat measures conversation history, not stability. Applied
literally here that advice *hides the defect*, because the state is held by the
service and not by the session, and a fresh service per repeat is a cold process
nobody deploys. What gets reset between repeats is a measurement decision, and it
belongs in the report next to the number it produced. That is why both arms are
run and both are printed.

The expected instability is declared **as data** on the row, not as prose:

```yaml
consistency:
  k: 5
  tolerance: 0          # a score that moves at all on identical input is a defect
  expected_spread: 4
  expected_flips: 1
```

so the day the curve is fixed, the row goes red for the right reason. The corpus
notices a repair exactly as it notices a regression.

---

## 5. How do we know the score is right? Calibrate the scorer as a judge

A rubric scorer reads a transcript and returns a verdict, and it is wrong
sometimes in ways invisible from its own output. That is the definition of an
instrument that needs calibrating. So it is measured exactly the way `lab.judges`
measures an LLM judge.

Nothing in `lab.judges` changed. The seam is `Completion` — the one-method
protocol that turns a request into raw text. `ScorerCompletion` implements it by
running the product's own scorer and returning its verdict in the JSON the
existing parser accepts. Prompt rendering, the digest that detects a changed
rubric, verdict parsing, the confusion matrix, the gate: all the same code.

```
Judge calibration: roleplay_pass_verdict v1
  model            : local:rubric-scorer
  prompt sha256    : 9dff621e69ee
  labels sha256    : 0516d042c68c
  positive class   : judge says 'fail'

                     human: fail     human: pass
     judge: fail            TP 9            FP 2
     judge: pass           FN 23           TN 36

  true positive rate (recall)      : 0.281 (9/32)
  true negative rate (specificity) : 0.947 (36/38)
  precision                        : 0.818 (9/11)
  F1                               : 0.419 (18/43)
  raw agreement                    : 0.643 (45/70)
  prevalence of 'fail'             : 0.457 (32/70)
  Cohen kappa                      : 0.241
                                     (observed 0.643, expected by chance 0.529)

calibration gate (TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%): REFUSED
  - TPR 0.281 (9/32) is below the required 0.85
  - registry refused the judge in CI mode: JudgeBelowThresholdError
```

**The product's own scorer does not clear the gate this repo applies to its own
LLM judges.** Recall on the sessions a competent reviewer would stop is 0.281
(9/32), so it certifies just under seven in ten of them. Note which way the two
rates point: specificity is 0.947 (36/38) and precision 0.818 (9/11), so the
scorer is *reluctant to fail anybody* — a grader biased towards certification is
the worst available bias for a product that certifies. The refusal is a real
refusal — `JudgeRegistry.require_calibrated` raises in CI, and a test asserts
that it does.

**The composition of the errors is the finding, not the rate.** The 23 misses are
concentrated rather than scattered — 9 in the compliance suite and 12 in the
locale suite, against 1 each in pitch and objection. Compliance and localisation
are exactly the two things a regulated-advice grader exists to check. Four of
them, three misses and the one false alarm:

```
FALSE_NEGATIVE  compliance-explicit-unlicensed-advice
    human:  "You should move the whole lot into this" is a personal
            recommendation made without a suitability assessment.
    scorer: PASS 20/20 (100.0%) -- every criterion full marks
FALSE_NEGATIVE  compliance-missing-risk-disclosure
    human:  Not one of the three disclosures this market requires was given.
    scorer: PASS 19/20 (95.0%)  mandatory_disclosure=3
FALSE_NEGATIVE  compliance-no-real-risk-reassurance
    human:  "There is no real risk here" is the opposite of the required
            disclosure, and the register is empty.
    scorer: PASS 19/20 (95.0%)  mandatory_disclosure=3
FALSE_POSITIVE  locale-es-mx-registered-spanish-disclosure
    human:  Every required disclosure was given in registered Spanish wording
            and the register records all three.
    scorer: FAIL 8/20 (40.0%)  discovery=0 mandatory_disclosure=0 closing=0
```

Two measurement decisions hold that matrix still, and both are in the code with
their reasons: the labelled traces are **conversation-only** (`converse` stops
before the scoring pass, so the instrument is never handed its own answer), and
each item is graded by a **fresh** scorer (otherwise the cohort curve makes the
confusion matrix a function of item ordering, which is a measurement of nothing).

---

## 6. Two contracts this domain needed, and why they are the same idea

Most of what a roleplay row asserts is already in `lab.checks`. Two are not, and
both are the named risks stated precisely.

### `FeedbackGroundednessContract` — hallucinated feedback

Every claim the feedback makes about what was said must be checkable against what
was actually said. This is the *reverse* of the usual grounding check: normally a
system's output is grounded in a retrieved document, here it is grounded in the
conversation the same session produced — which means the evidence is already in
the trace and the check is deterministic, free, and needs no judge.

Two families in one result. **Quoted spans** must appear, normalised, in
something the trainee or the customer said — never in the scorer's own prose,
because feedback grounded in feedback is a tautology. **Topic claims** cover the
subtler case: prose that is fluent, unquoted, and about a conversation that did
not happen.

```
feedback-grounded: 1/2 feedback claims grounded in the session
  -- quoted span never said: 'what would you want this money to be doing for you...'
  t=6.194s [agent] what would you want this money to be doing for you in ten years?
      <- attributed to the session in quotation marks; absent from all 14
         roleplay utterance(s)
```

```
feedback-grounded: 0/1 feedback claims grounded in the session
  -- fee objection: claimed but never came up
  [absence] fee objection is presupposed by the feedback and is absent from the
            objection_ledger
      <- searched 2 objection(s) in the ledger: ["last year's losses",
         'access to the money']
```

Two precision decisions are worth reading, because a groundedness check that
cries wolf gets switched off:

* The fee-objection claim is grounded in the **objection ledger** — the `topic`
  arguments of the product's own `raise_objection` events — not in the customer's
  words. Grounding it in the transcript accepts any turn containing "fee", and
  this domain's customers mention *school fees* while worrying about something
  else entirely. The loose version passed the fabricated claim on two rows before
  the ledger version replaced it. Structured evidence beats a keyword search over
  prose, which is the same argument this pack makes about the scorer.
* Quoted fragments under 24 characters are ignored. Short quotes in coaching
  prose are terminology ("the ongoing charge"), not attribution.

And the case that matters most: `pitch-exemplary-eu-retail-run` scores full marks
and its quoted exemplar **is** grounded, because that trainee happened to ask
almost exactly the template's model question. The defect is invisible on the row
a reviewer reads first and visible on the row where three equally good questions
are worded differently. Spot-checking the best row learns nothing.

### `ScoreClaimContract` — the decision-vs-action check, pointed at a grader

`lab.checks.PromiseContract` catches *the agent said it booked the table and never
called the tool*. This catches *the grader said the disclosure was given and the
register is empty* — the same defect class one layer up: a component asserting an
action that another component's ledger denies.

```
score-claims-backed: 1/2 live score claims backed by the session ledger
  -- mandatory disclosure given: asserted in the score card and feedback, but
     record_disclosure never happened
  t=6.289s [agent] mandatory_disclosure_given=True
      <- claims mandatory disclosure given; no record_disclosure event exists in
         the session ledger
```

The best row in the pack is the one where the evidence is already inside the
product. Its in-session compliance flagger raised a high-severity
personal-recommendation flag on turn six; the score card certified the session and
reported no advice:

```
score-claims-backed: 1/2 live score claims backed by the session ledger
  -- no unlicensed advice: asserted in the score card and feedback, but the
     session recorded flag_compliance_risk
  t=4.191s [agent] flag_compliance_risk({"kind": "personal_recommendation",
      "turn": 6, "utterance": "Look - you should move the whole lot into this..."})
      <- the session recorded this while the score card and feedback asserted no
         unlicensed advice
```

Nobody has to argue about whether the sentence was advice. Two components of one
product, one of them right, and the customer-facing one wrong — a contradiction
inside a single audit trail.

Three details that keep this contract honest:

* **Both channels.** A claim asserted only in the prose is still a claim; the
  trainee reads the prose and never sees the JSON. The report names which channel
  carried it.
* **Negative claims too.** "No advice occurred" is an assertion, and it is
  *refuted* by evidence rather than satisfied by it, so `ScoreClaim` has both
  `requires` and `refutes`. A claim declaring neither is a construction error.
* **A missing score card fails, rather than reporting vacuously.** A session that
  was never graded is not a session with nothing to check; it is a missing grade,
  and the first time that happens silently in CI it will happen for a month.

---

## 7. Per-row results, and the two verdicts

Fifteen of the seventy rows, chosen to show one of each shape — agreement, both
directions of disagreement, and a vacuous check. The aggregate under them is over
all seventy.

```
  pitch-cold-scorer-single-run-control     human=pass scorer=pass (16/20) agrees  3/3 pass
  pitch-exemplary-eu-retail-run            human=pass scorer=pass (20/20) agrees  4/4 pass
  pitch-feature-dump-no-discovery          human=fail scorer=fail ( 4/20) agrees  3/4, 1 fail
  pitch-terse-customer-patient-probing     human=pass scorer=pass (20/20) agrees  3/4, 1 fail
  compliance-explicit-unlicensed-advice    human=fail scorer=pass (20/20) DIFFERS 2/4, 2 fail
  compliance-guaranteed-return-caught      human=fail scorer=fail (11/20) agrees  4/4 pass
  compliance-missing-risk-disclosure       human=fail scorer=pass (19/20) DIFFERS 2/4, 2 fail
  compliance-no-real-risk-reassurance      human=fail scorer=pass (19/20) DIFFERS 2/4, 2 fail
  objection-aggressive-fee-challenge       human=pass scorer=pass (20/20) agrees  4/4 pass
  objection-lock-in-left-unanswered        human=fail scorer=fail (12/20) agrees  2/3, 1 fail
  objection-praise-for-unasked-question    human=pass scorer=pass (19/20) agrees  3/4, 1 fail
  consistency-borderline-transcript-warm-k5 human=pass scorer=pass (14/20) agrees 3/3 pass
  consistency-identical-transcript-warm-k5 human=pass scorer=pass (16/20) agrees  3/3 pass
  locale-apac-suitability-disclosure       human=pass scorer=pass (16/20) agrees  4/4 pass
  locale-es-mx-registered-spanish-disclosure human=pass scorer=fail ( 8/20) DIFFERS 2/3, 1 fail, 1 vacuous

suite 'roleplay': 32/70 traces passed every applicable check
  tools:               43/70 applicable traces passed
  score-claims-backed: 58/70 applicable traces passed
  feedback-grounded:   56/68 applicable traces passed, 2/70 vacuous
  trainee-phrases:     49/49 applicable traces passed
```

Every rate has a numerator and a denominator, and the vacuous results are counted
separately rather than folded into the green — `locale-es-mx` produces feedback
that quotes nothing and presupposes none of the declared topics, so the
groundedness contract had nothing to assert there and says so. Note the three
different denominators: 70, 68 and 49. `feedback-grounded` loses the two vacuous
rows, and `trainee-phrases` is declared on 49 rows rather than all 70. A single
"pass rate" over this suite would have to pick one of those denominators and be
wrong about the other two.

**The findings are red. The exit code is green.** Those are two different
verdicts and the demo prints both:

```
regression gate: PASS (0 surprise(s))
```

The product under test has three real defects and this run reports all of them.
The gate asks a different question: did anything *move*? Every declared
`expected_failure` fired, no undeclared contract failed, both consistency floors
were met, and the calibration gate refused the scorer as it is supposed to. A
green exit with a red report means "nothing changed since the last review". A red
exit means a finding appeared, disappeared, or changed shape, and somebody has to
look.

Conflating those two is how a suite ends up either permanently red and ignored,
or green and blind.

The gate is not only in the demo. `tests/test_roleplay_evaluation.py::test_the_demo_runs_clean_with_red_findings`
runs the whole demo and asserts the same thing, and
`tests/test_roleplay_checks.py::test_no_row_fails_a_contract_it_does_not_declare`
asserts the per-row half of it independently — so the existing `pytest -q` step in
CI already guards every finding in this document without a new workflow stage. The
measured rates in §5 and the score sequences in §4 are pinned by tests too: a
change in either is a diff to review rather than a drift to discover.

---

## 8. Two rows are worth reading in full

### The sentence that is worse than silence

> *"There is a bit of risk in it of course, but nothing that should trouble
> somebody with your level of capital behind them."*

`compliance-missing-risk-disclosure` scores 19/20 and is certified. The
mandatory-disclosure criterion is computed by counting keyword hits in the
trainee's speech — and that sentence contains two of them, *because a reassurance
has to name the risk in order to dismiss it*. The criterion does not merely miss
this. It rewards it.

`compliance-no-real-risk-reassurance` is the pair: there the disclosure is
inverted rather than absent, and it scores identically on the criterion. **A
keyword cannot distinguish a warning from its negation, because the keyword is in
both sentences.** That is the argument for reading the register — which the
product already wrote, and which `SessionView.disclosures` puts in front of the
scorer unread.

### The multilingual row, which is not a fourth defect

`locale-es-mx-registered-spanish-disclosure` is a compliant Spanish session. All
three required codes are recorded, from registered Spanish phrasings, by a
register that is keyed by jurisdiction *and* language and gets it right. The
scorer scores the disclosure criterion at zero — along with discovery and closing
— because its keyword list and its open-question stems are English.

Same root cause as the compliance rows, opposite direction: on the English rows
the keyword scoring **over**-credits; here it **under**-credits, and a competent
adviser is refused certification on a session in which they did everything asked
of them. That symmetry is the argument for fixing the mechanism rather than
extending the word list — a list extended into Spanish leaves the English
over-crediting exactly where it was.

`locale-apac-suitability-disclosure` is the control for the other half of it: the
same conversation plus one sentence, in a market whose requirement set is four
codes instead of three, and the register demands the fourth purely because the row
names a different jurisdiction. If a future change hard-codes the European set,
that row goes red and the exemplary row stays green.

---

## 9. Where things are

```
roleplay/
  register.py           jurisdictional disclosure requirements, as records
  persona.py            the customer: concerns, objections, manner   (clean)
  scorer.py             the rubric, the score card, the feedback     (3 defects)
  runtime.py            the adapter: one session, one trace, two stages
  contracts.py          FeedbackGroundednessContract, ScoreClaimContract
  corpus.py             the scenario schema and its closed vocabularies
  consistency.py        score spread and pass^k over identical repeats
  calibration.py        the scorer measured against the human column
  rubric_v1.md          the rubric, versioned and digested
  demo.py               python -m roleplay.demo
  SEEDED_DEFECTS.md     the answer key -- read this document first

scenarios/roleplay/
  customers/            4 customer profiles
  pitch/ compliance/ objection/ consistency/ locale/     70 rows

tests/
  roleplay_fixtures.py       scripts read from the corpus, never duplicated
  test_roleplay_sut.py       the product: register, persona, scorer, adapter
  test_roleplay_checks.py    the two contracts, both directions, and the schema
  test_roleplay_evaluation.py consistency, calibration, and the demo's gate
```

113 tests, all offline, all deterministic, no API keys, added to a suite that
was already green — the three files above still total exactly that:

```
1130 passed          # before this pack
1243 passed          # after it
1576 passed          # the whole suite today, after the later phases
```

Only `runtime.py`, `contracts.py`, `corpus.py`, `consistency.py` and
`calibration.py` import from `lab`. Everything that decides, acts, grades or
remembers is `lab`-free — which is what makes "an instrument pointed at a system,
rather than a framework the system must adopt" checkable rather than aspirational.

---

## 10. What this demo does *not* claim

* **The persona is not a language model.** It is a deterministic state machine,
  and nothing here claims that testing against it substitutes for testing against
  a real one. What it substitutes for is the *fixture* — the recorded,
  reproducible conversation a real pipeline would replay. Swapping in a model
  changes one file and leaves every check pointed at the same output shape.
* **The scorer is not a model either.** A real implementation would send the
  transcript to an LLM with the rubric in `rubric_v1.md`. The interesting failures
  — a score that moves on identical input, prose about a conversation that did not
  happen, a compliance criterion satisfied by vocabulary rather than by fact — are
  all reproducible without one, which is why the pack runs with no API keys.
  `roleplay.calibration.build_scorer_judge` is the seam: point it at
  `LiteLLMCompletion` and the same calibration, the same gate and the same
  disagreement list apply to a model-backed grader.
* **The register is deliberately strict.** Matching is substring-over-normalised
  text against a closed phrasing list, which is much stricter than a real register.
  That is chosen on purpose: a strict register produces *false negatives*, which
  are visible and arguable, and a loose one produces false positives — and a
  ground truth that over-credits cannot be used to catch a scorer that
  over-credits. When the instrument and the system under test share a bias, the
  measurement is worth nothing.
* **The jurisdictions are generic.** `eu-retail`, `apac-retail`, `amer-retail`
  and their code sets are invented. The point being demonstrated is that the
  requirement set is *data keyed by market*, not that these are anybody's real
  rule numbers.
