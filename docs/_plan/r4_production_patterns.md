# R4 — What a production regression harness knows that this repo does not

**Status:** research and planning. Nothing here has been built. No source file in this repo was
modified to produce it.

**Method.** I read a private, two-year-old regression harness that runs against a live
conversational-AI product, and compared it structurally against this repo. I ran only read-only
commands: `wc`, `find`, `grep`, `sed`, `csv.reader`, one read-only SQLite query, `--help`, and one
full replay run of this repo's own CLI. I spent no money, touched no network, opened no `.env`, and
modified nothing in the suite I was reading.

**Clean room.** The suite belongs to a different domain and a different employer. Everything below
is a *pattern*, deliberately stripped of its subject matter. I refer to it as **PS** ("the
production suite") and to its root as `$PS`; the path is not recorded here on purpose. Where an
idea could not be described without its domain, it is not in this document. This repo is **PF**
("the portfolio repo").

**Numbers.** Every figure carries the command that produced it or is labelled `ASSUMPTION`. There
is no third category. All cost estimates are `ASSUMPTION` — they are my judgement, not a
measurement.

---

## 1. Measurements, so the comparison is anchored

### PS

| Fact | Value | How I got it |
|---|---|---|
| Python files in the repo | 817 | `find $PS -name '*.py' -not -path './.venv/*' -not -path './.git/*' \| wc -l` |
| …of which are the harness | 95 files, 39,374 lines under `tests/regression` | `find tests/regression -name '*.py' \| xargs wc -l` |
| …of which are the review UI | 12 files, 13,122 lines under the dashboard dir | same, on the dashboard dir |
| …the remaining ~710 files | a vendored copy of the product under test | file listing by top-level dir |
| Largest scenario sheet | 11,808 rows × 28 columns | `csv.reader` count over the consolidated sheet |
| Second sheet | 2,147 rows × 28 columns | same |
| CI gate sheet | 1,175 rows × 20 columns | same |
| Curated release suite | 439 rows × 37 columns | same |
| Longest run in the local run store | 439 rows in **1.25 h** | read-only SQLite: `select payload from runs`, parsed `started_at`/`finished_at` |
| Second run in the store | 43 rows in **0.45 h** | same |
| CI job budget | `timeoutInMinutes: 240` | pipeline YAML |
| Default worker counts | CI gate 4 · nightly 12 (×2 repeat runs) · full sheet 32 · smoke 6 | the four `run_*.sh` scripts and the pipeline YAML |
| Grading evaluators | 9 defined, **6 of them can gate**, 3 are quality-only | the gate module's `GATE_KPIS` tuple and its docstring |
| Gate tolerance | 2.0 percentage points, on a **frozen** 100-row fixture against a **pinned** baseline | constants in the gate module |
| "Too broken to grade" threshold | a run with >10% unscoreable rows reports INCONCLUSIVE, not BLOCK | `MAX_ERROR_RATE = 0.10` |

### PF

| Fact | Value | How I got it |
|---|---|---|
| Python lines | 114,098 total; `lab` 31,541; `tests` 28,307 | `find . -name '*.py' \| xargs wc -l` |
| Corpus | 55 scenarios, 4 suites, 9 personas | `python -m scenarios.loader --summary` |
| A full replay run | 47/55 scenarios driven, 369 contract evaluations, **0.70 s wall clock** | `time python -m lab.cli run --no-traces` |
| Concrete contracts | 6 | `grep '^class .*Contract' lab/checks/contracts.py` |
| Parallel execution | **none** | `grep -rn 'ThreadPool\|ProcessPool\|asyncio.gather\|concurrent.futures\|multiprocessing' lab scripts roleplay tablemate` → no matches |
| Run persistence / run index | **none** | `grep -ril sqlite` over the source dirs → no matches |
| Baseline-diff regression gate | present | `lab/cli.py:1159 compare_to_baseline` |

The single most important line in that table is the contrast between **1.25 h for 439 rows** and
**0.70 s for 47 scenarios**. They are not the same activity. PS is paying a model per turn; PF is
replaying committed fixtures. Everything about how the two suites are engineered follows from that
one difference, and most of what PS knows is knowledge about how to survive the first number.

---

## 2. PS's architecture, structurally

Five layers. Named neutrally.

**1. Corpus — a spreadsheet, not a directory.** A scenario is one row of a wide CSV (20–37 columns
depending on sheet). Core columns: id, section, a one-line scenario, a caller-intent paragraph, key
topics, expected actions, fail criteria, a *verification mode* enum, a *variant* (which integration
the product should behave as), a language, and a skip reason. Layered on top, all opt-in and blank
by default, are ~12 behavioural-assertion columns: no-re-ask fields, already-known fields,
no-re-entry components, expected component sequence, required phrases, forbidden phrases, expected
action counts with comparators, max sentence repeats, expected category and sub-category, required
extracted fields, and a max-turns override. A row with all of those blank behaves exactly as it did
before they existed — additive by construction.

The parser is *deliberately lenient*: an unrecognised enum silently coerces to a default. The
suite's own docstring says why that is dangerous and then ships the antidote (see §3.4).

**2. Orchestration — pytest, parametrised over rows, fanned out with xdist.** One test function,
one row per parameter set. Scale comes from `-n WORKERS`. Because every worker hits the same model
deployment, there is a **deployment pool** that hands each worker one of N configured deployments
round-robin by worker index, prints the mapping in the run header, and degrades to identical
pre-existing behaviour when only one deployment is configured. Run configuration is a dozen env
vars with a documented precedence chain (§3.9).

**3. Side effects — a mock registry.** A module enumerates every agent class and every tool name,
with mock implementations. Three properties are worth naming:
 - **Defensive registration.** Every import group is wrapped in `try/except`. A component that
   exists on one build and not another simply fails to attach, and the suite runs unchanged. This
   is what lets *one harness grade several builds of a moving product*.
 - **Verification modes.** A closed enum on the scenario row switches an entire family of mock
   responses at once (which identity lookup succeeds, which fails, which returns multiple
   candidates). One column, one word, a whole substrate swapped.
 - **A fixture-database mode.** Instead of pinning the outcome per row, mocks resolve against a
   shared record set keyed on the arguments the agent actually passes. The outcome becomes
   *emergent from the persona*, so one mode expresses cross-record behaviours (a record whose phone
   changed, two records with the same name) that a per-row flag cannot express at all.

**4. Verdicts — three independent axes, plus a behavioural-assertion library.** §3.1.

**5. Results — JUnit XML as the interchange format, and everything downstream reads it.** The run
writes JUnit plus one transcript JSON per scenario. A converter joins JUnit + transcripts + the
source sheet into a wide review CSV. A second builder produces an 8-sheet workbook. A CI script
computes the pass rate from N JUnit files with best-of-N reconciliation and enforces a threshold. A
Flask app (~70 routes) persists runs to SQLite, lists run history, renders per-run and per-scenario
detail, and exports. JUnit being the hinge is why the flake logic can *amend results in place*
(§3.5) — there is a single file that everyone believes.

---

## 3. Ideas PF does not have

Ranked by (value to PF) ÷ (cost), best first. Cost is `ASSUMPTION` throughout. "Zero-keys" means:
does it survive PF's cardinal rule that a clean clone runs everything with no API keys?

| # | Idea | Cost (ASSUMPTION) | Zero-keys | Verdict |
|---|---|---|---|---|
| 3.1 | INCONCLUSIVE as a first-class status | ~half a day | yes | **take** |
| 3.2 | Failure-ownership triage → a defect report | 1–2 days | yes | **take** |
| 3.3 | Known-noise classification with an audit trail | ~1 day | yes | **take** |
| 3.4 | Mutation testing of the corpus ("teeth") | 2–3 days | yes, and *cheaper here than there* | **take** |
| 3.5 | Flake as a pipeline: quarantine, stability, systemic gate | 1–2 days | yes | **take, partially** |
| 3.6 | The no-look-ahead source contract | ~half a day | yes | **take** |
| 3.7 | Staged gate, cheapest first | ~half a day (docs + one target) | yes | **take** |
| 3.8 | Gating vs measured evaluators | ~half a day | yes | **take** |
| 3.9 | Run-configuration profiles with strict precedence | ~1 day | yes | take if runs get knobs |
| 3.10 | Row-selection filters distinct from subject configuration | ~1 day | yes | take |
| 3.11 | Multi-verdict grading with separate owners | 3–5 days | yes | **decide** |
| 3.12 | Run persistence and a run index | 2–3 days | yes | decide |
| 3.13 | A review/triage surface | 1 day (static) or weeks (server) | static: yes | take the static version only |
| 3.14 | Export layer: the cuts reviewers actually open | 1–2 days | yes | take a subset |
| 3.15 | Corpus composed to production proportions | n/a for PF today | yes | **borrow the rules, not the mechanism** |
| 3.16 | The programmatic-tool probe | n/a for PF today | yes | borrow the lesson |
| 3.17 | Parallel execution at scale | 2–4 days | yes | **do not build yet** |

### 3.1 INCONCLUSIVE — the status that stops infrastructure noise becoming a bug ticket

PS's status vocabulary is `PASS / FAIL / ERRORED / SKIPPED / INCONCLUSIVE`, with an explicit
constant `NON_GRADED = {SKIPPED, INCONCLUSIVE}` naming the two that may never sit in a pass-rate
denominator.

The comment attached to INCONCLUSIVE is the most valuable paragraph I read in the whole suite. It
records an actual run in which 34 rows ended with no terminal tool call and an absurd
seconds-per-turn ratio — the model had stopped returning content mid-conversation — and those rows
were then **graded as though the conversation had completed**. Every one became a "product defect"
in the report. The suite's own summary of the lesson: grading a truncated run is how infrastructure
noise turns into a bug ticket, and it is the same failure mode as counting an errored evaluator as
a pass.

PF has the two neighbouring states and not this one. `CheckResult` already distinguishes a **vacuous
pass** (`applicable=False`) from a **harness error** (`error` set) — both first-class, both
excluded or reported separately, both better-designed than PS's equivalents. But a conversation
that hit `max_turns` is, today, graded as if it ended. `lab/simulator/driver.py:105` is explicit
that this is deliberate — *"a truncated conversation is evidence, not an error"* — and for a
contract like NoProgress that is exactly right. The gap is narrower than PS's: it is that **the
report has no way to say "this scenario could not be graded"**, so a run truncated by a provider
stall in `--live` mode is indistinguishable in the summary from a run the agent genuinely botched.

The port: a scenario-level `INCONCLUSIVE` verdict, set when the session ended for a reason that
means the product never got its chance (`max_turns` with no terminal event, an agent-side
exception, an empty response run), excluded from the stability denominator exactly like a skip, and
printed in its own line. It is one enum value, one branch in the stability roll-up, and one report
section. It is cheap and it is the difference between a report that is honest under load and one
that is only honest when everything works.

**Zero-keys:** yes. It matters *most* on the live paths, but it costs nothing offline.

### 3.2 Failure-ownership triage — a defect report, not just evidence

PS has a ~750-line module that turns a saved run artefact into a structured defect report:
component that owns the missing action, component that owns the missing topic, severity derived
from the scenario's section, a detected repetition loop with its repeat count, a root-cause
hypothesis, and the full transcript attached so a human can overrule it. It is **heuristic tables,
no model calls** — instant, free, and reviewable, and the docstring says so as a design claim
rather than an apology.

PF stops one step earlier. `CheckResult.evidence` is excellent and better than anything PS has: the
quoted trace events that justify the verdict, deterministic down to sorted JSON keys so two reports
diff on behaviour. What PF does not do is answer **"whose bug is this?"** — and that is the
question that decides whether a finding gets fixed.

The port for PF is smaller than PS's because PF's corpus is typed: an ownership map from
`contract name × tool name → component`, plus a severity from the scenario's suite/tags, plus a
one-paragraph markdown renderer per failing scenario. Everything it needs is already in the trace
and the corpus; nothing needs a model.

**Zero-keys:** yes, trivially — it is a pure function of the run report.

### 3.3 Known-noise classification, with the filtered rows kept

PS ships a filter that reads a raw run report and splits it into "valid defects" and "noise", where
noise is six named, dated, reviewed categories of *harness artefact*: conversations that never
started (a provider stall, not a behaviour), assertions on a signal the harness architecturally
cannot observe, assertions whose vocabulary the matcher can never credit, and stale expectations
against a path that legitimately resolves differently.

Two things make this a good pattern rather than a cheat:
1. **Every filtered row is written to a second sheet** labelled "filtered out", so the decision is
   auditable rather than invisible.
2. **The categories are named and dated to the review that created them**, not a generic "ignore".

PF's analogue is `expected_failure` in the corpus, which is *stronger in one direction* — the
contract still runs, still reports, and the corpus notices the day it starts passing. But
`expected_failure` describes a known gap **in the product**. There is no vocabulary for a failure
that is **an artefact of the harness**, and those are exactly the ones that erode trust in a suite
because they never go away and nobody can say why.

The port: a `known_artefact` classification on a finding, with a required `reason` and a required
`review_date`, rendered in its own report section with its own denominator, and — the load-bearing
part — **counted in `integrity_gaps()`**, so a suite that accumulates artefacts is visibly degrading
rather than quietly green.

**Zero-keys:** yes.

### 3.4 Mutation testing the corpus — and PF is better placed to do it than PS is

PS's linter proves a row *has* an assertion. Its mutation module exists because that is not the
same as proving the assertion has **teeth**: an expectation the system satisfies on every path
never discriminates a good run from a bad one, so the row is green today and still green the day
the product breaks. The tool poisons each assertion so a correct run *must* fail, and reports:
mutant fails ⇒ the assertion discriminates; mutant passes ⇒ the assertion is toothless. It also
ships a cheap static mode that just counts independent assertion columns per row — 0 means
guaranteed false-green, 1 means a single point of detection.

PF's loader is already stronger at *prevention*: it rejects a tracked field the caller never says,
an unresolvable argument ref, an `expected_failure` naming a contract the scenario does not declare.
Those are exactly the "can never fire" holes PS's linter can only warn about. But prevention is not
proof. PF has never demonstrated that its 369 contract evaluations *discriminate*.

And here is the asymmetry worth acting on: **killing mutants costs PS money and hours; it costs PF
0.70 seconds.** PS must re-run the mutant sheet through a live model. PF can mutate the corpus, run
it against the committed cassettes, and assert that every mutant dies — in a single-digit number of
seconds, offline, in CI, on every commit. This is the one idea on the list where PF's architecture
makes a technique *cheap that is expensive in production*, which is the most defensible kind of
thing for this repo to own.

Suggested shape: a `mutate` mode that, per scenario, produces the N mutants its assertions admit
(an impossible tool name in a ToolContract, an impossible required phrase, a field the caller never
supplies in a FieldPropagationContract, an inverted ordering), replays each, and reports
`killed/total` per contract type. A surviving mutant is a finding about the corpus, printed with
the scenario id. Publish the kill rate with its denominator, like everything else here.

**Zero-keys:** yes — and this is the flagship case for why the replay-first design was correct.

### 3.5 Flake handled as a pipeline, not a retry flag

PS treats flake as five separate mechanisms, and the separation is the insight:

1. **Automatic serial recheck.** When a parallel run finishes with failures, a pytest hook re-runs
   *only the failed node ids* in an isolated subprocess at minimum contention. A row that passes on
   any serial recheck was contention flake; a row that keeps failing serially is genuine. Worker
   count for the recheck auto-scales as `ceil(failures/8)` capped at 12; attempts are 2 for ≤25
   failures and 1 above; the whole recheck has a 900-second wall-clock budget after which the
   unverified rows keep their original red.
2. **Authoritative amendment.** Once decided, the hook rewrites the primary results file, strips
   the failure evidence from the flake rows, **leaves an audit note in its place**, recomputes the
   suite counts, and flips the process exit status if nothing genuine remains.
3. **An honesty gate on that power.** If more than 20 rows failed, the failure is treated as
   *systemic* — a real regression or a provider outage — and the hook diagnoses but **does not
   amend anything**. This is the rule that makes mechanism 2 acceptable rather than a lie.
4. **A signature blocklist.** Failures whose text matches connectivity or authentication signatures
   are recorded separately, because re-running them while the provider is broken can never heal
   them, and "retried and still failed" would otherwise read as a product verdict.
5. **Best-of-N reconciliation, quarantine, and stability.** Across repeat runs: passed in ≥1 run is
   a pass; failed in every run is a regression; passed-but-also-failed is **FLAKY, surfaced but not
   counted as a regression**. A quarantine list of known-flaky ids is pulled out of the gate
   denominator *and printed loudly every run so it cannot rot*. Two derived signals are always
   emitted: per-scenario stability (pass fraction across runs) and mean stability.

PF is **more principled** here already: `pass^k`, a measured flake band, and `FLAKY is not a pass`
in the headline verdict. Mechanism 1 and 2 are wrong for PF — they exist because PS's flake is
caused by resource contention PF does not have, and PF should not build machinery that rewrites its
own verdicts.

What PF should take is **3 and 5's tail**: a *systemic threshold* (above which the run reports
"this looks like an environment failure" rather than N product findings), and **quarantine with
compulsory visibility** — the property that a suppressed row is printed in every single report so
suppression has an ongoing cost. PF's `expected_failure` is the right precedent; quarantine is its
sibling for rows that are unstable rather than wrong.

