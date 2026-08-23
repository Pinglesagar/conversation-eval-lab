# Call craft: what a good advisory seller observably does

Research note for the `roleplay/` corpus. This is the **selling** half of the domain,
not the compliance half. It exists because a coaching-platform eval suite written by
someone who has never listened to a sales call grades politeness and disclosure
vocabulary, calls that "quality", and never touches the behaviour that moves revenue.

**Scope.** I own this file. It states what the literature and the practitioner
evidence say a good adviser *does*, in a form a scorer can detect from a Trace. It
does not define scenarios, rubric weights or thresholds — it supplies the behavioural
vocabulary those should be built from, and at the end it says which parts of the
current `roleplay/rubric_v1.md` I think the evidence contradicts.

---

## 0. Evidence standard used in this file

Every factual claim carries an ID and a source reference, or it is labelled
`ASSUMPTION`. There is no third category. Assumptions are mine — inferences, design
judgements, or practitioner folklore I could not source — and they are collected in
§11 as well as marked inline, so a reader who only skims the register still sees all
of them.

Because "sourced" is not one thing, each source in §10 also carries a **verification
level**, and claims inherit it:

| Level | Meaning |
|-------|---------|
| **V1** | I extracted and read the primary text myself (raw PDF/HTML), and the quotes below are from that text. |
| **V2** | The page was retrieved and summarised by a fetch tool. I did not see raw text. Wording may be the summariser's. |
| **V3** | Search-engine summary only; the page itself was not retrieved. Treat exact figures as needing confirmation before they go in front of anyone. |
| **V4** | A secondary or commentary source standing in for a primary I could not reach. |

That distinction is not pedantry. Two of the most quotable numbers in this file are
V3, and one widely-circulated statistic in this field turns out to be a fabrication
laid on top of a real study — see §6.4. A file that flattened V1 and V3 into
"sourced" would have no way to tell the difference.

**Counts: 56 sourced claims (S-01 … S-56), 22 assumptions (A-01 … A-22).** Recount with:

    grep -c '^\*\*S-' docs/_research/call_craft.md
    grep -c '^\*\*A-' docs/_research/call_craft.md

**Reachability note.** The Monetary Authority of Singapore's own site returned
"service is currently unavailable" for every notice URL I tried on 2026-08-23, across
several paths. Every MAS claim below is therefore V4 — law-firm or compliance-vendor
commentary standing in for the primary notice. Anyone taking a MAS claim from this
file into a deliverable should re-fetch the notice first. I have not silently
upgraded any of them.

---

## 1. The bridge: business metrics are bought, behaviours are coached

The product category this corpus models sells on business outcomes — conversion
multiples, product penetration, time-to-first-sale, active ratio, contract expansion.
A coaching platform cannot move any of those directly. It can only change what an
adviser does on a call. So every behaviour in this file is written as
**observable behaviour → business metric it is a leading indicator for**, and the
corpus should refuse to grade a behaviour that cannot complete that sentence.

The strongest external validation that this framing is right is not a sales blog. It
is a regulator having already done it.

**S-01** (R18, V4) Singapore's MAS Balanced Scorecard framework (Notice FAA-N20)
requires financial advisers to grade representatives against four **non-sales** KPIs
— *understanding a client's needs*, *suitability of product recommendations*,
*adequacy of information disclosure*, and *standards of professionalism and ethical
conduct* — assessed by an Independent Sales Audit unit sampling actual transactions,
with the grade feeding the representative's variable income.

**S-02** (R18, V4) That framework classifies infractions by materiality: a
"Category 1" infraction is one with "a material impact on the interests of the
client" or which impinges on the representative's fitness and propriety.

Two things follow for this repo. First, a per-adviser behavioural scorecard whose
grades have consequences is an existing regulated artefact in one of the four target
regimes, not an invention — the corpus is modelling something the market already
buys. Second, MAS's four categories map almost exactly onto four of the five criteria
already in `roleplay/rubric_v1.md`, which is a useful independent check on the
rubric's shape. The one MAS has that the rubric does not is materiality grading:
MAS separates infractions that materially harmed the client from those that did not,
where the rubric currently has one outright-fail class. See §9.

**A-01** `ASSUMPTION` — the mapping from each behaviour below to its business metric
is my inference from the mechanics, not a measured causal link. Nobody in the sources
below ran an experiment showing that (say) stating the reason for the call raises
product penetration. Where I claim a link I am claiming plausibility of mechanism and
availability of measurement, and the corpus should present it that way: "this is the
behaviour we assert conversion depends on, here is how we detect its absence, here is
the study we would run to test the assertion."

---

## 2. The first thirty seconds

### 2.1 What the numbers say

**S-03** (R1, V2) In a corpus of 90,380 first-interaction cold calls, opening with
"Did I catch you at a bad time?" was associated with a 0.9% success rate against a
1.5% baseline — about 40% *less* likely to book the meeting.

**S-04** (R1, V2) In the same corpus, "How are you?" was associated with 5.2% (3.4x
baseline) and "How have you been?" with 10.01% (6.6x baseline), despite these being
first interactions with no prior relationship.

**S-05** (R1, V2) Explicitly stating the reason for the call was associated with a
2.1x higher success rate.

**S-06** (R2, V3) A separate Gong guide describing an analysis of 300M+ recorded
calls reports a *different* figure for the same "bad time?" opener — 2.15% rather
than 0.9%.

S-06 matters more than S-03. The same vendor publishes two incompatible numbers for
one phrase, which tells you the denominators differ, the corpora differ, or both,
and neither publication says which. This is exactly the failure the `lab/report`
denominator rule exists to prevent, and the corpus should not cite a single opener
percentage as fact. What survives S-06 is the **direction and the mechanism**: an
opener that invites the recipient to name the call as an imposition performs worse
than one that does not.

**A-02** `ASSUMPTION` — all of R1/R2/R3 are B2B technology sales corpora, on
switchboard-routed business lines, aiming at a booked meeting. Transferring them to
regulated retail advisory calls to individuals about their own money is an
assumption, and a large one: the recipient's relationship to the subject matter, the
legality of the call, and the definition of "success" all differ. I would use these
sources for *mechanism* and never for *thresholds*.

### 2.2 The mechanics, and what breaks when you skip them

The academic conversation-analytic work is more useful than the vendor data here,
because it describes structure rather than correlation.

**S-07** (R6, V3) B2B cold-call openings are organised to connect the salesperson to
the "relevant" person, and salespeople speed the call's progress by producing compact
turns that combine a response and a new sequence-initiating action in one turn.

**S-08** (R5, V3) Across 153 cold calls, persuasive conduct consisted of two practice
sets: **pre-expanding the request with accounts that secure the recipient's alignment
without disclosing where the sequence is going**, and **minimising the imposition of
the request to reduce the recipient's opportunities for refusal**.

