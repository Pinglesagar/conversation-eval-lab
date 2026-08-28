# The enhancement plan

**Status: a decision document. Nothing in it has been built, and nothing in it should be
built before the owner picks from it.** No `.py`, `.yaml`, fixture, `Makefile` or
`pyproject.toml` was modified to produce it. The two files this pass owns are this one and
one new section in `docs/WIKI.md` that points at it.

**Why it exists.** Five research passes ran against this checkout and wrote
`docs/_plan/r1_speech.md`, `r2_judge.md`, `r3_market.md`, `r4_production_patterns.md` and
`r5_gaps.md` — 3,711 lines between them. That directory is left **untracked**: it is working
notes, and one of the five describes a private production harness in de-domained terms, so
whether it ships is a decision for the owner rather than a default. This document merges
them, removes the overlap, and turns the result into choices. It is deliberately shorter than
any one of them.

**The constraint that shaped it.** The owner has said twice that he does not want to
accumulate more half-built things. So the useful output of this document is not the list of
what could be done; it is the list of what should be **declined**, and the reasons. Section
3 rejects eighteen named items. If every recommendation in a plan is "yes", the plan has
not done its job.

**House rules observed.** Every rate carries its denominator. Every number is either the
output of a command reproduced in the [appendix](#appendix--reproduction-log), a quotation
from a file in this tree, or a citation from one of the five research files — which carry
their own sources. Anything else is labelled **ASSUMPTION**, and there is no third
category. All effort estimates are ASSUMPTIONs; they are judgement, not measurement.

**Verified starting state**, this checkout at `032eab7`, 40 commits, clean tree:

```
.venv/bin/pytest -q            → 1976 passed, 4 skipped in 55.47s
                                 (the 4 skips name the live flag they need)
time python -m lab.cli run --no-traces
                               → 47/55 scenarios driven, 1.44 s wall clock
python -m lab.cli calibrate    → exit 0, artefacts regenerate byte-identically
git status --short             → only the untracked docs/_plan/
```

---

## 0. How to read this

| Section | What it answers |
|---|---|
| [1](#1-where-the-framework-stands) | Where this stands, without flattery, in one page |
| [2](#2-the-gap-list-merged-and-deduplicated) | Every gap the five passes found, merged, each tagged ALREADY-DO / PARTLY / MISSING |
| [3](#3-the-ranked-enhancements) | Twenty-seven candidates ranked by value ÷ effort, each with a verdict — and eighteen rejections with reasons |
| [4](#4-three-tiers) | A weekend · a week · a direction |
| [5](#5-the-judge-determinism-section) | The differentiator, in depth, including the statistics on a 24-item set |
| [6](#6-the-rag-separation) | How to make the retrieval pack visibly distinct without building anything |
| [7](#7-patterns-worth-porting-from-a-production-suite) | What a two-year-old production harness knows, in neutral terms |
| [8](#8-what-to-cut-or-stop-investing-in) | What is over-built relative to what it proves |

Verdicts are one of three words. **DO IT** — the value is clear and the cost is bounded.
**MAYBE** — good, but conditional on something the owner has to decide first, and named.
**DO NOT BOTHER** — with the reason, because a rejection without a reason is just a
backlog item in disguise.

---

## 1. Where the framework stands

### 1.1 Genuinely strong — the things to lead with

**1. A judge that can be refused, and is.** `lab/judges/registry.py::require_calibrated()`
raises `UncalibratedJudgeError` (never measured) or `JudgeBelowThresholdError` (measured,
too weak), the two kept separate because they call for opposite responses. The override
must be written at the call site and is deliberately unavailable from config or
environment. It bites on the repo's own scripted rubric scorer: `TPR 0.281 (9/32)`,
`kappa 0.241`, gate REFUSED [R2 §1.3, from `make roleplay-demo`]. Across the frameworks
surveyed in R2 §8.1 — DeepEval, Ragas, promptfoo, Braintrust, Langfuse, OpenAI Evals,
Inspect AI — every one lets you write a judge and use its verdicts immediately. A hard
precondition is the rarest thing in this repository.

**2. Calibration pinned to what was calibrated.** `Judge.with_prompt()` returns a judge
with *no* calibration attached; `ReplayJudge` refuses a recording whose prompt digest no
longer matches; `compare_reports` refuses two reports whose label digests differ. Agreement
is a property of a specific prompt against a specific label set, and carrying it across an
edit is made impossible rather than discouraged [R2 §8.2].

**3. Instability reported rather than voted away.** Three identical runs of the v1 prompt
produce a byte-identical confusion matrix — `TP=2 FP=0 FN=6 TN=16` in all three — while two
of twenty-four items flip between runs and cancel, because both happen to carry the same
human label [R2 §1.5, reproduced from the committed `verdicts_v1*.jsonl`]. `make calibrate`
prints the per-item churn. That finding, and the decision not to average it away, is the
single most interesting thing in the repository.

**4. Denominator discipline enforced by types, not by memory.** `Rate` will not render
without its fraction and prints `undefined (0/0)` on an empty denominator.
`min_samples_for_quantile()` makes a p95 refusable rather than printable at n=3.
`JudgeSummary.calibration` is a required field, so a judge verdict is structurally
unprintable without its measured error rates [R4 §5.4].

**5. Three outcomes where most harnesses have two.** `runnable` / `blocked` / `untestable`
in the audio tier, with `passed = null` for the latter two and neither in a pass-rate
denominator; `applicable=False` for a vacuous pass; `expected_failure` as a live
expectation that still runs, still reports, and notices the day it starts passing
[R1 §1.1, R4 §5.6–5.7].

**6. Zero keys, and the replay is fast enough to be a habit.** A full replay run is
**47/55 scenarios in 1.44 s wall clock** [verified, appendix]. That number is a design
property, not an accident, and Section 3 judges several proposals partly on whether they
preserve it.

**7. A timing gate with a failing control.** Injected-clock delay recovery at five rungs
against a 5% tolerance, plus a deliberately naive control that FAILs on the three shortest
rungs — the regime where a voice budget actually lives. Nothing downstream is reportable
until it passes [R1 §1.1].

**8. Qualitative error analysis, which almost no portfolio has.** `error_analysis/` is 288
lines of Python plus open coding → axial coding → a saturation note → `codes.csv` → a
Pareto chart, with five defects each carrying a *control* case that differs in one detail
and behaves correctly [R3 §3].

### 1.2 Average — real, but not a differentiator

- **The corpus.** 194 committed scenario YAML files, schema-validated, five corpora
  [verified]. Good, and the market asks for golden datasets more than it asks for anything
  else except framework-building (12 of 32 postings combined) [R3 §1.4] — but the size is
  ordinary and the provenance is hand-authored, which the repo says.
- **CI.** Four ordered gates, no secrets block, a byte-for-byte reproduction gate. Well
  argued. But the corpus-validation step sees **55 of 194** scenario files; the 78-row
  advisory corpus reaches CI only indirectly through `tests/test_advisory_corpus.py`
  [R5 §A2, verified].
- **RAG.** Sharp methodology (the worked example where every retrieval metric is 1.000 and
  the answer is 67% wrong) over 18 questions and a 16-chunk corpus, retrieved lexically
  [R3 §4.4]. Section 6 argues this is correctly scoped and mis-*placed*.
- **Reporting.** Markdown plus JSON, deterministic, with an `integrity_gaps()` section that
  audits the report's own evidence. Better designed than most; organised by contract rather
  than by "what should I look at first" [R4 §3.14].

### 1.3 Missing — the honest list

- **Uncertainty on its own numbers.** The repo has proved its judge is unstable and then
  prints point estimates from single runs of it. This is Section 5 and it is the most
  important line in this document.
- **The production half of the loop.** Everything is offline replay. Nothing ingests a
  trace produced by a system the repo does not control. 11 of 18 postings name tracing,
  monitoring or online evals [R3 §4.1].
- **Duplex.** Barge-in *discovery* does not exist; the turn loop plays the agent, then the
  caller, so no moment exists in which both are sounding. The one barge-in figure published
  (240 ms yield) is arithmetic over two clip durations handed to the row [R1 §1.2].
- **Cost and tokens.** `EventKind` has 15 known members and none carries tokens or money
  [verified]. A repo that calibrates a stopwatch to ±5% and never counts spend is a strange
  combination to defend [R3 §4.3].
- **Accents.** Every audio row uses one of two stock premade voices; Voice Library voices
  are a paid capability [R1 §1.3].
- **Whose bug is this.** `CheckResult.evidence` answers "what happened, with quoted
  evidence". Nothing answers "who owns it, how bad, here is a paragraph you can paste"
  [R4 §3.2].
- **A published line-coverage number.** `pytest-cov` is a dev dependency, `[tool.coverage.run]`
  is configured with `branch = true`, and no figure is produced anywhere. Measured once for
  this pass: **87% branch coverage, 17,035 statements, 1,892 missed** [R5 §B7.1].

### 1.4 The one-sentence version, for the room

> It is an evaluation harness whose distinguishing property is that **every instrument in it
> is itself measured, and the measurement is pinned to what was measured** — a judge that is
> refused below threshold, a clock with a failing control, rates that cannot print without
> their denominators — and whose largest remaining honesty gap is that it does not yet put
> an error bar on the numbers it publishes.

### 1.5 Defects found during this pass, recorded and **not** fixed

Four. Two are new to this pass; two were found by R5 and re-confirmed here.

| # | Defect | Where | Severity |
|---|---|---|---|
| D1 | **`README.md` line 1 names a different project.** The README is titled with the name of a separate, unrelated project of the owner's. The wiki correctly calls this repository `conversation-eval-lab` (`docs/WIKI.md:126`). The name appears in exactly one file in the tree — the first line a reader sees. Verified: `grep -rl` for it matches `README.md` only; `grep -c` in the wiki → 0. | `README.md:1` | **high** — it is the first line, and it collides with an unrelated project |
| D2 | **A machine-written artefact contradicts itself at any k ≠ 3.** `lab/cli.py:1712` interpolates the run's real `k` (`f"k={args.repeats} with a live rig…"`) and four lines later hardcodes *"three passes out of three put the 95% Wilson lower bound on the pass rate at 0.44"*. 0.44 is right for 3/3 (Wilson lower bound 0.439, computed). For 5/5 it is 0.566. Both committed reports run at k=3, so both are accidentally correct. | `lab/cli.py:1716` | **high** — a report that states two different things about one run |
| D3 | **The schema says the interruption events are never emitted. They are.** `lab/trace/schema.py:91–103` says *"Nothing in v1 emits, consumes, or asserts on them"*; `lab/voice/interaction.py:512 emit_barge_in()` writes both, `:594 barge_in_report()` reads them back, and `tests/test_voice_interaction.py` asserts on them. The stale claim is repeated in `lab/voice/adapter.py:63`, `lab/voice/metrics.py:52`, four places in the wiki and `INTERVIEW_NOTES.md`, and the wiki contradicts itself (§8.3.6 is correct). Also: `PAYLOAD_KEYS` has no entry for either kind, so two emitted kinds carry payload keys the schema's own contract does not describe. | `lab/trace/schema.py:103` +6 | **high** — it is a factual error about the schema, in the schema |
| D4 | **A naked percentage in a repo whose rule forbids them.** `lab/voice/adapter.py:1385` renders the literal *"could attribute 31.3%"* with no denominator and no derivation, eleven lines below a property whose docstring says *"A naked percentage is a defect in this repo."* It reaches a committed fixture and `docs/AUDIO_SUITE.md:1135`, where the modal "could attribute" has become the factual "attributed". | `lab/voice/adapter.py:1385` | medium — already in the wiki's Appendix B |

**D1 and D2 are code/docs changes, not research, and they are out of scope for this pass by
the brief.** They are recorded here so the decision to fix them is deliberate.

There is also one **non-defect worth a decision**: `docs/PLAYWRIGHT_NOTES.md` (216 lines) is
personal interview-preparation material describing a production system built at the owner's
current employer — its scale, its integration count and its internal figures — in a file
committed to this repository. It names no employer, and it triggers none of the clean-room
term greps (verified: 0 hits for every banned term). But it describes employer work in
detail, it is referenced by nothing in the tree, and it is about a technology stack this
project neither uses nor should claim. **Removing it costs nothing and it is the only item
in this document I would call urgent.** [R5 §B5, re-verified here]

---

## 2. The gap list, merged and deduplicated

Every gap the five passes raised, merged into 37 rows, each tagged once. Where two passes
found the same thing under different names, the row says so — that overlap is itself a
finding, because it means the gap is real from more than one direction.

**ALREADY-DO** means the capability exists and the gap is that nobody would find it.
**PARTLY** means the machinery exists and one step is missing.
**MISSING** means there is nothing.

### 2.1 Judge and statistics

| # | Gap | Tag | Note |
|---|---|---|---|
| G1 | Confidence intervals on any published rate | **MISSING** | The repo declines them on purpose; Section 5 argues the stated reason is backwards [R2 §5.1] |
| G2 | Run-to-run band on a rate | **PARTLY** | k=3 replicates are recorded and used only for a headline unanimity figure; no rate carries the band [R2 rank 1] |
| G3 | Paired significance test on the v1→v2 comparison | **MISSING** | `compare_reports` prints deltas, no test, no detectability floor [R2 §5.3] |
| G4 | Human ceiling — intra-rater reliability of the label column | **MISSING** | One labeller, one pass; label noise is charged to the judge and the repo says so [R2 §4.3] |
| G5 | Judge-side abstention | **PARTLY** | The *label* side abstains (R4 → AMBIGUOUS, 7 items excluded with reasons); `lab/report` already carries `abstained` + `abstention_rate` + a validator. Nothing wires them together [R2 §4.2] |
| G6 | A guard that the live judge and the live agent are different models | **MISSING** | `make live-replay` runs all three through litellm; nothing asserts the judge route ≠ the agent route [R2 finding B] |
| G7 | Decoding provenance — seed, `system_fingerprint`, dated model route | **MISSING** | Prompt digest ✓, label digest ✓, model route ✓, backend build ✗ [R2 rank 6] |
| G8 | Structured/constrained decoding of the verdict | **MISSING** | Current parse-error rate is 0/24 and 0/27, so this buys futures, not a present fix [R2 rank 7] |
| G9 | Verbosity-bias measurement | **MISSING** | Live and unmeasured; the five ordinal criteria are exactly the shape it exploits [R2 §2.3] |
| G10 | Position bias | **ALREADY-DO** (immune) | Single-item binary grading against a fixed rubric — no slots. Worth stating as designed, not accidental [R2 §2.3] |
| G11 | Ordinal agreement metric for the five-criterion card | **MISSING** | Nothing computes one today, so nothing is wrong; plain kappa would be the wrong instrument if one is ever added [R2 finding E] |
| G12 | A label set the judge cannot saturate | **MISSING** | HC v2 is 24/24, 8/8, 16/16. The gate guarding the build is guarded by a set that cannot detect a regression, and the repo says so [R2 finding C] |

### 2.2 Speech and voice

| # | Gap | Tag | Note |
|---|---|---|---|
| G13 | Entity Error Rate over a corpus | **PARTLY** | The 5 `digits-and-names` rows already assert *values*, which is EER at n=1 with the right all-or-nothing criterion. Only the roll-up is missing [R1 §2.1] |
| G14 | CER reported alongside WER | **PARTLY** | `scoring_unit()` already knows whether a row was scored in characters or words; the report does not say which [R1 §2.5] |
| G15 | Barge-in *discovery*; TOR; latency-after-interruption | **MISSING** | Structurally blocked: half-duplex loop, batch engines, no interim hypotheses [R1 §3.1, §4] |
| G16 | Post-interruption *recovery* scoring | **PARTLY** — the unusual entry | The scoring half already exists: "resume at the right step / don't repeat / address the interjection" are `NoProgressContract` / `NoReAskContract` / `FieldPropagationContract`. Only the injection is missing [R1 §3.4] |
| G17 | Endpointing metrics (ep50/ep90, early-cut, NoEP) | **PARTLY** | No endpointer, but `vad_false_silence` and `would_not_fire` measure the *consequences* from the other side, in private vocabulary [R1 §3.3] |
| G18 | Accent coverage | **MISSING** | Two stock voices; Voice Library is paid. Blocked by the *synthesis* side only — recorded human corpora need only the STT path [R1 §6.2] |
| G19 | Self-correction disfluency | **MISSING**, and documented | The containment matcher accepts `actually not X but Y` where the second value is the right one; `docs/AUDIO_SUITE.md` §14 says no row exercises it. Closing it is a schema change (declare a *final* value), not a matcher change [R1 §6.3] |
| G20 | Reverb and codec round-trip perturbations | **MISSING** | 5 perturbation families vs the ~114 a full bank has; these two are the ones a real call path cannot avoid [R1 §6.1] |
| G21 | TTFT / TTFA naming | **ALREADY-DO** | `agent_audio_first_byte` vs `audio_delivered` is TTFT-vs-TTFA made structural — two event kinds, not two derived numbers. Named privately [R1 §8] |
| G22 | Paired clean-text vs ASR arm | **PARTLY** | Both arms exist over one corpus; no committed paired comparison isolating "how much of this was the recogniser" [R1 §2.3] |

### 2.3 Harness engineering and reporting

| # | Gap | Tag | Note |
|---|---|---|---|
| G23 | INCONCLUSIVE as a status | **PARTLY** | `applicable=False` (vacuous) and `error` both exist and are better designed than the production suite's equivalents. A `max_turns` truncation is still graded, and the report cannot say "this could not be graded" [R4 §3.1] |
| G24 | Proof that the assertions have teeth | **MISSING** | The loader *prevents* unfireable assertions at load time, which is stronger than detection — but the repo has never demonstrated that its 369 contract evaluations discriminate [R4 §3.4] |
| G25 | Failure ownership, severity, and a paste-ready finding | **MISSING** | Evidence is excellent; "whose bug is this" is unanswered, and that is the question that decides whether a finding gets fixed [R4 §3.2] |
| G26 | A vocabulary for a failure that is a *harness* artefact | **MISSING** | `expected_failure` covers known gaps in the *product*. There is no dated, reasoned classification for a red that is the harness's own fault [R4 §3.3] |
| G27 | A documented ordering of the gates | **MISSING** | All the pieces exist — `validate`, `test`, `calibrate`, `replay`, `demo` — with no paragraph saying which you run first and why [R4 §3.7] |
| G28 | A no-look-ahead test at the source level | **MISSING** | The persona model is architecturally clean (gated facts, a leak audit that raises); nothing pins the boundary so a future refactor cannot quietly widen it [R4 §3.6] |
| G29 | Failure clustering and a ranked top-failures view | **MISSING** | The report is organised by contract, not by "what should I look at first"; one root cause can read as forty findings [R4 §3.14] |
| G30 | Trend across more than two points | **PARTLY** | The baseline diff is byte-exact and strictly two-point. Nothing answers "how has this contract's failure rate moved over ten commits" [R4 §3.12] |
| G31 | Line-coverage figure; type-check gate; version/changelog | **MISSING** | 87% branch coverage measured once for this pass and published nowhere; `[tool.coverage.run]` omits 4 of 7 packages; no mypy/pyright; `version = "0.1.0"`, no tags [R5 §B7] |

### 2.4 Market-facing

| # | Gap | Tag | Note |
|---|---|---|---|
| G32 | Guardrails / red-teaming / golden-dataset management | **ALREADY-DO, invisibly** | `lab/checks/contracts.py` **is** guardrails (6 of 18 postings); the 12 adversarial scenarios **are** red-teaming — the words "red team" and "jailbreak" appear 0 times in the code, scenarios and tests (this plan and the wiki section pointing at it are now the only occurrences); `evallab validate --coverage` over the corpus **is** golden-dataset management (12 of 32). Three top-half market rows already satisfied under names no reviewer will search for [R3 §6.1] |
| G33 | Production ingestion / online evals | **MISSING** | 11 of 18. The repo's answer is a one-way export [R3 §4.1] |
| G34 | OpenTelemetry | **MISSING** | 0 references anywhere including prose; the only observability standard the market names (3 of 18) [R3 §4.2] |
| G35 | Cost and token accounting | **MISSING** | 3 of 18 [R3 §4.3] |
| G36 | Scale / concurrency | **MISSING**, and correctly so | No xdist, no async fan-out. The production suite's entire parallelism apparatus exists to make 1.25 h for 439 rows bearable; this repo's number is 1.44 s for 47 [R4 §3.17] |
| G37 | Retrieval *engineering* (embeddings, vector store, reranker) | **MISSING**, and declined twice | Section 6 argues the retriever-agnostic property is a feature and should be written down as a decision, not carried as a gap |

---

## 3. The ranked enhancements

**Ranking method**, in order of precedence:

1. Does it protect the cardinal rule — clean clone, zero keys, green? Anything that cannot
   is ranked below anything that can, regardless of how good it is.
2. Does it strengthen the differentiator in [§1.1](#11-genuinely-strong--the-things-to-lead-with)
   rather than chase parity with tools that already have more headcount?
3. Value ÷ effort — **not** how impressive it sounds. Three of the top five are documentation.
4. Does it *close* something, or does it start something? A half-built thing is worse than an
   absence, because an absence can be explained in one sentence.

Effort figures are **ASSUMPTION** throughout.

---

### DO IT — fifteen items

---

#### 1. Remove the off-scope personal file

**What.** Delete 216 lines of personal interview-preparation material from the tree. Keep it
outside the repository if it has value to the author.

**Why it matters.** It describes a production system built at the owner's current employer —
its scale, its integration count, its internal figures — in a file that ships with any clone.
It is referenced by nothing (`grep -rl` over every `.md`, `.py`, `.toml` and the `Makefile`
returns nothing). And it is about a technology stack this project neither uses nor should
claim, which makes it the one file in the tree that could mislead a reader about what this
repository is.

**Effort.** Minutes. **Zero-keys.** Unaffected. **In an interview.** Nothing — its value is
entirely in not being there.
**VERDICT: DO IT. This is the only urgent item in this document.**

---

#### 2. Give the repository its own name in the README

**What.** `README.md:1` is titled with the name of a separate, unrelated project of the
owner's. Change it to the name the wiki already uses, `conversation-eval-lab`
(`docs/WIKI.md:126`).

**Why it matters.** It is the first line a reader sees, and it points at a different
project. Verified: the name appears in exactly one file in the tree, and 0 times in the
14,803-line wiki (at `032eab7`) — so this is a leftover from before the rename in commit `032eab7`
("docs: name the repository after itself"), not a deliberate choice.

**Effort.** Minutes. **Zero-keys.** Unaffected.
**In an interview.** Nothing gained; a great deal lost if it is still there.
**VERDICT: DO IT.**

---

#### 3. The naming pass — say what already exists in words the reader searches for

**What.** No code. Add the field's own vocabulary next to the repo's private vocabulary, in
the README and the wiki:

| Already in the repo | The name a reviewer searches for |
|---|---|
| `lab/checks/contracts.py` — six declarative contracts | **guardrails** (6 of 18 postings) |
| 12 adversarial scenarios of 55 — injection, impersonation, abuse, disclosure, over-reach | **red-teaming** (3 of 18 + secondary). "red team" and "jailbreak" appear **0 times** in code, scenarios and tests |
| `evallab validate --coverage` over 194 committed YAML rows | **golden-dataset management** (12 of 32 combined) |
| `agent_audio_first_byte` → `audio_delivered` as two event kinds | **TTFT vs TTFA**, made structural rather than derived |
| the 5 `digits-and-names` rows asserting values, all-or-nothing | **Entity Error Rate**, at n=1 per row |
| `vad_false_silence` / `would_not_fire` | the **early-cutoff** and **NoEP** families from the endpointing literature |
| `scoring_unit()` already choosing characters or words | **CER alongside WER** — say which unit scored each row |
| single-item binary grading against a fixed rubric | **structurally immune to position bias** — a designed property, not an accident |

**Why it matters.** [R3 §6.1] identifies this as the highest ratio in its entire document:
three top-half rows of the market-frequency table are *already satisfied* under names nobody
will find. And [R1 §9] independently reached the same conclusion from the speech side. Two
research passes converging on "your problem is labelling, not capability" is the strongest
signal in the merged set.

**Effort.** Hours, prose only. **Zero-keys.** Perfect.
**In an interview.** It converts existing rigour into claims a reviewer recognises inside
thirty seconds, which is all the time the first pass gets.
**VERDICT: DO IT. This is the best value-per-hour item in the document.**

---

#### 4. Fix the self-contradicting caveat at `lab/cli.py:1716` (defect D2)

**What.** Either derive the Wilson lower bound from `args.repeats`, or delete the sentence.

**Why it matters.** The surrounding f-string interpolates the run's real `k`, then hardcodes
*"three passes out of three … 0.44"*. At `k=5` the artefact says "k=5" and "three passes out
of three" in the same paragraph. Computed: 3/3 → 0.439, 5/5 → 0.566 [verified, appendix].
Both committed reports run at k=3, so both are accidentally correct — which is exactly the
kind of latent error this repository exists to argue against.

**Effort.** An hour, including a test that pins the derivation at two values of k.
**Zero-keys.** Perfect.
**In an interview.** *"We found a machine-written artefact that contradicted itself at any k
except the one we happened to run"* is a better story than the bug is a problem.
**VERDICT: DO IT.**

---

#### 5. Make one true statement about barge-in, and propagate it (defect D3)

**What.** The precise, defensible sentence — which nothing in the tree currently makes — is:
*the interruption events have an emitter and a reader, both tested; no adapter or committed
run calls the emitter, so no committed trace contains one.* Propagate it to
`lab/trace/schema.py:103`, `lab/voice/adapter.py:63`, `lab/voice/metrics.py:52`, the four
stale wiki locations and `INTERVIEW_NOTES.md`. Separately, decide whether `PAYLOAD_KEYS`
gets entries for the two kinds or a docstring saying they are deliberately unschema'd.

**Why it matters.** The schema is currently wrong about the schema, and the wiki contradicts
itself on a schema fact (§8.3.6 is right, four other places are stale). For a repository
whose thesis is auditability, an inaccurate claim in the trace schema's own docstring is the
worst possible place for one.

**Effort.** An hour. **Zero-keys.** Perfect.
**In an interview.** Removes a trap: a reader who greps `emit_barge_in` after reading the
docstring finds the contradiction in under a minute.
**VERDICT: DO IT.**

---

#### 6. Print the run-to-run band on every rate

**What.** The k=3 replicates are already recorded and committed. Compute each rate on each
replicate and print, for example, `TNR 1.000 (12/12) [0.917–1.000 across 3 runs]`. Derive a
*decision band* from the spread, and refuse any prompt-to-prompt comparison whose delta
falls inside it.

**Why it matters.** This is the item that closes the repo's own best finding. Today
`roleplay/scorer_study/study.md` prints, in prose, that its table is not reproducible — and
then quotes run 1's table as the answer. Two different confusion matrices came out of three
identical runs at temperature 0 (run 3: TNR 0.917 (11/12) against 1.000 (12/12) in runs 1–2)
[R2 §1.6]. Underneath the verdicts it is worse: `compliance-explicit-unlicensed-advice`
scored its `mandatory_disclosure` criterion **[0, 4, 0]** across three identical runs — a
four-point swing on a five-point criterion, on the item the whole rubric exists to catch.
The binary collapse in `lab/judges` is protective *and* it hides this. The repo currently
argues only the first half.

**Effort.** ASSUMPTION: ~150 lines plus report rendering. Zero API calls — the data is
already in the tree. **Zero-keys.** Perfect: pure arithmetic over committed recordings.
**In an interview.** It converts a narrated weakness into an enforced property, and the
sentence *"we do not publish a rate without the band our own instrument moves through"* has
no equivalent in any tool [R2] surveyed.
**VERDICT: DO IT. This is the single most valuable code item in the document.**

---

#### 7. McNemar in `compare_reports`, with the detectability floor printed

**What.** Add the exact paired test and print `discordant 6/0, exact two-sided p = 0.031`
next to the delta table, plus the standing note about what would have been undetectable.

**Why it matters.** The v1→v2 comparison is paired — same items, same labels, two prompts —
so a two-proportion z-test would be anticonservative and McNemar is the correct test. With
*n* discordant pairs all one way the exact two-sided p is 2/2ⁿ, so on a 24-item set the
**smallest detectable improvement is six items moving together**. Observed: 6/0,
p = 0.03125 — significant, and only just. A v3 that fixed three items and broke none would
be unpublishable at p = 0.250, no matter how real the improvement was [verified, appendix].

**Effort.** ASSUMPTION: ~50 lines, closed form, no dependency. **Zero-keys.** Perfect.
**In an interview.** It turns *"label more items"* from a platitude into a number.
**VERDICT: DO IT.**

---

#### 8. Print Wilson intervals, and reverse the stated position on them

**What.** A Wilson score interval on every rate, next to the fraction. A rule-of-three note
on zero-error cells. And — the load-bearing part — say explicitly whether the **lower bound**
clears the gate.

**Why it matters.** `lab/judges/calibration.py:85–88` declines confidence intervals on the
grounds that *"a Wilson interval on 8/8 would imply a precision the set cannot support"*.
The opposite is true: `TPR 1.000` is the number that implies unsupportable precision.
`TPR 1.000 (8/8), 95% CI [0.676, 1.000]` is the number that tells the reader the truth.
A gate at TPR ≥ 0.85 cleared by a point estimate whose lower bound is 0.676 is not clearing
the bar it claims to clear. The repo already quotes these Wilson bounds in prose in two
files, so the arithmetic is already trusted — it is just not in the report [R2 §5.1].

**Effort.** ASSUMPTION: ~60 lines, closed form, no scipy. The real cost is a doc rewrite and
the owner reversing a stated position **explicitly, in the docstring**, rather than silently.
**Zero-keys.** Perfect.
**In an interview.** Kills the most obvious criticism of the headline 1.000s before it is
made. See [§5.3](#53-the-honest-statistics-on-a-24-item-set).
**VERDICT: DO IT.**

---

#### 9. Assert that the live judge is not the live agent

**What.** One check at record time: warn — or refuse — when the judge's model route equals
the agent's.

**Why it matters.** `make live-replay` runs `--live-agent --live-caller --live-judge`, all
three routed through litellm from the environment, and nothing anywhere asserts the routes
differ. That is textbook self-enhancement-bias exposure with no guard rail and no warning
[R2 finding B]. It is currently unmeasured, not currently known to be wrong.

**Effort.** ASSUMPTION: 30 minutes and a test. **Zero-keys.** Perfect — it is a check on
configuration, not a call.
**In an interview.** A named bias with a structural guard is worth more than a named bias
with a paragraph.
**VERDICT: DO IT. Highest value-per-line in the document.**

---

#### 10. Mutation-test the corpus, and publish the kill rate — **the flagship**

**What.** A `mutate` mode that, per scenario, produces the mutants its assertions admit — an
impossible tool name in a `ToolContract`, an impossible required phrase, a field the caller
never supplies in a `FieldPropagationContract`, an inverted ordering — replays each against
the committed cassettes, and asserts every mutant dies. Report `killed/total` per contract
type. A surviving mutant is a finding about the corpus, printed with its scenario id.

**Why it matters, and why here specifically.** The loader already *prevents* assertions that
can never fire — an unreachable tracked field, an unresolvable argument ref, an
`expected_failure` naming an undeclared contract are all load-time errors, which is stronger
than detection. But prevention is not proof: the repo has never demonstrated that its 369
contract evaluations *discriminate*. An assertion the system satisfies on every path is green
today and green the day the product breaks.

The reason this is the flagship is the asymmetry. A production harness must spend model calls
and hours to kill a mutant. **This repo can kill them in 1.44 seconds, offline, in CI, on
every commit** [verified: `time python -m lab.cli run --no-traces` → 47/55 scenarios, 1.44 s].
This is the one technique where the replay-first architecture makes a technique *cheap that
is expensive in production* — which makes it the strongest single argument the repository can
make about its own design [R4 §3.4].

**Effort.** ASSUMPTION: 2–3 days, the largest DO IT here. **Zero-keys.** Perfect, and it is
the best demonstration of why zero-keys was the right call.
**In an interview.** *"We mutation-test our own assertions, the kill rate is published with
its denominator, and it runs in under two seconds because everything replays"* is a sentence
no other item in this document buys.
**VERDICT: DO IT — and if only one multi-day item is done, this is it.**

---

#### 11. Establish the human ceiling: blind test–retest by the same labeller

**What.** Re-label the same 24 items (and the 27), blind, ids shuffled, later. Publish the
intra-rater agreement as a printed ceiling next to every judge rate.

**Why it matters.** This is the bound on every number in the repository and no amount of
prompt or decoding work can move it. One labeller, one pass, so a measured TPR is a *joint*
measurement of judge quality and labeller consistency and the two cannot be separated. HC v2
scores 1.000 on every rate; if the labeller's own test–retest agreement were 0.95, a judge
scoring 1.000 is either lucky or agreeing with the labeller's errors, and **the repository
currently cannot distinguish those cases** [R2 §4.3]. The repo states this as a caveat and
never quantifies it.

**Effort.** ASSUMPTION: one afternoon of labelling, ~80 lines to load a second column and
compute agreement. Zero dollars. **Zero-keys.** Perfect.
**In an interview.** It answers the sharpest question a reviewer can ask about a 1.000 —
*"how do you know the labels are right?"* — with a number instead of an apology.
**VERDICT: DO IT.** Note: with two label columns Cohen's kappa still applies. With three, or
with abstentions, it must become Krippendorff's alpha [R2 §4.1].

---

#### 12. Write the debugging page

**What.** One page: what to do when a row goes red. Everything in it already exists, in six
separate places — `Evidence` carries the quote the contract failed on; `evallab replay <one
trace>` re-checks a single committed trace with no agent; `evallab run --scenario <id>
--transcript -k 1` prints the conversation; vacuity-versus-failure is the first distinction
to make; rule 14 says classify product / harness / invalid-scenario / label-error / variance
before believing a red; `make reference` prints its own diff, which is how you tell "fixed"
from "stopped applying" [R5 §A3].

**Why it matters.** The word "debug" appears **once in the wiki's 14,803 lines** (at `032eab7`), in an aside. Every
part of the wiki is add-shaped or explain-shaped and none is diagnose-shaped. This is
assembly, not research, and it is the single largest increase in what a reader can *do* per
line written.

**Effort.** ASSUMPTION: half a day, ~60–80 lines, no code. **Zero-keys.** Perfect.
**In an interview.** A reviewer who clones the repo and hits a red is the highest-value
reader you get, and right now they have nowhere to go.
**VERDICT: DO IT.**

---

#### 13. Document the gate order — cheapest first

**What.** One `make` target and one paragraph: lint the corpus (milliseconds, no model) →
harness self-tests (seconds, no model) → replay (seconds, no model) → calibrate → the paths
that spend money. Stop at the first failure.

**Why it matters.** All thirty `make` targets exist and are individually documented; nothing
says which you run first or why. A mistake should be caught in milliseconds rather than
after a long, misleading, expensive run. This converts a pile of capabilities into a
*procedure*, which is what a reviewer is actually looking for [R4 §3.7]. It also gives §5.4
of the wiki the missing *why* — and that why is already written, in `Makefile:126`: *"A
network test that blocks a merge trains people to bypass the gate, so the tier reports and
the offline suite gates."*

**Effort.** ASSUMPTION: half a day. **Zero-keys.** Perfect.
**VERDICT: DO IT.**

---

#### 14. Publish a coverage number, or decline it in writing

**What.** Either put a line-coverage figure in CI and the wiki, or state in §10.4 that it is
deliberately not measured and why. Also fix the incomplete config: `[tool.coverage.run]
source = ["lab", "roleplay", "tablemate"]` omits `ragcheck`, `scenarios`, `error_analysis`
and `scripts`, so anyone running coverage per the committed config silently measures three
of seven packages.

**Why it matters.** For a repository whose thesis is *measure your instruments before you
trust them*, line coverage is the most conspicuous unmeasured instrument. Measured once for
this pass: **87% branch coverage — 17,035 statements, 1,892 missed, 4,840 branches, 611
partial**. The lowest module is `ragcheck/__main__.py` at **0%** — a documented `make` target
whose 73-statement entry module no test executes [R5 §B7.1]. Silence is the one option that
is not defensible here.

**Effort.** ASSUMPTION: 20 minutes for the number, plus a decision. **Zero-keys.** Perfect.
**In an interview.** It is the second question after "does it run", and the answer should
not be a shrug.
**VERDICT: DO IT.**

---

#### 15. Make the RAG boundary visible

**What.** Documentation and navigation only — see [§6](#6-the-rag-separation) for the full
proposal, including the exact wiki, README and docstring changes and the checkable import
fact that anchors them.

**Effort.** ASSUMPTION: half a day, prose only. **Zero-keys.** Perfect.
**VERDICT: DO IT.**

---

### MAYBE — nine items, each with the decision that unblocks it

---

#### 16. Cluster the failures and rank them

**What.** Two new sections in the existing renderer: failure *patterns* (the same failure
clustered and counted) and *top failing scenarios* (ranked).

**Why it matters, with the repo's own numbers.** The committed run report carries **12
failures across 36/369 (9.8%) failed contract evaluations**, and **5 of those 12 are
`propagation:*`** — high-chairs, coeliac, allergy, shellfish, dairy [verified, appendix].
That is one root cause presented as five findings. A reader currently has to notice the
pattern themselves. That is the whole argument for this item, and it is measured rather than
asserted [pattern from R4 §3.14].

**The decision that unblocks it.** None, really — this is nearly a DO IT. It is here rather
than above only because the report is *already* deterministic and readable, so this improves
a good thing rather than fixing a broken one.
**Effort.** ASSUMPTION: a day in the existing renderer, no new dependency. **Zero-keys.** Yes.
**VERDICT: MAYBE — do it if the report is going to be read by someone who is not the author.**

---

#### 17. Judge-side abstention as a coverage/risk curve

**What.** Keep `Label` binary. Attach a per-item confidence — start with replicate unanimity,
which is free and already recorded. Report TPR/TNR at 100%, 92% and 80% coverage with the
abstained items named.

**Why it matters.** The *label* side already abstains — R4 → AMBIGUOUS excluded 7 items with
each exclusion printed and reasoned, on the stated principle that *"a visible smaller number
beats an invisible wrong one"*. The *judge* side has no way to say "I do not know", and that
asymmetry is the largest conceptual gap in the current design [R2 §4.2]. `lab/report` already
carries `abstained`, `abstention_rate` and a validator asserting `flagged + abstained <= judged`.

**The honest problem, and the decision.** On 24 items with 2 unstable, the coverage curve has
about three points. That is a curve in name only. **This becomes a DO IT the moment the label
set is bigger; today it is a good idea sitting on too small a denominator.** The decision is
therefore item 22, not this item.
**Effort.** ASSUMPTION: ~200 lines. **Zero-keys.** Yes — the confidence comes from recordings.
**VERDICT: MAYBE — sequenced after #6 and after the label set grows.**

---

#### 18. `INCONCLUSIVE` as a scenario-level status

**What.** A verdict for a session that ended for a reason meaning the product never got its
chance — `max_turns` with no terminal event, an agent-side exception, an empty response run —
excluded from the stability denominator exactly like a skip, printed on its own line.

**Why it matters.** The neighbouring states already exist and are better designed than the
production suite's: `applicable=False` for a vacuous pass, `error` for a harness fault. The
narrow gap is that **the report has no way to say "this could not be graded"**, so a run
truncated by a provider stall in `--live` mode is indistinguishable in the summary from a run
the agent genuinely botched. The production suite's own comment records a run where 34
truncated rows were graded as if complete and every one became a "product defect" [R4 §3.1].

**The decision that unblocks it.** *Do you intend live runs to become routine?* This costs
nothing and buys nothing offline; it matters entirely on the live paths, which are opt-in and
rarely run. If live runs stay occasional, one honest paragraph in the limitations section is
the cheaper answer.
**Effort.** ASSUMPTION: half a day. **Zero-keys.** Yes.
**VERDICT: MAYBE — conditional on live runs becoming routine.**

---

#### 19. Roll the field assertions up into a corpus entity error rate

**What.** Aggregate the existing per-row `capture_outcome` results into a named
`entity_error_rate` with its denominator.

**Why it matters.** The machinery is built and merely un-aggregated: the repo can say each
row passed, and cannot say "we recovered 15 of 16 entities". It also gives the documented
containment blind spots — a superstring, a superseded value — a place to be *reported* rather
than only pinned by a test [R1 §2.1].

**The honest problem.** The denominator is small: **16 capture declarations across 12 audio
rows** [verified by grep, appendix]. A named metric over 16 entities is a real number and a
thin one. Its value depends entirely on whether that corpus grows.
**Effort.** ASSUMPTION: small, reuses `capture_outcome` at `lab/voice/suite.py:730`.
**Zero-keys.** Yes — no new audio, no new vendor.
**VERDICT: MAYBE — cheap, but print the denominator prominently or it flatters.**

---

#### 20. The no-look-ahead source contract

**What.** An AST or regex check over the caller path in `lab/simulator/driver.py` asserting
it never reads the assertion side of a scenario.

**Why it matters.** The persona model is already architecturally clean — gated facts,
cooperativeness, a leak audit that raises, and a driver that detects a caller volunteering a
gated fact before it was asked for. The risk this guards is not today's driver; it is a
future refactor passing the scenario object one layer deeper, after which **the suite simply
starts passing more and nobody notices** [R4 §3.6]. A simulated user that can see the pass
criteria is an expensive parrot.

**The decision.** Cheap insurance against a low-probability, high-cost event. Worth it if the
simulator is going to keep changing; skip it if it is finished.
**Effort.** ASSUMPTION: half a day. **Zero-keys.** Yes.
**VERDICT: MAYBE.**

---

#### 21. A self-correction row, and the schema change it needs

**What.** A row that declares a *final* value, so `actually not the first one but the second`
is scored against the second.

**Why it matters.** `docs/AUDIO_SUITE.md` §14 already names this blind spot, and it is not
hypothetical: the containment matcher accepts the superstring, and the test at
`tests/test_audio_suite.py:737` pins the acceptance deliberately. It is the **self-correction
disfluency type** from the field's own taxonomy, it is a genuine instance of one of the
repo's own reference bugs, and the doc says no row exercises it [R1 §6.3]. Closing it is a
schema change (declare a final value), not a matcher change.

**The decision.** *Is voice still a focus?* If yes this is one of the two best voice items in
the document. If the target is the general agent-evaluation market, [§8](#8-what-to-cut-or-stop-investing-in)
argues voice is already over-weighted.
**Effort.** ASSUMPTION: schema change plus one row. **Zero-keys.** Yes.
**VERDICT: MAYBE — conditional on the voice-versus-breadth targeting decision.**

---

#### 22. A labelling programme, planned as a programme

**What.** Decide a target width and label to it. The arithmetic, at a rate near p = 0.9 and
the current prevalence of 8/24 = 0.333:

| target half-width on TPR | positives needed | total items at prevalence 1/3 |
|---|---|---|
| ±0.10 | 35 | ~104 |
| ±0.05 | 138 | ~415 |
| ±0.03 | 384 | ~1,152 |

[verified, appendix. ASSUMPTION: prevalence stays near 0.333.]

**Why it matters.** This is the only item that moves any of the numbers in
[§5.3](#53-the-honest-statistics-on-a-24-item-set). 24 items buys roughly a ±0.15–0.25
answer — enough to distinguish broken (TPR 0.250) from working (TPR 1.000), which is exactly
the v1→v2 comparison and is why that study is honest. It is nowhere near enough to
distinguish v2 from a v3, or model A from model B [R2 §5.2]. It also fixes finding C: the
gate guarding the build is guarded by a set the judge saturates at 24/24, so **it cannot
detect a regression**.

**The decision.** How many afternoons. ±0.10 is ~104 items, which is real work and is the
first point at which the gate becomes a gate again.
**Effort.** ASSUMPTION: several afternoons of the owner's labelling time. **Zero-keys.** Yes.
**VERDICT: MAYBE — but it is the prerequisite for #17, for a meaningful gate, and for any
future claim that one prompt beats another.**

---

#### 23. Record decoding provenance at the next re-record

**What.** Pass a fixed `seed` where the provider supports it; capture `system_fingerprint`
or the equivalent alongside the existing prompt and label digests; prefer a dated model
route in the docs.

**Why it matters.** It makes nothing deterministic — the providers say so explicitly. It makes
non-determinism **attributable**, which is what the repo's digest philosophy is already about.
Right now the provenance chain is prompt digest ✓, label digest ✓, model route ✓, backend
build ✗, so if the recordings were re-drawn and the numbers moved there would be no way to
tell a backend change from a prompt effect [R2 rank 6].

**The decision, and the framing that makes this cheap.** Populating it requires a re-record —
144 + 162 calls, real money. **Do it opportunistically the next time money is being spent
anyway; do not make it its own errand.**
**Effort.** ASSUMPTION: ~80 lines plus a fixture-schema field; the field must be
optional-and-recorded-as-absent, because litellm does not expose it uniformly.
**Zero-keys.** Good — it is metadata; replay is unaffected.
**VERDICT: MAYBE, opportunistically.**

---

#### 24. An OpenTelemetry *section*, not an OpenTelemetry exporter

**What.** One wiki section: why this repository has a bespoke 15-kind event schema in a
market that has converged on a standard, and what the mapping would look like.

**Why it matters.** OTel is named in 3 of 18 postings and is the only observability standard
the market names at all; the repo has **0 references anywhere, including prose** [R3 §4.2].
A reviewer who works in OTel will read a from-scratch schema as not-invented-here unless the
omission is addressed head-on. The honest answer is a good one — the schema carries
`applicable`, `blocked`, `untestable` and vendor-specific audio timing distinctions that a
generic span does not — but it has to be *said*.

**The decision.** Section: yes, hours. **Exporter: see the rejection list.**
`lab/report/interop.py` already exports to two shapes and depends on neither, and that
posture is right.
**Effort.** ASSUMPTION: hours. **Zero-keys.** Perfect.
**VERDICT: MAYBE — do the section; it is cheap and it closes a real credibility hole.**

---

### The large, directional items — decide once, do not drift into

---

#### 25. A duplex adapter and a streaming STT path

**What.** The one change that makes barge-in *discovery* possible, and with it Takeover Rate,
latency-after-interruption, and contract-scored interruption recovery.

**Why it is one decision and not three.** [R1] reaches this dependency from three separate
directions — turn-taking metrics (§3.1), interruption recovery (§3.4), and half of the
endpointing family (§3.3) — and the blocker is the same each time. There are two further
consequences worth knowing before deciding: the clip cache key
(`sha256(text, voice, model, format, normalisation)`) stops being sufficient, because under
duplex the *timing* of an injection is part of the stimulus and two identical clips at
different overlap offsets are different tests with the same key; and `speech_during_timeout`
stops being a declared input modelling the agent's belief and becomes an observation, which
is a strict improvement and is the best single reason to do it.

**The genuinely good news.** `blocked_on()` derives from `EventKind.V2_RESERVED`, so promoting
the two interruption kinds to `KNOWN` flips `audio-barge-in-not-discovered` from `blocked` to
runnable **with no scenario edit**. That is a design decision already in the repo that should
be named as one.

**The genuinely bad news.** Batch engines are the real blocker, not the loop. Interim
hypotheses do not exist in a batch transcription, so no amount of adapter work produces a
barge-in *detection* without a streaming STT path — a new vendor integration surface, not a
reuse [R1 §4].
**Effort.** ASSUMPTION: the largest item in this document. **Zero-keys.** At risk — a
streaming path needs a live vendor to record against, though replay would still hold.
**VERDICT: MAYBE — and if it is taken, take it as one decision with all three payoffs
counted, not as three discoveries.**

---

#### 26. An accent tier over recorded human audio

**What.** Add an accent axis using a recorded human conversational corpus rather than
synthesis.

**Why it matters.** The accent gap is caused entirely by the *synthesis* side — two stock
voices, Voice Library gated behind a paid tier. Recorded human audio needs **no TTS vendor at
all**, only the STT path and the existing field/WER scoring. It changes the claim from *"we
cannot test accents"* to *"we test accents on recorded human audio and cannot test accent
plus our own scenario content"* — a much narrower and more defensible limitation [R1 §6.2].
The published gap is large enough to be worth measuring: the source R1 cites reports 19.7%
average WER on accented conversational English against 2.7% on clean read speech for the same
model.

**The first step is not code.** It is a licence check. R1 explicitly labels the licence
position an ASSUMPTION.
**Effort.** ASSUMPTION: moderate, STT path only. **Zero-keys.** Needs a decision — a recorded
corpus is a data dependency, and the repo's rule is about *keys*, not about *bytes*, so a
committed subset with a compatible licence would satisfy it.
**VERDICT: MAYBE — check the licence first; that is an hour and it decides the item.**

---

#### 27. The production half of the loop

**What.** Sampling live traffic into the eval corpus — the loop where production failures are
categorised and fed back into the regression set.

**Why it matters.** It is the largest market gap by frequency: 11 of 18 postings name
tracing, monitoring or online evals, and several name the loop specifically rather than
abstractly [R3 §4.1]. The repo's current answer is a one-way export.

**Why it is hard here, and the middle option.** It collides with the cardinal rule the moment
it touches a live system. The middle option is real: **ingest a committed fixture that
*represents* production traffic, sample from it into the corpus, and be explicit that the
source is synthetic.** That buys the *shape* of the loop — sampling, categorising,
promoting a row into the regression set — with no credential and no dishonesty, provided the
synthetic origin is stated in the artefact and not just in a README.
**Effort.** ASSUMPTION: large for the real thing; moderate for the middle option.
**Zero-keys.** The middle option: yes. The real thing: no.
**VERDICT: MAYBE — decide *whether* before deciding *how*, and if yes, take the middle option.**

---

### DO NOT BOTHER — eighteen rejections, with reasons

A plan where nothing is rejected is a backlog. These are declined, and the reason is the
useful part — several of them are better interview answers as rejections than they would be
as features.

| # | Rejected | Why |
|---|---|---|
| R1 | **Majority voting over k samples** | Spends k× the calls to make instability **invisible**, which is the opposite of what an eval harness is for. Item 6 spends the *same already-recorded* data to make it visible. The repo's existing objection is correct [R2 §1.10] |
| R2 | **Batch-invariant kernels** | The one technique that genuinely delivers bitwise determinism — and it requires owning the inference server. Unreachable for a hosted, litellm-routed harness. **Worth a paragraph in the docs as "the thing that would actually work, and why we cannot use it"** [R2 §3.3] |
| R3 | **Chain-of-thought scaffolding in the judge prompt** | Raises token cost, and cannot be believed before the single-call case is measured. The v2 prompt already gets the auditability benefit from the mandatory quote plus critique at a fraction of the cost |
| R4 | **A 1–5 scale for the binary judge** | The docstring's argument is sound. The right answer to "severity matters" is more than one binary judge, each calibrated — not one judge with more values |
| R5 | **A panel of judges, now** | Genuinely the best confidence signal and the only attack on self-enhancement bias — and it needs items 6, 11 and 17 in place first or its output has nowhere to go, plus **three provider credentials at record time**, which is an operational burden the single-route design does not carry. 432 + 486 recorded calls [R2 §6.3]. Reject *for now*, with the sequence named |
| R6 | **Decomposing the five-criterion scorer into five calibrated binary judges, now** | Architecturally the most correct item anywhere in the research, and it needs **five label columns instead of one** — five times the labelling in item 11, before item 11 has established what one column costs. Do not start it until it has [R2 rank 10] |
| R7 | **Semantic error rate / embedding similarity** | It is a judge, and this repo's own rule is that a judge must be calibrated before it is trusted. Adding an uncalibrated similarity would import an unauditable number into the tier that has been most careful about auditability — and the failure it targets is already caught deterministically by a field assertion [R1 §2.2] |
| R8 | **Pronunciation assessment** | It grades a *speaker*. This harness has no speaker; it synthesises the caller. Grading our own synthesiser measures the instrument [R1 §2.6] |
| R9 | **Spoofing / anti-deepfake detection** | It is a property of a speaker-verification system. There is no identity claim here to attack, so a spoofing row would have nothing to fail. Revisit *if* a scenario ever gates an action on caller identity [R1 §6.4] |
| R10 | **Running the public voice benchmarks as suites** | They score *models*. This scores an *application* with tools and contracts. Borrow the vocabulary (item 3), not the harness [R1 §3.1, §5] |
| R11 | **MOS prediction on synthesised audio** | Same objection as R7: an uncalibrated judge. One narrow exception is flagged in the research — a perceptual axis on the transport tier's *received* audio, where an unperturbed control arm already exists — and even that is a MAYBE at best [R1 §7.5] |
| R12 | **A 114-perturbation bank** | The admission rule ("a row belongs in the audio tier only if the audio layer is the thing under test") argues against a sweep, and a ladder that never breaks reports nothing. Two additions (reverb, codec) would be defensible; a bank would look more thorough and be less true [R1 §6.1] |
| R13 | **Parallel execution** | The entire parallelism apparatus in a production suite exists to make one number bearable: **1.25 h for 439 rows**. The equivalent number here is **under 2 s for 47 scenarios** (1.44 s and 0.69 s on two runs). It would buy nothing measurable and would import the whole class of contention flake that the machinery exists to paper over. **Writing down why it is absent, with both numbers, is more impressive than having it** [R4 §3.17] |
| R14 | **A server-backed review dashboard** | A web app with auth, a database and dozens of routes is an operations product, not a portfolio artefact, and it breaks the clean-clone rule outright. A single self-contained static HTML file generated from the run JSON would survive the constraints — that is the only version worth considering, and it is a MAYBE, not a DO [R4 §3.13] |
| R15 | **A spreadsheet export layer** | Do not add a dependency for it. Readers of *this* repository read markdown [R4 §3.14] |
| R16 | **Self-healing result amendment** | Automatic re-runs that rewrite the primary results file exist in production because of resource contention this repo does not have. A harness that rewrites its own verdicts is exactly what this repo should never build [R4 §3.5] |
| R17 | **A `known_artefact` suppression class** | Good pattern, wrong repo, wrong time: it has **zero members today**. Building a classification scheme for an empty category is the definition of a half-built thing, and suppression mechanisms are how suites rot. Revisit the day a harness artefact actually appears [R4 §3.3, §8 Q3] |
| R18 | **A metric trend store, now** | The baseline diff is strictly two-point and that is a real limitation — but nothing here is moving. A trend chart over a byte-exact baseline in a 40-commit portfolio repo is a chart of a flat line. This becomes worth building when the repo runs against a model that changes underneath it [R4 §3.12] |

**Also rejected, from the market pass, and these ones should stay rejected loudly:**

- **Browser/UI automation** — outside this repository's remit entirely. It is a
  conversational-agent evaluation harness [R3 §4.8].
- **Cloud, container or orchestration infrastructure** — directly contradicts the cardinal
  rule, which is the repo's single strongest reviewer-facing property. Do not trade it for a
  keyword [R3 §4.8].
- **Taking any eval framework as a dependency** — `lab/report/interop.py` already has the
  right posture: export to their shapes, depend on none of them, pin the shapes with this
  repo's own tests. A dependency would import a version constraint and, for several of them,
  an API-key requirement into a repo whose cardinal rule forbids both [R2 §9].
- **More wiki** — see [§8](#8-what-to-cut-or-stop-investing-in).
- **A CHANGELOG and a type-check gate** — both arguable, neither a gap in what the repository
  *claims*. If one of them is done, mypy is the better of the two, because the tree is
  heavily annotated and nothing verifies the annotations [R5 §B7.2].

---

## 4. Three tiers

### Tier 1 — a weekend

Nine items. Six of them are documentation, three are small code changes, and **none of them
starts anything**. Every one either closes a defect, corrects the record, or makes an
existing capability findable. That is deliberate: the fastest way to stop accumulating
half-built things is to spend the next unit of time finishing the record rather than opening
a new front.

| Order | Item | Effort (ASSUMPTION) | Kind |
|---|---|---|---|
| 1 | [Remove the off-scope personal file](#1-remove-the-off-scope-personal-file) | minutes | deletion |
| 2 | [Name the repository after itself in the README](#2-give-the-repository-its-own-name-in-the-readme) | minutes | docs |
| 3 | [Assert the live judge ≠ the live agent](#9-assert-that-the-live-judge-is-not-the-live-agent) | 30 min | code |
| 4 | [Fix the k-insensitive caveat](#4-fix-the-self-contradicting-caveat-at-labclipy1716-defect-d2) | 1 h | code |
| 5 | [One true sentence about barge-in, propagated](#5-make-one-true-statement-about-barge-in-and-propagate-it-defect-d3) | 1 h | docs |
| 6 | [The naming pass](#3-the-naming-pass--say-what-already-exists-in-words-the-reader-searches-for) | 2–3 h | docs |
| 7 | [McNemar + the detectability floor](#7-mcnemar-in-compare_reports-with-the-detectability-floor-printed) | 2 h | code |
| 8 | [Publish or decline the coverage number](#14-publish-a-coverage-number-or-decline-it-in-writing) | 30 min | docs/config |
| 9 | [The RAG boundary](#6-the-rag-separation) | half a day | docs |

**What the weekend buys.** Four defects closed, one disclosure risk removed, three
market-facing capabilities made findable, one statistical claim made rigorous, and the
repository's own name on its own front page. Nothing new to maintain.

### Tier 2 — if there is a week

Add, in this order, because each earlier item makes a later one interpretable:

| Order | Item | Effort (ASSUMPTION) | Why this order |
|---|---|---|---|
| 10 | [The run-to-run band on every rate](#6-print-the-run-to-run-band-on-every-rate) | ~1 day | It changes what every other number means, so it goes first |
| 11 | [Wilson intervals, and reversing the stated position](#8-print-wilson-intervals-and-reverse-the-stated-position-on-them) | ~1 day | The second half of the same honesty; do them together, argue them together |
| 12 | [The debugging page](#12-write-the-debugging-page) | half a day | Assembly, not research |
| 13 | [The documented gate order](#13-document-the-gate-order--cheapest-first) | half a day | Turns a pile of targets into a procedure |
| 14 | [Blind test–retest by the same labeller](#11-establish-the-human-ceiling-blind-testretest-by-the-same-labeller) | one afternoon + ~80 lines | The ceiling that bounds items 10 and 11 |
| 15 | [Corpus mutation testing](#10-mutation-test-the-corpus-and-publish-the-kill-rate--the-flagship) | 2–3 days | The flagship. Last because it is the only one that needs uninterrupted time |

**What the week buys, in one sentence for the room:** *every published rate now carries both
sources of its uncertainty — sampling error over items and the measured run-to-run
instability of the instrument — the label column has a stated ceiling, and the corpus proves
its own assertions discriminate, in under two seconds, offline.* That sentence is the
strongest thing this repository could say about itself, and none of it requires a key.

### Tier 3 — a direction, not a task

These are not backlog items. Each is a question the owner answers once, and the answer
determines several months.

**A. Voice depth, or market breadth?** `lab/voice` is 15,851 of 31,541 lines — **50.3% of the
engine** — against 4 of 18 postings naming voice at all and **0 of 18** naming any of the
three vendors integrated [R3 §5.2, verified]. If the target is a voice-agent employer, the
duplex adapter (#25), the accent tier (#26) and the self-correction row (#21) are the roadmap
and they are correctly ordered. If the target is the broader agent-evaluation market, the
right move is to stop investing in voice, describe what is there precisely, and spend the
attention on #27.

**B. Regulated-domain specialist, or general harness?** The four-regulator apparatus is
roughly 5,400 lines of code and prose (`roleplay/regime_eval.py` 2,732 + `register.py` 462 +
`docs/ADVISORY_TEST_STRATEGY.md` 1,081 + `docs/SCORECARD.md` 1,086) [verified]. Regulated
*context* appears in 5 of 18 postings; **not one asks for a machine-readable
multi-jurisdiction rule engine**. This is a targeting decision, not a code problem, and
[R3 §5.3] explicitly declined to push on it for that reason. It is the owner's to make and
it should be made deliberately rather than by drift.

**C. Does this repository ever see traffic it did not generate?** #27. Answering "no,
deliberately, and here is what that costs us" is a defensible position that takes one
paragraph. Answering "yes" is several months and needs the middle option to stay honest.

**D. Is the label set ever going to be big enough to gate on?** #22. ~104 items for ±0.10.
Everything in [§5](#5-the-judge-determinism-section) that cannot currently be claimed is
downstream of this one number.

---

## 5. The judge-determinism section

This is the differentiator, so it gets the most space. The claim in this section is specific:
**with one week of work and no API keys, this repository could make a statement about judge
reliability that no surveyed open-source eval framework currently makes.** What follows is
what that statement is, what it costs, and — the part that matters most — the honest
statistics that bound it.

### 5.1 What is already here that nothing surveyed has

R2 surveyed DeepEval, Ragas, promptfoo, Braintrust, Langfuse, OpenAI Evals and Inspect AI.
Four properties in this repository had no equivalent in that survey, and they are all the
same idea applied four times — *the measuring instrument is itself measured, and the
measurement is pinned to what was measured*:

1. **A gate that structurally refuses an uncalibrated judge.** Every surveyed tool lets you
   write a judge and use its verdicts immediately. `require_calibrated()` raises. The
   override must be written at the call site and is unavailable from config or environment.
2. **A prompt digest that invalidates a calibration.** `Judge.with_prompt()` returns a judge
   with no calibration; `ReplayJudge` refuses a stale recording. Carrying agreement across a
   prompt edit is made *impossible*, not discouraged.
3. **Per-item self-consistency reported instead of voted away**, with the cancelling case
   worked through on real data (§5.2 below).
4. **Labels derived by stated rule from the product's own ledgers, with an explicit
   AMBIGUOUS class excluded rather than guessed** — 7 items excluded, each printed with its
   reason, on the principle that *"a visible smaller number beats an invisible wrong one"*.

Two more that are unusual rather than unique: a **warm/cold control arm** that localises
instability to cross-session state, and **withholding the tool ledger from the judge by
default** so the judge answers the half that needs judgement and a deterministic check
answers the half that does not — an information-partition decision with better reasoning
than most frameworks apply to the same choice.

### 5.2 The gap: it has proved its instrument is unstable, and it publishes point estimates

Two findings, both from the repo's own committed data, both reproduced independently in R2.

**Finding one — the aggregate lied.** Three identical runs of the v1 prompt at temperature 0
produced a **byte-identical confusion matrix**: `TP=2 FP=0 FN=6 TN=16` all three times. And
two items flipped. `all-set-saturday` went fail → pass → fail; `claim-buried-in-policy-answer`
went pass → fail → pass. Both carry the human label `fail`, so one left the TP cell exactly
as the other entered it. TP stayed 2. FN stayed 6. Every published rate held.

Three consequences that generalise far beyond this repository:

- **Aggregate stability is not instrument stability.** A confusion matrix repeated three
  times is evidence of nothing on its own. Only the per-item view sees the churn.
- **2 of 24 items (8.3%) were coin flips while the report read as 100% reproducible.** Any
  prompt comparison whose delta is one or two items is reading this noise.
- **The cancellation was luck.** Nothing about temperature 0 caused the flips to offset; they
  offset because both unstable items happened to share a human label.

**Finding two — the same failure with the opposite outcome, and it is stronger.** The live
rubric scorer at temperature 0, three identical runs, 27 items: **two different confusion
matrices out of three identical runs** (run 3: TNR 0.917 (11/12) against 1.000 (12/12) in
runs 1–2). And beneath the verdicts, the score cards are far less stable than the verdicts:
21 of 27 items fully stable, 1 of 27 verdicts moved, **5 of 27 had numbers move while the
verdict held**. The worst single item scored its `mandatory_disclosure` criterion **[0, 4, 0]**
across three identical runs — a four-point swing on a five-point criterion, on the item the
whole compliance rubric exists to catch. The verdict held at `fail` all three times, so a
binary-only view would have called that item stable.

**The honest reading, which the repo currently gives only half of:** the binary collapse in
`lab/judges` is *protective* — it absorbs exactly this kind of magnitude noise — **and it
hides this**. Both are true. The repository argues the first and does not say the second.

**And the gap that follows.** `roleplay/scorer_study/study.md` states in prose that its table
is not reproducible, and then quotes run 1's table as though it were the answer. The gate
verdict it prints is computed from **one sample of a demonstrably unstable instrument**. It
happens to be robust here — run 3 still clears 0.85 — but nothing in the code checks that,
and a judge sitting near the threshold would get a gate verdict that is itself a coin flip
[R2 finding A].

### 5.3 The honest statistics on a 24-item set

Computed for this document with the closed-form Wilson score interval at 95%; the script is
in the [appendix](#appendix--reproduction-log). These reproduce R2's figures exactly, and the
two starred rows also match figures the repository already quotes in prose — which is the
cross-check that says the arithmetic was already trusted, just not printed.

| quantity | point estimate | 95% Wilson interval | width |
|---|---|---|---|
| HC v2 TPR | 8/8 = 1.000 | [**0.676**, 1.000] * | 0.324 |
| HC v2 TNR | 16/16 = 1.000 | [**0.806**, 1.000] * | 0.194 |
| HC v1 TPR | 2/8 = 0.250 | [0.071, 0.591] | 0.519 |
| scorer_study v2 TPR | 15/15 = 1.000 | [0.796, 1.000] | 0.204 |
| scorer_study v2 TNR | 12/12 = 1.000 | [0.758, 1.000] | 0.243 |
| scripted rubric TPR | 9/32 = 0.281 | [0.156, 0.454] | 0.298 |
| scripted rubric TNR | 36/38 = 0.947 | [0.827, 0.985] | 0.158 |

**Four things follow, and each is a sentence worth being able to say out loud.**

**1. The gate is not clearing the bar it claims to clear.** `TPR ≥ 0.85` is cleared by a
point estimate of 1.000 whose 95% lower bound is **0.676**. That is not a reason to abandon
the gate; it is a reason to print the lower bound next to it, so that a reader can see the
gate is passing on a point estimate and not on evidence.

**2. Rule of three.** With 0 observed errors in *n* trials, the 95% upper bound on the true
error rate is ≈ 3/*n*. So **8/8 is consistent with a true miss rate up to 37.5%**, and 16/16
with up to 18.8% [verified]. This is the single most useful sentence to have ready when
somebody asks about a 1.000.

**3. The detectability floor, and it is a repo-specific number.** The v1→v2 comparison is
paired, so McNemar's exact test is correct. With *n* discordant pairs all one way the exact
two-sided p is 2/2ⁿ:

| discordant, all one way | exact two-sided p | significant at 0.05? |
|---|---|---|
| 4 | 0.125 | no |
| 5 | 0.0625 | no |
| **6** | **0.03125** | **yes — the observed case** |
| 7 | 0.0156 | yes |

**On a 24-item set the smallest detectable prompt improvement is six items moving together.**
A v3 that fixed three items and broke none would be unpublishable at p = 0.250 however real
the improvement was. That is the number that makes "label more items" actionable.

**4. The variance the intervals do *not* include.** A Wilson interval is binomial sampling
error over items, and it assumes the judge's answer per item is fixed. §5.2 shows it is not.
The honest total uncertainty is (item sampling) ⊕ (run-to-run instrument noise), and the
second component is currently reported in a different place and never combined. The
defensible cheap combination is to report both: *"TNR 0.917–1.000 across 3 runs; 95% CI on
run 1 [0.758, 1.000]"*. That reads as the genuinely humble claim it is.

**A caveat on the fix itself, which should be printed rather than left for the reader.**
`REPLICATES = 3`. Three replicates distinguish "unanimous" from "not unanimous" and
essentially nothing else — a 2/24 flip rate estimated from three draws is estimated with
enormous error. Whatever band item 6 prints will itself be a very noisy band, and the output
should say so [R2 finding F].

### 5.4 The ceiling nobody can exceed, and why it is not fixable by better prompts

One labeller, one pass. The repository says this itself, in
`lab/judges/hallucinated_confirmation/__init__.py`: *"One model, one temperature, one
labeller. No second rater, so label noise is charged to the judge."* It is stated as a caveat
and never quantified.

The consequence is sharp. **A judge cannot be measured above the reliability of the labels it
is measured against.** When one person labels a set once, the measured TPR is a *joint*
measurement of judge quality and labeller consistency, and nothing in the current design
separates them. HC v2 scores 1.000 on every rate; if the labeller's own test–retest agreement
were 0.95, that judge is either lucky or **agreeing with the labeller's errors** — and the
repository cannot currently tell those two cases apart.

This is deeper than determinism, because no amount of prompt work, decoding work or model
choice can move it.

**The cheapest fix is not a second person. It is the same person, blind, later** (item 11):
re-label the same items with the ids shuffled, and publish the intra-rater agreement as a
printed ceiling next to every judge rate — *"labeller test–retest agreement: 22/24 (0.917);
a judge scoring above this is agreeing with label noise."* One afternoon. Zero keys. Zero
dollars. It is the largest single increment in trustworthiness available anywhere in this
document.

**Note on the metric.** With two label columns Cohen's kappa still applies — two raters,
complete data, nominal. With three raters, or with abstentions (which create structurally
missing data), it must become Krippendorff's alpha. And if the five ordinal criteria ever get
an agreement number it must be **ordinal** alpha or weighted kappa, never plain kappa,
because scoring 4 against a label of 3 is not the same error as scoring 0. Nobody computes
one today, so nothing is wrong — this is a trap to comment before someone reaches for the
existing `_cohens_kappa` [R2 finding E].

### 5.5 What determinism can and cannot be bought, stated once

Temperature 0 makes decoding greedy. It does nothing about whether the logits are the same
number twice. The dominant cause of drift is **lack of batch invariance**: production servers
use dynamic batching, so a request is grouped with whatever traffic is live, and batch size,
padding and position within the batch change the reduction order and therefore the last bits
of the logits. Near an argmax tie, a last-bit difference flips a token, and one flipped token
can flip a verdict. Add backend heterogeneity, silent model updates behind an undated route,
and genuine argmax ties [R2 §3.2, with sources].

| technique | what it actually buys | fits zero-keys? | verdict here |
|---|---|---|---|
| temperature 0 | removes *sampling* variance only | — | already done |
| `seed` | providers describe it as a *best effort*, explicitly not guaranteed; buys partial reduction and, more usefully, a **recorded intent** | yes (metadata) | #23, opportunistically |
| recording `system_fingerprint` / dated model route | no variance reduction; makes variance **attributable** | yes | #23 |
| constrained/structured decoding | removes output-*shape* variance entirely; does **not** make content deterministic. Current parse-error rate is 0/24 and 0/27, so this buys futures | yes | low priority; keep the tolerant parser, because recordings store raw output so replay exercises it |
| batch-invariant kernels | genuine bitwise determinism | **no** — requires owning the server | rejected (R2), and worth a paragraph explaining why |
| majority voting | ~1/√k variance reduction on the aggregate, and it **hides** per-item instability | — | rejected (R1) |
| self-consistency *measurement* | no variance reduction; **quantifies** it per item | yes — already built | the foundation of item 6 |

**The insight that ties the table together:** the three replicates already recorded *are* a
variance measurement, and the honest use of a measured flip rate is not to average it away —
it is to **widen the error bar on every rate derived from a single run, and refuse comparisons
smaller than the band.** `lab/simulator` already ships a measured flake band for scenario pass
rates. The judge does not have one. That asymmetry is the whole of item 6.

### 5.6 The claim worth making, and what it costs

Putting items 6, 8, 11 and 7 together — total effort ASSUMPTION ~3 days plus one afternoon of
labelling, zero keys, zero dollars, entirely over data already committed — produces this:

> **Every rate this harness publishes carries both sources of its uncertainty: the sampling
> error over items, as a Wilson interval, and the measured run-to-run instability of the
> instrument itself, as a band across replicates. Comparisons smaller than the band are
> refused rather than reported. The label column carries its own measured ceiling, so a rate
> above that ceiling is reported as agreement with label noise rather than as accuracy. And
> the smallest improvement this set could detect is printed next to every comparison.**

R2's survey found no framework making that statement. It is buildable in a week, it needs no
credential, and it is a claim about *method*, which is the only kind of claim a small
repository can win on. Every part of it is already latent in data sitting in the tree.

### 5.7 What to reject in this area, and why the rejections are the better answers

- **Voting** — the repository's existing objection is correct and should be kept in writing:
  it spends k× the calls to make instability invisible.
- **Batch-invariant kernels** — the technique that would actually work, unavailable to a
  hosted-route harness. Say so in one paragraph; it demonstrates that the determinism problem
  is understood at the level of the kernel, not just the temperature parameter.
- **A panel of judges** — the best confidence signal available and the only attack on
  self-enhancement bias, and it needs items 6, 11 and 17 first plus three credentials at
  record time. Reject *with the sequence stated*, which is a stronger answer than either
  building it or ignoring it.
- **CoT scaffolding and a 1–5 scale** — both already argued down correctly in the existing
  docstrings. Leave them alone.

---

## 6. The RAG separation

**This is a documentation and navigation problem, and the proposal contains no code.** The
retrieval pack stays exactly as it is.

### 6.1 The problem, precisely

The wiki's §1 "Start here" table lists three rows under the heading **Domain**:

| Domain | Package |
|---|---|
| Advisory sales coaching | `roleplay/` |
| Restaurant booking | `tablemate/` |
| Knowledge retrieval | `ragcheck/` |

The §2.8 architecture diagram draws all three as peers hanging off `lab/`. But §8 files
`ragcheck/` under **"the supporting packages and the corpus"**, alongside `scenarios/`,
`error_analysis/`, `scripts/` and `tests/` [verified].

So the document says two different things about what `ragcheck` is: a third domain in §1 and
§2.8, a supporting package in §8. Both framings are defensible on their own; together they
leave a reader unable to tell whether the retrieval work is a third portability proof or a
different activity that shares an engine. It is the second, and saying so is worth more than
either current framing.

### 6.2 The boundary, as a checkable fact rather than an assertion

This is the anchor the whole section should hang on, because it is one command and it is
unambiguous. **`ragcheck` imports exactly three of `lab`'s subpackages, and roleplay and
tablemate import seven between them:**

```
$ grep -rhE '^ *from lab' ragcheck/*.py | sed 's/ import.*//' | sort -u
from lab.clock
from lab.judges.calibration
from lab.judges.judge
from lab.judges.registry
from lab.trace.build
from lab.trace.schema

$ grep -rhoE 'lab\.[a-z_]+' roleplay/*.py tablemate/*.py | sort | uniq -c | sort -rn
  51 lab.checks      38 lab.judges      24 lab.simulator      15 lab.trace
  14 lab.voice        5 lab.cli          3 lab.clock
```

[verified, appendix]

**`ragcheck` imports zero of `lab.checks`, `lab.simulator`, `lab.voice`, `lab.report` and
`lab.cli`.** That is not an accident and it is not a shortfall — it is the boundary itself,
expressed in the import graph. Conversation evaluation needs a *conversation*: multiple
turns, a simulated caller with gated facts, contracts decided on event-stream position, a
pass^k stability verdict. Retrieval evaluation needs none of those, because **a retrieval
turn is one question and one answer.** What the two share is the part that is genuinely
shared: a trace to record on, a clock to stamp it with, and a judge that must be calibrated
before it is believed.

That is a much better claim than "third domain". *"The same calibrated-judge machinery grades
a retrieval answer and a multi-turn conversation; nothing else transfers, and the import graph
proves the line"* is portability evidence with a receipt.

### 6.3 What is genuinely different about each, in one table

Worth writing down once, because it is the thing a reader is actually trying to work out.

| | Conversation evaluation (`roleplay/`, `tablemate/`, `scenarios/`) | Retrieval evaluation (`ragcheck/`) |
|---|---|---|
| Unit of evaluation | a multi-turn session | a single question–answer pair |
| Where the truth lives | a seeded backend state plus the trace of what the agent did | a corpus with gold chunk ids per question |
| What can be decided without a model | contracts on event-stream position — did the tool call exist, was the field re-asked, did the promise match the action | every retrieval metric — recall@k, precision@k, MRR, nDCG@k, AP@k are exact arithmetic |
| What needs a judge | whether an unbounded claim was hallucinated; the rubric score | whether each atomic claim is supported by the retrieved context |
| The characteristic failure it exists to expose | the agent said it did something it did not do | **retrieval was perfect and the answer was still wrong** |
| Stability model | pass^k with a measured flake band | none — the retriever is deterministic |
| Engine surface used | `lab.checks`, `lab.simulator`, `lab.trace`, `lab.judges`, `lab.voice`, `lab.report`, `lab.cli` | `lab.judges`, `lab.trace`, `lab.clock` — and nothing else |

The bottom-left and bottom-right cells are the sentence: one half exists to catch a *decision
that did not match an action*, the other exists to catch a *number that was right for the
wrong reason*. They are different failure classes and they need different instruments. The
repository already implements both correctly; it simply does not say this anywhere.

### 6.4 The proposal — three edits, no code

**1. The wiki (`docs/WIKI.md`).**
- Change the §1 "Start here" table so it has **two headings, not one column of three**: a
  *Conversation domains* block with `roleplay/` and `tablemate/`, and a separate one-row
  block, *A second kind of evaluation on the same engine*, for `ragcheck/`. One extra
  sentence under it: what it shares (judges, trace, clock) and what it does not (contracts,
  simulator, voice, report, CLI).
- Add the import-graph fact from §6.2 to §2.8, next to the existing "the interesting arrows
  are the absent ones" note. It is the same argument, one level finer, and §2.8 is already
  the right place for it.
- Move the `ragcheck/` entry in §8 out of "supporting packages" into its own numbered
  subsection — or, if renumbering is unwelcome, leave it where it is and add a one-line
  cross-reference from §1 saying it is filed there and why. Either resolves the contradiction;
  the second is cheaper.
- Add the §6.3 table verbatim. It is the whole answer in one screen.

**2. `README.md`.** The current framing is "two unrelated domains on the same engine, which
is the evidence it will work on a third". That is a good sentence and `ragcheck` currently
sits awkwardly beside it. Make the retrieval pack a **short, clearly separate section** with
its own heading, introduced as a second *kind* of evaluation rather than a third domain — and
keep its best asset in the first line of that section, which is the worked example where
every retrieval metric is 1.000 and the answer is 67% wrong.

**3. Package docstrings.** `ragcheck/__init__.py` is already the strongest document in this
area — it states what the package is, the three ideas worth the read, that it runs with no API
key, and an explicit **"WHAT IS NOT HERE, DELIBERATELY"** section naming embeddings, cosine-
similarity metrics, chunking-strategy evaluation and a vector store [verified]. It needs **one
added line**: the import boundary, stated as a rule — *this package imports `lab.judges`,
`lab.trace` and `lab.clock`, and deliberately nothing else; if it ever needs `lab.checks` or
`lab.simulator`, the thing being evaluated has stopped being a retrieval turn.* That converts
an accident-of-history into an invariant a reader can check in one command.

Correspondingly, one line in `lab/judges/__init__.py` noting that it is the one subpackage
used by both kinds of evaluation, and that this is why it carries the calibration machinery
rather than either domain carrying its own.

### 6.5 The declined vector database, written down as a decision

The owner has declined a vector store twice. **That decision should be written into the
repository as a decision, with its reasoning, rather than left as an unlisted gap** — because
otherwise every future reader (and every future research pass) rediscovers it as a hole.

The reasoning, stated properly:

> **Every retrieval metric here is retriever-agnostic, and that is the point.** recall@k,
> precision@k, MRR, nDCG@k and AP@k are computed from a ranked list of chunk ids against a
> gold set. They do not know, and must not know, whether that ranking came from BM25, from a
> dense embedding index, from a hybrid, or from a reranker. Swapping the retriever changes the
> *input* to these metrics and changes **not one line** of their definition — which is exactly
> the property that makes them a measuring instrument rather than a benchmark of one stack.
> `ragcheck/corpus.py` already ships two `Retriever` implementations behind a one-method
> protocol, so the substitution point exists and is exercised.
>
> Adding a vector store would therefore add an *engineering dependency* and **zero
> methodological content**: a new install, a new index to build, a new failure mode, an
> embedding model to choose and version — in exchange for demonstrating a component whose
> output the metrics deliberately do not depend on. It would also put pressure on the
> cardinal rule, because the credible versions of it want a hosted service.
>
> The honest limitation, stated plainly rather than hidden: **this repository can argue about
> retrieval evaluation methodology and cannot demonstrate retrieval engineering.** Those are
> different skills and only one of them is what this package is for. If a reader needs the
> second, the one-method `Retriever` protocol is where they would plug it in, and the metrics
> would not move.

That paragraph belongs in `docs/RAG_NOTES.md` and, in one compressed sentence, in the wiki's
§10 limitations. It converts a repeated "no" into a stated position — which is worth more in a
conversation than the vector store would have been.

---

## 7. Patterns worth porting from a production suite

R4 read a two-year-old regression harness that runs against a live conversational product and
compared it structurally against this repository. Everything below is a **pattern**, stripped
of its subject matter; the suite is referred to only as "the production suite". Seventeen
ideas were extracted; the ones worth acting on have already been ranked into
[§3](#3-the-ranked-enhancements) and are cross-referenced here rather than repeated.

### 7.1 The ported ideas, with their verdicts

| Pattern | Cost (ASSUMPTION) | Verdict | Where |
|---|---|---|---|
| Mutation-test the assertions and publish the kill rate | 2–3 days | **DO IT** | [#10](#10-mutation-test-the-corpus-and-publish-the-kill-rate--the-flagship) |
| A documented gate order, cheapest stage first | half a day | **DO IT** | [#13](#13-document-the-gate-order--cheapest-first) |
| Cluster failures; rank the top failing scenarios; put the conversation inline with the failure | ~1 day | MAYBE (near-DO) | [#16](#16-cluster-the-failures-and-rank-them) |
| `INCONCLUSIVE` — a status for a row that could not be graded | half a day | MAYBE | [#18](#18-inconclusive-as-a-scenario-level-status) |
| A no-look-ahead contract on the caller's source | half a day | MAYBE | [#20](#20-the-no-look-ahead-source-contract) |
| Failure ownership, severity, and a paste-ready finding renderer | 1–2 days | MAYBE | below, 7.2 |
| Separate the evaluators that **gate** from the evaluators that **measure** | half a day | MAYBE | below, 7.3 |
| A static single-file run viewer | ~1 day | MAYBE | [R14](#do-not-bother--eighteen-rejections-with-reasons) |
| A `known_artefact` suppression class | ~1 day | **DO NOT BOTHER** | [R17](#do-not-bother--eighteen-rejections-with-reasons) |
| Result persistence in a database / a run index | 2–3 days | **DO NOT BOTHER** | [R18](#do-not-bother--eighteen-rejections-with-reasons) — git *is* the run store here |
| Parallel execution, worker pools, contention-flake machinery | 2–4 days | **DO NOT BOTHER** | [R13](#do-not-bother--eighteen-rejections-with-reasons) |
| Self-healing amendment of the results file | — | **DO NOT BOTHER** | [R16](#do-not-bother--eighteen-rejections-with-reasons) |
| A server dashboard with auth and a database | weeks | **DO NOT BOTHER** | [R14](#do-not-bother--eighteen-rejections-with-reasons) |
| A spreadsheet export layer | 1–2 days | **DO NOT BOTHER** | [R15](#do-not-bother--eighteen-rejections-with-reasons) |

### 7.2 Failure ownership — the one that decides whether findings get fixed

The production suite turns a saved run into a structured defect report: which component owns
the missing action, severity derived from the scenario's section, a detected repetition loop
with its repeat count, a root-cause hypothesis, and the transcript attached so a human can
overrule it. **It is heuristic tables with no model calls** — instant, free, reviewable — and
its docstring presents that as a design claim rather than an apology.

This repository stops one step earlier, and its evidence layer is better: `CheckResult.evidence`
carries the quoted trace events that justify the verdict, deterministic down to sorted JSON
keys so two reports diff on behaviour. What it does not answer is **"whose bug is this?"**

The port here would be smaller than the original because this corpus is typed: an ownership
map from `contract name × tool name → component`, a severity from the scenario's suite and
tags, and a one-paragraph markdown renderer per failing scenario. Everything it needs is
already in the trace and the corpus, and nothing needs a model.
**Effort:** ASSUMPTION 1–2 days. **Zero-keys:** yes, trivially — it is a pure function of the
run report. **VERDICT: MAYBE**, and it pairs naturally with [#16](#16-cluster-the-failures-and-rank-them):
cluster first, then attribute the clusters.

### 7.3 Gating evaluators versus measuring evaluators

The production suite defines nine evaluators and lets **six** decide the gate; the other three
are explicitly quality-only and cannot change the verdict, by construction, with a flag to
score them anyway for the detail columns. It is a small, mature idea: a metric you are not yet
willing to block on should still be measured, and the way you stop it silently becoming a
blocker is to put the distinction in the code rather than in someone's head.

The obvious application here is the 28-KPI scorecard (`roleplay/scorecard.py`, 1,724 lines).
Splitting it into a **gating** set and a **measured** set, with the gating set justified in one
sentence each, directly answers the reviewer question *"why is this number here?"* — and it is
the cheapest available response to [§8](#8-what-to-cut-or-stop-investing-in)'s concern that 28
KPIs is the shape two postings explicitly warn against.
**Effort:** ASSUMPTION half a day. **Zero-keys:** yes. **VERDICT: MAYBE**, and it is the
better first move on the scorecard than deleting anything.

### 7.4 Two rules to adopt pre-emptively, before the pain arrives

Both cost a paragraph and a small assertion, and both prevent a class of error rather than
detecting one.

**Never compare across profiles.** The production suite's "fast mode" turns off the
per-scenario model judge, switches topic detection from model-based to keyword-based, and caps
turns at 15 instead of 45. That is not a speed knob — **it is a different instrument**, and a
pass rate measured under it is not comparable to one measured without it. R4 found the flags
documented and no rule forbidding the comparison. The rule to adopt here: *the run report
records its profile, and a baseline diff across differing profiles refuses rather than
compares.* Refusing matches this repository's temperament better than warning does.

**Row selection is not subject configuration.** The two look identical from outside and
conflating them produces a run that quietly tested a different subject than the one it
reported on — an error no downstream reporting discipline can recover from. This repo already
keeps them separate (`--suite`, `--tag`, `--scenario` on one side; `--subject`,
`--agent-factory`, `--live-*` on the other), but the **report records the subject as
free-text**. Making it a structured, validated field that appears in the report header and in
the baseline comparison closes the gap where a baseline is silently compared against a run of
a different subject.

### 7.5 One question worth answering honestly, in the wiki

Every mocked harness has a version of this problem: **a mock that stands in for a real gate
necessarily re-implements the gating logic that lives inside the real one, so the behaviour of
the real gate is not observable through the mock.** The production suite documents this
carefully and cannot escape it.

The version of the question for this repository is: *which contract failures could only ever
be caused by the fixture, and does the repo say so anywhere?* [R4 §8 Q4]. A related, cheaper
audit: *a trace built from what the model asked for is not a trace of what the system did* — if
any tool in `tablemate/` or `roleplay/` can be invoked from a code path rather than a model
decision, does the trace record it? That is a twenty-minute audit and the answer is worth
writing down either way [R4 §3.16].

### 7.6 What the production suite does better, unhedged

Worth keeping in view, because it is what a portfolio harness structurally cannot claim:

1. **It grades a product it does not control**, across several builds of a moving target. This
   repository grades a product that lives in the same repo and is written to be gradable. That
   is a much easier problem and should not be presented as the same one.
2. **It answers "whose bug is this?"** (§7.2).
3. **Its corpus has provenance** — rows derived from confirmed real incidents, in the
   proportions real traffic shows. This corpus is hand-authored and says so, which is honest,
   but it is not the same claim. The transferable part is not the proportions mechanism, which
   would be dishonest to fake here; it is the **authoring rules** learned from a first attempt
   that produced 100% false-positive failures: assert on words the system would actually say
   rather than on diagnostic labels, name only tools that exist, write the caller instruction
   as a caller's dialogue plan rather than as notes about what the system should do, and mark a
   scenario whose signal the harness cannot observe rather than asserting on it anyway. Rules
   one and three could be static corpus lints here — as **warnings**, since they are heuristics
   [R4 §3.15].
4. **It has learned which of its own failures are lies**, and encoded that in a reviewable
   filter with an audit trail. That knowledge is only obtainable by running for two years.
5. **Its flake economics are worked out** under real cost. This repo's flake handling is
   theoretically better — pass^k, a measured band, FLAKY is not a pass — and has never been
   stress-tested at cost.

**And the mirror, which is the list not to give up under pressure:** a typed trace as the
single artefact (the production suite reconstructs structure by regexing formatted strings out
of a rendered transcript, and needs a ~120-line adapter to pair tool calls back to their
results — pure tax that exists because its trace was a string); contracts decided on position;
**calibration gates that raise** — R4 grepped all 95 harness files in the production suite for
`kappa`, `inter-rater`, `true positive`, `precision`, `recall`, `calibrat*` and found **no
matches**, because it pins a baseline rate and alerts on a 2-point drift, which is drift
detection and will hold a baseline steady around an evaluator that is wrong a third of the
time; denominator discipline enforced by types; a corpus that rejects assertions which can
never fire; `expected_failure` as a live expectation; vacuous passes as a first-class state; a
report that audits itself; zero keys; and 1.44 seconds.

---

## 8. What to cut, or stop investing in

Cutting is as valuable as adding, and this section is the one most likely to be resisted.
**None of these is a recommendation to delete working code.** They are recommendations to stop
treating size as an asset and to stop spending the next hour on the thing that already has the
most hours in it.

### 8.1 The wiki — stop treating its line count as a feature

`docs/WIKI.md` is 14,803 lines with 72 Mermaid diagrams and **65.3% of all documentation in
the repository** (22,683 lines across all `.md` files) — *measured at commit `032eab7`, the
baseline this plan was written against* [verified]. It is asked for by **0 of 18** postings;
the nearest requirement is "communication of findings" (7 of 18), and every one of those
phrases it as clarity, not volume [R3 §5.1].

Committing this plan changes those two numbers, so the appendix command no longer returns
them: at `e4ee307` the wiki is 14,853 lines of 24,447 total = **60.8%**. The share fell
because this document added 1,714 lines of non-wiki prose, which is a change in the
denominator and not evidence of restraint. Both figures are given so the reader can
reproduce either; the argument below depends on neither.

It is also, on the evidence of R5's audit, a genuinely good document: **zero broken internal
anchors** — re-verified at `e4ee307` across 449 distinct heading slugs and 313 internal
links — every headline number re-derived, all 30 `make` targets documented. That is precisely
why the recommendation is not to cut it.

**The recommendation is to change what it is *for*.** Three costs a reviewer feels
immediately: it signals that documentation is the output rather than the measurement; it makes
the 31,541-line engine look smaller by comparison than it is; and nobody reads it, so its
accuracy work is unpaid. The reader-facing surface should be **README + one findings document
+ the debugging page**, with the wiki positioned as the reference you *look things up in*
rather than the headline artefact.

**Concretely: no new wiki volume should be written until the [tier-1 list](#tier-1--a-weekend)
is done.** Four of the nine tier-1 items are corrections *to* the wiki, which is the right
direction of spend.

### 8.2 Voice — correctly built, over-weighted for anything but a voice employer

`lab/voice` is 15,851 lines, **50.3% of the engine**, against **4 of 18** postings naming
voice at all and **0 of 18** naming any of Deepgram, ElevenLabs or LiveKit [R3 §5.2,
verified]. Inside it, `lab/voice/transport/` — the WebRTC tier — is **4,267 lines**, asked for
by 0 of 18, the only part of the repository requiring a credential and a reachable server, and
the only optional dependency deliberately kept out of `[dev]`. It is real engineering that
almost no reader will be qualified to assess.

**The trade to weigh:** the same reader-attention spent on the production loop
([#27](#27-the-production-half-of-the-loop), 11 of 18) or on cost accounting
([R3 §4.3](#24-market-facing), 3 of 18) moves against a market that asks for them, versus 0 of
18 for more transport work.

**This is tier-3 decision A and it should be answered before any further voice work**, because
three of the MAYBEs in §3 — [#21](#21-a-self-correction-row-and-the-schema-change-it-needs),
[#25](#25-a-duplex-adapter-and-a-streaming-stt-path), [#26](#26-an-accent-tier-over-recorded-human-audio) —
are all downstream of the same answer. One observation from the research to hold alongside it:
of the five perturbations, `shift_pitch` is the only one with no analogue in a real call path,
and it is used by exactly one scenario. If perturbation work is ever revisited, ask what that
one proves before adding more [R1 §10.3].

### 8.3 The 28-KPI scorecard — reframe before you cut

`roleplay/scorecard.py` is 1,724 lines. No posting in either market corpus asks for a
scorecard of that width; the market asks for a *small* number of metrics that are *defended*,
and two postings explicitly warn against vanity metrics [R3 §5.4]. Twenty-eight KPIs is the
shape those sentences warn about, whether or not it actually is one here.

**The cheap fix is not deletion — it is [§7.3](#73-gating-evaluators-versus-measuring-evaluators):
split it into a gating set and a measured set, and justify each gating KPI in one sentence.**
Four or five gating metrics with twenty-three measured behind them is a defensible artefact.
Twenty-eight undifferentiated KPIs is not, even if every one is individually sound.

### 8.4 The advisory regulatory apparatus — a targeting decision, not a code problem

Roughly 5,400 lines of code and prose [verified, §4 tier 3 B]. Regulated-domain *context*
appears in 5 of 18 postings; **not one asks for a machine-readable multi-jurisdiction rule
engine that computes verdicts from cited registers**, including the posting that is explicitly
about high-consequence regulated environments [R3 §5.3].

R3 deliberately declined to push on this and the same restraint applies here: **if the target
is a regulated-domain vendor, this is the differentiator and it is correctly sized. If the
target is the broader agent-evaluation market, it is the largest piece of work in the
repository that the market has no line item for.** That is the owner's call, it should be made
explicitly, and it does not need to be made this week — but it should not be made by
continuing to add to it without deciding.

### 8.5 Two orphan documents, and a discoverability gap

- **`docs/REAL_STACK_ARCHITECTURE.md`** — 1,109 lines, referenced by **nothing** in the tree,
  headed *"Status: proposal, for approval before any code is written."* The repository has
  since built all of it: real STT and TTS engines, the WebRTC transport tier, and a spoken-call
  runner. It is 78 KB describing, in the future tense, things that already ship. A reader who
  opens `docs/` sorted by size and reads this first will discount every subsequent claim.
  **Options: archive it with a two-line header naming the commit that shipped it; convert it
  into a "how we got here" section the wiki links from §8.3; or delete it. Any of the three is
  better than the current state.** [R5 §B4]
- **`docs/PLAYWRIGHT_NOTES.md`** — [item 1](#1-remove-the-off-scope-personal-file).
- **`scripts/set_keys.sh`** — tracked and referenced by nothing: not the `Makefile`, not the
  wiki, not the README. Inspected during the research pass: it is well written, writes a
  `chmod 600` gitignored file, uses silent reads, never echoes a value, and contains no
  credential. The problem is purely that **a script which touches credentials is
  undiscoverable** — a reader who needs it will not find it, and a reader who stumbles on it
  has no document saying it is the sanctioned path. One line in the wiki fixes it [R5 §B8].
- **Six documents the wiki never links** — `AUDIO_TRANSPORT.md`, `cli.md`,
  `ADVISORY_TEST_STRATEGY.md`, `ADVISORY_DEMO.md` and the two above, totalling ~3,600 lines
  the "everything in this repository" document does not point at. Six links, half an hour
  [R5 §A4].

### 8.6 What must not be cut by mistake

Named explicitly, because a cutting exercise has a way of reaching the wrong things:

- **`lab/judges`** — 2,966 lines in the package, plus 1,319 in the worked calibration study
  [verified]. Highest value per line in the repository. **The failing v1 prompt is the asset,
  not an embarrassment**, and the study that measured it is the reason anything in
  [§5](#5-the-judge-determinism-section) can be claimed.
- **`lab/checks/contracts.py`** — 1,749 lines, six concrete contracts plus the abstract base
  [verified]. Maps directly onto the joint-second market requirement, and it is the scoring
  half of the interruption-recovery work if [#25](#25-a-duplex-adapter-and-a-streaming-stt-path)
  is ever taken.
- **`error_analysis/`** — 288 lines of Python plus four short documents. The cheapest asset in
  the repository relative to what the market asks for.
- **The timing calibration gate.** Small, distinctive, and it refuses.
- **The zero-keys rule and `ci.yml`.** Do not touch. Every proposal in this document was tested
  against them, and three were rejected for failing.
- **1.44 seconds.** It is a feature. Judge every future addition partly on whether it survives.

---

## 9. Assumptions register

Everything above is either reproducible by a command in the appendix, quoted from a file in
this tree, or attributed to one of the five research files, which carry their own sources —
except the following.

1. **Every effort estimate.** All of them. They are judgement, not measurement, and none of
   the items has been built.
2. **The market frequencies** (n of 18, n of 32) come from R3's hand-coded corpus, coded once
   by one rater with no agreement statistic, biased toward postings that could be fetched.
   R3 states this at length. Treat them as an **ordering, not a measurement** — a 6 of 18 and
   a 5 of 18 are not distinguishable; only the 11s, the 1s and the 0s are worth acting on.
3. **Tool and framework capability claims** in [§5.1](#51-what-is-already-here-that-nothing-surveyed-has)
   come from vendor documentation as surveyed in R2 §8, not from running each tool. The
   statement "no surveyed framework does X" is a claim about that survey, not proof of novelty.
4. **The power table in [#22](#22-a-labelling-programme-planned-as-a-programme)** assumes
   prevalence stays near 8/24 = 0.333. If the post-filter defect rate shifts, the totals scale.
5. **Provider support for `seed`, `system_fingerprint`, JSON-schema decoding and logprobs is
   uneven** across the routes litellm serves. Believed true, not enumerated per provider.
6. **The licence position of any recorded-human accent corpus** ([#26](#26-an-accent-tier-over-recorded-human-audio))
   is unchecked. That check is the first step of that item and may end it.
7. **The claim that the interruption-recovery contracts would transfer to injected audio**
   ([G16](#22-speech-and-voice)) is R1's structural argument, not a demonstration. Nothing has
   been run.
8. **The entity-error-rate denominator** in [#19](#19-roll-the-field-assertions-up-into-a-corpus-entity-error-rate)
   is a grep count of capture declarations (16 across 12 rows), not a count of scored entities.
   The true denominator may differ.
9. **The paragraph in [§7.5](#75-one-question-worth-answering-honestly-in-the-wiki)** poses a
   question about this repository's fixtures that this pass did not answer.

---

## Appendix — reproduction log

Every number this document introduces, and the command that produced it. Run from the
repository root with `.venv/bin/python`. Figures carried over from the five research files
carry their `[R#]` tag inline instead and are reproducible from those files' own appendices.

| Figure | Command |
|---|---|
| `1976 passed, 4 skipped in 55.47s` | `.venv/bin/python -m pytest -q` |
| 47/55 scenarios driven, **1.4 s** wall clock (re-measured at 0.69 s on a later pass; wall clock varies with machine load, the scenario count does not) | `time .venv/bin/python -m lab.cli run --no-traces` |
| calibrate exits 0; v1 2/24 items unstable, v2 24/24 unanimous | `.venv/bin/python -m lab.cli calibrate` |
| **12 failures**, 36/369 (9.8%) contract evaluations failed, 44/47 (93.6%) stable-pass, 5 of 12 failures are `propagation:*` | `python -c "import json,collections; d=json.load(open('reports/run_report.json')); print(len(d['failures']), d['headline'], collections.Counter(f['contract'] for f in d['failures']))"` |
| `lab/` 31,541 · `roleplay/` 15,817 · `tablemate/` 5,091 · `ragcheck/` 3,108 · `tests/` 28,307 · `scenarios/` 2,404 · `error_analysis/` 288 | `find <pkg> -name '*.py' \| xargs wc -l \| tail -1` |
| `lab/voice` 15,851 (50.3% of `lab/`); `lab/voice/transport` 4,267 | same, on those directories |
| `lab/judges/*.py` 2,966; `lab/judges/hallucinated_confirmation/*.py` 1,319 | `wc -l lab/judges/*.py \| tail -1` etc. |
| `docs/WIKI.md` 14,803 of 22,683 total doc lines = 65.3% **at `032eab7`**; 14,853 of 24,447 = 60.8% at `e4ee307`, because this plan is itself 1,714 of those lines | `git stash` or `git checkout 032eab7 --` then `wc -l docs/*.md README.md DESIGN.md INTERVIEW_NOTES.md`; same command on `HEAD` for the second pair |
| `roleplay/scorecard.py` 1,724 · `regime_eval.py` 2,732 · `register.py` 462 · `ADVISORY_TEST_STRATEGY.md` 1,081 · `SCORECARD.md` 1,086 (≈5,400 for the apparatus) | `wc -l` on those paths |
| `lab/checks/contracts.py` 1,749; 6 concrete contracts + 1 abstract base | `wc -l`; `grep -n '^class ' lab/checks/contracts.py` |
| `EventKind`: 15 known kinds, 2 in `V2_RESERVED`, none carrying tokens or cost | `python -c "from lab.trace.schema import EventKind; print(len(EventKind.KNOWN), sorted(EventKind.V2_RESERVED))"` |
| 194 scenario YAML; `validate --coverage` sees 55 | `find scenarios -name '*.yaml' \| wc -l`; `python -m lab.cli validate --coverage` |
| 30 `make` targets | `grep -E '^[a-zA-Z0-9_-]+:' Makefile \| cut -d: -f1` |
| **`ragcheck` imports only `lab.clock`, `lab.judges.*`, `lab.trace.*`** | `grep -rhE '^ *from lab' ragcheck/*.py \| sed 's/ import.*//' \| sort -u` |
| roleplay+tablemate: `lab.checks` 51 · `lab.judges` 38 · `lab.simulator` 24 · `lab.trace` 15 · `lab.voice` 14 · `lab.cli` 5 · `lab.clock` 3 | `grep -rhoE 'lab\.[a-z_]+' roleplay/*.py tablemate/*.py \| sort \| uniq -c \| sort -rn` |
| Nothing in `lab/` imports `ragcheck` | `grep -rn ragcheck lab/` → no matches |
| 16 capture declarations across 12 audio rows | `grep -c "expect_capture\|capture:" scenarios/audio/*.yaml \| grep -v ':0'` |
| README names a different project; 0 occurrences in the wiki | `grep -rl` for the name over the tree; `grep -c` in `docs/WIKI.md` |
| `docs/PLAYWRIGHT_NOTES.md` 216 lines, 0 hits for every clean-room term, referenced by nothing | `wc -l`; `grep -ric <term>` per term; `grep -rl` over the tree |
| `docs/REAL_STACK_ARCHITECTURE.md` 1,109 lines, referenced by nothing | `wc -l`; `grep -rl REAL_STACK_ARCHITECTURE` |
| `lab/cli.py:1712` interpolates `args.repeats`; `:1716` hardcodes 0.44 | `sed -n '1705,1725p' lab/cli.py` |

**Wilson score interval (95%), McNemar exact, and the power table:**

```python
import math
def wilson(k, n, z=1.959963985):
    p = k/n; d = 1 + z*z/n
    c = (p + z*z/(2*n))/d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return c-h, c+h
# 8/8   -> [0.676, 1.000]      16/16 -> [0.806, 1.000]     2/8   -> [0.071, 0.591]
# 15/15 -> [0.796, 1.000]      12/12 -> [0.758, 1.000]     11/12 -> [0.646, 0.985]
# 9/32  -> [0.156, 0.454]      36/38 -> [0.827, 0.985]
# 3/3   -> [0.439, 1.000]      5/5   -> [0.566, 1.000]     <- defect D2
#
# McNemar, n discordant pairs all one way: exact two-sided p = 2/2**n
#   4 -> 0.12500   5 -> 0.06250   6 -> 0.03125   7 -> 0.01562
#
# power: positives needed for half-width h at p=0.9 is z**2*p*(1-p)/h**2
#   +-0.10 -> 35 positives, ~104 items at prevalence 1/3
#   +-0.05 -> 138 positives, ~415 items
#   +-0.03 -> 384 positives, ~1152 items
#
# rule of three, 95% upper bound on the true error rate with 0 errors in n:
#   3/8 = 0.375     3/16 = 0.1875     3/15 = 0.200
```

**Working state after this pass.** `git status --short` showed only the untracked
`docs/_plan/` before it and this file plus `docs/_plan/` after it. No `.py`, `.yaml`, fixture,
`Makefile` or `pyproject.toml` was modified. `make calibrate` and `lab.cli run` regenerated
their artefacts byte-identically, which is why the tree stayed clean; CLI output went to the
gitignored `reports/`.

---

*Written as a decision document. Nothing in it is a commitment, and the most useful thing in
it is [the rejection list](#do-not-bother--eighteen-rejections-with-reasons).*
