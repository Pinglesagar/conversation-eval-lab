# A test strategy for an enterprise advisory coaching platform

*Five product surfaces, twenty-four markets, four regulatory regimes, and a
certification decision about real people.*

---

This is the engineering argument behind the two artefacts committed beside it:
[`docs/SCORECARD.md`](SCORECARD.md) — twenty-eight behavioural KPIs, each laddered to
a business metric, each sourced or labelled an assumption — and
[`scenarios/advisory/`](../scenarios/advisory/) — eighteen scenarios built so a
specific failure is *possible*, plus the four regulatory registers they are graded
against.

Written for someone deciding whether to staff this. §0 is the part I would read first if
somebody handed it to me. **If you have fifteen minutes rather than thirty: §0, §3, §4.1
and §5** — the assumptions, the scorecard, the proof that one compliance checker cannot
serve four regimes, and the measured evidence that the eval itself can be trusted.

**The claim.** A coaching platform is sold on business outcomes — conversion, product
penetration, time-to-first-sale — and can only change behaviour. So the testable
object is behaviour, every KPI must name the business metric it is a *leading
indicator* for, and the honest form of that link is a hypothesis with a study design
attached rather than a number. Everything below follows from taking that seriously.

---

## 0. Assumptions and limitations, before anything else

First on purpose. The rest is only worth reading if you believe this part, and the
fastest way to find out is to put it where you cannot skip it.

**The market list is inferred.** The product category publishes a count (24), three
regions and three hub cities — not the list. The twenty-four rows in
[`markets_languages.md` §1](_research/markets_languages.md) are reconstructed from
hub cities, the four named regulators, the named use cases, and where BFSI advisory
*distribution headcount* is actually large. That file states plainly that the tail is
wrong by construction, that each regulator name in rows 5–24 is an assumption at the
level of "this is the right body for advisory conduct", and that the Gulf rows sit
outside the vendor's own stated region taxonomy. **The corpus does not need the right
24 names.** It needs the right set of *things that vary* — script, digit grouping,
refusal directness, honorific system, prescribed-wording regime, speech-vendor
coverage — and every one of those varies inside the inferred list the same way it
varies inside the real one.

**The registers are reconstructed from public regulatory sources, not from any firm's
compliance system.** The thirty-six entries in
[`scenarios/advisory/registers/`](../scenarios/advisory/registers/) carry
paragraph-level citations to MAS notices, the FCA Handbook, 17 CFR 240.15l-1 and the
SFC/IA codes. They are not a compliance opinion and no compliance function has seen
them.

**The rubric is a reasonable reconstruction, not anyone's real scorecard.** No firm's
certification standard was consulted. What is defensible is the *shape* — observable
behaviour, named business metric, gates counted rather than averaged, every rate with
a denominator.

**The corpus is synthetic**, and had to be: no published corpus of regulated retail
advisory calls exists, unsurprisingly, since they are recorded under obligations that
prevent publication. Every behavioural dataset behind the scorecard is B2B technology
sales or B2B organisational cold calls. The *mechanisms* transfer; the *magnitudes*
are unknown ([`call_craft.md` §12](_research/call_craft.md)).

**Some load-bearing claims are V3 — search-summary only.** The research pack grades
its own retrieval, V1 (primary text read in full) to V4 (commentary standing in for
an unreachable source). Two of its most quotable sentences are not V1 and are flagged
everywhere they are used: FCA PRIN 2A.5.9R, the "ask whether they understand" rule
that KPI CE-1 rests on, and HK IA GL28, the non-guaranteed-highlighting rule behind
CE-3. Four MAS claims are V4 because MAS's own site returned service-unavailable on
every attempt. Five of `regulators.md`'s sources are flagged `[retrieval: secondary]`
and carry that flag at every point of use — [HK-2], [HK-3], [US-3], [US-4]/[US-5]
and [FCA-13].
Nothing here should reach a compliance function before those are re-fetched.

**One widely-quoted industry statistic in this space is fabricated, and I can show
the seam.** Search results attribute to a well-known sales-research programme the
claim that implication questions are "the single highest predictor of close rate in
deals above \$50K ACV". The underlying data predates the term ACV and the SaaS
deal-size framing entirely. A real study, a real finding, and a fabricated precision
bolted on during a decade of blog re-summarisation
([`call_craft.md` §6.4](_research/call_craft.md), S-50). It is recorded as a warning
rather than quietly dropped, because it produced the test every other number here had
to pass: *did the study that supposedly produced this have the vocabulary to express
it?* Several vendor call-analytics figures that would have made attractive thresholds
— a 43:57 talk-to-listen ratio, a 76-second monologue ceiling, 11–14 discovery
questions — failed that test and are excluded.

**What is genuinely strong.** The regulatory divergences in §4 are sourced to
paragraph level and checkable by anyone with a browser. The numbers in §5 are
*measured in this repository*, with committed fixtures — and two of the five are
failures of my own work.

---

## 1. The problem, in the product's own terms

Five surfaces, each a different test problem:

| # | surface | why it is hard to test |
|---|---|---|
| 1 | **practice roleplay** — certifies an adviser as ready to sell | the output is an employment decision about a person |
| 2 | **knowledge chat** — answers from top performers, with citations | the authority is a peer, not a regulator |
| 3 | **live in-call support** — corrections and reminders mid-call | correctness has a deadline |
| 4 | **agentic outreach** — reaches and qualifies leads autonomously | a failure is a regulatory event, not a training artefact |
| 5 | **manager analytics** — "not just what they sold, but how and why" | it is where instrument bias becomes an HR decision |

Twenty-four markets. Four regulators named on the product's own materials — MAS, FCA
COBS, Reg BI, SFC/IA. 200,000+ advisers.

### 1.1 The combinatorics, concretely

The inferred market table names **52 market-language pairs** (plus roughly ten
unnamed Indian regional languages), across at least twenty-seven distinct advisory
languages and four scripts.

The register cannot be keyed on jurisdiction alone. `markets_languages.md` §3.2
Finding 1 shows why, with citations: in the UK, prescribed wording exists for one
product category and *explicitly does not* for another, so the real key is
**(jurisdiction, product category)**. Take four product families — retail investment
product, life policy, pension or rollover, non-exchange-traded derivative — and you
have on the order of **200 register keys** before a sentence of English is written.

Then twenty-eight KPIs per session. Seven name a judge, and one of those — LL-1, did
the adviser hear the refusal? — must be calibrated **per market**, and refuses to run
in a market with no committed calibration report, because pooling markets hides
exactly the disparity the KPI exists to find. That one rule implies, at full
coverage, **52 hand-labelled calibration sets for one KPI**.

```
5 surfaces × 52 market-language pairs × 4 product families × 28 KPIs
```

Five to six thousand distinct cells, some hundreds of which need human labels before
any model verdict in them is admissible. That is not a matrix you cover. It is a
matrix you sample from deliberately and report the sampling of.

### 1.2 What breaks when a prompt changes

Change the roleplay customer's system prompt — soften the persona, add a
market-specific instruction, upgrade the base model — and *every* cell above is
potentially affected, because the customer's turns are the input to every KPI. The
adviser is no longer being examined against the same examination. Nothing errors. Every
dashboard still renders.

