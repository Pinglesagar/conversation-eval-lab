# The gate — what to run, in what order, and what each stage cannot catch

This repository has thirty-two `make` targets. Every one of them is documented
individually; until now nothing said which you run **first**, or why.

This page is the procedure. One target runs it:

```bash
make gate
```

Eight stages, in cost order, stopping at the first failure. Nothing in it needs an
API key, spends a synthesis character or opens a socket.

When a stage goes red, [docs/DEBUGGING.md](DEBUGGING.md) is the page that tells you
what the message means and how to find out which of the four possible causes you
are actually looking at.

---

## The one fact to read before anything else

> **Replay is blind to a prompt change.**
>
> Every stage in the gate below reads a recording. The recording was made against
> the prompt as it was on the day it was recorded. Change a prompt, a persona or a
> rubric and the gate stays green — not because the change is safe, but because
> nothing in stages 1–8 ever asks a model anything.

This is not a theory about the design; it is measured. Editing the greeter's live
system prompt in `tablemate/runtime.py` (`LIVE_PROMPTS[GREETER]`, one word inserted)
and re-running the offline stages:

| command | exit |
| --- | --- |
| `evallab validate --coverage` | 0 |
| `evallab replay --failures-only` | 0 |
| `evallab run --replay --ci` | 0 |
| `python -m roleplay.demo` | 0 |
| `python -m ragcheck` | 0 |
| `python -m roleplay.spoken` | 0 |

Six green stages on a change to the words the agent is given. The one command that
noticed is `make live-replay`, which exits 1 — and *how* it fails is itself the
lesson, because it does not say "the prompt changed". It reports **23 findings that
vanished at once**, and the only line that names the real cause is in the rig
summary:

```
live rig:  agent: 141 model call(s), 0 recorded, 0 replayed, 0 rate-limit retr(ies)
```