**Zero-keys:** yes.

### 3.6 The no-look-ahead source contract

PS has a test that reads the *source* of the caller simulator and fails if it references any
validator-only field: success criteria, expected actions, fail criteria, key topics, verification
mode, covered topics. The rationale, verbatim in spirit: a suite that lets the simulated user see
the pass criteria is an expensive parrot — it has lost its ability to detect regressions because it
has been told the answer.

PF's persona model is architecturally cleaner (gated facts, cooperativeness, a leak audit that
raises), and the driver deliberately detects a caller volunteering a gated fact before it was asked
for. But I found no test that *pins the boundary at the source level*. The risk it guards against
is not that today's driver leaks; it is that a future refactor passes the scenario object one layer
deeper and nobody notices, because the suite will simply start passing more.

The port is small: an AST or regex check over `lab/simulator/driver.py`'s caller path asserting it
never reads the assertion side of a scenario. Half a day, and it is the kind of test that pays for
itself once in five years and is worth ten times its cost when it does.

**Zero-keys:** yes.

### 3.7 The staged gate: cheapest first

PS's pre-flight runs three stages in strict cost order and stops at the first failure:
lint the sheets with no model (milliseconds) → harness self-tests with no model (seconds) → a
handful of deterministic rows through the real path with a model (minutes). The stated reason is
that a mistake should be caught in milliseconds instead of after a long, misleading, expensive run.