S-08 is the sourced version of what practitioners call "shrinking the ask", and it is
directly gradeable: minimising the imposition is a turn-design property (a time
bound, a single specific next step, an explicit ceiling on what is being asked for),
not a sentiment.

It also carries an uncomfortable finding the corpus should not launder away: the
*first* practice — securing alignment without disclosing the end result — is a
technique for getting agreement to a course of action the recipient has not yet been
told the shape of. In a regulated retail context that is close to the line. Which
means the same paper supplies both a competence signal and a manipulation signal, and
the scorer needs to tell them apart. My reading: a time-bounded, specific,
honestly-labelled ask is the compliant form; an ask whose purpose is withheld until
alignment is already banked is the non-compliant form. Marked as `A-03` below because
the paper does not itself draw that line.

**A-03** `ASSUMPTION` — the distinction between "minimising the imposition" (good
craft) and "securing alignment before disclosing the purpose" (pressure) is mine.
R5 describes both as persuasive conduct without evaluating either.

**Observable openers checklist.** Each of these is a position-in-event-stream test,
which is what `lab/checks/PhraseContract` already decides on (position, not
timestamp — do not regress that):

| Behaviour | Observable | Leading indicator for |
|---|---|---|
| Identity given | adviser names self and firm before first question | trust; call survival |
| Reason for call stated | a purpose clause appears before the first substantive question | conversion (S-05) |
| Permission asked | an explicit yes/no request to continue, in the first N turns | conversion (S-08) |
| Time bound offered | a duration or turn-count ceiling stated in the same turn as the ask | conversion (S-08) |
| Imposition minimised | the ask names one specific small next step, not an open commitment | conversion (S-08) |
| No imposition-inviting opener | absence of "bad time?"-class phrasing | call survival (S-03) |

**A-04** `ASSUMPTION` — "most calls die in the first thirty seconds" is stated
confidently across practitioner training material (R26, V3) and is almost certainly
directionally right, but I found no dataset publishing a survival curve by elapsed
seconds for advisory calls. The corpus should grade the *opener mechanics* it can
observe and not assert the thirty-second figure as a finding.

---

## 3. Keeping someone on the call who does not want to be

This is the behaviour the corpus most needs and the one with the best academic
grounding, because there is a peer-reviewed taxonomy of exactly how people try to get
off a sales call.

### 3.1 Blocks and stalls

**S-09** (R4, V1) Humă & Stokoe collected and transcribed **159** B2B cold calls whose
goal was securing an appointment, and identified two practices through which
recipients resist: **blocks** and **stalls**.

**S-10** (R4, V1) Verbatim: "Blocks, examined first, hinder the ongoing course of
action by delivering a dispreferred response while also attempting to close down the
call."

**S-11** (R4, V1) Verbatim: "Stalls … frustrate the ongoing course of action through
the production of hedged or nonstraightforward responses followed by alternative
proposals that attempt to delay or divert the progress of the sale, thus threatening
its chances of success."

