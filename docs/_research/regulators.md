# Four regulatory regimes, and where they genuinely disagree

Research input for the BFSI advisory corpus. Scope: what a retail-facing adviser must
actually **do and say** at the point of sale of an investment or insurance product under
**MAS (Singapore)**, **FCA COBS (UK)**, **Reg BI (US)** and **SFC / IA (Hong Kong)** — and,
in §6, the specific cases where the *identical* sales conversation is compliant in one
jurisdiction and a breach in another.

Those divergence cases are the point of the file. A compliance checker that matches
keywords cannot survive them, because the same sentence is a pass in one market and a
fail in another, and in several cases the *substance* is identical and only the required
*form* differs. §7 lists the requirements that are about **order or timing**, and §8 the
ones satisfiable **only by a specific form of words** as against by substance — both
directly implementable as `PhraseContract` / register checks.

---

## 1. Evidence standard used in this file

Every factual claim below is either

- **sourced** — carries a bracketed reference to the regulator's own handbook, notice,
  guideline, rule text or statute, resolved in §11; or
- **labelled `ASSUMPTION`** in the text, at the point a reader meets it.

There is no third category. §10 collects every assumption in one list so it can be
attacked as a block.

**Retrieval provenance matters and is recorded.** Most sources below were downloaded and
read in full (the SFC Code of Conduct, MAS FAA-N16 and FAA-N03, the Reg BI adopting
release, the MAS Fair Dealing Guidelines, the Singapore free-look regulation). Two Hong
Kong Insurance Authority guidelines — **GL29** (cooling-off) and **GL30** (financial needs
analysis) — sit behind a bot-block that refused every fetch attempt; their content here
comes from search-engine extracts **of the IA's own PDFs**, not from the PDFs read
directly. That is a weaker link than the rest of the file and is flagged inline as
`[retrieval: secondary]` every time it is relied on. Anything resting on those two
guidelines should be re-verified against the PDF before it is quoted to a customer.

A second provenance note: **MAS FAA-N16 was read in its tracked-changes form**, which
compares the 2025 amendment against the 2021 text [MAS-1]. MAS states the amendments took
effect **29 December 2025** and that the untracked published version prevails over the
tracked document in the event of a discrepancy [MAS-1]. As of the date of this research
the amendments are therefore in force, but `ASSUMPTION`: the paragraph numbers cited from
FAA-N16 below are the tracked document's, and should be confirmed against the published
version before being cited externally.

---

## 2. MAS — Singapore

Statutory frame: the **Financial Advisers Act 2001** ("FAA"), with the operative conduct
detail in MAS Notices. Two notices carry almost all of the point-of-sale requirement:
**FAA-N16** (Recommendations on Investment Products) and **FAA-N03** (Information to
Clients and Product Information Disclosure).

### 2.1 Required disclosures at point of sale — enumerated

FAA-N03 organises mandatory disclosure into six heads: general information about the
adviser and the **status of the representative**; the adviser's **remuneration**;
**conflicts of interest**; the **designated investment product**; **illustration of past
and future performance**; and **marketing materials** [MAS-2 ¶6].

Product-level items the adviser must disclose *and explain* to the client [MAS-2]:

| # | Item | Source |
|---|---|---|
| 1 | The product's intended investment horizon, ease of conversion to cash, and the expected level of risk tolerance of the client | [MAS-2 ¶24(d)] |
| 2 | Commitment required: amount, frequency and period of payment; for a life policy, whether the premium rate is **guaranteed or non-guaranteed** | [MAS-2 ¶24(e)] |
| 3 | Benefits: amount, timing, and whether **guaranteed or non-guaranteed**; for a life policy, furnish *and explain* the policy illustration, plus any lien and any excluded benefit | [MAS-2 ¶24(f)] |
| 4 | Risks borne by the client; for a life policy, the risk factors that may make benefits **less than the illustrated values**, and the alternative scenarios in the policy illustration | [MAS-2 ¶24(g)] |
| 5 | Pricing basis — historical or forward; dual vs single pricing, and that a single price excludes separately payable subscription/realisation fees | [MAS-2 ¶24(h)] |
| 6 | Fees and charges: amount, frequency and **nature**; any deferred sales load stated as such with details | [MAS-2 ¶24(i)] |
| 7 | All **warnings, exclusions and disclaimers** attaching to the recommended product | [MAS-2 ¶24(n)] |
| 8 | Where a guaranteed fund's guaranteed value is **less than 100%** of the amount subscribed or premiums paid, that fact | [MAS-2 ¶24(f)] |

Plus, from FAA-N16, a document that must be **furnished to the client** containing a
summary of the information gathered and the recommendation with **the basis for it**
[MAS-3 ¶36] — and, where applicable, a statement recording that the client declined to
give information, declined the recommendation, or declined advice altogether [MAS-3
¶36(i)–(iii)]. Timing is strict; see §7.

For a bundled product, the adviser must disclose **the option of buying a comparable
term life product** as set out in the bundled product disclosure document [MAS-3 ¶34A].

### 2.2 Suitability standard and the evidence of it

Section 36 FAA (formerly s.27) requires the adviser to **analyse the information provided
and identify a product that is suitable** for that client [MAS-3 ¶28]. The adviser must
have systems and processes for its representatives to determine suitability, taking into
account the nature of the product, key risks and other features **including investment
tenor, fees and liquidity** [MAS-3 ¶29].

Three obligations make the standard evidential rather than merely aspirational:

1. **Where no suitable product can be identified, the adviser must say so** [MAS-3 ¶30].
   A "nothing here fits you" outcome is a required output, not a failure state.
2. The adviser must **explain the basis of the recommendation to the client**, and that
   basis must be **documented** [MAS-3 ¶31].
3. The documented basis must include the client's own statement of objectives, financial
   situation and needs; the adviser's reasonable basis for recommending; and — notably —
   **the adviser's assessment of the product's *disadvantages* given this client's
   circumstances** [MAS-3 ¶35(a)–(c)].

Fact-find scope is enumerated, not principles-based: financial objectives; risk
tolerance; employment status; financial situation including assets, liabilities, cash
flow and income; source and amount of regular income; financial commitments; current
portfolio including any life policy; **whether the amount to be invested is a substantial
portion of the client's assets**; and, for life policies, **the number of dependants and
the extent and duration of support each needs** [MAS-3 ¶11(a)–(i)].

Reliance on previously gathered information is permitted **only if the client confirms at
the time of the transaction that there are no material changes** [MAS-3 ¶12].

The adviser must highlight **in writing** that the information the client gives is the
basis of the recommendation, and that inaccurate or incomplete information may affect
suitability [MAS-3 ¶14(a)–(b)].

### 2.3 Knowledge gating — CKA and CAR

Singapore has a product-access gate that the other three regimes do not have in this
form. For an **unlisted Specified Investment Product**, the adviser must conduct a
**Customer Knowledge Assessment** before recommending [MAS-3 ¶16], considering the
client's educational qualifications, investment experience and work experience — and if
the client withholds those, the adviser **must deem the client to lack knowledge** [MAS-3
¶17]. The adviser must not allow the client to transact unless satisfied the client has
the knowledge or experience [MAS-3 ¶19]. A **Customer Account Review** is the parallel
mechanism for Listed SIPs [MAS-3 ¶27F–27K].

Even on a *positive* outcome the adviser must still **offer advice** [MAS-3 ¶20]. On a
*negative* outcome and the client still wanting to proceed in a product the adviser has
not recommended, the adviser must inform the client of the outcome in writing, obtain
written confirmation, tell the client in writing that suitability is now the client's own
responsibility [MAS-3 ¶24], **and obtain the approval of senior management who is neither
involved in the trade nor connected to the client** before the trade may proceed [MAS-3
¶25]. CKA outcomes expire after one year [MAS-3 ¶26].

### 2.4 Licensing boundary — recommendation vs product information

FAA-N16 does not apply where **only factual information** is given about an Excluded
Investment Product **and no advice or recommendation is made before the transaction**
[MAS-3 ¶4(b)(i)–(ii)]. That is the boundary: the notice's requirements attach to
*recommending*, and pure factual product information about an EIP with no advice sits
outside them.

Two further boundary markers:

- A client may **choose not to receive any recommendation**, and the adviser must then
  hold documentation demonstrating that this is so [MAS-3 ¶33].
- Where the client declines advice on an unlisted SIP, the adviser must warn **in
  writing** that, having chosen not to receive advice, the client **will not be able to
  rely on s.36 FAA to bring a civil claim** for loss — and must confirm in writing that
  the client wishes to proceed regardless [MAS-3 ¶21]. The licensing boundary is
  therefore not just a scope line; crossing it changes the client's *litigation rights*,
  and the adviser has to say so out loud.

### 2.5 Fee and commission disclosure

The general rule is strong: the adviser must disclose **in writing all remuneration,
including commission, fees and other benefits**, directly related to making the
recommendation or executing the trade [MAS-2 ¶16]. Specifically — fees disclosed **at the
outset** [MAS-2 ¶17]; **the amount of commission** received on the products it recommends
[MAS-2 ¶18]; **the amount** of any trailer commission, soft commission or other benefit
[MAS-2 ¶19]; where not quantifiable, a **description of how it will be remunerated**
[MAS-2 ¶20]; and where the precise rate is unknown in advance, an **estimate of the rate
likely to apply** [MAS-2 ¶21].

**Then the carve-out that makes this a divergence.** For a **life policy**, the adviser
discloses the **"distribution cost" item in the policy illustration** and is **not
required** to disclose the amount and type of its own remuneration under ¶16–21 [MAS-2
¶22; MAS-4 ¶4]. So the same adviser, in the same meeting, owes a commission-amount
disclosure on the unit trust and does not owe one on the life policy.