PF has all the pieces — `make validate`, `make test`, `make calibrate`, `make replay`, `make demo` —
and no documented ordering that says which one you run first and why. That is a `Makefile` target
and a paragraph, and it converts a pile of capabilities into a *procedure*, which is what a reviewer
is actually looking for.

**Zero-keys:** yes.

### 3.8 Separate the evaluators that gate from the evaluators that measure

PS defines nine evaluators and lets **six** decide the gate; the other three are explicitly
quality-only and *cannot change the verdict*, by construction, with a flag to score them anyway for
the detail columns. This is a small, mature idea: a metric you are not yet willing to block on
should still be measured, and the way you stop it silently becoming a blocker is to put the
distinction in the code rather than in someone's head.

PF's 28-KPI scorecard in the advisory pack is the obvious place this applies. Splitting it into
"gating" and "measured" sets, with the gating set justified in one sentence each, is cheap and
directly answers the reviewer question "why is this number here?".

**Zero-keys:** yes.

### 3.9 Run-configuration profiles with a precedence chain that cannot surprise you

PS's problem: a dozen env vars, and the canonical combination for "CI gate" versus "nightly" versus
"local debug" lived in shell scripts and people's memory. Its fix is a named profile file with a
precedence chain stated as a design rule and enforced by construction — the loader **only ever
`setdefault`s**, so an explicit env var always wins, a missing or malformed profile is a silent
no-op, and it can never raise into test collection.

