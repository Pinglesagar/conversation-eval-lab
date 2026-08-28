# R1 — Speech and voice-agent testing: what the field does, and what we already do

**Status: research only. Nothing here has been built, and nothing in this file asks
for anything to be built before the owner decides.** No `.py`, `.yaml`, fixture or
`Makefile` was touched producing it.

**Scope.** `lab/voice/` and the audio tiers of this repo only.

**Method.** Section 1 is read out of the repo and verified by commands recorded
inline. Sections 2–8 are the literature and the open-source landscape, each finding
carrying a URL. Section 9 is the decision table. Section 10 records the defect hunt — two
suspicions raised and cleared, one observation, nothing fixed.

**House rules observed.** Every rate below carries its denominator. Every number is
either the output of a command reproduced in this file, or a citation. Anything I
inferred is marked **ASSUMPTION** and there is no third category.

---

## 1. What this repo already does — the honest inventory

Reproduced with:

```
find lab/voice -name '*.py' -exec cat {} + | wc -l      # 15851
ls scenarios/audio/*.yaml | wc -l                        # 18
ls scenarios/audio/transport/*.yaml | wc -l              # 3
ls scenarios/voice/*.yaml | wc -l                        # 8
.venv/bin/pytest tests/test_audio_suite.py tests/test_voice_transport.py -q
                                                         # 129 passed in 3.07s
```

### 1.1 Runnable today

| capability | where | what it actually gives you |
|---|---|---|
| Timing calibration **gate** | `lab/voice/calibration.py` (890 lines), `python -m lab.voice.calibration` | Injected-clock delay recovery measured against nominal at 5 rungs, 5% tolerance, plus a deliberately naive control that FAILs on the 3 shortest rungs. Nothing downstream is reportable until it passes. `docs/AUDIO_SUITE.md` §15. |
| WER, two named numbers | `lab/voice/wer.py` (863) | `raw` and `normalised` error rates, never one figure. Unicode-aware normalisation, `scoring_unit()` for spaceless scripts, `jiwer` and a builtin backend (`available_backends()` → `('jiwer','builtin')`). |
| The reference-form trap, documented and enforced | `lab/voice/engines/WER_NORMALISATION.md` | Scoring the spoken form vs the sent string, `smart_format=false` for any scored number, and the CJK inversion where the "normalised" reference comes back romanised. |
| Field-level capture assertions | `lab/voice/suite.py` (1194), the 5 `digits-and-names` rows | Digits, dates, spelled names and money asserted as **values**, not as a rate. Documented blind spots: containment cannot reject a superstring or a superseded value. |
| Silence attribution | `lab/voice/silence.py` (647), `lab/voice/interaction.py` (627) | `caller_silent` / `vad_false_silence` / `would_not_fire`, adjudicated on an **RMS envelope, not a VAD** — deliberately not measuring the instrument with itself. Threshold verified at 5.9 s / 6.1 s to the millisecond. |
| Channel perturbation | `lab/voice/perturb.py` (663) | Exactly five: `add_noise` (white/pink, seeded, SNR set from measured signal power), `resample_speed`, `shift_pitch`, `telephone_band` (300–3400 Hz), `packet_loss`. All five are used by committed scenarios. Ladders find breaking points (−5 dB SNR; 90% loss). |
| Real vendors, opt-in, with replay | `lab/voice/engines/` | Deepgram STT and ElevenLabs TTS behind `LAB_LIVE_*`, digest-keyed clip cache, committed transcripts so the whole suite replays with zero keys. |
| Vendor capability matrix, computed | `lab/voice/engines/coverage.py` (270) | Market → testable / monolingual-only / untestable, derived from capability sets so a vendor adding a language turns a test red. |
| Delivered-vs-agent latency | `lab/trace/schema.py`, `lab/voice/transport/` (3,367 across 7 files) | `agent_audio_first_byte` (agent-side) is a **different event kind** from `audio_delivered` (receiver-side), and the gap between them is a pairing. Live LiveKit room, one process, records-then-pure-functions so figures recompute offline. |
| Denominator discipline | `lab/voice/metrics.py` (`min_samples_for_quantile`), `lab/voice/transport/measure.py` | p95 is **refused** rather than printed when n is too small. `tier_summary` never prints a naked percentage. |
| Three outcomes, not two | `Scenario.audio_status()` | `runnable` (16) / `blocked` (1) / `untestable` (1); `passed` is `null` for the latter two and they are in no pass-rate denominator. |

### 1.2 Declared but **not runnable** — say this out loud

- **Barge-in discovery does not exist.** `EventKind.INTERRUPTION_STARTED` and
  `INTERRUPTION_ACKNOWLEDGED` are in `V2_RESERVED` (verified:
  `python -c "from lab.trace.schema import EventKind; print(sorted(EventKind.V2_RESERVED))"`).
  `interaction.emit_barge_in` now *writes* them, so they are no longer un-emitted —
  but the turn loop is half-duplex: it plays the agent, then plays the caller, so
  no moment exists in which both are sounding. The one barge-in figure the suite
  publishes (240 ms yield, 0.240 s overlap) is **arithmetic over two clip
  durations handed to the row**, not a detection. `audio-barge-in-not-discovered`
  is reported `blocked` precisely so the suite cannot claim a capability it lacks,
  and the block expires by itself when the schema promotes those kinds to `KNOWN`.
- **No streaming anywhere.** Both engines are batch. That costs interim
  hypotheses, and it costs the vendor's own recommended `endpointing` mitigation
  for code-switched audio.
- **No latency at all in the in-process tier.** The transport tier has the only
  real latency numbers, and its delivery gap is a **floor, not a worst case**,
  because both ends share one process and one monotonic clock.

### 1.3 Untestable in this stack

- **Cantonese (`yue-HK`).** Zero of the nine ElevenLabs models synthesise it,
  verified live; Deepgram *does* transcribe `zh-HK`. Recognition is ahead of
  synthesis, so the gap is structural. `audio-hk-cantonese-untestable` carries its
  own remediation and expires when a vendor ships the voice.
- **Accents.** Every row uses one of two stock premade voices. Voice Library
  voices are not reachable on the free tier, so accent coverage is a paid
  capability and the tag vocabulary was pruned rather than left as aspiration.
- **A natural code-switch.** The Singapore row is a splice (a lower bound), the
  Hinglish row is one attempted native synthesis. Neither is a bilingual speaker.

---

## 2. What the field measures beyond WER