Timing and channel: disclosure in writing **at the time of making the recommendation** or
**prior to execution** [MAS-4 ¶9]. For telesales or advice concluded by telephone, oral
disclosure of the amount is permitted, with **written confirmation no later than three
business days after the transaction date** [MAS-4 ¶10]. Remuneration attributable to
non-advisory activity — e.g. a product-manufacturer profit mark-up — need not be
disclosed [MAS-4 ¶8].

### 2.6 Risk warnings and the past-performance rule

MAS requires the substance, in a phrasing of its own: when using past performance to
illustrate possible returns, the adviser must **advise the client that past performance
is not necessarily indicative of future performance**, and must make known the source of
the data (product provider or independent agency) [MAS-2 ¶25(c)]. Forecasts of economy,
stock or bond markets or economic trends carry the parallel warning that the forecast is
not necessarily indicative of future or likely performance [MAS-2 ¶25(b)]. Future
performance of a collective investment scheme may not be disclosed at all — orally or in
writing — unless the matter is in the registered prospectus and that prospectus complies
with the prescribed schedule [MAS-2 ¶25(a)], and no prediction, projection or forecast on
a CIS's future performance may be made except as permitted [MAS-2 ¶25(e)].

The oral-disclosure rule is unusually mechanical, and directly testable: past or future
performance may be disclosed **orally only if a written disclosure of that matter is
provided to the client at the same time** [MAS-2 ¶26(b)] — or, for a prospectus-sourced
figure, only if the prospectus is handed over **at the same time**, with the client's
attention drawn to the assumptions and warning statements, and with the client told
if the performance period ended **more than three months ago** [MAS-2 ¶26(a)].

Direct-response advertising designed to solicit and close a sale must carry a **prominent
warning** that the client may wish to seek advice before committing, and that if they do
not, they should consider whether the product is suitable for them [MAS-2 ¶29].

For a first-time recommendation of an **Overseas-Listed Investment Product**, a
prescribed risk warning statement must be given **before** the recommendation, with the
client's acknowledgement obtained **before** the recommendation, and that acknowledgement
retained **at least five years** [MAS-3 ¶41C–41E].

### 2.7 Vulnerable customers — the "selected client" mechanism

This is Singapore's most distinctive feature and the sharpest contrast with the UK's
outcomes-based approach. The know-your-client process must establish three objective
facts: whether the client is **under 62 years of age**; whether the client is
**proficient in spoken and written English** (or the language the process is conducted
in); and whether the client holds at least **GCE 'O' or 'N' Level** or equivalent
[MAS-3 ¶10A(a)–(c)]. **If at least two of the three are answered in the negative, the
client must be treated as a "selected client"** [MAS-3 ¶10A].

The consequence is procedural and hard-edged. The adviser **must not proceed with the
sales and advisory process** for a selected client unless either the client is
**accompanied by a "trusted individual"** — who must be 21 or older, proficient in the
language used, hold at least 'O'/'N' Level or equivalent, and be able to communicate
effectively with the client, with the client's documented consent to that person seeing
their personal information [MAS-3 ¶10D(a)] — or the client gives a **written statement
declining** a trusted individual and asserting they can decide unaccompanied [MAS-3
¶10D(b)]. The adviser must document the selected-client determination and **declare that
it has done so** [MAS-3 ¶10C].

The determination may be set aside only where the adviser has reasons to conclude the
client has adequate knowledge and experience in a class of products **and limits its
recommendation to that class** [MAS-3 ¶10B]. It does not apply to a qualifying fully
automated digital advisory platform, nor to a dealer providing execution-related advice
[MAS-3 ¶10E].

Alongside this sit the **Guidelines on Fair Dealing**, whose scope MAS expanded to all
financial institutions and all products and services [MAS-5]. They direct institutions to
consider the needs of customers **especially those who are more vulnerable** [MAS-5 ¶4],
name "low financial literacy, physical disabilities or impaired mental capacities" as
vulnerability in the self-assessment questions [MAS-5 ¶1.6.3], require marketing
approaches adjusted to the literacy of the target segment with controls preventing
inappropriate sale to unsuitable profiles within it [MAS-5 ¶2.4.2], require that
customers with limited financial knowledge be **encouraged to obtain advice** [MAS-5
¶2.4.3], and — for a **complex investment product** — require it be made clear that the
product generally cannot be sold without advice, and that **higher-level approval** be
obtained before executing for a customer with limited product knowledge [MAS-5 ¶2.4.4].
Where vulnerable customers are involved in financed purchases, MAS points to additional
safeguards such as **callbacks** to confirm understanding, or **requiring a trusted
individual** to accompany the customer [MAS-5 ¶3.3.3]. Note the status: these are
*Guidelines*, not a Notice — non-binding in form, and supervisory in practice.

### 2.8 Free-look period

Primary source, and pleasingly precise: no licensed insurer may issue a life policy or an
accident-and-health policy **of one year or more duration** without a clause giving the
policy owner **at least 14 days after the date of receipt of the policy** to examine it
[SG-1 reg 8(1)(a)]. The clock runs from **receipt of the policy document**, not from the
proposal or the sale.

### 2.9 Record-keeping and call recording

- Documentation of the basis of recommendation, per §2.2 [MAS-3 ¶35].
- Every CKA and every CAR documented, including the information collected, the assessment,
  the outcome, and any senior-management or designated-person approval [MAS-3 ¶40(a)–(d)].
- For advisory on a **Listed SIP**, records of **all communication** with the client,
  including **a file note or a tape recording of the telephone conversation** [MAS-3 ¶41].
  Note the disjunction: a file note *or* a recording. A recording is not mandatory.
- Acknowledgement of the Overseas-Listed Investment Product warning: **not less than five
  years** [MAS-3 ¶41E]. Selected-client assessment and determination records: **not less
  than five years** [MAS-3, records provisions at ¶41K et seq.].
- Where an abridged version of the ¶36 document is given with the client's written
  consent, the adviser must keep **both** the full and the abridged version [MAS-3 ¶38].

---

## 3. FCA COBS — United Kingdom

### 3.1 Required disclosures at point of sale — enumerated

| # | Item | Source |
|---|---|---|
| 1 | Appropriate information, in good time, about the firm and its services, and about financial instruments and proposed investment strategies | [FCA-1 COBS 2.2A.2R] |
| 2 | Whether the advice is **independent advice** or **restricted advice**, using those terms; whether the advice rests on a broad or restricted analysis; and for restricted advice, whether the range is limited to products from entities with close links to the firm | [FCA-2 COBS 6.2B.33R] |
| 3 | An explanation of **whether and why** the advice qualifies as independent or restricted, and the restrictions that apply | [FCA-2 COBS 6.2B.35R] |
| 4 | Where restricted advice is given with spoken interaction, an **oral** disclosure that the advice is restricted and of what nature | [FCA-2 COBS 6.2B.38R] |
| 5 | The firm's **charging structure**, in writing, in good time before the personal recommendation | [FCA-3 COBS 6.1A.17R] |
| 6 | The **total adviser charge** payable, agreed with and disclosed to the client **in cash terms**, as early as practicable, with amount, frequency and period where structured over time | [FCA-3 COBS 6.1A.24R] |
| 7 | A reasonable estimate of **all aggregated costs and charges**, ex-ante, covering both the service and the manufacture and distribution of the instrument | [FCA-4 COBS 6.1ZA.14BR(1)(a), (3)(a)] |
| 8 | An **illustration showing the cumulative effect of overall costs and charges on the return** of the investment, with a description and any anticipated changes | [FCA-4 COBS 6.1ZA.14BR(1)(c)] |
| 9 | A **suitability report** specifying the advice given and how it meets the client's preferences, objectives and other characteristics | [FCA-5 COBS 9A.3.2R] |
| 10 | For an insurance-based investment product, a **suitability statement** outlining the personal recommendation, how it meets objectives including risk tolerance, financial situation including ability to bear losses, and knowledge and experience; and whether periodic review is needed | [FCA-5 COBS 9A.3.3AR] |
| 11 | For insurance distribution, a **statement of the customer's demands and needs**, modulated to the complexity of the contract and the type of customer | [FCA-6 ICOBS 5.2.2R] |
| 12 | Advance notification that telephone communications will be **recorded**, and that recordings are available on request | [FCA-7 SYSC 10A.1.11R, 10A.1.12AR] |

### 3.2 Suitability standard and the evidence of it

The firm must obtain information on the client's **knowledge and experience** in the
relevant instrument type, **financial situation including ability to bear losses**, and
**investment objectives including risk tolerance** [FCA-8 COBS 9A.2.1R]. Each is
expanded: knowledge and experience covers the service and instrument types the client
knows, the nature and frequency of their transactions, and their education and profession
[FCA-8 COBS 9A.2.6R]; financial situation covers the source and extent of regular income,
assets including liquid assets, investments and real property, and regular financial
commitments [FCA-8 COBS 9A.2.7R]; objectives covers holding period, risk preferences,
risk profile and the purpose of the investment [FCA-8 COBS 9A.2.8R].

The gate: **where the required information is not obtained, the firm must not recommend**
[FCA-8 COBS 9A.2.13R]. Note what this does and does not do — it stops the
*recommendation*, not the *transaction*. It is a prohibition on advising, not a product
access gate (contrast MAS §2.3).

Evidence of suitability is a **written report delivered before the transaction is
concluded** [FCA-5 COBS 9A.3.2R]. The firm must also make clear that the suitability
assessment is what enables it to act in the client's best interest [FCA-5 COBS 9A.3.1R,
9A.3.1AR].

Overlaying all of it, the **Consumer Duty**: act in good faith towards retail customers
[FCA-9 PRIN 2A.2.1R]; **avoid causing foreseeable harm** [FCA-9 PRIN 2A.2.8R]; **enable
and support retail customers to pursue their financial objectives** [FCA-9 PRIN
2A.2.14R]. Taking advantage of a customer's circumstances — expressly including any
characteristics of vulnerability — in a way likely to cause detriment is given as an
example of non-compliance [FCA-9 PRIN 2A.2.3G].