`explicit env > profile file > code default`, with "only ever setdefault" as the mechanism, is a
pattern worth copying verbatim the moment PF's CLI grows past the number of flags a person holds in
their head. Today PF's `run` subcommand has 24 options, which is arguably already past it.

**Zero-keys:** yes.

### 3.10 Row selection is not subject configuration

The subtlest small idea in PS. It has two entirely separate concepts that look identical from the
outside:
- a **row filter** — which scenarios execute (by suite tag, by variant, by tenant, by group);
- a **runtime override** — what the system under test is configured to *be* for this run.

The code comments go out of their way to say "this is DISTINCT from the runtime override". They are
right to: conflating them produces a run that quietly tested a different subject than the one it
reported on, and no amount of downstream reporting discipline can recover from that.

PF has `--suite`, `--tag`, `--scenario` (row filters) and `--subject`, `--agent-factory`,
`--live-*` (subject configuration), and they are already separate — but the *report* records the
subject as a free-text label. Making the subject a structured, validated field that appears in the
report header and in the baseline-diff comparison would close the gap where a baseline is compared
against a run of a different subject.

**Zero-keys:** yes.

### 3.11 Multi-verdict grading — the headline idea, and the one to think hardest about

PS grades one conversation on **three independent axes**, and its module docstring is an argument
rather than a description. Paraphrased and de-domained:

> These are three different regressions with three different owners. A change in component A can
> break the conversation while classification stays perfect; a change in component B can drop the
> downstream success rate without changing a single word the agent says. Folding them together —
> which is what failing the test on a classification mismatch does — destroys exactly the signal the
> report exists to carry: the run would show "37 failures" with no way to tell which axis moved.

The mechanics that make it work are as instructive as the idea:
- Axes 2 and 3 are computed **after** axis 1 is already decided, in a `finally` block, and are
  **recorded rather than raised**. Axis 1 is whatever the primary assertions said, full stop.
- Each axis fails **onto its own axis**. A crashed grading stage lands as ERRORED on that axis and
  never touches the other two.
- Every axis can be **skipped independently**, and is skipped **automatically for scenarios that
  cannot have it** — a scenario with no downstream submission path is never charged for a
  submission verdict. "Never charge a row for a verdict it cannot have" is the same insight as PF's
  `applicable=False`, arrived at from the opposite direction.
- All axes score **provably identical text**. There is a single formatter, and the docstring notes
  that a divergence there would make the verdicts incomparable and therefore all of them
  untrustworthy.
- The extra axes are **off by default** because they cost tokens and wall-clock.

For PF the question is not whether the pattern is good — it is — but **what PF's second and third
axes are**. PF already has per-contract verdicts, which is a finer-grained thing than PS's axis 1.
What it does not have is a *small number of named, independently-owned axes* that a reader can scan.
Candidates, in decreasing confidence:
1. **Task outcome** — did the conversation reach the goal state? (Contract-derived; free.)
2. **Conduct** — did it comply with the declared policy/regime constraints? (Already computed in
   the advisory pack; needs surfacing as an axis.)
3. **Faithfulness** — was everything it asserted grounded? (`ragcheck` already computes per-claim
   faithfulness; wiring it as a third axis on a conversation is a real design question, not a
   plumbing one.)

**Cost** is the highest on this list (`ASSUMPTION`: 3–5 days) and most of it is *deciding*, not
typing. **Zero-keys:** yes, if the axes are contract- or replay-judge-derived.

**Recommendation:** this is a decision for the owner, not a task. If it is taken, take the four
mechanical rules above with it — they are what stop a three-axis report from becoming a
three-times-noisier one.

### 3.12 Result persistence and a run index

PS keeps run history in SQLite at a path chosen to survive redeployment, with three properties
worth copying: persistence is **best-effort and swallows its own errors** so a storage failure can
never break a live run; the writer **snapshots** the mutable run dict periodically instead of
chasing every mutation site; and per-run logs are **trimmed to a fixed character budget** on
persist because they grow to many megabytes.

PF has none of this and does not obviously need it — it has something arguably better for its
purpose, which is a **committed** reference run and a baseline diff, i.e. run history in git. The
honest framing: PS needs a database because its runs are expensive, non-deterministic and
non-committable; PF's runs are cheap, deterministic and committable, so git *is* its run store.

