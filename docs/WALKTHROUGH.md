# The ten-minute walkthrough

The interview path through this repository, in the order it makes sense.

The full engineering reference is [WIKI.md](WIKI.md) — 15,000 lines, every file. Do not
walk anyone through that. Walk them through this, and point at the wiki when they want
depth on one thing.

Every number below is reproducible by the command beside it. If a figure here disagrees
with what the command prints, the command is right and this page is stale.

---

**One picture of the whole thing:** [ARCHITECTURE_ONE_PAGE.md](ARCHITECTURE_ONE_PAGE.md) — the diagram to draw on a whiteboard, with eight plain-English pointers.

---

## The one-sentence version

> A test harness for conversational AI that grades sales conversations against a rubric
> and four regulators' rules — and measures whether its own graders are any good before
> it believes them.

---

## 0. Start it

```bash
git clone <repo> && cd conversation-eval-lab
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                # 2,302 tests, ~45 seconds, no API key
make start            # one finding, recomputed live, in about a second
```

**The thing to say out loud:** no credential of any kind is needed. Every live model,
speech and transport path is opt-in behind a `LAB_LIVE_*` flag, and each one ships a
committed recording that replays in its place. That is why a stranger can judge this in
two minutes instead of reading the README and leaving.

**The cost, stated before they find it:** replay is blind to a prompt change. Edit a
prompt and the recording is from the old one, so the test passes while behaviour has
moved. That is exactly what the live tier exists for, and it is why the flags exist at
all.

---

## 1. The domain

A trainee financial adviser practises a sales call against a simulated customer. The
platform grades the session and **certifies whether they are ready to sell**.

Two things are being judged, and keeping them apart is most of the design:

| | |
|---|---|
| **Did they sell well?** | discovery, objection handling, closing — a rubric, 20 points, pass at 14 |
| **Did they sell legally?** | four regulators, each with its own required disclosures |

A session **fails outright**, whatever it totals, on a missing registered disclosure or a
personal recommendation. Those are gates, not scores, and they are never averaged in —
because averaging a legal failure into a good total is how a compliance breach gets a
passing grade.

---

## 2. The trace — the one idea everything rests on

Everything the conversation does is written to a single ordered event stream: who spoke,
what they said, which tool was called, how long the silence was.

**Every check, score and metric reads only that stream. Nothing reads the agent.**

Two consequences worth saying:

- Swap the agent, swap text for speech — **every check keeps working unchanged.**
- Every number in a report traces to a line in a specific conversation. There are no
  figures that came from somewhere nobody can point at.

`session_view(trace)` at `roleplay/scorer.py:151` is a pure function of trace events.
That single property is why running a conversation through real speech synthesis and
recognition needed no fork of the scorer.

---

## 3. The two AI participants

```bash
LAB_LIVE_TRAINEE=1 LAB_TRAINEE_MODEL=…    # the adviser under test
LAB_LIVE_CUSTOMER=1 LAB_CUSTOMER_MODEL=…  # the customer, from a persona card
```

The customer is the part people get wrong. It holds facts it will only reveal **when
asked**, and its cooperativeness governs whether it volunteers anything at all.

**Why that matters:** a simulated customer that opens with all its details can never
catch an adviser who fails to run discovery. The test would pass a trainee who asked
nothing. Gated facts are what make the discovery score mean something.

---

## 4. Grading, and the three kinds of judgement

| Kind | Answers | Cannot |
|---|---|---|
| **Deterministic contract** | did the required action happen, in order, without re-asking? | see paraphrase — it is literal |
| **Calibrated judge** | was this objection genuinely engaged? | be trusted until measured |
| **Register lookup** | did this jurisdiction's required disclosure occur? | cover a rule nobody wrote down |

Six contracts: `ToolContract`, `PromiseContract` (the agent *said* it did X — did the
call happen?), `NoReAskContract`, `FieldPropagationContract`, `NoProgressContract`,
`PhraseContract`.

**Ordering is decided on position in the event stream, never on timestamps.** Tied
timestamps under a fake clock read as "in order", so a real violation passed on a fake
clock and failed on a ticking one. That is a bug this repo had and fixed.

**Absence is a first-class result and is not a pass.** A contract with nothing to look at
turns *vacuous* and the report says so, rather than quietly counting a win.

---

