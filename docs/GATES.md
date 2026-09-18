# The gate — what to run, in what order, and what each stage cannot catch

`make gate` runs every offline check in cost order, cheapest first, and stops at
the first failure. No key, no spend, no socket. This page says what each stage
proves, what it is blind to, and which changes need a tier that costs money.

The short version: **stages 1–5 are nine commands and about 2.5 seconds. Stage 6 is
18 seconds.** Running the whole artefact surface costs less than deciding whether
to, so there is no version of "I'll skip the cheap ones" that saves anything.

---

## The one fact to read before anything else

> **Replay is blind to a prompt change.**
>
> Every stage in the gate below reads a recording. The recording was made against
> the prompt as it was on the day it was recorded. Change a prompt, a persona or a
> rubric and the gate stays green — not because the change is safe, but because
> nothing in stages 1–6 ever asks a model anything.

This is not a theory about the design. It is a property of recorded fixtures, and
it is the reason the recording targets exist as first-class `make` targets rather
than as a script somebody remembers. A cassette answers with yesterday's words,
confidently, for as long as you let it.

The mitigation is not a cleverer replay. It is that every fixture carries the
identity of the engine that produced it — `tts:elevenlabs/eleven_flash_v2_5/<voice>`,
`stt:deepgram/nova-3/en/raw` — so the thing a reader cannot see in a green run is
at least written down in the artefact.

---

## The six stages

Costs are wall-clock, best of three consecutive runs, on the machine this was
written on (`.venv`, Python 3.12, warm filesystem). Reproduce any of them by
putting `time` in front of the command.

| # | stage | command | cost | what it proves | what it **cannot** catch |
| --- | --- | --- | --- | --- | --- |
| 1 | lint | `ruff check --select E9,F63,F7,F82 --exclude .venv .` | **0.03 s** | no syntax error and no undefined name, anywhere | anything about behaviour. It is deliberately not a style gate |
| 2 | the corpus | `python -m roleplay.corpus --coverage --list` | **0.24 s** | all 5 rows match the schema: no typo'd tool name, no tag outside the closed vocabulary, no `expected_failure` naming a contract the row does not declare. A typo here is a **load error**, not a silent pass at run time | whether the rows are the *right* rows. A perfectly valid scenario can assert nothing useful |
| 3 | the calibration gates, then their artefacts | `python -m lab.cli calibrate --ci`<br>`git diff --exit-code -- fixtures lab/judges` | **0.18 s** | the stopwatch is inside tolerance and the judge clears TPR ≥ 0.85, TNR ≥ 0.85, n ≥ 10, zero parse errors — and then that both artefacts regenerate byte-identically | whether the **labels** are right. A judge scoring 1.000 against one person's set is agreeing with one person, and this repository says so rather than measuring it |
| 4 | the two recorded calls | `python -m roleplay.spoken`<br>`python -m roleplay.scorecard_eval <both traces>` | **0.23 s**<br>**0.15 s** | both spoken calls still replay from their committed audio and still produce the committed gradings — including the per-criterion divergence between what was spoken and what was heard | a prompt change (see above). It reads a recording, so it proves reproduction, not correctness |
| 5 | the other packs and the recorded tiers | `python -m roleplay.demo`<br>`python -m roleplay.regime_eval --divergence --shadow`<br>`python -m ragcheck`<br>`python -m lab.voice.transport.report` | **0.25 s**<br>**0.50 s**<br>**0.41 s**<br>**0.28 s** | that the behavioural corpus, the cited registers, the retrieval tier and the WebRTC tier all still reproduce from their recordings | the same blindness as stage 4, once per tier. Every one of these reads a recording |
| 6 | the offline suite | `pytest -q` | **11 s** | the units; the committed artefacts of stages 3–5 a second time, through tests of their own; and code no committed artefact reaches at all | nothing that is not asserted. It is the broadest stage and the slowest, which is exactly why it is last |

One target is deliberately **not** in `make gate`: `make coverage` runs the suite
twice and reports a number, not a verdict. A coverage floor fails for reasons
unrelated to the change in front of it, and adopting an uncalibrated instrument as
a gate is the thing this repository spends its length arguing against.

---

## Why this order and not CI's

The conventional order is lint, then tests, then everything else. `make gate` runs
the same assertions in cost order instead. The two orders agree on everything
except where `pytest` sits.

**Cheapest first and most diagnostic first mostly point the same way here.** A
defect in the grading logic surfaces in stage 5 as a named row with the evidence
quoted beside it — `pitch-asked-then-ignored-the-answer  human=fail scorer=pass
(18/20) DIFFERS` — and in stage 6 as a test name two steps removed from the
sentence that is wrong. Cheaper *and* sharper.

**But stage 6 is not decoration on top of the cheap block.** It is the only stage
that covers code no committed artefact happens to reach: error paths, refusals,
the branches that raise. Most of this repository's substance is in what it
*refuses* to print, and a refusal has no artefact. That is the argument for
running the suite — last, and always.

---

## Stage 7 — the paid tiers: what the gate cannot do

Nothing above spends anything. These do, and there is no ordering trick that makes
them cheap; the question is only *whether your change requires one*.

| Target | What it spends | Gate | Run it when |
| --- | --- | --- | --- |
| `make spoken-record` | ElevenLabs synthesis characters and Deepgram recognition seconds for a whole call | `LAB_LIVE_SPOKEN` + both audio keys + the model routes | you changed the advisory prompts or the rubric and want to hear it end to end |
| `make transport-record` | live WebRTC sessions | `LAB_LIVE_TRANSPORT` + the room variables | you changed the transport tier |

`make audio-fixtures` re-records the local fixture set with the operating system's
own synthesiser: no key, no spend. Reach for it first.

**The decision rule, in one line.** If your diff touches only code, a check, a
report or a scenario, the gate is sufficient. If it touches **what a model is
told** — a rubric in `roleplay/rubric_v*.md`, a judge prompt in
`lab/judges/hallucinated_confirmation/prompt_v*.md`, or a `ragcheck/prompts/*.md` —
then a green gate has told you that nothing *else* broke, and has told you nothing
at all about the change you made.

A recorded run is not a measurement of the system you now have. It is a
measurement of the system you had when you paid for it.

---

## When the gate goes red

Stop at that stage and read [docs/DEBUGGING.md](DEBUGGING.md). Do not run the later
stages first: stage 3 rewrites `fixtures/` and `lab/judges/`, so once it is red the
artefacts on disk no longer match the ones committed, and every stage after it is
reading a tree that is mid-edit.

One consequence of that, worth knowing before it surprises you: **stage 3 diffs the
working tree, not your change.** If work in progress is sitting uncommitted under
`fixtures/` or `lab/judges/`, `git diff --exit-code` reports it as your regression.
The stage says so on screen before it runs. Run the gate on a tree that holds your
change and nothing else.