Where PF genuinely has a gap: there is no way to ask "how has this contract's failure rate moved
over the last ten commits?" — the baseline diff is strictly two-point. A trend, even as a committed
JSONL of run summaries appended by `make reference`, would answer that with no database at all.

**Zero-keys:** yes. **Cost** (`ASSUMPTION`): 2–3 days for the full thing, half a day for the JSONL.

### 3.13 A review/triage surface

PS's dashboard is ~70 routes over 8,973 lines: run history with status/suite/free-text filters and
per-run duration; a run detail page that shows the three verdicts per row; a per-scenario transcript
page; scenario CRUD; a "quick test" page that runs one scenario interactively; a monitor page with
a trend chart and an explicit **pin baseline** button; branch checkout controls; and exports in
four formats.

This is where PS's practicality is most visible and where copying it would most damage PF. A Flask
app with auth, a database and 70 routes is not a portfolio artefact; it is an operations product,
and PF's cardinal rule is that a clean clone runs everything with zero keys.

The version that survives PF's constraints: **one self-contained static HTML file, generated from
the run JSON, opened with `file://`.** Filterable table of scenarios, click a row to expand its
trace with the failing contract's evidence highlighted, no server, no database, no auth, no network.
That is a day's work, it demos in one click, and it is strictly better than a server for a reviewer
who has just cloned the repo.

The one PS feature I would explicitly steal into it: the **pin baseline** button's *discipline*, not
its UI — the baseline is refreshed only by an explicit, deliberate action and is **never**
recomputed as a side effect of a run, because a moving baseline makes the gate meaningless. PF's
`make reference` already works this way; it deserves to be stated as a rule in the docs rather than
implied by a Makefile.

**Zero-keys:** yes for the static version.

### 3.14 The export layer, and which cuts reviewers actually open

PS's workbook has eight sheets, and the list is itself the finding, because it is what two years of
review requests converged on:

Summary · By language · By section · **Failure patterns** · **Top failing scenarios** · All tests ·
**Failed tests with transcripts inline** · Passed tests with transcripts inline.

The three in bold are the ones a portfolio report should carry and PF's currently does not:
**failure patterns** (the same failure clustered and counted, so one root cause does not read as
forty findings), **top failing scenarios** (ranked, so the reader knows where to look first), and
**failures with the conversation inline** (so nobody has to open a second file to judge a finding).

PF's report is markdown plus JSON and is in most ways better designed — but it is organised by
contract, not by "what should I look at first". Adding a clustered failure-pattern section and a
ranked top-failures section is a day's work in the existing renderer and needs no new dependency. A
spreadsheet export is a separate question; `ASSUMPTION`: most reviewers of *this* repo will read
markdown, so I would not add `openpyxl` for it.

**Zero-keys:** yes.

### 3.15 Corpus composition — borrow the rules, not the mechanism

PS builds its main run set to **mirror the proportions its production traffic actually shows**, and
keeps rare-but-critical categories in a *separate* sheet precisely so they cannot dominate the
headline rate. It also has generators that turn confirmed production incidents into scenarios, and
those generators carry authoring rules learned from a first attempt that produced *100%
false-positive failures*:

1. Assert on **words the system would actually say**, never on diagnostic meta-labels. A topic
   called "infinite_loop" can never be uttered, so every row asserting it fails forever.
2. Only name **tools that exist**. A phantom tool name is a permanently-red row.
3. The caller instruction is given verbatim to the simulator, so it must read as a **caller's
   dialogue plan, not as QA notes about what the system should do** — otherwise the simulator
   recites the QA notes as speech.
4. A scenario testing a signal the harness architecturally cannot observe must be **marked as such**
   and its assertions moved to what the harness *can* see.

PF cannot use the proportion mechanism (it has no production traffic, and inventing one would be
dishonest). Rules 1–4, however, are corpus-lint rules PF could enforce *statically* — rule 2 it
already enforces via closed vocabularies, and rule 3 is a genuinely new check: a caller-instruction
field that reads as instructions-about-the-agent rather than as caller intent. That is a heuristic,
so it should be a WARN, never an error.

**Zero-keys:** yes.

### 3.16 The programmatic-tool probe — a bug class PF should know about

PS discovered that some tools are invoked **by the agent's own code**, not by the model. Those
invocations emit no tool-call event, so they were invisible to the assertions *and* they bypassed the
mock layer entirely and ran the real implementation against a real external service. PS's fix wraps
the underlying function of the decorated tool, so both paths are observed and both are mocked.

PF's subject is in-repo and instrumented, so this specific bug may not exist here. The
**transferable lesson** is a claim about trace design that PF should be able to state and defend:
*a trace built from what the model asked for is not a trace of what the system did.* If any tool in
`tablemate/` or `roleplay/` can be called from a code path rather than a model decision, PF should
verify its trace records it. That is a 20-minute audit, not a project, and the answer is worth
writing down either way.

### 3.17 Parallel execution — the thing I would explicitly not build

