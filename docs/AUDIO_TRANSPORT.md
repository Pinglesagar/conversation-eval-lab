# The WebRTC transport tier

Three rows run through real WebRTC transport because three things only exist in
transport. Everything else in this harness runs in process, because in process is
faster, deterministic, needs no credential, and the harness owns the clock —
which is what makes every other timing figure in this repository reproducible.
Using the expensive tool only where it is the only tool is the point of this tier,
and it is why the count is three rather than thirty. **This tier is also
non-gating in CI, by design: a flaky network test that blocks a merge trains
people to bypass the gate, and a gate people bypass protects nothing.** What gates
is the offline suite, which recomputes every figure below from committed
recordings on a machine with no keys.

Everything here was measured against a live LiveKit deployment on 23 August 2026.
The three sessions are committed as recordings; every number in this document is
recomputed from them by `make transport-report`, offline, and can be recomputed by
a reader who distrusts it.

**Cost: zero synthesis characters.** The tier publishes a clip this repository
already committed, because what a transport does to audio has nothing to do with
what the audio says.

---

## Why these three, and not a fourth

An in-process adapter has nothing between the agent and the listener except a
function call. Three failures live in that gap:

| # | row | what only transport has |
|---|---|---|
| 1 | `audio-transport-delivery-gap` | Two instants that can differ. Agent-side, the response exists; receiver-side, somebody hears it. In process those are the same instant and the gap is zero *by construction*, not by measurement. |
| 2 | `audio-transport-degradation` | Concealment and a time axis. A file perturbation deletes samples and nothing intervenes; a real transport conceals the gap, re-paces what survives, and can be late — which a file cannot be, because a file has no time. |
| 3 | `audio-transport-lifecycle` | A connection to lose. A simulated drop is a branch in the harness; what makes the row worth running is the parts nobody wrote — renegotiation, the far side's subscription state, and whether audio published after a rejoin reaches a listener subscribed to a track that no longer exists. |

The admission rule is enforced in code, not in review: `TransportRow` requires a
`why_transport` field, and `tests/test_voice_transport.py` asserts the three
justifications are distinct. A fourth row would have to write a fourth reason.

---

## Row 1 — the delivery gap

**The most valuable measurement in the suite, and it is one subtraction.** A voice
framework's `e2e_latency` is agent-side: it stops when the response exists. This
row times the same twelve responses twice — when the harness handed the first
energetic frame to the transport, and when that audio arrived at the far
participant — and reports the difference.

Twelve turns of a 1.42 s agent utterance, 20 ms frames, over a real room:

| figure | n | mean | p50 | p90 | p95 | min | max |
|---|---|---|---|---|---|---|---|
| delivery gap (measured) | 12 | 89.0 ms | 89.6 ms | 96.9 ms | refused (n<20) | 77.4 ms | 97.6 ms |
| net of this harness's send queue | 12 | 86.0 ms | 88.7 ms | 89.7 ms | refused (n<20) | 75.1 ms | 91.0 ms |
| **what an agent-side figure reports** | 12 | **0.0 ms** | 0.0 ms | 0.0 ms | 0.0 ms | 0.0 ms | 0.0 ms |

For a live in-call coaching product — a factual correction or a compliance
reminder delivered while an adviser is mid-sentence — a suggestion that arrives
after the moment has passed is a failure and not a slow success. That is the
sense in which this ~90 ms is not a performance detail but the product risk: it
is time the dashboard does not know about, spent after the agent believes it has
answered.

p95 is **refused**, not estimated. Twelve samples cannot bracket a 95th
percentile, and a room is real time so more samples cost more seconds. The
refusal is the same rule every other latency figure in this repo obeys
(`lab.voice.metrics.min_samples_for_quantile`).

### Three things that could have made this number wrong

**The stopwatch.** No latency figure here is reportable unless
`lab/voice/calibration.py`'s gate has passed. `delivery_gap()` takes the gate's
report and refuses without a PASS — it does not caveat, it declines. The gate
proves the harness recovers a known delay while excluding its own compute, and
this tier copies the discipline it proves: `clock.now()` is the last statement
before `capture_frame`, and the first statement after a frame arrives, with the
ledger row built afterwards. Reverse those two lines in the receive loop and every
arrival is late by the cost of the arithmetic.