**S-12** (R4, V1) A block is recognisable from concrete turn-design features the paper
enumerates in its extracts: a gap before the turn, turn-initial delay, abandoned
starts, a turn-initial "well" prefacing an elaborated turn, and an **account for not
answering** — plus a second unit that moves to end the sequence ("there's no point
really").

**S-13** (R4, V1) The paper's worked example of a stall is a recipient who, mid-way
through arranging a meeting both parties had implicitly agreed to hold, asks to be
called back a week later and then later still, and announces he will first discuss
internally "whether or not it's worth setting up this meeting" — the analysis notes
this **backtracks on an implicit agreement** and moves the decision further away.

**S-14** (R4, V1) A canonical stall in this data is the counter-proposal *"send me the
information by email"* in place of a meeting: the recipient has not refused, but the
trajectory they propose is less conducive to the sale.

**S-15** (R4, V1) The salesperson's own treatment of the turn is evidence of what the
turn was doing. Against a block, the successful move in the paper's extract is to
**decouple the immediate request from the larger sales activity** — supplying an
alternative reading of her own action ("to just literally ask her") that keeps the
sequence open and makes a response relevant again. Against a stall, the salesperson
**declines to align with the proposed trajectory and proposes a swifter, concrete
one** instead.

**S-16** (R4, V1) The paper notes that framing a response as a *partial repetition of
one's own prior talk* produces "escalated disaffiliation" and treats the seller's
move as inapposite.

S-16 is the sourced basis for grading the second raise of an objection (§4.3). When a
customer repeats themselves, the repetition itself is the signal — it says the prior
answer did not land, and it raises the stakes of the next one.

### 3.2 What this gives a scorer

The two resistance types demand *different* responses, and a scorer that treats
"customer pushed back" as one event cannot grade either. Concretely:

| Signal | Recipient behaviour | Correct adviser move | Failure mode |
|---|---|---|---|
| **Block** | dispreferred response + account for not answering + move to close the call | narrow and re-specify the immediate request so it is separable from the sale; make a response relevant again | pushing the original ask again unchanged |
| **Stall** | hedged response + counter-proposal that defers ("email it", "call me next month", "I'll discuss internally") | decline the deferring trajectory, propose a concrete nearer alternative, name what would change by then | accepting the deferral and recording it as progress |
| **Interruption / bad timing** | "I'm in the middle of something" | acknowledge, shrink the ask, offer a genuine exit with a named alternative | continuing the planned sequence as if unsaid |
| **Genuine exit taken** | recipient declines the shrunk ask | close cleanly, no fourth attempt | manufacturing urgency; a further ask |

`lab/checks/NoProgressContract` is the right primitive for the stall case and I would
reuse it rather than add anything: a stall accepted without a counter-proposal is
literally a turn that produced no progress toward the goal state, which is what that
contract already detects. `NoReAskContract` covers the block case — re-asking the
identical question after an account-for-not-answering is a re-ask.

**A-05** `ASSUMPTION` — "offer a genuine exit" as a *conversion-positive* behaviour is
not sourced. R4 shows that pushing through a block fails in these extracts; it does
not show that offering an exit succeeds. My reasoning is that a genuine exit is the
only move that distinguishes a stall (deferral, recoverable) from a block
(termination, not recoverable) by eliciting which one you are in — and that
information is worth more than one more attempt. This is the single most important
assumption in this file to test empirically, and I would design the experiment before
I would defend the claim.

**A-06** `ASSUMPTION` — false urgency ("this rate ends Friday") fails as well as being
non-compliant. Sourced only in the negative: R4 shows nothing about urgency, and I
found no dataset on invented deadlines in advisory calls.

**A-07** `ASSUMPTION` — the block/stall taxonomy, derived from B2B calls to
organisations, transfers to retail calls to individuals. The turn-design features
(accounts for not answering, deferring counter-proposals) are generic conversational
machinery and I would expect them to transfer, but R4 did not test that.

**Business bridge.** Block-and-stall handling is the natural leading indicator for
*active ratio* — the proportion of advisers actually producing.

**A-08** `ASSUMPTION` — an adviser whose calls terminate on blocks stops dialling, so
opener and resistance skill show up as activity before they show up as conversion.
Mechanism only; unmeasured.

---

## 4. A real objection taxonomy

### 4.1 Categories

**S-17** (R22, V3) The standard open-textbook taxonomy divides objections into
**practical (overt)** and **psychological (hidden)**, and enumerates six categories:
hidden, stalling, no-need, money, product, and source objections.

**S-18** (R25, V3) A hidden objection is defined as an objection not openly stated by
the recipient but which is nonetheless an obstacle to the sale — recognisable because
the recipient asks trivial questions, asks nothing at all, or simply asserts no need.

For BFSI advisory specifically, the categories that recur are money (premium
affordability, fee level, opportunity cost), product (liquidity/lock-in, exclusions,
guaranteed-vs-projected), source (trust in the adviser, trust in the firm, commission
suspicion), no-need (existing cover, "my employer covers me"), and a distinctively
retail one: **another decision-maker** (spouse, adult child, family adviser).

**A-09** `ASSUMPTION` — that specific BFSI category list is mine, assembled from the
generic taxonomy in S-17 plus the product mechanics in §5. I did not find an
insurance-specific empirical objection frequency study.

### 4.2 Engaged versus acknowledged-and-abandoned

This is the distinction that matters most for grading, and the current rubric already
names it. Making it observable:

An objection is **engaged** when, in the turns following it, the adviser supplies
information *specific to the objection's content* — the actual charge for a fee
objection, the actual surrender schedule for a lock-in objection, the actual
commission for a trust objection — and then checks whether that landed. It is
**acknowledged and abandoned** when the adviser produces an empathy token
("I completely understand"), no objection-specific content, and moves the sequence
on to the next planned item.

Detectable without a judge, at least partially: (a) does any turn after the objection
contain a *quantity or named term* drawn from the objection's own subject matter;
(b) does the next adviser turn resume the pre-objection agenda position. The repo's
existing `objection-lock-in-left-unanswered` row is exactly this shape, and the
existing `objection-aggressive-fee-challenge` row is its control — the corpus already
has the right pair, it needs the pair replicated across the six categories in S-17.

**A-10** `ASSUMPTION` — "contains a quantity or named term from the objection's
subject matter" is a decent proxy for engagement, but it is a proxy. An adviser can
recite a number without engaging (§5.3), and can engage without one ("you'd be
locked in for five years and I don't think you should accept that on my word").
The proxy's false-positive mode is the recital; a judge is needed for the residue,
which is what `lab/judges` calibration exists to bound.

### 4.3 The stated objection versus the real one, and the second raise

**S-19** (R4, V1) A repetition of the recipient's own prior turn escalates
disaffiliation and marks the seller's intervening move as inapposite (= S-16).

That gives a clean, position-based signal: **the same objection raised a second time
is evidence the first answer failed**, independent of any judgement about the
answer's quality. It is the best single metric in this section because it needs no
oracle — it is a repetition test on the event stream — and it inverts the usual
grading direction: the *customer's* behaviour scores the *adviser's* answer.

The repo's `aggressive_challenger` persona already re-raises anything unhandled,
which means the primitive exists. What it should become is a first-class metric —
call it **second-raise rate**, objections raised twice / objections raised — reported
with its denominator, per category. A cohort-level second-raise rate broken down by
objection category is precisely the "not just what they sold but how and why" artefact
the manager-analytics surface sells.

**A-11** `ASSUMPTION` — the practitioner claim that "I need to think about it" is
usually price, trust, or another decision-maker rather than a genuine desire for
time. This is stated everywhere in sales training material and I could not source it
to any study. R22/R25 support only the weaker claim that hidden objections exist and
that stalling objections are a recognised category. **Do not put the price/trust/
spouse breakdown in a scenario's rationale as fact.** The gradeable behaviour that
does not depend on the claim being true is: did the adviser *ask which it was* before
accepting the deferral, or did they accept it and end the call?

**A-12** `ASSUMPTION` — that asking a diagnostic question in response to a
"think about it" outperforms accepting it. Mechanism (it distinguishes a stall from a
block, §3) but unmeasured.

**A-13** `ASSUMPTION` — the "indecision ends 40–60% of qualified deals" figure that
circulates in this area appeared in my search results with no study attached. I am
recording it as unusable rather than as a claim, and it should not enter the corpus.

---

## 5. Explaining a policy clause to a layperson

This is where the selling half and the compliance half of the corpus meet, and it is
the section with the strongest primary sourcing, because three of the four target
regimes have written down what a real explanation looks like.

### 5.1 The obligation is behavioural, and one regime states it as a turn

**S-20** (R9, V2) FCA PRIN 2A.5.3R: "A firm must support retail customer understanding
so that its communications … are likely to be understood by retail customers."

**S-21** (R9, V2) FCA PRIN 2A.5.8R: "The firm must tailor communications provided to
retail customers, taking into account the characteristics of retail customers,
including any characteristics of vulnerability."

**S-22** (R9, V3) FCA PRIN 2A.5.9R applies specifically to one-to-one interaction
"such as in branch, during a telephone conversation or other interactive dialogue",
and requires the firm, where appropriate, to tailor the communication to that
customer's information needs **and to "ask the retail customer whether they understand
the information and if they have any further questions", particularly where the
information is key — such as where it prompts the customer to make a decision.**

S-22 is the most useful single sentence I found for this corpus. It converts
"confirming understanding versus assuming it" from a coaching preference into a named
regulatory behaviour, in one of the four target regimes, scoped to exactly the
interaction type the roleplay surface simulates, and triggered by exactly the moment
a scorer can locate — the turn that prompts a decision. That is a detectable
contract: *within k turns of a key-information turn, an understanding-check turn must
appear.* Note the verification level: I have this at V3 for the exact wording and V2
for the section, and it is load-bearing enough that it should be re-fetched from the
Handbook before it appears in any deliverable.

**S-23** (R16, V3) Hong Kong's SFC Code of Conduct paragraph 5.2 requires a licensed
person, when making a recommendation or solicitation, to ensure its suitability is
reasonable in all the circumstances given what it knows or should know about the
client through due diligence.

**S-24** (R16, V3) The SFC defines a "complex product" as one whose terms, features
and risks "are not reasonably likely to be understood by a retail investor because of
its complex structure", and requires that where a client has little or no prior
knowledge or experience of a product type, the licensed person **must provide more
assistance to ensure the client understands the product** — with the depth of
explanation varying by the client's sophistication and the product's complexity.

S-24 gives the corpus a *calibration* requirement, not just an explanation
requirement: the same explanation can pass for one customer persona and fail for
another. That is a scenario axis, and it is why the persona set matters as much as
the trainee script.

**S-25** (R11, V2) The FCA's own good-practice publication cites, as good practice,
firms carrying out post-sale comprehension calls with a short questionnaire to confirm
customers understood key aspects, and firms calling customers after sending revised
letters to check understanding.

**S-26** (R11, V2) The same publication cites as good practice summary sheets setting
out main features "alongside any significant exclusions or limitations, with both
given equal prominence"; and as **poor** practice, risk warnings "placed at the end of
long mobile journeys or displayed in small or low contrast formats", and surface-level
changes — "shorter wording, new icons or colour changes without improving clarity,
sequencing or prominence of key information".

**S-27** (R11, V2) Also cited as poor practice: firms that "committed to using
jargon-free and intelligible language but had limited evidence to show this had been
embedded in communications to customers."

S-26's *equal prominence* and S-27's *committed but no evidence* are both directly
portable to a spoken call. Equal prominence becomes: the limitation gets comparable
turn-space and comparable specificity to the benefit. "Committed but unevidenced"
becomes the rubric's own hazard — a scorer that rewards the word "risk" is measuring
the commitment, not the embedding, which is why `rubric_v1.md` is right to say a
sentence containing the word "risk" is not a risk warning.

### 5.2 The specific clauses, and the specific requirement attached to each

**Cooling-off / free-look.**

**S-28** (R14, V3) In Hong Kong, the cooling-off period for a life policy is **21
calendar days**, running from delivery of the policy or issue of the Cooling-off
Notice, **whichever is earlier**, during which the policy holder can cancel and obtain
a refund of premium.

**S-29** (R24, V4) In Singapore, licensed life and accident & health insurers must
offer a **14-day** free-look period on new individual policies, running from receipt
of the policy document. I could not reach any MAS primary text for this; every source
I found was industry or consumer press, and none agreed on which notice imposes it. It
is in this file at V4 and should not be cited with a notice number.

**Guaranteed versus projected returns.** This is the clause where the commercial
incentive to blur is strongest, and the regulator has written the anti-blur rule as a
presentation rule.

**S-30** (R15, V3) Hong Kong's IA guideline on benefit illustrations (GL28) provides
that assumed rates of return are for illustrative purposes, **neither guaranteed nor
based on past performance**, and that authorised insurers "should not highlight
figures (in bold, underlined or in different colors or font sizes) which are not
guaranteed."

**S-31** (R15, V3) For participating policies, GL28 requires additional projections
under **pessimistic and optimistic** scenarios to demonstrate the variability of
non-guaranteed benefits.

The spoken-call translation of S-30 is direct and gradeable: **a non-guaranteed figure
must not be given greater emphasis than the guaranteed one.** In speech, "emphasis"
is: which number is said first, which is repeated, which is attached to the customer's
own stated goal, and whether the guaranteed figure is said at all. An adviser who
quotes a projected maturity value, attaches it to the customer's retirement plan,
repeats it, and never states the guaranteed floor has done in speech exactly what
GL28 forbids in print. S-31 gives the additional test: was variability expressed as a
*range*, or as a point estimate.

**Surrender values, and why understating them is the commercially optimal move.**

**S-32** (R20, V1) Verbatim from the abstract: "Most individual life insurance policies
lapse before expiration. Insurers sell front-loaded policies, make money on lapsers,
and lose money on non-lapsers." The authors propose and test a model in which
consumers do not fully account for the likelihood of needing money during the future
policy period, supported by policy data from two major life insurers and a survey of
recent customers of a large national insurer.

**S-33** (R20, V1) About **4.2%** of all US life policies lapse each year (5.2% of
face value in force); term ~6.4%/yr; traditional whole life ~3.0%/yr; universal life
~4.6%/yr; variable and variable universal ~5.0%/yr.

**S-34** (R20, V1) Cumulatively: **almost 25% of permanent policyholders lapse within
three years**, and **40% within ten years**.

**S-35** (R21 via R20, V4) Milliman (2004), as cited in that paper: almost **85% of
term policies never pay a death claim**, and nearly **88% of universal life policies**
do not terminate with a death benefit claim; for policies sold to seniors at age 65,
74% of term and 76% of universal life never pay a claim.

**S-36** (R20, V1) The lapse literature the paper reviews finds income and
unemployment shocks are key determinants — households are roughly twice as likely to
surrender after a spouse becomes unemployed (Liebenberg, Carson & Dumm 2012, as cited).

This is the most valuable block of numbers in the file, because it identifies a
conflict the corpus can hardly get wrong once it is stated: **the product's economics
depend on a customer outcome the adviser has a duty to explain and an incentive to
minimise.** An adviser explaining early-surrender loss honestly is explaining the
mechanism by which the policy makes money. "Most people keep these going" is not a
white lie; S-34 says it is false for a quarter of buyers within three years.

The gradeable behaviour: when a customer raises affordability, or mentions income
volatility, or asks what happens if they stop — did the adviser state what is
recoverable in the early years, in a number or a clear "substantially less than you
paid in", and check understanding (S-22)? Or did the response reframe the question as
commitment ("it's a long-term product, you'd want to keep it going")?

**Exclusions and waiting periods.**

**S-37** (R23, V3) LIMRA's consumer research reports buyers saying their producer did
not consider whether the recommended policy was affordable for them, and that
household members other than the buyer still needed cover — i.e. the affordability
and household-scope gaps are consumer-reported, not just regulator-hypothesised.

**A-14** `ASSUMPTION` — I could not source a reliable figure for how many
policyholders do not know their own exclusions. One study reporting 48% unaware of
specific exclusions surfaced in search, in a venue I could not verify. Excluded as
unusable; recorded so nobody re-finds it and treats it as new.

**A-15** `ASSUMPTION` — the clause-by-clause explanation quality bar below is my
construction from S-20 to S-31, not a quoted standard.

### 5.3 Genuine explanation versus recital versus minimisation

Three observably distinct behaviours, which the corpus should be able to separate,
because they score differently and only the middle one looks like compliance:

| Behaviour | What it looks like | Verdict |
|---|---|---|
| **Recital** | the clause is read out in product language ("there is a 90-day waiting period on critical illness"), no translation, no worked case, no check | compliant-looking, fails S-22 and S-24 |
| **Genuine explanation** | the clause is restated in consequence terms for *this* customer ("if you were diagnosed in month two, this would pay nothing — you'd be relying on your savings"), then an understanding-check | passes |
| **Minimisation** | the clause is stated and then discounted in the same or next turn ("technically there's a waiting period, but in practice…"), no check | fails, and is the highest-severity failure in this section |

The minimisation pattern is the one to build detection around, because it is the one
that *passes a keyword scorer*: the disclosure vocabulary is present. The signal is
**adjacency** — a limitation turn followed within one or two turns by a minimising
construction, with no understanding-check in between. That is a position-based test,
which is what `PhraseContract` is for.

**A-16** `ASSUMPTION` — that "minimiser adjacent to a limitation with no intervening
check" is a reliable detector. It will false-positive on legitimate proportionality
("there's a waiting period, and for your age band the premium difference is small"),
so it needs a judge on the residue and a calibrated TNR before it gates anything.

---

## 6. Discovery: real versus performed

### 6.1 What the regulators require, which is more specific than "ask questions"

**S-38** (R10, V2) FCA COBS 9A.2.6R requires information on the client's knowledge and
experience including "the types of service, transaction and financial instrument with
which the client is familiar", the "nature, volume, and frequency of the client's
transactions", and "the level of education, and profession or relevant former
profession".

**S-39** (R10, V2) COBS 9A.2.7R requires information on financial situation:
"the source and extent of their regular income, their assets, including liquid assets,
investments and real property, and their regular financial commitments."

**S-40** (R10, V2) COBS 9A.2.8R requires information on objectives: "the length of time
for which they wish to hold the investment, their preferences regarding risk taking,
their risk profile, and the purposes of the investment."

**S-41** (R10, V2) COBS 9A.2.13R: "If a firm does not obtain the information required
by COBS 9A.2.1R, it must not recommend investment services or financial instruments
to the client."

**S-42** (R10, V2) COBS 9A.2.11R: "A firm must not encourage client not to provide
information required for the purposes of COBS 9A.2.1R."

**S-43** (R17, V4) Section 36 of Singapore's Financial Advisers Act requires a
financial adviser to have a **reasonable basis** for any recommendation on an
investment product, giving consideration to the client's investment objectives,
financial situation and particular needs, and conducting reasonable investigation of
the product; Notice FAA-N16 sets out the know-your-client information to gather, how
the needs analysis and the presentation of recommendations should be conducted, and
the documentation and record-keeping required.

**S-44** (R13, V3) US Reg BI (adopted 5 June 2019, effective 30 June 2020) imposes four
obligations on recommendations to retail customers — **Disclosure**, **Care**,
**Conflict of Interest**, and **Compliance**. The Care Obligation requires reasonable
diligence, care and skill and a reasonable basis to believe the recommendation is in
the retail customer's best interest, not placing the firm's interests ahead of the
customer's. The Disclosure Obligation requires full and fair disclosure of the scope
and terms of the relationship including material fees and costs, **material
limitations** on the securities or strategies that may be recommended, and all
material facts relating to conflicts of interest.

Two of these are gifts to a scenario designer. **S-42** makes *steering a customer
away from answering* a named breach, which is the compliance form of the commonest
commercial shortcut in existence. **S-41** makes proceeding to a recommendation on a
thin fact-find a named breach rather than a matter of taste. Together they mean the
"impatient customer, adviser shortens the fact-find" scenario is not a judgement call
— it has a rule number.

**S-45** (R19, V2) Effective 29 December 2025, MAS's revised notices require financial
advisers to identify and document a client's **"selected client"** status as part of
know-your-client, to have a **"trusted individual"** present when advising selected
clients subject to criteria, to conduct pre-transaction **call-backs** covering
specified information on a principles-based approach, to **audio-record** those
call-backs and provide recordings on request, and to have an independent sales audit
unit conduct post-transaction checks. Dealers providing execution-related advice are
excluded from these safeguards but must still deliver fair dealing outcomes.

S-45 is the APAC analogue of the FCA's vulnerability tailoring (S-21) and it gives the
corpus a second, differently-shaped vulnerability requirement: not "tailor the
explanation" but "do not conclude this alone". A locale axis with genuinely different
required *actions*, not just different disclosure wording, is a much better portability
proof than translated boilerplate.

### 6.2 What the sales research says, which partly contradicts the rubric

**S-46** (R7, V3) Rackham's Huthwaite programme observed more than **35,000 sales
calls** by ~10,000 salespeople across 23 countries over 12 years.

**S-47** (R7, V3) Its headline finding was that techniques developed for low-value
sales do not transfer to major sales, and that the discriminator between high and
average performers was the *type* of question asked, not the quantity or the pitch:
top performers asked far more **Implication** and **Need-payoff** questions —
questions that develop the consequence and the value of a problem the customer has
already stated.

**S-48** (R7, V3) The same programme reported that the **open-versus-closed question
distinction had no measurable effect on outcomes** in its data.

S-48 matters to this repo directly, because `roleplay/rubric_v1.md` currently grades
discovery as "Open questions count; confirmations do not." The best-known research
base in the field says the open/closed axis is not the discriminator. I would not rip
the criterion out — see §9 for the specific change — but the corpus should not present
open-question counting as evidence-based when the largest study in the area found it
inert.

**S-49** (R3, V3) Vendor call-analytics data reports a talk-to-listen ratio around
43:57 associated with better outcomes (46:54 on discovery calls specifically), reduced
win rates once a rep talks past ~65% of the call, and a longest-continuous-monologue
threshold around 76 seconds; and reports 11–14 discovery questions per call as the
higher-win-rate band, with more questions than that associated with losses.

### 6.3 The observable difference between a needs analysis and a questionnaire

Synthesising S-38 to S-49, four signals separate discovery that is real from discovery
that is performed, all computable from a Trace:

1. **Coverage against the register.** How many of the required fact-find fields for
   this jurisdiction were actually elicited (not merely mentioned)? This is
   denominator-safe by construction and needs no judge. It is the FCA COBS 9A field
   set (S-38–S-40) and the MAS BSC "understanding client's needs" KPI (S-01) restated
   as a checklist.
2. **Answer uptake.** Did a later adviser turn *use* the content of an earlier answer
   — reference the stated goal, the stated horizon, the stated constraint? Asking then
   ignoring is the defining move of performed discovery, and it is detectable as a
   propagation property. `lab/checks/FieldPropagationContract` is already the right
   primitive: an answer given and never propagated into the recommendation is the same
   defect class it was written for.
3. **Depth beyond the first answer.** Did any question build on a prior answer rather
   than advance a fixed list? This is the observable proxy for S-47's implication
   questions: a question containing a term the customer introduced.
4. **Sequence.** Did elicitation precede product description? Position, not timestamp.

**A-17** `ASSUMPTION` — signal 3 ("question contains a term the customer introduced")
is my operationalisation of implication questions. Rackham's coding scheme is not in
the sources I read, and my proxy will accept a shallow echo. It is a starting
detector, not a faithful reimplementation of his construct.

**A-18** `ASSUMPTION` — R3's thresholds (43:57, 76 seconds, 11–14 questions) are
published without methodology, denominators or corpus definition, on a vendor blog,
and are from B2B tech sales. Directionally interesting; unusable as thresholds here.
If the corpus wants a talk-ratio metric it should measure its own distribution and
report it with its denominator rather than importing these.

### 6.4 One laundered statistic, recorded as a warning

**S-50** (R7 + R27, V3) Search results attributed to Rackham's research the claim that
Implication questions are "the single highest predictor of close rate in deals above
$50K ACV". Rackham's data predates the term ACV and the SaaS deal-size framing
entirely; the sourced finding is S-47, and the ACV clause is a later addition
presented as part of the original study.

I am recording this because it is the exact failure mode this repo is a portfolio
piece *against*: a real study, a real finding, and a fabricated precision bolted on
during a decade of blog re-summarisation. Any number the corpus quotes should survive
the question "did the study that supposedly produced this have the vocabulary to
express it?"

**A-19** `ASSUMPTION` — that Rackham's underlying dataset has never been published in
peer-reviewed form or independently replicated. I found no such publication in this
search pass. That is an absence-of-evidence statement about my own search, not a
finding, and it is why every Rackham claim above is V3 despite the book being a
primary source I could have bought.

---

## 7. The close

**S-51** (R7, V3) Rackham's programme found that in large, complex sales, aggressive
closing techniques **reduce** success rather than raising it, and that the effect gets
worse as deal size and decision consequence rise.

**S-52** (R3, V3) Vendor call data reports successful calls tending to a structure of
rapport at the start, three to four customer problems explored in depth in the middle,
and logistics and next steps at the end.

The rubric's existing requirement — *a summary of what was agreed must precede the
ask* — is well-founded on S-52's shape and on the compliance side by S-44's Disclosure
Obligation, and I would keep it exactly as it is.

What makes a close **non-compliant** rather than merely pushy is where the regimes are
explicit, and it is not about tone:

**S-53** (R13, V3) Reg BI's Conflict of Interest Obligation requires firms to identify
and **eliminate** sales contests, sales quotas, bonuses and non-cash compensation
based on the sale of specific securities or specific types of securities **within a
limited period of time** — because those practices, coupled with a time limitation,
create high-pressure situations to act contrary to the customer's best interest. The
requirement does not extend to compensation based on total products sold, asset growth
or customer satisfaction.

**S-54** (R8, V2) FCA COBS 4.2.1R(1): "A firm must ensure that a communication or a
financial promotion is fair, clear and not misleading", and 4.2.1R(3): "As part of
complying with (1), a firm must take into account the nature of the client."

**S-55** (R12, V3) In the UK, unsolicited direct-marketing calls to individuals about
occupational or personal pension schemes have been prohibited since **9 January 2019**
(the Privacy and Electronic Communications (Amendment) (No. 2) Regulations 2018,
SI 2018/1396), with a narrow exception where the caller is an authorised person and
the individual has previously consented; enforced by the ICO with fines up to
£500,000.

S-53 is a nearly perfect scenario seed. It means a real, regulator-recognised
pressure mechanism has an observable linguistic trace: the *time-limited,
product-specific incentive* leaking into the call. "I've got one more of these to
place this month", "the campaign closes Friday" — those are utterances a scorer can
detect, and their presence is evidence of exactly the conflict Reg BI names.

S-55 is a different and rarer kind of scenario: a call where the *first turn* is the
violation and no subsequent good behaviour redeems it. The corpus needs at least one
row of that shape, because a suite that can only grade conduct within a permitted call
will score a prohibited call on its manner.

**S-56** (R4, V1) A stall is a *deferral*, not a refusal, and the salesperson's
counter-move in the paper's data is to propose a concrete nearer alternative rather
than accept the deferral — which is the closing behaviour that distinguishes a soft
close from an abandonment (= S-13/S-15).

**A-20** `ASSUMPTION` — the soft/pressured close boundary I would grade on: a close is
*soft* if it names a specific next step, states what the customer is not committing to,
and accepts a no without a further attempt; it is *pressured* if it invokes a deadline
the adviser cannot evidence, re-asks after a clear decline, or makes the exit costly.
Constructed from S-51, S-53 and S-54; not quoted from any of them.

**A-21** `ASSUMPTION` — offering the cooling-off period as a *reason to sign*
("you can always cancel") is a distinct and gradeable pressure move, because it
converts a consumer protection into a closing lever and shifts the decision burden
onto a customer who has just been told the decision is reversible. No source states
this; it follows from S-28's purpose. I think it is one of the highest-value rows the
corpus could add and I would flag it as unsourced in the scenario's own notes.

---

## 8. Where selling pressure and compliance genuinely conflict

The highest-value scenarios in the corpus. Each row is a moment where the
commercially optimal move is the non-compliant one, so a conversion-only scorer and a
compliance-only scorer give **opposite** answers and both are wrong.

| # | Commercial pressure | The move | Regime + basis | Observable signal | Why a single-axis scorer fails |
|---|---|---|---|---|---|
| C-1 | Customer is impatient; the fact-find is long | shorten or waive fact-find questions | FCA COBS 9A.2.11R (S-42), 9A.2.13R (S-41); MAS reasonable basis (S-43) | a required field is reframed as optional ("we can skip that"), or a recommendation turn occurs with N register fields unelicited | conversion scorer rewards it (call survives); compliance scorer sees the gap but not the *steering*, which is the actual named breach |
| C-2 | Projected returns sell; guaranteed floors do not | quote the projection, omit or bury the guarantee | HK IA GL28 (S-30, S-31); FCA COBS 4.2.1R (S-54) | non-guaranteed figure said first, repeated, attached to the customer's stated goal; guaranteed figure absent or unrepeated; point estimate not range | keyword compliance passes if "not guaranteed" appears anywhere; the breach is *relative emphasis*, which only a position-aware check sees |
| C-3 | An exclusion or waiting period kills momentum | state it, then minimise it | FCA PRIN 2A.5.3R (S-20); SFC 5.2 / complex products (S-23, S-24) | limitation turn followed within 1–2 turns by a minimiser, with no understanding-check between | disclosure vocabulary is present, so a vocabulary scorer passes it; §5.3 |
| C-4 | Early-surrender loss is the strongest reason not to buy | reframe "what if I stop paying" as commitment rather than answering it | FCA PRIN 2A.5.9R (S-22); economics per S-32–S-36 | the question is answered with a horizon statement and no recoverable-value content | conversion scorer rewards it; the product's own economics (S-34) make the omission material |
| C-5 | Cooling-off makes signing feel free | use the cooling-off/free-look period as the closing lever | purpose of HK 21 days (S-28) / SG 14 days (S-29) | cooling-off first mentioned adjacent to the ask rather than during explanation | compliance scorer sees a required disclosure *given*, and scores it as a positive |
| C-6 | Deadlines close deals | invent or imply urgency | FCA COBS 4.2.1R (S-54); Reg BI conflict rationale (S-53) | a deadline or scarcity claim with no evidenced basis in the product ledger | conversion scorer rewards it strongly; this is the single most-rewarded non-compliant move |
| C-7 | A recommendation converts; feature description does not | slide from "this product does X" to "you should do X" | Reg BI attaches at *recommendation* (S-44); SFC 5.2 (S-23) | modal shift to second-person prescription, absent the licensing/suitability precondition | already the rubric's outright-fail; the conflict is that it is also the highest-converting single utterance in the call |
| C-8 | Two decision-makers halve the close rate | close with the person present | MAS selected-client / trusted-individual (S-45); FCA vulnerability tailoring (S-21) | the customer names an absent decision-maker and the adviser proceeds to the ask anyway | conversion scorer rewards it; the "another decision-maker" objection (§4.1) is being *bypassed*, not engaged |
| C-9 | A confused or vulnerable customer is an easier close | proceed rather than pause | FCA PRIN 2A.5.8R/2A.5.9R (S-21, S-22); MAS SC/TI (S-45) | vulnerability signal in the customer's turns (confusion, deferral to a relative, repeated re-asking) with no tailoring or check in the adviser's | this is where the two axes diverge most sharply and where real advisers actually fail |
| C-10 | A switch books new business | recommend a replacement without the surrender cost | front-loading economics (S-32); Reg BI material limitations (S-44) | no comparison and no surrender-loss statement in a replacement recommendation | conversion scorer sees a sale; only a suitability-aware check sees a loss crystallised |
| C-11 | Quota and campaign pressure is real | let the incentive into the call | Reg BI Conflict of Interest Obligation (S-53) | first-person quota/campaign language in an adviser turn | the utterance is evidence of a conflict the regulator requires firms to *eliminate*, not manage |
| C-12 | The call itself is the growth channel | make an outbound call the regime prohibits | UK pensions cold-calling ban (S-55) | outbound + pension subject + no evidenced prior consent | a suite that only grades conduct *within* a call will grade a prohibited call on its manner |

### 8.1 The apparent conflicts that are not conflicts

Worth stating explicitly, because a corpus built only from the table above would teach
that compliance always costs conversion, and the research says otherwise in at least
three places:

- **The permission-based opener.** Naming the reason for the call and asking to
  continue is both the compliant framing and the higher-converting one (S-05, S-08).
- **Not pushing through a block.** R4's extracts show the unchanged re-ask failing
  (S-15); declining to push is also the honest move.
- **Volunteering the commission.** The repo's own
  `objection-aggressive-fee-challenge` row is built on this and it is the right
  intuition — pre-empting a source objection (S-17) is a trust move and a conversion
  move at once.

**A-22** `ASSUMPTION` — that these three are genuinely non-conflicting rather than
conflicting on a longer horizon. The evidence for the first is B2B (A-02); the second
is a handful of extracts; the third is unsourced. A corpus claiming
"compliance and conversion agree here" should hold that claim to the same standard as
the conflicts.

---

## 9. What this implies for the existing rubric

Recommendations, not changes — `roleplay/rubric_v1.md` and the scorer belong to the
sibling workflows and I have edited neither. Ordered by how much evidence backs them.

1. **Re-word the discovery criterion off the open/closed axis.** S-48 says the
   open-versus-closed distinction was inert in the largest study in the field. The
   evidence-backed replacements are the four signals in §6.3: register coverage,
   answer uptake, depth beyond the first answer, and elicitation-before-description.
   Register coverage in particular is denominator-safe and judge-free.
2. **Add second-raise rate as a first-class metric** (§4.3, S-19). It grades the
   adviser's answer using the customer's behaviour, needs no oracle, and is the
   cohort-analytics artefact the manager-analytics surface actually sells.
3. **Add materiality grading to the fail class** (S-02). MAS separates infractions
   with material client impact from those without; the rubric currently has one
   outright-fail bucket. A missing disclosure that the customer had already been given
   in writing and a minimised exclusion that changed the decision are not the same
   defect.
4. **Make the understanding-check a scored behaviour, not an inferred one** (S-22).
   One of the four target regimes names it as a rule for exactly this interaction
   type. The contract is: within k turns of a key-information turn, an
   understanding-check turn appears.
5. **Grade relative emphasis on guaranteed versus projected figures, not presence**
   (S-30). Presence-based checks pass C-2 by construction.
6. **Add the minimisation detector** (§5.3) and calibrate its TNR before it gates
   anything, because its false positives are legitimate proportionality statements.
7. **Locale rows should differ in required *action*, not just required wording**
   (S-45 vs S-21). "A trusted individual must be present" is a structurally different
   requirement from "tailor the explanation", and a portability proof that only swaps
   disclosure strings has proven less than it looks.

---

## 10. Sources

| Ref | Source | Level |
|-----|--------|-------|
| R1 | Gong Labs, "Effective Cold Call Opening Lines" (stated corpus: 90,380 first-interaction cold calls). gong.io/blog/cold-call-opening-lines | V2 |
| R2 | Gong, "How to Master Cold Calls" guide (stated corpus: 300M+ calls). gong.io/files/gong-guide-how-to-master-cold-calls.pdf | V3 |
| R3 | Gong Labs, "Mastering the talk-to-listen ratio in sales calls". gong.io/blog/talk-to-listen-conversion-ratio | V3 |
| R4 | Humă, B. & Stokoe, E. (2023) "Resistance in Business-to-Business 'Cold' Sales Calls", *Journal of Language and Social Psychology* 42(5–6):630–652, DOI 10.1177/0261927X231185520. Open access (CC BY-NC) via LSE Research Online. **Read in full text.** | V1 |
| R5 | Humă, B., Stokoe, E. & Sikveland, R.O. (2019) "Persuasive Conduct: Alignment and Resistance in Prospecting 'Cold' Calls", *JLSP* 38(1):33–60, DOI 10.1177/0261927X18783474 (corpus: 153 calls) | V3 |
| R6 | Humă, B. & Stokoe, E. (2020) "The Anatomy of First-Time and Subsequent Business-to-Business 'Cold' Calls", *Research on Language and Social Interaction* 53(2), DOI 10.1080/08351813.2020.1739432 | V3 |
| R7 | Rackham, N. (1988) *SPIN Selling* (Huthwaite programme: 35,000+ calls, ~10,000 salespeople, 23 countries, 12 years) | V3 |
| R8 | FCA Handbook, COBS 4.2 (fair, clear and not misleading) | V2 |
| R9 | FCA Handbook, PRIN 2A.5 (Consumer Duty: consumer understanding) | V2 |
| R10 | FCA Handbook, COBS 9A.2 (suitability: information to obtain) | V2 |
| R11 | FCA, "Consumer understanding: good practice and areas for improvement" | V2 |
| R12 | Privacy and Electronic Communications (Amendment) (No. 2) Regulations 2018, SI 2018/1396; ICO enforcement | V3 |
| R13 | SEC, Regulation Best Interest small-business compliance guide; SEC staff bulletin on conflicts of interest; Groom Law and WilmerHale summaries | V3 |
| R14 | Hong Kong Insurance Authority, GL29 *Guideline on Cooling-off Period* | V3 |
| R15 | Hong Kong Insurance Authority, GL28 *Guideline on Benefit Illustrations for Long Term Insurance Policies* | V3 |
| R16 | SFC (Hong Kong), Code of Conduct para 5.2 and FAQs on compliance with suitability obligations / complex products | V3 |
| R17 | MAS Notice FAA-N16 *Recommendations on Investment Products*; Financial Advisers Act s.36 — via MAS FAQ listing and law-firm commentary; **MAS primary text unreachable** | V4 |
| R18 | MAS Notice FAA-N20 Balanced Scorecard framework, Annex 1 non-sales KPIs — via compliance-vendor and consumer-press summaries; **MAS primary text unreachable** | V4 |
| R19 | Allen & Gledhill, "MAS issues revised notices and guidelines to enhance pre- and post-transaction safeguards for retail clients" (effective 29 Dec 2025) | V2 |
| R20 | Gottlieb, D. & Smetters, K. (2016) *Lapse-Based Insurance*, working paper, Wharton/WUSTL. **Read in full text.** | V1 |
| R21 | Milliman (2004), as cited in R20 | V4 |
| R22 | *The Power of Selling* (open textbook, BCcampus), ch. 11.3 "Types of Objections"; parallel Lumen Learning edition | V3 |
| R23 | LIMRA research and newsroom pages (Insurance Barometer; consumer/adviser studies) | V3 |
| R24 | Singapore 14-day free-look period — industry and consumer-press sources only; no primary notice located | V4 |
| R25 | Monash Business School Marketing Dictionary, "Hidden objection" | V3 |
| R26 | Telesales/telemarketing practitioner training material on the first 30 seconds (multiple vendor pages, no methodology) | V3 |
| R27 | Third-party blog summaries of R7 (source of the laundered ACV claim in §6.4) | V3 |

---

## 11. Assumption register

Twenty-two, each marked inline where it is used.

| ID | Assumption | Where |
|----|-----------|-------|
| A-01 | Behaviour→business-metric mappings are mechanism-plausible, not measured | §1 |
| A-02 | B2B tech-sales call data transfers to regulated retail advisory calls | §2.1 |
| A-03 | "Minimising the imposition" (craft) is separable from "alignment before disclosure" (pressure) | §2.2 |
| A-04 | "Most calls die in the first 30 seconds" — no survival curve located | §2.2 |
| A-05 | Offering a genuine exit is conversion-positive, not just honest | §3.2 |
| A-06 | False urgency fails commercially as well as being non-compliant | §3.2 |
| A-07 | Block/stall taxonomy transfers from B2B organisational calls to retail individuals | §3.2 |
| A-08 | Opener and resistance skill show up in active ratio before conversion | §3.2 |
| A-09 | The BFSI-specific objection category list is my assembly | §4.1 |
| A-10 | "Quantity or named term from the objection's subject" proxies engagement | §4.2 |
| A-11 | "I need to think about it" is usually price, trust, or another decision-maker | §4.3 |
| A-12 | Diagnosing a deferral beats accepting it | §4.3 |
| A-13 | The "indecision ends 40–60% of deals" figure is unusable — recorded, not used | §4.3 |
| A-14 | No reliable figure located for policyholder ignorance of exclusions | §5.2 |
| A-15 | The clause-explanation quality bar in §5.3 is constructed, not quoted | §5.2 |
| A-16 | Minimiser-adjacency is a reliable detector (needs TNR calibration) | §5.3 |
| A-17 | "Question contains a customer-introduced term" operationalises implication questions | §6.3 |
| A-18 | R3's numeric thresholds are unusable here (no methodology, wrong domain) | §6.3 |
| A-19 | Rackham's dataset has never been peer-reviewed or replicated — absence of evidence | §6.4 |
| A-20 | The soft/pressured close boundary is constructed from S-51/S-53/S-54 | §7 |
| A-21 | Cooling-off-as-closing-lever is a distinct gradeable pressure move | §7 |
| A-22 | The three "apparent conflicts" in §8.1 are genuinely non-conflicting | §8.1 |

---

## 12. What I did not do, unhedged

- **I did not read R7.** Every Rackham claim is a third-party summary of a book I could
  have obtained. Four claims (S-46, S-47, S-48, S-51) rest on it, one of which (S-48)
  I am using to recommend a rubric change. That recommendation should not ship until
  someone reads the book.
- **I reached no MAS primary text.** Four claims (S-01, S-02, S-43, and S-29's
  jurisdiction) are V4 commentary. MAS's site returned service-unavailable on every
  attempt on 2026-08-23.
- **The two most quotable regulatory sentences are not V1.** S-22 (PRIN 2A.5.9R, the
  "ask whether they understand" rule) and S-30 (GL28's non-guaranteed highlighting
  rule) are the load-bearing sentences of §5 and both need re-fetching from the
  primary handbook/guideline before use in a deliverable.
- **No BFSI call corpus.** Every behavioural dataset here is either B2B technology
  sales (R1–R3) or B2B organisational cold calls (R4–R6). I found no published corpus
  of regulated retail advisory calls, which is unsurprising — they are recorded under
  obligations that prevent publication. The honest position is that the *mechanisms*
  transfer and the *magnitudes* are unknown, and A-02 and A-07 carry that.
- **No frequency data on objections.** §4.1's category list has no weights, so the
  corpus cannot claim to be representative of what advisers actually meet. It can
  claim coverage of the taxonomy, which is a different and smaller claim.
- **Nothing here is validated against a scorer.** Every "observable" in this file is a
  detector I believe is computable from a Trace. None has been implemented, and the
  three I would least trust are named: A-10, A-16, A-17.