Three failure shapes, all of which this repository has produced and §5 measures with
numbers: **the detector goes blind** (every deterministic check answers "is this an
instance of the thing I am looking for" with a pattern — exact and free against a
scripted counterpart, close to worthless against a model, and a blind detector reports
green); **the harness blames the product** (a simulator bug denied the agent the turn it
needed, then failed it for not acting, producing a finding shaped exactly like a real KPI
failure); and **the instrument moves under the score** (change the speech vendor and
every voice score in every market changes, with no behaviour changed).

### 1.3 Why manual review cannot cover it

Certification alone: 200,000 advisers, one review each per quarter, ten-turn sessions —
**800,000 human reviews a year** before any re-certification or market launch. And the
output is not reusable: a prompt change on Monday invalidates Friday's reviews, because
a review is a judgement about a session the new prompt would not have produced.

Manual review is nonetheless indispensable in one role: it is the label supply. Every
judge below is inadmissible until a human-labelled set exists. The distinction is between
*review as throughput*, which cannot scale, and *review as calibration*, which is the
only thing that makes the scaled path trustworthy.

---

## 2. Surface by surface

For each: what goes wrong, what is deterministic, what needs a calibrated judge, and
— the part people leave out — what cannot be tested this way at all.

### 2.1 Practice roleplay (certification)

**What goes wrong.** The scorer rewards the wrong thing and the adviser learns it.
`roleplay/scorer.py` scores mandatory disclosure on keyword presence, so *"there is no
real risk to your capital here"* — containing `risk` and `capital` — scores full marks
on a session whose disclosure register is empty. The same instrument fails in the
other direction: its shadow keyword list is English, so a correctly disclosed Spanish
session scores zero. Both directions are quantified against the register by
`roleplay.register.compare_with_keyword_check`, which is why the gap is a number here
rather than a warning.

**Deterministic — more than people expect, and this is the strategic point of the
design.**

- *The disclosure set* — one recorded event per required code, keyed by (jurisdiction,
  code, language), read off a ledger rather than scanned out of a transcript.
- *Order and simultaneity* — A-before-B over event positions. `regulators.md` §7
  enumerates twenty-four order-or-timing requirements with citations, two of them MAS
  **simultaneity** rules (an oral past-performance statement is permitted only if the
  written disclosure is provided at the same time). None needs a clock.
- *Prescribed units and numbers* — the SFC's percentage ceiling rounded up to the whole
  point; 30 / 21 / 14 days with three different start triggers. A prescribed number is a
  prescribed unit, and units are checkable far more reliably than sentences.
- *Fact-find coverage* against the register's required set — judge-free,
  denominator-safe, unfakeable: you cannot fake a field you did not obtain.
- *Propagation* — a later adviser turn carrying the content of an earlier answer. The
  hardest KPI here to game, because faking it means actually using the answer.
- *Second-raise rate* — the numerator is the **customer's** behaviour, so it needs no
  oracle and cannot be gamed by an adviser asserting a resolution.

**Needs a calibrated judge.** Seven KPIs and no more: resistance response (was a block
answered as a block or as a stall?), fact-find steering, objection engagement, clause
explanation, licensing boundary, close pressure, refusal taxonomy. Each has a
deterministic floor that runs today, and the registry **refuses at import** to hold a
judge-primary gate with no fallback.

The one to look at is clause explanation. Minimisation — *"technically there is a
waiting period, but in practice nobody…"* — is the highest-severity failure in the
clause family and it *passes a keyword scorer by construction*, because the entire
disclosure vocabulary is present. Presence detection cannot express it. The detector has
to be positional: adjacency between a limitation turn and a minimiser, with the absence
of an intervening understanding-check as the discriminator.

**Cannot be tested this way.** Whether certification predicts real-call performance —
that needs outcome data joined to certification records; it is a study, not a suite.
Whether the simulated customer resembles a real one: no corpus to check against. And
sincerity, rapport, confidence, intent, whether the customer trusted the adviser,
whether the sale was right for this customer's *life* as distinct from right against the
register — each is real, none is recoverable from a trace, and a KPI claiming to measure
one is measuring an unnamed proxy.

### 2.2 Knowledge chat with source citations

A retrieval surface, and this repository already implements the metrics with their
denominators written down: [`ragcheck/`](../ragcheck/), documented in
[`RAG_NOTES.md`](RAG_NOTES.md). Three failures there, each invisible to the other two
and each pinned by a test: *retrieval perfect, answer wrong* (right passage at rank 1,
wrong figure in the answer — recall@3 1/1, groundedness **1/2**, so a retrieval-only
suite passes it while the customer is quoted a figure two-thirds too high); *grounded
and useless* (groundedness **2/2**, answer relevance **fail** — faithfulness cannot see
the wrong question); *faithful, relevant, incomplete* (context recall **1/2**, and only
a metric measured against a written reference names the retrieval team as the owner).

**Deterministic.** Citation integrity is the cheap win: does the cited document exist,
does the cited span contain the claim, is the cited version current, and — the
domain-specific one — is the citation **in the asker's regime**? A citation to a correct
FCA paragraph is a defect when the asker is in Singapore. Plus recall@k, precision@k,
MRR, nDCG and context recall against written references. `recall@k` is a **ceiling** on
every generation metric — a fact absent from the context cannot appear in a grounded
answer — so when groundedness drops, the first question is whether retrieval moved, and
that is answerable offline, exactly, before a token is spent.

**Needs a judge.** Groundedness, answer relevance, and the passage judge behind judged
context precision. `RAG_NOTES.md` §5 treats the grader as the weakest component and
measures it, which is the right default.

**Cannot be tested this way — and this is the surface's real risk.** The knowledge base
answers *from top performers*, and a top performer's technique is not validated against
a regulator. The research locates the exact seam: on a conversion-only scorer, **false
urgency is the single most-rewarded non-compliant move in the entire taxonomy**
(`call_craft.md` C-6, C-11) — while Reg BI requires firms to *eliminate*, not manage, the
sales contests and quotas that create it. A corpus distilled from the highest converters
will encode the highest-converting non-compliant moves, and it will look authoritative
because it *is* sourced — to a person. No retrieval metric detects that. What detects it
is running the retrieved answers through the compliance gates of §3, per regime, as a
corpus: the knowledge base needs the same register the roleplay surface is graded
against, and an answer that fails a gate does not get served in that market.

### 2.3 Live in-call support — where timeliness *is* correctness

The surface nothing off the shelf addresses, because the metric shape is different.

**A right suggestion delivered after the moment has passed is a failure, not a slow
success.** An LLM eval framework measures whether the output was correct and reports
latency separately, in a different table. Here they are the same number. If the adviser
has already quoted the projected maturity value, attached it to the customer's
retirement plan, repeated it and moved on, then a reminder to state the guaranteed
floor is not a late correct answer — the breach is complete, and prompting now produces
a confusing retraction to a customer who has already formed the belief.

**So the unit of measurement is the window of actionability, and it is conversational,
not temporal.**

