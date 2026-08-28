# Test selection: running only the scenarios a change can reach

A production suite of thousands of scenarios takes about ninety minutes and real
money per run. Most changes touch a small area — one flow, one prompt, one
agent. Running everything every time is waste. Running a guess is negligence.

This is the layer in between. Given a change, it says **which scenarios that
change can possibly affect**, so those run live and everything else replays for
free — and it says *why* for every scenario on both sides of the line.

```
python -m lab.selection --changed-since HEAD~1
```

Not an `evallab` subcommand yet. `lab/cli.py` is wired to the selection layer
separately, as a follow-up, once all three stages have landed; until then each
stage carries its own entry point. Nothing here starts a run — the output is
arguments for the runner's **existing** `--scenario` filter.

Offline, deterministic, no API key, no network. A clean clone with every
environment variable unset behaves identically, and there is a test that says so.

---

## 1. The shape of it, in one page

Three stages, each the cheapest tool that answers its question.

| stage | module | question | how |
|---|---|---|---|
| 1 | `lab/selection/diff.py` | what changed? | `git diff` + AST parsing, down to functions, classes and string literals — a reworded prompt is a change |
| 2 | `lab/selection/trace_map.py` | what does each scenario touch? | derived from the committed traces |
| 3 | `lab/selection/select.py` | which scenarios can this change reach? | the join, plus every fail-safe rule |

Stage 1 produces changed symbols as `path::qualname`. Stage 2 records where each
runtime agent and tool name is *defined*, in the same `path::qualname` shape. The
join is a set intersection with no translation layer between the stages.

**Version one is fully deterministic. There is no model anywhere in it.** The
things static analysis provably cannot see are named in §6 and fall back to
selecting everything; `scenarios/selection_overrides.yaml` is the documented seam
for the ones a human already knows about.

---

## 2. Why the map is derived and not declared

The obvious way to build this is metadata: each scenario declares which agents
and tools it depends on. That fails for a reason that has nothing to do with
discipline. **The person who writes the metadata is never the person who breaks
it.** A developer moves a tool call from one agent to another; the scenario's
declared dependency list is now wrong; nothing fails, because nothing checks a
declaration against reality. The map goes stale silently, and a stale selector is
strictly worse than no selector — no selector wastes money, a stale one ships
regressions behind a green run.

The owner of this suite is a QA engineer and cannot require developers to
maintain metadata. So the map must be derived. That is normally impossible: an
ordinary test suite leaves nothing behind to derive from.

**This repository is trace-first, and that is the only reason this works.** Every
run writes an ordered stream of typed events — which sub-agent spoke each turn,
which tools were called, which handoffs fired, which engine produced each event —
and those traces are committed under `fixtures/`. That is a dependency graph
nobody had to write down, and nobody can forget to update: it is regenerated from
the traces, and `python -m lab.selection.trace_map --check` fails if the committed
artefact has drifted from them.

The honest version of this claim: **this technique is not portable to a repository
that does not record execution.** It is not a clever idea that anyone could apply;
it is a payoff for having built the harness trace-first in the first place.

---

## 3. Stage 2 — the trace-derived dependency map

`lab/selection/trace_map.py` → `lab/selection/trace_map.json` (committed, generated)

```
python -m lab.selection.trace_map           # coverage summary
python -m lab.selection.trace_map --write   # regenerate the artefact
python -m lab.selection.trace_map --check   # exit 1 if it has drifted
```

### Coverage

| | |
|---|---|
| scenarios mapped | 55/73 |
| unmapped — always run | 18/73, all of them the audio tier |
| evidence | 226 sessions across 237 committed trace files |
| distinct agents / tools / engines | 4 / 5 / 4 |
| runtime names resolved to source | 9/9 |

The denominator is all five suites, not the four default text suites. On 55/55
the audio tier would disappear from the report while still needing to run on
every change — the exact error this tool cannot afford.

