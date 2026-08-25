# The Wiki

Everything in this repository: what each part is, why it exists, how to run it, and
the rules it refuses to break.

Written for two readers at once. Each section opens with **In plain terms** — no
jargon, safe to read if you have never opened the code — and then **In detail**,
which names files, functions and numbers. Read the first, skip the second, and you
will still understand what this project does and why.

---

## Table of contents

1. [Start here](#1-start-here)
2. [The one idea everything rests on](#2-the-one-idea-everything-rests-on)
3. [The golden rules](#3-the-golden-rules)
4. [The three tiers: how anything gets run](#4-the-three-tiers-how-anything-gets-run)
5. [Map of the repository](#5-map-of-the-repository)
6. [The processes: every command, when to use it, what it costs](#6-the-processes)
7. [The domains](#7-the-domains)
8. [What it found](#8-what-it-found)
9. [Limitations, stated plainly](#9-limitations-stated-plainly)
10. [How to extend it](#10-how-to-extend-it)
11. [Glossary](#11-glossary)

---

## 1. Start here

### In plain terms

Companies are building AI that talks to people — coaching a salesperson, answering
a customer, making a phone call. That AI can break in ways ordinary software cannot:
it can say something confidently wrong, it can give a different answer to the same
question twice, it can mishear an accent, or it can quietly stop following the rules
it is legally required to follow.

You cannot test that by hand. Nobody can listen to seven hundred calls after every
code change.

**This project is a machine that has thousands of conversations with an AI and
grades every one.** It works in text (fast, free) and in real speech (slower, costs
money, catches things text cannot). It also grades *the grader* — because an
automatic marker that is wrong is worse than no marker at all.

### In detail

`conversation-eval-lab` is an evaluation harness for conversational AI agents, voice
and text, built around a single auditable trace. `lab/` is the reusable engine
(31,541 LOC). It is applied to two unrelated domains to prove it is not
domain-specific:

| Domain | Package | What it models |
| --- | --- | --- |
| Advisory sales coaching (BFSI) | `roleplay/` (15,817 LOC) | a trainee adviser graded against a rubric, under four regulators |
| Restaurant booking | `tablemate/` (5,091 LOC) | a multi-agent booking assistant with seeded defects |
| Knowledge retrieval | `ragcheck/` (3,108 LOC) | retrieval quality separated from answer groundedness |

**194 scenario files**, **1,976 tests**, and a clean clone runs green with **zero API
keys**.

---

## 2. The one idea everything rests on

### In plain terms

Everything the AI does gets written down in one list, in order: *the customer said
this, the AI replied that, the AI looked up a booking, the AI stayed silent for six
seconds.* That list is called **the trace**.

Every single check, score, and measurement reads **only** that list. Nothing reads
the AI directly.

Why this matters: it means you can swap the AI for a different one, or swap text for
real speech, and **every check keeps working unchanged**. It also means every number
in a report can be traced back to a specific line in a specific conversation. There
are no numbers that came from somewhere nobody can point at.

### In detail

**Trace-first architecture.** `lab/trace/schema.py` defines `Trace` and `EventKind`;
`lab/trace/build.py` builds one via `TraceBuilder` with an *injected* clock
(`lab/clock.py`) so timing is testable; `lab/trace/io.py` is the JSONL codec.

Adapters produce traces. Checks, judges, metrics and reports consume traces. Nothing
consumes an agent.

The consequence that pays for the whole design: `roleplay/scorer.py:151`
`session_view(trace)` is a pure function of trace events. So when `roleplay/spoken.py`
runs the same conversation through real speech synthesis and recognition, it emits
the same event kinds — and **both existing scorers grade the spoken call unchanged,
with no fork**. That is why joining the audio tier to the conversation tier was cheap
rather than a rewrite.

See [`docs/trace_schema.md`](trace_schema.md) for the event reference.

---

## 3. The golden rules

These are the non-negotiables. Most are enforced in code, not documentation — a rule
that lives only in a README gets broken within a month. Where a rule is enforced, the
enforcing file is named.

### Rule 1 — A clean clone must work with no keys

**Plain:** anyone can download this and run it in two minutes without signing up for
anything.

**Detail:** `pip install -e ".[dev]" && pytest` must pass with every credential
variable unset. Every live provider path is opt-in behind a `LAB_LIVE_*` flag and
**must** ship a committed fixture that replays deterministically in its place. This
is verified from an actual fresh `git clone` with `env -i` and an empty `HOME`, not
from `env -u` in a working directory — a distinction that once hid a real breakage.

### Rule 2 — The trace is the product

**Plain:** every number must be traceable to a line in a conversation.

**Detail:** see §2. `DESIGN.md` §1.

### Rule 3 — Every rate carries its denominator

**Plain:** never say "93%". Say "93% of 2,132".

**Detail:** a naked percentage is a defect in this repository. `CheckStat.rate`
returns a string like `"3/4"`; the report layer refuses a numerator larger than its
denominator. `DESIGN.md` §3.

### Rule 4 — Absence is a first-class result, and it is not a pass

**Plain:** if a check had nothing to look at, that is not the same as passing.

**Detail:** when a conversation never reaches a handoff, the propagation contracts
turn **vacuous** and the report says so, rather than silently counting a pass.
`lab/checks/engine.py:21,85`. `DESIGN.md` §4.

### Rule 5 — A check that cannot fail is not a check

**Plain:** if a test has only ever been seen agreeing, it has not been tested.

**Detail:** this rule was written *because it was violated twice, in different
packages, on the same day*:

- `fca-fair-clear-not-misleading` returned `satisfied` on every possible input,
  including "this is risk-free and you cannot lose" — it declared no forbidden
  patterns, so it had no failing path. It was the only engaged entry on two rows.
- `capture_outcome` — the single function all sixteen audio field assertions flow
  through — had no direct test. Only whole rows were covered, and every committed row
  passes, so the matcher's ability to *reject* was never exercised.

Both are now pinned: **every non-carve-out register entry must have a demonstrated
failing input (31/31)**, and nineteen boundary cases pin the capture matcher.

### Rule 6 — Ordering is decided on event-stream position, never timestamps

**Plain:** "did A happen before B" is answered by their order in the list, not by
comparing clock readings.

**Detail:** tied timestamps under a fake clock read as "in order", so a genuine
violation passed on a fake clock and failed on a ticking one. Fixed to decide on
index. `lab/checks/contracts.py:1523`. Do not regress this.

### Rule 7 — A judge without calibration is not evidence

**Plain:** before you trust an AI to mark work, measure how often it marks correctly.

**Detail:** `lab/judges/registry.py:18` — `require_calibrated()` **raises** unless
TPR ≥ 0.85 and TNR ≥ 0.85. The gate has refused in anger: a judge prompt measured at
**TPR 0.250 (2/8)** was blocked from CI. `DESIGN.md` §8.

### Rule 8 — No latency figure unless the timing gate passed

**Plain:** do not quote a speed measurement until you have proved your stopwatch works.

**Detail:** `lab/voice/adapter.py:15,410,1138` — raises `LatencyUnproven` if a trace's
calibration verdict is not PASS. The gate recovers known delays to within **0.266%**
across 100 ms–2 s, and publishes a **naive control that fails** by a near-constant
~30 ms — worst exactly where a live-coaching budget is tightest (+30.3% at 100 ms).

### Rule 9 — A parse failure is an explicit error, never a silent pass

**Plain:** if the marker's answer is unreadable, record an error. Do not assume pass.

**Detail:** `lab/judges/judge.py:48,218,451` — `strict=True` raises; lenient mode
records a FAIL flagged `parse_error=True` so calibration can count it.
`max_parse_error_rate` defaults to 0.

### Rule 10 — Untestable is a status, not a pass or a fail

**Plain:** if something genuinely cannot be tested, say so — do not hide it and do
not let it inflate a score.

**Detail:** `lab/voice/suite.py:879` — `status` is `runnable`, `blocked` or
`untestable`. The Cantonese row carries `passed=None` and appears in **no pass-rate
denominator**. Its untestability *expires by itself*: it validates only while `yue`
is absent from the synthesisable set.

### Rule 11 — Grade what was heard, not what was sent

**Plain:** when the AI mishears something, the grade must reflect the mishearing —
because that is what happens in real life.

**Detail:** `roleplay/spoken.py` — the trace carries both `text_sent` and
`text_heard`; grading consumes only `text_heard`. Proven by mutation in both
directions: dropping a phrase from `text_heard` removes it from the register ledger,
while replacing `text_sent` entirely leaves grading byte-identical.

### Rule 12 — Score speech unformatted, and report WER twice

**Plain:** a transcript tidied up for humans to read will make a perfect result look
broken.

**Detail:** [`lab/voice/engines/WER_NORMALISATION.md`](../lab/voice/engines/WER_NORMALISATION.md).
A verified round trip transcribed a postcode **perfectly at 0.997 confidence** yet
scores ~50% word error against the synthesis reference, because ElevenLabs normalises
"seven thirty" while Deepgram's `smart_format` renders "07:30". So:
`smart_format=false` for anything scored (`deepgram_stt.py:286`), the display string
kept separately, **raw and normalised WER reported as two named numbers** (measured
0.4344 vs 0.0560 — a factor of 7.8), and **field-level assertions rather than WER**
for digits and postcodes.

### Rule 13 — Aggregate agreement is not agreement

**Plain:** two totals matching does not mean nothing changed. Check the parts.

**Detail:** found twice, independently.
- A failing judge prompt returned an **identical confusion matrix (2/0/6/16) across
  three separate runs**, because its two unstable items sat on opposite sides and
  cancelled.
- The spoken call: `discovery` fell 2→0 and `objection_handling` rose 2→4, so both
  channels totalled **12/20** with identical verdicts and identical ledgers. Only
  `ChannelEffect`'s **per-criterion** comparison surfaced it.

### Rule 14 — Classify a failure before believing it

**Plain:** when a test fails, first ask whether the *test* is wrong.

**Detail:** `DESIGN.md` §6. Every red is classified **product / harness /
invalid-scenario / label-error / variance** before it is reported. This has repeatedly
mattered: adversarial verification once overturned all 22 claimed product bugs, and
**79 of 163 "mismatches" turned out to be label errors**. The harness has also been
caught blaming the product for its own mistake — the simulator appended its hang-up
sentinel to the turn carrying the caller's final answer, denying the agent the turn it
needed, then failing it for not acting.

### Rule 15 — A literal in a check is a check that works once

**Plain:** matching exact words only works until the AI rephrases.

**Detail:** `DESIGN.md` §10. Measured: a promise detector matching literal substrings
had **100% recall against a scripted agent and 1/7 (~14%) against a live model that
paraphrased**. Same defect; the detector went blind. Phrase checks survive only where
the exact wording *is* the requirement — a prescribed regulatory disclosure — and
those are allowed to fail.

### Rule 16 — Never commit a credential, and never print one

**Plain:** keys live in one ignored file and never appear anywhere else.

**Detail:** `.env` is gitignored and mode 600. Docs name environment **variables**,
never values. Verified by sweeping every blob in history, every commit message, and
every historical filename.

---

## 4. The three tiers: how anything gets run

### In plain terms

Three speeds. Use the cheapest one that can answer your question.

| Tier | What it is | Speed | Cost | Run it |
| --- | --- | --- | --- | --- |
| **Replay** | recorded conversations played back | **~90 scenarios/sec** | free | every commit |
| **Live text** | real AI on both sides, real AI grading | ~20 s/conversation | ~$0.10 each | nightly |
| **Audio** | real speech, real recognition | real time | characters + STT | before a release |

The honest sentence, and the most important one here: **replay is blind to prompt
changes.** If you edit a prompt, the recording is stale and only a live run can tell
you what happened. That is exactly what the live tier is for.

### In detail

Selection is by *what changed*:

| You changed | Tier needed | Why |
| --- | --- | --- |
| harness code, a parser, a report | replay | prompts unchanged, recordings valid |
| a prompt or a rubric | **live text** | recordings are stale by definition |
| the speech stack, or before release | audio | only audio exercises recognition |

Live paths are gated by `LAB_LIVE_AGENT`, `LAB_LIVE_CALLER`, `LAB_LIVE_JUDGE`,
`LAB_LIVE_TRAINEE`, `LAB_LIVE_CUSTOMER`, `LAB_LIVE_SCORER`, `LAB_LIVE_TTS`,
`LAB_LIVE_STT`, `LAB_LIVE_SPOKEN`, `LAB_LIVE_TRANSPORT`. Each refuses with a message
naming whichever of flag-or-key is missing.

**Scaling.** Turns *within* a conversation are sequential; conversations are
independent. So 1,000 live scenarios is ~5.5 h sequential, **~33 min at 10 concurrent,
~7 min at 50** — after which your model provider's rate limit, not your code, is the
constraint.

---

## 5. Map of the repository

### `lab/` — the reusable engine (31,541 LOC)

**Plain:** the parts that are not about any particular product. This is the half you
would carry to a different company.

| Path | LOC | What it does |
| --- | --- | --- |
| `clock.py` | 96 | injectable monotonic clocks — why timing here is testable at all |
| `trace/schema.py` | 411 | `Trace`, `EventKind` — the event vocabulary |
| `trace/build.py` | 488 | `TraceBuilder`, clock injected |
| `trace/io.py` | 136 | JSONL codec |
| `checks/contracts.py` | 1,749 | the declarative checks (below) |
| `checks/engine.py` | 376 | runs contracts, tracks vacuity |
| `checks/result.py` | 171 | result types, `CheckStat` |
| `checks/text.py` | 415 | text matching, paraphrase-tolerant |
| `judges/judge.py` | 1,339 | `Judge`, `ReplayJudge`, live provider calls, retry/backoff |
| `judges/calibration.py` | 1,088 | TPR/TNR/precision/recall/F1/kappa/confusion, self-consistency |
| `judges/registry.py` | 387 | `require_calibrated()` — the gate that raises |
| `judges/hallucinated_confirmation/` | 1,319 | the worked v1→v2 calibration study |
| `simulator/persona.py` | 593 | `Persona`, `Goal`, gated facts |
| `simulator/driver.py` | 1,312 | `ScriptedCaller`, `LLMCaller`, anti-loop, leak detection |
| `simulator/passk.py` | 458 | `StabilityVerdict` — STABLE_PASS / STABLE_FAIL / FLAKY |
| `simulator/flake_band.py` | 760 | the measured flake band |
| `voice/calibration.py` | 890 | **the timing gate** + the naive control |
| `voice/wer.py` | 863 | raw + normalised WER, script-aware normalisation |
| `voice/silence.py` | 647 | gap attribution |
| `voice/perturb.py` | 663 | noise at SNR, telephone band, packet loss, speed, pitch |
| `voice/metrics.py` | 544 | latency percentiles |
| `voice/adapter.py` | 1,689 | joins audio to the trace; refuses unproven latency |
| `voice/suite.py` | 1,194 | the 18-row audio tier, `spoken_reference` |
| `voice/interaction.py` | 627 | barge-in and overlap mechanics |
| `voice/engines/elevenlabs_tts.py` | 926 | real TTS, digest cache, spoken-form handling |
| `voice/engines/deepgram_stt.py` | 610 | real STT, `smart_format=false` for scoring |
| `voice/engines/clipcache.py` | 319 | content-addressed clip cache |
| `voice/transport/` | 4,267 | the WebRTC tier — delivery gap, degradation, lifecycle |
| `report/report.py` | 899 | denominator-safe markdown + JSON |
| `report/heatmap.py` | 409 | transition-failure heatmap |
| `report/interop.py` | 416 | export to other eval ecosystems |
| `cli.py` | 2,334 | `evallab` — one entry point |

**The contracts**, which are the deterministic half of the grading:

| Contract | Question it answers |
| --- | --- |
| `ToolContract` | were the right actions taken, the right number of times, in the right order? |
| `PromiseContract` | the agent *said* it did X — did the action actually happen? |
| `NoReAskContract` | did it ask again for something already given? |
| `FieldPropagationContract` | did a captured value survive a handoff? |
| `NoProgressContract` | is the conversation going in circles? |
| `PhraseContract` | was required wording actually said? |

### `roleplay/` — the advisory domain (15,817 LOC)

**Plain:** a trainee financial adviser is graded on a practice sales call, under the
rules of four different countries.

| Path | LOC | What it does |
| --- | --- | --- |
| `scorer.py` | 505 | the deterministic rubric scorer (**holds 3 seeded defects**) |
| `livescorer.py` | 669 | the real LLM scorer, gated on `LAB_LIVE_SCORER` |
| `live.py` | 1,661 | the multi-turn loop with a model in both seats |
| `spoken.py` | 1,764 | that loop through real TTS and STT, graded on what was heard |
| `regime_eval.py` | 2,732 | reads the cited registers, computes per-regime verdicts |
| `scorecard.py` | 1,724 | the 28-KPI behavioural scorecard, validated at import |
| `advisory.py` | 313 | register loading and resolution |
| `register.py` | 462 | disclosure register, approved wording, keyword shadow |
| `contracts.py` | 537 | domain contracts incl. feedback groundedness |
| `calibration.py` | 304 | scorer calibration |
| `consistency.py` | 325 | score stability across identical runs |
| `corpus.py` | 1,080 | corpus loading and validation |
| `persona.py` | 598 | customer personas with hidden motivations |
| `labels.py` | 609 | the hand-labelled ground truth |
| `scorer_study/` | 1,353 | the scorer-as-judge study |

### `tablemate/` — the restaurant domain (5,091 LOC)

**Plain:** a booking assistant with bugs deliberately hidden in it, to check the
harness can find them.

`agents.py` (1,053) the multi-agent graph · `runtime.py` (1,648) scripted and live
backends · `store.py` (473) state · `tools.py` (382) the five tools ·
`understanding.py` (695) slot extraction · `SEEDED_BUGS.md` the answer key.

### `ragcheck/` — retrieval and groundedness (3,108 LOC)

**Plain:** when an AI answers from a document, two separate things can go wrong: it
found the wrong document, or it found the right one and made something up anyway.

`retrieval.py` (384) recall@k, MRR, nDCG · `judges.py` (267) groundedness ·
`claims.py` (105) claim decomposition · `offline.py` (308) a keyless lexical stand-in,
**labelled as a stand-in on every line it prints** · `calibration.py` (212) ·
`report.py` (427).

### `scenarios/` — 194 YAML files

`advisory` 31 · `roleplay` 78 · `audio` 21 · `edge` 20 · `happy` 15 ·
`adversarial` 12 · `personas` 9 · `voice` 8. `loader.py` (2,340) validates every row
against its schema and **rejects a row that declares no contract** — a scenario that
cannot fail is not a scenario.

### Other

`error_analysis/pareto.py` (281) — traces read by hand, coded, counted.
`scripts/` (2,539) — fixture recorders. `tests/` (28,307 LOC, 57 files, **1,976
tests**). `fixtures/` — every committed recording. `docs/` — this wiki plus twelve
other documents.

---

## 6. The processes

Everything runs through `make`. Targets that spend money say so.

### Free, no keys, run any time

```bash
make test              # 1,976 tests
make validate          # corpus against its schema, with coverage
make calibrate         # the timing and judge gates; non-zero if either fails
make demo              # the restaurant case study end to end
make roleplay-demo     # the advisory pack: contracts, consistency, scorer calibration
make advisory-verdicts # compute the 18 rows' regime verdicts from the registers
make spoken-replay     # replay the committed spoken call and re-grade it
make ragcheck          # retrieval + groundedness
make audio-suite       # the 18-row audio tier, offline
make transport-report  # recompute the WebRTC tier from recordings
make errors            # recount hand-assigned failure modes
```

### Spends money — needs flags and keys

```bash
make live-record         # a new live run from a provider
make audio-suite-record  # re-record the audio tier (LAB_LIVE_TTS + LAB_LIVE_STT)
make spoken-record       # a new spoken call (SPENDS ElevenLabs characters)
make transport-record    # new live WebRTC sessions
make audio-suite-plan    # print what re-recording WOULD cost, and spend nothing
```

`audio-suite-plan` exists because a cost you can see before paying is worth having.

### CI

`.github/workflows/ci.yml` runs, in order: lint → tests → corpus validation →
**calibration gates with `--ci`** → *calibration artefacts unchanged* → replay run →
**the run reproduces the committed report byte for byte** → error analysis agrees.

The byte-for-byte steps are the interesting ones: they fail if a committed result
drifts from what the code now produces, which catches silent changes that a passing
test suite would miss.

---

## 7. The domains

### Advisory sales coaching — the primary domain

**Plain:** a trainee practises selling a financial product to a simulated customer.
Afterwards they get a score and feedback, and that score decides whether they are
certified to sell for real. Four countries' rulebooks are in play, and they disagree
with each other.

**Detail:** the rubric grades discovery, objection handling, mandatory disclosure,
no-unlicensed-advice and closing (20 points, pass at 14), with an **outright fail** on
a missing registered disclosure or a personal recommendation regardless of total.

Four regulators — **MAS** (Singapore), **FCA COBS** (UK), **Reg BI** (US), **SFC/IA**
(Hong Kong) — are held as machine-readable registers under
`scenarios/advisory/registers/`, 36 entries, each carrying `kind`, `timing`, a
paragraph-level citation, and its research section.

`kind` drives genuinely different logic — this is why it is a register and not a
keyword list:

| `kind` | Logic |
| --- | --- |
| `verbatim` | only the prescribed wording satisfies it; a paraphrase **misses** |
| `prescribed-unit` | substance *plus* a specific figure (14 days vs 30 days) |
| `substance` | a paraphrase conveying the meaning passes |
| `prohibition` | the *presence* of something fails |
| `gate` | failure fails the session regardless of score |
| `not-required` | this regime does **not** require it — an omission must **pass** |

`not-required` is load-bearing: it stops a cross-market checker inventing requirements
where none exist. Reclassify each carve-out as a substance requirement and **3 of 5
flip the passing regime to fail**.

### Restaurant booking — the portability proof

**Plain:** kept deliberately, and its job is to show the same engine works on a
completely different problem.

**Detail:** three defects seeded and documented only in `tablemate/SEEDED_BUGS.md`.
Under a live model they become **probabilistic** rather than deterministic — measured
at 83% / 25% / 100% — which is itself the finding.

### Knowledge retrieval

**Plain:** it separates "did it find the right page" from "is the answer actually in
that page". Those fail independently and a single number hides it.

**Detail:** the worked example the CLI opens with: retrieval perfect at recall
**1.000 (1/1)**, groundedness **0.500 (1/2)** — the answer invents a figure that
appears nowhere in the retrieved passage.

---

## 8. What it found

**Plain:** the point of a testing tool is what it catches. Here is the list.

**In the systems under test:**

- **A grader reluctant to fail anybody.** Specificity 0.947 (36/38), recall
  **0.281 (9/32)** — it catches 9 of 32 sessions that should fail, and the misses
  concentrate in compliance and locale. In a product that certifies people, that is
  the worst direction to be wrong in.
- **A judge missing three of four real failures.** TPR 0.250 (2/8) — and the gate
  refused it. A revised prompt reached 1.000/1.000.
- **A confidently wrong postcode.** At −5 dB the recogniser returns `SW1A 1AF`; at
  −10 dB it returns nothing. **The milder line is the dangerous one** — a plausible
  wrong address delivered with confidence. Pass/fail loses that entirely.
- **Confidence 1.000 on a transcript with a clause missing.** The Singapore
  code-switch row lost the Mandarin clause completely and reported full confidence —
  worse than a low score, because a downstream consumer would promote it.
- **Emergent defects a scripted corpus could not reach**: a phantom promise about a
  severe allergy, a double booking, an agent answering a capacity question with **zero
  tool calls, three times out of three**.

**In its own instruments — the more valuable half:**

- **A compliance check that could pass anything**, including "this is risk-free".
- **A capture matcher that had never been shown rejecting.**
- **A harness that blamed the product for its own bug.**
- **A detector that went blind to paraphrase** — 100% recall scripted, 1/7 live.
- **A metric that reported 43% error on perfect recognition.**
- **A silent scoring failure masked by compensating errors** — `discovery` 2→0 and
  `objection_handling` 2→4, both totalling 12/20.

**Three times a control arm reversed a conclusion**, once against a claim already
written into the documentation.

---

## 9. Limitations, stated plainly

Stated here rather than buried, because a document's authority comes from what it
admits.

- **The corpus is synthetic.** The disclosure registers are reconstructed from public
  regulatory sources, not from any firm's compliance system. The rubric is a
  reasonable reconstruction, not anyone's real scorecard.
- **The 16/18 compliance agreement is in-sample.** The probes were written with those
  eighteen transcripts in view. The CLI says so on its own second line. A held-out
  paraphrase set is the honest next step.
- **The phrase lists are short.** "three per cent of the sum you invest" returns
  *missed* on a correct disclosure. Recall against unseen wording is unmeasured.
- **The spoken call is n = 1.** It demonstrates the pipeline is real. It supports no
  rate.
- **Barge-in is declared, not runnable.** The interruption events exist in the schema
  with no emitter; the rows report NOT YET RUNNABLE rather than passing silently.
- **Cantonese is untestable.** No TTS vendor synthesises it. Recorded as a finding,
  with the remediation named.
- **Accent variation is limited** — Voice Library voices are not reachable on the
  free API tier.
- **Only STT latency in the committed spoken artifact is a real measurement.** The
  other two clocks are replay lookups, and the report refuses to quote them.
- **It is Python.** Porting to another runtime means rewriting the adapters; the trace
  schema, contracts and calibration logic are not language-specific.

---

## 10. How to extend it

**Add a scenario:** [`docs/adding_a_scenario.md`](adding_a_scenario.md). A row must
declare at least one contract — `scenarios/loader.py` rejects one that cannot fail.

**Add a check:** subclass `Contract` in `lab/checks/contracts.py`. Before you commit
it, construct the bad behaviour and confirm it **fails**. Rule 5.

**Add a judge:** implement against `lab/judges/judge.py`, build a labelled set, run
`calibrate()`, and register it. It cannot gate until it clears the thresholds.

**Add a regulator:** a new YAML under `scenarios/advisory/registers/` with `kind`,
`timing` and a citation per entry. `regime_eval.py` picks it up; each entry needs a
probe, and a test asserts every entry has one.

**Add a language:** check both vendors support it *and* whether the pair is in
Deepgram's ten-language code-switching set — they are not the same question. See the
vendor capability matrix in [`docs/AUDIO_SUITE.md`](AUDIO_SUITE.md).

---

## 11. Glossary

| Term | Plain meaning |
| --- | --- |
| **Trace** | the ordered list of everything that happened in one conversation |
| **Contract** | a deterministic check — no AI involved, same answer every time |
| **Judge** | a check where an AI grades the output, so it needs calibrating |
| **Calibration** | measuring how often the judge agrees with a human |
| **TPR / recall** | of the things that should fail, how many did it catch |
| **TNR / specificity** | of the things that should pass, how many did it let through |
| **Kappa** | agreement, corrected for agreeing by luck |
| **pass^k** | run the same scenario k times; flaky is its own verdict, not a pass |
| **Flake band** | how much the same test varies run to run |
| **Vacuous** | the check had nothing to look at — not a pass |
| **WER** | word error rate — how much of the speech was misheard |
| **Code-switching** | changing language mid-sentence, normal in many markets |
| **Register** | the machine-readable list of what a regulator requires |
| **Replay / fixture** | a recorded run, played back for free with no AI calls |
| **Delivery gap** | the difference between the AI finishing and the human hearing it |

---

*Every figure in this wiki was re-derived from the committed artefacts. Where a number
could not be reproduced, it was removed rather than rounded.*