| correction | window closes when | source |
|---|---|---|
| state the guaranteed figure | the projection has been repeated and attached to the customer's own goal | HK IA GL28 |
| the fact-find is incomplete | the first advice-classified turn begins | COBS 9A.2.13R |
| charging structure not yet given | the ask for the business | COBS 6.1A.17R / 6.1A.24R |
| this is a selected client | the sales and advisory process proceeds | MAS-3 ¶10A–¶10D |
| the customer just refused | the adviser's next turn | LL-1 |

Which yields four outcomes where naive testing has two: **right and inside the
window** (the only pass); **right and outside it** — a *failure*, reported in the same
column as a wrong answer, never in a latency table; **absent**; and **right, in the
window, and wrong for this regime**, which is the divergence failure a single
compliance checker produces by construction (§4).

**Deterministic:** almost all of it. Window boundaries are event positions in the
trace, which is what a phrase-plus-ordering contract already decides on, and whether a
suggestion landed before the boundary is arithmetic over timestamps monotonic from
session start. This repository already runs a timing gate on the same primitive: p50
717 ms, p95 1104 ms over 175 turn samples, PASS.

**Needs a judge:** whether the correction was factually right, and whether it was the
*most useful* one available when several fired at once. Not the second one first — a
surface that fires four reminders into one turn has failed in a way no per-reminder
metric shows, and the metric for that is a rate (corrections per adviser turn, with its
denominator), not a judgement.

**Cannot be tested this way:** whether the adviser could actually *use* it. A correction
delivered inside its window to someone mid-sentence, looking at a customer, is a
human-factors question; no trace contains the answer. Same for barge-in on the voice
path.

**And one thing this surface makes worse that nobody expects.** Live support reads
speech-to-text output. Five commercial ASR systems transcribing matched structured
interviews averaged **WER 0.35 for Black speakers against 0.19 for white speakers**,
matched for age and gender (`markets_languages.md` §3.6 `[S18]`). On the *scoring*
surfaces that is a measurement-validity problem. Here it is a differential-service
problem: the adviser with the accent gets worse in-call help, in real time, on real
customers. That belongs in the test plan as a per-accent correction-recall table, not in
a fairness appendix.

### 2.4 Agentic outreach

**What goes wrong:** the economics of failure invert. Elsewhere a bad output costs a
training session. Here it reaches a member of the public, in a regulated market,
unsupervised, at volume.

**Deterministic, and nearly all of it must live here.** Consent and do-not-contact
ledgers; identity disclosure; whether the entity is licensed in the market being
contacted; whether the exchange stayed on the qualification side of the licensing
boundary; the record-keeping and call-recording obligations `regulators.md` §2.9 and
§3.8 enumerate. All ledger arithmetic, all cheap, and all of it must gate **pre-send**.
A dashboard reporting yesterday's breaches is not a control on this surface.

**Needs a judge:** whether an exchange amounted to a *recommendation*. Reg BI's boundary
is a gradient the SEC describes as "not susceptible to a bright line definition",
assessed on call-to-action and tailoring (`regulators.md` §4.3, D10) — and a
qualification script that tailors is closer to that line than its author thinks. Ship it
judged conservatively, with a human on the flagged tail, and say so in the release note.

**Cannot be tested this way:** volume and reputational effects; whether the qualified
leads are real. Plus the second-order effect the research does cover: an autonomous
agent that cannot recognise a **conventional-indirect refusal** will keep contacting
someone who has already declined. Japanese speakers overwhelmingly prefer indirect
refusal strategies, with unfinished sentences a conventionalised polite refusal, and
Chinese and American respondents differ materially in directness
(`markets_languages.md` §3.4). Mis-labelling that is simultaneously a corrupted pipeline
metric and a harassment risk — and it is why LL-1's calibration is per market and never
pooled.

### 2.5 Manager analytics

**What goes wrong:** an instrument artefact becomes an employment decision. Two named
defects already sit in this repository as seeded examples. **The cohort curve** —
`roleplay/scorer.py` applies a cohort-target adjustment to *individual* scores, so
identical performance grades differently depending on what was graded before it. The
idea is defensible, firms do steer certification rates, but a curve is a property of the
**cohort report**, never of an individual's certification: a certification that depends
on queue position is not a certification. And **the single number** — managers want one
and cannot have one, because a single number is only definable if gates are weighted,
and a weighted gate is not a gate.

**Deterministic, all of it**, because analytics is an aggregation layer and its defects
are aggregation defects: every rate prints `n/N`; `0/0` is reported as no denominator
and never as 100% (otherwise the most evasive session — the adviser who never let an
objection surface — becomes the highest-scoring one); second-raise rate is per persona
and never pooled, since an aggressive persona re-raises by construction; cross-market
comparison of a closing KPI is refused unless stratified by jurisdiction, because the
same transcript is a pass under Reg BI and a breach under both the FCA and MAS; and no
behavioural score from a voice session is reportable without that session's word error
rate beside it.

The method is adversarial aggregation: feed synthetic cohorts with known properties and
assert the dashboard **cannot** render the wrong number.
`test_a_suppressed_call_survival_group_shrinks_the_denominator_rather_than_scoring_zero`
is one such test. **No judge belongs here** — any judge already ran upstream, and
re-judging an aggregate is how a calibration gets laundered.

---

## 3. The scorecard

Full version: [`SCORECARD.md`](SCORECARD.md). Machine-readable and self-validating:
[`roleplay/scorecard.py`](../roleplay/scorecard.py).

### 3.1 The ladder, which is the whole argument

Every KPI is written as

> **observable behaviour → the business metric it is a leading indicator for**

and a behaviour that cannot complete that sentence is not on the scorecard.
`business_metric` is a required field over a closed vocabulary and `_validate()`
**raises at import** on a row without one. That constraint is what makes this a
proposal rather than a QA checklist: it forces every row to say what it is *for*.

| group | asks | ladders to |
|---|---|---|
| **CS** call survival | did the adviser earn the right to continue? | conversion, time-to-first-sale, active ratio |
| **DI** discovery | needs analysis, or questionnaire? | product penetration |
| **OH** objection handling | engaged with, or acknowledged and abandoned? | conversion |
| **CE** clause explanation | explained, recited, or **understated**? | penetration; mis-selling exposure |
| **CG** compliance gates | was this session lawful in this market? | licence to operate — **gates, never a score** |
| **CL** closing | did the adviser ask, and was the ask honest? | conversion |
| **LL** language and locale | does any of the above survive a change of market? | conversion, readiness, licence to operate |

19 scored (53 points), 8 gates, 1 diagnostic; CS 4, DI 4, OH 3, CE 4, CG 5, CL 4,
LL 4. The design budget is 3–5 per group and the registry enforces the **ceiling** as
hard as the floor, because a scorecard nobody reads all of certifies nobody.

**Each link in the ladder is a hypothesis about mechanism, not a measured causal
fact.** No regulator publishes conversion data and none of the sales-research sources
ran the experiment. The honest form is: *this is the behaviour we assert conversion
depends on, here is how we detect its absence, here is the study we would run to test
the assertion.* Twenty-eight hypotheses with detectors attached is a research
programme the platform's own outcome data could settle in a quarter — and that, rather
than any number in this document, is the strongest thing the ladder offers.