PS's entire parallelism apparatus — worker pools, a deployment pool to avoid rate-limit starvation,
contention-driven flake, serial recheck, systemic thresholds — exists to make **one number** bearable:
1.25 hours for 439 rows. PF's equivalent number is 0.70 seconds for 47 scenarios.

Adding parallelism to PF today would buy nothing measurable and would import the entire class of
bug that PS's flake machinery exists to paper over. The correct time to build it is when a live run
becomes the routine path, not before. Writing down *why* it is absent — with both numbers — is more
impressive than having it.

---

## 4. What PS does better, plainly

Stated without hedging, because it earns it: PS runs against a real, moving product, with real users
downstream, and has been maintained for two years.

1. **It grades a product it does not control.** The defensive mock registry, the version-drift
   preflight, the optional-module stubbing, the branch-checkout controls — all of it exists so one
   harness can grade several builds of a moving target. PF grades a product that lives in the same
   repo and is written to be gradable. That is a much easier problem, and PF should not pretend
   otherwise.
2. **It answers "whose bug is this?"** PF answers "what happened, with evidence". PS answers "what
   happened, who owns it, how bad, and here is a report you can paste into a ticket". That last step
   is the one that determines whether findings get fixed.
3. **It has an operations surface.** Run history, exports in four formats, a CI gate with a
   threshold, published artifacts, a notification on completion, a per-scenario interactive re-run.
   PF has a CLI and files.
4. **It has learned which of its own failures are lies**, and encoded that knowledge in a reviewable
   filter with an audit sheet. That knowledge is only obtainable by running a suite for two years
   against a product that changes underneath it.
5. **Its flake economics are worked out.** Bounded budgets, auto-scaled recheck concurrency, a
   systemic threshold that disables the self-healing, a signature blocklist. PF's flake handling is
   theoretically better and has never been stress-tested at cost.
6. **Its corpus has provenance.** Rows derived from real incidents, in the proportions real traffic
   shows. PF's corpus is hand-authored and says so, which is honest, but it is not the same claim.
7. **It knows what a reviewer asks for on day 400.** The eight-sheet breakdown, the "failed tests
   with transcript inline" sheet, the three-verdicts-per-row detail page — none of those are
   guesses.

---

## 5. What PF does better — the list to not give up under pressure

Each of these is a place where PS pays, visibly, for not having it.

1. **A typed trace as the single artefact.** PS's assertion library recovers structure by
   **regexing formatted strings** out of a rendered transcript — it matches `[Component called
   tool]` chips with a compiled regex to reconstruct the component-visit order. Worse, that
   transcript *drops the tool arguments and results*, so PS needed a whole adapter module to pair
   calls back to their outputs by call-id and re-emit a production-shaped line, for the grading
   stages only, without disturbing the transcript the other verdict uses. That adapter is ~120 lines
   of pure tax, and it exists because the trace was a string. PF's `TraceEvent` makes the entire
   problem not exist. **Do not give this up.**
2. **Contracts decided on event-stream position, not timestamps.** PS's checks are position-based in
   practice but not by design, and its timing signals live in a different layer entirely.
3. **Calibration gates that raise below threshold.** I searched PS for `kappa`, `inter-rater`,
   `true positive`, `precision`, `recall`, `calibrat*` across all 95 harness files: **no matches**.
   PS pins a *baseline rate* for its model-based evaluators and alerts on a 2-point drift. That is
   drift detection, not accuracy — it will happily hold a baseline steady around an evaluator that
   is wrong 30% of the time. PF's `calibrate()` measuring TPR/TNR/precision/recall/F1/kappa against
   hand labels, with a registry that **raises** below threshold, is the single largest methodological
   advantage this repo has, and it is exactly the thing a reviewer of an evals role is looking for.
4. **Denominator discipline enforced by types, not convention.** `format_rate` printing `5/6
   (83.3%)`; `JudgeSummary.calibration` being a *required* field so a judge verdict is structurally
   unprintable without its measured error rates; `VoiceMetrics.calibration_verdict` likewise,
   including an explicit `NOT_RUN`. PS achieves comparable honesty by writing careful docstrings and
   remembering. Types beat memory.
5. **A corpus that rejects assertions which can never fire.** PS's parser is *deliberately lenient*
   and coerces bad values silently — its own linter's docstring calls this out as the reason the
   linter had to be written. PF forbids it at the schema: unreachable tracked fields, unresolvable
   refs and undeclared `expected_failure` contracts are all load-time errors. Prevention beats
   detection.
6. **`expected_failure` as a live expectation.** A known gap that still runs, still reports, and
   notices the day it starts passing. PS's nearest equivalent is a quarantine list — exclusion, not
   observation.
7. **Vacuous passes as a first-class state.** PF counts and prints checks that asserted nothing. PS
   has no equivalent concept, and its linter's whole purpose is to catch statically the rows that
   would produce them.
8. **A report that audits itself.** `integrity_gaps()` printing where the report's own evidence is
   thin — failures without a quote, scenarios run at k=1, contracts with a zero denominator — in its
   own section. I found nothing like it in PS.
