# The behavioural scorecard

*What exactly is the adviser being judged on, and why that?*

This document answers that question for the advisory-coaching domain in `roleplay/`.
The machine-readable version is [`roleplay/scorecard.py`](../roleplay/scorecard.py) —
the same twenty-eight KPIs as data, with validation that refuses at import time to
hold a row that cannot be defended. [`tests/test_roleplay_scorecard.py`](../tests/test_roleplay_scorecard.py)
proves the validation would actually catch a bad row, and proves the one invariant
that matters most: a gate cannot be outscored.

---

## 0. How to read this

### 0.1 The ladder, which is the whole argument

The product category being modelled here — an enterprise advisory coaching platform
for banking, insurance and wealth — is sold on **business** outcomes: call
conversion, product penetration, positioning and readiness, time-to-first-sale,
active ratio. A coaching platform cannot move any of those directly. It can only
change what an adviser does on a call.

So every KPI below is written as

> **observable behaviour → the business metric it is a leading indicator for**

and a behaviour that cannot complete that sentence is not on the scorecard.
`business_metric` is a required field over a closed vocabulary and the registry
refuses a row without one. That constraint is what makes this a proposal rather
than a QA checklist: it forces every row to say what it is *for*.

Each of those links is a **hypothesis about mechanism**, not a measured causal
fact. No regulator publishes conversion data, and none of the sales-research
sources ran the experiment ([`call_craft.md`](_research/call_craft.md) A-01;
[`regulators.md`](_research/regulators.md) §10 item 9). What is claimed is
plausibility of mechanism plus availability of measurement, and the honest form of
the claim is: *this is the behaviour we assert conversion depends on, here is how we
detect its absence, here is the study we would run to test the assertion.*

The strongest external validation that the framing is right is not a sales blog. It
is a regulator having already done it: MAS's Balanced Scorecard framework grades
representatives against four **non-sales** KPIs — understanding the client's needs,
suitability of recommendations, adequacy of information disclosure, and standards of
professionalism — audited by an independent sales audit unit sampling real
transactions, feeding the representative's variable income (`call_craft.md` S-01,
R18, V4 — MAS primary text was unreachable, so this is commentary standing in for a
notice).

### 0.2 Every claim is sourced or labelled an assumption

There is no third category. Every KPI carries a **Basis** (a citation into
`docs/_research/`, which carry the primary references and a verification level
V1–V4) or a labelled **Assumption**, and usually both. The registry raises at import
if a row has neither.

Verification levels matter and are carried through: **V1** means the primary text
was read in full; **V4** means commentary standing in for a primary source that
could not be reached. Two of the most quotable sentences in the whole research pack
are not V1 and are flagged where they are used.

### 0.3 Gates are counted, never averaged

A compliance requirement that contributes to a total is a heavily-weighted
criterion, not a requirement — and a session with a missing disclosure will
eventually pass by being charming elsewhere. So the enforcement is structural: a
GATE row carries zero points, which means the score total *cannot* include one.
There is no filter to forget.

A session's result is therefore **two figures, always together**: points over points
available, and gates passed over gates applicable. There is no single number, and
the manager dashboard does not get one.

A third disposition, **DIAGNOSTIC**, marks a measurement that bears on the
*instrument* rather than on the adviser — this session's word error rate, its
code-mixing band, the text-only control run. Those are mandatory to report and
forbidden to score. §3 is where that decision is argued.

### 0.4 Every rate states its denominator and what it excludes

Every rate in this scorecard carries both, in the code and in the tables below,
because a naked percentage is a defect in this repo and a rate whose exclusions are
undeclared is the same defect wearing a denominator. Consolidated in §4.

### 0.5 What in here is computed today, and what is a proposal

This document is a **design** for twenty-eight KPIs and their detectors, and most of
it is a proposal: `roleplay/scorecard.py` holds the rows as validated data, and the
detectors named against each row describe the instrument that would compute it.

The exception is the compliance-gate material — CG-1 through CG-5, and the
cross-regime divergences they turn on. Those **are** computed, by
`roleplay/regime_eval.py`, against the four cited registers at
`scenarios/advisory/registers/`. One command, no API keys:

```
make advisory-verdicts
```

It grades all eighteen advisory rows entry by entry and prints its own limitations
block first. The measured agreement with the hand labels is **16 of 18 rows**, and
that figure is **in-sample** — the probes were written with those eighteen
transcripts in view. Read
[`ADVISORY_TEST_STRATEGY.md` §4.3](ADVISORY_TEST_STRATEGY.md) for what each entry
kind computes, which limbs are refutation-only, and the two rows where the computed
verdict and the hand label disagree. Nothing in §2 below should be read as claiming
more than that.

---

## 1. The seven groups

| id | group | asks | ladders to |
|---|---|---|---|
| **CS** | call survival | Did the adviser earn the right to continue? | conversion, time-to-first-sale, active ratio |
| **DI** | discovery | Was this a needs analysis or a questionnaire? | product penetration |
| **OH** | objection handling | Engaged with, or acknowledged and abandoned? | conversion |
| **CE** | clause explanation | Explained, recited, or understated? | penetration, mis-selling exposure |
| **CG** | compliance gates | Was this session lawful in this market? | licence to operate — **gates, never a score** |
| **CL** | closing | Did the adviser ask, and was the ask honest? | conversion |
| **LL** | language and locale | Does any of the above survive a change of market? | conversion, readiness, licence to operate |

Twenty-eight KPIs: 19 scored (53 points), 8 gates, 1 diagnostic. The design budget
is 3–5 per group and the registry enforces the ceiling as hard as the floor — a
scorecard nobody reads all of certifies nobody.

---

## 2. The KPIs

Notation: **[S]** scored, **[G]** gate, **[D]** diagnostic. `V1`–`V4` are the
research pack's verification levels. "Position" always means position in the event
stream, never elapsed time — none of these requirements needs a clock.

### CS — call survival

**CS-1 [S] · 2 points · leads conversion**
Did the adviser say what the call was for before asking the customer for anything?
*Evidence:* a purpose clause in an adviser turn positioned before the first
elicitation or product turn.
*Detector:* `lab.checks.PhraseContract`, positional ordering.
*Basis:* stating the reason for the call was associated with a 2.1× higher success
rate (`call_craft.md` S-05, R1, V2). *Assumption:* A-02 — a B2B technology-sales
corpus transferred to regulated retail advisory.

**CS-2 [S] · 0–3 points · leads conversion**
Did the adviser ask permission to continue, bound the time, and name one specific
next step?
*Evidence:* an explicit yes/no request to continue in the opening turns; a stated
duration or turn ceiling in the same turn as the ask; an ask naming one small next
step rather than an open commitment.
*Detector:* `lab.checks.PhraseContract` — a positional window plus the content of
the ask, not sentiment.
*Basis:* across 153 cold calls, persuasive conduct consisted of pre-expansion plus
**minimising the imposition of the request** (`call_craft.md` S-08, R5, V3).
*Assumption:* A-03 — that minimising the imposition (craft) is separable from
securing alignment before disclosing the purpose (pressure). The same paper supplies
both signals and evaluates neither.