The best external validation of the framing is not a sales blog; it is a regulator
having already built it. MAS's Balanced Scorecard framework grades representatives
against four **non-sales** KPIs — understanding the client's needs, suitability of
recommendations, adequacy of information disclosure, standards of professionalism —
audited by an independent sales audit unit sampling real transactions, feeding variable
income (`call_craft.md` S-01, and it is **V4**: MAS primary text was unreachable, so this
is commentary standing in for a notice).

### 3.2 Gates are counted, never averaged — structurally

A compliance requirement that contributes to a total is a heavily-weighted criterion,
not a requirement, and a session with a missing disclosure will eventually pass by
being charming elsewhere. So a GATE row carries **zero points**, which means the total
*cannot* include one. There is no filter to forget: `points_available()` sums
`max_points` unfiltered, which is only safe because the validator refuses any non-SCORE
row that carries points.

A session's result is therefore **two figures, always together**: points over points
available, and gates passed over gates applicable. There is no single number, and the
manager dashboard does not get one.

### 3.3 The conflict map, which is the other half of the centrepiece

Without it, an adviser games one axis and the scorecard rewards it. Nine pairs are
mapped in `SCORECARD.md` §5; four are worth stating here.

**Persistence against pressure.** CS-3 rewards declining a deferral and proposing a
nearer concrete step; CL-4 fails a session for pushing after a decline. Same behaviour,
different points in a sequence, so the resolution is positional — CS-3 is available on
the first resistance event and not after an explicit decline. The mechanism matters
more than the ruling: **when a gate fails, the scored KPIs that the gate-failing
behaviour earned are zeroed, not kept.** Otherwise pressure banks persistence points on
a failed session and the dashboard reads "strong objection handling" next to a mis-sell.

**Discovery coverage against the time-bounded ask.** CS-2 rewards "I'll only take two
minutes"; DI-1 wants a nine-item fact-find; DI-4 makes shortening it a named breach
(COBS 9A.2.11R — a firm must not *encourage a client not to provide* the information;
9A.2.13R — without it, the firm must not recommend). The most useful row in the section,
because the resolution is not to pick a side: the compliant behaviour is a **third** one
neither KPI described — re-contract the time out loud. *"This needs longer than two
minutes; can we book fifteen?"* satisfies CS-2, preserves DI-1, and does not touch
DI-4. A map that only ranked the two would have taught advisers to choose between a rule
and a score.

**Call survival against the duty to end the call.** On a vulnerability signal in a
Singapore session, the required action is to *not proceed* without a qualifying trusted
individual or a written declination. Ending the call is correct. Every call-survival KPI
rewards continuing. Scoring 0/9 trains the wrong behaviour and scoring 9/9 hides the
gap — so the group becomes **inapplicable and leaves the denominator**, points available
fall by nine, the threshold falls with them, and the report prints the reduced
denominator. Both naive readings punish an adviser for doing the right thing, and only
an explicit rule prevents it.

**Understanding checks against time-to-first-sale.** Every check costs turns, and one of
the metrics this product category advertises is 75% faster time-to-first-sale. This is
the one conflict where an advertised growth metric is in tension with a behaviour a
regulator states as a *turn*. The gate wins — but the honest version of the row is that
the platform should say out loud which of the two it optimises, because a coaching
product that quietly optimises the advertised metric **will coach the check away**.

### 3.4 What must not be a KPI

`SCORECARD.md` §7 in full. The framing that makes it necessary: **this scorecard
certifies people**, so a certification decision with employment consequences is an
employment decision, and every property of the score inherits that. A score correlated
with accent, dialect or non-native fluency is a **discrimination risk dressed as a
quality metric**, and the mechanism is not malice — it is a word error rate nobody
printed next to the score.

One trap deserves naming because it looks like a citation rather than a bias. MAS's
selected-client test asks whether the client is under 62, whether they have language
proficiency, and whether they hold an 'O'/'N' Level qualification. Those are procedural
triggers for extra *customer* protection, and they are questions about the **customer**.
Using any of them as an input to the **adviser's** score inverts the purpose of the rule
and imports a protected characteristic into an employment decision.

Also excluded, with reasons: **politeness**, and anything scored on Anglophone cues —
keigo, Korean speech levels, Vietnamese kinship address and the T-V distinction in four
European languages all encode what a scorer routinely grades as "professionalism", and a
model scoring politeness on Anglophone cues has no access to the signal, so the KPI
grades register *stability* instead, which is measurable without a cultural oracle;
imported vendor thresholds (§0); the **sales outcome itself**, since grading the outcome
re-creates the sales-driven scorecard MAS's framework exists to displace; and
single-session deltas, because the measured flake band (§5.5) is larger than any delta
anyone wants to report.

---

## 4. The corpus

Eighteen scenarios, six suites, loaded by the *existing* roleplay loader — no fork, no
new package. Nine personas, each carrying the real hidden motivation as a
`concern.reveal`. Thirty-six enumerated register requirements across four regimes.
Seven rows expect a pass, eleven a fail.

**The design principle: a row exists to make a specific failure possible.** Not to add
coverage, not to add volume. Every row's notes must contain the literal string
`"Why not a simpler row:"`, and a test asserts it — which is how an eighteen-row budget
is defended against the drift toward two hundred rows nobody reads.

| suite | n | rows |
|---|---|---|
| **divergence** | 5 | commission volunteered, four verdicts on one sentence · two products in one Singapore meeting, two commission standards · verbal close with nothing in writing · cooling-off duration **and** trigger · Hong Kong, where failing to advise is the breach |
| **nearmiss** | 3 | disclosure complete and correct, one turn *after* the ask · the risk warning delivered to the partner rather than the customer · the prescribed term said once, inside 140 words of firm biography |
| **clause** | 3 | surrender value **explained / recited / understated**, sharing three trainee turns verbatim |
| **conflict** | 3 | "what would you do", answered · the customer's own permission to skip the fact-find · a vulnerable customer keen to sign |
| **survival** | 2 | hostile opening, decoupled · the graceful exit that **must** score a pass |
| **lang** | 2 | an English risk warning inside a Cantonese sentence · an indirect refusal filed as still in play |

Two properties worth noticing. **The clause trio is a controlled comparison**: all three
share the first three trainee turns *verbatim* and differ only in how the surrender
clause is handled, so a verdict difference between them is attributable to the clause
turn and nothing else. That is how you test a judge rather than exercise one. And **the
graceful exit must pass**: a corpus of eleven failures teaches a scorer that severity is
the answer, so one row exists where an adviser who shrinks the ask, is declined, closes
cleanly with no fourth attempt and leaves the customer better off than before the call
scores a pass. Any change that makes that row red is a regression regardless of what it
fixed.

### 4.1 The divergence rows, which are the proof

**One compliance checker cannot serve four regimes.** Not "is hard to build for" —
cannot.

**The flagship.** *"There's no charge to you — the provider pays us a commission, which
is 3% of what you invest."* One sentence:

| regime | verdict | why |
|---|---|---|
| **FCA** | **fail** | a firm giving a personal recommendation on a retail investment product may be remunerated only by adviser charges and must not solicit or accept provider commission — so this *describes a prohibited arrangement* rather than disclosing a permitted one (COBS 6.1A.4R) |
| **MAS** | pass | lawful; the duty is to disclose the *amount*, and 3% of the sum invested is an amount (MAS-2 ¶18) |
| **SFC/IA** | pass | lawful with disclosure as a percentage ceiling of the investment amount per transaction, rounded up to the whole percentage point — and 3% is already a whole point, so the prescribed unit is satisfied by an adviser who was not thinking about units at all (HK-1 ¶8.3) |
| **Reg BI** | pass | over-satisfied; standardised ranges suffice |

