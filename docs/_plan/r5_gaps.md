# R5 — Gap audit: the wiki and the repository

**Status:** research and planning only. Nothing was built, nothing was fixed. No `.py`,
`.yaml`, fixture or `Makefile` was touched. The only file this pass owns is this one.

**Method.** Every claim below was produced by a command run against this checkout at
`032eab7` (40 commits, clean tree) or by reading a committed artefact. Commands are in
[Appendix — reproduction](#appendix--reproduction). House rules apply: every rate carries
its denominator, and anything not sourced is labelled **ASSUMPTION**. There is no third
category.

**Verified starting state.** `pytest -q` → `1976 passed, 4 skipped in 53.19s`.
`git status --short` → empty apart from this new `docs/_plan/` directory.
162 tracked `.py` files, 194 scenario `.yaml`, 581 files under `fixtures/`.
`lab/` 31,541 LOC, `roleplay/` 15,817, `tablemate/` 5,091, `tests/` 28,307 — all four
match the wiki exactly.

**Read this first, because it changes the tone of everything below.** The wiki is far
better than a gap list makes it sound. 527 headings, 72 Mermaid diagrams, **zero broken
internal anchors** out of 254 distinct internal links. Every headline number I re-derived
reproduced. All 30 `make` targets are documented, including the internal `python-ok`
guard. The layering claim in §2.8 — no domain package importable from `lab/`, one named
exception — is *true today*, verified by import. So the gaps below are gaps in a good
document, not a bad one. They are worth fixing precisely because so little else is wrong.

---

## Summary — the eleven things worth deciding on

| # | Gap | Where | Severity |
|---|---|---|---|
| A1 | 20 of 54 test modules are named nowhere in the wiki | §8.5.10 | medium |
| A2 | §5.4 (what CI asserts) is 18 lines and omits what CI does *not* cover | §5.4 | **high** |
| A3 | No debugging path anywhere — a reader cannot diagnose a red | §11 | **high** |
| A4 | ~3,400 lines of committed docs the wiki never mentions | `docs/` | medium |
| A5 | Four sections carry the load and have no diagram | §4, §5.3, §7.3, §7.8 | low |
| A6 | "In plain terms" blocks that contain exception class names | §2.4, §2.5 | medium |
| B1 | Schema says barge-in events are never emitted. They are. | `lab/trace/schema.py:103` | **high** |
| B2 | Adapter docstring: "no metric claims to measure barge-in". One does. | `lab/voice/adapter.py:63` | **high** |
| B3 | A second Rule-3 defect in a committed artefact, k-insensitive | `lab/cli.py:1716` | **high** |
| B4 | `docs/REAL_STACK_ARCHITECTURE.md` — 1,109 lines, orphan, superseded | `docs/` | medium |
| B5 | `docs/PLAYWRIGHT_NOTES.md` — off-scope, clean-room breach, orphan | `docs/` | **high** |

---

# Part A — the wiki

## A1. Which files are not documented at all

I checked all 162 tracked `.py` files against `docs/WIKI.md`, matching first on full path,
then on the last two path components, then on bare basename (the wiki's `######` headings
use bare basenames inside a package section, so a naive path grep massively overstates the
gap).

**Result: 142 of 162 are named. The 20 that are not are all test modules.**

`tests/` holds 57 `.py` files: 54 `test_*.py` modules plus `audio_doubles.py`,
`roleplay_fixtures.py` and `__init__.py`. §8.5.10 and Appendix A.4 between them name **34**
of the 54. These **20 appear nowhere in 14,802 lines**:

```
tests/test_advisory_corpus.py         tests/test_report_interop.py
tests/test_judges.py                  tests/test_report_render.py
tests/test_judges_calibration.py      tests/test_roleplay_checks.py
tests/test_judges_iteration_story.py  tests/test_roleplay_evaluation.py
tests/test_judges_live.py             tests/test_roleplay_live_scorer.py
tests/test_judges_registry.py         tests/test_roleplay_sut.py
tests/test_live_run.py                tests/test_simulator_driver.py
tests/test_report_heatmap.py          tests/test_simulator_passk.py
                                      tests/test_simulator_persona.py
                                      tests/test_tablemate_bugs.py
                                      tests/test_tablemate_tools.py
                                      tests/test_tablemate_understanding.py
```

Note the shape of the omission — it is not random. **Every judge test, every simulator
test and every report test is missing.** §8.2 spends 2,018 lines on
`lab/judges/`, `lab/simulator/` and `lab/report/` and never once says which tests hold
that code still. §8.5.10 gives the voice tier a 13-row table and the ragcheck tier a
5-row table; judges, simulator and report get nothing equivalent. `tests/test_judges.py`,
`test_judges_calibration.py` and `test_judges_registry.py` protect the single most
load-bearing claim in the repository — that an uncalibrated judge cannot gate — and a
reader is never told where that protection lives.

**Two non-test omissions worth naming, because both files carry an argument:**

- **`scenarios/audio/__init__.py` (45 lines)** — has no entry of its own. §8.5.7 documents
  `scenarios/loader.py` (2,340 lines) and `scenarios/__init__.py` (19 lines) and skips
  this one. Its docstring holds "THE ADMISSION RULE, WHICH IS THE ONLY REASON THIS TIER IS
  SMALL" and states that the directory is deliberately excluded from `SUITES` so that
  "no audio result is averaged into a text denominator". That is a Rule-3 mechanism and it
  is documented only inside the file.
- **`fixtures/__init__.py` (8 lines)** — not documented, and its docstring is stale; see
  [B6](#b6-stale-docstrings-that-describe-an-older-tree).

**Suggested fix (owner decides).** One table in §8.5.10, thirteen rows, same shape as the
ragcheck table already there: file → what it pins. Roughly 20 lines of writing. Cheapest
high-value edit in this document.

## A2. Which sections are thin relative to their importance

Measured by lines between headings (script in the appendix).

| Section | Lines | Why that is thin |
|---|---|---|
| **§5.4 What CI actually asserts** | **18** | The section on which every "this is enforced, not asserted" claim in the document rests. See below. |
| §9.3 Findings not fixed | 11 | Pure pointer to Appendix B. Fine as a stub, but it is the only place §9 admits the doc pass found things, and it says "Nine things" without listing even their categories. |
| §10.2 The scoring model | 17 | §7 is 1,928 lines. Its limitations get 17. |
| §10.1 The corpus and the domain model | 20 | Same imbalance. |
| §11 How to extend it | 53 | Eight "add a X" paragraphs, each 5–7 lines, for a 31.5k-LOC engine. See [A3](#a3-what-a-reader-still-cannot-do). |
| §12 Glossary | 63 (4 tables) | Adequate, but see [B1](#b1-the-schema-says-the-barge-in-events-are-never-emitted-they-are) — one entry is now factually wrong. |

**§5.4 is the one that matters.** It correctly lists the eight CI steps in order. What it
never says is **what CI does not run**, and the omissions are large:

Verified against `.github/workflows/ci.yml`, CI runs `pytest -q`, `evallab validate
--coverage`, `evallab calibrate --ci`, `evallab run --replay --ci`, two
`git diff --exit-code` gates, and `error_analysis.pareto --check`. It does **not** run:

- `make roleplay-demo`, `make roleplay-validate`, `make advisory-verdicts`
- `make ragcheck`
- `make audio-suite`, `make audio-check`, `make audio-suite-evidence`
- `make transport-report`
- `make spoken-replay`
- `make live-replay`, `make live-score`

Most of that code *is* covered by `pytest`, so this is not a hole in the safety net — but
it is a hole in the document, because §5.4's own framing is "each one makes the next
believable" and a reader is entitled to know that the byte-for-byte reproduction gate
covers `fixtures/replay_run/` and the calibration artefacts, and nothing else.

Sharper still: **`evallab validate --coverage` — the "corpus validates against its schema"
step — sees 55 scenarios, not 194.** Verified: `evallab validate --coverage` prints
`55/55 scenario files loaded`. The 70-row advisory corpus is validated by
`make roleplay-validate` (`python -m roleplay.corpus --coverage --list`), which CI does not
call; it reaches CI only indirectly, through `tests/test_advisory_corpus.py`. The wiki
leads with the advisory domain as the flagship and never mentions that its corpus takes a
different route into CI than the booking one.

## A3. What a reader still cannot do

I took the four capabilities the brief names and traced each through the document.

**Add a scenario — yes.** §11 points at `docs/adding_a_scenario.md` (148 lines) and §8.5.7
explains the loader's rejection rule. This one works.

**Add a contract — partly.** §11 says: subclass `Contract`, one `check()` method, construct
the bad behaviour and confirm it fails, return `applicable=False` when there is nothing to
assert on. That is the *policy*, and it is right. What is missing is the *mechanics*: how a
new contract becomes reachable from a YAML row. §8.5.7 documents `scenarios/loader.py` as
the thing that compiles YAML into `lab.checks` objects, and §8.4.9 says `roleplay/corpus.py`
"shares no code with `scenarios/loader.py` and shares every rule" — so a seventh contract
has to be wired into **two** independent loaders, and neither §11 nor §8.1.3.4 says so. A
reader following §11 literally writes a contract nothing can invoke.

**Debug a failing check — no.** This is the real gap. The word "debug" appears once in
14,802 lines, in an aside at §8.1.3.2 line 5573 ("a small kindness that saves a debugging
session"). There is no section, no subsection and no paragraph on *what to do when a row
goes red*. Everything in the document is add-shaped or explain-shaped, never
diagnose-shaped. The material for it already exists and is scattered:

- `Evidence` carries the quote a contract failed on (§8.1.3.1)
- `evallab replay <one trace>` re-checks a single committed trace with no agent (docs/cli.md:134)
- `evallab run --scenario <id> --transcript -k 1` prints the conversation (docs/cli.md:23)
- vacuity vs failure is the first distinction to make (§8.1.3.3)
- Rule 14 says classify product / harness / invalid-scenario / label-error / variance before believing it
- `make reference` prints its own diff, which is how you tell "fixed" from "stopped applying"

That is a complete debugging playbook already written, in six places, with no page that
assembles it. **ASSUMPTION:** roughly 60–80 lines as a new §11.1 or a §13, no new code.

**Explain a number to an interviewer — mostly yes, with one hole.** §6.9 is six worked
interviewer questions, §8.2.12 is an interview drill, and Appendix A pins every figure to
a command. The hole is that a reader cannot answer *"what is your test coverage?"* — see
[B7](#b7-things-a-reviewer-asks-immediately-and-finds-no-answer-to).

## A4. Documents the wiki never mentions

The wiki's opening line is "Everything in this repository: what each part is, why it
exists…". It never mentions these committed documents:

| File | Lines | Referenced by |
|---|---|---|
| `docs/REAL_STACK_ARCHITECTURE.md` | 1,109 | **nothing in the repository** |
| `docs/ADVISORY_TEST_STRATEGY.md` | 1,081 | `docs/SCORECARD.md`, `docs/ADVISORY_DEMO.md` only |
| `docs/ADVISORY_DEMO.md` | 606 | `Makefile`, `roleplay/SEEDED_DEFECTS.md` |
| `docs/AUDIO_TRANSPORT.md` | 408 | code docstrings + `pyproject.toml`; **not the wiki** |
| `docs/PLAYWRIGHT_NOTES.md` | 216 | **nothing in the repository** |
| `docs/cli.md` | 186 | `README.md` only; **not the wiki** |

Total: **3,606 lines of committed documentation the 14,802-line "everything" document does
not point at.** Two are structural problems in their own right and are in Part B
([B4](#b4-realstackarchitecturemd--1109-lines-superseded-orphan),
[B5](#b5-playwrightnotesmd--off-scope-and-a-clean-room-breach)). The other four are just
missing links:

- `docs/AUDIO_TRANSPORT.md` is the standalone write-up of the WebRTC tier. §8.3.12 is 1,360
  lines into §8.3 and never sends the reader there — even though `lab/trace/schema.py`'s own
  docstring does ("see `docs/AUDIO_TRANSPORT.md`").
- `docs/cli.md` is the full command reference. §8.1.4 documents `lab/cli.py` over ~200 lines
  and never mentions that a command reference exists.
- `docs/ADVISORY_TEST_STRATEGY.md` is 1,081 lines on the advisory surface — the flagship
  domain — and §8.4 does not link it.

**Also: `docs/cli.md:21` says "all 47 text rows".** `evallab validate` reports 55 scenario
files (15 happy + 20 edge + 12 adversarial + 8 voice). 47 is right for what a run *drives*
(55 minus the 8 voice rows), but the two numbers appear in different documents with no
reconciliation, and 55/47/194 all circulate. Worth one sentence somewhere.

## A5. Are the diagrams covering the right things?

72 Mermaid blocks, well placed in §2 (12 across eight subsections) and §8 (40). The
distribution is sound. Four load-bearing sections have **no diagram and would benefit**:

- **§4 The sixteen golden rules — 219 lines, 0 diagrams.** The rules are cross-cited
  constantly ("rule 5", "rule 14", "Rule 3") and there is no single picture of them. The
  obvious one: a 2×2 or a grouping showing which rules are about *the instrument* (5, 7, 8,
  13, 14), which about *reporting* (3, 4, 9, 10, 12), which about *the trace* (2, 6, 11),
  which about *hygiene* (1, 15, 16).
- **§7.3 The rubric — 259 lines, 0 diagrams.** The five criteria, the 20-point total, the
  14-point pass line and the outright-fail clauses that bypass the arithmetic entirely.
  That last relationship — a verdict that is *not a function of the total* — is exactly what
  a picture makes obvious and prose does not. §8.4.9 says the scorer's own
  `self_consistent` flag exists to report when the two disagree; there is no diagram of the
  two paths.
- **§7.8 Gaming — 181 lines, 0 diagrams.** The keyword-shadow control arm measured in both
  directions is a comparison, and comparisons draw well.
- **§5.3 Every command, and what it costs — 58 lines, 0 diagrams.** Three tiers × cost ×
  gate is a natural matrix; it is currently three prose tables.

**Nothing has a diagram that should not.** No cuts recommended.

## A6. Is the plain-terms half genuinely plain?

Mostly yes. I extracted all 38 `#### In plain terms` blocks and scanned each for code
identifiers (`snake_case`, `CamelCase`, `SomethingError`, `func()`). The prose is clean.
**The diagrams inside those blocks are not**, and the promise in the preamble is explicit:
"no jargon, safe to read if you have never opened the code".

**Worst offender — §2.4, line 429.** The prose is genuinely excellent ("Grade what was sent
and you are measuring the script. Grade what was heard and you are measuring the
product."). Then the sequence diagram inside the same block hands the jargon-free reader
ten identifiers with no gloss:

> `text_sent` · `spoken_form` · `text_heard` · `clip_key` · `audio_emitted { text_sent, clip_key }` · `transcript_in { text, text_sent, confidence }` · `caller_utterance { text, text_sent }` · `ChannelEffect`

and the caption underneath uses three of them again. A reader who has never opened the code
cannot tell `text` from `text_sent` from `text_heard` at this point in the document — the
glossary entry that distinguishes them is 13,600 lines later.

**Second offender — §2.5, line 499.** Same pattern. The prose is plain and good. The
diagram inside it says:

> `require_calibrated()` · `TPR ≥ 0.85 AND TNR ≥ 0.85` · `n ≥ 10, no parse errors` · **`JudgeBelowThresholdError`**

An exception class name, inside a block promised as jargon-free, 6,000 lines before the
file that raises it is described.

**Third — §2.3, line 310.** `deepgram_stt` / `elevenlabs_tts` as node labels. Milder,
because the vendor names alongside make them guessable.

**The fix is cheap and does not lose anything.** These are the same diagrams; they just need
plain node labels with the identifier in a caption below rather than inside the box —
"what the harness said" / "what the agent heard" instead of `text_sent` / `text_heard`,
with the field names named once underneath. The **In detail** half already re-draws the
same material with the real names; that is where the identifiers belong.

## A7. Where it explains WHAT but not WHY

The document is unusually strong here — §8's stated contract is that item 3 of every file
entry is "why it exists, or the tricky part… **This is the part worth reading twice**", and
it delivers. Four places where it does not:

1. **§5.4 — WHAT CI runs, not WHY those eight and not others.** No sentence explains why the
   audio tier, the transport tier and the advisory pack are outside the gated path. There
   *is* a good answer in the code — `Makefile:126` says of the transport tier, "A network
   test that blocks a merge trains people to bypass the gate, so the tier reports and the
   offline suite gates." That is exactly the missing why, and it is in the Makefile, not the
   wiki.
2. **§11 — WHAT to do to extend, not WHY the steps are ordered that way.** "Build a labelled
   set of at least ten items with both classes represented" — why ten, why both classes?
   §8.2.3 has the answer (a one-class set makes TPR or TNR undefined, and the registry's
   `n ≥ 10` is a floor on the confusion matrix being meaningful at all). §11 does not link it.
3. **§10 — WHAT the limitations are, not WHY they were accepted.** Each of the ~20 bullets
   names a mechanism section, which is good, but none says what was traded for it. "The
   spoken call is n = 1" — why one and not five? (The answer is cost, and §8.5.9 has the
   character budget.) The limitations read as apologies rather than as decisions.
4. **§12 glossary — definitions with no rationale.** Fine for most entries. For **Vacuous**,
   **Untestable** and **Undecidable** — three of the document's genuinely original ideas —
   the entries define the words and never say why three are needed rather than one.

---

# Part B — the repository

## B1. The schema says the barge-in events are never emitted. They are.

**Confirmed, and it is the clearest documentation defect in the tree.**

`lab/trace/schema.py`, lines 91–103, in the module docstring:

> ```
> DECLARED BUT NOT EMITTED IN v1 — PLANNED FOR v2
> ...
>     Nothing in v1 emits, consumes, or asserts on them.
> ```

All three clauses are false as of this commit:

- **Emits:** `lab/voice/interaction.py:512 emit_barge_in()` writes both
  `interruption_started` and `interruption_acknowledged` through `TraceBuilder.emit`.
- **Consumes:** `lab/voice/interaction.py:594 barge_in_report()` reads them back off a
  trace and pairs them by event-stream position.
- **Asserts on:** `tests/test_voice_interaction.py` (lines 419, 437, 465, 470, 504) and
  `tests/test_audio_adapter.py:180`.

`interaction.py`'s own docstring (lines 48–65, "REFERENCE BUG: THE INTERRUPTION EVENTS
NOBODY USED") describes the change honestly and explains why. Nothing propagated the change
back to `schema.py`, and the wiki inherited the stale text in **four places**:

| Wiki | Claim | Status |
|---|---|---|
| L994 (§2.8) | "2 more that are declared but have no emitter yet" | stale |
| L5140–5146 (§8.1.2.1) | "declared and never emitted… nothing else touches them" | stale |
| L13956 (§10.3) | "The two interruption events exist in the schema with no emitter" | stale |
| L14069 (§12) | "plus 2 reserved for barge-in and not yet emitted" | stale |
| L14109 (§12) | "Barge-in … Declared in the schema, not measurable by a half-duplex adapter" | stale |

Against L9020–9029 (§8.3.6), which is **correct** and says the module emits them and reports
`yield_ms = 240.0`. The document contradicts itself on a schema fact.

`INTERVIEW_NOTES.md` carries the same stale line: *"Barge-in is declared in the schema and
not implemented — that is stated everywhere the schema is documented."*

**The precise, defensible statement** (which nothing currently makes): *the events have an
emitter and a reader, both tested; no adapter or committed run calls the emitter, so no
committed trace contains one.* Verified: `emit_barge_in` is imported only by
`lab/voice/__init__.py` (re-export) and `tests/test_voice_interaction.py`.
`lab/voice/suite.py:88` imports `barge_in` but not `emit_barge_in`, and
`grep -rl interruption_started fixtures reports` matches two files, both of which contain
it only as descriptive text in a `note` field, not as a trace event.

**Related, and separately actionable:** `PAYLOAD_KEYS` in `schema.py` has 16 entries and
**no entry for either interruption kind**. So the two kinds that are now emitted carry
payload keys (`turn`, `agent_started_s`, `agent_would_end_s`, `overlap_s`, `yielded`) that
the schema's own payload contract does not describe. Emitted-but-unschema'd.

## B2. The adapter docstring makes a claim the repo no longer honours

`lab/voice/adapter.py`, lines 61–64:

> "`interruption_started` and `interruption_acknowledged` stay reserved and unemitted (see
> `lab.trace.schema`), **no metric in this repo claims to measure barge-in**, and a v2 duplex
> adapter can emit them without a schema change."

`lab/voice/interaction.py` defines `BargeInReport` with an `interruptions` / `yields` /
`yield_rate` triple and a `BargeInOutcome.yield_ms`; `lab/voice/suite.py:1103` runs it on the
committed row `audio-barge-in-agent-yields`, which passes with
`"barge-in at 1.200s: agent yielded after 240ms, 0.240s of overlap"` in
`fixtures/audio/cloud/audio_suite_evidence.json`. A metric in this repo does claim to
measure barge-in.

`lab/voice/metrics.py:52` carries a milder version of the same staleness: barge-in latency
"is deliberately absent: it needs the `interruption_*` events". True of `metrics.py`; no
longer true of the repository.

## B3. A second Rule-3 defect, same class as the known one, and it is k-insensitive

**The known one, confirmed.** `lab/voice/adapter.py:1385`, inside
`SilentCorrectionReport.describe()`:

> `"reconciliation could attribute 31.3%, because production never knows what was really said"`

A hardcoded literal, no denominator, no derivation anywhere in the tree. It reaches
`fixtures/audio/cloud/audio_suite_report.json:432` (committed) and `docs/AUDIO_SUITE.md:1135`.
The irony is documented: the `per_hundred_turns` property **eleven lines above it** says
*"A naked percentage is a defect in this repo."* Appendix B of the wiki already records
this one, correctly.

*One extra detail the wiki does not note:* the code says **"could attribute** 31.3%" and
`docs/AUDIO_SUITE.md:1135` says **"attributed** 31.3%". Modal in one place, factual in the
other, for the same undocumented number.

**The new one, same class, arguably worse — `lab/cli.py:1716`.** Inside `_run_caveats`, the
k-note is interpolated with the run's real k (`f"k={args.repeats} with a live rig…"`) and
then continues with a hardcoded statistic that does not track k:

> "three passes out of three put the 95% Wilson lower bound on the pass rate at 0.44, so a
> row that came back STABLE_PASS is consistent with a real-world failure rate above one call
> in two."

0.44 is correct for 3/3 (Wilson lower bound 0.4385, computed). It is wrong for any other k.
**Reproduced:** running the CLI at `-k 5` emits, into `run_report.json`:

> `"k=5 with a live rig … three passes out of three put the 95% Wilson lower bound on the pass rate at 0.44"`

— a self-contradicting sentence in a machine-written artefact. The correct value for 5/5 is
0.57. This is strictly worse than the 31.3% case, because the surrounding sentence *is*
parameterised, so the artefact tells the reader two different things about the same run.
Both currently-committed reports run at k=3 so both are accidentally correct
(`fixtures/live_full/run_report.json:525`, `fixtures/live_full/run_report.md:297`,
`fixtures/live_full/README.md:51`).

**A third instance, milder — `lab/cli.py:1877`.** The judge-stage caveat hardcodes
"24 hand-labelled items whose rates are 8/8 and 16/16 … as low as 0.68 and 0.81". All four
figures check out (Wilson: 8/8 → 0.6756, 16/16 → 0.8064). This one *does* carry its
denominators, so Rule 3 holds; it breaks **Rule 15** ("a literal in a check is a check that
works once") instead — change the calibration set and the artefact lies with no test failing.

**Fourth, for completeness — `lab/voice/adapter.py:1383`,** in the same f-string as the
31.3%: `{(attributed or 0) * 100:.1f}% attributable`. The value *is* computed
(`attributed_fraction`), but the two counts it comes from — `len(corrections)` and
`unattributable` — are not printed beside it. A percentage whose denominator exists in the
object and is not rendered.

**Everything else I checked in this class is clean.** I grepped every percentage-bearing
string literal in `lab/`, `roleplay/`, `tablemate/`, `ragcheck/`, `error_analysis/` and
`scripts/`. Every other one either prints `n/N (x%)` (e.g.
`lab/voice/metrics.py:377`, `lab/voice/transport/measure.py:565,628,786`) or is a threshold
constant being explained, not a measurement.

## B4. `REAL_STACK_ARCHITECTURE.md` — 1,109 lines, superseded, orphan

`grep -rl REAL_STACK_ARCHITECTURE` over every `.md`, `.py`, `.toml` and the `Makefile`
returns **nothing**. Not the wiki, not the README, not a docstring.

Its header reads:

> **Status:** proposal, for approval before any code is written.
> **Scope:** how `tablemate-evals` moves from a fully simulated harness to one that drives a
> real voice stack…

The repository has since built all of it: real Deepgram and ElevenLabs engines
(`lab/voice/engines/deepgram_stt.py`, `elevenlabs_tts.py`), a WebRTC transport tier
(`lab/voice/transport/`, 4,267 LOC, three committed live sessions), and a spoken-call
runner (`roleplay/spoken.py`). 78 KB of "before any code is written" describing, in the
future tense, things the tree already ships.

**Reviewer's first impression risk.** Anyone opening `docs/` sorted by size sees a 78 KB
proposal and a 800 KB wiki. If they open the proposal first they will read a plan for a
system that exists, and every subsequent claim gets discounted.

**Options (owner decides, do not act on this without a decision):** delete it; or move it to
`docs/_archive/` with a two-line header saying which commit shipped it; or convert it into a
"how we got here" section the wiki links from §8.3.

## B5. `PLAYWRIGHT_NOTES.md` — off-scope, and a clean-room breach

216 lines, referenced by nothing in the repository.

It is not a project document. It is personal interview-preparation material: it discusses
the author's CV, positions them against a job description, and — this is the problem —
**describes a system built at the author's current employer**, including its scale, its
integration count and its internal metrics, in a file that is committed to this repository
and would ship with any clone or publish.

This repository's stated clean-room rule is that no deliverable names the owner's current
employer or its products. The wiki is clean. **This file is not**, and it sits two
directories away from it.

It also breaches the repository's scope in the ordinary sense: it is entirely about a
different technology stack and a different kind of testing from anything `conversation-eval-lab`
does or should do.

**Recommendation: remove it from the repository.** It has value to the author; it has none
here and carries a real disclosure risk. It should live outside the tree. Nothing links to
it, so removal costs nothing. **This is the single highest-priority item in this document
and the only one I would call urgent.**

*(I have not read the file into this document beyond what is necessary to describe the
problem, and I have not reproduced its employer-identifying content.)*

## B6. Stale docstrings that describe an older tree

Beyond [B1](#b1-the-schema-says-the-barge-in-events-are-never-emitted-they-are) and
[B2](#b2-the-adapter-docstring-makes-a-claim-the-repo-no-longer-honours):

- **`fixtures/__init__.py`** — "Currently holds the timing calibration report and the sample
  trace behind it." `find fixtures -type f | wc -l` → **581 files**, across
  `audio/{clips,cloud,spoken_call,traces,transport,tts_cache}`, `live_caller/`, `live_full/`,
  `live_run/`, `replay_run/` and `roleplay_live/`. The docstring describes the directory as
  it was around commit `0b0540c`.

I checked several other high-traffic docstring claims and they **hold**:
`lab/checks/engine.py:25` ("no method on this module returns a bare percentage" — true;
`CheckStat.rate` returns `"13/15"`), `lab/trace/schema.py`'s `engine`-field and
`audio_delivered`-vs-`first_byte` rationale, and `lab/voice/interaction.py`'s own account of
the barge-in change. The staleness is localised, not systemic.

## B7. Things a reviewer asks immediately and finds no answer to

**1. "What is your test coverage?"** — 1,976 tests, `pytest-cov` in `[dev]`,
`[tool.coverage.run]` configured in `pyproject.toml` with `branch = true`, and **no coverage
figure is produced anywhere**: not in CI (`pytest -q`, no `--cov`), not in the `Makefile`,
not in the wiki (all 14,802 lines' worth of "coverage" hits are corpus/KPI/tag coverage,
never line coverage). For a repository whose thesis is *measure your instruments before you
trust them*, this is the most conspicuous unmeasured instrument.

I measured it once so the gap is quantified rather than asserted:

```
pytest -q --cov=lab --cov=roleplay --cov=tablemate --cov=ragcheck --cov=scenarios --cov=error_analysis
→ 17,035 statements, 1,892 missed, 4,840 branches, 611 partial → 87% branch coverage
```

Lowest, sorted by cover:

| Module | Cover | Note |
|---|---|---|
| `ragcheck/__main__.py` | **0%** | the entry point `make ragcheck` runs |
| `lab/judges/hallucinated_confirmation/__main__.py` | 0% | 5 statements; trivially a shim |
| `roleplay/scorer_study/__main__.py` | 0% | 5 statements; ditto |
| `error_analysis/pareto.py` | 42% | 79 of 141 statements unexecuted |
| `lab/voice/transport/session.py` | 44% | expected — the live half |
| `tablemate/__main__.py` | 50% | the live runner |
| `lab/voice/engines/tts.py` | 66% | vendor branches |
| `roleplay/regime_eval.py` | **75%** | 152 statements unexecuted, in the file §8.4.5 spends 222 lines on |

`ragcheck/__main__.py` at 0% is the one worth a decision: it is a documented `make` target
whose 73-statement entry module no test executes.

Also note the config itself is incomplete: `[tool.coverage.run] source = ["lab", "roleplay",
"tablemate"]` omits `ragcheck`, `scenarios`, `error_analysis` and `scripts`. Anyone running
`coverage` per the committed config silently measures three of seven packages.

**2. "Is there a type-check gate?"** — No `[tool.mypy]` in `pyproject.toml`, no
`mypy`/`pyright` step in CI, no type-checking dev dependency. The tree is heavily annotated
(`Protocol`s, `Literal`s, frozen dataclasses, pydantic models throughout) and nothing
verifies the annotations. CI lint is `ruff check --select E9,F63,F7,F82` only — which the
workflow explicitly and reasonably defends as "real errors only, deliberately not a style
gate", but that defence does not extend to types.

**3. "What version is this and what changed?"** — `pyproject.toml` says `version = "0.1.0"`,
`evallab --version` prints `evallab 0.1.0`, and there is **no `CHANGELOG.md`, no
`CONTRIBUTING.md`, and no tags**. 40 commits with excellent messages, and no way to ask
"what shipped between X and Y" without reading them all.

**4. "Which corpus does CI validate?"** — see [A2](#a2-which-sections-are-thin-relative-to-their-importance). 55 of 194.

## B8. Dead and unreferenced code — the honest, short answer

I ran an import-graph scan and then hand-checked every candidate, because a naive scan
massively over-reports (relative imports, `python -m` entry points, glob-loaded fixtures).

**Genuinely unreferenced:**

- **`scripts/set_keys.sh`** — tracked, and referenced by **nothing**: not the `Makefile`, not
  the wiki, not the README, not `docs/AUDIO_SUITE.md`. `make audio-setup` runs
  `scripts/setup_audio.sh`; nothing runs this one. Inspected: it is a well-written
  interactive credential-entry helper that writes a `chmod 600`, gitignored `.env`, uses
  silent reads, never echoes a value, and prints only key names and character counts. **It
  contains no credential and honours Rule 16.** The gap is purely that a script that touches
  credentials is undiscoverable — a reader who needs it will not find it, and a reader who
  finds it has no document telling them it is the sanctioned path.
- **`docs/REAL_STACK_ARCHITECTURE.md`**, **`docs/PLAYWRIGHT_NOTES.md`** — see B4, B5.

**Everything else came back clean, and I am recording the negatives so the list is not
mistaken for exhaustive-by-omission:**

- All 30 `make` targets are documented. §5.3 covers every one, including `python-ok`,
  `clean` and `help`, and explicitly earns the word "every".
- No orphan fixture directories. Every one of `fixtures/{calibration_report.md,
  live_caller, live_run, live_full, replay_run, audio/tts_cache, audio/traces,
  roleplay_live}` is read by code. The 696-of-803 data files not named in any source file
  are glob-discovered by design, not orphans.
- Every module the scan flagged is reachable: `roleplay/spoken.py` via `make spoken-replay`,
  `lab/simulator/flake_band.py` via `tests/test_simulator_passk.py`, the four `scripts/make_*`
  recorders via `make *-record`, every `__main__.py` via `python -m`.
- Zero `TODO` / `FIXME` / `XXX` / `HACK` in the tracked tree. Every broad `except` carries a
  `# noqa: BLE001` with a written reason, and every `# pragma: no cover` states why.
- **`build/lib/`** exists on disk with a stale snapshot of `lab/` from around commit
  `b7a825f` (no `flake_band.py`, no `interaction.py`, no `suite.py`, no `transport/`). It is
  gitignored and not a repository defect — local hygiene only. `rm -rf build/` when
  convenient.

---

## What I would do, in order

Not a plan to execute — a proposed ordering for the owner to accept, reorder or reject.

**Decide now, act now (minutes, and the only urgent one):**

1. **B5** — get `docs/PLAYWRIGHT_NOTES.md` out of the tree. Disclosure risk, zero cost to remove.

**Correctness of the record (an hour, no code):**

2. **B1 / B2** — one true sentence about barge-in, propagated to `lab/trace/schema.py:103`,
   `lab/voice/adapter.py:63`, `lab/voice/metrics.py:52`, four wiki locations and
   `INTERVIEW_NOTES.md`. Add `PAYLOAD_KEYS` entries for the two kinds, or state in the
   docstring that they are deliberately unschema'd.
3. **B3** — decide the fix for `lab/cli.py:1716`: derive the Wilson bound from `args.repeats`,
   or drop the sentence. Either way it is a code change and therefore out of scope for a
   docs pass — record it as a ticket now, do it in a build pass.

**Cheap wiki wins (an afternoon):**

4. **A1** — the 13-row judges/simulator/report test table in §8.5.10.
5. **A4** — six links: `AUDIO_TRANSPORT.md`, `cli.md`, `ADVISORY_TEST_STRATEGY.md`,
   `ADVISORY_DEMO.md` into the sections that already discuss their subjects.
6. **A2** — extend §5.4 with the "what CI does not gate, and why" paragraph. The *why* is
   already written in `Makefile:126`; lift it.

**The one substantial addition (half a day):**

7. **A3** — a debugging page. Everything in it already exists in six places; this is
   assembly, not research. This is the single largest increase in what a reader can *do*
   per line written.

**Then decide, without urgency:**

8. **B4** — archive, delete or repurpose `REAL_STACK_ARCHITECTURE.md`.
9. **B7.1** — publish a coverage number, or decide deliberately not to and say why in §10.4.
   Either is defensible; silence is not, in this repository.
10. **A6** — de-jargon the two diagrams in §2.4 and §2.5.
11. **A5** — the §4 rules diagram.

**Explicitly not recommended:** adding features, adding a type-check gate, adding a
CHANGELOG, or expanding CI to cover the audio and transport tiers. Each is arguable; none is
a gap in what the repository *claims*, and the brief is to understand and decide, not to
accumulate.

---

## Appendix — reproduction

Every number in this document, and what produced it. Run from the repository root with
`.venv/bin/python`.

| Figure | Command |
|---|---|
| 1976 passed, 4 skipped in 53.19s | `.venv/bin/python -m pytest -q` |
| 162 tracked `.py`; 54 `test_*.py`; 57 `tests/*.py` | `git ls-files '*.py' \| wc -l` etc. |
| 194 scenario YAML | `find scenarios -name '*.yaml' \| wc -l` |
| 581 fixture files | `find fixtures -type f \| wc -l` |
| 31,541 / 15,817 / 5,091 / 28,307 LOC | `find <pkg> -name '*.py' \| xargs wc -l \| tail -1` |
| 72 Mermaid, 527 headings, 0 broken anchors | slug-and-link script, below |
| 20 test modules unnamed in the wiki | path→two-component→basename match script, below |
| 55/55 scenarios at the CI validate step | `.venv/bin/python -m lab.cli validate --coverage` |
| 87% branch coverage, 17,035 stmts, 1,892 missed | `pytest -q --cov=lab --cov=roleplay --cov=tablemate --cov=ragcheck --cov=scenarios --cov=error_analysis` |
| per-module coverage | `.venv/bin/python -m coverage report --sort=cover` |
| Wilson: 3/3→0.4385, 5/5→0.5655, 8/8→0.6756, 16/16→0.8064 | closed form, script below |
| k=5 emits "three passes out of three … 0.44" | `.venv/bin/python -m lab.cli run -k 5 --live-agent --live-caller --live-judge --out <scratch>` then grep `run_report.json` |
| 676 passed, 4 skipped across the 14 voice files | the 14 paths listed in wiki Appendix A.4, `pytest -q` |
| 32 occurrences / 23 traces / 13 modes, exit 0 | `.venv/bin/python -m error_analysis.pareto --check --no-chart` |
| layering holds; numpy absent; one crack | `import lab, lab.cli, …; sys.modules` + `grep -rnE '^\s*(from\|import)\s+(roleplay\|tablemate\|ragcheck\|scenarios)' lab` |
| 30 make targets, all documented | `grep -nE '^[a-zA-Z0-9_-]+:' Makefile` × `grep -c "make <t>" docs/WIKI.md README.md` |
| interruption events: emitter, reader, tests, no committed trace | `grep -rn 'emit_barge_in\|interruption_' lab tests`; `grep -rl interruption_started fixtures reports` |
| doc cross-reference table | `for f in $(git ls-files '*.md'); do grep -c $(basename $f) docs/WIKI.md README.md; done` |

**Scripts used (paste-able):**

```python
# wiki anchors and links — 527 headings, 254 links, 0 broken
import re
txt = open("docs/WIKI.md").read(); lines = txt.split("\n")
infence = False; heads = []
for l in lines:
    if l.strip().startswith("```"): infence = not infence; continue
    if not infence and re.match(r'^#{2,6} ', l): heads.append(l.strip())
def anchor(h):
    t = re.sub(r'^#+\s*', '', h).strip().lower()
    return re.sub(r"[^\w\s-]", "", t).replace(" ", "-")
seen = {}; final = []
for a in map(anchor, heads):
    n = seen.get(a, -1) + 1; seen[a] = n
    final.append(a if n == 0 else f"{a}-{n}")
links = set(re.findall(r'\]\(#([^)]+)\)', txt))
print(len(heads), len(links), sorted(links - set(final)))
```

```python
# which .py files the wiki never names
import subprocess, os
files = subprocess.run(["git","ls-files","*.py"], capture_output=True, text=True).stdout.split()
wiki = open("docs/WIKI.md").read()
for f in files:
    if wiki.count(f) or wiki.count("/".join(f.split("/")[-2:])): continue
    if wiki.count(os.path.basename(f)) == 0: print(f)
```

```python
# Wilson score lower bound, 95%
import math
def lo(k, n, z=1.959963985):
    p = k / n; d = 1 + z*z/n
    return ((p + z*z/(2*n)) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / d
```

**Working state after this pass:** `git status --short` shows only the untracked
`docs/_plan/` directory. No tracked file was modified. `.coverage` was removed after
measurement; CLI output went to the scratchpad and to gitignored `reports/`.
