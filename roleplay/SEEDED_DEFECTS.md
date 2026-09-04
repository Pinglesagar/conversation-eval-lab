# Seeded defects — the answer key

**Read `docs/ADVISORY_DEMO.md` first.** This file is the answer key to it. Everything
in the demo output, in `scenarios/roleplay/`, and in every number this pack reports
can be read without it, which is the point: a suite that only finds the defects its
author pointed it at has demonstrated nothing.

The scorer carries three planted defects. They are documented here and nowhere
else, and nothing in `lab/` knows they exist. They are real code paths, not
switches — no flag, no injected fault, no random seed. Each one is a plausible
decision made for a plausible reason, reachable by ordinary use, and identical on
every machine, which is what lets a recorded trace stand as evidence.

## Where all three live, and why that is the whole design

The product has two halves. `roleplay/persona.py` is the AI customer the trainee
talks to; `roleplay/scorer.py` grades the result. **The persona is clean.** Not one
defect is planted in it.

That is a measurement decision. A roleplay product's conversational half and its
evaluative half fail in unrelated ways, and a suite that cannot say which one broke
will report "the roleplay is bad" for a year. With the customer correct by
construction and deterministic by design, every finding in this pack is
attributable to the grader, and the score-consistency measurement has exactly one
candidate explanation instead of two.

All three defects share a second property: **none of them produces bad-looking
output.** No exception, no tool error, no missing field. Every score card is
well-formed, every feedback page reads as competent coaching, and every session
completes. They are only visible if you check a number against its own repeats, a
sentence against the transcript, or a claim against a ledger.

---

## DEFECT-1 — the cohort curve moves an individual score

**Where** `roleplay/scorer.py`, `RubricScorer._update_curve`, applied in `score`
as `total = raw_total + self.adjustment`.

**What happens** The scorer steers its recent pass rate towards a target (60% over
a five-session window). After each session it nudges a running `adjustment` by one
point in whichever direction the recent rate needs, and that adjustment is applied
to the *next* session's total. So an identical transcript scores differently
depending on how many sessions the service graded before it, and how those went.
A performance worth 16/20 scores 16, then 15, then 14, then 13, then 12 across five
identical submissions — certified three times and refused twice.

**Why it is plausible** Grade inflation across a cohort is a real problem for a
certification product, and steering the pass rate towards a target is a real
requirement a customer will ask for by name. The design is not the mistake. The
mistake is two-fold: the correction is applied to *individual* scores rather than
to a cohort statistic, and the state lives on the service rather than on the
cohort. Reviewing this method, the arithmetic is correct and the intent is
defensible; nothing about it says "this makes the grade depend on queue position".

**Reachable by** Submitting more than one session to the same process. That is all.

**What should find it** `roleplay.consistency.measure_consistency`: k identical
repeats, scored two ways at once — the pass^k stability verdict from
`lab.simulator.passk` (FLAKY, 3/5) and a `ScoreSpread` (mean, population standard
deviation, min-max, spread against a stated tolerance, and pass/fail flip count).

**What must not find it, and this is the important half** A harness that builds a
fresh scorer for every repeat. `lab.simulator.passk.run_pass_k` documents, quite
correctly, that `run` must construct a fresh agent per repeat — an agent carrying
state from the previous repeat measures conversation history, not stability.
Applied literally here, that advice *hides this defect*: the state is held by the
service, not by the session, and a fresh service per repeat is a cold process
nobody deploys.

So the pack runs both arms and reports the pair. **A stability harness that resets
more than the deployment does cannot see state-leak instability**, and what gets
reset between repeats is a measurement decision that belongs in the report next to
the number it produced.

**The controls** `pitch-cold-scorer-single-run-control` is the same trainee script
as `consistency-identical-transcript-warm-k5` and asserts a cold grade of 16 with a
zero adjustment. The consistency row's cold arm scores `[16,16,16,16,16]` and its
warm arm `[16,15,14,13,12]`. A boundary pair whose boundary is a process lifetime.

**The one-line fix** Scope the curve to a cohort report rather than to an
individual score — or, if it must move a score, key the state on the cohort and
recompute it once, not on the service and once per session. `RubricScorer.reset()`
exists so a test can demonstrate the fixed behaviour without editing the scorer.

---

## DEFECT-2 — the feedback is written from the rubric, not from the session

**Where** `roleplay/scorer.py`, `RubricScorer._feedback`. Two branches:
the `discovery >= 3` template and the `objection_handling <= 1` template.

**What happens** Feedback prose is assembled from templates keyed on the *score*.
Two of the templates carry specifics that belong to the session and are not read
from it:

* A high discovery score emits the sentence *You opened well — asking "what would
  you want this money to be doing for you in ten years?" gave you the horizon to
  work with.* The quoted question is the template's exemplar. It is attributed to
  the trainee, in quotation marks, whatever the trainee actually asked.
* A low objection-handling score emits *You left the fee objection unanswered — the
  customer raised cost and you moved past it.* Fees are the objection the template
  was written against. The customer may have objected to last year's losses and to
  being unable to reach their money, and never mentioned cost at all.

**Why it is plausible** Templated feedback is cheaper, faster and far more
consistent than generated prose, and it is what you build first. The exemplar was
put in quotation marks so the trainee could see what a good question looks like.
Somewhere between the design and the copy, the exemplar stopped being an
illustration and became a quotation, and nothing in the type system noticed.

**Reachable by** Any session that scores three or more on discovery without asking
the exemplar question, and any session that scores one or zero on objection
handling without a cost objection. Both are common.

**Why it hides in plain sight** `pitch-exemplary-eu-retail-run` scores full marks
and the quoted exemplar is *grounded on that row*, because that trainee asked
almost exactly the model question. So the defect is invisible on the row a reviewer
reads first, and visible on `pitch-terse-customer-patient-probing`, where three
equally good questions are worded differently. A reviewer spot-checking the best
row learns nothing.

**What should find it** `roleplay.contracts.FeedbackGroundednessContract`. Two
families in one result: every quoted span in the feedback must appear (normalised)
in something the trainee or the customer said, and every declared `TopicClaim` the
prose presupposes must be grounded where the claim says it must be.

**A precision decision worth reading** The fee-objection claim is grounded in the
**objection ledger** — the `topic` arguments of the product's own `raise_objection`
events — and not in the customer's words. Grounding it in the transcript accepts
any turn containing "fee", and this domain's customers mention *school fees* while
worrying about something else entirely. The loose version passed the fabricated
claim on two rows before the ledger version replaced it. Structured evidence beats
a keyword search over prose, which is the same argument this whole pack makes about
the scorer.

**What must not find it** Anything reading only the score. Every number on
`objection-praise-for-unasked-question` is correct and deserved; the one thing the
product says to the human being who did the work is untrue. A rubric-score
regression suite is structurally incapable of catching this.

**Where it fires** `pitch-terse-customer-patient-probing` and
`objection-praise-for-unasked-question` (quoted span);
`pitch-feature-dump-no-discovery` and `objection-lock-in-left-unanswered` (fee
objection). Controls that must stay green: `pitch-exemplary-eu-retail-run`,
`objection-aggressive-fee-challenge` — on the latter the customer genuinely did
raise cost, so the same sentence is grounded.

**The one-line fix** Render the exemplar as an illustration outside quotation
marks, and name the objection from the ledger instead of from the template.

---

## DEFECT-3 — the compliance criteria are scored on vocabulary, not on the ledger

**Where** `roleplay/scorer.py`, `RubricScorer._mandatory_disclosure` and
`RubricScorer._no_unlicensed_advice`.

**What happens** Two criteria that have structured ground truth available ignore it.

* `_mandatory_disclosure` counts hits from a six-word keyword list
  (`risk`, `capital`, `past performance`, `value can go`, `charge`, `fee`) against
  the trainee's speech. `SessionView.disclosures` — the disclosure register the
  product itself wrote, keyed by jurisdiction — is in the view and is never read.
* `_no_unlicensed_advice` awards full marks unless one of *two* regexes matches
  (`guaranteed returns?`, `you will definitely make`). `SessionView.compliance_flags`
   — what the product's own in-session flagger already detected — is in the view
  and is never read.

The score card then asserts `mandatory_disclosure_given` and
`unlicensed_advice_detected` from those criteria, and the feedback says *"The
mandatory risk disclosure was given and is recorded"* and *"Nothing you said
crossed into personal advice."* Both are claims about events. Both can be false at
once, and on `compliance-no-real-risk-reassurance` they are.

**Why it is plausible** The keyword list is what you write in an afternoon before
the register exists, and it works well enough on the happy path to survive review.
The advice blocklist covers the two phrasings whoever wrote the criterion thought
of. Neither is obviously wrong on any single session a reviewer reads.

**The sentence that shows why keyword scoring is worse than nothing**
> *"There is a bit of risk in it of course, but nothing that should trouble
> somebody with your level of capital behind them."*