The distribution is **3–1, not 1–1**, which is why the row carries four verdicts rather
than two: a majority-vote or pooled-market compliance score gets it wrong by
construction, and the regime where the sentence is most *reassuring* is the regime where
it is a confession. A row reading "commission disclosed: yes/no" scores the FCA breach
as a strength.

**The intra-conversation one.** A Singapore adviser recommending a unit trust and a
whole-of-life policy in one meeting owes **two different disclosure standards in the
same conversation**: an amount on the unit trust (MAS-2 ¶18), and for the life policy
only the distribution-cost item in the illustration, with no duty to disclose the amount
and type of the adviser's own remuneration (MAS-2 ¶22, MAS-4 ¶4). Declining to name a
figure on the policy is *the rule being followed*, not a gap. This is the only row in
the pack where saying **less** is compliant, and its `expected_failure` predicts that a
strict checker over-fires on it. If somebody later "fixes" commission checking by
requiring an amount everywhere, that row goes red — and it should.

**The one that inverts the detector.** Every plausible unlicensed-advice check — in this
repository and in the product category — fires when an adviser *gives* a recommendation.
In Hong Kong, for a non-exchange-traded derivative where there was no solicitation and
the client has no derivatives knowledge, the intermediary must warn the client, **advise
on suitability** and keep records (HK-1 ¶5.1A(b)(ii)). The absence of a recommendation
is what triggers the duty, so declining to advise is the breach. A single-outcome
detector does not score this wrong — it has **nowhere to put the finding**, which is
strictly worse than a wrong answer, because it is invisible. Hence that gate carries two
outcomes.

**The one that stops the pack teaching the wrong lesson.** A flawless discovery, a
well-reasoned verbal recommendation and a close on the call is **compliant under Reg
BI** — no suitability report exists as a requirement there — and in breach under both
the FCA and MAS. Without this row, a scorer trained on UK and Singapore rows learns
"close without a document = fail", which is a market convention learned as a rule. The
Reg BI register file carries **four `not-required` entries** for exactly this reason: a
register that can only say "this is required here" makes a cross-market checker invent
requirements in the markets that do not have them. The absence has to be recorded to be
gradeable.

**And the one where the number is the easy half.** Cooling-off: FCA 30 calendar days
from conclusion of the contract or from notification of it, applying the **longest**
period where several apply; SFC/IA 21 days from delivery of the policy **or** issue of
the cooling-off notice, whichever is earlier; MAS at least 14 days from *receipt of the
policy*; US state-dependent and **unanswerable without naming the state**. *"You've got a
couple of weeks to change your mind, starting from when you sign"* is roughly right in
Singapore and wrong about the trigger, materially wrong in the UK and Hong Kong, and
unanswerable in the US. A checker keyed only on the duration is wrong about the half
that decides whether the customer still has the right when they try to use it.

### 4.2 One recorded lie, and why it is recorded rather than fixed

The code-switching row declares `language: en`. That is false — it is a Cantonese matrix
with English inserted clauses. `roleplay/register.py` keys registered phrasings by **one
language per session** and raises on unknown values, so there is no legal value meaning
"Cantonese matrix, English inserted clause". Writing `en` and documenting it as a defect
in both the persona and the row was the better of two bad options; the alternative was
planting a runtime landmine. The row's `expected_failure` predicts the register scores
it zero.

**Fixing it properly is a schema change, not a data change — and that is the finding.**
82% of transcribed segments in the Singapore/Malaysia corpus of record are neither
monolingual Mandarin nor monolingual English, and the two code-switching pairs that
matter most in this product category's two Asian hubs are **not** in the speech vendor's
supported code-switching set (`markets_languages.md` §3.1). A locale axis that only
swaps disclosure strings proves less than it looks.

### 4.3 The registers, computed rather than reasoned

Everything above §4.3 was written while the tables in §4.1 were **hand-reasoned**: the
registers were cited data, `expectation.human_verdict` and every per-regime block were
labels a person typed, and no code turned one into the other. `roleplay/regime_eval.py`
closes that gap. `RegimeEvaluator` reads the same thirty-six YAML entries, runs each row
through the existing roleplay adapter, and returns a verdict per regime with a status per
entry — `satisfied` / `missed` / `not-applicable` / `instrument-gap` — each carrying the
entry's own paragraph citation, so any verdict traces to a source.

One command: `make advisory-verdicts`, or
`python -m roleplay.regime_eval --divergence --shadow`.

| measurement | result |
|---|---|
| rows where the computed verdict matches the hand label | **16/18** (confusion: pass/pass 7, fail/fail 9, fail/pass 1, fail/undecidable 1) |
| divergence blocks producing **opposite** computed verdicts on one transcript | **6/6** |
| per-regime block verdicts reproduced on the entry each block names | **18/18** |
| the same, graded against that regime's **whole** register | **16/18** |
| rows the computed register fails and `RubricScorer` certifies | **4** |
| register entries a lax check over the same vocabulary over-credits | **3**, every one of them on **position** |
| rows a lax check passes outright that the register does not pass | **1/4** |
| register entries with a **reachable failure** — a real row or a hostile input that makes them miss | **31/31** non-carve-out entries; the 5 `not-required` carve-outs must never miss and are asserted separately |

### What each `kind` actually computes, and what decides nothing

All six kinds are computed, but they are not equally strong, and the difference
matters more than the agreement figure:

| kind | entries | what decides it |
|---|---|---|
| `prescribed-unit` | 6 | **5 of 6** do arithmetic on a parsed figure and, for the three cooling-off entries, check the start **trigger** as well as the number. The strongest instrument here. The sixth (MAS oral-performance) is a presence check for a simultaneous written artefact, which a transcript can only ever approximate. |
| `verbatim` | 3 | substring match on the prescribed form of words. **1 of 3** also decides on position; the other two carry a `timing` string the probe does not compute (see below). |
| `prohibition` | 2 | presence of the conduct, over 5 patterns each. Polarity is inverted, so disclosing it does not cure it. |
| `gate` | 4 | the precondition. **1 of 4** also has an explicit waiver pattern set; the other three are decided on presence of the required step. |
| `not-required` | 5 | decided before the transcript is read. **3 of the 5 are load-bearing**: reclassify them as substance requirements and the passing regime fails. |
| `substance` | 16 | presence and position over a pattern set — **9 of 16** decide on position against the recommendation or the call to action; the rest on presence, a custom decider, or refutation. The weakest kind, and the kind most of the register is. |

**Seventeen of the thirty-six entries carry a `timing` string that the probe's own
`position` field does not compute.** Several enforce ordering inside a custom decider
instead — the FCA cash-terms probe demonstrably does: the same figure satisfies
before the close and misses after it. Several more are legitimately not turn-ordering
rules at all, being a duration with a trigger, or adjacency to a figure rather than a
place in the call. But the gap is real, it produced one of the defects below, and it
is not visible from the YAML: **treat a `timing` field as documentation unless the
probe's printed `basis` says it is enforced.**