### 3.3 Licensing boundary — personal recommendation vs information

The UK boundary is drawn around the **personal recommendation**. COBS 9A's suitability
machinery is triggered by advising; the disclosure-of-service-type rules are triggered
"before providing investment advice" [FCA-2 COBS 6.2B.33R]. `ASSUMPTION`: the precise
statutory perimeter — Article 53 of the Regulated Activities Order and the FCA's
perimeter guidance on advice versus guidance — was **not** read for this file. The
proposition used here is only the weaker one that the COBS obligations above attach to
advice and not to the provision of product information, which the rules' own trigger
wording supports. Any corpus row that turns on the fine line between a "personal
recommendation" and generic advice under UK law needs the perimeter guidance read first.

### 3.4 Fee and commission disclosure — and the commission ban

The UK is the outlier, and this is the single largest divergence in the file. A firm may
**only be remunerated for a personal recommendation by adviser charges**, and must **not
solicit or accept any other commission, remuneration or benefit of any kind** [FCA-3 COBS
6.1A.4R]. Provider commission is not a disclosure problem in the UK; it is prohibited.

On top of the prohibition: the charging structure in writing in good time before the
recommendation [FCA-3 COBS 6.1A.17R]; the total charge agreed and disclosed **in cash
terms** as early as practicable [FCA-3 COBS 6.1A.24R]; aggregated ex-ante costs [FCA-4
COBS 6.1ZA.14BR(1)(a)]; and the **cumulative-effect-on-return illustration** [FCA-4 COBS
6.1ZA.14BR(1)(c)]. So the UK requires not merely the number but a demonstration of what
the number does to the client's outcome over time.

### 3.5 Risk warnings and the past-performance rule

The past-performance rule is prescriptive about content, prominence and period. Past
performance must not be the most prominent feature; it must cover at least the preceding
five years (or the whole period if shorter) in complete twelve-month periods; the
reference period and source must be stated; if a non-sterling currency is used that must
be stated with a warning about currency fluctuation; if the figures are gross, the impact
of commissions, fees and charges must be disclosed; and a **prominent warning** must
state that the figures refer to the past and that **"past performance is not a reliable
indicator of future results"** [FCA-10 COBS 4.5A.10R].

Simulated past performance must be based on the actual past performance of the same or
substantially similar instruments or indices, and carry a prominent warning that the
figures refer to **simulated** past performance and that past performance is not a
reliable indicator [FCA-10 COBS 4.5A.12R]. Future performance may not be based on or
reference simulated past performance, requires reasonable assumptions supported by
objective data, must show scenarios in **both negative and positive** market conditions,
and must carry a prominent warning that **forecasts are not a reliable indicator of
future performance** [FCA-10 COBS 4.5A.14R].

For high-risk categories the FCA prescribes the warning **verbatim**. For
non-readily-realisable securities, the required wording is *"Don't invest unless you're
prepared to lose all the money you invest. This is a high-risk investment and you are
unlikely to be protected if something goes wrong."*, with separate prescribed sentences
for P2P agreements, long-term asset fund units and qualifying cryptoassets [FCA-11 COBS
4.12A.11R(1)(a)–(d)]. Prominence is specified mechanically: legible, bordered, bold and
underlined; statically fixed at the top of the screen on a website or app; prominently
fixed throughout a television broadcast [FCA-11 COBS 4.12A.36R], with design features
that reduce visibility prohibited [FCA-11 COBS 4.12A.38R].

`ASSUMPTION`: COBS 4.5A and 4.12A sit in the **financial promotions** chapter. Whether a
spoken sentence in a live advisory conversation is itself a financial promotion is a
facts-and-circumstances question that was not resolved for this file. The corpus should
treat the *substance* of these warnings as the requirement in a spoken scenario and
should not assert that a spoken omission of the exact prescribed sentence is a COBS
4.12A breach without that question being settled.

### 3.6 Vulnerable customers — Consumer Duty

The consumer-understanding outcome requires communications **likely to be understood**
by retail customers and equipping them to make decisions that are effective, timely and
properly informed [FCA-12 PRIN 2A.5.3R]; communications **tailored** to the
characteristics of the customers including **any characteristics of vulnerability**, the
complexity of the product, the channel, and the firm's role [FCA-12 PRIN 2A.5.8R]; and,
where appropriate, communications **tested before** use and **monitored after** for
whether they support good outcomes [FCA-12 PRIN 2A.5.10R]. Firms must understand and take
account of cognitive and behavioural biases and the impact of characteristics of
vulnerability [FCA-9 PRIN 2A.2.25G].

The FCA's finalised guidance FG21/1 frames vulnerability through four drivers — **health,
life events, resilience, capability** [FCA-13] `[retrieval: secondary]` — the guidance PDF
was located but its content here comes from summaries of it, not a direct read.

Two properties matter for the corpus. First, the UK test is **not a checklist**: there is
no age threshold, no education threshold, no mandated accompanying person. Second, it is
**not satisfied by disclosure**: a communication that was delivered but not understood is
a Consumer Duty problem, which means a scenario can be graded on whether the adviser
*checked* comprehension rather than on whether the sentence was uttered.

### 3.7 Cancellation / cooling-off

Prescribed periods, by product [FCA-14 COBS 15.2.1R]:

- **Life policies and pension contracts** — **30 calendar days**
- **Personal pension schemes and stakeholder pension schemes** — **30 calendar days**
- Cash deposit ISAs — 14 calendar days
- Other non-life/pensions cases (advised but not at a distance; and at a distance) — 14 calendar days

The period begins on the day the contract is concluded — except for life policies, where
it begins when the consumer is **informed that the contract has been concluded** — or, if
later, the day the consumer receives the contractual terms and pre-contractual
information [FCA-14 COBS 15.2.3R]. Where multiple cancellation rights apply to one
transaction, the firm should apply the **longest** [FCA-14 COBS 15.2.2G].

### 3.8 Record-keeping and call recording

Firms must record telephone conversations relating to the covered activities in financial
instruments, made on or received on firm-provided or firm-accepted equipment [FCA-7 SYSC
10A.1.6R], **including conversations intended to result in those activities even where no
transaction results** [FCA-7 SYSC 10A.1.8R]. Retention is **five years, and up to seven
where the FCA requests** [FCA-7 SYSC 10A.1.14R]. Clients must be notified in advance that
calls will be recorded [FCA-7 SYSC 10A.1.11R] and told recordings are available on
request for five years to them and seven to the FCA [FCA-7 SYSC 10A.1.12AR(1)].

This is the regime in which an evaluation harness can most safely assume a recording of
the conversation exists.

---

## 4. Reg BI — United States

Source read: the rule text as adopted, and the adopting release, Release No. 34-86031
[US-1].

### 4.1 The rule, in its own structure

Rule 15l-1(a)(1): a broker, dealer or associated person, **when making a recommendation
of any securities transaction or investment strategy involving securities (including
account recommendations) to a retail customer**, must act in the best interest of that
retail customer **at the time the recommendation is made**, without placing its own
financial or other interest ahead of the customer's [US-1 §240.15l-1(a)(1)].

The general obligation is satisfied by four component obligations [US-1
§240.15l-1(a)(2)]:

**Disclosure Obligation** — **prior to or at the time of the recommendation**, provide the
retail customer, **in writing**, full and fair disclosure of: that the firm or person is
acting as a broker-dealer or associated person with respect to the recommendation; the
**material fees and costs** that apply to the customer's transactions, holdings and
accounts; the **type and scope of services**, including any material limitations on the
securities or strategies that may be recommended; and **all material facts relating to
conflicts of interest associated with the recommendation** [US-1
§240.15l-1(a)(2)(i)(A)–(B)].

**Care Obligation** — exercise reasonable diligence, care and skill to understand the
potential risks, rewards and costs and have a reasonable basis to believe the
recommendation could be in the best interest of **at least some** retail customers; to
believe it is in the best interest of **this particular** customer given their investment
profile; and to believe a **series** of recommended transactions is not excessive and is
in the customer's best interest taken together [US-1 §240.15l-1(a)(2)(ii)(A)–(C)].

**Conflict of Interest Obligation** — written policies and procedures reasonably designed
to identify and **at a minimum disclose, or eliminate**, all conflicts associated with
such recommendations; to identify and **mitigate** conflicts creating an incentive for an
associated person to put their interest ahead of the customer's; and to identify and
disclose material limitations on the securities or strategies that may be recommended
[US-1 §240.15l-1(a)(2)(iii)(A)–(C)].

**Compliance Obligation** — written policies and procedures reasonably designed to achieve
compliance with the regulation as a whole [US-1 §240.15l-1(a)(2)(iv)].

"Retail customer investment profile" includes age, other investments, financial situation
and needs, tax status, investment objectives, investment experience, investment time
horizon, liquidity needs and risk tolerance [US-2].

### 4.2 What Reg BI conspicuously does not require

- **No suitability report, no written statement of the basis of the recommendation.** The
  rule requires disclosure of relationship facts, fees and conflicts — not a document
  explaining why *this* product suits *this* customer.
- **No individualised fee disclosure.** As adopted, the Disclosure Obligation "does not
  mandate individualized fee disclosure particular to each retail customer"; firms may
  use standardised or hypothetical amounts, **dollar or percentage ranges**, and
  explanatory text, and must supplement with particularised information only where
  needed to fully and fairly disclose the material facts [US-1, Particularity of Fees and
  Costs Disclosed]. Product-level fees may be described in initial standardised terms
  with particularised disclosure later; where that later information appears in a
  mandated document such as a trade confirmation or prospectus, delivery under existing
  obligations satisfies the Disclosure Obligation **even if delivery occurs after the
  recommendation is made** [US-1, same section].