## 5. Compliance across four regulators

```bash
make advisory-verdicts
```

MAS (Singapore), FCA COBS (UK), Reg BI (US), SFC/IA (Hong Kong) — **36 register entries
across 4 machine-readable registers**, each carrying a paragraph-level citation. Not a
keyword list.

`kind` drives genuinely different logic, and this is the part to explain:

| `kind` | Behaviour |
|---|---|
| `verbatim` | only the prescribed wording satisfies it — a paraphrase **misses** |
| `prescribed-unit` | substance *plus* a specific figure (14 days vs 30 days) |
| `substance` | a paraphrase conveying the meaning passes |
| `prohibition` | the **presence** of something fails |
| `gate` | failure fails the session regardless of score |
| `not-required` | this regime does **not** require it — an omission must **pass** |

**`not-required` is the load-bearing one.** It stops a global checker inventing
requirements where none exist. Reclassify each carve-out as a substance requirement and
**3 of 5 flip the passing regime to fail** — so it is doing real work, not decoration.

**The result: 16 of 18 rows agree with the hand labels**, one disagreement and one
explicit `undecidable` where no field in the schema records the fact the rule turns on.

**Say the caveat yourself:** that figure is **in-sample** — the probes were written with
those transcripts in view. The CLI prints that on its own second line. It shows the
register is *computable*, not that it is accurate on unseen data.

**The rows that matter are the divergences:** the same transcript, opposite verdicts
under two regimes, because the registers differ. One row carries four verdicts on one
sentence. **That is the property a single global compliance checker cannot have.**

---

## 6. The grader is the thing under test

```bash
make roleplay-demo
```

```
true positive rate (recall)      : 0.281 (9/32)   95% CI [0.156, 0.454]
true negative rate (specificity) : 0.947 (36/38)  95% CI [0.827, 0.985]
Cohen kappa                      : 0.241
```

**It is reluctant to fail anybody.** It catches 9 of the 32 sessions that should fail. In
a product that certifies people, that is the worst available direction to be wrong in —
and the misses concentrate in compliance and locale, the two things a regulated-advice
grader exists to check.

**The separate judge study**, prompt v1 against v2 on 24 hand-labelled items:

| | v1 | v2 |
|---|---|---|
| TPR | **0.250 (2/8)** | 1.000 (8/8) |
| TNR | 1.000 (16/16) | 1.000 (16/16) |
| kappa | 0.308 | 1.000 |
| gate | **REFUSED** | passes |

`require_calibrated()` at `lab/judges/registry.py` **raises** below threshold. v1 was
blocked from CI by the repo's own gate. Every surveyed tool lets you write a judge and
use its verdicts immediately.

**Two things to volunteer here, because they are the strongest content:**

**Aggregate agreement is not agreement.** The failing prompt returned an *identical*
confusion matrix across three separate runs — because its two unstable items sat on
opposite sides and cancelled. A stability check on the totals would have called it
perfectly reproducible.

**The interval does not support the gate.** v2 passes at TPR 1.000 (8/8), whose 95%
Wilson interval is **[0.676, 1.000]** — a lower bound below the 0.85 it just cleared. On
24 items, exact McNemar means the smallest publishable improvement is **six items moving
together**; a v3 fixing three would be unpublishable at p = 0.25. Saying that about your
own headline number is the most credible move available.

---

## 7. Voice — what only exists in audio

```bash
make audio-suite       # 18 rows, offline
make spoken-replay     # the full graded call
```

ElevenLabs is the caller's **voice**. Deepgram is the **ears**. LiveKit is the **phone
line**, used for three rows only.

**The finding to lead with.** A full 16-turn, 181-second call, spoken and heard back:

| | sent | heard |
|---|---|---|
| discovery | 2 | **0** |
| objection handling | 2 | **4** |
| **total** | **12/20** | **12/20** |

The question detector is `body.endswith("?")`. The graded transcript is the unformatted
one — required, because the prettified version fabricates a word error rate — and it
carries no sentence punctuation. **So no spoken turn can ever end in a question mark.**
Discovery fell to 0 on a call where the adviser asked five questions.

**And it nearly hid.** Objection handling moved the other way, both channels totalled
12/20, verdicts and disclosure ledgers identical. A check on the total, the verdict, or
the register would each have reported that the audio channel changed nothing. **Only a
per-criterion comparison surfaced it.**