Per scenario the map derives the agents, tools, handoff edges, engines, adapters
and event kinds its committed traces show, the evidence file paths, and a 12-hex
digest over their sha256s so a re-recorded fixture shows up in the diff even when
the derived sets are unchanged. Nothing reads a timestamp; events are consulted in
file position order.

---

## 4. Stage 3 — the join, and the one rule

### 4.1 Fail safe, encoded as defaults

**When the selector is unsure, it includes. Never excludes.** Skipping a scenario
that should have run is the only unrecoverable error this tool can make: the run
goes green, the report says "all passed", and the regression ships. Every other
mistake costs money and is visible. That asymmetry is the whole design.

So every ambiguity is a value in an enum, not a convention in a comment, and each
one has its own test on a synthetic corpus rather than on the tidy committed one —
so the rule is live before the day it is needed.

| ambiguity | resolution | code |
|---|---|---|
| git unreachable, or a file that will not parse | whole corpus | `global-trigger` |
| a changed symbol at a site no trace ever observed | whole corpus | `unplaceable-change` |
| a changed file stage 1 attributed to nothing | whole corpus | `unaccounted-file` |
| the map is missing, unreadable or degraded | whole corpus | `map-*` |
| a directly-changed scenario id that is not a corpus row | whole corpus | `direct-unknown-scenario` |
| an override file that will not parse | whole corpus | `overrides-unreadable` |
| an override naming a scenario that no longer exists | whole corpus | `override-unknown-target` |
| a scenario with no usable trace evidence | always selected | `unmapped-scenario` |

The mirror-image rule matters just as much: **an empty answer is stated, never
implied.** A diff that changes nothing returns a `NOTHING` verdict in words, not a
bare empty set a caller might read as "the filter is off". In `--runner-args` mode
that case prints nothing to stdout and exits 2, because
`evallab run $(python -m lab.selection --runner-args)` with an empty substitution
would run the entire suite — the precise opposite of what the answer meant.

### 4.2 Two joins that are not obvious

*Nested qualnames.* Stage 1 reports a changed method as
`tablemate/agents.py::PolicyAgent.handle`. Stage 2 records the runtime name's
definition site as `tablemate/agents.py::PolicyAgent`. A literal string
intersection misses, which would send the commonest change of all — editing a
method body — to the whole corpus every single time. The resolver therefore
matches when either qualname is a dotted ancestor of the other. A method is part
of its class; that is not a guess. The dot in the prefix test is load-bearing, so
`check_policy_helper` is never mistaken for part of `check_policy`.

*Module-level code.* `path::<module>` is import-time code, which runs for
everything in the file, so it selects every runtime name observed in that path.
Stage 2 documents the same widening, and a test pins the two implementations
together so a divergence fails loudly instead of quietly narrowing.

### 4.3 The override file adds; it cannot remove

`scenarios/selection_overrides.yaml` exists for dependencies a trace genuinely
cannot see — a config value read at run time, a prompt fragment shared between two
agents, a dependency that lives only in data. It ships with an empty rule list,
which is the healthy state.

**It can only widen a selection, and that is structural rather than advisory:**

* `then:` has no key that names an exclusion. There is nothing to write.
* The schema is `extra="forbid"`, so an invented `exclude:` or `skip:` key is a
  validation error the operator sees, not a key that is silently ignored.
* The code that applies the rules returns *additions* and is never shown the base
  selection, so it cannot subtract from a set it has not got.
* `select()` asserts the post-condition anyway, and raises if it is ever violated.

Three of those are structural and the fourth is a backstop. The reason narrowing
is refused even to a human who is certain: over-selection is a cost, under-
selection is a silent failure, and the fix for over-selection is better
derivation — never a person declaring something safe to skip. That is the same
staleness trap §2 rejects, just with a shorter fuse.

A rule naming a scenario that no longer exists escalates to the whole corpus.
Staleness in this file is therefore expensive rather than dangerous, which is the
trade you want.

---