**CS-3 [S] · 0–3 points per resistance event · leads active ratio**
When the customer resisted, did the adviser answer the kind of resistance it
actually was?
*Evidence:* a **block** (turn-initial delay, an account for not answering, a second
unit moving to end the call) is answered by narrowing and re-specifying the
immediate request so it is separable from the sale. A **stall** (a hedge plus a
deferring counter-proposal — "email it", "call me next month", "I'll discuss
internally") is answered by declining the deferring trajectory and naming a nearer
concrete alternative. Answering a block as though it were a stall, or the reverse,
is the failure.
*Detector:* judge `resistance_response` — **unusable before calibration**.
Deterministic floor, which is what runs today: `NoReAskContract` for the block case
(an identical re-ask after an account for not answering) and `NoProgressContract`
for the stall case (a deferral accepted with no counter-proposal is literally a turn
that produced no progress toward the goal state).
*Basis:* `call_craft.md` S-09–S-16 (R4, **V1**, read in full text) — 159 transcribed
cold calls, the block/stall taxonomy, and the counter-moves the paper's extracts
show working. *Assumptions:* A-05 (that offering a genuine exit is
conversion-positive and not merely honest — the single most important assumption in
that file to test), A-07 (that the taxonomy transfers from organisational to retail
calls).

**CS-4 [S] · 1 point · leads conversion**
Did the adviser avoid inviting the customer to name the call as an imposition?
*Evidence:* absence of "is this a bad time?"-class phrasing in the opening turns.
*Detector:* `lab.checks.PhraseContract`, an absence check over a positional window.
*Basis:* the same vendor publishes **0.9%** and **2.15%** for the same opener on two
different corpora, neither stating its denominator (`call_craft.md` S-03 R1 V2 and
S-06 R2 V3). *Assumption:* A-04 — no survival curve by elapsed seconds was located
for advisory calls.
*Why one point:* the direction is consistent across both publications and the effect
size is unpublishable. The KPI carries the direction and refuses to carry a
threshold.

### DI — discovery

**DI-1 [S] · rate → 4 points · leads penetration**
How much of the fact-find this market requires was actually elicited?
*Evidence:* fields present in the elicitation ledger against the register's required
field set for this jurisdiction. **Elicited, not mentioned** — a field the adviser
named and moved past is not a field the adviser obtained.
*Detector:* ledger read (`roleplay.register` required-field set + `ToolContract`), so
the numerator is events the product recorded rather than a reading of speech.
*Basis:* COBS 9A.2.6R knowledge and experience, 9A.2.7R financial situation, 9A.2.8R
objectives (`call_craft.md` S-38–S-40, R10, V2); FAA s.36 reasonable basis and
FAA-N16's KYC set (S-43, R17, **V4** — MAS primary unreachable); MAS-3 ¶11's
nine-item checklist, which is already a scoring rubric (`regulators.md` §9).

**DI-2 [S] · rate → 4 points · leads penetration**
Did the adviser *use* the answers, or just collect them?
*Evidence:* a later adviser turn carrying the content of an earlier answer — the
stated goal, the stated horizon, the stated constraint. Asking and then ignoring is
the defining move of performed discovery.
*Detector:* `lab.checks.FieldPropagationContract`. The primitive already exists for
exactly this defect class: a value obtained and never propagated into the outcome.
*Basis:* `call_craft.md` §6.3 signal 2, named there as a propagation property.
*Why four points:* it is the hardest KPI here to game. See §6.

**DI-3 [S] · 0–2 points · leads penetration**
Did any question build on an answer, rather than advance a fixed list?
*Evidence:* an adviser question containing a term the customer introduced.
*Detector:* deterministic term overlap (`lab.checks.text.surface_forms`,
`question_key`).
*Basis:* the discriminator between high and average performers was the *type* of
question — implication and need-payoff — not the quantity (`call_craft.md` S-47, R7,
V3). And the same programme found the **open-versus-closed distinction had no
measurable effect on outcomes** (S-48), which is why this KPI *replaced*
`rubric_v1`'s open-question count rather than sitting beside it.
*Assumptions:* A-17 — "contains a term the customer introduced" is an
operationalisation of an implication question and will accept a shallow echo; A-19 —
the underlying dataset appears never to have been peer-reviewed or replicated, and
`call_craft.md` §12 states the book itself was not read.

**DI-4 [G] · gate · leads licence to operate**
Was the fact-find neither steered away from nor bypassed?
*Evidence:* two outcomes on one gate. **Steering** — a required field reframed as
optional in an adviser turn ("we can skip that one"). **Bypass** — a recommendation
turn occurring while register fields are still unelicited.
*Detector:* judge `fact_find_steering` for the steering outcome, which is a
judgement about a turn. Deterministic fallback for the bypass outcome, which is not:
a turn classified as advice positioned before register completeness — an ordering
check needing no oracle, and it is the outcome with the rule number.
*Basis:* COBS 9A.2.13R — without the required information the firm **must not
recommend** (`call_craft.md` S-41). COBS 9A.2.11R — a firm **must not encourage a
client not to provide** the information (S-42). Both R10, V2.
*Why a gate:* the commonest commercial shortcut in existence has a rule number,
which makes "impatient customer, adviser shortens the fact-find" a gate rather than
a matter of taste.

### OH — objection handling

**OH-1 [S] · rate, inverted → 4 points · leads conversion**
How often did the customer have to raise the same objection twice?
*Evidence:* a distinct objection key raised a second time in the customer's own
turns.
*Detector:* a repetition test on the objection ledger. Fully deterministic.
*Basis:* a partial repetition of the recipient's own prior talk produces escalated
disaffiliation and marks the intervening move as inapposite (`call_craft.md` S-16 /
S-19, R4, **V1**).
*Why it is the best metric in this group:* it needs no oracle, and it inverts the
usual grading direction — the **customer's** behaviour scores the **adviser's**
answer. A repetition is evidence the first answer did not land, independent of any
judgement about its quality. Second-raise rate by objection category, with its
denominator, is precisely the "not just what they sold but how and why" artefact the
manager-analytics surface sells.

**OH-2 [S] · rate → 4 points · leads conversion**
Was each objection engaged with, or acknowledged and abandoned?
*Evidence:* **engaged** — within a few turns, an adviser turn carrying a quantity or
named term drawn from the objection's own subject matter (the actual charge for a fee
objection, the actual surrender schedule for a lock-in objection, the actual
commission for a trust objection), followed by a check that it landed. **Abandoned**
— an empathy token, no objection-specific content, and a return to the pre-objection
agenda position.
*Detector:* judge `objection_engagement` for the residue the proxy cannot separate —
an adviser reciting a number without engaging. Deterministic fallback:
quantity-or-named-term presence plus `NoProgressContract` on agenda resumption.
*Basis:* `call_craft.md` §4.2 and the six-category taxonomy in S-17 (R22, V3).
*Assumptions:* A-10 — the proxy's false-positive mode is the recital; A-09 — the
BFSI-specific category list is that file's own assembly with no frequency study
behind it, so this measures **coverage of a taxonomy** and not representativeness.

**OH-3 [S] · 0–2 points per deferral · leads conversion**
On a deferral, did the adviser find out what it was, or just accept it?
*Evidence:* a diagnostic question positioned after the deferral and before any
acceptance of it.
*Detector:* `lab.checks.PhraseContract`, positional.
*Basis:* a hidden objection is one not openly stated but still an obstacle
(`call_craft.md` S-18, R25, V3); a stall is a deferral rather than a refusal, and the
counter-move in the data is a concrete nearer alternative (S-56, R4, **V1**).
*Assumptions:* A-11 — the practitioner claim that "I need to think about it" is
usually price, trust or another decision-maker is **unsourced** and must not appear
in a scenario rationale as fact. **This KPI is written so it does not depend on that
claim being true:** it grades whether the adviser asked, not whether the answer
matched the folklore. A-12 — that diagnosing beats accepting is mechanism only.

