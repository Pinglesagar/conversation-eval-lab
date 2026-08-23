# Markets, languages, and what changes between them

Research input for the BFSI advisory-coaching corpus. This file exists to answer one
question the scenario corpus cannot answer for itself: **when the language changes,
what stops being true about the measurement?**

It is research, not code. Nothing here is imported by anything. It is the argument
for why `scenarios/roleplay/locale/` has rows in it at all, and for which rows to
write next.

---

## 0. How to read this file, and what it is not

The product category this corpus models is an enterprise AI coaching platform for
banking, insurance and wealth management: practice roleplay with certification,
knowledge chat with citations, live in-call support, agentic outreach, and manager
analytics. It operates in **24 markets** across Asia-Pacific, North America and
Europe, with hubs in **Singapore, Hong Kong and London**, and states **200,000+
advisers**. Four regulatory regimes are named on its own public site: **MAS
(Singapore), FCA COBS (UK), Reg BI (US), SFC / IA (Hong Kong)**. `[BRIEF]`

**Every claim below carries one of three labels.** There is no fourth category and
there are no unlabelled claims:

| Label | Meaning |
|---|---|
| `[S##]` | Sourced. The number in the Sources table at the bottom. Each source is marked **primary** (regulator, statute, statistics office, peer-reviewed paper, vendor's own docs) or **secondary** (law-firm note, encyclopaedia, search-result extract). |
| **ASSUMPTION** | My inference. Stated in the sentence, where a reader will see it. Not a soft source. |
| `[BRIEF]` | Given to me as the design target for this corpus, sourced from the vendor's public marketing. Not independently verified by me, and marketing numbers should never be treated as measurements. |

**Three things this file is not.** It is not legal advice — a compliance officer in
each market owns the actual requirement, and several claims below are deliberately
scoped narrowly to one product category because that is as far as the source went.
It is not a disclosure of any real vendor's roadmap: the market list in §1 is
reconstruction from public facts, and I say so again there. And it is not complete —
§6 counts what I sourced against what I assumed, and the ratio is the honest summary
of how far this got.

**Method.** Web research only, in one pass, August 2026: 23 searches and 10 page
fetches, no model calls, so nothing here consumed the phase's API budget. Three
fetches failed and each failure is recorded at its source rather than papered over —
the SEC Reg BI FAQ returned HTTP 403, a SingStat census infographic 404'd, and a
Census & Statistics Department table URL served API documentation instead of data.
Where I could pull the primary PDF and read the figures out of it myself — the Hong
Kong census language article — I did, and those are the most trustworthy numbers in
this file. Anything I could only reach through a search-result extract is labelled
**secondary-provenance** and carries an instruction to re-fetch before external use.

---

## 1. The 24 markets: a reconstruction, not a disclosure

**This table is inferred.** The vendor publishes the count (24) and the three
regions and the three hubs; it does not publish the list. I reconstructed the list
from four public facts — the hub cities, the four named regulators, the named use
cases (new-agent onboarding, bank cross-selling, wealth suitability), and where BFSI
*advisory distribution headcount* is actually large — and the reconstruction is
wrong in its tail by construction. **ASSUMPTION** applies to the membership of every
row; the regulator names are checkable.

The ordering logic: a platform whose stated scale is 200k+ *advisers* is selling into
**agency and bancassurance forces**, and those are concentrated in Asian life
insurance, not in European wealth boutiques. So the list is APAC-heavy, and I would
expect the European tail to be thinner than a European reader assumes.

| # | Market | Primary regulator(s) for advisory conduct | Dominant advisory languages | In the 24? |
|---|---|---|---|---|
| 1 | Singapore | MAS `[S6]` | English, Mandarin, Malay, Tamil, Hokkien/Cantonese with older clients | Near-certain (hub + named) |
| 2 | Hong Kong SAR | SFC; Insurance Authority; HKMA `[S11-sec]` | Cantonese, English, Putonghua | Near-certain (hub + named) |
| 3 | United Kingdom | FCA `[S1]` `[S2]` `[S3]` | English | Near-certain (hub + named) |
| 4 | United States | SEC (Reg BI) + FINRA; state insurance departments `[S4]` `[S5]` | English, Spanish | Near-certain (named) |
| 5 | Malaysia | Bank Negara Malaysia; Securities Commission Malaysia | Bahasa Melayu, English, Mandarin, Tamil | High |
| 6 | Indonesia | OJK | Bahasa Indonesia, Javanese/Sundanese regionally | High |
| 7 | Thailand | SEC Thailand; OIC (insurance) | Thai | High |
| 8 | Vietnam | State Bank of Vietnam; Ministry of Finance (insurance) | Vietnamese | High |
| 9 | Philippines | Insurance Commission; SEC PH; BSP | Filipino/Tagalog + English ("Taglish"), Cebuano | High |
| 10 | India | SEBI; IRDAI; RBI | English, Hindi, and ~10 regional languages | High |
| 11 | Japan | FSA Japan | Japanese | High |
| 12 | South Korea | FSC / FSS | Korean | High |
| 13 | Taiwan | FSC Taiwan | Mandarin (Traditional script), Taiwanese Hokkien | High |
| 14 | Australia | ASIC | English | High |
| 15 | Mainland China | CSRC; NFRA | Putonghua (Simplified script) | Medium — large market, hard cross-border footprint |
| 16 | Canada | CIRO + provincial CSA members; AMF (Québec) | English, French | Medium-high |
| 17 | Mexico | CNBV; CONDUSEF | Spanish (es-MX) | Medium |
| 18 | Netherlands | AFM | Dutch, English | Medium |
| 19 | Germany | BaFin | German | Medium |
| 20 | France | AMF; ACPR | French | Medium |
| 21 | Spain | CNMV; DGSFP | Spanish, Catalan/Galician/Basque co-official | Medium |
| 22 | Italy | CONSOB; IVASS | Italian | Medium |
| 23 | United Arab Emirates | SCA; CBUAE; DFSA (DIFC) | Arabic, English; Hindi/Urdu/Malayalam/Tagalog with expat clients | Medium-low |
| 24 | Saudi Arabia | CMA; SAMA | Arabic | Medium-low |

Regulator names for rows 1–4 are attached to sourced documents in this file; rows 5–24
name the securities or conduct regulator each jurisdiction registers with IOSCO or its
insurance equivalent, and IOSCO's own membership list is the primary index for that
mapping `[S27]` — I did not fetch 20 individual regulator sites in this pass, so treat
each individual name in rows 5–24 as **ASSUMPTION at the level of "this is the right
body for advisory conduct"**, even though the bodies themselves plainly exist.

**Two honest problems with my own table.**

First, **the Gulf rows sit outside the stated region taxonomy.** The vendor names
Asia-Pacific, North America and Europe. Including UAE and Saudi requires assuming
their internal taxonomy folds MEA into one of the three — plausible for a
London-hubbed EMEA sales team, but it is an assumption doing real work, and if it is
wrong then two European or two APAC markets take those slots. **ASSUMPTION.**

Second, **the bubble.** These are the markets I would expect to displace my rows
15–24 if I am wrong, and a reader who knows the vendor will probably find two or three
of them inside the real 24: New Zealand (FMA), Ireland (CBI), Switzerland (FINMA),
Poland (KNF), Luxembourg (CSSF), Brazil (CVM/SUSEP), Sri Lanka, Bangladesh, Cambodia,
Türkiye, South Africa (FSCA). **ASSUMPTION.**

**Why the reconstruction is still worth having even where it is wrong.** The corpus
does not need the right 24 names. It needs the right *set of things that vary*: script,
digit grouping, refusal directness, honorific system, prescribed-wording regime, and
STT vendor coverage. Every one of those varies inside my list in the same way it varies
inside the real list, so a suite designed against this table transfers to the real one.

---

## 2. What advisers actually sell in

The official language of a market is a bad predictor of the language a policy gets sold
in. This section is the gap between the two.

### 2.1 Singapore — the code-switching case, with a number attached

English is the language **most frequently spoken at home** for 48.3% of residents aged
5 and over (Census of Population 2020) `[S14]`. Mandarin, Chinese dialects, Malay and
Tamil make up most of the rest; the secondary reporting of the same census release puts
Mandarin near 30%, Malay near 9%, Chinese dialects near 9% and Tamil near 2.5%, and I
was not able to fetch the primary infographic to verify those four figures myself
`[S14-sec]`.

The number that matters for an eval is not the census, though. It is this: in the SEAME
corpus of spontaneous Mandarin-English conversation recorded from Singaporean and
Malaysian speakers, **only 12% of transcribed segments are monolingual Mandarin and
only 6% are monolingual English** `[S15]`. In that corpus, monolingual speech is the
exception and mixed speech is the default. A test suite that models Singapore as
"English, plus a Mandarin variant" is modelling a register that most of the recorded
conversation is not in.

Singlish is a distinct register, not broken English — discourse particles (lah, lor,
meh, hor), topic-prominent word order, dropped copulas, aspect marked lexically
(already, got). An adviser softening a hard close in Singlish is doing skilled
relationship work. **ASSUMPTION:** a scorer built on standard-English fluency
heuristics will read that skill as low professionalism and mark the adviser down for
the exact move that closed the sale.

**ASSUMPTION:** older-client business (the segment where whole-of-life and legacy
products sell) skews to Hokkien, Teochew and Cantonese, which are the dialects with
the *worst* commercial STT support of anything in this market.

### 2.2 Hong Kong — three languages, and a 40% hole

The 2021 Population Census, read from the primary C&SD article `[S13]`:

| Ability, population aged 5+ | 2011 | 2021 |
|---|---|---|
| Can speak Cantonese | 97.4% | **96.0%** |
| Can speak English (usual or other) | 45.1% | **57.7%** |
| Can speak Putonghua | 49.5% | **56.5%** |
| Can speak another Chinese dialect (Hakka, Fukien, Chiu Chau lead) | — | **14.1%** |

And the figure that should shape the corpus, same source: among **professionals**,
91.4% could read and write both Chinese and English — but only **60.3% reported being
"biliterate and trilingual"**; for managers 58.8%, for associate professionals 57.5%
`[S13]`.

So roughly **four in ten Hong Kong professionals are not trilingual.** The adviser
population is not uniformly English-capable, the client population is overwhelmingly
Cantonese, and English is nonetheless the language most product documentation is
drafted in. That triangle is where mis-selling lives, and it is the strongest argument
in this file for testing Cantonese-primary sessions rather than English sessions with
Cantonese decoration.

Written Chinese in Hong Kong is Traditional; mainland-facing business is Putonghua and
Simplified; the two are not interchangeable in a disclosure. English may be used in
addition to Chinese for official purposes under Basic Law art. 9 `[S28-sec]`.

**ASSUMPTION:** the real advisory register is Cantonese carrying English financial
nouns wholesale — *portfolio*, *risk profile*, *premium*, *surrender value*, *unit
trust* — because those terms have Chinese equivalents that clients do not use. Which
means a Cantonese-language session contains English terminology *precisely at the
points a compliance check is looking at*. That is the worst possible place for a
language boundary to fall.

### 2.3 The rest, compressed

| Market | Official | What is actually used in a sales conversation |
|---|---|---|
| Malaysia | Bahasa Melayu | Malay-English mixing ("Manglish") in urban professional sales; Mandarin plus Hokkien/Cantonese with Chinese-Malaysian clients; Tamil. Takaful business carries Arabic-derived product vocabulary (takaful, wakalah, tabarru') inside Malay sentences. **ASSUMPTION** on register mix. |
| Indonesia | Bahasa Indonesia | Bahasa with Javanese/Sundanese in-region; formality is carried by address terms (Bapak/Ibu) rather than verb morphology. Mononyms are common `[S23]`. **ASSUMPTION** on register mix. |
| Thailand | Thai | Thai, written **without inter-word spaces** — which is a tokenisation problem before it is a language problem (§3.6). Politeness particles ครับ/ค่ะ are gendered and their absence reads as rudeness. **ASSUMPTION.** |
| Vietnam | Vietnamese | Vietnamese; tonal, diacritic-dense; family name first; address by kinship term (anh/chị/em/cô/chú) which encodes relative age and cannot be skipped. **ASSUMPTION.** |
| Philippines | Filipino + English | "Taglish" is the default professional register, not a lapse. Cebuano and Ilocano outside Metro Manila. **ASSUMPTION.** |
| India | Hindi + English + 20 more | English for documentation, Hindi/Hinglish and regional languages for the actual sale; IRDAI agency distribution runs in Marathi, Tamil, Telugu, Kannada, Bengali, Gujarati, Punjabi, Malayalam. Money is counted in lakh and crore `[S25-sec]`. **ASSUMPTION** on the documentation/sale split. |
| Japan | Japanese | Japanese with keigo — a three-way honorific system (sonkeigo / kenjōgo / teineigo) whose misuse is a competence signal in itself. Magnitudes group by 万 (10⁴) and 億 (10⁸). **ASSUMPTION.** |
| South Korea | Korean | Korean speech levels (-습니다 formal vs -요 polite-informal); 만 / 억 grouping; family name first. **ASSUMPTION.** |
| Taiwan | Mandarin | Mandarin in Traditional script; Taiwanese Hokkien (Taigi) with older clients; 萬 / 億. **ASSUMPTION.** |
| Mainland China | Putonghua | Putonghua, Simplified script, strong regional accent variation. **ASSUMPTION.** |
| Australia | English | Australian English. Accent matters for STT, not for the scorer (§3.6). **ASSUMPTION.** |
| United States | English | English and Spanish. The Spanish path has an explicit regulatory shape — see §3.2 `[S4]` `[S5]`. |
| Canada | English + French | English outside Québec; in Québec, French with a statutory "French first" sequence for adhesion contracts `[S29-sec]` — a *behaviour*, not just a translation. |
| Mexico | Spanish | es-MX, distinct from es-ES in vocabulary and in formality defaults. **ASSUMPTION.** |
| Netherlands | Dutch | Dutch, with unusually high English tolerance — often the one market where an English-language session is genuinely representative. **ASSUMPTION.** |
| Germany / France / Spain / Italy | de / fr / es / it | Formal address is grammaticalised (Sie / vous / usted / Lei) and switching to informal without invitation is a real conduct error. Decimal **comma**, not point. Spain has co-official Catalan, Galician, Basque. **ASSUMPTION** on the conduct-error framing. |
| UAE | Arabic | Arabic official; English is the working language of finance; expat client base brings Hindi, Urdu, Malayalam, Tagalog. RTL script, and optionally Arabic-Indic digits. **ASSUMPTION.** |
| Saudi Arabia | Arabic | Arabic, RTL, Arabic-Indic digits (٠١٢٣) available alongside Western `[S24]`. **ASSUMPTION.** |

---

## 3. What breaks in an eval when the language changes

This is the engineering section. Each subsection ends with what I would actually build,
because a research note that does not change a test is a blog post.

### 3.1 Code-switching inside one sentence

**Why it is not an edge case.** In the corpus of record for Singaporean and Malaysian
conversational speech, 82% of transcribed segments are neither monolingual Mandarin
(12%) nor monolingual English (6%) `[S15]`. Advisory speech in Singapore, Hong Kong,
Malaysia, India and the Philippines mixes within the clause, not between turns.
**ASSUMPTION:** advisory speech specifically mixes *more* than general conversation,
because product nouns and regulatory nouns arrive in English while the persuasion
happens in the local language.

**What it breaks, in order of severity.**

1. **The speech engine.** Our own stack: Deepgram documents multilingual
   code-switching as a feature of specific models — Nova-2, Nova-3 and Flux
   Multilingual `[S20]`. But the Nova-3 Multilingual language set is *ten languages*:
   English, Spanish, French, German, Hindi, Italian, Japanese, Dutch, Russian,
   Portuguese `[S21]`. Nova-3 supports Mandarin, Cantonese, Korean, Vietnamese,
   Indonesian, Thai, Arabic and Tamil **monolingually** `[S22]`, and Malay only on
   Nova-2/base `[S22]`.

   Read those two lists together and the finding falls out: **the two code-switching
   pairs that matter most in this product's two Asian hubs — English↔Mandarin in
   Singapore and English↔Cantonese in Hong Kong — are not in the supported
   code-switching set, while English↔Hindi and English↔Spanish are.** That is not a
   criticism of a vendor; it is a coverage map, and it tells you exactly which two
   pairs need a measured baseline before anyone believes a score computed on them.

2. **Language identification, and therefore routing.** A per-utterance LID decision is
   the wrong shape for an utterance with three switch points. Deepgram's own guidance
   for code-switching is to drop endpointing to 100 ms `[S20]` — i.e. the transport
   itself needs retuning, which means a code-switched session and a monolingual
   session are not comparable measurements unless both were captured under the same
   settings. That is a fixture-hygiene requirement, not a nicety.

3. **The scorer.** `roleplay/register.py` matches registered phrasings per language
   (`REGISTERED_PHRASINGS[lang][code]`). A sentence that is 70% Cantonese with an
   English clause carrying the risk warning belongs to *neither* language bucket. The
   existing `locale-es-mx-registered-spanish-disclosure` row already proves the
   single-language failure — the register records the Spanish disclosure and the
   English-keyword criterion scores zero. A code-switched row is the harder version of
   the same bug, and the register's current design (per-language phrasing table)
   cannot express "the disclosure was given in English inside a Cantonese sentence"
   without a schema change.

**How would you even detect it?** There is a real literature with real metrics, and
the answer is that you do not detect code-switching with a boolean:

| Metric | What it measures | Where it fails |
|---|---|---|
| **CMI** (Code-Mixing Index, Gambäck & Das) | fraction of tokens not in the matrix language `[S16]` | insensitive to *where* the switches are — one long inserted phrase and ten scattered words can score alike |
| **M-index** (Multilingual Index) | Gini-style inequality of the language distribution `[S16]` | says nothing about switching, only about mix |
| **I-index** (Integration Index) | switch points / language-dependent tokens `[S16]` | the one that captures burstiness; needs reliable token-level LID first |

All three are critiqued for exactly this in the CALCS literature `[S16]`, and the
broader survey of code-switched speech and language processing is the standard
orientation `[S17]`.

**What I would build.** A token-level language tag on the transcript, then CMI plus
I-index per utterance stored on the `Trace` as measured metadata — not as a pass/fail.
Then two things become possible that are impossible today: (a) stratify every score by
code-mixing band, so "the scorer is worse on mixed speech" becomes a number rather
than a suspicion; and (b) gate the corpus itself — a scenario tagged `code-switching`
whose measured CMI is near zero is a mislabelled scenario, and the suite should say so
before anyone draws a conclusion from it. That is the same move `lab/voice/calibration`
already makes for timing: measure the instrument, then measure with it.

### 3.2 Prescribed wording versus translated wording

This is the subsection where an unlabelled guess would do the most damage, so it is
the most narrowly scoped.

**Finding 1 — "prescribed wording" is not a property of a regulator; it is a property
of a product category.** In the UK, the FCA prescribes exact words for promotions of
Restricted and Non-Mass Market Investments: *"Don't invest unless you're prepared to
lose all the money you invest. This is a high-risk investment and you are unlikely to
be protected if something goes wrong. Take 2 mins to learn more."* `[S3]`. And on its
own guidance page for mainstream investments the FCA says the opposite in as many
words: **"We do not prescribe risk wording for mainstream investments"** `[S2]`, with
the duty instead being the outcome standard in COBS 4.2.1R(1) plus balance under COBS
4.5.2R(2) / 4.5A.3R(2)(b) `[S2]`.

So a compliance check that looks for a fixed phrase in a *mainstream* UK advisory
session is testing a requirement that does not exist — and would fail a compliant
adviser. That is the same defect class as the Spanish locale row, arriving from the
other direction. Any register keyed only by jurisdiction is under-specified: it must be
keyed by **(jurisdiction, product category)**.

**Finding 2 — where a warning is required, the requirement is usually about
*substance conveyed prominently*, not about a string.** COBS 4.6.2R(4) requires past-
performance information to carry "a prominent warning that the figures refer to the
past and that past performance is not a reliable indicator of future results" `[S1]`.
That is a content test, not a phrase test. Substring matching approximates it and will
produce false negatives on every valid paraphrase — which is exactly why
`roleplay/register.py` documents its own strictness as deliberate, and why the
strictness must stay a *documented false-negative source* rather than being quietly
loosened.

**Finding 3 — the one place I found a clean answer to "does a translation satisfy the
requirement?", the answer is: only alongside the original.** SEC staff FAQ guidance on
Regulation Best Interest states the staff would not object to delivering a complete
foreign-language translation of the required disclosures **so long as the firm also
delivers those disclosures in English at the same time**; the translation must be
complete, fair and accurate and must not render terms misleading; and the firm
**should not translate "U.S. Securities and Exchange Commission"** `[S4]`. The same
position is stated for the Form CRS relationship summary `[S5]`.

That last detail is the most testable sentence in this entire file: **there exists a
named entity that must survive translation verbatim.** That is a check you can write
today, it is deterministic, and it has a citation.

*Provenance caveat, stated because it matters:* my direct fetch of the SEC FAQ page
returned HTTP 403 in this pass. The wording above comes from a search-result extract of
that SEC page, and the FAQ is staff guidance dated 7 May 2020, not a rule. Before any
of this is asserted in a deliverable, re-fetch `[S4]` and `[S5]` and quote the current
text. Marked **secondary-provenance**, not primary.

**Finding 4 — in the EU the language obligation attaches to the document, and it is a
Member State variable.** The PRIIPs KID must be written in an official language of the
Member State where the PRIIP is distributed, or another language accepted by that
Member State's competent authority; and where a PRIIP is promoted through marketing
documents in a Member State's official language(s), the KID must at least be in the
corresponding official language(s) — Article 7(1), Regulation (EU) 1286/2014 `[S9]`.
The joint committee publishes a table of per-Member-State language requirements
precisely because the answer differs by state `[S10]`. **ASSUMPTION:** this means an
eval cannot hold a single "the disclosure language must equal the session language"
rule for Europe; the rule is a lookup, and the lookup is a published table.

**Finding 5 — in Hong Kong, bilingual is sometimes the requirement, and I could only
confirm it for one narrow product route.** For Mainland funds recognised under Mutual
Recognition, a **bilingual (Traditional Chinese and English)** Hong Kong offering
document, covering document and product key facts statement must be provided to Hong
Kong investors `[S12-sec]`. Separately, SFC advertising expectations require an upfront
risk disclosure box with font size comparable to the main text `[S11-sec]`. I did **not**
establish a general bilingual rule for all SFC-authorised products, and I am not going
to imply one. Scope: MRF route only, source secondary (a statutory investor-education
body and a law-firm note).

**Finding 6 — Singapore's plain-language expectations are expressed in English.** MAS
consulted on guidelines for plain English in prospectuses `[S7]`, and the Product
Highlights Sheet practice note expects clear simple language avoiding legal, financial
and technical jargon `[S8]`. FAA-N16 sets the substantive advisory duties — reasonable
basis under s.36 FAA, the KYC information set, and documentation of the basis for the
recommendation `[S6]`. **ASSUMPTION**, and an important one: the documentation regime is
English-centred while a large share of the actual advice is delivered in Mandarin,
Malay or Tamil, so the gap between "what the file says" and "what the client heard" is
a *language* gap, and that gap is a suitability risk the corpus should model directly.

**Finding 7 — sometimes the language rule is a behaviour, not a document.** Under
Québec's Charter of the French Language as amended (Bill 96), contracts of adhesion —
which includes insurance contracts — must be drawn up in French, and a party may be
bound by a non-French version only if, **after having examined the French version**, it
is that party's express wish; related notices, letters and product summaries follow the
same sequence `[S29-sec]`. Source is law-firm commentary (secondary); the primary
instrument is the Charter (CQLR c. C-11) as amended, which I did not fetch.

Note what that shape is: **an ordered behaviour in the conversation.** French version
first, examined, then an express election. `lab/checks/PhraseContract` already decides
ordering on event-stream position rather than timestamp, and that is exactly the
primitive this needs. This is the single best "the framework already fits" row in the
file.

### 3.3 Numbers, dates, currency and magnitude

A capture check ("did the adviser record the client's investable amount correctly?")
is a string or numeric comparison, and every convention below silently breaks it.

| Convention | The variation | What silently fails |
|---|---|---|
| Magnitude words, South Asia | lakh = 10⁵, crore = 10⁷; written 1,00,000 and 1,00,00,000 `[S25-sec]` | "fifty lakh" parsed as 50, or as 50,000. A ₹50,00,000 goal recorded as ₹500,000 is a suitability error the eval scores as a capture success. |
| Magnitude words, East Asia | 万/萬 = 10⁴ and 億 = 10⁸ in Chinese and Japanese; 만/억 in Korean | "三千万" is 30,000,000, not 3,000 anything. A myriad-based system has no word for "million", so a literal translation pipeline invents one. |
| Digit grouping | Indian 2-2-3 grouping is expressible in CLDR as the pattern `#,##,##0.###` `[S24]` | A formatter hard-coded to 3-digit grouping renders a correct value in a form the client cannot read back — and a *readback confirmation* check then fails for a formatting reason. |
| Decimal separator | comma in de/fr/es/it/id/vi and much of Europe; point in en/ja/zh `[S24]` | "0,68 per cent" vs "0.68 per cent". Naive float parsing turns a 0.68% fee into 68. The existing Spanish locale row already contains `0,68 por ciento` — the trap is already in the corpus, un-exercised by a numeric check. |
| Group separator | some locales use U+00A0 NO-BREAK SPACE `[S24]` | An invisible non-ASCII character defeats exact-match assertions and produces the least debuggable class of test failure there is. |
| Numeral shapes | Arabic-Indic digits ٠١٢٣ are a CLDR numbering system alongside Western `[S24]` | Digit-shape mismatch fails a comparison between two numerically identical values. |
| Date order | D/M/Y in most of Europe and much of Asia, M/D/Y in the US, Y/M/D in Japan/Korea/China `[S24]` | 03/04 is two different months. On a policy commencement date, that is a real financial fact recorded wrong. |
| Currency | same symbol across markets: $ for USD/SGD/HKD/AUD/CAD/MXN | "$500,000" without a currency tag is between one and eight times a different amount depending on market. |

**What I would build.** Locale-aware normalisation inside the capture check, exercised
by paired scenarios — the *same* advisory conversation, the *same* underlying amount,
expressed in two conventions, expected to produce the identical captured value. If the
two rows disagree, the defect is in the check and not in the agent, and that is a
distinction the current corpus cannot make. This is the cheapest high-yield work in
this whole document: it is deterministic, needs no model, and needs no live keys.

### 3.4 Honorifics, register, and indirect refusal — the part that corrupts the business metric

Everything above is a capture bug. This one is a **label** bug, which is worse, because
a wrong label propagates into every downstream aggregate and cannot be found by staring
at the aggregate.

**The mechanism.** In several of these markets a refusal is delivered as
non-commitment. The cross-cultural pragmatics literature is consistent on the
direction: both Chinese and American respondents prefer indirect refusal strategies,
but Americans use a materially greater proportion of direct ones; Japanese speakers
overwhelmingly prefer indirect strategies, with unfinished sentences being a
conventionalised polite refusal; and refusals in English-as-lingua-franca by Chinese
speakers are typically indirect, mitigated, and structured as dispreferred with a
deferred refusal head act `[S26-sec]`. The Beebe et al. direct/indirect taxonomy is the
standard frame `[S26-sec]`.

**ASSUMPTION**, and I want it labelled loudly because it is the most consequential
inference in this file: the specific mapping "*I will consider it* / *let me think about
it* / *I'll discuss with my family* = a settled no" is a real and widely-reported
feature of East Asian business communication, but I did not find a source that
quantifies it for a *financial advisory* setting, and the strength of the convention
plainly varies by market, relationship and product. Treat the mapping as a hypothesis
the corpus should *test*, not a fact the scorer should assume.

**Why this is a measurement problem and not a cultural note.** Trace the error:

```
adviser asks for the close
client indirectly refuses ("I will consider it and revert")
  -> scorer trained on direct Anglophone refusal labels the outcome OPEN, not LOST
     -> the session enters the "still in play" denominator
        -> pipeline-conversion and positioning-readiness KPIs are computed on a
           denominator that contains dead calls
           -> the coaching recommendation is "follow up", when the correct
              recommendation is "you lost this at the objection and did not notice"
```

The platform's headline claims are **business** outcomes — conversion, product
penetration, time to first sale `[BRIEF]`. A coaching platform cannot move a business
outcome directly; it can only change adviser **behaviour**. So the corpus's whole job
is to grade behaviours and state which business metric each one leads. Misreading
indirect refusal breaks that chain at the first link: it corrupts the outcome label
that every behavioural KPI is validated against. **You cannot calibrate a
conversion-linked judge in a high-context market until the refusal taxonomy is
market-specific.**

**Registers and honorifics, same class of problem, lower severity.** Japanese keigo,
Korean speech levels, Vietnamese kinship address, Indonesian Bapak/Ibu, and the
grammaticalised T-V distinction in German, French, Spanish and Italian all encode
something a scorer routinely grades under "professionalism" or "rapport". A model
scoring politeness on Anglophone cues has no access to the actual signal.
**ASSUMPTION:** the useful eval here is not "was the adviser polite" but "was the
register *stable* and *appropriate to the relationship*", because an unrequested switch
from formal to informal is a specific, detectable, market-relevant error, and stability
is measurable without a cultural oracle.

**What I would build.** A refusal-taxonomy judge whose labels are `direct-no`,
`conventional-indirect-no`, `genuine-defer`, `open`, calibrated **separately per market**
against human labels — and the calibration report published per market, not pooled. The
repo already has the instrument for this: `lab/judges.calibrate()` producing
TPR/TNR/precision/recall/F1/kappa, and `JudgeRegistry.require_calibrated()` which
*raises* below threshold. Pooling markets would hide precisely the disparity that
matters, so per-market calibration is not extra rigour, it is the whole point. And the
expected finding is publishable either way: if kappa on `conventional-indirect-no` is
high in the UK and low in Japan, that gap is the deliverable.

### 3.5 Names and addresses

The W3C internationalisation article on personal names is the standard reference and
covers the four things that break form-filling and readback: multiple family names
(Spain, Latin America), no family name at all (Iceland), different name ordering
(China, Korea, Japan), and non-Latin characters `[S23]`. Mononyms remain common in parts
of Indonesia and South India `[S23-sec]`.

For this corpus specifically:

- **Name order.** Chinese, Korean, Japanese, Vietnamese and Hungarian put the family
  name first. A "confirm the client's name" check that asserts `given + family` fails a
  correct readback. **ASSUMPTION** that advisory readback is a graded behaviour at all —
  but it should be, since it is the cheapest possible identity control.
- **Two surnames.** Spanish-speaking markets carry paternal + maternal surname;
  truncating to one is a KYC mismatch waiting to happen, and Mexico is on my market
  list.
- **Romanisation variants.** The same Cantonese name romanises differently on a HKID,
  a passport and a bank record; the same Mandarin name differs across Pinyin and
  Wade-Giles; Vietnamese names lose diacritics in Latin-1 systems. **ASSUMPTION:** an
  identity-verification behaviour check must accept a *set* of acceptable strings, not
  one — and if it does not, the eval will report identity failures that are really
  transliteration variance.
- **Addresses.** Postcode presence and shape, and whether the address runs
  large-to-small (Japan, China) or small-to-large (US, UK), both vary. **ASSUMPTION**
  on the eval consequence, which is the same shape as the name one.

### 3.6 Accent, STT quality, and the question of what you are actually measuring

**The core confound.** In a voice eval, a low behavioural score can mean the adviser
behaved badly *or* that the speech engine misheard them. Those are different findings
with different owners, and a suite that cannot separate them is measuring the vendor
stack and reporting it as a skills gap. Systematically, that means an accent with worse
STT support produces a worse coaching score — and if certification is a pass/fail gate
on a person's readiness to sell `[BRIEF]`, that is an adverse-impact problem, not just a
metrics problem.

**The evidence that this is real and large.** Five commercial ASR systems (Amazon,
Apple, Google, IBM, Microsoft) transcribing matched structured interviews showed an
average WER of **0.35 for Black speakers versus 0.19 for white speakers** — nearly
double, on the same task, matched for age and gender `[S18]`. On accents specifically,
evaluation of Whisper found better recognition of American English than British or
Australian, and native accents outperforming non-native `[S19-sec]`. Reported mean WER
for Whisper large-v3 in one comparison was 9.3% against 26.1% for Wav2Vec2 `[S19-sec]`
— which is mostly a reminder that any absolute WER number is meaningless without its
corpus.

Applied to my market list: **ASSUMPTION**, ordered from hardest to easiest — Cantonese
and Chinese dialects, Thai (unsegmented script), Vietnamese (tone plus diacritics),
Tamil and Malay (low commercial resourcing; Malay is Nova-2/base only in our own stack
`[S22]`), heavily accented non-native English from South and Southeast Asia, then
Mandarin/Japanese/Korean, then European languages, then US English. The ordering is
inference from resourcing and script properties, not measurement — but it is a
*testable* ordering, which is the point.

**What the repo already has, and it is the right thing.** `lab/voice` carries a WER
implementation, a timing calibration gate, silence attribution, perturbations, and a
documented normalisation trap. The missing piece is not a metric, it is a **discipline**:

> No behavioural score from a voice session is reportable without the WER of that
> session's transcript reported beside it, and no cross-market comparison of
> behavioural scores is reportable at all unless the WERs are within a stated band.

That is a reporting rule of the same family as the denominator-safety rule already
enforced in `lab/report` — a naked percentage is a defect there; a naked cross-accent
behavioural comparison should be a defect too. And it needs one more instrument to be
honest: **a text-only control run of the same scenario**. Text score minus voice score,
per market, *is* the speech-engine contribution, separated from the agent. Without that
control there is no way to attribute the gap, and with it the attribution is arithmetic.

---

## 4. Recommendation: the first eight language/market pairs

Selection criteria, in order: (1) does it exercise a *mechanism* not already covered,
(2) is the mechanism one that corrupts a label rather than a field, (3) is there a
regulatory hook with a real citation, (4) can I get data and STT support for it. The
minimum was two code-switching pairs and one non-Latin script; this list carries three
code-switching pairs (#2, #3, #6) and five pairs written in a non-Latin script (#2, #3,
#4, #6, #8), across four scripts — Han, Kana/Kanji, Devanagari and Arabic.

| # | Pair | Mechanism it is bought for | Graded behaviour | Business metric it leads `[BRIEF]` | Cost |
|---|---|---|---|---|---|
| 1 | **en-GB / United Kingdom** | Baseline. Prescribed wording exists for one product category and explicitly does not for another `[S2]` `[S3]` — forces the register to be keyed by (jurisdiction, product), not jurisdiction | required disclosures given, in a category-correct form | product penetration; positioning readiness | Lowest — already largely built |
| 2 | **en-SG ⇄ zh-CN (Mandarin), Singapore** | Code-switching **outside** the supported set `[S21]`; Singlish register; MAS documentation-in-English vs advice-in-Mandarin gap `[S6]` `[S7]` | disclosure delivered and *understood-checked* across a language boundary | conversion rate; time to first sale | High value, high effort — needs token-level LID |
| 3 | **yue-HK ⇄ en (Cantonese), Hong Kong** | The other unsupported code-switching pair `[S21]` `[S22]`; English financial nouns inside Cantonese clauses; ~40% of professionals not trilingual `[S13]` | suitability questioning in the client's language, not the document's | product penetration; active ratio | High value, high effort |
| 4 | **ja-JP / Japan** | **Indirect refusal** at its strongest `[S26-sec]`; keigo; 万/億 magnitudes | correctly detecting a lost call and stopping, vs pushing a dead close | conversion rate — via the outcome **label**, so this one gates the metric itself | Medium; needs native human labels |
| 5 | **es-US / United States** | The only pair with a citable answer on translation: complete translation is acceptable **only alongside English**, and "U.S. Securities and Exchange Commission" must not be translated `[S4]` `[S5]` | delivering the disclosure bilingually; leaving the named entity verbatim | conversion; contract-value expansion | **Lowest cost, highest citation density — do this first alongside #1** |
| 6 | **hi-IN ⇄ en (Hinglish), India** | Code-switching that **is** supported `[S21]` — the control that separates "code-switching is hard" from "*this* code-switching is unsupported"; plus lakh/crore magnitudes `[S25-sec]` | numeric capture and readback of a goal amount | time to first sale; new-agent onboarding | Low-medium — best ratio on the list |
| 7 | **fr-CA / Québec, Canada** | A language rule that is an **ordered behaviour**: French version examined before an election to be bound in another language `[S29-sec]` | sequence compliance — exactly what `PhraseContract` decides on event position | penetration; and it is the clearest audit story on the list | Low — deterministic, no model needed |
| 8 | **ar-AE / United Arab Emirates** | Non-Latin **RTL** script, Arabic-Indic numeral option `[S24]`, and an expat client base that makes language *selection* itself a behaviour | choosing and holding the client's language; numeric capture in a second numeral system | active ratio; onboarding | Medium — mostly rendering and normalisation work |

**Why not the obvious ones.** de-DE, fr-FR, it-IT and es-ES add a T-V distinction and a
decimal comma — both already exercised by #5 and #7 — and add no new mechanism, so they
are breadth, not coverage. ko-KR duplicates #4's mechanism at higher data cost. th-TH
and vi-VN are the most interesting *STT* pairs on the whole list, and they belong in a
second wave aimed squarely at §3.6, where the deliverable is a WER table rather than a
behavioural score.

**Sequencing, if the budget only funds three:** #5, #1, #7. All three are deterministic,
all three have real citations, none needs a native-speaker labelling panel, and together
they establish the three structural claims — that the register must be keyed by product
category, that a translation obligation can be a *pairing* obligation, and that a
language rule can be an *ordering* obligation. #2, #3 and #4 are the ones worth
presenting to an employer, and they are also the ones that need human labels, so they
should be scoped as a funded second phase rather than promised in a first.

---

## 5. What this changes in the repo

Small, concrete, and none of it redesigns anything:

1. `roleplay/register.py` — `JURISDICTIONS` is keyed by jurisdiction alone. §3.2
   Finding 1 says the real key is **(jurisdiction, product category)**, sourced `[S2]`
   `[S3]`. That is a data-shape change with a citation behind it.
2. A `verbatim_entities` concept in the register: strings that must survive translation
   unchanged `[S4]`. Deterministic, cheap, cited.
3. Locale-aware numeric normalisation in the capture checks, with paired same-amount
   different-convention scenarios (§3.3). No model, no keys.
4. Token-level language tags on the `Trace`, plus CMI and I-index as *measured
   metadata* (§3.1) — enabling score stratification by code-mixing band.
5. A reporting rule for voice: behavioural score is not reportable without session WER
   beside it, and a text-only control run per market to separate engine from agent
   (§3.6).
6. Per-market judge calibration for refusal taxonomy, never pooled (§3.4).

---

## 6. Counts

Counted mechanically, not estimated. "Body" means §0 to §5 — this section and the
Sources table are excluded so that the count cannot count itself. Reproduce it:

```sh
F=docs/_research/markets_languages.md
awk '/^## 6. Counts/{exit} {print}' $F > /tmp/body.md
grep -o '\[S[0-9][0-9]*[a-z-]*\]' /tmp/body.md | wc -l            # 79  inline refs
grep -o '\[S[0-9][0-9]*[a-z-]*\]' /tmp/body.md | sort -u | wc -l  # 31  distinct refs used
grep -o 'ASSUMPTION' /tmp/body.md | wc -l                          # 33  assumption labels
grep -o '\[BRIEF\]' /tmp/body.md | wc -l                           #  5  brief labels
grep -c '^| `\[S' $F                                               # 30  = 29 source rows + §0 legend
# and the cross-check that matters: every source row is actually cited, and every
# citation resolves to a row (the two -sec sub-labels resolve inside their parent row)
comm -3 <(grep -o '\[S[0-9][0-9]*[a-z-]*\]' /tmp/body.md | sort -u) \
        <(grep -o '^| `\[S[0-9][0-9]*[a-z-]*\]' $F | sed 's/^| `//' | sort -u)
```

| | Count |
|---|---|
| Rows in the Sources table (§7) | 29 |
| — of which **primary** (regulator, statute, statistics office, peer-reviewed paper, vendor's own docs) | 20 |
| — of which **secondary** (law-firm note, encyclopaedia, or search-result extract of a page I could not fetch) | 9 |
| Sub-labels documented inside a parent row rather than as their own row (`[S14-sec]`, `[S23-sec]`) | 2 |
| Source rows never actually cited in the body | **0** |
| Citations in the body that do not resolve to a source | **0** |
| Inline `[S##]` citation references in the body | 79 |
| Distinct source refs cited in the body | 31 (all 29 rows, plus the 2 sub-labels) |
| Occurrences of the **ASSUMPTION** label in the body | 33 |
| — of which is the §0 legend rather than a claim | 1 |
| **Net claims explicitly labelled ASSUMPTION** | **32** |
| Occurrences of `[BRIEF]` (given design target, vendor marketing, unverified) | 5 |
| Unlabelled factual claims | **0** — that is the invariant this file exists to hold |

Ratio of sourced references to assumptions: 79:32, roughly **2.5:1**. I would not present that
as good. The assumptions cluster in two places — the 24-market reconstruction (§1,
unavoidable, and flagged three times) and the per-market register/refusal claims (§2.3,
§3.4), which are the claims that most need a native-speaker review before anyone acts
on them. The sourcing is strongest exactly where it matters most: prescribed wording
(§3.2), STT disparity (§3.6), and the code-switching coverage gap (§3.1).

**The single biggest hole.** §3.4 asserts that misreading indirect refusal corrupts
conversion metrics, and that is the most load-bearing argument in the file — it is the
bridge from "language differences" to "your business metric is wrong". It rests on
general pragmatics literature `[S26-sec]` plus an explicit **ASSUMPTION** about the
advisory setting. It is a hypothesis with a clear test, and the test is #4 in §4. Until
that test runs, it should be presented as a hypothesis, and anyone who presents it as a
finding is doing the thing this repo is supposed to be the opposite of.

---

## 7. Sources

| Ref | Type | Source |
|---|---|---|
| `[S1]` | primary | FCA Handbook, COBS 4.6.2R(4) — prominent past-performance warning. https://handbook.fca.org.uk/handbook/cobs4/cobs4s6 (fetched) |
| `[S2]` | primary | FCA, "Risk warnings for mainstream investments" — "We do not prescribe risk wording for mainstream investments"; COBS 4.2.1R(1), COBS 4.5.2R(2)/4.5A.3R(2)(b). https://www.fca.org.uk/firms/risk-warnings-mainstream-investments (fetched) |
| `[S3]` | primary | FCA PS22/10, "Strengthening our financial promotion rules for high-risk investments" — prescribed RMMI/NMMI risk-warning wording. https://www.fca.org.uk/publication/policy/ps22-10.pdf |
| `[S4]` | **secondary-provenance** | SEC staff, "Frequently Asked Questions on Regulation Best Interest" (posted 7 May 2020), translation FAQ. https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/faq-regulation-best — **direct fetch returned HTTP 403; wording taken from a search-result extract of this page. Re-fetch and re-quote before external use.** |
| `[S5]` | **secondary-provenance** | SEC staff, "Frequently Asked Questions on Form CRS", translation FAQ. https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/frequently-asked-questions-form-crs — same caveat as `[S4]`. |
| `[S6]` | primary | MAS Notice FAA-N16, Recommendations on Investment Products (s.36 FAA reasonable basis, KYC, documentation). https://www.mas.gov.sg/regulation/notices/notice-faa-n16 |
| `[S7]` | primary | MAS media release (2015), "MAS Proposes Guidelines to Use Plain English in Prospectuses". https://www.mas.gov.sg/news/media-releases/2015/mas-proposes-guidelines-to-use-plain-english-in-prospectuses |
| `[S8]` | primary | MAS Practice Note SFA-PN-02 on the Product Highlights Sheet — clear and simple language. https://www.mas.gov.sg/-/media/MAS/resource/legislation_guidelines/securities_futures/sub_legislation/Practice-Note-on-the-Product-Highlights-Sheet-SFA-PN-02.pdf |
| `[S9]` | primary | Regulation (EU) No 1286/2014 (PRIIPs), Article 7(1) — KID language. https://eur-lex.europa.eu/eli/reg/2014/1286/oj/eng |
| `[S10]` | primary | ESMA/EIOPA/EBA Joint Committee, JC 2024 44 — table of Member State language and ex-ante notification requirements for the PRIIPs KID. https://www.esma.europa.eu/sites/default/files/2024-07/JC_2024_44_Table_MS_language_ex_ante_notification_PRIIPs_KID_CL.pdf |
| `[S11-sec]` | secondary | SFC advertising expectations for authorised funds — upfront risk disclosure box, font size comparable to main text (law-firm note summarising SFC guidelines; primary is the SFC Handbook / Advertising Guidelines). https://www.deacons.com/2024/08/29/reminders-for-advertisements-of-authorised-funds/ · primary: https://www.sfc.hk/-/media/EN/assets/components/codes/files-current/web/codes/sfc-handbook-for-unit-trusts-and-mutual-funds/sfc-handbook-for-unit-trusts-and-mutual-funds.pdf |
| `[S12-sec]` | secondary | Investor and Financial Education Council (HK statutory body) on Mutual Recognition of Funds — bilingual (Traditional Chinese and English) HK offering document, covering document and KFS. https://www.ifec.org.hk/web/en/other-resources/hot-topics/mutual_recognition_of_funds.page |
| `[S13]` | **primary, read directly** | HK Census & Statistics Dept, 2021 Population Census thematic article "Use of Language by Hong Kong Population" — English 45.1%→57.7%; Putonghua 49.5%→56.5%; Cantonese 97.4%→96.0%; 14.1% other Chinese dialects; biliterate-and-trilingual 58.8% managers / 60.3% professionals / 57.5% associate professionals. https://www.census2021.gov.hk/doc/pub/21C_Articles_Use_of_Language.pdf (PDF text extracted and read) |
| `[S14]` | primary | Singapore Dept of Statistics, Census of Population 2020 Statistical Release 1 — English the language most frequently spoken at home for 48.3% of residents aged 5+. https://www.parliament.gov.sg/docs/default-source/default-document-library/cop2020sr1.pdf · `[S14-sec]` the Mandarin/dialect/Malay/Tamil sub-percentages are from secondary reporting of the same release; the SingStat infographic PDF 404'd on fetch |
| `[S15]` | primary | Lyu, Tan et al., "SEAME: a Mandarin-English code-switching speech corpus in south-east Asia", Interspeech 2010 — Singapore/Malaysia speakers; 12% monolingual Mandarin and 6% monolingual English segments. https://www.isca-archive.org/interspeech_2010/lyu10_interspeech.html |
| `[S16]` | primary | Code-mixing metrics: Code-Mixing Index (Gambäck & Das), M-index, I-index, and their documented limitations — "Challenges and Limitations with the Metrics Measuring the Complexity of Code-Mixed Text", CALCS 2021. https://aclanthology.org/2021.calcs-1.2.pdf |
| `[S17]` | primary | Sitaram et al., "A Survey of Code-switched Speech and Language Processing", arXiv:1904.00784. https://arxiv.org/pdf/1904.00784 |
| `[S18]` | primary | Koenecke et al., "Racial disparities in automated speech recognition", PNAS 117:7684–7689 (2020) — mean WER 0.35 for Black vs 0.19 for white speakers across five commercial ASR systems. doi:10.1073/pnas.1915768117 · https://www.pnas.org/doi/10.1073/pnas.1915768117 |
| `[S19-sec]` | secondary | Whisper accent evaluation — American English better recognised than British/Australian, native better than non-native; large-v3 mean WER 9.3% vs Wav2Vec2 26.1% in one comparison. JASA Express Letters 4(2):025206 (2024), figures taken from abstract/search extract rather than the full text. https://pubs.aip.org/asa/jel/article/4/2/025206/3267247/ |
| `[S20]` | primary (vendor docs) | Deepgram, "Multilingual Codeswitching" — available on Nova-2, Nova-3 and Flux Multilingual; `endpointing=100` recommended for streaming code-switching. https://developers.deepgram.com/docs/multilingual-code-switching (fetched) |
| `[S21]` | primary (vendor docs) | Deepgram, "Nova-3 Multilingual: Major WER Improvements Across Languages" — supported set: English, Spanish, French, German, Hindi, Italian, Japanese, Dutch, Russian, Portuguese; ~34% batch / ~21% streaming relative WER reduction on code-switching. https://deepgram.com/learn/nova-3-multilingual-major-wer-improvements-across-languages (fetched) |
| `[S22]` | primary (vendor docs) | Deepgram, "Models & Languages Overview" — Nova-3 monolingual coverage incl. Chinese (Mandarin & Cantonese), Korean, Vietnamese, Indonesian, Thai, Arabic, Tamil; Malay on Nova-2/base. https://developers.deepgram.com/docs/models-languages-overview (fetched) |
| `[S23]` | primary | R. Ishida, W3C Internationalization, "Personal names around the world" — multiple family names, absent family names, name ordering, non-Latin characters. https://www.w3.org/International/questions/qa-personal-names · `[S23-sec]` mononym prevalence in parts of Indonesia and South India from secondary reference |
| `[S24]` | primary | Unicode CLDR / UTS #35 Part 3: Numbers — locale grouping patterns incl. Indian `#,##,##0.###`, locale-substituted decimal and group separators (incl. U+00A0), alternate numbering systems, date-order data. https://www.unicode.org/reports/tr35/tr35-numbers.html · https://sites.google.com/unicode.org/cldr/translation/number-currency-formats/number-and-currency-patterns |
| `[S25-sec]` | secondary | Indian numbering system — lakh = 10⁵, crore = 10⁷, 2-2-3 grouping written 1,00,000 and 1,00,00,000; standard in Indian government and central-bank financial reporting. Encyclopaedic source; a primary example is any RBI publication reporting figures in ₹ crore. https://en.wikipedia.org/wiki/Indian_numbering_system |
| `[S26-sec]` | secondary | Refusal-speech-act pragmatics: Beebe et al. direct/indirect taxonomy; Chinese and American speakers both prefer indirect strategies with Americans more direct; Japanese preference for indirect and conventionalised unfinished-sentence refusals; indirect, mitigated, deferred refusal head acts in Chinese English-as-lingua-franca. Journal articles accessed via abstracts/search extracts. https://www.academypublication.com/issues/past/tpls/vol02/02/07.pdf · https://www.sciencedirect.com/science/article/pii/S037821662400122X · https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8929272/ |
| `[S27]` | primary | IOSCO membership — 134 ordinary members, the national securities regulator per jurisdiction. https://www.iosco.org/about/?subsection=membership |
| `[S28-sec]` | secondary | English may be used in addition to Chinese for official purposes in Hong Kong (Basic Law art. 9). Encyclopaedic source; primary is the Basic Law itself. https://en.wikipedia.org/wiki/Bilingualism_in_Hong_Kong |
| `[S29-sec]` | secondary | Québec Charter of the French Language as amended (Bill 96) — French-first requirement for contracts of adhesion incl. insurance, and for related notices and product summaries. Law-firm/industry commentary; primary instrument is CQLR c. C-11, not fetched. https://gowlingwlg.com/en/insights-resources/articles/2023/bill-96-s-french-first-rule-takes-effect · https://www.lavery.ca/en/publications/our-publications/4308-amendments-to-the-charter-of-the-french-language-impacts-on-the-insurance-sector.html |

**Note on `[BRIEF]`.** The 24-market count, three regions, three hubs, 200,000+
advisers, four named regulatory regimes and the business metrics (+2.6x conversion,
+15% penetration, +51% positioning readiness, 75% reduction in time to first sale, +15%
active ratio, 300% conversion increase, 8x contract-value expansion) are the design
target handed to this corpus, drawn from a vendor's public marketing. They are
**claims, not measurements**, and this file uses them only to decide what to build —
never as evidence that anything works.