## 5. The selector is a grader, so it carries a number

The selector decides "this need not run". If it is wrong, a regression ships
silently. That is the LLM-as-judge problem in a different costume, and this
repository already has the answer: measure it, publish the number with its
denominator, and refuse to gate below threshold.

There are **two** questions here, they are not the same question, and passing the
first tells you nothing about the second:

| | question | if it fails | command |
|---|---|---|---|
| §5.1 | Is the join internally sound? Does the selector keep every scenario its own map implicates? | the join is broken | `python -m lab.selection --calibrate` |
| §5.2 | Does it keep the scenarios that **actually broke**? | the map is incomplete, or the join is | `python -m lab.selection.calibrate` |

§5.1 never runs the system under test; it reasons over the committed map, so it
cannot notice that the map itself is missing a dependency. §5.2 runs the real
suite over real code, twice, and compares. A selector can score a perfect 5.1 and
still miss a regression in 5.2 — which is why the gate reads 5.2.

### 5.1 Is the join internally sound?

```
python -m lab.selection --calibrate
```

```
selector calibration (deterministic, offline)
  probes                  18 synthetic single-symbol changes
  evidence pairs kept     444/444   (recall 1.000)
  always-run floor kept   324/324
  controls passed         4/4
  mean selection          42.7/73   (30.3/73 skipped, 41.6%)
  measures the join against the recorded evidence, not the evidence against reality
  verdict                 PASS (threshold 1.000)
```

**What the probes are.** For each of the 9 runtime names the map has observed, a
single-symbol change is synthesised at its definition site — once as the plain
qualname, once nested one level deeper, which is what stage 1 emits for a method
body — and the resulting selection is checked against every scenario whose
committed traces name that symbol. 9 names × 2 forms = 18 probes.

**Recall 444/444** means the selector never dropped a scenario the evidence
implicates. **324/324** is the always-run floor: 18 unmapped scenarios × 18
probes, every one of them selected every time. Overrides are excluded from the
probes on purpose — otherwise one `everything: true` rule buys a perfect score.

The four controls guard the other direction, because a selector that always
returned the whole corpus would score a perfect recall and be useless:

1. a change the map cannot place selects all 73;
2. an empty diff selects nothing, and says so;
3. a degraded map selects all 73;
4. at least one probe genuinely narrows.

Exit status is 1 below `--min-recall` (default `1.000`), so this can gate.

**What the number does not cover, stated next to it every time it is printed:**
this measures the *join* against the recorded evidence, not the evidence against
reality. The map is a lower bound on the dependency set (§6), so a scenario that
*would* reach a symbol on some other input appears in neither the expectation nor
the selection. No deterministic stage can close that gap; §6 says what does.

### 5.2 Does it keep the scenarios that actually broke?

The number that matters, and the one commercial test-impact tools do not print.
Run the full suite at a base tree and at a changed tree. The failures that appear
are what a full run catches. Ask the selector what it would have run. A **miss**
is a scenario that newly failed and that the selector would have skipped — a
regression made invisible.

```
python -m lab.selection.calibrate            # both studies, summary
python -m lab.selection.calibrate --write    # ... and record the artefact
```

Measured at commit `44b6625`, recorded in `lab/selection/calibration.json`:

```
selector calibration — evidence: all
  cases measured          300/324
  cases that broke a row  39/300
  failures caught         319/319   (recall 1.000)
  verdict changes caught  335/335   (recall 1.000)
  of those, non-vacuous   125/125   (recall 1.000)
  vacuous confirmations   194/319   (failures where the selector had skipped nothing)
  mean selection          66.5/73   (6.5/73 skipped, 8.9%)   [proportional stratum, n=197]
  gate                    PASS (threshold 1.000)
    observed-enriched     45.1/73   (27.9/73 skipped, n=103)
    proportional          66.5/73   (6.5/73 skipped, n=197)
  not measurable          24/324
     24  head tree would not run
```