- **No ongoing duty to monitor.** The SEC states the rule "imposes no duty to monitor a
  customer's account following a recommendation" [US-1, comparison with adviser fiduciary
  duty] and separately confirms that "Regulation Best Interest does not impose a duty to
  monitor a retail customer's account" [US-1, scope of recommendations]. It does apply to
  explicit recommendations to **hold**, and to any recommendation arising from monitoring
  services the firm has agreed to provide [US-1, same].
- **No knowledge gate.** Nothing in the rule prevents a retail customer from buying a
  product they demonstrably do not understand; the obligations attach to *recommending*.

### 4.3 Licensing boundary — the "recommendation" trigger

This is the crux, and the SEC deliberately declined to make it mechanical. Whether a
recommendation has taken place "should turn on the facts and circumstances of the
particular situation and therefore, whether a recommendation has taken place is not
susceptible to a bright line definition" [US-1]. The factors are whether the
communication "reasonably could be viewed as a 'call to action'" and "reasonably would
influence an investor to trade a particular security or group of securities", and the
more **individually tailored** the communication is to a specific customer or targeted
group about a security or group of securities, the more likely it is a recommendation
[US-1].

Two consequences for the corpus. First, the US boundary is a **gradient**, not a line:
identical words can cross it or not depending on tailoring and context, which is precisely
what a keyword checker cannot represent. Second, it is the *only* one of the four regimes
where the trigger is expressly non-bright-line, which makes it the natural home for
scenarios that probe judgement rather than compliance-by-recitation.

`ASSUMPTION`: the SEC staff bulletins issued after the adopting release (on account
recommendations, on conflicts, and on the care obligation's treatment of "reasonably
available alternatives") were **not** read for this file. The adopting release refers to
reasonably available alternatives, but the staff's later elaboration of that expectation
is not captured here and should be read before the corpus grades an adviser on failing to
consider alternatives.

### 4.4 Form CRS

Form CRS must be delivered to a retail investor **before or at the earliest of**: a
recommendation of an account type, a securities transaction or an investment strategy
involving securities; a recommendation to roll over assets from a retirement account; or
recommending or providing a new brokerage service or investment that does not involve
opening a new account [US-3] `[retrieval: secondary]` — the operative rule is 17 CFR
240.17a-14; the timing formulation here comes from summaries of it rather than a direct
read of the rule text.

### 4.5 Risk warnings, cooling-off, vulnerability

- **No prescribed retail-conversation risk-warning wording** analogous to FCA COBS
  4.12A.11R was found in the Reg BI rule text or the parts of the adopting release read.
  `ASSUMPTION`: FINRA Rule 2210 governs communications with the public and imposes
  balance and fair-dealing requirements on performance claims, but FINRA's rulebook was
  **not** read for this file. Do not build a corpus row asserting a specific US
  prescribed sentence.
- **No federal cooling-off period for a securities transaction.** For insurance and
  annuity products the right is a **state** free-look, varying by state and typically in
  the range **10 to 30 days**, with per-state and sometimes per-product or per-age
  variation; the NAIC maintains state-by-state charts [US-4] `[retrieval: secondary]`.
  `ASSUMPTION`: no specific state's day count is asserted here beyond that range. Any
  scenario that turns on a US free-look length must name the state and cite that state's
  provision.
- **No vulnerability construct in Reg BI itself.** Age appears only as one component of
  the investment profile [US-2]. For annuity sales, the **NAIC Suitability in Annuity
  Transactions Model Regulation #275**, revised in 2020, imposes a best-interest standard
  on producers with obligations of care, disclosure, conflict of interest and
  documentation, and requires the producer to communicate **the basis of the
  recommendation** to the consumer [US-5] `[retrieval: secondary]`. That last element is
  a closer analogue to MAS ¶31 than anything in Reg BI. Adoption is state-by-state;
  reported as adopted in a large majority of jurisdictions [US-5] `[retrieval:
  secondary]`. `ASSUMPTION`: the count of adopting states was not verified against NAIC's
  own tracker and no specific state's adoption status is asserted.

---

## 5. SFC / IA — Hong Kong

Two regulators, two product perimeters. The **SFC** Code of Conduct governs investment
products distributed by licensed or registered persons; the **Insurance Authority**
governs long-term insurance through its guidelines. A wealth conversation covering both a
fund and a life policy is governed by both, with materially different rules.

### 5.1 SFC — required disclosures at point of sale, enumerated

Paragraph 8.3A requires, **prior to or at the point of entering into the transaction**,
delivery of [HK-1 ¶8.3A(a)]:

1. The **capacity** — principal or agent — in which the intermediary is acting.
2. Its **affiliation** with the product issuer.
3. **Whether or not it is independent**, and the bases for that determination.
4. Disclosure of **monetary and non-monetary benefits**, per ¶8.3.
5. **Terms and conditions in generic terms** under which the client may receive a
   discount on fees and charges.

Form rules: the disclosure must be made **in writing**, electronically or otherwise, and
the independence disclosure must take the form of the statement specified in **Schedule
9** [HK-1 ¶8.3A(b)]. Items (i), (ii), (iii) and (v) may be given as a **one-off
disclosure**, but where the one-off disclosure changes, either an updated one or a
transaction-specific disclosure must be provided **prior to or at the point of** the
transaction [HK-1 ¶8.3A(b)]. Where written form is not possible before the transaction
concludes, the intermediary should make a **verbal disclosure** and provide it in writing
**as soon as practicable after** conclusion [HK-1 ¶8.3A(c)]. Written disclosure must be
**in Chinese or English according to the client's language preference** [HK-1 ¶8.3A(d)],
and must be prominent, clear and concise [HK-1 ¶8.3A Notes].

Client agreements must include the **risk disclosure statements specified in Schedule 1**
[HK-1 ¶6.2(h)], in print **at least as large as the other text** in the agreement, with a
**declaration by a licensed staff member** confirming the statement was provided in the
client's language of choice (English or Chinese) and that the client was invited to read
it, ask questions and take independent advice — plus a **matching acknowledgement signed
and dated by the client** [HK-1 Schedule 1]. The staff member's name and CE number must
appear in block letters [HK-1 Schedule 1].

### 5.2 SFC — suitability standard

The core obligation is short and famously general: having regard to information about the
client of which it is or should be aware through the exercise of due diligence, the
intermediary should, **when making a recommendation or solicitation, ensure the
suitability of the recommendation or solicitation for that client is reasonable in all the
circumstances** [HK-1 ¶5.2]. Know-your-client requires reasonable steps to establish the
client's true and full identity, **financial situation, investment experience and
investment objectives** [HK-1 ¶5.1]. There is a general best-interests obligation at
[HK-1 ¶3.10].

Note the shape: **"reasonable in all the circumstances"**, with no prescribed fact-find
schedule of the MAS kind and no mandated suitability report of the FCA kind.

**Complex products** carry an extra layer. For complex products the intermediary must
ensure the transaction is suitable in all the circumstances; that **sufficient information
on the key nature, features and risks** is provided to enable the client to understand it
**before making an investment decision**; and that **warning statements** about the
distribution of a complex product are provided **in a clear and prominent manner** [HK-1
¶5.5(a)(i)–(iii)]. "Complex product" means an investment product whose terms, features
and risks are not reasonably likely to be understood by a retail investor because of its
complex structure, assessed against six factors: whether it is a derivative; whether a
secondary market at publicly available prices exists; whether adequate and transparent
information is available to retail investors; whether there is a risk of **losing more
than the amount invested**; whether features could fundamentally alter the nature, risk or
pay-out profile or involve multiple variables or complicated formulas; and whether
features could render it **illiquid or difficult to value** [HK-1 ¶5.5 Notes]. Paragraph
5.5 took effect 6 July 2019 [HK-1].

There is a carve-out with real teeth for the corpus: for complex products that are also
derivative products traded on an exchange in Hong Kong or a specified jurisdiction, and
**where there has been no solicitation or recommendation**, ¶5.5(a) does not apply,
though ¶5.1A and ¶5.3 still do [HK-1 ¶5.5(b)].

### 5.3 SFC — licensing boundary and the derivatives characterization

Hong Kong's boundary is drawn by **solicitation or recommendation**, and it interacts with
a client-characterization requirement that has no analogue in the other three regimes.

As part of know-your-client, the intermediary must **assess the client's knowledge of
derivatives and characterize the client on that basis** [HK-1 ¶5.1A(a)]. Where a client
**without** derivatives knowledge wants to buy a derivative product and there has been
**no solicitation or recommendation**:

- if the product is **exchange-traded**, the intermediary should **explain the relevant
  risks** [HK-1 ¶5.1A(b)(i)];
- if it is **not exchange-traded**, the intermediary should **warn** the client and, having
  regard to what it knows — particularly that the client lacks derivatives knowledge —
  should **provide appropriate advice as to whether the transaction is suitable**, keep
  records of the warning and communications, and, **if the transaction is assessed
  unsuitable, may only proceed if to do so would be acting in the client's best
  interests** [HK-1 ¶5.1A(b)(ii)].

That is the reverse of the intuitive shape: the *absence* of a recommendation triggers a
*duty to advise*. Separately, for derivative products, futures, options or any leveraged
transaction, the intermediary must **assure itself that the client understands the nature
and risks and has sufficient net worth** to assume them and bear the potential losses
[HK-1 ¶5.3].

### 5.4 SFC — monetary benefit disclosure

Where benefits are **quantifiable** before or at the point of the transaction, and the
intermediary or an associate **explicitly** receives monetary benefits from a product
issuer for distributing a product, it must disclose those benefits as a **percentage
ceiling of the investment amount or the dollar equivalent** [HK-1 ¶8.3 Part A(a)(i)]. For
a **back-to-back transaction** — buying from a third party and selling on to the investor,
or the reverse, with no market risk taken — the **trading profit** must be disclosed, again
as a percentage ceiling of the investment amount or dollar equivalent [HK-1 ¶8.3 Part
A(a)(ii)]. The minimum is a percentage ceiling **rounded up to the nearest whole
percentage point**, and disclosure is **on a transaction basis** [HK-1 ¶8.3 Part A Notes].

Where the arrangement is **non-explicit** — the intermediary distributes a product issued
by itself or an associate without explicitly receiving benefits — it must disclose that it
or an associate **will benefit from the origination and distribution** of the product
[HK-1 ¶8.3 Part B(b)(i)]. Where benefits are **not quantifiable** before or at the point
of the transaction, it must disclose the **existence and nature** of the benefits and the
**maximum percentage receivable per year**, again on a transaction basis [HK-1 ¶8.3 Part
B(b)(ii)]. Non-monetary benefits require disclosure of their **existence and nature** [HK-1
¶8.3 Part B(a)].

### 5.5 SFC — the Schedule 9 independence statement

Schedule 9 prescribes the **substance** of the independence statement. Where independent,
the intermediary states that it is independent because it does not receive fees,
commissions or other monetary benefits from any party in relation to distributing
investment products to the client, and does not have close links or other legal or
economic relationships with product issuers, or receive non-monetary benefits, likely to
impair its independence in favour of any product, class of products or issuer. Where **not**
independent, it states that it is NOT independent because it receives fees, commissions or
other monetary benefits from other parties (which may include product issuers) in relation
to distributing investment products to the client — referring the client to the monetary
benefits disclosure — and/or because it receives non-monetary benefits from other parties
or has close links or other legal or economic relationships with issuers whose products it
may distribute [HK-1 Schedule 9]. Adding a description of those close links is **optional**
[HK-1 Schedule 9 Note].

Crucially, Schedule 9 requires disclosure "containing the **substance** set out in the
following disclosure statements" [HK-1 Schedule 9]. This is a substance test with a model
form, not a verbatim-wording test. Contrast FCA COBS 6.2B.33R(2), which requires the
literal **terms** "independent advice" or "restricted advice" [FCA-2]. Same regulatory
objective, opposite drafting technique. §8 develops this.

### 5.6 SFC — advertising and record-keeping

Advertisements must contain a **Schedule 1 risk disclosure statement** where prescribed,
and any material intended to promote — or having the effect of promoting — interest in the
licensed person's business is deemed an advertisement for that purpose [HK-1 ¶33–34 of the
relevant schedule].

