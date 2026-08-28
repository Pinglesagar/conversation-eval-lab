# R3 — What the market actually asks for

Research only. Nothing here is a change to the repository, and nothing here should be
built without a decision from the owner first.

Date of the survey: 28 August 2026. Everything below is either cited to a posting URL,
reproducible from a command recorded in this file, or explicitly labelled an
**ASSUMPTION**. There is no third category.

---

## 0. Method, and the honest limits of it

### 0.1 The corpus

Two tiers, kept separate on purpose so no rate mixes them.

**Primary corpus — 18 postings I fetched and read in full.** These are the denominator
for every count in §1. They were selected by searching for the six title families named
in the brief (QA Automation Engineer AI, AI Evaluation Engineer, LLM Evaluation Engineer,
Applied AI QA, Conversational AI QA, Voice AI QA), then keeping every posting whose full
text I could actually retrieve. Postings that were closed, JS-only, 403, or 404 were
dropped rather than coded from a summary.

| # | Title | Organisation | Location | Stage / kind |
|---|---|---|---|---|
| P1 | LLM Evaluation Engineer | ThirdLaw | San Francisco | VC-backed startup, AI runtime control ([src](https://jobs.ashbyhq.com/thirdlaw/146d2379-88e4-4073-9c2a-1899871fdaeb)) |
| P2 | Senior QA Engineer | Caylent | Remote, US | Cloud consultancy, contact-centre work ([src](https://job-boards.greenhouse.io/caylent/jobs/6138112004)) |
| P3 | QA Engineer (Voice Agents) | Bamboo Works | Remote, AU | SME ([src](https://bambooworks.applytojob.com/apply/LPuzRpoczU/QA-Engineer-Voice-Agents)) |
| P4 | Senior AI QA Engineer | Turing | Bengaluru | Scale-up ([src](https://work.turing.com/r/iljutAmuej)) |
| P5 | AI Quality Assurance Intern | Cresta | Toronto | Conversational-AI vendor ([src](https://job-boards.greenhouse.io/cresta/jobs/5047247008)) |
| P6 | Associate QA Engineer | Anaplan | Manchester, UK | Public enterprise SaaS ([src](https://builtin.com/job/associate-qa-engineer/8211927)) |
| P7 | Research Engineer, Evals | Variance (YC) | San Francisco | Seed, fraud/risk ([src](https://www.ycombinator.com/companies/variance/jobs/Eok2YSs-research-engineer-evals)) |
| P8 | Red Team Engineer, Safeguards | Anthropic | SF / remote-friendly | Frontier lab ([src](https://job-boards.greenhouse.io/anthropic/jobs/5320469008)) |
| P9 | Quality Engineer (AI & Test Automation) | Cognizant | TX/NC/NJ/FL, US | Global services, graduate level ([src](https://careers.cognizant.com/us-en/jobs/46858/quality-engineer-ai-test-automation/)) |
| P10 | AI Quality Engineer | Accenture | Singapore | Global services, early career ([src](https://www.accenture.com/in-en/careers/jobdetails?id=R00344458_en)) |
| P11 | AI Agent Engineer | General Motors | Austin TX / Warren MI | Enterprise in-house ([src](https://search-careers.gm.com/en/jobs/jr-202606937/ai-agent-engineer/)) |
| P12 | AI Evaluation Engineer | TXP | London / Bristol / Manchester | Consultancy, public-sector delivery ([src](https://www.itjobswatch.co.uk/jv/TXP/AI-Evaluation-Engineer-Job-London-UK-527xgz)) |
| P13 | Machine Learning Evaluation Engineer | writewithmarker | London | Seed ([src](https://machinelearningjobs.co.uk/view-job/machine-learning-evaluation-engineer-london-7e648c528365)) |
| P14 | Senior Machine Learning Engineer | Retell AI | San Francisco | Voice-agent platform, YC ([src](https://api.ashbyhq.com/posting-api/job-board/retell-ai)) |
| P15 | Software Engineer, Evals | Glean | Bangalore | Late-stage, enterprise search ([src](https://job-boards.greenhouse.io/gleanwork/jobs/4712438005)) |
| P16 | AI Evaluation & Test Engineer | BharatGen | India | Foundation-model programme ([src](https://bharatgen.com/careers/technology-jobs/ai-evaluation-and-test-engineer/)) |
| P17 | Applied AI Engineer | Future | Remote, continental US | Growth-stage consumer AI ([src](https://job-boards.greenhouse.io/future/jobs/4683133005)) |
| P18 | AI Engineer | WITHIN | Long Island City, NY | Mid-size agency, in-house AI ([src](https://job-boards.greenhouse.io/agencywithin/jobs/5056863007)) |

Regions covered: US (8), UK (3), India (3), Singapore (1), Canada (1), Australia (1),
remote-global (1). Stages covered: frontier lab (1), late-stage (2), scale-up/growth (4),
seed/YC (3), global services (2), enterprise in-house (2), consultancy (2),
public/SME (2).

**Secondary corpus — 14 postings I have a URL for and a quoted requirement from, but did
not read in full.** Used only in §1.4 as corroboration, never inside a primary count.
ID.me Staff SWE AI Agent Evaluations
([src](https://job-boards.greenhouse.io/idme/jobs/7766372003)),
Scale AI Evals Engineer Applied AI ([src](https://scale.com/careers/4629589005)),
Scale AI Staff MLRS LLM Evals ([src](https://job-boards.greenhouse.io/scaleai/jobs/4628044005)),
Anthropic Prompt Engineer Agent Prompts & Evals ([src](https://job-boards.greenhouse.io/anthropic/jobs/5107121008)),
Deloitte GenAI Testing QE Bengaluru ([src](https://southasiacareers.deloitte.com/job/Bengaluru-T&T-EAD-Consultant-AI-ML-Testing-Bengaluru-Engineering/53756844)),
GE Vernova AI QA Engineer GenAI ([src](https://www.themuse.com/jobs/gevernova/ai-qa-engineer-genai-fbbe2e)),
Deqode Gen AI QA Engineer ([src](https://in.linkedin.com/jobs/view/gen-ai-qa-engineer-at-deqode-4444126796)),
Cekura Forward Deployed Engineer ([src](https://www.ycombinator.com/companies/cekura-ai/jobs/0ZGLW69-forward-deployed-engineer-us)),
Myria Lead AI Agent Engineer Prompting & Evaluation ([src](https://www.ycombinator.com/companies/myria/jobs/LaLEAZF-lead-ai-agent-engineer-prompting-evaluation)),
CollectWise AI Agent Engineer ([src](https://www.ycombinator.com/companies/collectwise/jobs/ZunnO6k-ai-agent-engineer)),
Vector Legal Founding AI Engineer ([src](https://www.ycombinator.com/companies/vector-legal/jobs/SoiB0TW-founding-ai-engineer)),
PolyAI Senior Software Quality Engineer ([src](https://job-boards.eu.greenhouse.io/polyai/jobs/4878638101)),
Snowflake AI Engineer Cortex Code Quality and Capgemini SDET IVR & Telecom (both listed
at [src](https://scrolltest.com/25-latest-qa-sdet-ai-testing-jobs-march-2026/)).

### 0.2 Limits you should hold against every number below

- **n = 18 is small.** These are proportions in a hand-built sample, not population
  estimates. A 6/18 and a 5/18 are not distinguishable. Only the extremes — the 11s and
  the 1s and the 0s — are worth acting on.
- **Selection is biased toward postings that fetch.** Greenhouse, Ashby and public
  careers pages are over-represented; LinkedIn and Indeed postings, which are the bulk of
  the actual market, are almost absent because they cannot be retrieved. **ASSUMPTION:**
  this biases the sample toward tech-forward employers and probably *over*-states how
  often "eval framework" language appears relative to plain "test the chatbot" language.
- **Coding is mine and unblinded.** I coded each posting once, from the extracted text,
  against the categories in §1.1. There is no second rater and therefore no agreement
  statistic. Treat the table as an ordering, not a measurement.
- **A requirement absent from a posting is not a requirement the employer does not
  have.** Postings are marketing documents of finite length.

### 0.3 Repository facts, and the commands that produced them

Every claim about this repository in §2–§5 comes from one of these, run on
2026-08-28 against a clean tree at `032eab7`:

```
$ .venv/bin/python -m pytest -q --collect-only | tail -1
1980 tests collected in 1.38s

$ .venv/bin/python -m lab.cli validate --coverage | head -8
55/55 scenario files loaded; 0 error(s), 0 warning(s)
suites: happy 15/55, edge 20/55, adversarial 12/55, voice 8/55

$ find scenarios -name '*.yaml' | wc -l
194                     # 55 tablemate + 78 roleplay + 31 advisory + 21 audio + 9 personas

$ for d in lab roleplay tablemate ragcheck; do find $d -name '*.py' | xargs wc -l | tail -1; done
31541 lab   15817 roleplay   5091 tablemate   3108 ragcheck

$ wc -l lab/voice/*.py lab/voice/*/*.py | tail -1
15851 total             # 50.3% of lab

$ wc -l docs/*.md README.md DESIGN.md INTERVIEW_NOTES.md | tail -1
22682 total             # of which docs/WIKI.md is 14802 (65.3%)

$ grep -c '```mermaid' docs/WIKI.md
72
```

The brief says 1,976 tests passing; `--collect-only` reports 1,980 collected. The
four-test difference is skips or xfails and I did not chase it.

---

## 1. Requirement frequency

### 1.1 The table

Denominator is **18 postings read in full** for every row. A posting is counted only
where its own text names the thing; inference from adjacency was not counted.

| Requirement | Count | Postings |
|---|---|---|
| **Build an evaluation framework / harness / pipeline as the core deliverable** | **14 of 18** | P1 P4 P6 P7 P8 P10 P11 P12 P13 P14 P15 P16 P17 P18 |
| Python named explicitly | 13 of 18 | P1 P3 P4 P6 P9 P10 P11 P12 P13 P15 P16 P17 P18 |
| **Evaluate agent / tool-call / multi-turn behaviour** | **11 of 18** | P1 P2 P4 P7 P10 P11 P12 P15 P16 P17 P18 |
| **Observability, tracing or production monitoring of an AI system** | **11 of 18** | P1 P2 P4 P6 P7 P11 P13 P14 P15 P16 P17 |
| Prompt engineering / prompt evaluation | 8 of 18 | P1 P5 P10 P11 P12 P16 P17 P18 |
| Golden / benchmark dataset curation | 8 of 18 | P4 P7 P10 P12 P13 P14 P15 P17 |
| API or backend service testing | 7 of 18 | P3 P4 P6 P9 P10 P17 P18 |
| Cross-functional communication of findings | 7 of 18 | P1 P4 P6 P7 P10 P11 P16 |
| Guardrails / safety enforcement on outputs | 6 of 18 | P1 P2 P8 P11 P16 P18 |
| RAG evaluation | 6 of 18 | P1 P4 P11 P12 P16 P18 |
| Error analysis / failure taxonomy / RCA | 6 of 18 | P1 P4 P7 P12 P16 P18 |
| Latency or throughput measurement | 6 of 18 | P1 P4 P9 P14 P16 P17 |
| Cloud platform (AWS / Azure / GCP) | 6 of 18 | P2 P4 P9 P15 P17 P18 |
| Statistical rigour (precision/recall, calibration, experiment design) | 6 of 18 | P1 P4 P7 P11 P13 P16 |
| Regression suites named explicitly | 6 of 18 | P3 P4 P6 P10 P16 P18 |
| Human evaluation / annotation / rubrics | 5 of 18 | P5 P7 P14 P16 P17 |
| Hallucination / groundedness / factuality metrics | 5 of 18 | P2 P4 P5 P10 P16 |
| LLM-as-judge / model-graded scoring | 5 of 18 | P1 P7 P15 P16 P17 |
| Vector search / embeddings / retrieval infrastructure | 5 of 18 | P1 P4 P7 P15 P18 |
| Browser or UI automation (Playwright / Selenium / Cypress) | 5 of 18 | P2 P4 P6 P10 P16 |
| Named LLM provider APIs | 5 of 18 | P1 P4 P13 P17 P18 |
| Regulated / high-stakes / compliance domain context | 5 of 18 | P1 P7 P10 P12 P16 |
| Voice-specific (ASR / TTS / IVR / barge-in) | 4 of 18 | P2 P3 P5 P14 |
| CI/CD pipeline integration | 4 of 18 | P2 P9 P10 P16 |
| A named eval or observability vendor tool | 4 of 18 | P4 P12 P16 P17 |
| A named agent framework (LangChain, LangGraph, CrewAI, ADK, …) | 4 of 18 | P1 P4 P11 P17 |
| Non-determinism / flakiness handled as a first-class problem | 3 of 18 | P6 P7 P13 |
| Red-teaming / jailbreak / prompt-injection testing | 3 of 18 | P1 P8 P16 |
| Cost or token accounting | 3 of 18 | P4 P16 P17 |
| Multilingual / localisation | 1 of 18 | P2 |
| **Drift or degradation over time, named verbatim** | **0 of 18** | — |

### 1.2 What the shape of that table says

Three things, none of which match the way the eval-tooling blogosphere talks.

**The job is "build the measurement system", not "run the tests".** 14 of 18 want a
framework, harness or pipeline built. Only P5 and P9 read like classical test execution.
This is the strongest signal in the sample and it is the one this repository is best
positioned against.

**Observability ties agent-behaviour evaluation as the joint-second requirement at 11 of
18, and it is the thing this repository is furthest from.** More on that in §4.

**The loud concepts are quieter than expected.** LLM-as-judge is named in 5 of 18.
Red-teaming in 3 of 18. Non-determinism in 3 of 18. Drift, verbatim, in 0 of 18. If you
read eval Twitter you would predict all four near the top. They are not. They are
*differentiators* — things that separate a strong candidate once the framework-building
requirement is already met — not entry criteria.

### 1.3 Where the sample splits by employer kind

Worth naming because it changes which posting you are writing for.

- **Frontier lab and seed-stage research roles (P7, P8, P13, P14)** ask for judgement,
  experimental design, dataset curation and adversarial thinking. They name almost no
  tools. P7 names zero vendor tools; P8 names Burp Suite and Metasploit and nothing else.
- **Global services and enterprise (P9, P10, P11, P2)** ask for automation frameworks,
  cloud, CI/CD, Playwright/TypeScript, ticketing. AI evaluation is a *layer added onto*
  conventional QA, not a replacement for it.
- **Product companies building agents (P1, P4, P15, P16, P17, P18)** are the middle, and
  the closest match to this repository: framework building, tracing, judges, datasets,
  RAG, guardrails, in Python.

### 1.4 Secondary corroboration

The secondary corpus (14 postings, §0.1) does not change the ordering but sharpens two
rows the primary corpus under-counts:

- **Drift**: named verbatim in 2 of 14 secondary postings — ID.me ("instrumenting agentic
  systems for behavioral drift, regression, and failure modes") and GE Vernova ("model
  degradation over time"). Combined: **2 of 32**. Still low.
- **LLM-as-judge**: 2 more (ID.me "LLM-as-judge pipelines", Scale AI evals). Combined:
  **7 of 32**.
- **Golden datasets**: ID.me ("golden dataset construction"), Deloitte, Myria, Vector
  Legal. Combined: **12 of 32**, which promotes it well above where the primary count
  alone put it. Treat golden-dataset construction as a top-tier requirement.

---

## 2. Tools named most often, and what this repository does with each

Three honest buckets. "Touches" means there is executable code in this tree that uses it.
"Exports to" means the repo emits the tool's documented data shape without depending on
it. "No story" means zero code and, where noted, zero prose either.

### 2.1 Touches — real code, exercised by the offline suite

| Tool | Market count | Where in this repo |
|---|---|---|
| Python | 13 of 18 | Whole tree; 3.12 required and enforced by the `python-ok` Makefile target |
| pytest | 3 of 18 (P4 P9 P16) | 1,980 tests collected; `make test` |
| GitHub Actions | 3 of 18 (P2 P9 P11 name CI generally; P2/P9 name GH Actions) | `.github/workflows/ci.yml`, four gates, no secrets block |
| Pydantic | 1 of 18 (P17) | `lab/trace/schema.py` — the trace is validated, not duck-typed |
| Deepgram (STT) | 0 of 18 | `lab/voice/engines/deepgram_stt.py`, 610 lines, real engine + digest cache |
| ElevenLabs (TTS) | 0 of 18 | `lab/voice/engines/elevenlabs_tts.py`, 926 lines |
| LiveKit (WebRTC) | 0 of 18 | `lab/voice/transport/`, ~4,100 lines, `[transport]` extra |
| jiwer / numpy / soundfile / matplotlib | 0 of 18 | WER scoring, perturbation, Opus decode, Pareto + heatmap |
| litellm | 0 of 18 | Provider-agnostic LLM access for judges and live callers |

The awkward line in that table is the middle. **The four tools this repo has the deepest,
most expensive integration with — Deepgram, ElevenLabs, LiveKit, litellm — are named in
0 of 18 postings.** Voice-specific requirements exist (4 of 18) but they are stated as
ASR/NLU/TTS/barge-in as *concepts*, not as vendor names. The vendor integrations are
credibility, not keyword match.

### 2.2 Exports to — emits the shape, does not depend on the package

`lab/report/interop.py` (416 lines, tests in `tests/test_report_interop.py`):

| Tool | Market count | Fidelity |
|---|---|---|
| Langfuse | 2 of 18 (P4 P17) | Lossless round-trip: `to_langfuse_batch` → `from_langfuse_batch` reconstructs the `Trace` exactly, and that equality is a test |
| promptfoo | 0 of 18 primary | One-way projection ("what happened" → "what must keep happening"), documented as non-round-trippable |

Neither is imported or declared as a dependency. That is a defensible design choice and
it is documented as one — but it means a reviewer grepping for `langfuse` in
`pyproject.toml` finds nothing.

### 2.3 No story at all

Verified by grep across `lab roleplay tablemate ragcheck scenarios scripts tests docs
error_analysis README.md DESIGN.md pyproject.toml Makefile .github`:

| Tool | Market count | Repo state |
|---|---|---|
| **OpenTelemetry** | **3 of 18** (P1 P4 P17) | **0 references anywhere**, including prose |
| Playwright / Selenium / Cypress | 5 of 18 | 0 code. One doc, `docs/PLAYWRIGHT_NOTES.md`, which exists specifically to state the gap rather than close it |
| Vector stores (Pinecone, Weaviate, Qdrant, FAISS, pgvector, Milvus) | 5 of 18 | 0 code. `ragcheck` retrieves lexically over a 16-chunk corpus |
| LangChain / LangGraph / CrewAI / AutoGen / Semantic Kernel | 4 of 18 | 0 code |
| LangSmith, Arize Phoenix, Braintrust, DeepEval, Ragas, MLflow | 4 of 18 | Named in README and WIKI prose only; 0 code, 0 fixtures |
| Postman / REST Assured / API test tooling | 7 of 18 name API testing | 0. There is no service under test with an HTTP surface |
| AWS / Azure / GCP | 6 of 18 | 0. Deliberate — the zero-keys rule forbids it |
| Jenkins / GitLab CI | 2 of 18 (P9) | 0. GitHub Actions only |
| pandas / scipy / scikit-learn | 0 of 18 named directly | 0. Statistics are hand-rolled (Wilson bounds in `lab/judges/calibration.py`, Cohen's kappa in the judge report). Defensible for a portfolio, a liability at a shop that expects the standard stack |

---

## 3. Concepts named most often, mapped to what the repository demonstrates

| Concept | Market | What this repo has | Verdict |
|---|---|---|---|
| **Golden datasets** | 12 of 32 combined | 194 committed YAML scenario files across five corpora; schema-validated with a coverage report (`evallab validate --coverage`); every corpus reviewable by a non-programmer because it is data, not code | **Strong.** The validation-and-coverage step is more than most postings ask for |
| **LLM-as-judge** | 7 of 32 combined | `lab/judges` with `calibrate()` → TPR/TNR/precision/recall/F1/Cohen's kappa/confusion, a registry that raises below threshold, and a committed v1→v2 iteration where v1 **fails** the gate at TPR 0.250 (2/8) and v2 passes | **Strongest single asset in the repo.** A judge that is *refused* on the record is rarer than a judge that exists |
| **Non-determinism** | 3 of 18 primary | `lab/simulator/passk.py` (pass^k), `flake_band.py` (measured band, 760 lines), stability treated as a verdict dimension; Wilson lower bounds so "3 of 3" is not quoted as 1.0 | **Strong, and under-asked-for.** Lead with the *discipline*, not the machinery |
| **RAG evaluation** | 6 of 18 | `ragcheck`: recall@k, MRR, nDCG, per-claim faithfulness, 18 hand-labelled questions over a 16-chunk corpus; the demo's opening example is a row where every retrieval metric is 1.0 and the answer is wrong by 67% | **Present and pedagogically sharp, but small and lexical.** See §4.4 |
| **CI gating** | 4 of 18 primary | Four ordered gates in `ci.yml`: offline suite → corpus validation → timing + judge calibration → case study reproduced **byte-for-byte** against the committed baseline via `git diff --exit-code`. The committed report says FAIL and the build is still green, because the *regression* gate is what is being enforced | **Strong and unusually well-argued.** The FAIL-but-green separation is a genuine idea |
| **Observability** | 11 of 18 | A bespoke 15-kind event trace, and an exporter to one vendor | **Weak. This is the headline gap.** See §4.1 |
| **Red-teaming** | 3 of 18 primary + ID.me | 12 adversarial scenarios of 55 in the tablemate corpus: 4 tagged `injection`, 2 `impersonation`, 2 `abuse`, 2 `disclosure`, 4 `over-reach`. The injection rows are real: `adversarial-injection-in-dietary-note` requires the agent to store a field's *content* while refusing the *instruction* inside it, with forbidden-phrase checks against three real surnames in the seeded state | **Better than the grep suggests.** The words "red team" and "jailbreak" appear 0 times, which means a keyword-scanning reader will miss it entirely. This is a labelling problem, not a capability problem |
| **Guardrails** | 6 of 18 | Six declarative contracts decided on event-stream *position*, not timestamp: Tool, Promise, NoReAsk, FieldPropagation, NoProgress, Phrase. `PromiseContract` is exactly the guardrail postings describe: the agent said it booked, therefore the tool call must exist | **Strong, and mis-named.** Nobody searching for "guardrails" will find `lab/checks/contracts.py` |
| **Drift / regression detection** | 2 of 32 verbatim | Byte-exact baseline regression only. Nothing measures a metric *moving* across versions or time | **Partial.** Regression: yes, and rigorously. Drift: no. See §4.5 |
| **Error analysis / failure taxonomy** | 6 of 18 | `error_analysis/`: open coding → axial coding → saturation note → `codes.csv` → Pareto chart, plus `FINDINGS.md` with five defects, each with a *control* case that differs in one detail and behaves correctly | **Strong and rare.** Very few portfolios show the qualitative-coding step. P7 and P12 are effectively asking for exactly this |
| **Human evaluation / rubrics** | 5 of 18 | 24 hand-labelled judge items; 18 hand-labelled RAG questions; hand-assigned failure modes in `codes.csv`; Cohen's kappa between judge and human | **Present at small n.** No annotation guideline document, no second annotator, no inter-rater agreement between *humans* — kappa here is judge-vs-human, which is a different quantity |
| **Latency / performance** | 6 of 18 | Timing calibration gate (20 repeats × 5 nominal delays, abs. relative error ≤ 5%, stdev ≤ 15 ms) that **refuses to report percentiles from an unproven stopwatch**, plus a naive whole-turn control that FAILs to prove the gate discriminates | **Strong, and the calibration-gate idea is genuinely distinctive** |
| **Cost / token accounting** | 3 of 18 | Nothing. `EventKind` has 15 members and none carries tokens or cost | **Absent.** See §4.3 |

---

## 4. The gaps — things postings ask for repeatedly that this repository has no story for

This is the section to act on. Ordered by market frequency × distance from the current
state.

### 4.1 Production. There is no online half of the loop. (11 of 18)

Everything here is offline and replayed. The repo drives synthetic scenarios, records
traces, and re-checks committed traces. It never *ingests* a trace produced by a system
it does not control.

Postings do not ask for this abstractly — they name the specific work: P15 wants "trace
enrichment, durable telemetry pipelines, dashboards, and debugging workflows"; P16 wants
"traces, spans, and session tracking"; P11 wants "monitoring, logging, and feedback
loops"; P7 wants "offline **and online** evals"; P6 wants "monitoring and alerting
systems" and "production quality tracking"; P14 wants "post-deployment monitoring".

The repo's answer to all of that is a one-way Langfuse export. That is the smallest
possible version of the thing 11 of 18 postings put in the job title's first paragraph.
The missing capability is not "a dashboard" — it is *sampling live traffic into the
eval corpus*, which is the loop P7 and the ID.me posting both describe explicitly
(production failures → categorised → fed back into the regression set).

### 4.2 OpenTelemetry. Zero references, and it is the only standard the market names. (3 of 18)

P1 (nice-to-have), P4 (required tooling) and P17 (nice-to-have) name OTel. Nothing else
in the observability space is named by more than two postings. The repo has a bespoke
15-kind event schema with a deliberate forward-compatibility argument, and exports to one
proprietary shape.

The uncomfortable framing: this repo built a trace schema *from scratch* in a market that
has converged on a standard, and the README argues for the schema's honesty without ever
mentioning the standard it is not. A reviewer who works in OTel will read that as
not-invented-here unless the omission is addressed head-on.

### 4.3 Cost and tokens are not measured at all. (3 of 18)

`EventKind` carries `SESSION_START … SESSION_END`, audio timing, transcripts and
transport events. No token counts, no cost. P4 asks for "token usage" alongside
P50/P95/P99; P16 asks for "latency, and cost"; P17 asks for "token budgets" and "prompt
caching strategies".

This is the cheapest gap in the list to close and the one whose absence is most
conspicuous, because the repo *does* measure latency to a calibrated stopwatch. Measuring
time precisely while not measuring money at all is a strange combination to defend.

### 4.4 RAG has metrics but no retrieval stack. (6 of 18 RAG, 5 of 18 vector infra)

`ragcheck` is 3,108 lines and its worked examples are excellent — the opening one shows
recall 1.000, context precision 1.000, and an answer that is 67% wrong, which is exactly
the argument a hiring manager needs to hear about retrieval-only suites. But:

- retrieval is lexical, over 16 chunks and 18 questions;
- there are no embeddings, no vector store, no chunking strategy, no reranker;
- the groundedness judge in the offline path is a "lexical stand-in… which is not a
  model", per its own output.

So the repo can argue about RAG evaluation *methodology* and cannot demonstrate RAG
*engineering*. P4 names five vector stores; P1 names three plus "embeddings pipelines".

### 4.5 Regression is proven; drift is not measured. (2 of 32, but structurally important)

The byte-exact baseline gate is a strong regression story and better than most. It is not
a drift story. There is no time series of any metric, no version-over-version comparison
of a *rate*, no threshold that fires when a number moves without the baseline file
changing. In practice a real eval system needs both: "nothing changed" (this repo has it)
and "the pass rate fell 4 points across the last six model releases" (it does not).

Low market frequency, so this is a *cheap credibility* item rather than an urgent one.

### 4.6 Scale, concurrency and the shape of a real eval run. (P15 explicitly)

- Largest corpus driven: 55 scenarios. Total committed: 194 YAML files.
- No `pytest-xdist`, no sharding, no worker configuration (`grep -rn xdist pyproject.toml
  Makefile .github` → nothing).
- `asyncio` appears only inside `lab/voice/transport/session.py`, where WebRTC forces it.
  There is no async fan-out of LLM calls anywhere.

P15 asks for "large-scale evaluation pipelines"; P7 for "proprietary benchmarks and
datasets"; P1 for evaluation logic that runs at "real-time or near-real-time" against
"real-world data distributions". A 55-row sequential corpus does not answer any of those.
Common eval-engineer screening explicitly probes async/concurrent LLM calls and
data-engineering patterns ([career guide](https://jobsbyculture.com/blog/ai-evals-engineer-career-guide-2026)).

### 4.7 There is no service under test with an HTTP surface. (7 of 18 name API testing)

`tablemate` and `roleplay` are in-process Python. Nothing is served, nothing is called
over a network the repo controls, nothing exercises status codes, auth, retries or
contract testing against an API. For the 7 of 18 postings that name API testing, and for
all 4 that name CI/CD in an enterprise sense, the repo is silent.

### 4.8 Two gaps that are real but should probably stay open

Named for completeness, with a recommendation *not* to build:

- **Browser / UI automation (5 of 18).** Out of this repository's remit entirely — it is a
  conversational-agent evaluation harness, not a web app test suite. `docs/PLAYWRIGHT_NOTES.md`
  already states the gap honestly and maps what transfers, which is the correct response.
  Building UI automation here would dilute the thing that makes the repo legible.
- **Cloud / container / infra (6 of 18).** Directly contradicts the cardinal rule that
  everything runs from a clean clone with zero keys. That rule is the repo's single
  strongest reviewer-facing property. Do not trade it for a keyword.

---

## 5. What is over-represented — where the investment does not match the ask

Cutting is as valuable as adding. Four candidates, largest first, each with the number
that makes the case.

### 5.1 `docs/WIKI.md` — 14,802 lines, 72 Mermaid diagrams, 65.3% of all documentation

**Asked for by 0 of 18 postings.** The nearest requirement is "communication of findings"
(7 of 18), and every one of those phrases it as clarity and cross-functional work, not
volume. P1's version is "clear written communication, as much of our work is
asynchronous". That is a memo.

This is the single largest misallocation in the repository by line count. A 14.8k-line
wiki has three costs a reviewer feels immediately: it signals that documentation is the
output rather than the measurement; it makes the 31.5k-line engine look smaller by
comparison than it is; and nobody reads it, so its accuracy work is unpaid. Consider
whether the reader-facing surface should be README + DESIGN + one findings document, with
the wiki kept as an internal artefact rather than a headline.

**Not a recommendation to delete anything** — a recommendation to stop treating its size
as an asset.

### 5.2 `lab/voice` at 15,851 lines — 50.3% of the engine, against 4 of 18 voice postings

Voice is a genuine differentiator, and for a voice-agent employer this is the whole
pitch. But at half the engine it is priced as if the market were majority-voice, and it
is not: 4 of 18 name voice at all, and 0 of 18 name any of the three vendors integrated.

Inside that, `lab/voice/transport/` (~4,100 lines, the LiveKit WebRTC tier) is the
sharpest case. It is asked for by 0 of 18 postings, it is the only part of the repo
requiring a credential and a reachable server, and it is the only optional dependency
deliberately kept out of `[dev]`. It is real engineering that almost no reader will be
qualified to assess.

**The trade to consider:** the same reader-attention spent on §4.1 (production ingestion)
or §4.3 (cost/tokens) would move against 11 of 18 and 3 of 18 respectively, versus 0 of 18
for more transport work.

### 5.3 The four-regulator advisory apparatus — roughly 5,400 lines of code and prose

`roleplay/regime_eval.py` (2,732) + `roleplay/register.py` (462) +
`docs/ADVISORY_TEST_STRATEGY.md` (1,081) + `docs/SCORECARD.md` (1,086).

Regulated-domain *context* appears in 5 of 18 postings (P1, P7, P10, P12, P16). Not one
of them asks for a machine-readable multi-jurisdiction rule engine that computes regime
verdicts from cited registers. P7 — the closest match, and explicitly about "regulated,
adversarial, high-consequence environments" — asks for benchmarks, datasets, quality
metrics and failure analysis. It does not ask for a compliance engine.

**This is the one entry on the list where I would not push.** If the target is a
regulated-domain conversational-AI vendor, this apparatus is the differentiator and is
correctly sized. If the target is the broader agent-evaluation market, it is the largest
piece of work in the repo that the market has no line item for. That is a decision about
targeting, not about code, and it is the owner's to make.

### 5.4 The 28-KPI scorecard — `roleplay/scorecard.py`, 1,724 lines

No posting in either corpus asks for a scorecard of that width. The market asks for a
*small* number of metrics that are *defended* — P13's phrasing is "critical thinking about
metrics that matter; skepticism toward vanity metrics", and P7's is "define quality
metrics for judgment systems, including precision, calibration, consistency, abstention".
Twenty-eight KPIs is the failure mode both of those sentences are warning about, whether
or not it actually is one here. Worth a look at whether the scorecard can be presented as
four or five metrics with the rest as detail.

### 5.5 What is *correctly* sized, so it does not get cut by mistake

- `lab/judges` (2,966 lines). Highest market-value-per-line in the repo. The failing v1 is
  the asset, not an embarrassment.
- `lab/checks/contracts.py` (1,749 lines). Six contracts, position-not-timestamp. Maps
  straight onto the 11-of-18 agent-behaviour requirement.
- `error_analysis/` (288 lines of Python plus four short documents). Cheapest asset in the
  repo relative to what P7 and P12 ask for.
- The timing calibration gate. Small, distinctive, and it refuses.
- The zero-keys rule and `ci.yml`. Do not touch.

---

## 6. Three things a decision could be made about, stated as options, not as work

Not a plan. Options, with the number attached to each so the owner can weigh them.

1. **Labelling, not building.** Several capabilities already exist under names the market
   does not search for: `lab/checks/contracts.py` *is* guardrails (6 of 18); the 12
   adversarial scenarios *are* red-teaming (3 of 18 + ID.me); `evallab validate --coverage`
   over 194 files *is* golden-dataset management (12 of 32). Cost: hours, in prose only.
   Reach: three top-half rows of §1.1. This is the highest ratio on the list and touches
   no code.

2. **The cheapest real gap is tokens and cost (§4.3, 3 of 18).** Two fields on a trace
   event and a column in a report. It also removes the strange asymmetry of a repo that
   calibrates a stopwatch and never counts money.

3. **The largest real gap is production ingestion (§4.1, 11 of 18)** and it is not cheap —
   it is a second half of the loop, and it collides with the zero-keys rule the moment it
   touches a live system. Worth deciding *whether* before deciding *how*. A middle option
   exists: ingest a committed fixture that *represents* production traffic, sample from
   it into the corpus, and be explicit that the source is synthetic. That buys the shape
   of the loop without a credential.

---

## Sources

All posting URLs are inline above, in §0.1 for both corpora. Non-posting sources used for
market context, cited where quoted:

- [AI Evals Engineer career guide, 2026](https://jobsbyculture.com/blog/ai-evals-engineer-career-guide-2026)
- [AI Evaluation Engineer role blueprint — devopsschool](https://www.devopsschool.com/blog/ai-evaluation-engineer-role-blueprint-responsibilities-skills-kpis-and-career-path/)
- [AI skills premium in QA postings — InterviewStack](https://interviewstack.io/blog/how-ai-is-changing-qa-engineer-2026)
- [How to recruit AI evals engineers, 2026 — HeroHunt](https://www.herohunt.ai/blog/how-to-recruit-ai-evals-engineers-2026/)
- [Golden dataset evaluation — Langfuse](https://langfuse.com/resources/engineering/golden-dataset-evaluation)
- [LLM-as-a-judge guide — Evidently AI](https://www.evidentlyai.com/llm-guide/llm-as-a-judge)
- [LLM red teaming — promptfoo docs](https://www.promptfoo.dev/docs/red-team/)
- [Voice agent QA framework — Hamming](https://hamming.ai/resources/guide-to-ai-voice-agents-quality-assurance)
- [AI red teamer hiring and salary — infosec.qa](https://infosec.qa/blog/hire-ai-security-engineer-2026/)