**Our own send queue.** The measured gap contains audio sitting in this process's
outbound buffer, so the queue depth is recorded at every push. It matters:

> The gap and the local send-queue depth correlate at **0.72**, and subtracting
> the queue takes the per-turn scatter from **7.1 ms to 5.2 ms**. On an earlier
> session the correlation was **0.98** and the scatter fell from 14.5 ms to 4.0
> ms — that session's raw gap drifted from 91 ms to 121 ms across twelve turns
> while the net figure stayed flat, because the queue was slowly filling.

So the measured figure is what the listener waited, and the net figure is what the
transport contributed. Both are reported. Without the queue readings, the drift
would have looked like a network getting worse.

**Where speech starts.** The measurement rests on one judgement call, so the
report sweeps it:

| onset threshold (RMS) | 0.005 | 0.010 | 0.020 | 0.040 | 0.080 |
|---|---|---|---|---|---|
| mean gap | 109.1 ms | 89.0 ms | 89.0 ms | 91.7 ms | 99.2 ms |

A 16× change in the threshold moves the answer by about 20 ms and never near
zero. The figure is an order of magnitude, not a point estimate — which the next
section makes unavoidable.

### Run-to-run spread, committed rather than caveated

The same row has now been recorded three times and **every recording is
committed**, because a live figure whose repeatability is not in the repository is
a figure a reader has to take on trust.

| session | n | mean | p50 | net mean | net p50 | stdev | queue corr | row verdict |
|---|---|---|---|---|---|---|---|---|
| primary | 12 | 89.0 ms | 89.6 ms | 86.0 ms | 88.7 ms | 7.1 ms | 0.72 | PASS |
| second session | 12 | 137.6 ms | 90.1 ms | 104.7 ms | 90.1 ms | 72.2 ms | 0.99 | **FAIL** |
| live run | 12 | 128.3 ms | **158.2 ms** | 87.9 ms | 84.7 ms | 42.5 ms | 0.99 | PASS |

Spreads over all three: means **48.6 ms**, medians **68.6 ms**, means net of the
local send queue **18.7 ms**, medians net of the local send queue **5.4 ms**.

**This paragraph used to say "quote the median", and the third session falsified
it.** On two sessions the raw medians agreed to half a millisecond, which made the
median look like the stable statistic; the third put the raw medians 68.6 ms apart
and made it the *least* stable of the four. What actually reproduces across all
three sessions is the **median net of the local send queue**: 88.7, 90.1, 84.7 ms,
a 5.4 ms spread — well inside one 10 ms frame.

The mechanism is the correction the row already recorded for a different reason.
The third session's raw median of 158.2 ms sits beside a queue correlation of
**0.99** and a net median of 84.7 ms: roughly 70 ms of that figure was this
harness's own send buffer filling, not a network delivering late. Without the
send-queue column, that session reads as a transport degrading by 76% and would
have been the headline. Quote the net median; read the spread as the risk.

Two things were wrong with how the old claim was defended, and both are fixed:

* The report **asserted** the interpretation in a fixed sentence beneath a
  computed table. It now computes which of the four statistics has the tightest
  spread and names that one, so the sentence cannot outlive the data underneath
  it.
* The test that existed to catch exactly this **named two files** and compared
  them, so a counterexample could land in the directory it was reading from
  without it noticing. It now globs every committed session, and it fails if the
  tightest statistic is not the one this document quotes.

The second session **fails the row's own scatter ceiling** and is kept anyway. An
assertion no recorded session has ever tripped is an assertion nobody has tested.
Note that the failing session and the session with the largest raw median are
*different* sessions — which is itself an argument for keeping all three.

A third session was recorded during development (mean 105.2 ms) and not kept: it
was produced by code that has since changed, and a recording that the shipped code
did not produce is not evidence about the shipped code.

---

## Row 2 — real loss against the file-based ladder