### CE — clause explanation

**CE-1 [S] · rate → 3 points · leads penetration**
Was understanding checked, or assumed?
*Evidence:* within a few turns of a key-information turn — one stating a limitation
or a cost, or prompting a decision — an adviser turn asking whether the customer
understands and whether they have further questions.
*Detector:* `lab.checks.PhraseContract` — an understanding-check turn within *k*
turns of a key-information turn.
*Basis:* FCA PRIN 2A.5.9R applies to telephone and other interactive dialogue and
requires the firm to **ask the retail customer whether they understand the
information and if they have any further questions**, particularly where the
information prompts a decision (`call_craft.md` S-22, R9 — **V3 for the wording, V2
for the section**; that file's §12 flags it as load-bearing and not V1, and it should
be re-fetched from the Handbook before it goes in front of anyone). Also: where a
client has little prior knowledge of a product type, the SFC requires more assistance
to ensure understanding (S-24, R16, V3).
*Why it is here:* it is the one KPI in this registry that a regulator states as a
**turn** rather than as an outcome, in exactly the interaction type the roleplay
surface simulates.

**CE-2 [S] · 3-level label → 0 / 1 / 3 points · leads licence to operate**
Was the clause explained, recited, or understated?
*Evidence:* three observably different behaviours. **Recital** — read out in product
language, no translation, no worked case, no check. **Genuine explanation** —
restated in consequence terms for *this* customer ("if you were diagnosed in month
two this would pay nothing, and you would be relying on your savings"), then a check.
**Minimisation** — stated and then discounted in the same or the next turn
("technically there is a waiting period, but in practice…"), with no check in
between.
*Detector:* judge `clause_explanation`. Deterministic prefilter: a limitation turn
followed within one or two turns by a minimising construction with no intervening
understanding-check — adjacency, which is what `PhraseContract` decides on.
*Basis:* `call_craft.md` §5.3; FCA PRIN 2A.5.3R (communications likely to be
understood) and 2A.5.8R (tailored to characteristics including vulnerability), S-20
and S-21, R9, V2.
*Assumptions:* A-15 — the quality bar is that file's construction, not a quoted
standard. A-16 — minimiser-adjacency will false-positive on legitimate
proportionality ("there is a waiting period, and for your age band the premium
difference is small").
*A gate in waiting.* Minimisation is the highest-severity failure in this group and
it is the one that **passes a keyword scorer**, because the disclosure vocabulary is
present. It is a SCORE today only because the detector's true negative rate is
unmeasured (A-16), and this repo's rule is that an uncalibrated detector does not
gate. Calibrate the TNR and this becomes the highest-severity gate in the registry.

**CE-3 [S] · 0–3 points · leads licence to operate**
Was the non-guaranteed figure given more emphasis than the guaranteed one?
*Evidence:* emphasis in speech is positional and countable — which figure is said
first, which is repeated, which is attached to the customer's own stated goal,
whether the guaranteed figure is said **at all**, and whether variability was
expressed as a range or as a point estimate.
*Detector:* `lab.checks.PhraseContract` plus numeric mention counting.
*Basis:* HK IA GL28 — assumed rates are neither guaranteed nor based on past
performance, and insurers "should not highlight figures … which are not guaranteed"
(`call_craft.md` S-30, R15, V3, flagged in §12 as load-bearing and not V1);
participating policies require pessimistic **and** optimistic projections to show
variability, which is the range-versus-point-estimate test (S-31).
*The point:* a presence check passes this failure by construction. An adviser who
quotes a projected maturity value, attaches it to the customer's retirement plan,
repeats it and never states the guaranteed floor has done in speech exactly what the
guideline forbids in print (`call_craft.md` C-2).

**CE-4 [S] · 0–2 points per trigger · leads licence to operate**
When the customer asked what happens if they stop, did they get a recoverable value?
*Evidence:* a trigger — affordability raised, income volatility mentioned, or "what
if I stop paying" asked — followed within a few turns by a turn stating what is
recoverable in the early years: a number, or an explicit "substantially less than you
paid in". The failure signature is a horizon statement in its place ("it's a
long-term product, you'd want to keep it going").
*Detector:* `lab.checks.PhraseContract`, conditional: a trigger obliges specific
content within a window.
*Basis:* "Insurers sell front-loaded policies, make money on lapsers, and lose money
on non-lapsers" (`call_craft.md` S-32, R20, **V1**, read in full text). Almost **25%
of permanent policyholders lapse within three years** and 40% within ten (S-34) —
which is what makes "most people keep these going" false rather than merely
optimistic. Households are roughly twice as likely to surrender after a spouse
becomes unemployed (S-36), which is why volatility is a trigger and not a digression.
*Why this row exists:* the product's economics depend on a customer outcome the
adviser has a duty to explain and an incentive to minimise. An adviser explaining
early-surrender loss honestly is explaining how the policy makes money.

### CG — compliance gates

Every row here is a **gate**. None carries points. All ladder to licence to operate,
and the registry refuses a gate that ladders anywhere else.

**CG-1 [G]** — Did every disclosure this market requires actually get made, in this
session's language?
*Evidence:* one `record_disclosure` event per required code, keyed by
**jurisdiction, code and language**. The register also records, per code, which
*kind* of requirement it is — **verbatim**, **prescribed-unit**, or **substance** —
because that determines whether a miss is evidence about the adviser or evidence
about the instrument.
*Detector:* `roleplay.register.DisclosureRegister` + `ToolContract`. A ledger of
recorded events, **not a phrase scan of the transcript** — see §6 for the measured
reason.
*Basis:* the four regimes split into two drafting traditions (`regulators.md` §8).
Some requirements are satisfiable only by a specific form of words — the FCA's COBS
4.12A verbatim warnings with prescribed prominence, the COBS 4.5A.10R past-
performance sentence, the literal terms "independent advice" / "restricted advice".
Some only by substance — the SFC's Schedule 9 disclosure "containing the substance",
MAS ¶25(c)'s "not necessarily indicative", the FCA's consumer-understanding outcome.
And D4: "not a reliable indicator of future results" and "not necessarily indicative
of future performance" carry the same meaning and share almost no tokens, so a
substring register keyed on the UK phrasing records **zero** disclosures in a
correctly conducted Singapore session.
*Note:* `roleplay/register.py` already carries `KEYWORD_SHADOW_TERMS` as a committed
control, so the gap between the register and a keyword check is a measured number in
this repo rather than an assertion.

**CG-2 [G]** — Were the disclosures delivered in the required order, and where
required, simultaneously?
*Evidence:* A-before-B tests on the utterance and artefact sequence — written
disclosure of relationship, fees and conflicts prior to or at the time of the
recommendation; the charging structure in writing in good time before the personal
recommendation; the suitability report before the transaction is concluded; the
recommendation document before the client signs. Two MAS requirements are
**simultaneity, not sequence**: an oral past-performance statement is permitted only
if the written disclosure is provided *at the same time*.
*Detector:* `PhraseContract` + `Ordering`. Position, not timestamps.
*Basis:* `regulators.md` §7 rows 1–24, each with a paragraph-level citation, and the
closing note on MAS ¶26(a)–(b).
*Note:* that section also warns that the rubric's own "a summary must precede the
ask" is a **rubric** requirement and not a regulatory one. It lives in CL-2 and must
not be cited to a regulator.

**CG-3 [G]** — Did the adviser stay on the right side of the licensing boundary — in
whichever direction it runs here?
*Evidence:* **two outcomes**, because the boundary is a different *kind* of object in
each regime. One: a modal shift to second-person prescription ("you should move your
pension into this") absent the licensing and suitability precondition, where duties
attach at the recommendation. Two: the **absence** of advice where the regime
inverts — for an unsolicited non-exchange-traded derivative sold to a client without
derivatives knowledge, the SFC's ¶5.1A(b)(ii) duty is to warn **and advise on
suitability**, so failing to advise is the breach.
*Detector:* judge `licensing_boundary`. Deterministic fallback: the in-session
compliance-flag ledger, which `rubric_v1` already treats as dispositive.
*Basis:* `regulators.md` D10 — MAS a scope carve-out, FCA a trigger for a body of
rules, Reg BI a gradient expressly "not susceptible to a bright line definition"
assessed on call-to-action and tailoring, SFC inverted. Reg BI's four obligations
attach to a recommendation (`call_craft.md` S-44, R13, V3).
*Assumptions:* `regulators.md` §10 items 1 and 3 — the UK advice perimeter (Article
53 RAO) and the SEC post-adoption staff bulletins were not read; only the weaker
claim is used, that the cited obligations attach to advice rather than to product
information.
*Why this is the row to build the pitch on:* every off-the-shelf compliance detector
fires on *giving* advice. In one of the four regimes, **not advising is the breach**.
A single-outcome detector does not score that wrong — it has no place to put the
finding.

**CG-4 [G]** — Was remuneration disclosed in the form this regime permits, including
"not at all"?
*Evidence:* the **unit**, not the sentence. MAS: the *amount* of commission on an
investment product — and for a life policy, only the distribution-cost item in the
illustration. SFC: a **percentage ceiling of the investment amount, per transaction,
rounded up to the whole percentage point**. Reg BI: standardised ranges suffice.
FCA: provider commission on a retail investment recommendation is **prohibited
outright**, so disclosing it is a confession rather than a compliance.
*Detector:* `lab.checks.ArgPredicate` — a unit check on a recorded number.
*Basis:* `regulators.md` D1 and D8. "There's no charge to you — the provider pays us
a commission, which is 3% of what you invest" satisfies MAS, satisfies the SFC,
over-satisfies Reg BI, and confesses a prohibited remuneration arrangement under FCA
COBS 6.1A.4R. **A keyword checker looking for a commission disclosure scores that
line identically in all four.** And D8 is an *intra-conversation* divergence: a
Singapore adviser recommending a unit trust and a whole-of-life policy in one meeting
owes two different disclosure standards.
*Why it is cheap and strong:* requirements about units are checkable far more
reliably than requirements about sentences (`regulators.md` §8 consequence 3). This
is the highest-confidence compliance signal in the set.

**CG-5 [G]** — On a vulnerability signal, did the adviser do what *this* regime
requires?
*Evidence:* the required **action** differs in kind, not in wording. MAS: the
selected-client determination made, documented and declared before the sales and
advisory process proceeds, then a qualifying **trusted individual present** or a
**written declination** — a procedure. FCA: communications tailored to
characteristics of vulnerability, plus an understanding-check — CE-1's contract
evaluated on the vulnerability signal — an outcome.
*Detector:* `ToolContract` on the determination (MAS route) + CE-1's contract (FCA
route). Same signal, two detectors.
*Basis:* from 29 December 2025 MAS's revised notices require the selected-client
determination, a trusted individual present subject to criteria, audio-recorded
pre-transaction call-backs, and independent sales audit checks (`call_craft.md`
S-45, R19, V2). FCA PRIN 2A.5.8R tailoring (S-21, R9, V2). `regulators.md` D6 — MAS's
three objective questions with two negatives making a selected client, against the
FCA's no-thresholds outcomes test.
*Why it matters beyond compliance:* MAS asks whether the **procedure** was followed;
the FCA asks whether the **customer understood**. Those are different observable
behaviours, which is why a locale axis that only swaps disclosure strings has proven
less than it looks (`call_craft.md` §9 item 7).

### CL — closing

**CL-1 [S] · 2 points · leads conversion**
Did the adviser ask for the business at all?
*Evidence:* a turn classified as a close attempt.
*Detector:* `roleplay.persona.classify_trainee_turn`, already ordered so that a
personal recommendation dressed as a close classifies as **advice**, not as a close —
the compliance consequence outranks the conversational one.
*Basis:* `roleplay/rubric_v1.md` criterion 5, retained unchanged.

**CL-2 [S] · 0–3 points · leads conversion**
Did a real summary precede the ask — including what is wrong with this product for
this customer?
*Evidence:* a summary turn positioned before the close attempt, whose content
carries fields elicited earlier in the session (propagation, not a template), and
which states **at least one disadvantage of this product for this customer**.
*Detector:* `PhraseContract` + `FieldPropagationContract`. The propagation half is
what a template summary fails.
*Basis:* successful calls tend to rapport, then problems explored in depth, then
logistics and next steps (`call_craft.md` S-52, R3, V3); Reg BI's Disclosure
Obligation covers **material limitations** on what may be recommended (S-44, R13,
V3); MAS-3 ¶35(c) requires the product's *disadvantages for this client* to be
documented and ¶30 requires "no suitable product" to be said when it is true
(`regulators.md` §9).
*Note:* the ordering half is the rubric's own requirement and **not** a regulator's;
the disadvantage half is a regulator's. Keeping the two apart inside one KPI is
deliberate — a reader should be able to see which half survives a challenge.

**CL-3 [S] · 0–3 points · leads conversion**
Was the close soft or pressured?
*Evidence:* **soft** — names a specific next step, states what the customer is not
committing to, and accepts a no without a further attempt. **Pressured**, three
signals — a re-ask after a clear decline; a deadline or scarcity claim; and the
cooling-off period offered as a *reason to sign* ("you can always cancel"), which
converts a consumer protection into a closing lever and shifts the decision burden
onto someone who has just been told the decision is reversible.
*Detector:* judge `close_pressure` for the residue. Deterministic fallback:
`NoReAskContract` for the re-ask after a clear decline, the signal that needs no
oracle.
*Basis:* in large, complex sales, aggressive closing techniques **reduce** success,
and the effect worsens as decision consequence rises (`call_craft.md` S-51, R7, V3);
FCA COBS 4.2.1R, fair, clear and not misleading, taking into account the nature of
the client (S-54, R8, V2).
*Assumptions:* A-20 — the soft/pressured boundary is constructed from S-51, S-53 and
S-54 and quoted from none of them. A-21 — cooling-off-as-closing-lever is a distinct
gradeable pressure move; no source states it, it follows from the purpose of the
cooling-off period.
*Why this is scored and not gated:* A-21 is an assumption. Gating on an unsourced
construct would be exactly the failure this repo is a portfolio piece against.

**CL-4 [G]** — Was there any unevidenced urgency, or any incentive leaking into the
call?
*Evidence:* a deadline or scarcity claim with no basis in the product ledger; or
first-person quota or campaign language in an adviser turn ("I've got one more of
these to place this month", "the campaign closes Friday").
*Detector:* `PhraseContract` + `ArgPredicate` **against the product ledger**, so "the
rate does end Friday" passes and an invented deadline does not.
*Basis:* Reg BI's Conflict of Interest Obligation requires firms to **eliminate**
sales contests, quotas and bonuses based on specific securities within a limited time
period, because those create high-pressure situations to act contrary to the
customer's best interest (`call_craft.md` S-53, R13, V3); FCA COBS 4.2.1R (S-54).
*Assumption:* A-06 — that false urgency also fails *commercially* is unsourced. The
gate does not rest on that; it rests on the rule.
*Why a gate:* the utterance is evidence of a conflict the regulator requires firms to
**eliminate**, not manage. And on a conversion-only scorer it is the single
most-rewarded non-compliant move in the whole taxonomy (`call_craft.md` C-6, C-11).

### LL — language and locale

**LL-1 [S] · 0–3 points · leads conversion**
When the customer refused, did the adviser hear it?
*Evidence:* a closing-sequence customer turn labelled by the market's own refusal
taxonomy — `direct-no`, `conventional-indirect-no`, `genuine-defer`, `open`. The
adviser failure is treating a conventional-indirect refusal as still open: a
follow-up ask, or a session outcome recorded as OPEN.
*Detector:* judge `refusal_taxonomy`, calibrated **per market** and published per
market. Pooling markets would hide precisely the disparity that matters, so
per-market calibration is not extra rigour, it is the point. In a market with no
committed calibration report the KPI **does not run** and leaves the denominator; it
does not silently score zero.
*Basis:* both Chinese and American respondents prefer indirect refusal strategies but
Americans use materially more direct ones; Japanese speakers overwhelmingly prefer
indirect strategies, with unfinished sentences a conventionalised polite refusal; the
Beebe et al. direct/indirect taxonomy is the standard frame
([`markets_languages.md`](_research/markets_languages.md) §3.4, `[S26-sec]`).
*Assumption*, labelled loudly in that file as the most consequential inference in
it: the specific mapping "*I will consider it*" = a settled no is widely reported for
East Asian business communication but is **not quantified for a financial advisory
setting**. It is a hypothesis this corpus should test, not a fact the scorer assumes.
*Why it is here at all:* this is the label every conversion-linked KPI is validated
against. Misread it and the error propagates — the session enters the "still in play"
denominator, pipeline and readiness KPIs are computed on a denominator containing
dead calls, and the coaching recommendation becomes "follow up" when the correct one
is "you lost this at the objection and did not notice". **You cannot calibrate a
conversion-linked judge in a high-context market until the refusal taxonomy is
market-specific.**

**LL-2 [S] · 0–2 points · leads positioning readiness**
Was the adviser's formality register stable and appropriate to the relationship?
*Evidence:* an unrequested switch between formality levels inside one session — the
grammaticalised T-V distinction in German, French, Spanish and Italian; Japanese
keigo; Korean speech levels; Vietnamese kinship address; Indonesian Bapak/Ibu.
*Detector:* per-language, deterministic switch detection. It detects a **switch**,
which is measurable, rather than politeness, which is not.
*Basis:* all of those encode something a scorer routinely grades under
"professionalism" or "rapport", and a model scoring politeness on Anglophone cues has
no access to the actual signal (`markets_languages.md` §3.4).
*Assumption:* that the useful eval is register **stability and appropriateness**
rather than politeness, because stability is measurable without a cultural oracle.
That is that file's assumption and it is this KPI's whole basis.
*The politeness version of this KPI must never be built.* §7 says why.

**LL-3 [G]** — Was every prescribed number correct for this jurisdiction — the number
**and its trigger**?
*Evidence:* the cooling-off or free-look period and the event that starts its clock.
FCA: **30 calendar days**, from conclusion of the contract or from when the consumer
is informed it was concluded — and where several periods apply, the firm applies the
**longest**. SFC/IA: **21 calendar days** from delivery of the policy or of the
cooling-off notice, **whichever is earlier**. MAS: **at least 14 days** from the date
of receipt of the policy. US: state-dependent, typically 10–30 days, and
**unanswerable without naming the state**.
*Detector:* `lab.checks.ArgPredicate`, a numeric register check. A prescribed number
is a prescribed *unit*, which makes this one of the few compliance requirements that
is genuinely string-checkable.
*Basis:* `regulators.md` D7 — every number differs and every start-trigger differs
(FCA-14 COBS 15.2.1R, 15.2.3R and 15.2.2G; HK-3 secondary; SG-1 reg 8(1)(a); US-4
secondary) — and §7 row 24, where the clock start is an ordering rule inside a
duration rule.
*Assumption:* `regulators.md` §10 item 5 — no specific US state's number is asserted
anywhere in the research, so a US scenario turning on one must name the state and
cite that state's provision.
*The failure it catches:* "You've got a couple of weeks to change your mind, starting
from when you sign" is roughly right in Singapore and wrong about the trigger,
materially wrong in the UK and Hong Kong, and unanswerable in the US.

**LL-4 [D]** — Is this session's behavioural score readable at all, and readable
against what?
*Evidence:* three instrument readings published beside every voice score — this
session's word error rate; its code-mixing band (CMI and I-index, as measured
metadata rather than a verdict); and the **text-only control run of the same
scenario**. Text score minus voice score, per market, *is* the speech-engine
contribution, separated from the adviser.
*Detector:* measurement (`lab.voice` WER and timing calibration; per-utterance
code-mixing metrics). It grades the harness and the vendor stack. It grades nobody's
adviser.
*Basis:* five commercial ASR systems transcribing matched structured interviews
averaged WER **0.35 for Black speakers against 0.19 for white speakers**, matched for
age and gender (`markets_languages.md` §3.6 `[S18]`); Whisper recognised American
English better than British or Australian, and native accents better than non-native
(`[S19-sec]`); 82% of transcribed segments in the Singapore/Malaysia corpus of record
are neither monolingual Mandarin nor monolingual English, and the two code-switching
pairs that matter most in this product's two Asian hubs are **not** in our own speech
vendor's supported code-switching set (§3.1 `[S15]`, `[S21]`, `[S22]`); and
[`WER_NORMALISATION.md`](../lab/voice/engines/WER_NORMALISATION.md) — a verified
round trip where recognition was **perfect** still scored roughly 50% word error
because the two sides format numerals differently, so even the instrument that
protects against the bias needs its own normalisation stated.
*Why DIAGNOSTIC and not SCORE:* it measures the instrument, not the adviser, and a
measurement of the instrument must never move a person's certification. It is
mandatory reporting all the same: **no behavioural score from a voice session is
reportable without that session's WER beside it, and no cross-market comparison is
reportable at all unless the WERs are within a stated band.** Same family as this
repo's denominator-safety rule.

---

## 3. Where this extends `rubric_v1`

`roleplay/rubric_v1.md` grades five criteria out of four and totals out of twenty.
Nothing it graded has been dropped. `RUBRIC_V1_SUCCESSORS` is the map, and a test
asserts it is total against `roleplay.scorer.CRITERIA`:

| rubric_v1 criterion | successors | what changed |
|---|---|---|
| discovery | DI-1 … DI-4 | off the open/closed axis, which the largest study in the field found **inert** (S-48), and onto register coverage, propagation, depth and a steering gate |
| objection handling | OH-1 … OH-3 | second-raise rate added as a first-class metric; it needs no oracle and it is the cohort artefact the analytics surface sells |
| mandatory disclosure | CG-1, CG-2, LL-3 | one criterion became three gates: the set, the **order**, and the numbers |
| no unlicensed advice | CG-3 | one gate, now with **two** outcomes, because in one regime failing to advise is the breach |
| closing | CL-1 … CL-4 | summary-before-ask retained verbatim; the product's disadvantages added; pressure separated from persistence; urgency and incentive leakage promoted to a gate |

Two things `rubric_v1` has that this keeps: the register is the authority for
disclosure, not vocabulary; and any in-session compliance flag is dispositive. One
thing it lacks that this adds, on MAS's own model (S-02): **materiality**. MAS
separates infractions with material client impact from those without; a missing
disclosure the customer already had in writing and a minimised exclusion that changed
the decision are not the same defect. In this scorecard that separation is the
gate/score line itself.

---

## 4. Every rate, its denominator, and what it excludes

A naked percentage is a defect in this repo. So is a rate whose exclusions are
undeclared.

| KPI | denominator | excludes |
|---|---|---|
| CS-3 | resistance events (blocks + stalls) | customer turns that are questions rather than resistance; **sessions with zero resistance events are 0/0, never a full score** |
| DI-1 | fields the jurisdiction's fact-find register requires | fields the customer **refused** — recorded as refused and removed, because a refusal is the customer's behaviour |
| DI-2 | answers the customer actually gave | answers given in the final two turns, which had no later turn to propagate into |
| DI-3 | adviser questions in the session | questions repeating a term the adviser introduced first; confirmations, which echo by construction |
| OH-1 | **distinct objection keys**, not ledger rows | sessions with no objection (0/0); and the rate is **never pooled across personas** |
| OH-2 | objections raised, by distinct key | objections raised in the final turn |
| OH-3 | deferral events | sessions with no deferral (0/0) |
| CE-1 | key-information turns | key-information turns inside the final *k* turns, which had no window for a check |
| CE-2 | limitation turns | a limitation named only inside a quoted policy title — a reference, not a statement |
| CE-3 | sessions quoting a projected figure at all | sessions quoting no figures; products with no guaranteed element |
| CE-4 | trigger events | sessions with no trigger (0/0) |
| CG-1 | codes this jurisdiction requires | a code satisfied in a language the register does not carry — an **instrument gap**, recorded as such, not an adviser failure |
| CG-2 | ordering requirements applicable to this jurisdiction and product | requirements whose trigger never occurred |
| CG-3 | one gate, two outcomes | product/solicitation combinations that engage neither |
| CG-4 | one gate at recommendation | the life-policy leg inside a MAS session, which carries a different standard from the investment leg **in the same conversation** |
| CG-5 | one gate, on a vulnerability signal | sessions with no signal. An adviser who correctly **stops** the call passes this gate |
| CL-2, CL-3 | the session, where a close attempt exists | sessions with no close attempt |
| CL-4 | every session with an adviser turn | nothing |
| LL-1 | closing-sequence customer turns, per market | markets with no committed per-market calibration report — the KPI does not run and leaves the denominator |
| LL-2 | adviser turns in a language that grammaticalises formality | languages without the distinction; switches the **customer requested**, which are correct |
| LL-3 | prescribed numbers quoted | sessions quoting none. A US session quoting a period without naming the state **fails**; the omission is the defect |
| LL-4 | reference tokens in this session's transcript, **harness-relative** | sessions with no audio path; and a cross-market comparison outside a stated WER band is **not reported at all** |

Two of these are the load-bearing ones. OH-1's `0/0` and LL-4's harness-relative
denominator are both cases where the tempting rendering — 100%, and "our WER is 9%" —
would be a fabricated number rather than a good result.

---

## 5. The conflict map

Without this section an adviser games one axis and the scorecard rewards it. Each
row is a pair that genuinely pulls, what wins, and the mechanism that makes the win
happen rather than being asserted.

### 5.1 Persistence (CS-3, OH-3) against pressure (CL-3, CL-4)

CS-3 rewards declining a deferral and proposing a nearer concrete step. CL-4 fails a
session for pushing after a decline. These are the same behaviour at different points
in a sequence, and the resolution is positional: **CS-3 is available on the first
resistance event; after an explicit decline it is not.** A second attempt past a clear
decline scores nothing on CS-3 and fails CL-4.

The mechanism matters as much as the ruling. When a gate fails, the scored KPIs that
the gate-failing behaviour *earned* are zeroed, not kept. Otherwise pressure banks
persistence points on a failed session and the manager's dashboard reads "strong
objection handling" next to a mis-sell. **Winner: the gate, and it takes the points
with it.**

### 5.2 Discovery coverage (DI-1) against the time-bounded ask (CS-2) — and DI-4

CS-2 rewards "I'll only take two minutes". DI-1 wants a nine-item fact-find. Those
are incompatible, and DI-4 makes shortening it a named breach (COBS 9A.2.11R /
9A.2.13R).

The resolution is not to pick a side, and this is the most useful row in the section:
the compliant behaviour is a **third** one — re-contract the time out loud. "This
needs longer than two minutes; can we book fifteen?" satisfies CS-2 (an explicit,
bounded, single-next-step ask), preserves DI-1, and does not touch DI-4. A conflict
map that only ranked the two would have taught advisers to choose between a rule and
a score. **Winner: DI-4, and the way out is a behaviour neither KPI originally
described.**

### 5.3 Call survival (CS, all of it) against the duty to end the call (CG-5)

On a vulnerability signal in a MAS session the required action is to *not proceed*
without a trusted individual or a written declination. Ending the call is the correct
behaviour. Every call-survival KPI rewards continuing.

Scoring CS 0/9 on such a session would train exactly the wrong behaviour. So the
resolution is neither "CS wins" nor "CS scores zero": **the CS group becomes
inapplicable and leaves the denominator**, the session's points available fall by
nine, the threshold falls with them, and the report prints the reduced denominator.
`test_a_suppressed_call_survival_group_shrinks_the_denominator_rather_than_scoring_zero`
is that rule in arithmetic. **Winner: CG-5, unconditionally, and it suppresses rather
than penalises.**

This is also the row that shows why a scorecard needs a conflict map at all. Both
naive readings — "survive the call" and "you scored zero on survival" — punish an
adviser for doing the right thing, and only an explicit rule prevents it.

### 5.4 Closing (CL-1) against disclosure ordering (CG-2)

A flawless discovery, a well-reasoned verbal recommendation and a close on the call
is **compliant under Reg BI** and **in breach under both the FCA and MAS** — the same
transcript, the same words (`regulators.md` D2). CL-1 scores full marks in all four.
CG-2 fails two of them.

**Winner: CG-2**, and the consequence for reporting is sharper than the ruling: a
cross-market comparison of CL-1 is meaningless unless stratified by jurisdiction,
because the same behaviour is a pass in one column and a breach in the next. The
denominator has to carry the market.

### 5.5 Trust-building disclosure (CG-4) against itself, across regimes

Volunteering the commission unprompted is the right instinct — it pre-empts a source
objection, and `call_craft.md` §8.1 lists it as one of the three places where
compliance and conversion genuinely agree. Under the FCA it is a confession of a
prohibited remuneration arrangement.

**Winner: the jurisdiction data.** The lesson is that the KPI is not "disclose
commission" but "disclose remuneration in this regime's permitted form" — and one of
the permitted forms is *nothing, because the arrangement is not allowed*. A scorecard
whose row read "commission disclosed: yes/no" would score the FCA breach as a
strength.

### 5.6 CG-3 against itself, in two directions

Every plausible unlicensed-advice detector fires when an adviser gives a
recommendation. In Hong Kong, on an unsolicited non-exchange-traded derivative sold
to a client without derivatives knowledge, **not advising is the breach**
(`regulators.md` D10, HK-1 ¶5.1A(b)(ii)).

**Neither direction wins; both are outcomes on one gate.** A single-outcome detector
does not get this wrong — it has nowhere to put the finding, which is a strictly worse
failure than a wrong answer, because it is invisible.

### 5.7 Understanding checks (CE-1) against time-to-first-sale

Every check costs turns, and one of the business metrics this platform is sold on is
*75% faster time-to-first-sale*. This is the one conflict where a growth metric the
vendor advertises is in tension with a behaviour a regulator states as a rule.

**Winner: CE-1**, because PRIN 2A.5.9R names the behaviour for exactly this
interaction type. But the honest version of this row is that the platform should say
out loud which of the two it optimises, because a coaching product that quietly
optimises the advertised metric will coach the check away.

### 5.8 OH-1 against the persona mix

OH-1 grades the adviser using the customer's repetitions. An aggressive persona
re-raises more by construction, so a cohort whose scenario mix shifted toward
combative customers looks like a cohort that got worse.

**Neither wins: the metric is reported per persona and never pooled.** This is the
same discipline already in `roleplay/scorer.py`, which counts distinct objection keys
rather than ledger rows precisely so that an insistent customer does not make one
unhandled objection look like two.

### 5.9 The meta-conflict: managers want one number

They cannot have one. A single number is only definable if gates are weighted, and a
weighted gate is not a gate. The deliverable is two figures side by side, and the
argument for that is not aesthetic: the moment they are combined, the combination
tells you which of the two the firm is willing to trade.

---

## 6. How each group would be gamed, and what the detector must do instead

Every metric here is a target the moment it is published. This section is what
separates a scorecard from a rubric.

**The measured finding this whole section rests on.** In this repo,
`lab.checks.PromiseContract` — the most carefully reviewed literal-pattern set in the
codebase — caught **1 of the 7** unbacked confirmations that a deliberately generous
hand-written detector found across 30 recorded live conversations. Against 24
hand-labelled traces it had scored 6/8. Same defect class; the detector went blind
when the words changed. The numbers are pinned in
[`tests/test_checks_paraphrase.py`](../tests/test_checks_paraphrase.py) so that a
pattern edit that drops recall fails the build rather than a README.

The consequence for this scorecard is a rule, not a caveat: **no phrase-list detector
may gate.** CG-1 gates on a *register* — a ledger of events the product recorded —
and not on a phrase scan of the transcript. Every phrase-list detector here is a
SCORE or a DIAGNOSTIC, and any KPI whose detector is a literal pattern set must
publish its recall against paraphrase before anyone believes its zeros.

**CS.** Gamed by memorising one opener and reciting it every call. The words are
trivially satisfiable; the structure is not. The detector must check that the *ask
was actually smaller* — one named next step, a stated ceiling — rather than that the
phrase "just two minutes" appeared. And the cross-session signal is the strongest one
available: an opener byte-identical across forty sessions is an adviser optimising a
detector, and it is visible only if the report looks across sessions rather than
inside one.

**DI.** Open-question **count** is the canonical gamed metric — fifteen questions,
zero coverage — and the largest study in the field says the open/closed axis was inert
anyway (S-48). The replacements are chosen for game-resistance: DI-1 is coverage
against a register (denominator-safe, judge-free, and you cannot fake a field you did
not obtain), and DI-2 is propagation, which requires a later turn to *carry the
content* of an earlier answer. Propagation is the hardest row here to fake, because
faking it means actually using the answer.

**OH.** Two distinct games. First, resolve your own objection: emit `resolve_objection`
with no engaging content. The defence is that OH-1's numerator is the **customer's**
repetitions, not the adviser's claims. Second, and worse, **suppress the objection**:
an adviser who never lets a concern surface has a second-raise rate of 0/0. Rendering
that as 100% would make the most evasive session the highest-scoring one, which is
why `0/0` is reported as no denominator and never as a score. Third, the cheap one:
"I completely understand" satisfies any sentiment detector, which is exactly why OH-2
requires a quantity or named term from the objection's **own** subject matter.

**CE.** Recital games every keyword scorer by construction, and **minimisation
actively passes one** — the disclosure vocabulary is all present ("technically there's
a waiting period, but in practice nobody…"). Presence detection cannot express this
failure. The detector has to be positional: adjacency between a limitation turn and a
minimiser, with the absence of an intervening understanding-check as the discriminator.
And CE-3 is the same lesson on numbers: "not guaranteed" appearing *somewhere* is
satisfied by an adviser who then repeats the projection four times and never states
the floor. Emphasis is order, repetition and attachment — a presence check passes the
breach by construction.

**CG.** The seeded-defect case, already committed in this repo. `roleplay/scorer.py`
DEFECT-3 scores mandatory disclosure on keyword presence, so *"there is no real risk
to your capital here"* — which contains `risk` and `capital` — scores full marks on a
session whose register is empty. And the same instrument fails in the other direction:
`KEYWORD_SHADOW_TERMS` is English, so a correctly disclosed Spanish session scores
zero. Both directions are quantified against the register by
`roleplay.register.compare_with_keyword_check`, which is why the gap is a number here
rather than a warning.

The deeper version is D4. A substring register keyed on the FCA's *"past performance
is not a reliable indicator of future results"* records **zero** disclosures in a
correct Singapore session, where the requirement is the substance *"not necessarily
indicative of future performance"*. Same meaning, almost no shared tokens. So the
register records, per code, **which kind of requirement it is** — verbatim,
prescribed-unit, or substance — because that field is what tells a reviewer whether a
miss is evidence about the adviser or evidence about the instrument. Without it, the
two are indistinguishable, and an instrument bug is indistinguishable from a
compliance breach.

**CL.** Gamed by a template: a stock summary and a stock ask, identical every call.
CL-2's defence is propagation again — the summary must carry fields elicited *in this
session*, and the disadvantage stated must be a disadvantage of *this* product for
*this* customer. A template cannot satisfy either without becoming a real summary.

**LL.** The worst gaming case in the set, and it is the instrument's fault rather than
the adviser's: **speak English in a non-English market, because the English detector
is better.** If the register only carries English phrasings and the judges are only
calibrated on English, then abandoning the customer's language raises the score. The
detector must credit a disclosure in whichever language carried it — including an
English clause inside a Cantonese sentence, which the register's current per-language
schema cannot express without a change (`markets_languages.md` §3.1) — and the shadow
comparison must be published per language so the incentive is visible.

**And the calibration rule that sits over all of it.** Seven KPIs here name a judge.
None of them is usable until a calibration report is committed and clears the gate,
and the repo has a measured reason for the caution: one judge prompt scored **TPR
0.250 (2/8)** with TNR 1.000 (16/16) and kappa 0.308 — it missed six of eight real
failures — and `lab.judges.require_calibrated` **refused** it; a revised prompt reached
1.000/1.000 (`lab/judges/hallucinated_confirmation/calibration_v1.md`, `_v2.md`).
Worse, the failing prompt returned an *identical* confusion matrix on three separate
runs, because its two unstable items sat on opposite sides and cancelled. **Aggregate
stability is not instrument stability**, so a judge-detected KPI must publish per-item
verdict stability and not only a matrix.

---

## 7. What must **not** be a KPI

The section a reviewer will respect you for writing before they ask for it.

### 7.1 The framing that makes this section necessary

**This scorecard certifies people.** The practice surface exists to declare an
adviser ready to sell; the manager surface reports on how and why they sold. A
certification decision with employment consequences is an employment decision, and
every property of the score inherits that. Two things follow, and they are not
philosophy:

- A score correlated with accent, dialect or non-native fluency is a **discrimination
  risk dressed as a quality metric**. The mechanism is not malice. It is a word error
  rate nobody printed next to the score.
- The evidence that the mechanism is real and large: five commercial ASR systems
  transcribing matched structured interviews averaged **WER 0.35 for Black speakers
  against 0.19 for white speakers**, matched for age and gender
  (`markets_languages.md` §3.6 `[S18]`). Whisper recognised American English better
  than British or Australian, and native accents better than non-native (`[S19-sec]`).
  A behavioural score computed from a transcript inherits that transcript's error
  rate.

Hence LL-4 exists as a DIAGNOSTIC and its reporting rule is mandatory: no behavioural
score from a voice session is reportable without that session's WER beside it; no
cross-market comparison is reportable at all unless the WERs sit within a stated band;
and the **text-only control run** of the same scenario is the only way to attribute
the gap, because text score minus voice score *is* the speech-engine contribution.
Without the control, the attribution is a guess. With it, it is arithmetic.

And the instrument that protects against the bias needs its own guard: in a verified
round trip in this repo, recognition was **perfect** and the score was roughly **50%
word error**, because the two sides format numerals differently
([`WER_NORMALISATION.md`](../lab/voice/engines/WER_NORMALISATION.md)). An unnormalised
WER gate would have condemned a flawless transcript.

### 7.2 Not measurable from a transcript, therefore not a KPI

Sincerity. Rapport. Confidence. Intent. Whether the customer trusted the adviser.
Whether the sale was right for this customer's life, as distinct from right against
the register. Each of these is a real thing a manager cares about and none is
recoverable from a Trace, so a KPI claiming to measure one is measuring a proxy it has
not named.

### 7.3 Politeness, and anything scored on Anglophone cues

Japanese keigo, Korean speech levels, Vietnamese kinship address, Indonesian
Bapak/Ibu and the grammaticalised T-V distinction in four European languages all
encode what a scorer routinely grades as "professionalism". A model scoring politeness
on Anglophone cues has no access to the signal (`markets_languages.md` §3.4). LL-2
therefore grades register **stability**, which is measurable without a cultural
oracle. The politeness version must not be built.

### 7.4 Imported thresholds

The vendor call-analytics figures — a 43:57 talk-to-listen ratio, a 76-second
monologue ceiling, 11–14 discovery questions — are published without methodology,
denominators or corpus definition, on a vendor blog, from B2B technology sales
(`call_craft.md` A-18). Directionally interesting; unusable as thresholds here. If
this scorecard wants a talk-ratio metric it measures its own distribution and reports
it with its denominator.

The same file records why this matters: a claim that implication questions are "the
single highest predictor of close rate in deals above \$50K ACV" circulates attached
to a study that predates the ACV framing entirely (S-50). A real study, a real
finding, and a fabricated precision bolted on during a decade of re-summarisation. The
test any number here must pass: *did the study that supposedly produced this have the
vocabulary to express it?*

### 7.5 Anything proxying a protected characteristic

Age, gender, name origin, education level, non-native fluency. One trap deserves
naming because it looks like a citation rather than a bias: MAS's selected-client test
asks whether the client is under 62, whether they have language proficiency, and
whether they hold an 'O'/'N' Level qualification (`regulators.md` D6). Those are
**procedural triggers for extra customer protection**, and they are questions about
the *customer*. Using any of them as an input to the *adviser's* score inverts the
purpose of the rule and imports a protected characteristic into an employment
decision. They belong in CG-5's applicability condition and nowhere else on this
scorecard.

### 7.6 The sales outcome itself

Putting conversion in the scorecard collapses the entire ladder. The scorecard grades
behaviour precisely because behaviour is what coaching can change; grading the outcome
re-creates the sales-driven scorecard that MAS's Balanced Scorecard framework exists
to displace — four **non-sales** KPIs, independently audited, feeding variable income
(S-01). A coaching scorecard that grades outcomes is a sales target with a compliance
decoration.

The same argument rules out speed as a graded behaviour. Time-to-first-sale is a
lagging business metric and it is the metric CE-1 is in tension with (§5.7). A KPI
cannot be its own business metric.

### 7.7 Anything that moves without the adviser moving

Two specific exclusions, both already visible in this repo.

**The cohort curve.** `roleplay/scorer.py` DEFECT-1 applies a cohort-target
adjustment to *individual* scores, so identical performance grades differently
depending on what the service graded before it. The idea is defensible — firms do
steer certification rates — but a curve is a property of the **cohort report**, never
of an individual's certification. A certification that depends on queue position is
not a certification.

**Single-session deltas.** "Improved since last session" is not a KPI. The measured
flake band in this repo, holding the agent still, was **7/8 stable-pass at one caller
turn budget and 5/8 at another**, and two independent draws of the same eight
scenarios disagreed about which rows were flaky (1/8 versus 3/8)
(`fixtures/live_caller/flake_band.json`, `flake_band_budget8.json`). The honest
reading is *low tens of percent, no more precisely than that.* A session-over-session
delta smaller than that band is noise with a narrative attached.

### 7.8 And one thing that must not be *reported* as an adviser failure

Before any KPI failure is attributed to a person, the harness must be cleared. This
repo has the case on record: the simulator appended its hang-up sentinel to the turn
carrying the caller's **final answer**, which ended the call on the caller's own turn,
denied the agent the turn it needed to act, and then failed it for not acting. Two
runs in forty. Fixing it moved the flake band from 2/40 to 1/40
(`lab/simulator/driver.py`, `_split_sentinel`).

The failure was real, reproducible, and entirely the instrument's. The shape of KPI
that bug fabricates is exactly the shape of DI-2 (the answer was never used) and CL-1
(the business was never asked for) — a "did not act on the information" finding. Any
such failure gets the harness checked before it gets a coaching recommendation
attached to it.

---

## 8. What this scorecard is not

- **Not validated against outcomes.** Every behaviour→metric link in §2 is a
  hypothesis (`call_craft.md` A-01; `regulators.md` §10 item 9). The platform's own
  outcome data is what would test them, and the honest presentation is a hypothesis
  register with a study design attached, not a claim.
- **Not built on a corpus of the calls it grades.** Every behavioural dataset behind
  §2 is either B2B technology sales or B2B organisational cold calls. No published
  corpus of regulated retail advisory calls exists, which is unsurprising — they are
  recorded under obligations that prevent publication. The **mechanisms** transfer;
  the **magnitudes** are unknown (`call_craft.md` §12, A-02, A-07).
- **Not reachable at full strength today.** Seven KPIs name judges that do not exist
  yet, and none may gate before its calibration report clears
  `lab.judges.require_calibrated`. Six of the eight gates run deterministically now;
  the two judge-primary gates run on their declared fallbacks, and the registry
  refuses to hold a judge-primary gate that has none.
- **Not precise enough to rank two advisers who are close.** See §7.7. A difference
  smaller than the measured flake band is not a difference.
- **Not a compliance opinion.** Two of the most load-bearing regulatory sentences
  behind CE-1 and CE-3 are V3, four MAS claims are V4 because the regulator's own
  site was unreachable, and both research files name what they did not read. Nothing
  here should reach a compliance function without those being re-fetched from primary
  sources first.