**Read the third-from-last number before the first one.** A recall of 319/319
looks conclusive and mostly is not: **194 of those 319 failures happened in cases
where the selector had skipped nothing at all**. It was never at risk of missing
them. Counting them as catches is the same error `lab.checks` refuses to make
when a vacuous contract "passes" — it asserted nothing, so it found nothing.

The honest denominator is the **non-vacuous** one: 125 failures that occurred in
cases where the selector *had* excluded at least one scenario, and so could
genuinely have been wrong. It missed none of them. That is the calibration.

**The failure counts are not run-to-run stable, and the selection counts are.**
Three runs of the pinned command at this commit, seed 0, gave:

| run | cases | broke a row | failures | non-vacuous | unmeasurable | mean (prop) |
|---|---|---|---|---|---|---|
| a | 300/323 | 36 | 264/264 | 120/120 | 23 | 66.5/73, n=197 |
| b | 304/324 | 37 | 245/245 | 99/99 | 20 | 66.5/73, n=199 |
| c | 300/324 | 39 | 319/319 | 125/125 | 24 | 66.5/73, n=197 |

The right-hand column — everything the *selector* decides — is identical every
time, as is the §7 per-symbol table. What moves is how many contract failures a
given mutant produces and how many mutants leave a runnable tree, which is a
property of the suite being driven, not of the selector. So quote the **recall**
and the **selection ratio**, which are stable; do not quote `319` as though a
rerun would print it again. Making the counts reproducible means pinning down the
suite-side variance first, and that is open work, not a solved thing.

**Why the two strata.** An unbiased sample of code edits almost never produces a
non-vacuous case, because most changes land somewhere no trace observed and the
selector correctly widens to everything. The first study run this way found **1**
discriminating failure in 150 mutants — a denominator of one is not a
measurement. So the sample is drawn in two parts:

* **proportional** (n=197) — an even draw across the five operators, over
  `tablemate`, `lab/checks` and `lab/simulator`. Representative, so the
  **selection ratio is computed from this stratum alone**.
* **observed-enriched** (n=103) — drawn only from the 9 sites the trace map
  resolved to source, where the selector is *able* to narrow. Deliberately
  disproportionate, so it is excluded from the saving and pooled only into the
  recall. A miss is a miss wherever it happens.

Pooling them would quote a 27.9/73 (38.2%) saving the tool does not deliver on
a representative change. Reporting only the proportional stratum would leave the
recall with a denominator of one. Both are printed, each labelled with its n.

**What the saving actually is.** 6.5 of 73 scenarios on a representative change,
and 27.9 of 73 when the change lands on an observed site. The modest headline is
the fail-safe rule working as designed: the map resolves 9 runtime names to
source, and a change anywhere else — `runtime.py`, `store.py`, `understanding.py`,
any harness module — cannot be placed and runs everything. §6 is why that is
correct rather than a deficiency, and §7's table shows the per-symbol costs.

**Mutants are derived, not declared** — the same argument §2 makes about the
dependency map, for the same reason. 3,819 are enumerated from the AST of the
three roots (numbers 380, strings 2,575, docstrings 311, booleans 183,
comparisons 370); 270 are drawn with seed 0. A hand-picked mutant set measures the
taste of whoever picked it. Docstring edits are kept and labelled rather than
excluded, because "how much does the suite run for a comment change" is a fair
question to ask of the tool.

**24 of 324 cases were not measurable** — the mutant broke the tree so the head
suite would not run. They are listed with their reason and excluded from the
recall, never counted as passes: silently treating an unmeasurable case as "no
miss" would raise the published number by dropping exactly the cases most likely
to contain one.

**The recall's observable base is 47/73 rows**, and that is the denominator to
read the recall against. The deterministic suite drives the runner's default
corpus less the rows with no committed caller script and the rows whose point is
a perturbed audio channel; the selector's denominator is the whole corpus. A row
outside the observable base cannot fail here, so a miss on one is never counted.