Two of the six keywords, one criterion satisfied at 3/4, register empty. This
criterion does not merely miss a reassurance offered in place of a warning — it
**rewards** it, because the reassurance has to name the risk in order to dismiss
it. `compliance-missing-risk-disclosure` (absent) and
`compliance-no-real-risk-reassurance` (inverted) score identically on the criterion.
That is the finding: a keyword cannot distinguish a warning from its negation,
because the keyword is in both sentences.

**The second symptom, which is not a fourth defect** The keyword list and the
open-question stems are English. `locale-es-mx-registered-spanish-disclosure` is a
compliant Spanish session: all three required codes are recorded from registered
Spanish phrasings, and the criterion scores zero, along with discovery and closing.
Same root cause, opposite direction — on the English rows the keyword scoring
over-credits, here it under-credits, and a competent adviser is refused
certification on a session in which they did everything required of them. That
symmetry is the argument for fixing the mechanism rather than extending the word
list: a list extended into Spanish leaves the English over-crediting untouched.

**What should find it** `roleplay.contracts.ScoreClaimContract` — the
decision-versus-action check pointed at a grader. A factual claim on the score card
must agree with the session's own ledgers: a claim that the disclosure was given
requires a `record_disclosure` event, and a claim that no advice occurred is refuted
by a `flag_compliance_risk` event. Both channels are checked, structured argument
and prose, because the trainee reads the prose and never sees the JSON.

`ToolContract` finds the same defects from the other side, as a `verdict` that
should have been `fail`, and `roleplay.calibration` finds them in aggregate: recall
against the human column is 0.500 (3/6), and all three misses are compliance
misses.

**The control that makes it diagnostic** `compliance-guaranteed-return-caught`.
Handed one of the two phrasings on its list, the advice criterion fires correctly,
zeroes the criterion and fails the session. The criterion is not broken; its
coverage is two phrasings wide. A row that only ever failed would not tell you
which.

**The one-line fix** Score `mandatory_disclosure` from `view.disclosures` against
`required_codes(view.jurisdiction)`, and `no_unlicensed_advice` from
`view.compliance_flags`. Both are already in the view. Deleting the keyword list is
the entire change.

**Where that fix now lives** `roleplay/scorecard_eval.py` grades the same view
against the cited registry, and its gate CG-1 is exactly that read. Both committed
spoken calls show the contrast on real audio: `rubric_v1` awards each 4/4 on
`mandatory_disclosure`; the ledger holds 2 of 3 and 1 of 3 required codes; CG-1
fails both. `scorer.py` is left as it is — the defect is the specimen.

---

## What this pack should *not* find

Deliberately absent, so that a suite reporting them is over-firing rather than
thorough:

- **No defect in the persona.** The customer is a deterministic state machine with
  no planted fault. If a run reports a persona problem, the finding is real and
  worth writing up on its merits, or the check is wrong.
- **No wrong values.** Nothing here writes a party size into a score or transposes
  a criterion. Every number the scorer computes from a criterion it computes
  *properly* is correct; the failures are the wrong input, not bad arithmetic.
- **No tool errors.** Every tool call in every session returns `ok=True`.
- **No non-determinism.** No `random`, no wall clock, no network in the decision
  path. DEFECT-1 looks like non-determinism and is not: the sequence of scores from
  a cold start is byte-identical on every machine, which is why the consistency
  rows can declare an expected spread as a number.
- **No fourth defect.** The Spanish false-fail is DEFECT-3's second symptom, not a
  new one. Anything else a run reports is either a genuine emergent defect worth
  writing up, or a false positive worth fixing in the check. Both are more
  interesting than the three above; neither is planted here.

## Where each one is exercised

| Defect | Fires in | Controls that must stay green |
|---|---|---|
| DEFECT-1 | `consistency-identical-transcript-warm-k5`, `consistency-borderline-transcript-warm-k5` (warm arm) | `pitch-cold-scorer-single-run-control`, and both rows' own cold arm |
| DEFECT-2 | `pitch-terse-customer-patient-probing`, `objection-praise-for-unasked-question`, `pitch-feature-dump-no-discovery`, `objection-lock-in-left-unanswered` | `pitch-exemplary-eu-retail-run`, `objection-aggressive-fee-challenge` |
| DEFECT-3 | `compliance-missing-risk-disclosure`, `compliance-no-real-risk-reassurance`, `compliance-explicit-unlicensed-advice`, `locale-es-mx-registered-spanish-disclosure` | `compliance-guaranteed-return-caught`, `locale-apac-suitability-disclosure`, `pitch-exemplary-eu-retail-run` |

The controls are the load-bearing half of that table. A finding without one is a
description of a symptom; a finding with one names a boundary.