Order recording: particulars of instructions for agency orders and internally generated
orders must be recorded and **immediately time-stamped** [HK-1 ¶3.9(a)]; where order
instructions come in by telephone, a **telephone recording system** must be used and
recordings maintained as part of the records **for at least six months** [HK-1 ¶3.9(b)];
staff are prohibited from taking client order instructions on **mobile phones** in the
trading floor, trading room or usual place of business, under a written policy [HK-1
¶3.9(c)], with mobile-phone orders taken elsewhere requiring an immediate call-back into
the recording system to log the time of receipt and order details [HK-1 ¶3.9 Notes]. A
separate provision directs installation of a **centralised tape recording system** to
record all telephone conversations with prospective clients, clients and recognised
counterparties, with relevant lines routed through it [HK-1 ¶35–36 of the relevant
schedule].

**Six months is the number that matters for evaluation design.** It is an order of
magnitude below the FCA's five-to-seven years [FCA-7], and it constrains what a
retrospective quality-assurance or coaching-analytics product can assume is still on
disk.

### 5.7 IA — long-term insurance in Hong Kong

- **Financial Needs Analysis.** GL30 requires an FNA for every new life insurance policy
  application, obtaining adequate information on the customer's insurance needs, financial
  knowledge, circumstances, affordability and risk profile; advice must be **personalised
  to disclosed needs and circumstances rather than being product information**; premium
  financing must be taken into account in assessing affordability under ¶6.11; and a policy
  must not be recommended where there is an over-leveraging risk unless there is sufficient
  justification, which must be explained to the customer [HK-2] `[retrieval: secondary]`.
- **Cooling-off period.** GL29 sets **21 calendar days**, running from the day of delivery
  of **the policy or the cooling-off notice** to the policyholder or their nominated
  representative, **whichever is the earlier**, during which the policy may be cancelled
  and premium refunded [HK-3] `[retrieval: secondary]`.

`ASSUMPTION`: GL29's and GL30's exclusions, and the interaction between the SFC Code and
the IA guidelines where a product is both an investment and an insurance contract, were
not established. A conversation covering an investment-linked policy in Hong Kong may
attract both regimes; the corpus should not assert which governs a given utterance
without that being resolved.

---

## 6. The contrast table — where the regimes genuinely diverge

Ten divergences. Each is a case where **the same adviser behaviour, word for word, lands
differently across the four regimes** — which is exactly why a keyword-matching compliance
checker cannot work. The summary table is deliberately terse; the detail follows.

| # | The identical adviser behaviour | MAS | FCA | Reg BI | SFC / IA |
|---|---|---|---|---|---|
| D1 | "It costs you nothing — the provider pays us" on a retail investment recommendation | Lawful; must disclose the **amount** | **Prohibited outright** | Lawful; ranges suffice | Lawful; must disclose a **% ceiling** |
| D2 | Verbal recommendation, closed on the call, nothing written | Breach — doc before signing | Breach — report before conclusion | **Compliant** | Compliant for non-complex |
| D3 | "I'll keep an eye on it for you" | Transactional | Consumer Duty engages | **No duty to monitor** | Transactional |
| D4 | "Past performance is no guide to the future" | Substance satisfied | **Prescribed wording differs** | No prescribed sentence | Substance satisfied |
| D5 | Client insists on a product they don't understand | **Senior-management approval required** | May not advise; sale not gated | No gate at all | Depends on product + solicitation |
| D6 | Proceeding alone with a 68-year-old, limited English, no formal qualification | **Breach unless trusted individual or written declination** | Judgement under Consumer Duty | No rule engaged | No equivalent mechanical rule |
| D7 | "You've got a couple of weeks to change your mind" on a life policy | Correct (≥14 days) | **Wrong — 30 days** | State-dependent | **Wrong — 21 days** |
| D8 | Naming commission on a life policy vs on a fund, in one meeting | **Fund yes, life policy no** | Both prohibited | Ranges for both | % ceiling for both |
| D9 | Fluent, accurate risk explanation given in the client's own language, undocumented | Written highlight required | Understanding is the test | Written disclosure required | **Language of choice is the rule** |
| D10 | Discussing a structured note *without* recommending it | Outside the notice if factual + EIP | Outside COBS 9A | Gradient — "call to action" | **Triggers a duty to advise** |

### D1 — Provider commission: a disclosure item in three regimes, a prohibition in one

Under the FCA, a firm giving a personal recommendation on a retail investment product may
be remunerated **only by adviser charges** and must **not solicit or accept commission or
any other benefit** from a provider [FCA-3 COBS 6.1A.4R]. Under MAS the same arrangement is
lawful, with a duty to disclose **the amount** of commission on the products recommended,
and the amount of any trailer, soft or other benefit [MAS-2 ¶18–19]. Under the SFC it is
lawful with disclosure as a **percentage ceiling of the investment amount, per
transaction, rounded up to the whole percentage point** [HK-1 ¶8.3 Part A(a)(i) and Notes].
Under Reg BI it is lawful, and the disclosure may be **standardised ranges** rather than
this customer's number [US-1].

So a trainee who says "there's no charge to you — the provider pays us a commission, which
is 3% of what you invest" has: satisfied MAS, satisfied the SFC, over-satisfied Reg BI, and
**confessed to a prohibited remuneration arrangement** in the UK. A keyword checker looking
for a commission disclosure scores that line identically in all four.

### D2 — Whether the recommendation has to be written down at all, and when

- **FCA**: a **suitability report before the transaction is concluded** [FCA-5 COBS
  9A.3.2R], deliverable immediately after only where the client consents and could have
  delayed the transaction, in distance communications [FCA-5 COBS 9A.3.2R(3)].
- **MAS**: a document containing the information summary and the recommendation **with its
  basis**, furnished **before the client signs the application form** or consents to a
  disposal [MAS-3 ¶36].
- **Reg BI**: **no suitability report exists as a requirement.** The written obligation is
  about relationship facts, fees and conflicts [US-1 §240.15l-1(a)(2)(i)] — not about why
  this product suits this customer.
- **SFC**: no general suitability report; for **complex** products, sufficient information
  on key nature, features and risks **before the investment decision**, plus prominent
  warning statements [HK-1 ¶5.5(a)(ii)–(iii)].

An adviser who conducts a flawless discovery, gives a well-reasoned verbal recommendation
and closes on the call is **compliant under Reg BI** and in **breach under both the FCA and
MAS** — on the same transcript, with the same words.

### D3 — What happens after the sale

Reg BI is explicit: it "imposes no duty to monitor a customer's account following a
recommendation" and "does not impose a duty to monitor a retail customer's account",
though it does reach explicit **hold** recommendations and any recommendation arising from
monitoring services the firm has agreed to provide [US-1]. The FCA's Consumer Duty runs
across the relationship — avoid foreseeable harm, enable and support customers to pursue
their objectives [FCA-9 PRIN 2A.2.8R, 2A.2.14R] — and the insurance-based-investment
suitability statement must address **whether periodic review will be needed** [FCA-5 COBS
9A.3.3AR].

The behavioural consequence is precise and gradeable: the sentence "I'll keep an eye on it
and call you if anything changes" is a throwaway reassurance in a US scenario, and in a UK
scenario it is a **promise that creates an expectation the firm must then meet** — which is
what `PromiseContract` is for.

### D4 — The past-performance warning: identical substance, different mandated words

- **FCA**: the prominent warning must state that the figures refer to the past and that
  **"past performance is not a reliable indicator of future results"** [FCA-10 COBS
  4.5A.10R].