Splitting those 26 rows matters, because only one half is harmless:

| | rows | can a miss hide here? |
|---|---|---|
| observable — the study drives them | 47/73 | no; a miss is counted |
| invisible **and unmapped** | 18/73 | no; always selected, so never skippable |
| invisible **and mapped** | **8/73** | **yes, and it would go uncounted** |

The eight are the perturbed-voice rows (`voice-telephone-band-booking`,
`voice-noise-over-party-size` and six more). They are mapped, so the selector is
free to exclude them, and this study would never learn that it had. That is the
recall's real blind spot, it is printed in the limitation section next to the
number, and it is recorded in `calibration.json` as `blind_excludable_rows`.
Closing it needs the audio tier driven under the same base/head comparison, which
version one does not do.

**What this cannot establish**, printed next to the number every time: these are
enumerated constant-and-comparison edits, not a sample of real developer changes.
No mutant adds a function, moves a file, or introduces a dependency that lives
only in data. It is evidence about the selector's *logic*, not about its
behaviour on a real week's commits — which is what §5.3 was supposed to supply.

### 5.3 What the real history could not tell us, and what would fix it

The history study is the one you actually want, and it is reported here because
it came back **empty**, not in spite of it.

```
54 backtestable commit pairs, 0 failures.
55/62 commits runnable (the 7 oldest predate the harness).
```

Every commit in this repository's history is a curated, green commit. The suite's
verdicts — 47 scenarios, contract by contract — are **identical either side of
all 54 pairs**. There is no historical failure for a selector to miss, so the
recall's denominator is zero, and a recall over an empty denominator is not
1.000. It is **unknown**, and the gate treats unknown exactly as
`require_calibrated` treats a judge with no calibration attached: it refuses.

That is a property of how this repository was written, not of the selector, and
it is the single biggest limitation of everything in §5.2. **What would make the
measurement sound:**

* commits that were **red when pushed and green after a fix** — the red/green
  transition is the only thing that supplies a failure at all;
* roughly **30–50** of them, which is where a single miss would move the recall
  by a visible amount at this corpus's failure rate;
* spread across the kinds of change the map treats differently: one agent, a
  shared prompt, a tool signature, a check, and **at least a few config- or
  data-only changes**, which are precisely the cases §6 says stage 1 and stage 2
  provably cannot see;
* each with the suite runnable at both parent and child.

Until a history like that exists, §5.2 is the only evidence available and it is
synthetic. Saying so is not a hedge — a fabricated recall of 1.000 from three
cherry-picked commits would be worse than no number, because it would be believed.

### 5.4 The gate refuses three different ways

`require_calibrated_selector()` mirrors `lab.judges.registry.require_calibrated`,
and refuses when:

1. **nothing was ever measured** — no failure occurred in any case, so the miss
   rate is unknown (`SelectorNotCalibratedError`). This is what the history study
   alone produces;
2. **every measured failure was vacuous** — the selector had skipped nothing in
   every case that broke, so a perfect recall says only that it declined to
   narrow (`SelectorNotCalibratedError`);
3. **a regression was missed** — measured, non-vacuously, and below
   `min_recall` (`SelectorBelowThresholdError`, default `1.000`).

Strict in CI, advisory outside it: a developer narrowing a local run still has
the full suite ahead of them, so the danger is the unattended run nobody reads.
The bypass is `allow_uncalibrated=True`, a **keyword argument and nothing else** —
no environment variable, no config key, no flag. An override that can be set from
a shell becomes permanent within a month and nobody remembers turning it on; a
keyword argument puts it in a diff, in front of a reviewer. It logs a banner
either way.

`python -m lab.selection.calibrate` exits 1 both when the recall is below
threshold **and when it could not be measured at all**. A tool that exits 0
having learned nothing gets wired into CI and then believed.

---

## 6. What this provably cannot catch