**The framing is not controversial any more.** A survey of 305 Interspeech papers
2023–2025 that report ASR results found **86.6% used WER**, fewer than 40% used
anything else, and **180 of 305 used WER exclusively**
([Beyond Word Error Rate: Auditing the Diversity Tax in Speech Recognition through
Dataset Cartography](https://arxiv.org/pdf/2603.05267)). So "WER is a blunt
instrument" is the field's own position, and the interesting question is which
sharper instrument.

### 2.1 Entity Error Rate (EER) — errors weighted by what they cost

**What it is.** `EER = 1 − entity recall`: the proportion of reference entities not
recovered *in their entirety*. An entity counts as correct only if **all** of its
constituent words are error-free — so a name half-right scores zero, which is the
right answer for a name. Used as a headline metric alongside WER in recent
LLM-ASR work, which reports a **relative 8.7% WER reduction against a 16.9% EER
reduction** on the same system — the entity metric moves nearly twice as far
([Speech LLMs are Contextual Reasoning Transcribers](https://arxiv.org/pdf/2604.00610)).
Vendor-side the same idea appears as **Keyword Recall Rate** — Deepgram's framing
is that a system can hit an acceptable WER and still miss a third of the terms
that decide the outcome
([Speech Recognition Accuracy: Production Metrics](https://deepgram.com/learn/speech-recognition-accuracy-production-metrics)).

**Does this repo do it?** **PARTLY, and arguably better for its own rows.** The
five `digits-and-names` rows already assert *values* rather than a rate, which is
EER at n=1 per row with a hard all-or-nothing criterion — exactly the EER
definition. `docs/AUDIO_SUITE.md` §22 shows why: `readback-account-number` scores
raw WER **0.900** on a *perfect* transcript, normalised **0.000**. What is missing
is the **aggregate**: there is no `entity_error_rate` over a corpus, so the repo
cannot say "we recovered 47/52 entities" — it can only say each row passed.

**Worth doing here?** **Yes, and it is the cheapest high-value item in this
document.** Reason: the machinery is already built and merely un-aggregated. The
per-row field matcher exists; a corpus-level roll-up would give a *named metric
with a denominator* that maps onto how the field talks, and it costs no new vendor,
no new fixture and no new audio. It also gives the containment blind spots
(superstring, superseded value — `docs/AUDIO_SUITE.md` §14) a place to be reported
rather than only pinned by a test.

### 2.2 Semantic error rate / Semantic-WER

**What it is.** Score meaning preservation instead of token distance: normalise,
embed both sides, cosine similarity, `1 − sim` as a distance, threshold per domain
([Deepgram, Semantic Error Rate](https://deepgram.com/learn/semantic-error-rate-asr-accuracy-metric);
academically [Semantic-WER](https://arxiv.org/pdf/2106.02016)). The motivating
example is "3 PM" → "5 PM": 20% by WER, 100% wrong in meaning.

**Does this repo do it?** **DOES NOT**, and it does not need to for the rows it
has.

**Worth doing here?** **No — and being able to say why is the better answer.**
Reason: an embedding-based similarity is a *judge*, and this repo's own rule is
that a judge must be calibrated before it is trusted (`lab/judges`, `calibrate()`
raising below threshold). Adding an uncalibrated similarity metric would import a
number nobody can audit into the one tier that has been most careful about
auditability. The "3 PM → 5 PM" failure is already caught here by a **field
assertion**, deterministically, with no threshold to argue about. Record the
metric, decline it, cite the reason.

### 2.3 Answer Error Rate (AER) — the error that reaches the downstream task

**What it is.** The proportion of question–answer pairs where an LLM's output
*differs* between the clean transcript and the ASR transcript. Reported to exceed
raw WER by 10–30 percentage points, i.e. a small number of semantically load-bearing
errors dominate downstream failure
([Analyzing Error Propagation in Korean Spoken QA with ASR–LLM Cascades](https://arxiv.org/pdf/2605.17443)).

**Does this repo do it?** **PARTLY, and by a different route.** The repo has both
arms available — a text tier and an audio tier over the same scenario corpus —
and the `es-US` row is explicitly described as a **control arm** for a diagnostic
reason. But there is no committed clean-vs-ASR *paired* comparison that isolates
"how much of this failure was the recogniser".

**Worth doing here?** **Yes, as a diagnosis, not as a metric.** Reason: the repo's
stated admission rule is "a row belongs in the audio tier only if the audio layer
is the thing under test" — a paired clean/ASR run is precisely the instrument that
*proves* the admission rule was applied correctly, because it quantifies how much
of a voice failure was audio at all. It reuses the existing corpus and costs zero
synthesis characters (transcripts are committed). **ASSUMPTION:** that a
clean-text arm can be run over the same scenario ids without touching production
code — I did not verify a runner exists for that pairing.

### 2.4 PIER — Point of Interest Error Rate

**What it is.** Score only at the points that carry the phenomenon under test — for
code-switching, the language-alternation boundaries — rather than uniformly across
the transcript ([PIER, ICASSP 2025](https://arxiv.org/pdf/2501.09512)). It is the
generalisation of "keyword error rate": *you nominate the points of interest, and
the metric ignores everything else.*

**Does this repo do it?** **PARTLY.** The Singapore and Hinglish rows are
point-of-interest rows in spirit — they assert the switched-in item — but the
scoring is containment on a declared value, not an error rate at a declared point.

**Worth doing here?** **Yes if the code-switch work grows; no on its own.** Reason:
PIER's value shows up when you have enough switch points to make a rate meaningful.
With one splice row and one native-synthesis attempt, a "rate" over two points is
noise wearing a denominator. The honest note is: *the concept is right, our n is
too small, and the reason our n is small is a vendor limit we have documented.*

### 2.5 Character Error Rate for multilingual scoring

**What it is.** CER argued as the better default than WER for multilingual
evaluation, especially for morphologically rich and non-space-delimited scripts
([Advocating Character Error Rate for Multilingual ASR Evaluation, Findings of
NAACL 2025](https://aclanthology.org/2025.findings-naacl.277/)).

**Does this repo do it?** **PARTLY, and it hit the exact problem the paper
describes.** `wer.py` has `is_spaceless_script()`, `_segment_spaceless()` and a
`scoring_unit()` label, added after `normalise` was found to reduce Hindi,
Japanese, Mandarin and Arabic to the empty string and to *delete* accented Latin
characters (`pensión` → `pensi n`, two phantom errors in every Spanish and French
row). So per-character segmentation exists for the scripts that need it. A named,
reported **CER alongside WER** does not.

**Worth doing here?** **Yes, small.** Reason: the segmentation work is already done
and the naming is the gap — `scoring_unit()` already knows whether a row was scored
in characters or words, so the report can say which, and a reader currently cannot
tell two differently-scored rows apart in the same table.

### 2.6 Pronunciation grading

**What it is.** A separate literature with its own de facto benchmark:
**speechocean762**, 5,000 utterances from 250 non-native speakers, annotated at
three granularities — utterance (accuracy, fluency, completeness, prosody, total),
word (accuracy, stress, total) and **phoneme** (0–2)
([speechocean762](https://www.emergentmind.com/topics/speechocean762-dataset)).
The classical method is Goodness of Pronunciation (GOP), now moving
segmentation-free ([Segmentation-free GOP](https://arxiv.org/abs/2507.16838)) and
towards LLM-based raters
([Read to Hear](https://arxiv.org/pdf/2509.14187)).

**Does this repo do it?** **DOES NOT — and it does something adjacent that is more
useful here.** `audio-capture-confusable-names` **plants** a mispronunciation via
SSML `<phoneme>` and asserts the capture *fails as declared*. That is not
pronunciation *scoring*; it is using a controlled mispronunciation as a probe of
the recogniser.

**Worth doing here?** **No.** Reason: pronunciation assessment grades a *speaker*.
This harness has no speaker — it synthesises the caller. Grading our own
synthesiser's pronunciation measures the instrument, and the repo already has a
sharper name for that class of number: the "TTS-intelligibility probe" in
`docs/AUDIO_SUITE.md` §22. Worth citing as a deliberate exclusion, not a gap.

---

## 3. Turn-taking, endpointing, barge-in — where voice agents actually fail

This is the strongest part of the literature and the weakest part of the repo.

### 3.1 Full-Duplex-Bench — the reference metric set

**What it is.** Four dimensions with named automatic metrics
([arXiv 2503.04721](https://arxiv.org/abs/2503.04721),
[GitHub](https://github.com/DanielLin94144/Full-Duplex-Bench)):

| dimension | metrics |
|---|---|
| Pause handling | **Takeover Rate (TOR)** — binary: 0 if the model stays silent or backchannels through the user's mid-turn pause, 1 for any other speech. Lower is better. |
| Backchanneling | TOR (lower better) + **backchannel frequency** (events/second) + **Jensen–Shannon divergence** of backchannel *timing* against human ground truth |
| Smooth turn-taking | **Response latency** — user speech end → model response start |
| User interruption | TOR (here **1 is correct**) + an LLM-judge quality score 0–5 + **latency after interruption** |

The versioning matters for planning: **v1.5** adds overlap scenarios (listener
backchannel, side conversation, ambient speech); **v2** adds a streaming automated
examiner with Fast/Slow pacing over four task families — daily interaction,
correction handling, entity tracking, safety
([arXiv 2510.07838](https://arxiv.org/pdf/2510.07838)); **v3** adds tool use across
4 domains crossed with **5 disfluency types** (fillers, pauses, hesitations, false
starts, self-corrections).

**Does this repo do it?** **DOES NOT, structurally.** TOR is not computable in a
half-duplex loop: there is no moment where the model *could* take over, so the
denominator does not exist. The single number the repo publishes in this space —
240 ms to yield — is constructed, and `docs/AUDIO_SUITE.md` says so.

**Worth doing here?** **The metric set: yes as a target vocabulary. The benchmark
itself: no.** Reason: FDB scores *audio foundation models* on conversational
behaviour. This repo's system under test is an **application** with tools, contracts
and regulated content, not a speech model. Adopting TOR/latency-after-interruption
as the **names** the repo would use once a duplex adapter exists costs nothing now
and makes the eventual work legible. Running FDB is somebody else's evaluation of
somebody else's artefact.

### 3.2 Talking Turns — turn-taking judged by a supervised model

**What it is.** A protocol that scores a dialogue system's turn-taking using a
**supervised judge trained on human–human conversation** (Switchboard) to predict
turn-taking events: inter-pausal units (IPU: continuous speech separated by >200 ms
of silence), pauses, gaps, overlaps, backchannels, interruptions. Headline user-study
finding: systems "do not understand when to speak up, can interrupt too aggressively
and rarely backchannel" ([arXiv 2503.01174](https://arxiv.org/abs/2503.01174),
[ICLR 2025 poster](https://iclr.cc/virtual/2025/poster/31129)).

**Does this repo do it?** **DOES NOT.** But the *shape* is one this repo already
insists on: a judge that is calibrated against ground truth before it is believed.

**Worth doing here?** **The event taxonomy: yes. The judge: no.** Reason: the
IPU/pause/gap/overlap/backchannel/interruption vocabulary is a **ready-made
extension of the trace event schema** — and the schema was explicitly designed to
accept kinds it has never heard of (`EventKind.kind` is a plain `str`, with
`KNOWN`/`V2_RESERVED` so tooling reports unrecognised kinds rather than crashing).
That is a documentation-and-schema decision the owner can take without any model.
Training or hosting a supervised turn-taking judge is a different project.

### 3.3 Endpointing metrics — the ones with settled names

**What they are.** From the streaming-endpointer literature:

- **ep50 / ep90** — median and 90th-percentile latency between the detected
  endpoint and the true turn end.
- **ep-cutoff** — the proportion of user turns endpointed *before* the true turn
  end. Also called **Early Endpointing Rate (EEPR)** or **Early Cut%**.
- **NoEP%** — the proportion of utterances where the endpointer never fired.

([Streaming Endpointer for Spoken Dialogue using Neural Audio Codecs and
Label-Delayed Training](https://arxiv.org/html/2506.07081);
[Improving endpoint detection in end-to-end streaming ASR for conversational
speech](https://arxiv.org/pdf/2505.17070);
[Two-pass Endpoint Detection for Speech Recognition](https://arxiv.org/pdf/2401.08916))

**Does this repo do it?** **PARTLY, and the partial version is the interesting
one.** The repo does not endpoint — batch engines, no streaming — but
`lab/voice/interaction.py` measures the *consequence* of a bad endpoint from the
other side. `vad_false_silence` is a **false-endpoint-adjacent** verdict: the
timeout fired while speech was present. And the repo's insistence on measuring it
with an RMS envelope rather than a VAD is exactly the methodological point the
endpointing literature makes when it warns that a delayed ASR emission corrupts
endpoint measurement.

**Worth doing here?** **Yes for the naming; no for the mechanism.** Reason: the
repo owns a real finding here (one label, two situations, opposite remedies) and
currently states it in private vocabulary. Mapping `vad_false_silence` → the
literature's early-cutoff family, and `would_not_fire` → the NoEP family, costs a
paragraph and makes the finding portable. Building an endpointer to measure ep50
requires streaming, which is a much larger change (§4).

### 3.4 IHBench — interruption *recovery*, not interruption *timing*

**What it is.** The explicit gap it names: existing benchmarks score the **timing**
of interruptions — barge-in detection, endpointing, turn-taking dynamics. IHBench
scores what happens **afterwards**: 10 enterprise domains, 6 interruption types
injected mid-utterance at controlled points, over state-machine-driven workflows.
Two axes — **task fulfilment** (did the workflow objective complete) and **recovery
quality** (did it resume at the correct step, address the interjection, and avoid
repeating already-heard content). 27 audio-LM configurations evaluated; closed-weight
models degrade ~3.3× more slowly as conversations lengthen, and recovery quality is
"a largely distinct capability axis"
([arXiv 2606.19595](https://arxiv.org/abs/2606.19595)).

**Does this repo do it?** **DOES NOT for audio — but it has the closest thing to
the scoring half of it already.** "Resume at the correct step", "do not repeat
already-heard content" and "address the interjection" are, respectively,
`NoProgressContract`, `NoReAskContract` and `FieldPropagationContract` — six
declarative contracts decided on **event-stream position**, which is exactly the
representation an interruption-recovery check needs. What is missing is the
*injection*: a way to interrupt mid-utterance.

**Worth doing here?** **This is the single best-aligned finding in the document.**
Reason: the repo's differentiator is that it already owns the hard half. IHBench's
recovery axis is a scoring problem the contracts solve position-wise, and it is
domain-agnostic — a restaurant booking flow and an advisory flow are both
state-machine workflows. The blocker is the same one blocking §3.1: a duplex
adapter. Which makes "duplex adapter" the one dependency worth costing properly,
because it unlocks TOR, latency-after-interruption **and** contract-scored recovery
at once.

---

## 4. Full-duplex and streaming — what it breaks in a harness shaped like this one

The field is moving from half-duplex turn-taking to full duplex, and the harness
consequences are concrete rather than philosophical.

**The evidence that it is moving.** Full-Duplex-Bench v1 → v1.5 → v2 → v3 in
roughly fifteen months, v2 explicitly a *streaming* framework with an automated
examiner ([arXiv 2510.07838](https://arxiv.org/pdf/2510.07838)); EchoChain adding
**state-update reasoning under interruptions** as a full-duplex benchmark
([arXiv 2604.16456](https://arxiv.org/pdf/2604.16456)); v2's own headline being that
full-duplex systems "get confused when people talk at the same time, struggle to
handle corrections smoothly, and sometimes lose track of who or what is being
talked about".

**What breaks here, specifically:**

1. **"Turn" stops being the unit.** `lab/voice/metrics.py` pairs
   `caller_utterance` → `agent_audio_first_byte` per turn
   (`iter_turn_latencies`, `first_byte_latencies`). Under duplex there is no clean
   left edge: the caller may still be speaking. The repo's transport tier has
   *already solved the general version of this* — it segments each stream into
   **speech runs** on its own terms and pairs run `k` with utterance `k` by
   ordinal, explicitly refusing to compare two streams' clocks. That is the
   duplex-safe pairing primitive, and it currently only exists in
   `lab/voice/transport/measure.py`.
2. **The clip cache stops being sufficient.** Cache keys are
   `sha256(text, voice, model, format, normalisation)`. Under duplex the *timing*
   of an injection is part of the stimulus, so two identical clips at different
   overlap offsets are different tests with the same key. **ASSUMPTION:** the key
   would need an offset term; I did not check whether any caller already passes a
   distinguishing parameter.
3. **`speech_during_timeout` becomes inferable rather than declared.** Today the
   module is explicit that it is an *input* modelling the agent's belief. With a
   duplex adapter, whether the agent was speaking is observable, and the module's
   most-caveated field becomes a measurement. That is a strict improvement and it
   is worth writing down as the *reason* to do duplex.
4. **The `blocked` row becomes runnable without an edit.** `blocked_on()` derives
   from `EventKind.V2_RESERVED`, so promoting the two interruption kinds to `KNOWN`
   flips `audio-barge-in-not-discovered` automatically. This is a genuinely good
   design decision already in the repo and it should be named as one.
5. **Batch engines are the actual blocker, not the loop.** Interim hypotheses do
   not exist in a batch transcription, so no amount of adapter work produces a
   barge-in *detection* without a streaming STT path.

**Worth doing here?** **Worth costing, not worth starting on the strength of this
document.** Reason: it is the dependency for §3.1, §3.4 and half of §3.3, which
makes it the highest-leverage item — and it is also the only item here that
requires a new vendor integration surface (streaming STT) rather than reuse. The
owner should decide it as one decision, not discover it three times.

---

## 5. Public benchmarks and datasets for voice agents end-to-end

| benchmark | what it evaluates | metrics | repo verdict |
|---|---|---|---|
| **EVA-Bench** ([arXiv 2605.13841](https://arxiv.org/abs/2605.13841)) | End-to-end voice agents, 3 enterprise domains, **213 scenarios**, plus a controlled perturbation suite for accent and noise | **EVA-A** (task completion, faithfulness, audio speech fidelity) and **EVA-X** (conversation progression, spoken conciseness, turn-taking timing); reported at **pass@1, pass@k and pass^k**. 12 systems across 3 architectures. No system exceeded 0.5 on both EVA-A and EVA-X pass@1; median peak-vs-reliable gap **0.44** on EVA-A; accent/noise perturbation cost up to **0.314** mean | **PARTLY — and the methodological overlap is striking.** This repo already runs **pass^k** (`lab/simulator/passk.py`) with a **measured flake band** (`lab/simulator/flake_band.py`) to separate caller flakiness from agent flakiness, and it already runs a noise perturbation ladder. What it does not have is EVA-Bench's *scale* (213 scenarios vs 18 audio rows) or an accent axis. |
| **VoiceBench** ([arXiv 2410.17196](https://arxiv.org/abs/2410.17196), [TACL](https://aclanthology.org/2026.tacl-1.18/)) | LLM-based voice assistants: 6,783 spoken instructions, 8 tasks, over general knowledge / instruction-following / safety, with accent, reverberation and content-noise variation | Per-task accuracy under perturbation. Finding: **pipeline (STT→LLM) systems were generally more robust than end-to-end**, and a weak speech encoder makes a model highly vulnerable | **DOES NOT**, and should not. It scores *models*, not applications. Cite the pipeline-vs-end-to-end finding: it is the empirical justification for this repo's cascade-shaped adapter. |
| **VAmoS Bench** ([arXiv 2607.27453](https://arxiv.org/abs/2607.27453)) | Whole voice-agent systems, 100 customer-support scenarios, simulated caller with a private goal, **seeded PostgreSQL backend**, 5 tools doing real SQL, ~1/3 adversarial | **Containment** (share resolved without human handoff), plus binary assertions over the full interaction trace, catching both "claimed a change it did not make" and "made the right change while disclosing protected information" | **PARTLY, and this is the closest sibling in the literature.** Seeded backend + private-goal caller + assertions over a trace is this repo's architecture. What VAmoS has that this repo does not: **containment** as a named business metric, and adversarial pressure on a third of rows. What this repo has that VAmoS does not: a calibrated judge registry that *raises* below threshold, six position-decided contracts, and a channel-perturbation layer. |
| **IHBench** ([arXiv 2606.19595](https://arxiv.org/abs/2606.19595)) | Post-interruption recovery over workflows | task fulfilment + recovery quality | see §3.4 |
| **Full-Duplex-Bench v1/1.5/2/3** ([arXiv 2503.04721](https://arxiv.org/abs/2503.04721), [v2](https://arxiv.org/pdf/2510.07838)) | Duplex conversational behaviour | TOR, backchannel freq, JSD, latencies | see §3.1 |
| **EchoChain** ([arXiv 2604.16456](https://arxiv.org/pdf/2604.16456)) | State-update reasoning under interruption, full duplex | state-tracking accuracy under overlap | **DOES NOT.** Same duplex dependency. The *state-update* framing maps onto `FieldPropagationContract`. |
| **speechocean762** ([overview](https://www.emergentmind.com/topics/speechocean762-dataset)) | Pronunciation assessment | multi-granular human scores | **DOES NOT**, deliberately — §2.6 |
| **EdAcc** ([arXiv 2303.18110](https://arxiv.org/pdf/2303.18110), [HF dataset](https://huggingface.co/datasets/edinburghcstr/edacc)) | 40 h of dyadic English conversation across a wide range of first- and second-language accents | WER by accent. **19.7% average WER for the best model vs 2.7% on US clean read speech**, worst on Indian, Jamaican and Nigerian English | **DOES NOT — and this is the sharpest available answer to the repo's own biggest hole.** §1.3: accent coverage is a paid TTS capability here. EdAcc is *recorded human* audio, so it needs **no TTS at all** — it needs only an STT path. |
| **ASVspoof 5** ([arXiv 2502.08857](https://arxiv.org/abs/2502.08857), [CSL](https://dl.acm.org/doi/10.1016/j.csl.2025.101825)) | Spoofing / deepfake / adversarial-attack detection; ~2,000 speakers, 32 attack algorithms, crowdsourced acoustic conditions, adversarial attacks included for the first time | detection EER etc. | **DOES NOT.** See §6.4. |

---

## 6. Adversarial and robustness testing for speech

### 6.1 Speech Robust Bench — the accepted method for perturbation testing

**What it is.** **114 input perturbations** — environmental, digital and
adversarial — applied at graded severity, with a taxonomy separating adversarial
from non-adversarial, scored with **WERD (WER Degradation)** and **NWERD**
(normalised), and broken out by demographic subgroup (English/Spanish, male/female)
([arXiv 2403.07937](https://arxiv.org/abs/2403.07937),
[ICLR 2025 proceedings PDF](https://proceedings.iclr.cc/paper_files/paper/2025/file/605e02ae04cba1ebf6a08206299e76b9-Paper-Conference.pdf)).
Headline: robustness to non-adversarial noise and robustness to adversarial attack
are **anti-correlated** — Whisper and Canary are robust to noise and vulnerable to
utterance-specific adversarial attacks; wav2vec2 is the reverse.

**Does this repo do it?** **PARTLY — same method, 5 perturbations vs 114, and a
better reporting habit.** Verified:
`python -c "import lab.voice.perturb as p; print(sorted(p.PERTURBATIONS))"` →
`['add_noise','packet_loss','resample_speed','shift_pitch','telephone_band']`, all
five used by committed scenarios. The repo grades severity (ladders), seeds them,
and **measures the perturbation back out of the assembled clip** (declared 20/10/6/3/0
dB → measured 20.00/10.00/6.00/3.00/0.00 dB) before believing the result — which is
a control SRB's write-up does not emphasise. WERD is not a named metric here, but
the ladder-with-a-breaking-point is the same idea with a sharper output: `SW1A 1AA`
holds to 0 dB SNR, breaks at **−5 dB** as `SW1A 1AF` — *a plausible wrong postcode* —
and returns empty at −10 dB. The finding that the dangerous band is the *milder* one
is exactly what a WERD curve averages away.

**Missing perturbation families** compared to what a 114-strong bank contains:
**reverberation / room impulse response**, **codec artefacts** (Opus, G.711 — real
for any telephony path), **recorded environmental noise** rather than synthetic
pink/white, **crosstalk / competing speaker**, and **gain/clipping**. **ASSUMPTION:**
that reverb and codec are the two highest-value additions, on the grounds that both
are unavoidable in a real call path while `shift_pitch` is not — I have no
measurement supporting that ranking.

**Worth doing here?** **Two additions, not a bank.** Reason: the repo's admission
rule ("a row belongs here only if the audio layer is the thing under test") argues
against a 114-perturbation sweep, and the documented cost of a ladder that never
breaks is a ladder that reports nothing. Reverb and a codec round-trip are the two
that a real deployment cannot avoid; the rest is padding that would look more
thorough and be less true — the repo's own phrase for the vendor matrix.

### 6.2 Accent

**What it is.** §5, EdAcc: 19.7% WER for the best model on accented conversational
English against 2.7% on US clean read speech, on the same model.

**Does this repo do it?** **DOES NOT.** §1.3 — two stock voices, Voice Library
gated behind a paid tier, and the tag vocabulary was *pruned* of unused caller-voice
locales rather than kept as aspiration. That pruning was the right call and it
should be cited as one.

**Worth doing here?** **Yes, and it is the highest-value unblock in the document
after duplex, because it needs no TTS vendor.** Reason: the harness's accent gap is
caused by the *synthesis* side. EdAcc is recorded human speech, so an accent tier
built on it uses only the STT path and the existing field/WER scoring. It changes
what the harness can claim from "we cannot test accents" to "we test accents on
recorded human audio and cannot test accent *plus* our own scenario content" —
which is a much narrower and more defensible limitation. **ASSUMPTION:** EdAcc's
licence permits this use; I did not check the licence terms.

### 6.3 Disfluency

**What it is.** VocalBench-DF evaluates speech-LLMs over a disfluency taxonomy
across 22 mainstream systems and finds **substantial degradation**, localising the
bottleneck to phoneme-level processing and long-context modelling
([arXiv 2510.15406](https://arxiv.org/abs/2510.15406)). Full-Duplex-Bench v3
crosses tool use with **5 disfluency types** — fillers, pauses, hesitations, false
starts, self-corrections ([GitHub](https://github.com/DanielLin94144/Full-Duplex-Bench)).

**Does this repo do it?** **PARTLY, one type only.** `insert_pause` /
`pause_for_silence` handle the *pause* type with millisecond rigour. Fillers,
hesitations, false starts and self-corrections are not injected.

**Worth doing here?** **Yes for self-correction specifically, no for the rest.**
Reason: `docs/AUDIO_SUITE.md` §14 already names the gap and it is not hypothetical —
the containment matcher passes `actually not SW1A 1AA but EC1A 1BB`, where the
value the agent should record is the second one. That is the **self-correction**
disfluency type, it is a genuine instance of the repo's own reference bug 4, and
the doc says **no row in this tier exercises it**. Closing it needs a row that
declares a *final* value — a schema change, not a matcher change. Fillers and
hesitations without that would be perturbations with nothing to assert.

### 6.4 Spoofing

**What it is.** ASVspoof 5 (§5): the standing community challenge, now with
adversarial attacks and crowdsourced acoustic diversity.

**Does this repo do it?** **DOES NOT.**

**Worth doing here?** **No.** Reason: spoofing detection is a property of a speaker
verification system. This harness has no verification step and no identity claim to
attack — a spoofing row would have nothing to fail. Record it as a bounded exclusion
with a named condition for revisiting: *if a scenario ever gates an action on caller
identity, this becomes in scope.*

### 6.5 Code-switching

Covered at §2.4. Supporting datasets if the corpus ever grows:
[CS-FLEURS](https://www.isca-archive.org/interspeech_2025/yan25c_interspeech.pdf),
[SwitchLingua](https://openreview.net/pdf/a8f9202b5127d5a80e4b7a1962253382b2b5270c.pdf)
(12 languages, 63 ethnic groups, 27 topics),
[commercial ASR on code-switched Arabic/Persian/German](https://arxiv.org/pdf/2605.19069).
Repo verdict unchanged: the concept is right, the n is two, and the reason the n is
two is a documented vendor limit.

---

## 7. Open-source projects doing voice-agent evaluation

### 7.1 ServiceNow **EVA** — the closest functional competitor

([GitHub](https://github.com/ServiceNow/eva), MIT, ~202 stars / 1,444 commits;
[HF write-up](https://huggingface.co/blog/ServiceNow-AI/eva); paper §5)

**What it does well.** A genuinely end-to-end automated loop — "speech in to
judgment out", no human listeners. Three components: an **audio user simulator**
(ElevenLabs Agents, or OpenAI Realtime in beta) so turn-taking dynamics are real
rather than scripted; the **agent under test** as a Pipecat pipeline supporting both
cascade (STT→LLM→TTS) and speech-to-speech; and a **deterministic tool executor**
backed by scenario databases. Metrics split accuracy (task completion —
deterministic; faithfulness — LLM judge; agent speech fidelity — audio LLM judge,
beta) from experience (turn taking — deterministic; conciseness and conversation
progression — LLM judges). Stated limitations are unusually honest: the Realtime
caller is unvalidated at scale, transcription errors contaminate faithfulness and
progression, and WER normalisation needs per-language configuration.

**What this repo has that EVA does not.**
- **Judge calibration that raises.** EVA has LLM judges; `lab/judges` has
  `calibrate()` returning TPR/TNR/precision/recall/F1/kappa/confusion and a registry
  that **refuses to serve a judge below threshold**. EVA's own limitations section
  says transcription error contaminates its judged metrics; this repo's answer to
  that class of problem is a measured one.
- **A timing calibration gate with a failing control.** EVA reports turn-taking
  timing; nothing in it establishes that its own clock is trustworthy first. The
  naive-control table (§1.1) is a genuinely unusual artefact.
- **Position-decided contracts.** Six declarative contracts on event-stream
  position rather than timestamps — a different and more durable representation
  than per-turn judging.
- **Zero-key replay.** The whole audio tier reruns from committed fixtures at
  **0 synthesis characters**. EVA's loop requires live vendor calls. **ASSUMPTION:**
  I did not find a replay/cassette mode in EVA's documentation; absence of evidence.
- **Three outcomes.** `runnable` / `blocked` / `untestable`, with `passed=null` and
  no naked percentages.

**What EVA has that this repo does not.**
- A **real audio user simulator** producing genuine turn-taking dynamics, which is
  the thing §3/§4 keep coming back to.
- A first-class **speech-to-speech** path, not just cascade.
- 213 scenarios across 3 domains vs 18 audio rows.
- An **audio LLM judge** for speech fidelity — judging the audio, not the transcript.

### 7.2 Pipecat Evals

([docs](https://docs.pipecat.ai/pipecat/fundamentals/evaluations/overview)) —
scripted or AI-driven test calls over API, WebSocket or telephony, exercising
multi-turn flows. Strength: it drives the **real transport**, including telephony.
This repo has a WebRTC transport tier but no telephony path (and
`docs/AUDIO_TRANSPORT.md` is explicit that both ends live in one process, so its
delivery gap is a floor).

### 7.3 LiveKit's own test framework — worth knowing precisely

([docs.livekit.io/agents/start/testing](https://docs.livekit.io/agents/start/testing/))
pytest/Vitest helpers asserting on messages, tool calls, arguments and handoffs turn
by turn, with an LLM judge for open-ended replies. **The framework and agent
simulations both run in text mode.** It explicitly does **not** cover audio quality,
barge-in or turn-taking mechanics, and points users at third-party tools for those.

**Why this matters for this repo.** The most widely deployed open-source voice-agent
framework ships a test story that stops exactly where audio starts — and this repo
already has a LiveKit WebRTC transport tier, an audio suite and a calibration gate.
That is the sharpest available statement of where this work sits.

### 7.4 Commercial landscape (named for completeness, not adopted)

LiveKit's own docs name **Bluejay, Cekura, Coval and Hamming** as the tools to use
for full audio-pipeline testing. Coval publishes reproducible TTS/STT latency and
accuracy benchmarking
([coval.dev](https://www.coval.dev/), [Coval GitHub](https://github.com/coval-ai));
Cekura auto-generates test scenarios from agent configuration
([roundup](https://www.speechmatics.com/company/articles-and-news/de-risk-your-voice-agent-11-best-voice-agent-testing-platforms)).
**Relevance:** these are the tools a reader will compare this repo against, and the
comparison this repo wins on is auditability — calibrated judges, refusals with
reasons, committed evidence, denominators — not scale.

### 7.5 Speech-quality models the repo could reuse without a vendor

**NISQA** (MIT licence, ~218K parameters, non-intrusive MOS plus diagnostic quality
dimensions — [GitHub](https://github.com/gabrielmittag/NISQA/blob/master/README.md),
[PyPI](https://pypi.org/project/nisqa/)) and **UTMOS** (non-intrusive MOS predictor,
Pearson ~0.82 against human MUSHRA on neural-codec conditions —
[overview](https://www.emergentmind.com/topics/utmos)).

**Does this repo do it?** **DOES NOT.** There is no perceptual audio-quality number
anywhere: `perturb.py` measures SNR back out, and `transport/measure.py` measures
per-frame energy and silence share, but nothing scores how the degraded audio
*sounds*.

**Worth doing here?** **Probably not, and the reason is a repo rule.** Reason: a
MOS predictor is an uncalibrated judge in the sense of §2.2 — it would need its own
calibration against something before its numbers could be quoted, and this repo
does not print numbers it cannot audit. There is one narrow exception worth flagging
to the owner: NISQA on the **transport** tier's received audio would give the packet-loss
row a perceptual axis alongside its capture axis, and the transport tier already has
both an unperturbed control arm and a documented baseline habit ("each side of the
comparison has its own baseline"), which is the structure a MOS number needs to be
meaningful. **ASSUMPTION:** that NISQA runs on 16 kHz mono float audio without
resampling work; unverified.

---

## 8. Latency: how the field names it, and what this repo already has right

**The field's vocabulary.** Time to First Token (**TTFT**) is the LLM-side number;
Time to First Audio / **TTFAB** (time to first audio byte) is "the gap between the
moment the caller stops speaking and the moment the agent's audio starts — the
silence a real caller sits through on every turn". Endpointing plus ASR typically
consumes 150–300 ms and TTS another 100–200 ms before the first audio frame; the two
largest line items in time-to-first-audio are **the LLM's TTFT and endpointing**
([AssemblyAI, TTFT for voice agents](https://www.assemblyai.com/blog/time-to-first-token-voice-agents);
[Gradium, Time to First Audio](https://gradium.ai/blog/time-to-first-audio);
[HF, Voice Agent Latency Playbook](https://huggingface.co/blog/dvalle08/voice-agent-latency-playbook)).
The standing advice is **report percentiles, not averages** — p50 for typical
experience, p90 for how often it feels stuck.

**Does this repo do it?** **DOES, and with more discipline than the sources.**

- The agent-side/receiver-side distinction is **in the schema as two event kinds** —
  `agent_audio_first_byte` vs `audio_delivered` — not as two derived numbers, with
  the reasoning written into `lab/trace/schema.py`. That is TTFT-vs-TTFA made
  structural.
- Percentiles are the default (`Distribution`, `Quantile`) and
  `min_samples_for_quantile()` makes a p95 **refusable** rather than printable at
  n=3. The transport tier refuses p95 explicitly and says three rows are not a
  distribution.
- The whole latency stack sits behind a calibration gate whose naive control fails
  on the three shortest rungs — the regime where a voice budget actually lives.

**The gap.** There is no **endpointing** contribution in the breakdown, because
there is no endpointer (§3.3), and the delivery gap is a floor because both ends
share a process. Both are already stated in `docs/AUDIO_TRANSPORT.md`.

**Worth doing here?** **No new work; a naming pass.** Reason: the repo measures the
right two quantities and calls them by internal names. Saying "`agent_audio_first_byte`
→ `audio_delivered` is TTFT vs TTFA" in one line converts an internal design
decision into a recognisable claim.

---

## 9. Decision table — for the owner, not a plan of record

Ordered by (value ÷ cost), not by interest. Nothing here is started.

| # | item | verdict on repo today | cost shape | why |
|---|---|---|---|---|
| 1 | **Name what already exists in the field's vocabulary** — TTFT/TTFA, early-cutoff family for `vad_false_silence`, NoEP for `would_not_fire`, IPU/gap/overlap taxonomy, EER for the field assertions, CER via `scoring_unit()` | measures it, names it privately | documentation only, no code | Converts existing rigour into claims a reader recognises. Zero risk. |
| 2 | **Aggregate entity error rate** over the existing `digits-and-names` rows | PARTLY (per-row, no roll-up) | small; reuses `capture_outcome` | §2.1. A named metric with a denominator, no new audio, no new vendor. |
| 3 | **A self-correction row** that declares a *final* value | DOES NOT; the gap is documented and the matcher blind spot is test-pinned | schema change (declare final value) + one row | §6.3. Closes a documented instance of the repo's own reference bug 4. |
| 4 | **Accent tier over recorded human audio** (EdAcc or similar) | DOES NOT; blocked by the *synthesis* side only | STT path only; licence check first | §6.2. Turns "we cannot test accents" into a narrow, defensible limitation. |
| 5 | **Reverb + codec round-trip** perturbations | PARTLY (5 of the families a full bank has) | two functions, ladders like the existing ones | §6.1. Both unavoidable in a real call path. Explicitly *not* a 114-perturbation bank. |
| 6 | **Paired clean-text vs ASR arm** (AER-style diagnosis) | PARTLY | reuses corpus; zero synthesis characters | §2.3. Proves the audio-tier admission rule was applied correctly. |
| 7 | **Duplex adapter + streaming STT** | DOES NOT; structurally blocked | large; new vendor surface | §3.1/§3.4/§4. Unlocks TOR, latency-after-interruption, real barge-in detection, and contract-scored interruption recovery — and flips the `blocked` row with no scenario edit. **Decide it once, as one thing.** |
| — | Semantic error rate | DOES NOT | — | **Decline** (§2.2): uncalibrated judge; the failure it targets is already caught deterministically. |
| — | Pronunciation assessment | DOES NOT | — | **Decline** (§2.6): grades a speaker; this harness has none. |
| — | Spoofing / ASVspoof | DOES NOT | — | **Decline** (§6.4): no identity claim to attack. Revisit if a scenario ever gates on caller identity. |
| — | Running FDB / VoiceBench / EVA-Bench as suites | DOES NOT | — | **Decline** (§3.1, §5): they score models; this scores an application. Borrow vocabulary, not the harness. |
| — | MOS prediction (NISQA/UTMOS) | DOES NOT | — | **Probably decline** (§7.5), with one narrow exception on the transport packet-loss row. |

---

## 10. Defects — **none found.** Two suspicions raised and cleared, one observation

Recorded in full, including the two that turned out to be my own error, because a
research pass that only reports its confirmed suspicions is not auditable.

1. **Suspected: `docs/AUDIO_SUITE.md` duplicates section 22. — FALSE.**
   `grep -c '^## 22\.' docs/AUDIO_SUITE.md` → **1**, at line 1273, with `## 21.` at
   1256 and `## 23.` at 1307. The apparent duplication was an artefact of two
   overlapping `sed` ranges I read it through. No issue; nothing to fix.
2. **Suspected: the "field matcher" named in `docs/AUDIO_SUITE.md` §14 has no
   corresponding function. — FALSE.** It is `capture_outcome` at
   `lab/voice/suite.py:730`, exercised by
   `tests/test_audio_suite.py:737` (`test_the_matcher_is_containment_and_this_is_
   the_limit_of_it`). My first grep searched for the words "field" and
   "containment" rather than the function's actual name. The documented blind
   spots are genuinely pinned: the test asserts that containment **accepts** a
   superstring (`s w one a one a a b`), the value surrounded by noise, and the
   self-correction case, and its docstring states that closing the last of these
   is a schema change, not a matcher change. That is the finding item 3 in §9
   rests on, and it is real.
3. **Observation, not a defect: `shift_pitch` has the weakest justification of the
   five perturbations.** Used by exactly one scenario
   (`scenarios/voice/voice-pitch-shift-unfamiliar-voice.yaml`), and unlike noise,
   band-limiting and packet loss it does not correspond to something a real call
   path does to audio. Not wrong — but if item 5 in §9 adds two perturbation
   families, this is the one to ask "what does it prove" about first.

---

## Sources

- [Beyond Word Error Rate: Auditing the Diversity Tax in Speech Recognition through Dataset Cartography](https://arxiv.org/pdf/2603.05267)
- [Speech LLMs are Contextual Reasoning Transcribers (Entity Error Rate)](https://arxiv.org/pdf/2604.00610)
- [Deepgram — Speech Recognition Accuracy: Production Metrics (Keyword Recall Rate, PER, RTF)](https://deepgram.com/learn/speech-recognition-accuracy-production-metrics)
- [Deepgram — Semantic Error Rate](https://deepgram.com/learn/semantic-error-rate-asr-accuracy-metric)
- [Semantic-WER: A Unified Metric for the Evaluation of ASR Transcript for End Usability](https://arxiv.org/pdf/2106.02016)
- [Analyzing Error Propagation in Korean Spoken QA with ASR–LLM Cascades (Answer Error Rate)](https://arxiv.org/pdf/2605.17443)
- [PIER: A Novel Metric for Evaluating What Matters in Code-Switching (ICASSP 2025)](https://arxiv.org/pdf/2501.09512)
- [Advocating Character Error Rate for Multilingual ASR Evaluation (Findings of NAACL 2025)](https://aclanthology.org/2025.findings-naacl.277/)
- [speechocean762 — pronunciation assessment corpus](https://www.emergentmind.com/topics/speechocean762-dataset)
- [Segmentation-free Goodness of Pronunciation](https://arxiv.org/abs/2507.16838)
- [Read to Hear: A Zero-Shot Pronunciation Assessment Using Textual Descriptions and LLMs](https://arxiv.org/pdf/2509.14187)
- [Full-Duplex-Bench (arXiv 2503.04721)](https://arxiv.org/abs/2503.04721) · [GitHub](https://github.com/DanielLin94144/Full-Duplex-Bench) · [IEEE](https://ieeexplore.ieee.org/document/11433838/)
- [Full-Duplex-Bench-v2 (arXiv 2510.07838)](https://arxiv.org/pdf/2510.07838)
- [Talking Turns: Benchmarking Audio Foundation Models on Turn-Taking Dynamics (ICLR 2025)](https://arxiv.org/abs/2503.01174) · [poster](https://iclr.cc/virtual/2025/poster/31129)
- [IHBench: Evaluating Post-Interruption Recovery in Voice Agents with Structured Workflows](https://arxiv.org/abs/2606.19595)
- [EchoChain: A Full-Duplex Benchmark for State-Update Reasoning Under Interruptions](https://arxiv.org/pdf/2604.16456)
- [EVA-Bench: A New End-to-end Framework for Evaluating Voice Agents](https://arxiv.org/abs/2605.13841) · [GitHub (MIT)](https://github.com/ServiceNow/eva) · [HF write-up](https://huggingface.co/blog/ServiceNow-AI/eva)
- [VAmoS Bench: Voice Agent Simulation Bench](https://arxiv.org/abs/2607.27453)
- [VoiceBench: Benchmarking LLM-Based Voice Assistants](https://arxiv.org/abs/2410.17196) · [TACL](https://aclanthology.org/2026.tacl-1.18/)
- [Speech Robust Bench: A Robustness Benchmark For Speech Recognition](https://arxiv.org/abs/2403.07937) · [ICLR 2025 PDF](https://proceedings.iclr.cc/paper_files/paper/2025/file/605e02ae04cba1ebf6a08206299e76b9-Paper-Conference.pdf)
- [VocalBench-DF: Speech LLM Robustness to Disfluency](https://arxiv.org/abs/2510.15406)
- [The Edinburgh International Accents of English Corpus (EdAcc)](https://arxiv.org/pdf/2303.18110) · [HF dataset](https://huggingface.co/datasets/edinburghcstr/edacc)
- [ASVspoof 5: Design, Collection and Validation of Resources](https://arxiv.org/abs/2502.08857) · [Computer Speech & Language](https://dl.acm.org/doi/10.1016/j.csl.2025.101825)
- [CS-FLEURS: A Massively Multilingual and Code-Switched Speech Dataset (Interspeech 2025)](https://www.isca-archive.org/interspeech_2025/yan25c_interspeech.pdf)
- [SwitchLingua: The First Large-Scale Multilingual and Multi-Ethnic Code-Switching Dataset](https://openreview.net/pdf/a8f9202b5127d5a80e4b7a1962253382b2b5270c.pdf)
- [Benchmarking Commercial ASR Systems on Code-Switching Speech: Arabic, Persian, German](https://arxiv.org/pdf/2605.19069)
- [Streaming Endpointer for Spoken Dialogue using Neural Audio Codecs and Label-Delayed Training](https://arxiv.org/html/2506.07081)
- [Improving endpoint detection in end-to-end streaming ASR for conversational speech](https://arxiv.org/pdf/2505.17070)
- [Two-pass Endpoint Detection for Speech Recognition](https://arxiv.org/pdf/2401.08916)
- [AssemblyAI — Time to First Token: The Voice Agent Latency Metric](https://www.assemblyai.com/blog/time-to-first-token-voice-agents)
- [Gradium — Time to First Audio](https://gradium.ai/blog/time-to-first-audio)
- [Hugging Face — The Voice Agent Latency Playbook](https://huggingface.co/blog/dvalle08/voice-agent-latency-playbook)
- [LiveKit — Testing and evaluation (agents)](https://docs.livekit.io/agents/start/testing/)
- [Pipecat Evals — overview](https://docs.pipecat.ai/pipecat/fundamentals/evaluations/overview)
- [Coval](https://www.coval.dev/) · [Coval GitHub](https://github.com/coval-ai)
- [Speechmatics — roundup of voice-agent testing platforms](https://www.speechmatics.com/company/articles-and-news/de-risk-your-voice-agent-11-best-voice-agent-testing-platforms)
- [NISQA (MIT)](https://github.com/gabrielmittag/NISQA/blob/master/README.md) · [PyPI](https://pypi.org/project/nisqa/)
- [UTMOS — overview](https://www.emergentmind.com/topics/utmos)