- **MAS**: the adviser must advise the client that **past performance is not necessarily
  indicative of future performance** [MAS-2 ¶25(c)].
- **SFC**: substance-based risk disclosure via Schedule 1 and, for advertisements, the
  prescribed statement [HK-1 ¶6.2(h), ¶33].
- **Reg BI**: no prescribed sentence found in the rule or the parts of the release read
  (see the `ASSUMPTION` in §4.5).

"Reliable indicator of future results" and "necessarily indicative of future performance"
carry the same meaning and share almost no tokens. A substring register keyed on the UK
phrasing records **zero** disclosures in a correctly-conducted Singapore session — which is
the exact failure the repo's `KEYWORD_SHADOW_TERMS` control exists to quantify.

### D5 — Product access when the client does not understand the product

- **MAS**: for an unlisted SIP, a negative CKA outcome means the client may not transact
  unless the adviser informs them in writing, obtains written confirmation, states that
  suitability is now the client's responsibility [MAS-3 ¶24], **and obtains approval from
  senior management who is uninvolved in the trade and unconnected to the client** [MAS-3
  ¶25]. Withholding education, work or investment-experience information forces a deemed
  negative outcome [MAS-3 ¶17].
- **FCA**: insufficient information means the firm **must not recommend** [FCA-8 COBS
  9A.2.13R] — a bar on advising, not on transacting.
- **Reg BI**: no gate. The Care Obligation binds the *recommendation* [US-1
  §240.15l-1(a)(2)(ii)].
- **SFC**: for a **non-exchange-traded** derivative with no solicitation, the intermediary
  must warn, **advise on suitability**, keep records, and may proceed on an unsuitable
  assessment **only if doing so would be acting in the client's best interests** [HK-1
  ¶5.1A(b)(ii)]; for exchange-traded, explain the risks [HK-1 ¶5.1A(b)(i)].

Four different correct behaviours for one customer sentence — "I don't really follow how it
works, but I want it anyway."

### D6 — Vulnerability: a mechanical test against an outcomes test

MAS: three objective questions — under 62, language proficiency, 'O'/'N' Level or
equivalent — and **two negatives make the client a "selected client"** [MAS-3 ¶10A], after
which the adviser **must not proceed** without a qualifying **trusted individual** present
or a **written declination** [MAS-3 ¶10D], with the determination documented and declared
[MAS-3 ¶10C].

FCA: no thresholds. Tailoring to characteristics of vulnerability, communications likely to
be **understood**, testing before and monitoring after [FCA-12 PRIN 2A.5.3R, 2A.5.8R,
2A.5.10R], four drivers of vulnerability in guidance [FCA-13] `[retrieval: secondary]`.

Reg BI: no vulnerability construct; age is one input to the investment profile [US-2]. For
annuities, NAIC #275 requires communicating the **basis of the recommendation** [US-5]
`[retrieval: secondary]`.

SFC/IA: no mechanical equivalent found in the Code; the IA route runs through the FNA's
assessment of circumstances and affordability [HK-2] `[retrieval: secondary]`.

This is the divergence that best separates a compliance *checker* from a coaching
*product*: MAS asks "was the procedure followed", the FCA asks "did the customer
understand", and those are different observable behaviours in a transcript.

### D7 — Cooling-off and free-look periods do not agree on any number

| Regime | Life / long-term insurance | Start of the clock |
|---|---|---|
| FCA | **30 calendar days**; 14 for other non-life/pension cases [FCA-14 COBS 15.2.1R] | Conclusion of contract; for life policies, when the consumer is **informed** it was concluded; or later receipt of terms [FCA-14 COBS 15.2.3R] |
| SFC / IA (HK) | **21 calendar days** [HK-3] `[secondary]` | Delivery of **the policy or the cooling-off notice, whichever is earlier** [HK-3] `[secondary]` |
| MAS | **at least 14 days** [SG-1 reg 8(1)(a)] | **Date of receipt of the policy** |
| Reg BI / US | No federal securities cooling-off; state free-look, typically **10–30 days** [US-4] `[secondary]` | Commonly delivery of the contract [US-4] `[secondary]` |

Every number differs and **every start-trigger differs**. "You'll have a couple of weeks to
change your mind, starting from when you sign" is: roughly right in Singapore but wrong
about the trigger; materially wrong in the UK and Hong Kong; and unanswerable in the US
without naming the state. Note also that the FCA directs firms to apply the **longest**
applicable period where several apply [FCA-14 COBS 15.2.2G] — so in the UK even the
adviser's arithmetic has a tie-break rule.

### D8 — The intra-jurisdiction divergence: MAS's life-policy carve-out

Almost all cross-regime divergence is between countries. This one is **inside** a single
conversation. Under MAS the adviser must disclose the **amount** of commission on
investment products [MAS-2 ¶18], but for a **life policy** discloses only the
**"distribution cost" item in the policy illustration** and is **not required** to disclose
its own remuneration [MAS-2 ¶22; MAS-4 ¶4]. A Singapore adviser recommending a unit trust
and a whole-of-life policy in one meeting owes two different disclosure standards, and a
checker with one rule per jurisdiction gets one of them wrong regardless of which it picks.

### D9 — Language: a first-class rule in one regime, an outcome in another

The SFC requires written disclosure **in Chinese or English according to the client's
language preference** [HK-1 ¶8.3A(d)], and the Schedule 1 declaration requires a licensed
staff member to confirm the risk disclosure statement was provided **in the language of the
client's choice**, with the client acknowledging the same [HK-1 Schedule 1]. The FCA
approaches the same problem from the other end: communications must be **likely to be
understood**, tailored to the customer's characteristics [FCA-12 PRIN 2A.5.3R, 2A.5.8R].
MAS requires the language of the sales process to be established as part of KYC and drives
it into the selected-client test [MAS-3 ¶10A(b)].

A monolingual register is therefore not a simplification, it is a defect — and the repo's
existing decision to key `REGISTERED_PHRASINGS` by **language as well as code** is the
correct shape. §5.1 is the citation that justifies it.

### D10 — The licensing boundary is a different *kind* of object in each regime

- **MAS**: a **scope carve-out**. Outside the notice where **only factual information** on
  an Excluded Investment Product is given and **no advice or recommendation precedes** the
  transaction [MAS-3 ¶4(b)]. Crossing back in has a consequence the adviser must state
  aloud: declining advice costs the client the ability to rely on s.36 FAA to bring a civil
  claim [MAS-3 ¶21].
- **FCA**: a **trigger for a body of rules** — suitability and service-type disclosure
  attach to giving advice [FCA-2 COBS 6.2B.33R; FCA-8 COBS 9A.2.1R]. (See the perimeter
  `ASSUMPTION` in §3.3.)
- **Reg BI**: a **gradient**, expressly "not susceptible to a bright line definition",
  assessed on "call to action" and degree of individual tailoring [US-1].
- **SFC**: **inverted**. The absence of solicitation or recommendation is what triggers the
  ¶5.1A duty to explain risks or to warn and **advise on suitability** for a client without
  derivatives knowledge [HK-1 ¶5.1A(b)].

Hong Kong is the case that breaks naive detectors outright. Every plausible
"unlicensed-advice detector" is built to fire when an adviser *gives* a recommendation; in
Hong Kong, for an unsolicited non-exchange-traded derivative sold to a
derivatives-inexperienced client, **failing to give advice is the breach**.

---

## 7. Requirements that are about order or timing

Directly implementable as event-stream ordering assertions — which is what the repo's
`PhraseContract` positional ordering is for. Nothing here needs a timestamp; all of it is
"A before B" on the sequence of utterances and artefacts.

| # | Ordering requirement | Regime | Source |
|---|---|---|---|
| 1 | Written disclosure of relationship, fees and conflicts **prior to or at the time of** the recommendation | Reg BI | [US-1 §240.15l-1(a)(2)(i)] |
| 2 | Form CRS **before or at the earliest of** a recommendation, a rollover recommendation, or a new brokerage service | Reg BI | [US-3] `[secondary]` |
| 3 | Suitability report **before the transaction is concluded** (distance exception requires consent and the option to delay) | FCA | [FCA-5 COBS 9A.3.2R, (3)] |
| 4 | Suitability statement **prior to conclusion** of an insurance-based investment contract | FCA | [FCA-5 COBS 9A.3.3AR] |
| 5 | Demands-and-needs statement **prior to the conclusion** of the insurance contract | FCA | [FCA-6 ICOBS 5.2.2R] |
| 6 | Charging structure in writing **in good time before** the personal recommendation | FCA | [FCA-3 COBS 6.1A.17R] |
| 7 | Total adviser charge in cash terms **as early as practicable** | FCA | [FCA-3 COBS 6.1A.24R] |
| 8 | Aggregated costs and the cumulative-effect illustration **ex-ante**, in good time before the service | FCA | [FCA-4 COBS 6.1ZA.14BR(1)] |
| 9 | Independent/restricted status **in good time before** providing advice; and, with spoken interaction, an **oral** restricted-advice disclosure in good time before the advice | FCA | [FCA-2 COBS 6.2B.33R, 6.2B.38R] |
| 10 | The ¶36 document — information summary plus recommendation and its basis — **before the client signs the application form** or consents to a disposal | MAS | [MAS-3 ¶36] |
| 11 | CKA conducted **before** making a recommendation on an unlisted SIP | MAS | [MAS-3 ¶16] |
| 12 | Overseas-Listed Investment Product risk warning given, **and acknowledged**, **before** the first such recommendation | MAS | [MAS-3 ¶41C–41D] |
| 13 | Issuer's SIP classification obtained **before** any recommendation on that product | MAS | [MAS-3 ¶15] |
| 14 | Senior-management approval obtained **before** the trade proceeds on a negative CKA | MAS | [MAS-3 ¶25] |
| 15 | Selected-client determination made, documented and declared **before** the sales and advisory process proceeds | MAS | [MAS-3 ¶10C–10D] |
| 16 | Past/future performance may be disclosed orally **only if written disclosure of that matter is provided at the same time** — simultaneity, not mere sequence | MAS | [MAS-2 ¶26(b)] |
| 17 | Prospectus-sourced performance disclosed orally **only at the same time as** the prospectus is handed over, with attention drawn to assumptions and warnings | MAS | [MAS-2 ¶26(a)] |
| 18 | Fees disclosed **at the outset**; remuneration in writing **at the time of the recommendation** or **prior to execution**; telephone cases in writing **within three business days after** the transaction | MAS | [MAS-2 ¶17; MAS-4 ¶9–10] |
| 19 | Transaction-related information under ¶8.3A delivered **prior to or at the point of entering into** the transaction — including an updated one-off disclosure where it has changed | SFC | [HK-1 ¶8.3A(a)–(b)] |
| 20 | Where writing is impossible pre-transaction: verbal disclosure first, written **as soon as practicable after** conclusion | SFC | [HK-1 ¶8.3A(c)] |
| 21 | Sufficient information on a complex product's nature, features and risks **before the client makes the investment decision** | SFC | [HK-1 ¶5.5(a)(ii)] |
| 22 | Order particulars recorded and **immediately** time-stamped; mobile-phone orders called back into the recording system immediately | SFC | [HK-1 ¶3.9(a), Notes] |
| 23 | Clients notified that calls will be recorded **before** the service is provided | FCA | [FCA-7 SYSC 10A.1.11R] |
| 24 | Cooling-off clock starts on **delivery of the policy or the cooling-off notice, whichever is earlier** — an ordering rule inside a duration rule | IA (HK) | [HK-3] `[secondary]` |