**Two limbs refute and never certify**, and they are marked as such in their own
output: FCA COBS 4.2.1R "fair, clear and not misleading" and SFC "reasonable in all
the circumstances". A clean result on either means *no breach detected*, not
confirmed compliance, and the naive control credits both by silence for the same
reason a keyword check credits a prohibition.

### What an adversarial pass found, and what was changed

The verdicts are computed rather than restated, and the sharpest evidence is a
**direction inversion**: rewrite `divergence-cooling-off-duration-and-trigger` from
fourteen days-from-receipt to thirty days-from-conclusion and the computed MAS
verdict goes pass→fail while the computed FCA verdict goes fail→pass, on hand labels
that never move. An evaluator reading the labels could not do that.

Two real defects were found by asking, of every entry, *is there any input that makes
this fail?*

- **`fca-fair-clear-not-misleading` could not fail.** It used a refutation-only
  decider with an empty pattern set, so it returned `satisfied` on every input
  including "this is risk-free and you cannot lose" — and it is the **only** engaged
  entry on both survival rows, so two of the seven pass/pass agreements rested on a
  check with no failing path. It now carries a cited urgency limb
  (`call_craft.md` §8 C-6, against this entry's own COBS 4.2.1R) and an
  ASSUMPTION-labelled misstated-risk limb.
- **`fca-restricted-advice-oral-disclosure` ignored its own `timing`.** The entry
  states "in good time before providing advice" and the probe graded presence alone,
  so the prescribed term said *after* the close satisfied it. It now decides on
  position against the recommendation landmark.
- **Fourteen declared waiver patterns decided nothing.** Three entries in three
  regimes declare the same pattern set for a customer purporting to discharge a duty
  the firm owes — "you have said you know the risks, so I will take you at your word".
  The waiver limb was read only under `kind: gate`, so that sentence failed the FCA
  gate at COBS 9A.2.13R and was **silently ignored** by Reg BI's care obligation and
  the SFC's "reasonable in all the circumstances", both of which are `kind:
  substance`. Same words, same shape of failure, two regimes passing. The waiver limb
  now runs whatever the kind, and a structural test refuses a probe that declares
  patterns nothing reads.

**None of the three fixes moved any of the numbers in the table above** — that is what
made them safe to make, and it is also the point: each closed a path by which the
instrument could have reported green for the wrong reason. Three tests now hold the
invariants: every non-carve-out entry must have a demonstrated failing input, the five
carve-outs must have none, and a declared pattern set must be reachable.

**The brittleness that remains is pattern coverage, and here is a concrete
instance.** The SFC percentage-ceiling probe recognises "three per cent of what you
invest" and "three per cent of the amount invested"; it does **not** recognise "three
per cent of the sum you invest", and returns *missed* — "remuneration discussed but
not as a percentage" — on a disclosure that is correct. The unit arithmetic is right
and the phrase list is short. This is the in-sample caveat with a face on it: recall
against wording these eighteen transcripts do not contain is **unmeasured**, and a
held-out set of paraphrases is the next thing this instrument needs.

**One divergence block diverges partly off its own axis.** All six blocks produce
opposite computed verdicts, and all 18 named-entry verdicts reproduce — but on
`divergence-commission-volunteered-four-verdicts` the named entry is *not* what
carries the MAS and SFC register-scope failures (a missing recommendation document,
and an unexplained complex product). Its entry-scoped 3-pass/1-fail split reproduces
exactly; its register-scoped split is 1-pass/3-fail. **Quote the entry-scoped figure
for that row**, and the register-scoped one as the wider, weaker claim it is.

**The agreement figure is in-sample and the CLI says so on every run.** The probes were
written with these eighteen transcripts in view. 16/18 is evidence that the register is
*computable* — that a cited paragraph can be turned into a decision procedure with its
assumptions written down — and is not a held-out accuracy. Every probe prints the
ASSUMPTION it rests on beside the sourced requirement it implements, because the
requirement is sourced and the pattern that looks for it is not.

**The two disagreements are the interesting part, and neither was tuned away.**
`nearmiss-warning-addressed-to-the-partner` returns **undecidable**: the understanding
check is present and the transcript shows it addressed to the customer's partner, so the
answer turns on a disclosure's *addressee*, and no field in the register, the evaluator or
the scorer records one. The hand label is right and the instrument declines to answer —
which is that row's own stated schema gap, now measured. `lang-indirect-refusal-recorded-
as-open` computes **pass**: the SFC register is genuinely clean, and the row fails on
reading an indirect refusal as an open outcome, which no register entry addresses. A
session verdict and a register verdict are different objects, and this is the one row
where they come apart.

**What `kind` buys, as a number rather than an argument.** A paraphrase of COBS
4.5A.10R's prescribed sentence *misses* the FCA entry and *satisfies* the MAS substance
counterpart, from the same words. Three per cent of the sum invested satisfies the SFC's
whole-percentage-point unit and one and a half per cent of the same sum does not, while
both satisfy MAS's amount requirement. Fourteen days from receipt of the policy is right
in Singapore and wrong in both the UK and Hong Kong — on the number *and* the trigger.
Each of those is pinned by a test on a synthetic trace whose timestamps are all
identical, so the positional rules can only be passing because they are decided on
event-stream position.

**Where a judge belongs, it is named and it does not gate.** Two entries have a limb no
pattern can decide — PRIN 2A.5.3R's "likely to be understood", the SFC's "reasonable in
all the circumstances". Both name the judge that would decide it, ask
`lab.judges.registry` for it, and record the answer ("not registered") as a residue on a
status decided by the deterministic limb alone. The same discipline covers the two
detectors `call_craft.md` labels ASSUMPTION: the minimisation-adjacency detector fires on
`clause-surrender-value-understated` and the prominence observation fires on
`nearmiss-restricted-advice-buried-in-a-long-turn`, and neither decides an entry, because
neither has a calibrated TNR. The row still fails; another entry carries it.

**And what the instrument cannot see, stated in its own output.** A transcript cannot show
that a document was provided, so a written-artefact limb is a printed residue rather than a
silent pass. An entry gated on a topic being raised cannot catch an adviser who never
raises it. Product class is detected over the whole transcript, so the two-product
Singapore row is graded on the union — which is exactly why the `not-required` carve-out
entry is load-bearing there rather than decorative.

---

## 5. How I would know the eval itself is trustworthy

Every number here was *measured in this repository*, with committed fixtures,
recomputable offline with no API key — and two of the five are failures of my own work.
That is the point. An eval suite that has never caught itself being wrong has not been
tested; it has been believed.

### 5.1 A judge the gate refused

`hallucinated_confirmation`, prompt v1, against 24 hand-labelled items (8 positives, 16
negatives, 11 of them deliberate near misses):

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 2 | FP 0 |
| **judge: pass** | **FN 6** | TN 16 |

TPR **0.250 (2/8)**. TNR **1.000 (16/16)**. Precision 1.000 (2/2). Raw agreement 0.750
(18/24). Cohen's kappa **0.308**. It **missed six of eight real failures** and
`lab.judges.require_calibrated` **refused it**. A revised prompt reached 1.000 (8/8) and
1.000 (16/16).

Two things about that table. Raw agreement of 75% is what a naive report would have
printed, and it flatters the judge because the data is imbalanced — always answering
with the majority class scores the majority fraction with zero discrimination. And
precision 1.000 is *perfect*, on two items. A judge can be perfectly precise and almost
entirely blind, and only reporting both rates with both denominators shows it.

### 5.2 The identical confusion matrix, three times

The *failing* prompt returned the **same** matrix — 2 / 0 / 6 / 16 — on three separate
runs (`verdicts_v1.jsonl`, `_run2`, `_run3`, all committed), because its two unstable
items sat on opposite sides of the diagonal and cancelled.

**Aggregate stability is not instrument stability.** Three identical matrices is exactly
what a stable judge looks like, and also what an unstable judge looks like when its
errors happen to balance. So a judge-detected KPI must publish **per-item verdict
stability**, not only a matrix — the cheapest piece of rigour in this document, because
the per-item data was already on disk.

### 5.3 The detector that went blind against paraphrase

`lab.checks.PromiseContract` — the most carefully reviewed literal-pattern set in the
codebase — measured in both directions:

| | scripted build | 30 recorded live conversations |
|---|---|---|
| before the paraphrase work | fires on every seeded case | **1 of 7** unbacked confirmations |
| after | unchanged | **7 of 7**, plus one the hand-written detector missed |

**Same defect class. The detector went blind when the words changed** — 86% blind to a
model's wording. Against the judge's 24 hand-labelled items it had scored a respectable
TPR 6/8 and TNR 14/16, so the hand-labelled set said the detector was fine and the live
traces said it was useless.

Four of the seven misses were ordinary synonyms. **Two were not a vocabulary problem at
all: the pattern was right and the punctuation was wrong**, because the patterns use an
ASCII apostrophe and the model types U+2019. Nothing about that is visible in a report.
The contract passes, the trace looks clean, and the defect is in the character set of
the pattern language.

The numbers are pinned in `tests/test_checks_paraphrase.py` so a pattern edit that drops
recall **fails the build** rather than a README. And the consequence for the scorecard is
a rule, not a caveat: **no phrase-list detector may gate.** The disclosure gate reads a
*ledger of recorded events*, never a phrase scan of the transcript. The honest limit is
in that file too — 8/8 and 16/16 on 24 items is consistent with true rates as low as 0.68
and 0.81 (95% Wilson lower bounds), so this is a floor under a known failure mode, not a
claim of correctness.

### 5.4 The harness bug that blamed the product

The simulator appended its hang-up sentinel to the turn carrying the caller's **final
answer** — *"Yes, please. My name is Ruth Kelleher. No allergies. [END OF CALL]"* —
ending the call on the caller's own turn, denying the agent the turn it needed to act,
then failing it for not acting. **Two runs in forty.** Fixing it moved the flake band
from 2/40 to 1/40 (`lab/simulator/driver.py`, `_split_sentinel`).

Real, reproducible, and entirely the instrument's. And the shape of KPI that bug
fabricates is exactly the shape of the two most important scored rows in the scorecard:
*the answer was never used*, and *the business was never asked for* — a "did not act on
the information" finding.

**Hence: before any KPI failure is attributed to a person, the harness must be
cleared.** On a surface that certifies people that is not process hygiene. It is the
difference between a coaching recommendation and a defamation.

### 5.5 The flake band, whose own two draws disagreed

Holding the agent still — scripted backend, fake clock, fresh state per repeat, fully
deterministic — with a live model playing the caller, 8 scenarios × k=5 repeats:

| caller turn budget | stable pass | flaky |
|---|---|---|
| 12 | **7/8** | 1/8 |
| 8 | **5/8** | 3/8 |

Two runs of the same eight rows, differing only in how many turns the caller was
allowed, **disagreed about which rows were flaky**. On the larger corpus — 47 rows ×
k=3 = 141 conversations, 2,056 recorded model calls, model in the agent, caller and
judge seats — the band was 6/47 (12.8%).

**The honest reading is "low tens of percent, no more precisely than that."** Which is a
finding with teeth, because it rules things out: any session-over-session delta smaller
than that band is noise with a narrative attached, and no dashboard should render
"improved since last session".

### 5.6 The instrument that was perfect and scored 50% wrong

A verified TTS→STT round trip:

```
reference    "Table for four at seven thirty, postcode S W one A one A A."
hypothesis   "Table for four at 07:30. Postcode SW1A1AA."   confidence 0.997
```

**Recognition was perfect** — every digit and letter correct. Word-level WER over those
two strings reports roughly **50% error**, because the two sides format numerals for
different purposes: one is what was spoken, the other is what a human would want to read.

Without normalisation this produces a specific, expensive, entirely wrong decision: the
digits-and-names rows — whose whole purpose is proving the system hears a postcode
correctly — report the *highest* error rate in the suite while the engine performs
flawlessly, and the suite is then used to argue for a vendor change that fixes nothing.
The rules are in [`WER_NORMALISATION.md`](../lab/voice/engines/WER_NORMALISATION.md).

And this matters beyond audio hygiene: WER is the instrument that protects against the
accent-disparity problem in §2.3 and §3.4, and an unnormalised WER gate would have
condemned a flawless transcript. **The guard needs its own guard.**

### 5.7 And the number that says how much all of this misses

On the case study, a human reading traces by hand found **13 failure modes across 32
coded occurrences**, of which the automated checks caught **9 of 31** product
occurrences. The suite is good at what it was told to look for and blind to the rest,
and that gap is why hand-coded error analysis is a committed part of this repository
rather than a paragraph about methodology.

That ratio is the most useful single number for sizing this work honestly. It is not an
argument against automation. It is the reason the labelling programme in §7 is the line
item I would defend hardest in a budget conversation.

---

## 6. Tiering and cost

### Tier 0 — scripted replay

Free. Seconds. Every commit. Byte-identical: 47/47 scenarios reproduced exactly at k=3,
and CI regenerates the reference run and diffs it byte for byte.

**And it is blind to prompt regressions. Say this plainly, because it is the sentence
most likely to be misunderstood in a procurement conversation: a scripted replay cannot
detect that the model got worse, because there is no model in it.** What it detects is
that the *harness* got worse — a check that stopped applying, a scenario that stopped
asserting, a contract that went vacuous, a report whose numbers no longer derive from
its own JSON. That is genuinely valuable and it is not what people think they bought. A
suite whose green light comes entirely from Tier 0 will be green through a model
downgrade.

Two Tier-0 properties earn their keep specifically here. **Absence is a first-class
result and it is not a pass**: a check that ran with nothing to assert on is `VACUOUS`,
counted separately, and a contract vacuous everywhere is reported as a gap rather than
green — which is how eval suites rot, with scenarios drifting, half the contracts
silently inapplicable, and the dashboard steady. And **the gate answers "did anything
change", not "is it correct"**: it fails on a new finding, on a finding that
*disappeared*, on a declared expectation that stopped reproducing, and on repeats that
were not identical. The middle two are the ones people leave out, and they are the same
case — from outside, a fixed defect and a check that quietly stopped applying are
indistinguishable.

**Trigger:** every commit, blocking.

### Tier 1 — live models

Real flake band, real cost. The reference live run was 47 rows × k=3 = **141
conversations and 2,056 recorded model calls**, with a model in the agent, caller and
judge seats, replayed offline from committed fixtures thereafter so nobody needs a key to
re-check it.

The only tier that can see a prompt regression, and it reads differently from Tier 0. A
verdict is a property of **k repeats**, not of a run: a probabilistic defect that fires
twice in three was being reported as both reproduced *and* a stale expectation in the
same run until staleness moved to the k level. Declared expectations name the build they
describe, because a seeded defect is a certainty in one build and a tendency in the other
— 6/6, 2/5 and 0/4 for three defects against 3/3 each under the scripted build. And a
live run is diffed against a *live* baseline, because comparing it to a scripted baseline
reports the difference between two builds as a regression.

The cost that dominates is not tokens. It is **human labels**: every judge is
inadmissible until a calibration report clears the gate, and LL-1 requires that per
market.

**Trigger:** any change to a prompt, a model version, a persona, a judge or the register
schema; every release candidate; plus a scheduled weekly draw so the flake band stays
measured rather than remembered. k ≥ 3 always, verdict read at the k level.

### Tier 2 — audio

Small on purpose: 8 of 55 rows in the existing corpus need the audio path. Audio is the
only tier that can test what audio uniquely determines, and nothing else — word error
rate per accent and language with raw and normalised named separately; the
code-switching cases, where the finding that matters most is a *coverage* fact rather
than a score (the two pairs central to this product category's two Asian hubs are not in
the speech vendor's supported set); barge-in and timing behaviour, which is the
live-support surface's real risk; and the **text-only control run** of the same scenario,
which is what makes the accent argument arithmetic instead of a suspicion. Text score
minus voice score, per market, *is* the speech-engine contribution, separated from the
adviser. Without the control, the attribution is a guess.

Audio is also the tier where naive scoring is *worse than none* — §5.6 is a perfect
transcript scored 50% wrong — so the entry price is the normalisation rules, not the
audio pipeline.

**Trigger:** speech-vendor change, new language or market launch, quarterly per-accent
WER table. Never per commit.

### What each tier costs and buys

| | Tier 0 scripted | Tier 1 live | Tier 2 audio |
|---|---|---|---|
| runs on | every commit | prompt/model change, RC, weekly | vendor change, market launch, quarterly |
| wall clock | seconds | tens of minutes | hours |
| marginal money | none | provider tokens (~2k calls per full draw) | tokens plus speech minutes |
| **dominant cost** | none | **human labels** | **human labels, per accent** |
| sees a prompt regression | **no** | yes | yes |
| sees a harness regression | **yes** | yes, but noisily | yes, most noisily |
| sees an instrument bias | no | partly | **yes** |
| verdict is | byte-identical or not | a band over k repeats | a rate with a stated WER band |

---

## 7. 30 / 60 / 90

### Day one: what I would ask the team for

1. **The real registers** — one firm's actual point-of-sale requirements for two
   markets, keyed by (jurisdiction, product category), with internal rule references.
   The reconstructed thirty-six entries are then replaced rather than extended, and the
   diff between reconstruction and reality is itself the most informative artefact of
   week one.
2. **The real rubric and the certification threshold** — what a manager actually sees,
   the pass mark, and who can override it.
3. **A hundred real sessions with the outcome attached** — not for training, but because
   twenty-eight behaviour-to-metric links are twenty-eight hypotheses and this is the
   only thing that tests them.
4. **Who owns a certification appeal.** If nobody does, the scorecard is not ready to
   certify, and that is a finding on day one rather than day ninety.

### Days 1–30 — instrument what needs no oracle

The bet is that most of the compliance value is deterministic, so it lands first and
cheaply. Stand up the **disclosure register as a ledger**, real for two markets, with the
keyword-comparison shadow beside it so the gap between "a ledger says so" and "the words
appeared" is a committed number from day one. Implement the **order, simultaneity and
prescribed-unit** checks — twenty-four ordering requirements with citations already exist,
and none needs a clock, a model or a label. Turn on **denominator safety and the
two-figure result** everywhere, and make the refusal to render a composite score a *test*
rather than a policy. Port the **flake-band measurement** to the real corpus and publish
the band before publishing any score. And run **error analysis by hand** on fifty real
sessions, counting what the automated checks caught: §5.7's 9/31 is the baseline shape to
expect, and the real ratio is what sizes the whole programme.

### Days 30–60 — earn the right to use a judge

Build **one** judge properly, end to end: `clause_explanation`, because minimisation is
the highest-severity failure that passes a keyword scorer, and because the clause trio in
§4 is a controlled comparison built to test exactly it. Label set first, then prompt.
Publish TPR, TNR, precision, raw agreement, kappa **and per-item verdict stability**
across at least three runs — §5.2 is why the last is not optional. Wire the calibration
**gate**, not a calibration report: a gate refuses, and being able to point at §5.1 is
worth more than a judge that passed. Measure the **paraphrase recall** of every
deterministic detector against live traces before anyone believes its zeros. And start the
**per-market label programme** for the refusal taxonomy in the two hub markets — the
long-lead item, and the thing still in progress at day 90.

### Days 60–90 — the surfaces where a failure reaches a customer

**Live in-call support**: window-of-actionability instrumentation, the four-outcome
metric, and a correction-recall table stratified by accent, with late-and-right in the
failure column. **Agentic outreach**: consent and licensing gates pre-send, the
recommendation boundary judged conservatively with a human on the flagged tail. **First
divergence report**: the same corpus graded under two regimes, published as two columns,
so the organisation sees for itself that one checker cannot serve both. **Audio,
narrowly**: WER by accent and language, raw and normalised named separately, with the
text-only control run.

### What I would leave alone

- **A single composite score.** Not "later" — never. It is only definable if gates are
  weighted, and a weighted gate is not a gate.
- **A politeness or professionalism judge.** The stability version is measurable; the
  politeness version should not be built.
- **Cohort-relative individual scores.** A curve belongs to the cohort report, never to
  a person's certification.
- **Breadth for its own sake.** Four European markets add a T-V distinction and a
  decimal comma, both already exercised elsewhere, and no new mechanism. That is breadth,
  not coverage. The corpus grows when a row makes a *new* failure possible.
- **Session-over-session deltas**, until the flake band is smaller than the delta anyone
  wants to report. Today it is not.

---

## 8. What would change my mind

- **If the real registers are keyed on jurisdiction alone** and the compliance function
  is comfortable with that, the (jurisdiction, product) argument in §1.1 is
  over-engineering and the divergence corpus is smaller than five rows.
- **If the platform's outcome data shows no relationship** between any of the
  twenty-eight behaviours and the metric it is laddered to, the ladder is decoration and
  the scorecard collapses to a compliance checklist — still worth having, and a much less
  interesting product.
- **If a judge can be calibrated above the human–human agreement ceiling** on the clause
  taxonomy, the deterministic-first bias in §2 is too conservative and more of the
  scorecard can be judged than I have assumed.

None of the three is answerable from outside the firm, which is the honest reason this
is a strategy rather than a result.
