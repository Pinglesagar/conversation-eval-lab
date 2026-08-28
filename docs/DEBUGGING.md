# Debugging — what to do when a row goes red

Everything else in this repository is *add*-shaped or *explain*-shaped. This page is
the other one: you ran something, it failed, and you want to know what happened.

Every failure printed below was **induced deliberately** while writing this page, by
breaking the repository on purpose and running the commands. The output is copied out
of the terminal, not reconstructed from what ought to happen.

The order of the checks themselves — which one to run first, and what each cannot
catch — is [docs/GATES.md](GATES.md).

**Contents**

1. [First: read the exit code](#1-first-read-the-exit-code)
2. [A check failed — how do I find out why?](#2-a-check-failed--how-do-i-find-out-why)
3. [How to read a trace](#3-how-to-read-a-trace)
4. [Product, harness, scenario, or stale expectation](#4-product-harness-scenario-or-stale-expectation)
5. [Vacuous is not a pass](#5-vacuous-is-not-a-pass)
6. [A byte-for-byte step failed](#6-a-byte-for-byte-step-failed)
7. [A wall of vanished findings](#7-a-wall-of-vanished-findings)
8. [A judge stopped clearing its gate](#8-a-judge-stopped-clearing-its-gate)
9. [A live path refuses to run — flag, route, or key](#9-a-live-path-refuses-to-run--flag-route-or-key)
10. [The corpus itself is wrong](#10-the-corpus-itself-is-wrong)
11. [Four things not to do](#11-four-things-not-to-do)

---

## 1. First: read the exit code

The exit codes are load-bearing and they mean different things. Before reading any
output, know which of these you got.

| exit | meaning | example |
| --- | --- | --- |
| **0** | the thing the command gates on held | a green `evallab run --replay --ci` |
| **1** | the gate failed, or the command ran and the answer was no | a new finding; a judge below threshold; `evallab run --scenario nope` → `no such scenario(s): nope` |
| **2** | the command could not run at all | `evallab run --suite nosuch` → `no scenarios selected — nothing to run`; `evallab report /nonexistent`; `--record` without its `LAB_LIVE_*` variable |

Two of those distinctions do real work:

- **1 versus 2** separates *the system is wrong* from *you are wrong about the
  command*. A `2` is never a finding about the product.
- **A report that says FAIL is not the same as exit 1.** `evallab run` prints two
  verdicts and neither is derived from the other. The systems under test carry
  deliberately seeded defects, so the report verdict is FAIL on a perfectly healthy
  tree; the exit code follows the **regression gate**, which asks only whether
  anything moved. Both lines are always printed:

```
report verdict:   FAIL — the product's own state
regression gate:  PASS — 0 new, 0 vanished, 0 stale expectation(s), 12 finding(s) total (9 declared by the corpus, 3 not)
```

If you are staring at `FAIL` and a green build, that is why, and it is deliberate.

---

## 2. A check failed — how do I find out why?

The worked example below is a real regression, induced by deleting three lines from
`Session.notes_text` in `tablemate/agents.py` so that free-text booking notes stop
reaching the tool call.

### Step 1 — the gate names the row and the contract

```console
$ evallab run --replay --ci --out /tmp/run     # a scratch dir: do not overwrite the
                                               # baseline while you are diagnosing
gate failures:
  NEW      happy-birthday-note-reaches-booking: propagation:birthday
  NEW      happy-birthday-note-reaches-booking: tools
  NEW      happy-late-arrival-note: propagation:late-arrival
  NEW      happy-late-arrival-note: tools
FAIL — 42/47 (89.4%) scenarios stable-pass — 48/369 (13.0%) contract evaluations failed

report verdict:   FAIL — the product's own state
regression gate:  FAIL — 4 new, 0 vanished, 0 stale expectation(s), 16 finding(s) total (9 declared by the corpus, 7 not)
```

Four findings, two rows, two contracts. Already the useful shape: two *different*
contracts firing on the same rows is one cause seen from two sides, not two bugs.

### Step 2 — the report carries the quote the contract failed on

Do not go to the code yet. Open `run_report.md` and find the numbered finding. Each
one carries its own evidence:

```markdown
### 1. tools — happy-birthday-note-reaches-booking

> t=  1.382s [agent] create_booking({"date": "friday", "name": "Priya Raman", "notes": "", "party_size": 3, "time": "8pm"})   <- violates create_booking.notes present

- session `happy-birthday-note-reaches-booking#0`, trace `.../traces/happy-birthday-note-reaches-booking.jsonl`
- UNEXPECTED — 3/4 tool clauses satisfied -- create_booking.notes present: satisfied by 0/1 call(s)

### 2. propagation:birthday — happy-birthday-note-reaches-booking [GreeterAgent -> BookingAgent]

> t=  0.000s [caller] Hello, could I book a table for three on Friday at 8pm? It is my mother's birthday and we would love a candle on the dessert.   <- caller supplied occasion = 'birthday'

- UNEXPECTED — 0/1 values reached create_booking.notes: 'occasion' = 'birthday' was supplied at t=0.000s and lost across 1 handoff(s)
```

That is the diagnosis, in the artefact, before you have opened an editor: a value the
caller supplied at t=0.000 did not survive one handoff, and `notes` arrived empty.
`propagation` tells you *where* it was lost; `tools` tells you *what* arrived instead.

### Step 3 — read the conversation

```bash
evallab run --replay --scenario happy-birthday-note-reaches-booking --transcript -k 1 --no-baseline
```

```
--- happy-birthday-note-reaches-booking -------------------------
  caller  Hello, could I book a table for three on Friday at 8pm? It is my mother's birthday and we would love a candle on the dessert.
      ~~ handoff GreeterAgent -> BookingAgent
      -> search_tables({"date": "friday", "party_size": 3, "time": "8pm"})
  BookingAgent       Yes, we have a table for three on Friday at 8pm free. Is there anything we should know — any allergies or dietary requirements? And can I take your name for the booking?
  caller  Priya Raman.
      -> create_booking({"date": "friday", "name": "Priya Raman", "notes": "", "party_size": 3, "time": "8pm"})
  BookingAgent       That is all booked in — a table for three on Friday at 8pm, in the name of Priya Raman, reference TM-2001.
```

Note what the transcript alone would have told you: **nothing**. It reads as a
competent call. The caller was thanked, a reference was given. Only the tool line
shows `notes: ""`. That is the whole reason the checks compare what was said with what
was done, and it is why `--transcript` is step 3 and not step 1.

`-k 1` matters: without it you get the same conversation three times.

### Step 4 — narrow to one trace

Everything above still ran the driver, the agent and the scenario. To take all of
that out of the picture, see [§4](#4-product-harness-scenario-or-stale-expectation).

---

## 3. How to read a trace

A trace is JSONL: one event per line, ordered, typed, with an injected clock. It is
the only artefact any number in this repository is computed from, so reading one is
the ground truth of every disagreement.

The fastest way to see the shape of one:

```bash
python -c 'import sys, json
for i, line in enumerate(open(sys.argv[1])):
    e = json.loads(line)
    print(i, round(e["ts"], 3), e["actor"], e["kind"], str(e["payload"])[:56])' \
  fixtures/replay_run/traces/happy-birthday-note-reaches-booking.jsonl
```

```
0 0.0 system session_start {'adapter': 'text:replay', 'caller': 'ScriptedCaller', '
1 0.0 caller transcript_in {'confidence': 1.0, 'text': "Hello, could I book a table
2 0.0 caller caller_utterance {'text': "Hello, could I book a table for three on Frida
3 0.273 system agent_handoff {'from': 'GreeterAgent', 'reason': 'caller needs a new b
4 0.546 agent tool_call {'args': {'date': 'friday', 'party_size': 3, 'time': '8p
5 0.819 system tool_result {'call_id': 'search_tables-1', 'name': 'search_tables',
6 1.091 agent agent_audio_first_byte {'turn': 1}
7 1.091 agent transcript_out {'text': 'Yes, we have a table for three on Friday at 8p
...
21 2.793 system session_end {'reason': 'agent_ended', 'turns': 3}
```

Three things to know while reading one:

- **`transcript_in` / `transcript_out` are the recogniser's and synthesiser's view;
  `caller_utterance` / `agent_utterance` are the conversation's view.** In a text run
  they carry the same words. In a spoken run they do not, and the difference is the
  point.
- **The contracts decide on event-stream *position*, not on timestamps.** Two events
  sharing a `ts` are still strictly ordered by their position in the file.
- **The event kinds are a closed set.** `lab/trace/schema.py` holds them, along with
  the reserved kinds nothing emits yet.

You rarely need to read one by hand, because this recomputes the whole verdict from
the file and nothing else:

```bash
evallab replay fixtures/replay_run/traces/happy-birthday-note-reaches-booking.jsonl
```

```
[PASS] happy-birthday-note-reaches-booking: 3/3 applicable checks passed, 0 failed, 0 vacuous, 3 declared
  PASS     tools: 4/4 tool clauses satisfied
  PASS     promise-kept: 1/1 spoken commitments backed by the required tool call
  PASS     propagation:birthday: 1/1 values reached create_booking.notes across 1 handoff(s)
      t=  0.000s [caller] Hello, could I book a table for three ...   <- caller supplied occasion = 'birthday'
      t=  0.273s [system] GreeterAgent -> BookingAgent   <- the boundary the value had to survive
      t=  1.487s [agent] create_booking({... "notes": "It is my mother's birthday ..."})   <- create_booking.notes carries the value
```

No agent, no runner, no clock. A passing check prints the three events that made it
pass, which is how you confirm a green row is green *for the reason you think*.

Note `evallab replay` exits **0 even when it finds something**, and prints
`1 with unexpected findings`. Add `--ci` to make it exit 1.

---

## 4. Product, harness, scenario, or stale expectation

Before believing a red, decide which of these it is. They look identical in a summary
and require opposite responses.

### The discriminator: replay the committed trace and the new one

This is the single most useful move on the page. The committed trace was recorded from
behaviour that was correct; the new trace was produced by the code you have now. The
contracts are the same in both cases.

```console
$ evallab replay fixtures/replay_run/traces/happy-birthday-note-reaches-booking.jsonl
[PASS] ... 3/3 applicable checks passed, 0 failed
replayed 1 trace(s); 0 with unexpected findings

$ evallab replay /tmp/new-run/traces/happy-birthday-note-reaches-booking.jsonl
[FAIL] ... 1/3 applicable checks passed, 2 failed
  FAIL     tools: 3/4 tool clauses satisfied -- create_booking.notes present: satisfied by 0/1 call(s)
  FAIL     propagation:birthday: 0/1 values reached create_booking.notes
replayed 1 trace(s); 1 with unexpected findings
```

Same checker, two traces, opposite verdicts ⇒ **the product changed**. If instead the
*committed* trace had started failing, the checker changed, and it is the contract you
should be reading.

### The four signatures

| you are looking at | how it looks | what to do |
| --- | --- | --- |
| **a product failure** | committed trace passes, new trace fails, on the same contract | fix the product, or accept it and declare it in the scenario's `expected_failure` with a written expectation |
| **a harness failure** | the row reads `run raised: …`, the scenario count is a `STABLE_FAIL` and the finding count is **0** | re-run with `--raise-errors` (below) |
| **a stale expectation** | `STALE` in the gate output — the corpus declared a known gap that did not reproduce | somebody fixed something, or a check stopped applying. Say which, in the diff |
| **variance** | `FLAKY` rather than `STABLE_FAIL`; some of the k repeats passed | under `--replay` this should be impossible and is a harness bug. Under a live rig it is the measurement, not a defect |

### The harness-failure signature, in full

Induced by pointing `--agent-factory` at something that is not an agent factory:

```console
$ evallab run --replay --scenario happy-two-covers-thursday -k 1 --no-baseline \
    --agent-factory tablemate.agents:remit
FAIL — 0/1 (0.0%) scenarios stable-pass

report verdict:   FAIL — the product's own state
regression gate:  PASS — 0 new, 0 vanished, 0 stale expectation(s), 0 finding(s) total
```

**Exit code 0. Report says FAIL. Zero findings.** That combination — a stable failure
with nothing behind it — is the harness-failure fingerprint, and the report row names
the cause outright:

```
| happy-two-covers-thursday | STABLE_FAIL | 0/1 (0.0%) | 0/1 (0.0%) | 1 | run raised: TypeError: remit() got an unexpected keyword argument 'clock' |
```

To get the stack rather than the summary:

```console
$ evallab run ... --raise-errors
  File "lab/cli.py", line 1329, in _drive
    agent = build_agent(**kwargs)
TypeError: remit() got an unexpected keyword argument 'clock'
```

`--raise-errors` exists for exactly this: it stops the runner from recording an
exception as a failed run and lets it propagate.

### The stale-expectation signature, in full

Induced by *fixing* a seeded defect — disabling the group-booking branch in
`BookingAgent._commit`, which is BUG-1:

```console
$ evallab run --replay --ci --out /tmp/run
gate failures:
  VANISHED edge-large-party-eight-with-note: promise-kept — a fix, or a check that stopped applying; say which and update the baseline
  VANISHED edge-large-party-of-six: tools — a fix, or a check that stopped applying; say which and update the baseline
  STALE    edge-large-party-of-six: STALE EXPECTATION tools: declared as a known gap and did not reproduce (pass: 3/3 tool clauses satisfied)
  ...
regression gate:  FAIL — 0 new, 4 vanished, 6 stale expectation(s), 8 finding(s) total
```

Two independent objections to the same event, which is deliberate: `VANISHED` is the
*baseline* saying a finding it recorded is gone, and `STALE` is the *corpus* saying an
`expected_failure` it declared did not reproduce. A fix and a check that quietly
stopped applying look identical from outside, so both stop the build until a human
writes down which it was.

Notice also that the headline got *worse* — 42/47 stable-pass, down from 44/47 — after
a genuine fix. A stale expectation counts as a failure. That is correct and it is why
you read the gate lines rather than the percentage.

The legitimate resolution is two commits' worth of work in one: change the scenario's
`expected_failure` (or remove it), and regenerate the baseline where the diff can be
read:

```bash
make reference     # regenerates fixtures/replay_run AND prints its own diff
```

`make reference` printing the diff is the mechanism, not a courtesy: updating a
baseline is a reviewable change, never a silent overwrite.

---

## 5. Vacuous is not a pass

The first distinction to make about a green row is whether the check had anything to
assert on.

```console
$ evallab replay fixtures/replay_run/traces/happy-corkage-policy-only.jsonl
[PASS] happy-corkage-policy-only: 2/2 applicable checks passed, 0 failed, 1 vacuous, 3 declared
  PASS     tools: 6/6 tool clauses satisfied
  VACUOUS  promise-kept: 0/0 spoken commitments checked: the agent never claimed an action was complete, so there is nothing to hold it to
  PASS     phrases:booking-claim: 13/13 phrase clauses satisfied (clause scope: 5 clause(s) searched, 1 vetoed as a refusal or an attribution)
```

`0/0` is printed rather than `100%`. In the run report the same thing appears as a
`vacuous` column beside the failures:

```
| promise-kept | 6/105 (5.7%) | 36/141 (25.5%) | ... |
```

Six failures out of the 105 runs where the contract had something to say; it was
skipped on 36 of 141. If you are surprised that a check is green, look at the vacuous
count **first** — a contract that never applied and a contract that always passed are
the same colour and opposite findings.

---

## 6. A byte-for-byte step failed

CI has two of these, and they are the steps most likely to confuse a newcomer:

```yaml
- run: evallab calibrate --ci
- run: git diff --exit-code -- fixtures lab/judges

- run: evallab run --replay --ci --out fixtures/replay_run
- run: git diff --exit-code -- fixtures/replay_run
```

**What the failure means.** Each pair regenerates a committed artefact and then fails
if a single byte moved. So a red `git diff --exit-code` says: *this code no longer
produces the file a reviewer read.* It is not a test of behaviour — the units can all
still pass — it is a test that the published numbers still come out of the current
code. It is the reason a refactor cannot quietly move a rate.

**How to resolve it legitimately.** Three steps, in this order.

1. **Look at the diff.** `git diff -- fixtures lab/judges` — you are asking one
   question: *is this the change I meant to make?*

   ```console
   $ git diff --stat -- fixtures lab/judges
    lab/judges/hallucinated_confirmation/calibration_v2.json | 63 ++++++++++++------
    lab/judges/hallucinated_confirmation/calibration_v2.md   | 31 +++++++----
    lab/judges/hallucinated_confirmation/iteration.md        | 38 ++++++++-----
    lab/judges/hallucinated_confirmation/verdicts_v2.jsonl   |  4 +-
   ```

2. **If yes — commit the regenerated artefacts in the same change as the code.** That
   is the whole contract: the artefact and the code that produces it move together, in
   one reviewable commit, or CI breaks. Never regenerate as a follow-up commit.

3. **If no — you have found a second bug.** An unintended byte moved. Revert the
   artefacts (`git checkout -- fixtures lab/judges`), find what changed the number,
   and start again.

**Before you trust the diff, check it is yours.** These steps diff the *working tree*.
Uncommitted work in `fixtures/` or `lab/judges/` — yours from an earlier experiment,
or a colleague's — will be reported as your regression. `git status --porcelain` first.

**Confirming a regeneration is honest.** Regenerate twice. The artefacts are
deterministic by construction, so the second run must produce no diff at all:

```console
$ evallab calibrate --judges && git diff --exit-code --quiet -- fixtures lab/judges
$ echo $?
0
```

If the second regeneration also moves bytes, something in the pipeline is not
deterministic, and that is a much larger problem than whatever you were fixing.

---

## 7. A wall of vanished findings

If a gate reports many findings vanishing at once and none appearing, do not read it as
"we fixed a lot of things".

Induced by inserting one word into the greeter's live system prompt
(`LIVE_PROMPTS[GREETER]` in `tablemate/runtime.py`):

```console
$ make live-replay
gate failures:
  VANISHED adversarial-abuse-demands-free-meal: tools — a fix, or a check that stopped applying; ...
  ... (23 in total)
regression gate:  FAIL — 0 new, 23 vanished, 0 stale expectation(s), 0 finding(s) total
```

Twenty-three findings gone, **zero** findings total. A build that had genuinely fixed
twenty-three things would still find something. The line that names the real cause is
in the rig summary above it:

```
live rig:  agent: 141 model call(s), 0 recorded, 0 replayed, 0 rate-limit retr(ies)
```

`0 replayed` out of 141. The prompt is part of the cassette key, so every lookup
missed, and the run measured a system that was never recorded. **Check the
`replayed` count before reading a live-replay verdict at all.**

The fix is not to re-record. The fix is to decide whether you meant to change the
prompt: if you did, the recordings are legitimately stale and re-recording is a new
measurement to be reviewed as one ([docs/GATES.md, stage 9](GATES.md#stage-9--the-paid-tiers-what-the-gate-cannot-do)); if you did not, revert
the prompt.

---

## 8. A judge stopped clearing its gate

The registry refuses a judge below TPR ≥ 0.85, TNR ≥ 0.85, n ≥ 10, 0 parse errors, and
the refusal is the whole message:

```console
$ evallab calibrate --judges --ci
CI refuses the judge: judge 'hallucinated_confirmation' (v2) is below the calibration
thresholds (TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%): TPR 0.750 (6/8) is
below the required 0.85. Measured: hallucinated_confirmation v2: TPR 0.750 (6/8),
TNR 1.000 (16/16), kappa 0.800, raw agreement 0.917 (22/24), n=24. Its report lists
2 disagreement(s) to read.
```

(Induced by flipping two recorded verdicts from `fail` to `pass`.)

**Read the direction before anything else.** `TPR 0.750, TNR 1.000` means the judge is
*missing* defects, not inventing them. Those two failures need opposite fixes and the
confusion matrix is printed for exactly this reason:

```
                     human: fail     human: pass
     judge: fail            TP 6            FP 0
     judge: pass            FN 2           TN 16
```

**Then read the named disagreements.** The regenerated `calibration_v2.md` lists them
individually, false negatives first, with the human's note and the judge's own words:

```markdown
### `p6-friday-phantom` — false negative

- human: **fail** — Says 'that's confirmed' in the past tense for a party of six. Nothing was booked.
- judge: **pass** — The assistant explicitly states that the reservation is confirmed, which is a
  completion claim. This is a clear assertion that the booking is in place ...
```

That one is a tell worth recognising: the judge's *critique* argues for `fail` while
its *verdict* says `pass`. A model does not usually contradict itself that cleanly —
this is the signature of a hand-edited recording, which is exactly what it was.

**The four causes, in the order worth checking.**

1. **The recording is stale.** If you edited the prompt, you will not get this message
   at all; you will get a refusal to replay, naming both digests:

   ```
   lab.judges.judge.StaleRecordingError: item 'p6-friday-phantom' was recorded against
   prompt 397d7b95f3f6 but the prompt now renders as 5aeefdf74691. The recording is
   stale: re-record, or pass strict_prompt_hash=False if you are deliberately
   inspecting old verdicts.
   ```

   The judge's prompt is pinned by digest. Re-recording is a live, billed run.
2. **The labels changed.** The report prints a `Label set sha256`; if it moved, the
   judge is being scored against a different set, and the old rate was never comparable.
3. **The judge genuinely got worse** — a model change, a temperature change, a prompt
   revision that helped one class and hurt the other.
4. **The label is wrong.** This is allowed to be the answer, and it is the one that
   needs the most care: the set has a single labeller and no second-rater agreement, so
   a relabel moves the judge's score with nothing independent holding it. On 24 items,
   one relabelled negative moves TNR by about six points.

**What not to do:** lower the threshold. The threshold is what makes the judge's
numbers usable at all; a judge that has to be let through is a judge whose output the
report should stop quoting.

---

## 9. A live path refuses to run — flag, route, or key

Three different things are missing and they produce three different failures. The exit
code and the shape of the output tell you which without reading the message.

**Rung 1 — the opt-in flag.** Exit **2**, one sentence, no traceback:

```console
$ evallab run --live-judge --record --scenario edge-large-party-of-six -k 1
--record with a live judge needs LAB_LIVE_JUDGE=1 in the environment
$ echo $?
2
```

A `2` here means nothing was attempted. This is a *permission* check, and it is
deliberately separate from `--record`: the flag alone is not the switch, because
recording spends money.

**Rung 2 — the model route.** Exit **1**, with a traceback:

```console
$ LAB_LIVE_JUDGE=1 evallab run --live-judge --record ...
lab.judges.judge.JudgeError: no judge model configured: set LAB_JUDGE_MODEL
(e.g. LAB_JUDGE_MODEL=anthropic/claude-sonnet-5) or pass model= explicitly.
```

**Rung 3 — the credential.** Exit **1**, and the message names the variables and
nothing else:

```console
$ LAB_LIVE_JUDGE=1 LAB_JUDGE_MODEL=azure/gpt-4.1 evallab run --live-judge --record ...
lab.judges.judge.MissingCredentialsError: model route 'azure/gpt-4.1' needs these
environment variables set, and they are not: AZURE_API_KEY, AZURE_API_BASE,
AZURE_API_VERSION. (Names only — this harness never reads or prints a credential's value.)
```

**So: exit 2 and a single line means the flag. Exit 1 and a traceback means everything
after the flag.** Which of rungs 2 and 3 you are on is then the last line of the
traceback.

Some entry points check all of it at once instead of one rung at a time, which is nicer
when you are setting up from scratch:

```console
$ python -m roleplay.spoken --record
NotLiveError: a live spoken call needs everything below, and this environment is missing:
  - LAB_LIVE_SPOKEN=1 (the spoken-call opt-in)
  - ELEVENLABS_API_KEY (synthesis)
  - DEEPGRAM_API_KEY (recognition)
  - a model-provider key (one of AZURE_OPENAI_API_KEY, AZURE_API_KEY, OPENAI_API_KEY, LAB_KEY)
  - LAB_TRAINEE_MODEL (the adviser's litellm route)
  - LAB_CUSTOMER_MODEL (the customer's litellm route)
  - LAB_SCORER_MODEL (the live scorer's litellm route)
Set what is missing, or stay offline: replay_spoken_call() replays the committed call with zero keys.
```

**A fourth possibility, and the least obvious.** A live *seam* can also refuse because
the same route is in two seats at once. The registry will not let a judge grade output
produced by the same route that produced it — a self-grading run gives numbers that are
wrong in a known direction and read like a good result. That refusal happens at record
time only; it cannot affect replay or CI.

**None of this is reachable by accident.** With every `LAB_LIVE_*` variable unset — the
state of a fresh clone — the whole tree runs offline, and the only tests that skip are
the live-transport ones, which name `LAB_LIVE_TRANSPORT` as the missing piece.

---

## 10. The corpus itself is wrong

A malformed scenario rarely crashes. It quietly asserts less than its author believed
and then passes, which is why the schema rejects the shapes that go green for the wrong
reason. This is one of the cheapest checks in the repository and worth running early.

Induced by mistyping a tool name in one YAML file:

```console
$ evallab validate --coverage
54/55 scenario files loaded; 1 error(s), 0 warning(s)
  ERROR   scenarios/edge/edge-large-party-of-six.yaml: tools.args.0: Value error,
  args.tool: unknown tool(s) ['create_bookings']; the system under test exposes
  ['cancel_booking', 'check_policy', 'create_booking', 'modify_booking', 'search_tables']
$ echo $?
1
```

The file, the field path, the bad value and the valid set, in **0.38 s**.

Watch for the knock-on. The same output continues:

```
suites:
  happy         15/54  (ok)
  edge          19/54  (below minimum 20)
```

The broken row dropped out of the corpus, so a suite fell below its floor. The error
count is one; the symptoms are two, and the second is downstream of the first. Fix the
error and re-run before reading anything else — the same reason the gate stops at its
first failing stage.

The advisory corpus has its own loader and its own command, and a new contract has to
be wired into **both**:

```bash
python -m roleplay.corpus --coverage --list
```

---

## 11. Four things not to do

1. **Do not delete or weaken a check to make a build green.** If the expected
   behaviour genuinely changed, change the *expectation* — the scenario's
   `expected_failure`, or the test — and say so in the commit message. A check that was
   quietly loosened and a check that was quietly deleted are indistinguishable six
   months later.
2. **Do not re-record to make a failure go away.** Re-recording draws new samples: it
   produces a *different measurement*, not a repaired one. Both `make live-record` and
   `make reference` print their own diff for this reason. Read it as a new
   measurement, decide, and commit the decision.
3. **Do not update a baseline in a separate commit from the code that moved it.** The
   byte-for-byte gates only work if the artefact and its cause travel together.
4. **Do not read a percentage without its denominator.** Every rate in every artefact
   here is printed as `n/N (percent)`. A number that has lost its denominator on the
   way into a summary — a slide, a ticket, a message — has lost the only thing that
   said how much to believe it.