A trace records what one recorded run **did**. It is not a record of what a
scenario **could** do. The map is therefore a **lower bound on the dependency set,
never an upper bound**: it proves "touched X", and it cannot prove "cannot touch
Y".

Concretely, none of these emit an event, so none of them appear in the map:

* **a config value read at run time** — the branch it selects is invisible;
* **a prompt fragment shared between agents** — the trace records which agent
  spoke, never which fragments its prompt was assembled from;
* **a dependency that lives only in data** — a policy sheet row, a fixture value;
* **a branch the recorded run did not take** — no event, no evidence.

Every one of those resolves to selecting everything, because the map's silence is
read as "no evidence", never as "unrelated". The override file is the seam for the
subset a human already knows about; a future stage 3 that reasons about the
remainder would slot in at the same point, but it is deliberately not built —
version one is fully deterministic.

There is also one known **over**-selection, worth naming because it is the first
thing to fix. Stage 1 compares ASTs, so a comment-only or formatting-only edit
produces zero changed symbols; from the selector's side that is indistinguishable
from a gap in stage 1's classifier, so it escalates to `unaccounted-file` and runs
everything. The fix is for stage 1 to publish "parsed both sides, no semantic
change" explicitly, rather than for the selector to infer it. Inferring it would be
a guess, and a guess here can only ever exclude the wrong thing.

---

## 7. Using it on a real change

Take a one-line edit inside `check_policy` in `tablemate/tools.py`:

```
$ python -m lab.selection --changed-since HEAD
selection HEAD..<working tree> in /…/tablemate-evals
  map        /…/lab/selection/trace_map.json
  corpus     73 scenario(s)
  change     files changed 1, global triggers 0, symbols changed 1
  verdict    SUBSET — 30/73 selected
  overrides  /…/scenarios/selection_overrides.yaml (none fired)

changed runtime names (1): check_policy

selected 30/73:
  adversarial-abuse-demands-free-meal  [adversarial]  trace-dependency: committed evidence names check_policy
  audio-barge-in-agent-yields  [audio]  unmapped-scenario: no committed trace names this scenario; there is no evidence to exclude it from any change, so always run it
  …

excluded:
  43/73 excluded; basis: no-overlap 43
    by suite: adversarial 10/12, edge 15/20, happy 12/15, voice 6/8
    every excluded scenario's committed evidence names none of: check_policy
```

30 selected: 12 whose committed evidence names `check_policy`, plus the 18-row
always-run audio floor. 43/73 skipped. **The exclusion summary is the part to
review** — a selector that only shows its inclusions is unreviewable, because the
inclusions are the safe half.

Then hand it to the runner's existing filters:

```
$ evallab run $(python -m lab.selection --changed-since HEAD --runner-args)
note: 18/30 selected scenario(s) are outside the corpus `evallab run` loads by
      default and cannot be addressed by any --scenario argument. They are NOT in
      the arguments below and they still need running, by the command that owns
      their tier:
  audio-barge-in-agent-yields, audio-barge-in-not-discovered, … (+13 more)
…
corpus coverage:  10/55 scenarios driven — 2 voice row(s) need the audio adapter,
                  0 unscripted, 43 filtered out by the command line
```

Only `--scenario` arguments are emitted, never `--suite` or `--tag`. The runner
ANDs its filters, so a `--suite` sitting next to a `--scenario` could only shrink
the set — and shrinking is the one direction this tool may not err in. Explicit
ids are also exact: they do not depend on what the runner's default suite list
happens to be today.

**Two corpora, and why the emit is not the selection.** The selector reasons over
`ALL_SUITES`, because a row it cannot see is a row it cannot protect. `evallab
run` calls `loader.load_corpus()` with no `suites` argument, which is the four
text suites — the audio tier is driven by its own command and its ids are not
addressable through `evallab run` under *any* flag, `--suite audio` included.