Two ordering requirements are worth separating out because they are **not** satisfiable by
sequence at all. MAS ¶26(a)–(b) require **simultaneity** — the written disclosure or the
prospectus must be provided "at the same time" as the oral statement [MAS-2]. And the
repo's own closing criterion — that a summary of what was agreed must **precede** the ask
for the business — is a *rubric* requirement rather than a regulatory one; it should not be
cited to a regulator, and the corpus should not blur the two.

---

## 8. Form of words versus substance

The single most important structural finding for the register. The four regimes split
cleanly into two drafting traditions, and a compliance instrument that assumes only one of
them is wrong half the time.

**Satisfiable only by a specific form of words**

| Requirement | Regime | Source |
|---|---|---|
| The **verbatim** risk warnings for restricted mass market investments — e.g. the non-readily-realisable-securities sentence — with prescribed prominence, bordering, bold and underline, and static positioning | FCA | [FCA-11 COBS 4.12A.11R(1)(a)–(d), 4.12A.36R, 4.12A.38R] |
| The prominent past-performance warning stating that figures refer to the past and that **"past performance is not a reliable indicator of future results"** | FCA | [FCA-10 COBS 4.5A.10R] |
| The simulated-past-performance warning naming the figures as **simulated** | FCA | [FCA-10 COBS 4.5A.12R] |
| The forecast warning that **forecasts are not a reliable indicator of future performance** | FCA | [FCA-10 COBS 4.5A.14R] |
| The literal **terms** "independent advice" or "restricted advice" must be included in the disclosure | FCA | [FCA-2 COBS 6.2B.33R(2)] |
| Total adviser charge expressed **in cash terms** — a prescribed unit, not a prescribed sentence | FCA | [FCA-3 COBS 6.1A.24R] |
| Monetary benefits expressed as a **percentage ceiling of the investment amount rounded up to the nearest whole percentage point**, or the dollar equivalent — again a prescribed unit | SFC | [HK-1 ¶8.3 Part A Notes] |
| Schedule 1 risk disclosure statements in print **at least as large as** the other text, with a staff declaration naming the staff member and **CE number in block letters** and a countersigned client acknowledgement | SFC | [HK-1 ¶6.2(h), Schedule 1] |

**Satisfiable by substance**

| Requirement | Regime | Source |
|---|---|---|
| The independence statement: disclosure "containing the **substance**" of the Schedule 9 statements; the description of close links is **optional** | SFC | [HK-1 Schedule 9] |
| Schedule 1 risk disclosures and the staff declaration and client acknowledgement: the stated content is "the **minimum required**" substance, and more may be added | SFC | [HK-1 Schedule 1] |
| Suitability: "reasonable **in all the circumstances**" | SFC | [HK-1 ¶5.2] |
| Advising the client that past performance is **not necessarily indicative** of future performance — a stated substance, not a quoted formula | MAS | [MAS-2 ¶25(c)] |
| Highlighting **in writing** that the client's information is the basis of the recommendation and that inaccuracy may affect suitability — form (writing) fixed, wording free | MAS | [MAS-3 ¶14] |
| Explaining the **basis** of the recommendation, and documenting the product's **disadvantages** for this client | MAS | [MAS-3 ¶31, ¶35(c)] |
| Best interest "at the time the recommendation is made"; "full and fair disclosure" of material facts; **standardised** ranges and hypothetical amounts acceptable for fees | Reg BI | [US-1 §240.15l-1(a)(1), (a)(2)(i); Particularity of Fees section] |
| Communications **likely to be understood**, tailored to customer characteristics, tested and monitored | FCA | [FCA-12 PRIN 2A.5.3R, 2A.5.8R, 2A.5.10R] |

Three consequences follow, and they are the whole argument for the corpus's design.

1. **A strict phrase register is correct for some requirements and wrong for others.** The
   FCA's COBS 4.12A wording and the SFC's percentage-ceiling unit are genuinely
   string-checkable. The SFC's Schedule 9 substance test and the FCA's consumer-understanding
   outcome are not, and a register that scores them by substring produces false negatives it
   cannot distinguish from real breaches. The register should therefore record, per code,
   **which kind of requirement it is** — verbatim, prescribed-unit, or substance — because
   that determines whether a miss is evidence about the adviser or evidence about the
   instrument.
2. **The same substance has different mandated words across regimes** (D4), so a register
   keyed by code alone, without jurisdiction *and* language, silently grades a compliant
   Singapore or Spanish-language session as a total disclosure failure.
3. **Some requirements are about units, not sentences** — cash terms [FCA-3], a whole-percentage-point
   ceiling [HK-1], "at least 14 days" [SG-1]. Those are checkable far more reliably than
   sentences, and they are the cheapest high-confidence compliance signals available.

---

## 9. What this means for behaviour, and for the business metric

The corpus grades observable behaviour; the buyer buys conversion, penetration and time-to-
first-sale. The bridge has to be explicit, so here is the mapping this research supports.
Each behaviour below is detectable in a transcript, and each is named with the leading
indicator it plausibly moves. Marked `ASSUMPTION` where the causal link is a design
hypothesis rather than a sourced finding — which is all of them, because no regulator
publishes conversion data.

| Observable behaviour | Detectable via | Leading indicator for | Status |
|---|---|---|---|
| Enumerated fact-find completed before any product is named — MAS ¶11 gives a nine-item checklist that is *already* a scoring rubric [MAS-3] | Field-propagation and ordering checks | Product penetration; suitability quality | `ASSUMPTION` (link to metric) |
| The adviser states **why not** — the product's disadvantages for this client [MAS-3 ¶35(c)] | Presence of a disadvantage statement | Persistency, complaint rate, and trust-led conversion | `ASSUMPTION` (link to metric) |
| "No suitable product" said out loud when true [MAS-3 ¶30] | Negative-outcome detection | Mis-selling rate; long-run conversion quality | `ASSUMPTION` (link to metric) |
| Disclosure delivered **in the right order** (§7) rather than merely delivered | Positional ordering contracts | Certification pass rate; audit exposure | Sourced requirement; metric link `ASSUMPTION` |
| Comprehension **checked**, not just information delivered [FCA-12 PRIN 2A.5.3R] | Question-after-explanation patterns | Time to first sale; vulnerable-customer harm | Sourced requirement; metric link `ASSUMPTION` |
| Promise-keeping — "I'll monitor it" honoured or not made [US-1; FCA-9] | `PromiseContract` | Retention; contract-value expansion | Sourced requirement; metric link `ASSUMPTION` |
| Jurisdiction-correct number quoted for cooling-off (D7) | Numeric register check | Complaint and cancellation rate | Sourced requirement; metric link `ASSUMPTION` |
| Staying the right side of a boundary that runs in **opposite directions** by regime (D10) | Unlicensed-advice / failure-to-advise dual check | Certification validity | Sourced requirement; metric link `ASSUMPTION` |

The last row is the one worth building the pitch on. Every off-the-shelf compliance
detector fires on *giving* advice. In Hong Kong, on an unsolicited non-exchange-traded
derivative sold to a client without derivatives knowledge, **not advising is the breach**
[HK-1 ¶5.1A(b)(ii)]. A corpus containing that row can demonstrate a class of defect a
keyword checker cannot express at all — not "it scores it wrong", but "it has no place to
put the finding".

---

## 10. Assumption register

Every assumption in this file, collected. Each is a place where a reader should push.

1. **§3.3 — UK advice perimeter.** Article 53 RAO and the FCA's perimeter guidance on
   advice versus guidance were not read. Only the weaker claim is used: that the COBS
   obligations cited attach to advice rather than to product information, per the rules'
   own trigger wording.
2. **§3.5 — spoken statements as financial promotions.** Whether a sentence spoken in a
   live advisory conversation is a financial promotion, and therefore whether COBS 4.5A and
   4.12A bite on it, was not resolved. The corpus should treat the substance as the
   requirement in spoken scenarios and must not assert a COBS 4.12A breach for a missing
   verbatim sentence in speech.