9. **Reproducibility from a clean clone with zero keys**, with committed fixtures and byte-identical
   reports from identical results. PS cannot be run at all without credentials and a vendored copy
   of the product.
10. **0.70 seconds.** It is a feature, not an accident, and every idea in §3 should be judged partly
    on whether it preserves it.

---

## 6. Scale lessons

**Where the cost actually is.** Not in the harness — in the model calls, one or more per
conversational turn, times rows, times repeat runs. Everything expensive in PS is downstream of that:
worker pools, deployment pools, contention flake, recheck budgets, "fast mode".

**What "fast mode" really trades.** PS's fast profile turns off the per-scenario model judge,
switches topic detection from model-based to keyword-based, and caps turns at 15 instead of 45. That
is not a speed knob; it is a **different instrument**, and a pass rate measured under it is not
comparable to one measured without it. PS documents the flags but I did not find a rule forbidding
comparison across profiles. `ASSUMPTION`: this is a live hazard there, and it is a rule PF should
adopt pre-emptively — *the run report must record the profile, and a baseline diff across differing
profiles must refuse rather than compare.*

**Where it hurts, from the code itself.** All four are honest and all four are visible in the source:
- **Corpus sprawl.** The largest sheet is 11,808 rows × 28 columns, and the data directory holds
  dozens of derived and backup sheets with timestamp suffixes. A spreadsheet corpus scales to
  thousands of rows and then becomes hard to reason about; the suite's answer is a *generator* and a
  "control surface" workbook where editing one shared block regenerates every scenario. That is a
  good answer, and it means the CSVs are build artefacts, which is not always obvious to a reader.
- **Leniency debt.** The parser silently coerces bad values; the linter, the mutation checker and a
  dedicated hardening test suite all exist to contain a decision that could have been made
  differently once. PF made the other choice and owes nothing.
- **Verdict cost.** The extra grading axes cost up to nine model completions and two subprocess
  calls *per scenario*, which is why they ship off by default. Any multi-verdict design must decide
  its per-scenario cost before it decides its taxonomy.
- **The mock that hides a real path.** The mock standing in for the routing decision necessarily
  re-implements the gating logic that lives inside the real routing function. The suite handles this
  carefully and documents it, but the structural consequence is unavoidable: **the behaviour of the
  real gate is not observable through the mock.** This is the general risk in every mocked harness,
  PF included, and PF's version of the question is: *which contract failures could only ever be
  caused by the fixture, and does the repo say so anywhere?* Worth one honest paragraph in the wiki.

---

## 7. Shortlist

If the owner wants a minimal set that is defensible, cheap, and does not compromise anything in §5:

**Tier 1 — take (all `ASSUMPTION`: ~4 days total, all zero-keys, none touch the trace design)**
1. `INCONCLUSIVE` as a scenario-level status, excluded from denominators (§3.1).
2. Failure-ownership + severity + a paste-ready finding renderer (§3.2).
3. `known_artefact` classification with a required reason, a required date, and a line in
   `integrity_gaps()` (§3.3).
4. The no-look-ahead source contract over the caller path (§3.6).
5. A documented, ordered gate — lint, self-test, replay, calibrate — as one Makefile target and one
   paragraph (§3.7).

**Tier 2 — the flagship**
6. Corpus mutation testing with a published kill rate and its denominator (§3.4). This is the one
   that is *cheaper here than in production* and it is the strongest single argument this repo can
   make about its own architecture.

**Tier 3 — decide, do not drift into**
7. Multi-verdict axes (§3.11) — a design decision about what PF's second and third axes *are*, not a
   build task.
8. A static single-file run viewer (§3.13) — one day, one click, no server.

**Explicitly do not build:** parallel execution (§3.17), a Flask dashboard with auth and a database
(§3.13), a spreadsheet export layer (§3.14), self-healing result amendment (§3.5, mechanisms 1–2).

---

## 8. Open questions for the owner

1. **Multi-verdict:** what are PF's second and third axes? If the answer is not obvious in one
   sentence each, the honest move is to keep one axis and say why in the wiki, rather than to invent
   axes to match a pattern.
2. **Profile-tagged reports:** should a baseline diff *refuse* to compare runs recorded under
   different profiles, or merely warn? PS warns by omission; refusing is stricter and matches this
   repo's temperament.
3. **Quarantine:** does PF want one at all? `expected_failure` covers known product gaps.
   Quarantine covers known *instability*, which PF currently handles by reporting FLAKY. Adding
   quarantine adds a suppression mechanism, and suppression mechanisms are how suites rot.
4. **The mock-hides-the-real-path paragraph** (§6, fourth bullet): is there a fixture in
   `tablemate/` or `roleplay/` whose behaviour a contract can only ever be testing *because the
   fixture says so*? If yes, that belongs in the wiki as a stated limitation, and it would be a
   genuinely rare thing for a portfolio project to admit.