That is n = 1. It demonstrates the pipeline is real; it supports no rate. Say so.

**Also worth thirty seconds:** the noise ladder breaks at −5 dB by returning a *plausible
wrong postcode* at 0.907 confidence, while −10 dB returns nothing. **The milder line is
the dangerous one** — pass/fail loses that distinction entirely.

---

## 8. Non-determinism

Run once and you have learned almost nothing about a system that answers differently each
time. `pass^k` runs each scenario k times and returns **STABLE_PASS / STABLE_FAIL /
FLAKY** — flaky is its own verdict, never quietly a pass.

**The honest part:** two independent draws of the same 8 scenarios disagreed — 1/8 flaky
against 3/8. The repo's own conclusion is that k=5 over 8 scenarios locates the flake
rate in the low tens of percent and no more precisely.

---

## 9. Pointing it at *their* system

The most likely question in the room, and it has a real answer.

```
--agent-factory AGENT_FACTORY
    dotted path to the agent factory
```

It is a plug-in point on the runner (`lab/cli.py:2294`). You supply a Python callable that
takes a customer message and returns the agent's response plus any tool calls. **That is
the entire contract.**

Everything downstream consumes the trace, so the contracts, judges, scorecard and reports
work against a different agent unchanged. It will accept a rule-based bot, another
model, a vendor API or an internal agent — each needs a thin wrapper, and supplying a
model name alone is not enough.

**What that means for a first week somewhere:** write the wrapper, run the existing
contracts, and the first honest number arrives the same day.

---

## 10. Release gating

```bash
make gate     # every stage, cheapest first
```

CI runs: lint → 2,302 tests → corpus validation → **calibration gates** → *artefacts
unchanged* → replay run → **the run reproduces the committed report byte for byte** →
error analysis agrees.

The byte-for-byte steps are the interesting ones — they fail when a committed result
drifts from what the code now produces, which catches silent changes a passing suite
would miss.

There is also **trace-derived test selection** (`evallab select --changed-since HEAD~1`):
it works out which scenarios a change can reach, using a dependency map derived from the
committed traces rather than declared by hand, because a declared map goes stale silently
and a stale selector is worse than none. It is fail-safe — an unparseable file, a shared
module or a scenario with no trace all escalate to running everything — and it **measures
its own miss rate and refuses to gate below threshold.**

---

## 11. Limitations — say these before they are asked

- **The corpus is synthetic.** The registers are reconstructed from public regulatory
  sources, not any firm's compliance system. The rubric is a reasonable reconstruction,
  not anyone's real scorecard.
- **The 16/18 is in-sample.** A held-out paraphrase set is the honest next step.
- **The phrase lists are short.** *"three per cent of the sum you invest"* returns
  *missed* on a correct disclosure. Recall against unseen wording is unmeasured.
- **The spoken call is n = 1.**
- **Barge-in is constructed, not discovered** — the events are written by a scenario, not
  observed by an adapter. Discovering a real overlap needs a duplex path this version
  does not have.
- **Cantonese is untestable** — no TTS vendor synthesises it, so a market cannot be
  audio-tested on this stack. Recorded as a finding, with the remediation named.
- **It is Python.** Porting to another runtime means rewriting the adapters; the trace
  schema, contracts and calibration logic are not language-specific.

---

## 12. If you only remember three things

1. **The trace is the product.** Everything reads one event stream, so the checks survive
   a change of agent, of model, or of medium.
2. **Measure the instrument before believing it.** A judge at TPR 0.250 that the gate
   refused. An interval that does not support its own threshold. A confusion matrix that
   was stable for the wrong reason.
3. **The most useful findings were against my own tools** — a compliance check that could
   pass anything, a metric reporting 43% error on perfect recognition, a harness that
   blamed the product for its own bug. Those are in the docs because that is the part
   nobody writes down.

---

## Appendix — the second domain

The repository also drives a **restaurant-booking assistant** with three deliberately
seeded defects. It is not part of the story above and should not be walked through.

Its job is one argument: **the same engine, a second unrelated domain.** A harness that
only ever ran against the domain it was written for has proved nothing about portability
— and portability is the whole claim in §9. One page of evidence that the engine is not
secretly welded to advisory work.

Mention it in a sentence. Move on.