`0 replayed` out of 141: every cassette lookup missed, because the prompt is part of
the cassette key. See [docs/DEBUGGING.md §7](DEBUGGING.md#7-a-wall-of-vanished-findings).

**There is one exception, and it is worth knowing.** The judge's prompt is pinned by
digest, so an edit to `lab/judges/hallucinated_confirmation/prompt_v2.md` does not go
quietly — `make calibrate` refuses:

```
lab.judges.judge.StaleRecordingError: item 'p6-friday-phantom' was recorded against
prompt 397d7b95f3f6 but the prompt now renders as 5aeefdf74691. The recording is
stale: re-record, or pass strict_prompt_hash=False if you are deliberately
inspecting old verdicts.
```

So the honest statement is narrower than the slogan and more useful: **the recordings
that are keyed on a prompt fail loudly when it changes; the recordings that are not
stay green.** The judge's verdicts and the live agent cassette are keyed. The
scripted offline tier has no prompt at all, so there is nothing for it to key on —
and that is the tier the gate is made of.

Which changes therefore require going live is [stage 9](#stage-9--the-paid-tiers-what-the-gate-cannot-do).

---

## The eight stages

Costs are wall-clock, best of three consecutive runs, on the machine this was written
on (`.venv` Python 3.12, warm filesystem). Every one is reproducible by putting `time`
in front of the command in the middle column.

| # | stage | command | cost | what it proves | what it **cannot** catch |
| --- | --- | --- | --- | --- | --- |
| 1 | lint | `ruff check --select E9,F63,F7,F82 --exclude .venv .` | **0.05 s** | no syntax error, no undefined name, anywhere in seven packages | anything about behaviour. It is deliberately not a style gate |
| 2 | the corpora | `evallab validate --coverage`<br>`python -m roleplay.corpus --coverage --list` | **0.38 s**<br>**0.47 s** | every scenario matches its schema: no typo'd tool name, no tracked field the caller never says, no `expected_failure` naming a contract the row does not declare | whether the product is right. A perfectly valid scenario can assert nothing useful |
| 3 | the traces | `evallab replay --failures-only` | **0.45 s** | every committed trace still produces the verdict committed beside it, with no agent, no runner and no clock involved | anything about code that runs *before* a trace exists — the driver, the adapter, the agent |
| 4 | the case study | `evallab run --replay --ci --out fixtures/replay_run`<br>`git diff --exit-code -- fixtures/replay_run` | **0.81 s** | the regression gate: no new finding, no *vanished* finding, no stale `expected_failure`, k byte-identical repeats — and then that the whole report reproduces to the byte | a prompt change (see above). Also: it gates on *change*, so a defect that was already there and is still there is green by design |
| 5 | the calibration gates | `evallab calibrate --ci`<br>`git diff --exit-code -- fixtures lab/judges` | **0.24 s** | the stopwatch is within tolerance and the judge clears TPR ≥ 0.85, TNR ≥ 0.85, n ≥ 10, 0 parse errors — then that both artefacts regenerate byte-identically | whether the *labels* are right. A judge scoring 1.000 against a single labeller's set is agreeing with one person, and the repository says so rather than measuring it |
| 6 | the error analysis | `python -m error_analysis.pareto --check --no-chart` | **0.35 s** | the hand-assigned failure modes still count to the same totals as the artefacts they were coded from | that the codes are the *right* codes. This is an agreement check, not a validity one |
| 7 | the other packs | `python -m roleplay.demo`<br>`python -m roleplay.regime_eval --divergence --shadow`<br>`python -m ragcheck`<br>`python -m roleplay.spoken`<br>`python -m lab.voice.transport.report`<br>`make live-replay`<br>`python -m tablemate --score fixtures/live_full`<br>`python -m scripts.make_audio_fixtures --check` | **0.72 s**<br>**0.90 s**<br>**0.62 s**<br>**0.45 s**<br>**0.47 s**<br>**0.98 s**<br>**0.66 s**<br>**0.81 s** | that the second domain, the retrieval tier, the spoken call, the WebRTC tier, the live text run and the audio fixtures all still reproduce from their recordings | the same blindness as stage 4, once per tier. Every one of these reads a recording |
| 8 | the offline suite | `pytest -q` | **73 s** | the units; the committed artefacts of stages 4, 5 and 7 a second time, through tests of their own; and code that no committed artefact reaches at all | nothing that is not asserted. It is the broadest stage and the slowest, which is exactly why it is last |

**Sum of the column, stages 1–7: 8.4 s for fifteen commands**, plus the two
`git diff --exit-code` calls at 0.03 s each. Stage 8 alone is **73 s**. That ratio is
the entire argument for this ordering: running the whole artefact surface costs less
than deciding whether to, so there is no version of "I'll skip the cheap ones" that
saves anything.

Two things are deliberately **not** in `make gate`:

- `make coverage` — **134 s**, two passes of the suite. It is a number, not a gate;
  the Makefile explains why it has no threshold.
- `make audio-suite` — it is `pytest tests/test_audio_suite.py`, a strict subset of
  stage 8. Run it on its own (**2.23 s**) while you are working on the audio tier;
  running it inside the gate would only pay for it twice.

---

## Why this order and not CI's

`.github/workflows/ci.yml` runs lint → **tests** → corpus → calibrate → diff → run →
diff → error analysis. CI puts the 73-second stage second. That is right for CI, which
has no human waiting and wants the broadest signal in the log; it is wrong for a
laptop, where the only thing that matters is how long it takes to be told what you
broke.

`make gate` runs the same assertions in cost order instead. The two orders agree on
everything except where `pytest` sits.

**Cheapest first and most diagnostic first mostly point the same way here** — but not
always, and the exception is worth stating.

*Measured, in the direction that supports the ordering.* Deleting the "other clauses"
loop from `Session.notes_text` in `tablemate/agents.py` — a one-hunk product
regression that drops free-text notes from every booking:

- stage 4 catches it in **0.81 s**, and names it:
  `NEW happy-birthday-note-reaches-booking: propagation:birthday`
- stage 8 also catches it, in **51 s** (`pytest -x`), and names
  `test_the_committed_flake_band_replays_offline_and_reproduces_exactly` — a true
  statement about a fixture, two steps removed from the sentence that is wrong.

Cheaper *and* sharper.

*Measured, in the direction that does not.* Deleting the nearest-first `options.sort`
from `Restaurant.alternative_times` in `tablemate/store.py`:

- stages 1–7 are **all green**, including `make live-replay`;
- stage 8 fails, on `tests/test_live_run.py`, with
  `{'edge-no-availability-at-requested-time': 'STABLE_FAIL'} != ... 'STABLE_PASS'`.

So stage 8 is not decoration on top of the cheap block. It is the only stage that
covers code no committed artefact happens to reach, and there is at least one
one-line change in this repository that nothing else in the gate sees. That is the
argument for running it — last, and always.

---

## Stage 9 — the paid tiers: what the gate cannot do

Nothing above spends anything. These do, and there is no ordering trick that makes
them cheap; the question is only *whether your change requires one*.

| Target | What it spends | Gate | Run it when |
| --- | --- | --- | --- |
| `make live-record` | provider tokens: agent, caller and judge | `LAB_LIVE_AGENT`, `LAB_LIVE_CALLER`, `LAB_LIVE_JUDGE` + a model route + a key | you changed a prompt, a persona, a temperature or the model, and want to know what the system now does |
| `make audio-suite-record` | synthesis characters + recognition seconds | `LAB_LIVE_TTS`, `LAB_LIVE_STT` | you changed the speech stack, a voice, or an engine setting |
| `make spoken-record` | synthesis characters for a whole call | `LAB_LIVE_SPOKEN` + both audio keys + three model routes | you changed the advisory prompts or the rubric and want to hear it end to end |
| `make transport-record` | live WebRTC sessions | `LAB_LIVE_TRANSPORT` + the room variables | you changed the transport tier |

`make audio-suite-plan` prints what re-recording the audio tier **would** cost and
spends nothing. Reach for it before the target above it.

**The decision rule, in one line.** If your diff touches only code, a check, a
report or a scenario, the gate is sufficient. If it touches **what a model is told** —
a system prompt in `tablemate/runtime.py`, a persona in `scenarios/personas/`, a
rubric in `roleplay/rubric_v*.md`, a judge prompt in
`lab/judges/hallucinated_confirmation/prompt_v*.md`, or a `ragcheck/prompts/*.md` —
then a green gate has told you that nothing *else* broke, and has told you nothing at
all about the change you made.

A recorded run is not a measurement of the system you now have. It is a measurement of
the system you had when you paid for it.

---

## When the gate goes red

Stop at that stage and read [docs/DEBUGGING.md](DEBUGGING.md). Do not run the later
stages first: stage 4 rewrites `fixtures/replay_run` and stage 5 rewrites
`lab/judges/`, so once one of them is red the artefacts on disk no longer match the
ones committed, and every stage after it is reading a tree that is mid-edit.

One consequence of that, worth knowing before it surprises you: **stages 4 and 5 diff
the working tree, not your change.** If somebody else's work in progress is sitting
uncommitted in `fixtures/` or `lab/judges/`, `git diff --exit-code` will report it as
your regression. Run the gate on a tree that holds your change and nothing else.