This row audits an instrument rather than a product. `lab/voice/perturb.py` grades
audio in a file, and the voice suite reads verdicts off that ladder — "survives 5%
loss, fails at 20%". Whether a rung of it resembles the thing it stands for is a
question about every one of those verdicts.

One clip, one live session, two arms: a control with nothing withheld, then the
same clip with **every fourth frame withheld at the sender** (25.4%, deterministic).
The file side is `packet_loss` on the same clip, in both of its fill modes.

| fill mode | injected | realised in file | transport adds | file adds | per unit loss: transport | per unit loss: file | ratio | verdict |
|---|---|---|---|---|---|---|---|---|
| `zero` (the default) | 18/71 (25.4%) | 16.7% | 14.8% | 14.1% | 0.58 | 0.85 | **1.45×** | DISAGREE |
| `hold` | 18/71 (25.4%) | 16.7% | 14.8% | 2.8% | 0.58 | 0.17 | **0.29×** | DISAGREE |

**The two file modes bracket reality and neither is close to it.** `zero` — what
the ladder does by default — is about 1.5× harsher than a real transport, because
it leaves a hole where a codec would conceal one. `hold` is about 3.5× gentler.
The truth sits between them, so a rung of the file ladder cannot be read as a
loss rate a real connection would produce. That does not make the ladder useless;
it makes it a *relative* instrument. Ranking two builds against rung 3 is fine.
Saying "this build tolerates 20% packet loss" is not.

### The comparison had to be normalised twice, and the naive version says the opposite

Two corrections, both of which changed the conclusion:

1. **Each channel has its own silence floor.** With no loss at all, the file reads
   17.6% of frames as silent and the transport reads 11.4% — a codec's output has
   a noise floor a raw file does not. Comparing the loaded figures compares the
   floors as much as the loss, so each side is measured against **its own**
   no-loss baseline, and the transport's baseline is the control arm recorded in
   the same session.
2. **The two instruments applied different doses.** This harness withheld exactly
   25.4%. `packet_loss` is Bernoulli per packet, and over 72 packets a request for
   25.4% realised **16.7%** — which `perturb` reports honestly in its own
   descriptor. Ignoring it credits the file with a gentleness that is really a
   smaller dose.

Skip both corrections and the naive reading — loaded silent fractions, 20.4%
against 26.1% — concludes that `hold` **agrees** with reality within tolerance.
Apply them and it does not, by a factor of 3.5. Same recording, opposite
conclusions; the difference is whether the comparison controls for the floor and
the dose. This is the same shape as the naive control in
`lab/voice/calibration.py`, which reads ~30% high for an equally boring reason.

### And one thing the file ladder cannot express at all

Pacing at the receiver, from this row's own session — 465 frames, 464 intervals,
nominal 10 ms:

| session | injected loss | mean abs deviation | longest interval | late frames |
|---|---|---|---|---|
| `degradation.json` | 18/71 (25.4%) | 1.02 ms | **101.3 ms** | 1/464 (0.2%) |
| `degradation-live-run.json` | 18/71 (25.4%) | 0.60 ms | **21.2 ms** | 1/464 (0.2%) |
| loss-free (row 1's session) | none | 0.60 ms | 24.7 ms | 6/2285 (0.3%) |

**An earlier version of this table had two rows and drew a causal conclusion from
them: "injected loss quadrupled the longest inter-arrival interval". A second live
session of the same row, at the identical injected rate, falsifies it.** 21.2 ms
against 101.3 ms, with the same 18 of 71 frames withheld — and *below* the
loss-free session's 24.7 ms. So the 101 ms hole was a network event that happened
during that session, not a consequence of the loss injection, and the original
comparison was not controlled: the two rows it compared came from different
sessions, so session and treatment varied together.

This is the third time in this suite that a control arm has reversed a
conclusion — after the confusable-name clip and the es-US arm — and the first
time it did so to a claim that had already been written down. The measurement was
right both times; the causal reading was available only because n was 1.

What survives, and does not need a causal claim: **jitter has no column for the
file ladder at all.** A perturbed file has no time axis, so pacing — the thing
that actually degrades a live call, and the thing a jitter buffer exists to
absorb — is inexpressible in it at any rate. That is a structural gap in the
ladder rather than a missing feature, and it holds regardless of which session's
longest interval you quote.

---

## Row 3 — a participant drops mid-utterance

40 of 71 frames pushed, then the publisher's transport is taken away underneath
it; then a rejoin with the same identity and a fresh turn.

| interval | measured | what it contains |
|---|---|---|
| drop → publisher reconnected | 910 ms | signalling only |
| drop → far side subscribed again | 1,092 ms | signalling, republish, subscription |
| last audio heard → next audio heard | **1,800 ms** | the listener's experience, including 600 ms this harness left deliberately |

Three intervals, because "reconnect time" means at least three different things
and they differ by hundreds of milliseconds. The row asserts on the middle one —
it contains nothing the harness chose to do — and *reports* the third, which is
the only one measured from the listener's own stream and is roughly twice the
transport-level figure.

**Verdict: `recovered-turn-lost`.** The connection came back; the utterance it was
carrying did not. Nothing retransmits the 31 frames that were never sent — not
this harness, and not a production voice agent either. The distinction is the
whole row: *a reconnect metric that stops at "the participant came back" scores a
lost sentence as a success*, which is how this failure survives in production
dashboards.

The verdict is **pinned** in the row (`expected_verdict: recovered-turn-lost`), so
a change in it — a hang, a slower recovery, somebody adding replay — fails the row
and gets read by a person. Two of the four possible verdicts (`hung`,
`no-recovery`) are the ones that matter to a product and neither has been
observed; both are exercised by unit tests against synthetic recordings so the
paths are not merely written.

Two details that only a real transport produces:

- **4 frames of the interrupted turn arrived after the sender had already gone** —
  audio sitting in the receiver's jitter buffer, which briefly outlives the
  connection that filled it.
- That in-flight audio is why the analysis has **three** buckets and not two. A
  run that began before the drop and was still arriving at it belongs to neither
  "before" nor "after"; with two buckets it disappears, and the row reports that
  nothing was interrupted.

---

## How the tier stays honest

**A real-time session cannot be replayed, so the tier splits in two.**
`session.py` opens a live room and writes down what happened; `measure.py` turns
records into findings with pure functions. Nothing in a committed recording is a
result — they hold timestamps and per-frame energies — so every figure is derived
on each run, offline, and a reader can move a threshold and watch it move. That is
the only sense in which a one-off live measurement can be reproducible, and it is
what lets the offline suite assert on numbers from a session it never ran.

**Ordering is decided on stream position, never by comparing two streams' clocks.**
A receiving track hands over a frame every 10 ms whether or not anyone is
speaking, so "the first frame that arrived" is meaningless. The tempting fix —
search the arrival ledger for the first energetic frame later than the push — is
the one thing this tier must not do. Instead each stream is segmented on its own
terms into *speech runs*, and run `k` is paired with utterance `k` by ordinal.
Lifecycle events carry the receiver's stream position (`arrival_index`) for the
same reason, and the analysis refuses rather than falling back on timestamps when
it is missing.

**Every measurement can refuse, and the refusals are exercised.** No calibration
report, a failing one, a run count that does not match the utterance count, audio
that appears to arrive before it was sent, a missing denominator, a comparison
with no baseline or no realised dose — each is a refusal with a reason, and each
has a test that drives it. A refusal path that never executes is a comment.

**Failure is a timeout with a name.** Every wait in a live session is capped and
raises `TransportTimeout` naming the phase — `connect:receiver`,
`publish:attempt-2`, `subscribe:attempt-2`. A test that hangs teaches people to
skip the suite.

### Two mistakes this tier made, kept here because they are the interesting part

**The segmenter counted frames where it should have counted time.** The first
version closed a speech run after 20 consecutive quiet *positions* in the arrival
ledger, on the assumption that a receiving track delivers one frame every 10 ms.
It does not. The second live session contained a stall followed by a burst — 24
frames arriving inside 128 ms — which reads as a 240 ms silence if you are
counting positions. One utterance split into two runs, the pairing correctly
refused, and a perfectly good session reported nothing. Position and elapsed time
diverge *exactly under jitter*, which is the condition this tier exists to
measure. The gap is now measured in seconds from the ledger's own timestamps, and
`test_a_delivery_burst_does_not_split_a_run` is that session in miniature.

**An assertion was written about the clip instead of the harness.** Row 2
originally capped the loss-free control arm at 5% silent frames, to catch the
harness starving the transport. The row failed on it — because real speech
contains quiet frames of its own, and this clip reads 17.6% silent in the file
with nothing done to it. The check was a check on the audio. It is now one-sided
and relative: how much *more* silence the control arm shows than the unperturbed
file, which is what under-feeding would actually cause. Measured, it comes in
*below* the file baseline, for the codec-noise-floor reason that also forces each
side of the comparison to have its own baseline.

---

## What this tier does not claim

- **Both ends are in one process.** That is deliberate: the delivery gap is a
  difference between two instants, and two machines would add an unmeasured clock
  offset typically larger than the thing being measured. One process means one
  monotonic clock and no skew term. The cost is that the path is this machine to a
  hosted server and back, not between two distant callers, so **the figure is a
  floor on the delivery gap and not an estimate of the worst case.**
- **There is no agent, no recogniser and no model in the loop.** A transport does
  not care what audio means, and an LLM would add a second source of variance to a
  measurement whose value is that its ground truth is known. The projected traces
  therefore carry no `caller_utterance`, `transcript_in` or `agent_utterance`
  events — a test asserts their absence, so no conversational check can run
  against a session where no conversation happened.
- **Loss is injected at the sender, not induced on the wire.** Withholding frames
  is a real dose into a real transport, so what is observed — concealment,
  re-pacing, the jitter buffer — is genuine. But it is not the same as a lossy
  path, and the row says so rather than implying it degraded the network.
- **Three rows are not a distribution.** Two sessions of one row, twelve turns
  each. Enough to reject "the gap is zero" decisively and to characterise its
  order of magnitude; not enough for a tail estimate, which is why p95 is refused
  rather than printed.

### What would improve it next

1. **Pace against the send queue rather than a fixed deadline.** The queue drift is
   currently measured and subtracted; feedback control on
   `AudioSource.queued_duration` would prevent it, and would take the raw figure
   closer to the transport's own contribution.
2. **A second machine, and clock synchronisation to match.** The only way past the
   "floor, not worst case" limitation, and it needs its own calibration — the gap
   would carry a skew term that has to be bounded before the figure means anything.
3. **Induce loss on the path rather than at the sender**, so the transport's
   retransmission and congestion response are exercised too.
4. **Twenty turns per session**, which is what p95 costs: about forty more seconds
   of real time per recording.

---

## Running it

```bash
# Offline. No key, no network. Recomputes every figure above from the recordings.
make transport-report

# The offline suite: 62 tests over the committed recordings.
pytest tests/test_voice_transport.py

# Live. Needs the extra, the opt-in flag and three LiveKit variables.
pip install -e ".[transport]"
export LAB_LIVE_TRANSPORT=1
export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
pytest tests/test_voice_transport_live.py     # 4 liveness tests, ~19 s
make transport-record                          # re-record all three sessions
```

Environment variable **names** only appear in this repository; no value is
printed, logged, or written into a recording. A recording carries a 12-character
SHA-256 prefix of the signalling URL — enough to show two sessions came from the
same deployment, not enough to say which — and a test asserts the committed files
contain no URL, key or secret.

| path | what it is |
|---|---|
| `lab/voice/transport/records.py` | the evidence format: ledgers, lifecycle facts, no verdicts |
| `lab/voice/transport/measure.py` | pure functions from records to findings, every one refusable |
| `lab/voice/transport/session.py` | the only module that touches a network |
| `lab/voice/transport/trace.py` | records → `Trace`, so the gap is a pairing like every other latency figure |
| `lab/voice/transport/rows.py` | the three rows' schema, closed vocabulary and assertions |
| `lab/voice/transport/report.py` | the report, with the calibration gate in front of it |
| `scenarios/audio/transport/*.yaml` | the three rows |
| `fixtures/audio/transport/` | four recordings and their traces — the committed evidence |
| `scripts/make_transport_fixtures.py` | the live recorder |