So a selection is not in general expressible as one runner invocation, and the
18-row always-run floor is in every selection. Emitting the whole list looks like
the conservative choice and is the opposite of one: the runner rejects the first
unknown id and aborts with `no such scenario(s)`, so **nothing runs at all**, and
the operator's next move is to delete ids by hand until the command starts — which
is exactly how an always-run floor gets quietly deleted. `--runner-args` therefore
emits what this runner can address and prints the remainder to **stderr**, named,
counted, and flagged as still needing to run. A withheld row is not a skipped row:
that runner would not have run it under any arguments, including none.

The boundary is read by making the same `load_corpus()` call the runner makes,
not by naming suites in the selector, so it widens automatically if the runner's
default corpus ever does. Two cases refuse outright — printing nothing to stdout
and exiting 2, which leaves the runner unfiltered and therefore over-running:
the corpus will not load, or every selected id is outside it.

### What a change of each kind costs

Every row from `python -m lab.selection --calibrate --json`, one probe per
definition site:

| changed symbol | selected |
|---|---|
| `tablemate/tools.py::cancel_booking` | 21/73 |
| `tablemate/tools.py::modify_booking` | 25/73 |
| `tablemate/tools.py::check_policy` | 30/73 |
| `tablemate/agents.py::PolicyAgent` | 34/73 |
| `tablemate/agents.py::ModificationAgent` | 35/73 |
| `tablemate/tools.py::create_booking` | 49/73 |
| `tablemate/tools.py::search_tables` | 55/73 |
| `tablemate/agents.py::BookingAgent` | 62/73 |
| `tablemate/agents.py::GreeterAgent` | 73/73 |

The floor of 18 is in every row. `GreeterAgent` at 73/73 is not a failure — every
recorded call starts at the greeter, so a change there really can reach
everything, and the tool saying so is it working.

---

## 8. Files, commands, and what is still to do

```
lab/selection/diff.py          stage 1   python -m lab.selection.diff
lab/selection/trace_map.py     stage 2   python -m lab.selection.trace_map
lab/selection/trace_map.json   stage 2   generated, committed
lab/selection/select.py        stage 3   python -m lab.selection
lab/selection/calibrate.py     stage 4   python -m lab.selection.calibrate
lab/selection/calibration.json stage 4   generated by --write; commits, date, command
scenarios/selection_overrides.yaml       additive widenings; ships empty
tests/test_selection_*.py                the fail-safe paths, one test each
```

Every number in this document is reproducible:

```
python -m lab.selection.trace_map          # §3 coverage
python -m lab.selection --calibrate        # §5.1, and the §7 table via --json
python -m lab.selection --changed-since HEAD~1
python -m lab.selection.calibrate --sample 150 --enrich 120 --write   # §5.2, §5.3
pytest tests/test_selection_select.py -q      # 73 tests
pytest tests/test_selection_calibrate.py -q   # 70 tests
```

§5.2 takes a few minutes: it runs the deterministic suite once per case (0.4s
each, 47 scenarios, no key), plus once per commit for §5.3. Nothing it does
writes to the repository it measures — every tree is extracted with `git archive`
into a temporary directory, never a worktree and never a checkout.

Four follow-ups, all outside this layer:

* **Wire it into `evallab`.** The CLI is owned elsewhere and is mid-change; the
  entry point stays `python -m lab.selection` until that lands.
* **Add `python -m lab.selection.trace_map --check` to CI.** Without it the map
  can rot: the tests catch drift locally, but the CI gate is what forces the
  regeneration diff into the pull request, which is half the feature's value.
* **Re-measure §5.2 when the map grows.** The recall is a property of a specific
  map and a specific corpus. Add a scenario or resolve a tenth runtime name and
  the number is stale, so `python -m lab.selection.calibrate --write` belongs on
  the same schedule as regenerating the map.
* **Do not gate on selection until §5.3 has real failures.** §5.2 passes and the
  gate will open on it, but the evidence is synthetic. Until this repository's
  history contains genuine red→green commits, treat a narrowed run as fast
  feedback and keep a full run before anything lands.