3. **§4.3 — SEC staff bulletins.** The post-adoption staff bulletins on account
   recommendations, conflicts, and the care obligation's treatment of reasonably available
   alternatives were not read. Do not grade a US scenario on failure to consider
   alternatives on the strength of this file alone.
4. **§4.5 — US prescribed risk-warning wording.** No prescribed retail-conversation risk
   warning was found in the Reg BI rule text or the parts of the release read. FINRA Rule
   2210 was not read. Do not assert a US prescribed sentence.
5. **§4.5 — US free-look day counts.** State free-look periods vary, reported as roughly
   10–30 days. No specific state's number is asserted. Any scenario turning on a US
   free-look length must name the state and cite that state's provision.
6. **§4.5 — NAIC #275 adoption count.** Reported as adopted in a large majority of
   jurisdictions; not verified against NAIC's own tracker. No specific state's adoption
   status is asserted.
7. **§5.7 — IA guidelines and dual regulation.** GL29's and GL30's exclusions, and which
   regime governs a given utterance where a Hong Kong product is both an investment and an
   insurance contract (notably an investment-linked policy), were not established.
8. **§1 — MAS FAA-N16 paragraph numbering.** Read in tracked-changes form. MAS states the
   untracked published version prevails; numbering should be confirmed against it before
   external citation.
9. **§9 — every behaviour-to-business-metric link.** No regulator publishes conversion
   data. Each mapping in §9 is a design hypothesis, and the corpus should present them as
   hypotheses to be tested by the platform's own outcome data, not as findings.

Retrieval-quality flags, which are weaker links rather than assumptions but should be
treated with the same suspicion: **[HK-2] GL30** and **[HK-3] GL29** were read only via
search-engine extracts of the IA's own PDFs, because ia.org.hk refused every direct fetch;
**[US-3]** Form CRS timing and **[US-4]/[US-5]** the NAIC material likewise; and **[FCA-13]**
FG21/1's four drivers came from summaries rather than the guidance PDF.

---

## 11. Sources

Primary documents downloaded and read in full unless marked otherwise.

**MAS (Singapore)**

- **[MAS-1]** MAS, *Notice No: FAA-N16 (Amendment) 2025*, issue date 29 December 2025 —
  tracked-changes document, introductory paragraphs 1–4 on status and precedence.
  `https://www.mas.gov.sg/-/media/mas/regulations-and-financial-stability/regulations-guidance-and-licensing/financial-advisers/notices/faa-n16---tracked.pdf`
- **[MAS-2]** MAS, *Notice on Information to Clients and Product Information Disclosure*
  (FAA-N03), issued under the Financial Advisers Act.
  `https://www.mas.gov.sg/-/media/mas/sectors/notices/cmg/notice-faa-n03/notice-on-information-to-clients-and-product-information-disclosure-faan03.pdf`
- **[MAS-3]** MAS, *Notice on Recommendations on Investment Products* (FAA-N16), as
  amended to 29 December 2025 — same document as [MAS-1].
- **[MAS-4]** MAS, *Practice Note on the Disclosure of Remuneration by Financial Advisers*
  (FAA-PN01), issued 11 May 2004, last revised 8 October 2018.
  `https://www.mas.gov.sg/-/media/MAS/resource/legislation_guidelines/fin_advisers/fin_advisers_act/practice_notes/FAA--FAAPN1--Practice-Note-on-the-Disclosure-of-Remuneration-by-Financial-Advisers.pdf`
- **[MAS-5]** MAS, *Guidelines on Fair Dealing — Board and Senior Management
  Responsibilities for Delivering Fair Dealing Outcomes to Customers* (FSG-G04), 30 May
  2024. `https://www.mas.gov.sg/-/media/mas-media-library/fair-dealing-guidelines-30-may-2024.pdf`
- **[SG-1]** *Insurance (General Provisions) Regulations* (Singapore), regulation 8, "Free
  look for life policies and accident and health policies", via Singapore Statutes Online.
  `https://sso.agc.gov.sg/SL/IA1966-RG17`

**FCA (United Kingdom)** — FCA Handbook, `handbook.fca.org.uk`

- **[FCA-1]** COBS 2.2A — Information disclosure before providing services (MiFID and
  insurance distribution provisions).
- **[FCA-2]** COBS 6.2B — Describing advice services. (Note: COBS 6.2A, the former
  provision, is **deleted**; 6.2B is the live text.)
- **[FCA-3]** COBS 6.1A — Adviser charging and remuneration.
- **[FCA-4]** COBS 6.1ZA — Information about the firm, its services and remuneration
  (MiFID provisions), costs and charges.
- **[FCA-5]** COBS 9A.3 — Information to be provided to the client (suitability report /
  statement).
- **[FCA-6]** ICOBS 5.2 — Demands and needs.
- **[FCA-7]** SYSC 10A.1 — Recording telephone conversations and electronic
  communications.
- **[FCA-8]** COBS 9A.2 — Assessing suitability.
- **[FCA-9]** PRIN 2A.2 — Consumer Duty cross-cutting obligations.
- **[FCA-10]** COBS 4.5A — Additional requirements for financial promotions to retail
  clients: past, simulated past and future performance.
- **[FCA-11]** COBS 4.12A — Promotion of restricted mass market investments: prescribed
  risk warnings and prominence.
- **[FCA-12]** PRIN 2A.5 — Consumer understanding outcome.
- **[FCA-13]** FCA, *FG21/1 — Guidance for firms on the fair treatment of vulnerable
  customers*, February 2021. `https://www.fca.org.uk/publication/finalised-guidance/fg21-1.pdf`
  `[retrieval: secondary — located, not read directly]`
- **[FCA-14]** COBS 15.2 — Cancellation: the cancellation period table and when it begins.

**SEC / Reg BI (United States)**

- **[US-1]** SEC, *Regulation Best Interest: The Broker-Dealer Standard of Conduct*,
  Release No. 34-86031, File No. S7-07-18, 84 Fed. Reg. 33318 (12 July 2019) — adopting
  release **and** the text of 17 CFR 240.15l-1 as adopted, at the "Text of the Rule"
  section. `https://www.govinfo.gov/content/pkg/FR-2019-07-12/pdf/2019-12164.pdf`
- **[US-2]** 17 CFR 240.15l-1, definitions of "retail customer" and "retail customer
  investment profile", via Cornell LII. `https://www.law.cornell.edu/cfr/text/17/240.15l-1`
- **[US-3]** 17 CFR 240.17a-14 — Form CRS preparation, filing and delivery; timing.
  `[retrieval: secondary — rule located, timing formulation taken from summaries]`
- **[US-4]** NAIC model law charts on life insurance and annuity disclosure provisions
  (free-look / right-to-examine, state by state). `[retrieval: secondary]`
- **[US-5]** NAIC, *Suitability in Annuity Transactions Model Regulation* (#275), 2020
  revision, §6A best-interest obligation and the care / disclosure / conflict /
  documentation obligations. `[retrieval: secondary]`

**SFC / IA (Hong Kong)**

- **[HK-1]** SFC, *Code of Conduct for Persons Licensed by or Registered with the
  Securities and Futures Commission* — paragraphs 3.9, 3.10, 5.1, 5.1A, 5.2, 5.3, 5.4,
  5.5, 6.2, 8.3, 8.3A; Schedule 1 (risk disclosure statements, staff declaration and
  client acknowledgement); Schedule 9 (independence disclosure statement); and the
  advertising and taping provisions. Read in full.
  `https://www.sfc.hk/-/media/EN/assets/components/codes/files-current/web/codes/code-of-conduct-for-persons-licensed-by-or-registered-with-the-securities-and-futures-commission/Code-of-Conduct-for-Persons-Licensed-by-or-Registered-with-the-Securities-and-Futures-Commission.pdf`
- **[HK-2]** Insurance Authority (Hong Kong), *GL30 — Guideline on Financial Needs
  Analysis*, commenced 23 September 2019, including ¶6.11 on premium financing and
  affordability. `https://www.ia.org.hk/en/legislative_framework/files/GL30.pdf`
  `[retrieval: secondary — direct fetch blocked]`
- **[HK-3]** Insurance Authority (Hong Kong), *GL29 — Guideline on Cooling-off Period*.
  `https://www.ia.org.hk/en/legislative_framework/files/GL29_English.pdf`
  `[retrieval: secondary — direct fetch blocked]`
- SFC, *Suitability Requirement* topic page, confirming ¶5.2 as the locus of the
  requirement. `https://www.sfc.hk/en/Rules-and-standards/Suitability-requirement`

---

## 12. Claim count

Counted mechanically rather than asserted, because a claim count in a document about
evidence standards should itself be reproducible. The method is stated so it can be
re-run and disagreed with.

- **Sourced claims: 308** citation-bearing statements — every table row and every sentence
  carrying a bracketed reference. This is the count of *claims made*.
- **Distinct citation loci: 213** — unique rule, paragraph or regulation references
  (e.g. `MAS-3 ¶36`, `FCA-5 COBS 9A.3.2R`). This is the closer figure for *distinct
  sourced propositions*: §§6–8 deliberately restate propositions established in §§2–5 in
  order to line them up against each other, so the 308 figure includes those
  cross-references and the 213 figure largely does not.
- **Sourced claims with no citation: 0.** Every factual proposition about a regulation is
  either bracketed or labelled.
- **Assumptions: 9 distinct**, appearing as 20 `ASSUMPTION` labels in the text — the extra
  occurrences are the eight behaviour-to-metric rows in §9, which collapse into register
  item 9, plus the §10 register restating each one. All nine are collected in §10.
- **Retrieval-quality flags: 5** — [HK-2], [HK-3], [US-3], [US-4]/[US-5], [FCA-13] — marked
  `[retrieval: secondary]` at every point of use. These are sourced claims whose *source is
  the regulator's own document* but whose *retrieval* was via search-engine extract rather
  than a direct read, and they are held to a lower confidence than the rest of the file.
