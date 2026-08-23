# The audio suite: real engines, and the numbers that turn out to be wrong

**What this covers:** wiring the two real speech vendors into `lab/`, and what
running them revealed. Everything below was measured against live APIs on
23 Aug 2026. Nothing here is inferred from documentation, and several of the
findings contradict it.

**Why an audio suite at all.** A text harness can evaluate what an assistant
*says*. It cannot evaluate a voice product, because two of the failure modes that
actually take voice teams down are structurally invisible in a transcript: there
is no silence in text, and no two turns can overlap. For a product doing live
in-call support — factual corrections and compliance reminders delivered while an
adviser is mid-sentence — those two absences cover the core interaction.

> ## The admission rule, which is why this tier is small
>
> **A row belongs in the audio tier only if the audio layer is the thing under
> test.**
>
> Compliance logic, disclosure ordering, objection handling and judge calibration
> are all cheaper, faster and more repeatable in text. Putting any of them behind
> a synthesiser buys nothing but cost and variance — and it buys a *worse* test,
> because a failure then has two candidate causes instead of one.
>
> This tier is therefore **eighteen rows**, deliberately, and the constant that
> used to demand fifty was lowered to match rather than the rule being bent to
> reach it. A minimum that cannot be met without breaking the entry rule is not a
> quality bar; it is pressure to break the entry rule. Sections 10 to 14 are the
> tier: what each row proves, the vendor capability matrix, what running it
> measured, and what it cannot tell you.

---

## 1. The probe: two undocumented facts, 108 characters

Everything downstream depended on two things the vendor documentation does not
state, so they were measured first, before any design. One 27-character sentence
— `"Ring at 7:30 from SW1A 1AA."` — one premade voice, four requests.

Evidence: [`fixtures/audio/cloud/normalized_alignment_probe.json`](../fixtures/audio/cloud/normalized_alignment_probe.json)

### (a) Does `/with-timestamps` return a populated `normalized_alignment`?

| model | `apply_text_normalization` | `normalized_alignment` | verdict |
|---|---|---|---|
| `eleven_flash_v2_5` | `"on"` | `" Ring at seven thirty from S W one A one A A. "` | **spoken form** |
| `eleven_multilingual_v2` | `"on"` | `" Ring at seven thirty from S W one A one A A. "` | **spoken form** |
| `eleven_flash_v2_5` | `"auto"` (the API default) | `" Ring at 7:30 from SW1A 1AA. "` | input, padded |
| `eleven_v3` | `"on"` | `"Ring at 7:30 from SW1A 1AA."` | input, unpadded |

**It is never null.** The field's nullability was the wrong thing to worry about.
Its *content* is the problem, and there are two traps in that table:

1. **Under `"auto"` — the default — the field is the input text with a space added
   at each end, while the audio still speaks the numerals aloud.** The two clips
   were 3.808 s and 3.854 s long: the model said "seven thirty" either way and
   only *reported* it in one of them. A field called `normalized_alignment` that
   is not a normalised form and cannot be distinguished from one by inspection.

2. **`eleven_v3` silently ignores the request.** It accepts the parameter, returns
   HTTP 200, and hands back `normalized_alignment` byte-identical to `alignment`.
   The newest and most capable model is the one that cannot supply a WER
   reference, and it fails by returning a plausible string rather than an error.

So the "can I trust this reference?" predicate is **model-aware**, not
request-aware. `SPOKEN_FORM_MODELS` holds the two measured to honour it, and
`eleven_v3` is deliberately absent. Asking only "did I request normalisation?" —
as the interrupted first draft of the engine did — returns `True` for
`eleven_v3` + `"on"` and hands a written-form reference to the WER.

When no spoken form is available the engine **declines to publish one**:
`spoken_text` stays `None`, `reference_source` reads `caller-input`, and the
fallback is visible in the trace and named in the report. The fallback is not the
bug. An invisible fallback is the bug.

### (b) Does `pcm_16000` work on a Free-tier key?

**Yes.** All four requests returned real 16 kHz mono PCM — 121,858 bytes for the
sentence above, peak amplitude 0.68, not silence and not an error. 16 kHz is
exactly the pipeline's working rate, so the usual path has no decode step, no
codec dependency and no resample between the synthesiser and a word error rate.

### A third fact, unasked for

`GET /v1/user/subscription` reported `character_count` **284 before** the four
probe calls, **284 after**, and still 284 on a later re-read — 108 characters of
synthesis the vendor's own counter never moved. Whatever the cause, the
consequence is settled: **the budget guard is a local ledger, not a poll.** A cost
control that depends on a counter which does not update in time is a cost control
that reports the overspend afterwards.

---

## 2. Audit: the orphaned `elevenlabs_tts.py`

652 lines existed from an interrupted build, imported by nothing.

**Verdict: finish it, not replace it.** Its central argument — call
`/with-timestamps`, take the reference from `normalized_alignment`, cache by
content digest, guard the spend, cross-check the decoded duration against the
alignment — is correct and matches what the probe measured. Replacing it would
have thrown away right reasoning. Six things were wrong:

| defect | consequence |
|---|---|
| Passed `spoken_text=` to `SynthesisResult`, which has `extra="forbid"` and no such field | **The file could not execute a single synthesis.** This is why nothing imported it. |
| `reference_is_spoken_form` checked only the request, not the model | Returns `True` for `eleven_v3`, reintroducing the exact bug the module exists to prevent |
| Cache defaulted to `~/.cache` only | A fresh clone gets a miss on every line, so "re-runs are free" was true only on the machine that paid |
| Budget counted raw characters | The real multiplier is on the model (0.5x flash, 1.0x v3), so a v3 run costs double what the guard thinks |
| No voice guard | Nothing stopped a non-premade voice, which is the only kind that could carry a surcharge |
| Claimed responses were committed under `fixtures/audio/cloud/` | The directory did not exist |

Its docstring also cited `eleven_turbo_v2_5`, which is deprecated. The measured
table has been replaced with the probe above.

---

## 3. What was built

| module | what it is |
|---|---|
| `lab/voice/engines/elevenlabs_tts.py` | Synthesis. Model-aware spoken-form predicate, premade-voice allowlist, credit ledger, `pcm_16000`, duration cross-check. |
| `lab/voice/engines/deepgram_stt.py` | Recognition. `nova-3`, pre-recorded endpoint, verbatim by default, word-level timings and confidence. No SDK and no `httpx` — the request is a POST, so it uses `urllib`. |
| `lab/voice/engines/clipcache.py` | The digest cache. Two layers: committed (in-repo, read-only) and scratch (outside, writable). |
| `lab/voice/engines/coverage.py` | The market matrix, computed from the two capability sets rather than written down. |
| `lab/voice/interaction.py` | Silence attribution and barge-in — the two things text cannot see. |

### Voices, and why those

Free-tier keys cannot use Voice Library voices over the API, so both defaults are
`category == "premade"`:

- **caller — George** (`JBFqnCBsd6RMkjVDRZzb`), British. Chosen for the widest
  verified-language set in the premade pool (en, fr, es, hi, ja, ar, cs, fil),
  which lets one caller voice carry the English rows *and* all four testable
  code-switching pairs without a voice change becoming a confound.
- **agent — Alice** (`Xb7hH8MSUJpSbSDYk0k2`), British, female. Chosen to be
  plainly distinguishable from George, because a report that cannot tell the two
  synthesisers apart cannot attribute a regression to a side.

### The credit multiplier lives on the model, not the voice

`GET /v1/models` returns `model_rates.character_cost_multiplier`: **0.5** for
`eleven_flash_v2_5` and `eleven_v3_conversational`, **1.0** for `eleven_v3` and
`eleven_multilingual_v2`. The same sentence costs twice as much on v3.

`GET /v2/voices` exposes **no per-voice cost field at all**. The full key union is
`available_for_tiers, category, collection_ids, created_at_unix, description,
favorited_at_unix, fine_tuning, high_quality_base_model_ids, is_bookmarked,
is_legacy, is_mixed, is_owner, labelling_status, labels, name,
permission_on_resource, preview_url, recording_quality,
recording_quality_reason, safety_control, samples, settings, sharing,
verified_languages, voice_id, voice_verification` — nothing cost-shaped, and
`available_for_tiers` is empty on every voice.

So a per-voice multiplier cannot be read from the API. The enforceable guard is
therefore an **allowlist of the 21 measured premade voice ids**, plus a ledger
charged in credits at the model rate. The account's one non-premade voice
(`category: "professional"`) is refused, and the override has to be typed.

---

## 4. The five reference bugs, and where each one lives

| # | bug | where it is caught |
|---|---|---|
| 1 | **Silence misattribution.** "silence-timed-out" labelled calls where the caller was talking. | `interaction.attribute_silence` — three verdicts where production had one label |
| 2 | **`e2e_latency` is agent-side and excludes delivery.** | `adapter.audio_delivered_latency_report` — *refuses* unless `tts_owner == "sut"` |
| 3 | **Silent STT corrections, 68.7% unattributable in production.** | `adapter.silent_correction_report` — 100% attributable here, because we synthesised the audio |
| 4 | **Read-back failures on names, dates of birth, postcodes.** | `adapter.readback_report` — field assertions, never WER |
| 5 | **Interruption metrics exist and nothing uses them.** | `interaction.emit_barge_in` / `barge_in_report` — the events are now emitted *and* consumed |

### Bug 1 in detail, because it is the one that justifies the suite

Production ended calls with `callEndReason = "silence-timed-out"`. The label was
believed, the investigation went looking for quiet callers, and it was
misdiagnosed for weeks. The real cause was two things compounding: under
`turn_detection="stt"` the VAD was driving `user_state` (upstream fix unmerged),
and `away_timeout` was 6 seconds — aggressive enough that ordinary thinking pauses
reached it.

One label, two completely different events, **opposite remedies**:

- *the caller really went quiet* → raise the timeout, or prompt
- *the caller was speaking and we could not tell* → fix turn detection; changing
  the timeout only postpones it

Telling them apart requires knowing whether there was speech in the window.
Production cannot know — it has only the detector whose failure is the question.
An audio harness can, because it synthesised the audio and can measure energy
independently of any detector. Deliberately **RMS, not a VAD**: the failure under
investigation is a VAD failure, and using a VAD to adjudicate it would be
measuring the instrument with itself.

The tests run at the shipped 6 seconds, on real clips, and assert both halves of
the threshold (5.9 s does not fire, 6.1 s does) *and* the attribution.

---

## 5. The headline: which of the 24 markets can be audio-tested

Computed by `lab/voice/engines/coverage.py`, not written down, so a vendor adding
a language turns a test red instead of leaving a document stale.

| verdict | markets | why |
|---|---|---|
| **untestable** | Hong Kong (en + `zh-HK`) | **No TTS model in this stack can synthesise Cantonese.** There is no audio, so there is no row. |
| **monolingual** | Singapore (en + `zh`), UAE (en + `ar`) | Both languages synthesise and transcribe fine. Neither `zh` nor `ar` is in Deepgram's code-switching set, so a caller switching mid-sentence cannot be tested. |
| **code-switched** | UK, US, Spain, India, Japan, France, Germany | Fully testable end to end, including mid-sentence switching. |

**The Cantonese finding, verified against the live API rather than the docs:** zero
occurrences of `yue`, `zh-HK` or "Cantonese" across all nine ElevenLabs models. The
only Chinese language id is `zh`, named "Mandarin Chinese" on `eleven_v3`. The
Voice Library's 13 voices labelled with a "Cantonese (Hong Kong)" accent sit under
`language=Chinese` — that is **voice metadata, not model support**, and selecting
one does not make the model speak Cantonese.

Recognition is *ahead* of synthesis here, which is what makes the gap structural:
Deepgram `nova-3` transcribes `zh-HK` and distinguishes Cantonese from Mandarin.
ElevenLabs does not model it at all.

**Remediation:** Azure AI Speech and Google Cloud TTS both offer `yue-HK`. Adding
either behind the existing `TTSEngine` protocol moves Hong Kong from *untestable*
to *monolingual* — and no further, since `zh-HK` is still outside Deepgram's ten,
so an English/Cantonese switching row stays out of reach on this recogniser.

The three verdicts are three because collapsing to pass/fail would lose the middle
one. Calling Singapore "covered" overclaims; calling it "untestable" hides that
Hong Kong is a different and worse problem.

**One limit worth stating rather than discovering:** Deepgram's own guidance for
code-switched audio is to drop `endpointing` to 100 ms — and `endpointing` is a
*streaming* parameter with no meaning on the pre-recorded endpoint. So the
vendor's recommended mitigation for the hardest multilingual case is unavailable
to this batch harness. Not fixable by tuning. Sending the parameter anyway would
be claiming a mitigation never applied.

---

## 6. What the live round trip actually measured

17 lines, both real engines, twice each (verbatim and smart-formatted).
Evidence: [`fixtures/audio/cloud/round_trip_evidence.json`](../fixtures/audio/cloud/round_trip_evidence.json)

The central table. Normalised WER, same audio and same transcript in every row —
only the **reference** and the **formatting** change:

| row | spoken-form / raw | spoken-form / smart | caller-input / raw | field captured? |
|---|---|---|---|---|
| `readback-postcode` | **0.000** | 0.700 | **1.400** | yes |
| `readback-account-number` | **0.000** | 0.000 | **1.250** | yes |
| `readback-sort-code` | 0.200 | 0.800 | **1.429** | yes |
| `readback-dob` | **0.000** | **0.556** | 0.000 | yes |
| `readback-irish-name` | 0.400 | 0.400 | 0.400 | **no** |

Read the postcode row: the recognition was perfect, the field-level assertion says
captured, and the *same transcript* scores **0.000 or 1.400** depending only on
which string it is compared against. Scored the wrong way, the ten rows whose
entire purpose is proving a postcode survives the channel become the worst
category in the suite while both vendors are working — and somebody swaps a vendor
to fix nothing.

The `readback-dob` row shows the other axis: smart formatting rewrote "the
fourteenth of March, nineteen eighty-two" as `03/14/1982` (US order, for a UK
date), which is 5 errors in 9 words of pure formatting policy.

`readback-irish-name` is a **genuine failure**, and the only one: "Siobhan
O'Rourke" was heard as "siobono rock". The field assertion says missed and the
per-word confidences localise it — the two name tokens came back at 0.72 and 0.75
while every other row sits above 0.99. The utterance-level confidence stayed at
0.93 and pointed at nothing, which is exactly why word-level figures are recorded.

### Three refusals, enforced in code

1. **No latency without the calibration gate.** `audio_latency_report` raises
   `LatencyUnproven` unless `lab/voice/calibration.py`'s verdict is `PASS`.
   `NOT_RUN` is refused too — a skipped gate is not a passed one.
2. **No delivered latency when the harness owned the voice.**
   `audio_delivered_latency_report` raises `DeliveredLatencyUnavailable` unless
   `tts_owner == "sut"`. When this harness synthesised the agent's speech, the
   synthesis time is our compute and the playback time is a clock we advanced
   ourselves. An absent `tts_owner` defaults to `harness`, because a safety check
   that defaults to "permitted" is not one.
3. **No WER from a transcript the harness produced, or from a smart-formatted
   one.** Loopback transcripts are scored by `tts_intelligibility_probe`, which
   carries a fixed `metric` field and a `caveat` field into every serialisation
   so the number cannot be quoted as agent WER once it has left its table.

### The cache claim, proven

A full re-run of the paid corpus: **0 characters, 0 credits.** The committed
evidence is the product of a fully cached run, which is the proof rather than the
assertion. Cache hits are checked *before* the budget guard and the ledger, so a
hit is structurally free.

---

## 7. Four defects the live run found

None of these were visible offline. Each is now guarded, with a test.

### `detect_language` + `language` returns nothing at all

| clip | `language` | `detect_language` | transcript |
|---|---|---|---|
| Japanese, 2.14 s | `multi` | true | `""`, confidence **0.0** |
| Japanese, 2.14 s | `multi` | false | correct, confidence 0.999 |
| Arabic, 2.93 s | `ar` | true | `""`, confidence **0.0** |
| Arabic, 2.93 s | `ar` | false | correct, confidence 0.998 |

Sending both returns an empty transcript at confidence 0.0 with
`detected_language: "en"`, and **nothing errors**. The row scores 100% word error
and the obvious conclusion — "the recogniser cannot handle Japanese", "our TTS
produced unintelligible audio" — is wrong in a way that gets a market written off.
The two options are now mutually exclusive at construction.

Found by re-transcribing a cached clip under a second setting, which cost nothing.

### The spoken-form rule inverts for CJK

| sent | `normalized_alignment` | the audio |
|---|---|---|
| 資産配分を見直したいです。 | `"Zi Chan Pei Fen woJian Zhi shitaidesu."` | correct Japanese, transcribed at confidence 1.0 |
| 我想检视我的投资组合。 | `"Wo Xiang Jian Shi Wo De Tou Zi Zu He ."` | correct Mandarin, 0.992, two characters out |

For CJK the vendor **romanises the written form** — into Mandarin pinyin, even for
Japanese input, where the correct reading would be "shisan haibun". The audio is
right; the reference is not. Scoring against it reports **100% error on audio that
is nearly perfect** — the precise mirror image of the Latin-script trap.

"Always prefer the spoken form" is therefore a rule with a script boundary. Applied
unconditionally it writes off Singapore and Japan while both are working.
`_is_romanised` detects it structurally (CJK in, no CJK out), so it needs no
language pin and also catches a caller who forgot to set one. Arabic and
Devanagari are unaffected — their normalised forms come back in their own scripts.
The guard is re-applied on the **cache read path** as well, because a guard that
only runs on a cache miss stops working as soon as the cache warms up.

### `wer.normalise` was ASCII-only

```
"मुझे अपना पोर्टफोलियो देखना है।"  ->  ""        (Hindi)
"資産配分を見直したいです。"          ->  ""        (Japanese)
"我想检视我的投资组合。"              ->  ""        (Mandarin)
"أريد مراجعة محفظتي."               ->  ""        (Arabic)
"pensión"                          ->  "pensi n"
"año"                              ->  "a o"
```

Two failures, and the second is the dangerous one. Non-Latin scripts normalised to
the empty string, which makes WER *undefined* — the denominator is the reference
word count — so those rows raise or get dropped. Loud, at least. But accented
Latin characters were silently **deleted**, splitting one word into two tokens:
every accented word in a Spanish or French row scored a substitution plus an
insertion. Nothing raised. The es/fr rates were simply wrong, in proportion to how
many accents each sentence happened to contain.

For a suite whose headline claim is coverage across 24 markets, the multilingual
figures were the least trustworthy numbers in it.

The filter is now category-based rather than regex-based, because `\w` does not
match combining marks and Python's `re` has no `\p{M}` — Devanagari writes its
vowels as combining marks, so a `\w`-only fix would have turned Hindi into
`"म झ अपन प र टफ ल य"`: the accent bug again, one script further along.

Scripts without word delimiters are segmented per character, and `scoring_unit()`
reports `"character"` for them — because a Japanese figure is a *character* error
rate and must not share an unlabelled column with English word error rates.

### The quantiser was not the inverse of the decoder

Every decode path divides int16 by **32768**; `quantise_pcm16` multiplied by
**32767**. So `int16 -> float -> int16` was not the identity, and a clip written to
the cache and read back differed in **1,966 of 4,000 samples**, each by one LSB.

- `audio_digest` changed across a cache round trip, so the transcript cassette —
  keyed on that digest — *missed* whenever a clip arrived by the other path. 17
  rows produced 23 entries. Precisely the failure the digest exists to prevent.
- The error was **cumulative**: every write-then-read cycle shifted the samples
  again, so a committed fixture would drift further from the recorded audio each
  time it was promoted. A fixture store that quietly degrades its own contents is
  worse than one that loses them, because nothing ever fails.

Now bit-exact and idempotent, with a test asserting both.

### And one about the instrument itself

Synthesised clips carry their own trailing silence — about **200 ms** on every
committed ElevenLabs clip. Appending a declared 5.9 s pause therefore produces a
6.1 s silent run, over the 6 s production threshold. A naive threshold test fails,
and the tempting repair is to widen the tolerance — which would leave a suite
unable to tell 5.9 from 6.1, certifying a threshold it had never measured. The
boundary is the subject of the test, so `pause_for_silence` computes the padding
against the clip in hand.

`readback_report` needed the same kind of correction. A digit-by-digit readout is
how account numbers are actually said, and `normalise`'s cardinal parser does not
merely decline that job — it corrupts it: `"four zero seven one nine nine two
eight"` becomes `"4 8 9 11 8"`. The field path bypasses it and maps digit names
one at a time. Before that fix, two perfect transcripts were reported as capture
failures.

---

## 8. Running it

Everything below is green on a fresh clone with **every key unset** — verified, not
assumed:

```
pip install -e ".[dev]" && pytest
```

The live paths are opt-in and refuse clearly, naming whichever of the flag and the
key is missing:

```
LAB_LIVE_TTS=1  ELEVENLABS_API_KEY=…   # synthesis
LAB_LIVE_STT=1  DEEPGRAM_API_KEY=…     # recognition
```

Re-record the cloud evidence (needs both, spends credits — and costs nothing if
the cache is warm):

```
python -m scripts.make_cloud_fixtures --dry-run   # print the corpus and its cost
python -m scripts.make_cloud_fixtures             # record
```

Optional overrides: `LAB_ELEVENLABS_MODEL`, `LAB_ELEVENLABS_VOICE`,
`LAB_ELEVENLABS_CREDIT_BUDGET`, `LAB_DEEPGRAM_MODEL`, `LAB_TTS_CACHE_DIR`.
Variable **names** only — no value is ever logged, committed, or written to a
trace or a fixture.

### Character ledger

| | characters sent | credits (local ledger) |
|---|---|---|
| Probe (step 1) | 108 | 82 |
| Live corpus (17 rows) | 655 | 345 |
| **Total** | **763** | **427** |

Well inside the ~7,000 ceiling, and every subsequent re-run is free — the last
three runs of the corpus each cost 0 characters.

**The counter resolved, and it settles which unit to budget in.** It read 284
before any of this work and did not move for the whole session. Re-read afterwards
it stands at **705** — a delta of **421** against 763 characters sent and 427
credits computed locally.

So `character_count` counts **credits, not characters**, and it does so with a lag
of tens of minutes. Two consequences, both already baked into the engine:

1. Budgeting in credits at the model multiplier was the right choice, and the
   local ledger tracks the vendor's own meter to within 1.5% (427 against 421 —
   over, because each line rounds up, which is the direction a cost guard should
   err in).
2. The guard could not have been a poll. A control that reads a counter which is
   both lagged and denominated in a different unit than you think reports the
   overspend after the fact.

Remaining of the 10,000 allowance: **9,295**.

### Committed fixtures

| path | what |
|---|---|
| `fixtures/audio/tts_cache/` | 17 clips, 16 kHz mono WAV, digest-keyed (1.7 MB) |
| `fixtures/audio/cloud/normalized_alignment_probe.json` | the step-1 probe, audio stripped |
| `fixtures/audio/cloud/elevenlabs_capabilities.json` | sanitised `/v1/models` + `/v2/voices` |
| `fixtures/audio/cloud/deepgram_transcripts.json` | the cassette, keyed by audio digest |
| `fixtures/audio/cloud/round_trip_evidence.json` | 17 rows, both WER axes, word confidences |

Lossless WAV, not Opus, in the clip cache. Opus is lossy, so a round trip through
it changes the samples and therefore the digest — which would silently break every
recorded transcript, and break them by *missing*, so the symptom would be a
surprise bill rather than an error.

---

## 9. What is not covered

Stated rather than left to be discovered:

- **No streaming.** Batch only, on both engines. Buys determinism, costs interim
  hypotheses and the `endpointing` mitigation noted above.
- **Barge-in is constructed, not discovered.** The adapter's turn loop is
  half-duplex and cannot produce an overlap. `interaction.barge_in` builds one
  from two clip timings and says so; it does not pretend the loop found it.
- **`speech_during_timeout` is an input, not an inference.** It models the agent's
  internal belief that the user was away, which no amount of listening can reveal.
  What the audio *can* reveal is whether that belief was false — which is the
  comparison the module performs.
- **Delivered latency cannot be measured at all here**, by construction. It needs
  the product to own its own TTS inside the session. The refusal is the honest
  output until then.
- **Cantonese cannot be tested**, and no amount of work in this repo changes that
  without a second TTS vendor.

---

# Part two: the in-process tier

Eighteen declared rows in `scenarios/audio/`, run by `lab/voice/suite.py` against
committed clips. Synthesised audio goes straight to the engines — no room, no
transport, no network — so the harness owns every timestamp and every sample.

Everything in this part was measured on 23 Aug 2026, offline, from the committed
fixtures. Re-running it costs **zero characters**.

## 10. The eighteen rows

**A note on the count.** The brief asked for fifteen and then enumerated blocks
summing to eighteen: 3 silence, 2 barge-in, 3 line-quality, 5 English capture,
2 es-US, 1 hi-IN, 1 en-SG, 1 yue-HK. Eighteen rows were built. Dropping three
would have meant dropping a named requirement — the Hinglish row, the constructed
Singapore row or the Cantonese refusal — and each of those carries a finding the
others do not.

Every row carries exactly one **category** tag, which is what makes the table
below countable rather than editorial. `passed` is `null` for the two rows that
are not runnable, and that is a deliberate third value: see §13.

| id | category | what it proves | perturbation chain | expected | why text cannot |
|---|---|---|---|---|---|
| `audio-silence-under-threshold` | silence | 5.9 s of dead air does **not** fire a 6 s timeout | pause to 5.9 s | `would_not_fire` | there is no silence in a transcript |
| `audio-silence-over-threshold` | silence | 6.1 s does fire, and the label is **true** | pause to 6.1 s | `caller_silent`, reason accurate | same |
| `audio-silence-boundary-misattributed` | silence | the timeout fires while the caller is **speaking** | pause to 2.0 s + detector reports away | `vad_false_silence`, reason **false** | the failure is audio disagreeing with agent state |
| `audio-barge-in-agent-yields` | barge-in | the agent stops when talked over, and how fast | two real clips, overlap at 1.2 s | yields within 300 ms | two turns cannot overlap in text |
| `audio-barge-in-not-discovered` | barge-in | the harness cannot **discover** an overlap | — (blocked) | **blocked**, never a pass | same |
| `audio-line-quality-noise-ladder` | line-quality | where rising noise breaks a postcode | `add_noise` pink, seeded, 20 → −15 dB | `SW1A 1AA` | noise is not representable in text |
| `audio-line-quality-telephone-band` | line-quality | narrowband alone does not cost the postcode | `telephone_band` 300–3400 Hz | `SW1A 1AA` | a filter has no textual equivalent |
| `audio-line-quality-packet-loss-ladder` | line-quality | where packet loss breaks it | `packet_loss` 20 ms, seeded, 1 → 90 % | `SW1A 1AA` | loss removes spans of *time*, not tokens |
| `audio-capture-postcode` | digits-and-names | a postcode survives a clean channel | — | `SW1A 1AA` | the substitution guarded against is acoustic |
| `audio-capture-date-of-birth` | digits-and-names | day, month and year all survive | — | `14th` / `march` / `1982` | the reordering happens in the STT formatter |
| `audio-capture-spelled-surname` | digits-and-names | letter-by-letter spelling works | — | `Gupta` | spelled letters are only sounds |
| `audio-capture-confusable-names` | digits-and-names | a **planted** mispronunciation is detected | SSML `<phoneme>` forcing | capture **fails**, as declared | the content *is* a pronunciation |
| `audio-capture-money-amount` | digits-and-names | a spoken amount parses to the right number | — | `4250.0` | the ambiguity is created by speech |
| `audio-bilingual-es-us-disclosure` | multilingual | **the control arm**: en/es both survive | — | both halves | the switch is the test |
| `audio-bilingual-es-us-regulator-verbatim` | multilingual | a regulator's name is not translated | — | `FINRA` verbatim | the risk is the STT language model |
| `audio-hinglish-lakh-magnitude` | multilingual | lakh scale captured as a **value** | — (attempted native mix) | `1500000` | switch and magnitude both live in audio |
| `audio-sg-constructed-code-switch` | multilingual | Mandarin is outside the switching set | **concatenation** of two clips | capture **fails**, as declared | the join is an acoustic event |
| `audio-hk-cantonese-untestable` | untestable | Hong Kong has **no audio path at all** | — (no audio exists) | **untestable** | text would report this market as covered |

### The three silence rows are the highest-value rows here

Reference bug 1. A production voice agent ended calls with
`callEndReason = "silence-timed-out"`. The label was believed, the investigation
went looking for quiet callers, and it was misdiagnosed for weeks. The real cause
was two things compounding: under `turn_detection="stt"` the VAD was driving
`user_state` — an upstream fix still unmerged — and `away_timeout` was 6 seconds,
aggressive enough that ordinary thinking pauses reached it.

One label, two situations, **opposite remedies**:

| situation | verdict | remedy | what the label recommends |
|---|---|---|---|
| the caller really went quiet | `caller_silent` | raise the timeout, or prompt | correct |
| the caller was speaking and we could not tell | `vad_false_silence` | fix turn detection | **the wrong fix** — raising the timeout only postpones it |

Telling them apart needs to know whether there was speech in the window.
Production cannot: its only instrument is the detector whose failure is the
question. This tier can, because it synthesised the audio and measures energy
with an **RMS envelope rather than a VAD** — deliberately, since adjudicating a
VAD failure with a VAD is measuring the instrument with itself.

Measured, and the exactness matters:

| row | declared pause | **measured** silence | fires | label accurate |
|---|---|---|---|---|
| under-threshold | 5.9 s | **5.900 s** | no | — |
| over-threshold | 6.1 s | **6.100 s** | yes | **yes** |
| misattributed | 2.0 s | **2.000 s** | **yes** | **no** |

The measured column is to the millisecond because it had to be. Every committed
clip carries about **200 ms of its own trailing silence**, so appending a declared
5.9 s pause produces a 6.1 s silent run and fires the 6 s timeout. The tempting
repair is a wider tolerance, and the result would be a suite unable to tell 5.9
from 6.1 while certifying a threshold it had never measured. `pause_for_silence`
computes the padding against the clip in hand, and every row asserts
`declared_matches_measured` **before** its verdict is read — the instrument
checking itself, because a verdict drawn from the wrong pause is a verdict about
padding arithmetic.

The pair matters more than either half. A detector that never fires passes the
under-threshold row alone; one that always fires passes the over-threshold row
alone. Only both together assert the threshold is where it is claimed to be.

### Barge-in: one row that runs, one that is honestly blocked

Reference bug 5 — the interruption metrics exist in the framework and nothing
used them. `lab/trace/schema.py` has defined `interruption_started` and
`interruption_acknowledged` from the beginning, with no named emitter and no
consumer.

`audio-barge-in-agent-yields` now produces a number: the agent's clip is
**3.297 s** measured, the caller cuts in at 1.2 s, the agent stops at 1.44 s —
**240 ms to yield, 0.240 s of overlap**, inside the 300 ms budget. The agent
duration is measured rather than declared, so the overlap cannot be a number
chosen to make the row pass.

`audio-barge-in-not-discovered` is reported **blocked**, and the distinction it
draws is the point. The two event kinds are no longer un-emitted —
`interaction.emit_barge_in` writes them. What does not exist is an adapter that
*discovers* an overlap: the turn loop plays the agent, then plays the caller, so
there is no moment at which both are sounding and nothing for a detector to find.
Every barge-in figure this tier reports was **constructed from timings the row
handed it**, and if that row silently passed, the suite would be claiming a
detection capability it does not have — with the first real duplex integration
measured against a baseline that had never detected anything.

The block expires by itself. `blocked_on()` derives from `EventKind.V2_RESERVED`,
so the day a duplex adapter emits those kinds and the schema promotes them to
`KNOWN`, the row becomes runnable with nobody editing a scenario.

### The line-quality ladder, and the range that had to be extended

The first ladder stopped at 0 dB SNR and 30 % packet loss, and the postcode was
captured at **every rung of both**. A ladder that never breaks reports no
breaking point, which is the one output a ladder exists to produce.

The obvious suspicion was checked first, because a perturbation that is not being
applied looks exactly like a recogniser that is unbreakable. The noise was
measured back out of the assembled clip:

| declared | **measured SNR** | digest |
|---|---|---|
| 20 dB | 20.00 dB | distinct |
| 10 dB | 10.00 dB | distinct |
| 6 dB | 6.00 dB | distinct |
| 3 dB | 3.00 dB | distinct |
| 0 dB | 0.00 dB | distinct |

The perturbation was real. nova-3 simply holds a spelled postcode at unity
signal-to-noise. So the **range** was wrong, not the result, and it was extended
until it contained the answer:

| axis | held to | **broke at** | what the break looks like |
|---|---|---|---|
| `add_noise` (pink, seeded) | **0 dB SNR** | **−5 dB** | `the postcode s w one a one a f` — **a plausible wrong postcode** |
| `packet_loss` (20 ms, zero-fill, seeded) | **70 % loss** | **90 %** | empty transcript |
| `telephone_band` 300–3400 Hz | holds | — | one rung: it is the network, not a dial |

**The most useful line in this document is the noise row's break.** At −5 dB the
postcode comes back as `SW1A 1AF` — one character wrong, at the end, and entirely
deliverable-looking. At −10 dB the transcript is empty. So the dangerous band is
*narrow and it is the milder one*: a slightly-too-noisy line yields a confident
wrong address, and a much worse line yields an obvious failure that any system
would catch. A pass/fail channel test reports both as "failed at high noise" and
loses the distinction that matters, which is that the silent wrong answer arrives
first.

`telephone_band` deliberately has one rung. A sweep over cutoff frequencies would
be sweeping a parameter no real channel varies, and reporting a breaking point in
it would invite tuning against a condition that does not occur.

### English capture: fields, never word error rates

Reference bugs 3 and 4. All five rows assert **values**, and
`lab/voice/engines/WER_NORMALISATION.md` is why: the same perfect postcode
transcript scores **0.000 against what was spoken, 1.400 against the string we
sent, and 0.700 against the smart-formatted rendering**. Scored the wrong way,
the rows whose entire purpose is proving a postcode survives become the worst
category in the suite while both vendors work perfectly — and somebody swaps a
vendor to fix nothing.

Measured, on `smart_format=false` as required, with the smart-formatted string
kept for display only:

| row | raw (scored) | smart-formatted (display only) | captured |
|---|---|---|---|
| postcode | `the postcode is s w one a one a a` | `The postcode is SW1A 1AA.` | ✅ |
| date of birth | `date of birth the fourteenth of march nineteen eighty two` | **`Date of birth, the 03/14/1982.`** | ✅ |
| spelled surname | `my name is priya gupta that's g u p t a` | `...that's g u p t a.` | ✅ |
| money amount | `the premium is four thousand two hundred and fifty pounds` | **`The premium is £4,250.`** | ✅ `4250.0` |
| confusable (forced) | `is that beeti or beeti` | `Is that Beeti or Beeti?` | ❌ **as declared** |

The date row is the standing evidence for asserting fields: smart formatting
rendered a **UK** date as `03/14/1982`, in US month-day order. Recognition was
perfect; five of nine words differ from the spoken form, all of it formatting
policy. A date of birth wrong by a transposition is an identity check that passes
for the wrong person, and nothing downstream rejects it because 14/03 and 03/14
are both valid dates.

The money row is asserted as a **number** and not a string, because `4250`,
`4,250`, `£4,250` and the spoken words are all correct and no two are equal as
strings — while `4000 250` is an error and a string comparison could not
distinguish it from the formatting differences.

### The one row that plants its own failure, and the control that saves it

`audio-capture-confusable-names` is the only row using phonetic forcing, and the
only place it is *available*: inline IPA works on `eleven_v3` alone, SSML
`<phoneme>` works on `eleven_flash_v2` alone — English-only, one tag per word —
and the multilingual models get alias substitution and pronunciation dictionaries,
nothing phonetic. **So a planted mispronunciation cannot be built for any
multilingual row**, and the Spanish, Hindi and Mandarin rows carry no equivalent.

CMU Arpabet forces "Beattie" to be pronounced as "Beatty", so the two names in
the utterance are acoustically identical. `expect_capture: false` is therefore
correct: the recogniser is being asked to make a distinction no longer present in
the signal, and the row's job is to prove the harness notices.

**And then the control changed the conclusion.** Both clips were transcribed:

| clip | transcript | confidence |
|---|---|---|
| forced (SSML phoneme) | `is that beeti or beeti` | 0.848 |
| **control** (same sentence, no tag) | `is that bt or bt` | 0.996 |

The control **also** collapses the pair — into "BT", a telecoms company — at high
confidence. So the honest finding is *not* "SSML forcing defeats capture". It is
that **this name pair was beyond the recogniser anyway, and the forcing proved
nothing about it.** Without the control, the row would have reported a planted
cause for a failure that had a different cause, which is a worse outcome than no
row: it is a wrong explanation with evidence attached.

A consequence worth stating: `eleven_flash_v2` is not in `SPOKEN_FORM_MODELS`, so
this clip has **no WER reference at all**. The one row where a word error rate is
impossible is a row where it was the wrong instrument anyway.

### es-US is the control arm, and it is here for a diagnostic reason

English/Spanish is the only pair both vendors fully support including
code-switching. That makes it the one place where a failure has a single possible
cause, and it turns the multilingual group into a three-way test:

| observation | the failure is | consequence |
|---|---|---|
| es-US passes, Singapore fails | **the vendor** | real limitation; nothing in this repo moves it |
| both fail | **the agent** or the content | the pair known to work did not |
| es-US fails | **the harness** | every other multilingual result is void until fixed |

**Measured: es-US passed both rows and Singapore failed**, so the Singapore
result is attributable to the vendor. Without this arm those three outcomes are
indistinguishable, and the default reading of a red multilingual cell is "our
product is bad in that market" — a vendor limitation filed as a product bug, with
an engineer assigned to it.

| row | transcript | result |
|---|---|---|
| disclosure | `this call is recorded esta llamada se graba por motivos de cumplimiento` | both halves captured, confidence 1.0 |
| regulator verbatim | `finra exige que le informemos del coste total antes de continuar` | `FINRA` survived, confidence 1.0 |

Both halves are asserted separately rather than the sentence as a whole, because
the characteristic failure of a bilingual utterance is not garbling — it is one
language quietly winning while the other is transcribed as approximate English,
which a whole-string check reports as a partial match.

**A caveat this row cannot fix:** the caller is a *British* premade voice speaking
Spanish, not an es-US voice. Voice Library voices are unavailable over the API on
the free tier, so the language is right and the accent is wrong. What is tested is
the language capability and the capture, not the accent.

### Hinglish: supported by the recogniser, not by the synthesiser

`hi` and `en` are both inside Deepgram's ten, so a caller mixing them
mid-sentence is a case the vendor claims. **ElevenLabs has no code-switching
support at all** — zero occurrences of "code-switch" in their documentation, and
their help centre advises against multiple languages in one prompt. So this row
is an *attempted native* mixed-script synthesis, and whatever came out is the
finding.

What came out was correct: `मेरा portfolio अब पंद्रह लाख का है` at confidence
0.996, Devanagari intact and the English noun preserved. The vendor's own advice
was pessimistic for this case.

**The row then caught a defect in the harness rather than the product.** The
value is asserted numerically, and `parse_magnitude` was ASCII-only: it read that
transcript as containing *no number at all*, and the row would have been filed as
a capture failure against a near-perfect transcript. Exactly the class of bug that
made `wer.normalise` reduce Hindi to the empty string — the instrument fails on
the script and the result is read as a failure of the market. `smart_format` did
not rescue it either; it returned the same Devanagari with a full stop added, so
there was no written-form shortcut. The parser now reads Devanagari cardinals and
the two Indian scale words, using an explicit codepoint range because `\w` does
not match the combining marks Devanagari writes its vowels with.

**Why lakh needs its own scale at all:** 1 lakh is 100,000 and 1 crore is
10,000,000, so "fifteen lakh" is 1,500,000 and no grouping of Western thousands
produces it. A parser that knows only thousand and million reads the words
perfectly and returns **15** — a silent five-order-of-magnitude error in a
portfolio value, and one that looks entirely plausible on screen.

A limit this row cannot work around: Deepgram's guidance for code-switched audio
is to drop `endpointing` to **100 ms**, and `endpointing` is a *streaming*
parameter with no meaning on the pre-recorded endpoint. **The vendor's
recommended mitigation for the hardest multilingual case is unavailable to this
batch harness.** A pass here is a pass on batch audio; whether the streaming path
survives retuning is not something this suite can tell you.

### Singapore is constructed by concatenation, and says so everywhere

**CONSTRUCTED BY CONCATENATION, NOT NATIVE SYNTHESIS.** The English clause was
synthesised with an English voice, the Mandarin clause with a Mandarin voice, and
the waveforms were joined. The label is on the row, in the report, in the corpus
schema (`audio.clauses`) and here, because a spliced utterance presented as a
native one would be the most misleading artefact this suite could produce.

It is constructed because it *cannot* be synthesised: `language_code` pins exactly
one language, so Singapore's ordinary register has no native path on this vendor.

Predicted failure, and the loader **enforces** the prediction — a row switching
into a language outside the recogniser's ten may not declare
`expect_capture: true`, because its red cell would be read as a defect. Measured:

    i want to check my portfolio

The Mandarin half is **gone entirely** — not garbled, not approximated, absent —
at confidence 1.0 on what remains. A recogniser reporting full confidence on
half an utterance is worth noting on its own: the confidence figure describes what
it transcribed, not what it was given.

The splice is arguably the *harder* test, and that is a feature rather than an
excuse: a concatenated boundary has no coarticulation, no shared prosody and a
discontinuity at the join, conditions no real bilingual speaker produces. A
recogniser that followed this switch would certainly follow a natural one, so the
row is a **lower bound** on capability and is labelled as one.

## 11. The vendor capability matrix

Computed by `lab/voice/engines/coverage.py` from the two capability sets, not
written down — so a vendor adding a language turns a test red instead of leaving
a document stale.

| market | hub | regulator | languages | TTS synthesises | STT transcribes | code-switching | **verdict** |
|---|---|---|---|---|---|---|---|
| **Hong Kong** | Hong Kong | SFC/IA | en + `zh-HK` | ❌ **no model** | ✅ `zh-HK` | ❌ | **untestable** |
| **Singapore** | Singapore | MAS | en + `zh` | ✅ | ✅ | ❌ `zh` outside the ten | **monolingual only** |
| **UAE** | London | FCA COBS | en + `ar` | ✅ | ✅ | ❌ `ar` outside the ten | **monolingual only** |
| Spain | London | FCA COBS | en + `es` | ✅ | ✅ | ✅ | **fully testable** |
| India | Singapore | MAS | en + `hi` | ✅ | ✅ | ✅ | **fully testable** |
| Japan | Hong Kong | SFC/IA | en + `ja` | ✅ | ✅ | ✅ | **fully testable** |
| France | London | FCA COBS | en + `fr` | ✅ | ✅ | ✅ | **fully testable** |
| Germany | London | FCA COBS | en + `de` | ✅ | ✅ | ✅ | **fully testable** |
| United Kingdom | London | FCA COBS | en | ✅ | ✅ | ✅ | **fully testable** |
| United States | London | Reg BI | en | ✅ | ✅ | ✅ | **fully testable** |

Deliberately not all 24 markets: a matrix padded with markets whose language
profile nobody checked would look more thorough and be less true. These are the
ones whose profile is established, and they include every case the boundary runs
through.

**Three verdicts and not two.** The middle one is what a pass/fail table
destroys. Singapore and the UAE are perfectly testable as monolingual rows — both
languages synthesise and transcribe fine — and are *not* testable for switching.
Calling them "covered" overclaims; calling them "untestable" hides that Hong Kong
is a different and worse problem.

**Hong Kong, verified against the live API rather than the docs:** zero
occurrences of `yue`, `zh-HK` or "Cantonese" across all nine ElevenLabs models.
The only Chinese id is `zh`, named "Mandarin Chinese". The Voice Library's `yue`
filter returns **0 voices**, and the 13 voices labelled with a "Cantonese (Hong
Kong)" accent sit under `language=Chinese` — **voice metadata, not model
support**; selecting one does not make the model speak Cantonese.

Recognition is **ahead of** synthesis here, which is what makes the gap
structural: Deepgram transcribes `zh-HK` and distinguishes Cantonese from
Mandarin. ElevenLabs does not model it at all.

**Remediation:** Azure AI Speech and Google Cloud TTS both offer `yue-HK`. Adding
either behind the existing `TTSEngine` protocol moves Hong Kong from *untestable*
to *monolingual* — **and no further**, because `zh-HK` is still outside Deepgram's
ten, so an English/Cantonese switching row stays out of reach on this recogniser.
Two vendor changes, two separate gains, neither speculative about what it buys.

## 12. Three outcomes, because two would lie

`Scenario.audio_status()` returns `runnable`, `blocked` or `untestable`, and
`RowResult.passed` is **`null`** for the latter two.

| status | count | meaning | who fixes it |
|---|---|---|---|
| `runnable` | **16** | ran, and has a verdict | — |
| `blocked` | **1** | declared; **this harness** cannot run it | us, with a duplex adapter |
| `untestable` | **1** | declared; **no vendor** in this stack can run it | a purchase order |

A report counting only passes and failures has to put the last two somewhere, and
both available answers are wrong. Counted as passes they inflate coverage;
counted as failures they look like defects and somebody is sent to fix a product
that is working.

This is also why `tier_summary` never prints a naked percentage — a bare
percentage is a defect in this repo, and this tier is where that rule earns its
keep. With 16 runnable, 1 blocked and 1 untestable, "94 %" is available and
dishonest three different ways depending on which denominator it silently used.
The pass rate is reported as **`16/16 runnable`**, carrying its denominator.

**Result: all 16 runnable rows observed what they declared.** Two of those are
declared *failures* — the planted mispronunciation and the Singapore splice — and
they pass because they failed as predicted.

## 13. What it cost

| | characters | credits |
|---|---|---|
| 7 new clips | **371** | **188** |
| 7 reused clips (cache hits) | 0 | 0 |
| — *had nothing been reused* | *742* | *342* |
| Transcriptions (33, Deepgram) | 0 | 0 |

Half the clips are reuse, and that is what let a tier this wide fit inside a
nearly-exhausted free allowance. Every silence row, every barge-in row, all three
line-quality axes and three of the five capture rows are built from clips the
engine phase already paid for — three silence thresholds, three channel axes and
a barge-in measurement for **zero characters**, because the cache key is
`sha256(text, voice, model, format, normalisation)` and an identical line is free.

The ladders are free for a different reason: each rung is a distinct audio digest
and therefore a distinct *transcription*, and recognition is billed against a
Deepgram signup credit. The perturbation happens to a clip already in hand, so
nine SNR rungs cost nothing in characters.

The generator planned 36 transcriptions and the cassette holds **33**, which is
content addressing working rather than three going missing: each line-quality
row's *declared* rung is also one of its ladder rungs, so it is the same audio,
the same digest and one entry. A cassette keyed on the row id would have stored
it twice and answered one of them for sound it never heard.

**Running total across both phases: 1,134 characters, 615 credits.** Roughly
9,107 of the 10,000 allowance remains. The second and third runs of this
generator each cost **0** — the committed evidence is the product of a fully
cached run, which is the proof rather than the claim.

## 14. What this suite cannot tell you

Stated rather than left to be discovered.

- **Nothing about accents.** Every row uses one of two stock premade voices,
  because Voice Library voices are not available over the API on the free tier.
  There are **928 Indian-English voices** in that library and this harness can
  reach none of them. Accent coverage is a paid capability, and the audio tag
  vocabulary was pruned of its four unused caller-voice locales rather than
  keeping them as aspirations that read as coverage.
- **Nothing about Cantonese**, and no amount of work in this repo changes that
  without a second TTS vendor.
- **Nothing about es-US accent**, per §10: right language, wrong accent.
- **No latency at all.** This tier measures no response times. An in-process run
  has no delivery leg, and the one number that looks like a latency — the 240 ms
  barge-in yield — is arithmetic over two clip durations, named for the yield
  rather than for latency so it cannot be quoted as one. Reference bug 2, the gap
  between agent-side `e2e_latency` and what the user actually experienced, is
  structurally invisible to an in-process harness and belongs to a transport
  tier, not here.
- **No streaming, so no interim hypotheses** — and no `endpointing`, which is
  the vendor's own recommended mitigation for code-switched audio.
- **Barge-in is constructed, not discovered.** Quantified in §10: one row runs on
  handed timings, one is blocked precisely because discovery does not exist.
- **`speech_during_timeout` is an input, not an inference.** It models the
  agent's belief that the user was away, and no amount of listening reveals a
  belief. What the audio reveals is that the belief was false.
- **A natural code-switch is not tested anywhere.** The Singapore row is a
  splice, which is a lower bound; the Hinglish row is a single attempted native
  synthesis. Neither is a bilingual human speaker.
- **The ladder breaking points are for one sentence, one voice and one seed.**
  `SW1A 1AA` breaking at −5 dB is not a general claim about postcodes, and the
  seeded placement of packet loss is one realisation of where the gaps fell.
- **Two clips is not a sample.** The confusable-name finding says that *this*
  pair defeats the recogniser, not that confusable names in general do.
- **The field matcher is containment, and that has two blind spots.** Every
  capture assertion here asks "is the declared value present in what was heard",
  which is the right question for a read-back on a degraded line and is
  deliberately not a WER (§22). The price is that it cannot reject a
  **superstring** — `s w one a one a a b` contains the postcode and passes — and
  it cannot reject a transcript where the declared value appears but is then
  **superseded**, as in a caller correcting themselves: `actually not SW1A 1AA
  but EC1A 1BB` passes, although the value the agent should have recorded is the
  second one. That second case is a genuine instance of reference bug 4 and **no
  row in this tier exercises it**. Both blind spots are pinned by
  `test_the_matcher_is_containment_and_this_is_the_limit_of_it` in
  `tests/test_audio_suite.py`, so they are asserted properties rather than
  accidents, and that test is what a future row closing the gap will have to
  change. Closing it needs a row to declare a *final* value — a schema change,
  not a matcher change. What the matcher *does* reject is tested beside it: a
  wrong final letter (the real −5 dB failure), a truncation, a different valid
  postcode, an empty transcript, a magnitude off by one or by tenfold, and a
  translated `FINRA`.

### Running it

```
pytest tests/test_audio_suite.py          # 64 tests, no keys, no network
python -m scenarios.loader --summary      # the text corpus, unchanged
```

Re-recording needs both live flags and spends credits — and costs nothing if the
cache is warm:

```
python -m scripts.make_audio_suite_fixtures --dry-run        # plan and cost
LAB_LIVE_TTS=1 LAB_LIVE_STT=1 python -m scripts.make_audio_suite_fixtures
python -m scripts.make_audio_suite_fixtures --evidence-only  # replay, no keys
```

| fixture | what |
|---|---|
| `fixtures/audio/tts_cache/` | 7 new clips, 16 kHz mono WAV, digest-keyed |
| `fixtures/audio/cloud/audio_suite_transcripts.json` | 33 recorded transcripts, keyed by the digest of the *perturbed* audio |
| `fixtures/audio/cloud/audio_suite_evidence.json` | 18 row results, produced by replay so they are reproducible with every key unset |

---

# Part two: the live run

Everything above was built and measured on 23 Aug 2026. This section is a
**separate live execution** of the whole thing — the gate, all eighteen
in-process rows, and the three transport rows — run to answer one question the
first pass could not: *are these numbers measurements, or are they one HTTP
response each?*

It cost **0 ElevenLabs characters**, and that is a property of the program rather
than a result: `scripts/run_audio_live.py` never imports the synthesiser.

## 15. The gate, first, because nothing downstream is reportable without it

`python -m lab.voice.calibration` — **PASS.**

| nominal | n | mean | p50 | stdev | rel err | verdict |
|---|---|---|---|---|---|---|
| 100 ms | 20 | 100.266 ms | 100.689 ms | 4.100 ms | **+0.266%** | PASS |
| 250 ms | 20 | 249.903 ms | 250.167 ms | 4.644 ms | −0.039% | PASS |
| 500 ms | 20 | 501.180 ms | 501.639 ms | 3.435 ms | +0.236% | PASS |
| 1000 ms | 20 | 999.531 ms | 999.288 ms | 3.708 ms | −0.047% | PASS |
| 2000 ms | 20 | 2000.184 ms | 2001.902 ms | 5.016 ms | +0.009% | PASS |

Tolerance is 5% and the worst row uses 5% of it. The naive whole-turn control
**FAILS**, as designed, and the shape of its failure is the part worth reading:

| nominal | naive control mean | naive error | verdict |
|---|---|---|---|
| 100 ms | 130.27 ms | **+30.3%** | FAIL |
| 250 ms | 279.90 ms | +12.0% | FAIL |
| 500 ms | 531.18 ms | +6.2% | FAIL |
| 1000 ms | 1029.53 ms | +3.0% | PASS |
| 2000 ms | 2030.18 ms | +1.5% | PASS |

All five rungs are shown because the two that *pass* are the argument: the
control's overall verdict is FAIL on **3 of 5** rungs, and it is the three
shortest. The naive reading is wrong by a near-constant **~30 ms** — the
harness's own injected compute — so its *relative* error shrinks as the delay
grows, until at 1 s and 2 s a 30 ms error slips inside a 5% tolerance and the
broken instrument certifies itself. Its relative error is worst exactly where the
budget is tightest. For a product whose value proposition is a suggestion arriving before
the moment passes, the regime where the naive instrument overstates by a third is
the only regime that matters. A control that failed by a constant percentage would
have been much less informative than one that fails by a constant *offset*.

### The real-clock arm fails at 100 ms, and it is reported rather than dropped

`--clock real` additionally exercises OS scheduling:

| nominal | mean | rel err | verdict |
|---|---|---|---|
| 100 ms | 105.855 ms | **+5.855%** | **FAIL** |
| 250 ms | 256.328 ms | +2.531% | PASS |
| 500 ms | 506.896 ms | +1.379% | PASS |
| 1000 ms | 1006.209 ms | +0.621% | PASS |
| 2000 ms | 2006.753 ms | +0.338% | PASS |

**These five numbers are the only ones in this document that do not reproduce
bit-for-bit,** and that is the arm's nature rather than a defect: it measures OS
scheduling, so it measures the machine and its load at the time. A re-run on the
same laptop while busier read **+7.910%** at 100 ms and passed every rung from
250 ms up — the same verdict pattern, a different floor. What is stable is the
*shape*: a scheduling floor of single-digit milliseconds, which fails a 5% test
at 100 ms and passes it everywhere above. Quote the shape, not the digits. The
deterministic arm above is the one that gates, and it is exactly reproducible.

A ~6 ms scheduling floor is 6% of 100 ms and 0.3% of 2 s. So the honest statement
of this harness's resolution is: **it can certify a 250 ms figure to 5% on a real
clock and it cannot certify a 100 ms one.** The gate that admits latency figures
is the deterministic arm, which is correct — it is measuring the *attribution*
logic, not the OS — but a report that quoted only the passing arm would be
claiming a resolution the machine does not have.

## 16. The in-process tier, live

`scripts/run_audio_live.py --live` re-transcribes **every** variant of every
runnable row against the live API, discarding the committed cassette on the way
in, then diffs the two passes digest by digest. The generator deliberately will
not do this — it skips any digest it already holds, which is right for a recorder
and means a re-run of it makes no live calls at all. "I ran it live" would
otherwise be a claim about a replay.

**33 of 33 paired variants reproduced byte-identically.** 66 live Deepgram
requests over 227.7 s of audio. Zero ElevenLabs characters.

That is the result that licenses every other number in this document. Two
independent live observations of the same audio returned the same string every
time, so the cassette is a measurement of the recogniser rather than a snapshot of
one response, and the offline tier is a regression test rather than a recording.

(33 rather than 36: three rows declare a perturbation that *is* one of their
ladder's rungs, so the row variant and that rung are the same audio and the same
digest. Deduplicated by digest, not by label.)

### The tier's verdict

**16 of 16 runnable rows passed.** 18 rows total: 16 runnable, 1 blocked, 1
untestable. The untestable row is `passed=None`, never `True` and never `False`.

| category | result | rows in tier |
|---|---|---|
| digits-and-names | 5/5 runnable | 5 |
| multilingual | 4/4 runnable | 4 |
| silence | 3/3 runnable | 3 |
| line-quality | 3/3 runnable | 3 |
| barge-in | 1/1 runnable | 2 (one blocked) |
| untestable | 0/0 runnable | 1 |

### Field-level results, because a WER would have hidden every one of these

**14 of 16 declared field checks captured.** Both misses were *predicted* by their
rows, which is why the tier reads 16/16 while the field count reads 14/16 — the
two figures measure different things and both are printed.

| row | conf | fields |
|---|---|---|
| `audio-capture-postcode` | 0.998 | postcode ✓ |
| `audio-capture-date-of-birth` | 1.000 | dob_day ✓ dob_month ✓ dob_year ✓ |
| `audio-capture-spelled-surname` | 0.999 | surname ✓ |
| `audio-capture-money-amount` | 1.000 | premium_gbp ✓ |
| `audio-capture-confusable-names` | 0.848 | surname ✗ *(predicted)* |
| `audio-bilingual-es-us-disclosure` | 1.000 | english_half ✓ spanish_half ✓ |
| `audio-bilingual-es-us-regulator-verbatim` | 1.000 | FINRA ✓ |
| `audio-hinglish-lakh-magnitude` | 0.996 | portfolio_inr ✓ |
| `audio-sg-constructed-code-switch` | 1.000 | english_half ✓ mandarin_half ✗ *(predicted)* |

`SW1A 1AA` was heard as `s w one a one a a` and scored **captured**, at 0.998.
A word error rate against the written form would have called that row a failure.

## 17. The silent-correction rate, and the mistake I made computing it

**1 correction over 10 reconciled turns = 10.0 per 100 turns, 100% attributable,
0 unattributable.** Production's equivalent reconciliation attributed 31.3%.

And the single correction is not a mishearing:

```
turn 7: substitution 'पोर्टफोलियो' -> 'portfolio' (conf 0.996)  [cross-script]
```

The synthesiser's spoken form transliterates the English loanword into Devanagari;
the recogniser writes it in Latin. Both are right about the word. Classified as
**cross-script** and counted separately, so the honest headline is **0 recognition
corrections over 10 turns** with the alphabet disagreement stated rather than
buried.

### The first version of this number was 416.7 per 100 turns

I walked into the trap this repository has a document about. My first
reconciliation used the clips' **input strings** as ground truth, so it compared
`"SW1A 1AA"` against `"s w one a one a a"` and scored every spoken letter as an
insertion — 50 corrections over 12 turns.

It was absurd enough to catch. The point of writing it down is that a subtler
version would not have been, and the fix is now a library function with the
failure in its docstring rather than a habit:

`lab.voice.suite.spoken_reference` takes the vendor's own `normalized_alignment`
per clip and **declines, by name, the two clips that have no usable reference**:

| clip | why it is declined |
|---|---|
| `confusable-forced` | `eleven_flash_v2` is the SSML model and not a spoken-form model. It has no spoken form at all, and its input string is *markup* — reconciling against it manufactured ten deletions out of `alphabet`, `cmu-arpabet` and the phoneme string. |
| `mandarin-portfolio` | Its spoken form is **pinyin**: the vendor romanised the Mandarin while the audio stayed correct. `_is_romanised` declines it structurally — CJK in, no CJK out — and the guard now applies on the reconciliation path too, not just inside the synthesis engine. |

Both rows are **listed as declined**, never counted as zero. A reconciliation
whose denominator silently absorbs the rows it could not check has the same defect
as a naked percentage. 10 rows reconciled, 2 declined, and the reasons are in the
artefact.

## 18. The line-quality ladder: where capture actually broke

| axis | parameter | held to | broke at |
|---|---|---|---|
| `add_noise` | SNR | **0 dB** | **−5 dB** |
| `packet_loss` | loss rate | **70%** | **90%** |
| `telephone_band` | 300–3400 Hz | captured at the only rung | — |

Rung by rung, `add_noise`: 20 ✓ 15 ✓ 10 ✓ 6 ✓ 3 ✓ 0 ✓ **−5 ✗** −10 ✗ −15 ✗.
`packet_loss`: 1% ✓ 2% ✓ 5% ✓ 10% ✓ 20% ✓ 30% ✓ 50% ✓ 70% ✓ **90% ✗**.

**The dangerous rung is the milder one.** At −5 dB the postcode comes back as
`SW1A 1AF` — a *plausible wrong address*, confidently delivered. At −10 dB the
transcript is empty and the failure is obvious. A pass/fail ladder loses that
distinction entirely; the actionable output is the pair (held-to, broke-at) and
the *content* of the break.

## 19. Silence: the timeout fired at the right threshold, and one label is false

Reference bug 1, and the reason this tier exists. All three rows PASS, and each
asserts `declared_matches_measured` **before** its verdict — without that, a
verdict is a test of the harness's padding arithmetic rather than of the timeout.

| row | declared | measured | threshold | fires | verdict | reason accurate? |
|---|---|---|---|---|---|---|
| `audio-silence-under-threshold` | 5.9 s | **5.900 s** | 6.0 s | no | `would_not_fire` | n/a |
| `audio-silence-over-threshold` | 6.1 s | **6.100 s** | 6.0 s | yes | `caller_silent` | **true** |
| `audio-silence-boundary-misattributed` | 2.0 s | **2.000 s** | 6.0 s | yes | `vad_false_silence` | **FALSE** |

Two separate results. **The threshold resolves to the millisecond**: 5.900 s does
not fire, 6.100 s does. And **the end reason can be wrong while the timeout is
right** — the third row has a caller *talking through* a 2.000 s pause, the
timeout fires anyway, and the label `caller_silent` would be a lie. The row
reports `vad_false_silence` and marks the accuracy of the label FALSE.

That second column is the whole bug. A production system labelled calls
`silence-timed-out` for weeks when the cause was VAD driving `user_state`; a
harness that only asserted "did the timeout fire" would have passed every one of
those calls. Firing correctly and *attributing* correctly are two assertions, and
only the second one catches this.

## 20. The control-arm reading — the most important paragraph here

Three failures, three different owners, and the only reason each is attributable
is that a control arm ran beside it.

**es-US versus en-SG: the vendor.** The Spanish row captured both halves at
confidence 1.000, including `FINRA` surviving untranslated inside a Spanish
sentence. The Singapore row, on the same voice, the same model, the same
recogniser configuration and the same `multi` code-switching setting, **lost the
Mandarin clause entirely** — and returned confidence 1.000 on the English that
remained. One arm works and one does not, with only the language pair varying.
That is a **vendor capability boundary**, not an agent defect and not a harness
defect: `zh` is outside Deepgram nova-3's ten-language `multi` set, and the
control arm is what turns "Singapore failed" into "Singapore is outside the
supported set". Without es-US, the same observation supports "our pipeline is
broken", which is a week of debugging pointed at the wrong component.

The confidence figure is the sharp end. **1.000 on a transcript with a clause
missing** is worse than a low score, because a downstream consumer reading
confidence would promote it.

**The confusable-name row: the harness's own hypothesis was wrong.** This row
plants a mispronunciation with SSML `<phoneme>` and predicts a capture failure. It
got one — `Beattie`/`Beatty` both collapse to `beeti` at 0.848. But the
**unforced control clip**, with no phoneme tag at all, collapses them too, into
`bt`, at *higher* confidence. So the SSML proved nothing about that name: the
recogniser cannot separate the pair regardless. Without the control, this suite
would have published a planted cause for a failure that had a different one. The
row still passes — it predicted a capture failure and got one — but the *reason* in
the report is now the recogniser, not the plant.

**The Hinglish row: the harness.** Devanagari transcribed perfectly; the row
failed because `parse_magnitude` was ASCII-only and read a Devanagari numeral as
containing no number. Same class as `wer.normalise` reducing Hindi to `""`. A
harness defect masquerading as a product failure, and the thing that separated
them was that the *transcript was correct* — visible only because ground truth
was an input.

**So: one vendor boundary, one recogniser limitation the harness had
mis-attributed to itself, one harness bug.** Not one of the three is an agent
defect, and no amount of staring at the failing row alone would have sorted them.

## 21. yue-HK: untestable, and counted as neither

`audio-hk-cantonese-untestable` — `passed=None`, `status="untestable"`,
`language="yue"`. Not a pass, not a failure, not in any pass-rate denominator.

Zero of the nine ElevenLabs models synthesise Cantonese, verified live. Deepgram
*does* transcribe `zh-HK` and distinguishes it from Mandarin, so **recognition is
ahead of synthesis and the gap is structural**, not a configuration miss.
Remediation is committed with the row: Azure AI Speech or Google Cloud TTS both
offer `yue-HK` neural voices behind the existing `TTSEngine` protocol — and
neither moves Hong Kong past *monolingual*, because `zh-HK` is outside Deepgram's
`multi` set, so an English/Cantonese switching row stays out of reach.

The refusal **expires by itself**: it validates only while `yue` is absent from
the synthesisable set. When a vendor ships it, the row fails and demands to be
rewritten rather than sitting there as a permanent excuse.

## 22. Word error rate: two named numbers, and neither is an agent's

**This number is not an agent WER, and in this suite it structurally cannot be.**
The audio is synthesised by this harness and transcribed by this harness. There is
no system under test in the loop. Every figure below is the **TTS-intelligibility
probe** — the instrument's own noise floor, the error a caller-side row inherits
before the product has done anything.

Scored on the only legitimate cell of the 2×2: spoken-form reference,
`smart_format=false`.

| | figure |
|---|---|
| **raw** error rate, mean over 14 rows | **0.4344** |
| **normalised** error rate | **7 / 125 words = 0.0560** |

Same audio. Same transcripts. **One canonicalisation step apart, and a factor of
7.8.** That gap is the entire subject of
`lab/voice/engines/WER_NORMALISATION.md`, now measured over the whole corpus
rather than one sentence: the raw figure would have reported this pipeline as
43% wrong while both vendors were working nearly perfectly.

Per row, the worst raw offenders are exactly the rows whose content is digits:
`readback-account-number` raw **0.900** / normalised **0.000**;
`readback-postcode` raw 0.600 / normalised 0.000; `readback-name-spelled` raw
0.727 / normalised 0.000. Every one of those is a *perfect* transcript. Which is
why the digit and name rows assert **fields**, never a rate.

The rows with genuine residual error are visible only in the normalised column:
`readback-irish-name` 0.400 (2/5), `readback-sort-code` 0.200 (3/15),
`switch-hi` 0.200 (1/5), `mono-ar-uae` 0.167 (1/6). Reporting one number would
have hidden both facts — that the digit rows are clean, and that the Irish name is
not.

## 23. The transport tier, live — and two claims my own run falsified

All three rows ran against a live LiveKit room. 0 characters; this tier publishes a
committed clip and synthesises nothing.

### Row 1 — the delivery gap, and the statistic that actually reproduces

Reference bug 2. `e2e_latency` is agent-side and excludes network delivery, so the
agent-side figure for these twelve turns is **0.0 ms by construction**.

| session | n | mean | p50 | net mean | net p50 | stdev | queue corr | verdict |
|---|---|---|---|---|---|---|---|---|
| primary | 12 | 89.0 ms | 89.6 ms | 86.0 ms | 88.7 ms | 7.1 ms | 0.72 | PASS |
| second | 12 | 137.6 ms | 90.1 ms | 104.7 ms | 90.1 ms | 72.2 ms | 0.99 | **FAIL** |
| **live run (this one)** | 12 | 128.3 ms | **158.2 ms** | 87.9 ms | **84.7 ms** | 42.5 ms | 0.99 | PASS |

Spreads over all three: means **48.6 ms**, medians **68.6 ms**, net means
**18.7 ms**, **net medians 5.4 ms**.

**The suite used to say "quote the median". My third session falsified it.** On two
sessions the raw medians agreed to half a millisecond, which made the median look
like the stable statistic. The third put them 68.6 ms apart and made the raw median
the *least* stable of the four. What reproduces across all three is the **median
net of the local send queue** — 88.7, 90.1, 84.7 ms, a **5.4 ms** spread, inside
one 10 ms frame.

The mechanism is a correction the row already had, for a different reason. That
158.2 ms raw median sits beside a send-queue correlation of **0.99** and a net
median of 84.7 ms: roughly **70 ms of it was this harness's own buffer filling**,
not a network delivering late. Without the send-queue column, that session reads as
a transport degrading by 76% and would have been the headline.

Two things were wrong with how the old claim was defended, and both are now fixed:

- The report **asserted** the interpretation in a fixed sentence beneath a computed
  table. It now **computes** which of the four statistics has the tightest spread
  and names that one, so the sentence cannot outlive the data above it.
- The test that existed to catch exactly this **named two files**, so a
  counterexample could land in the directory it was reading from without it
  noticing. It now globs every committed session and fails if the tightest
  statistic is not the one the document quotes.

What does not vary, on any statistic in any session: the gap is nowhere near the
0 ms an agent-side figure implies. For live in-call coaching, a suggestion arriving
after the moment has passed is a failure and not a slow success — so the number the
dashboard does not contain is the number that is the product risk.

### Row 2 — the file ladder does not agree, and the naive comparison says it does

| fill | injected | realised in file | transport adds | file adds | per unit (transport) | per unit (file) | ratio |
|---|---|---|---|---|---|---|---|
| `zero` | 18/71 (25.4%) | 16.7% | 13.6% | 14.1% | 0.54 | 0.85 | **1.57×** |
| `hold` | 18/71 (25.4%) | 16.7% | 13.6% | 2.8% | 0.54 | 0.17 | **0.31×** |

The two fill modes **bracket** reality — one 1.57× harsher, one 0.31× — so neither
is the loss rate a real transport produces. Both DISAGREE, in both sessions
(committed: 1.45× and 0.29×). The conclusion reproduces.

The naive comparison — loaded silent fractions, no baseline, no dose correction —
reads 31.7% against 23.5% and would have concluded disagreement *for the wrong
reason*; correcting for each channel's own silence floor and for the dose each
actually applied is what makes the numbers mean anything.

### The 101 ms jitter claim was not causal, and my session proves it

The document previously read: *"injected loss quadrupled the longest inter-arrival
interval"* — 101.3 ms under injection against 24.7 ms in the loss-free session.

| session | injected loss | mean abs dev | longest interval |
|---|---|---|---|
| `degradation.json` | 18/71 (25.4%) | 1.02 ms | **101.3 ms** |
| `degradation-live-run.json` | 18/71 (25.4%) | 0.60 ms | **21.2 ms** |
| loss-free (row 1's session) | none | 0.60 ms | 24.7 ms |

**Same dose, 4.8× difference, and my session comes in *below* the loss-free
control.** So the 101 ms hole was a network event during that session, not a
consequence of the injection — and the original comparison was uncontrolled,
because the two rows it compared came from *different sessions*, so treatment and
session varied together.

This is the third time a control arm has reversed a conclusion in this suite,
after the confusable name and es-US, and the first time it did so to a claim that
had already been written down. The measurement was right both times; the causal
reading was available only because n was 1. A test now pins the corrected reading.

What survives without a causal claim, and is the row's real finding: **jitter has
no column for the file ladder at all.** A perturbed file has no time axis, so
pacing is inexpressible in it at any rate. Structural gap, not a missing feature.

### Row 3 — the transport recovers, the turn does not

Reproduced closely across both sessions:

| figure | committed | live run |
|---|---|---|
| frames pushed before the drop | 40 of 71 | 40 of 71 |
| frames that arrived *after* the sender had gone | 4 | 4 |
| drop → publisher reconnected | 910 ms | 867 ms |
| drop → far side re-subscribed | 1092 ms | 1025 ms |
| **listener heard nothing for** | **1800 ms** | **1711 ms** |
| verdict | `recovered-turn-lost` | `recovered-turn-lost` |

The remaining 31 frames were never sent and nothing retransmits them. The
transport-level figure (1025 ms) **understates the hole in the conversation**
(1711 ms) by two thirds of a second. A reconnect metric that stops at "the
participant came back" scores a lost sentence as a success.

Four frames arriving after the sender departed is why the analysis needs three
buckets rather than two — jitter-buffer audio briefly outlives the connection that
filled it. It reproduced exactly, in both sessions, which is what makes it a
property rather than a quirk.

## 24. Cost, and the counter that settled the unit question

**This live run: 0 ElevenLabs characters, 0 credits.** Not a budget outcome — the
live-run script has no code path to the synthesiser, and it refuses before its
first API call if any clip is missing from the committed cache. A guard can be
wrong about a price; code with no path to the vendor cannot be charged by it.
Deepgram: 66 requests over 227.7 s, against a signup credit.

| phase | characters | credits (local ledger) |
|---|---|---|
| Probe | 108 | 82 |
| Engine corpus | 655 | 345 |
| Suite corpus | 371 | 188 |
| **This live run** | **0** | **0** |
| **Total** | **1,134** | **615** |

The vendor counter now reads **860 of 10,000**, and it rose from 705 to 860
**during a session that made zero synthesis requests**. That is a cleaner proof of
the lag than the original observation was: a counter that moves while nothing is
being spent cannot be polled as a cost control, and the local credit ledger is the
only thing that can refuse a request before it is made.

Reconciled against the pre-work baseline of 284, the work's own contribution is
**860 − 284 = 576 credits** measured, against **615** ledgered locally — the
ledger is **6.8% over**, because it rounds every line up. That is the direction a
cost guard should err in, and it revises the earlier 1.5% figure, which was taken
before the counter had finished settling.

**Remaining: ~9,140 of the 10,000 allowance**, resetting 2026-09-23. Nothing was
cut for budget, in this phase or any earlier one.

## 25. Running the live pass

```
# the gate — must pass before any latency figure is reportable
python -m lab.voice.calibration
# The scheduling arm; fails at 100 ms. --no-write because this arm's numbers are
# machine- and load-dependent, so letting it overwrite fixtures/calibration_report.*
# replaces committed evidence with a figure that will not reproduce.
python -m lab.voice.calibration --clock real --no-write

# the report, from committed fixtures, no keys, no network
python -m scripts.run_audio_live

# the live second pass: re-transcribes every variant, 0 synthesis characters
LAB_LIVE_STT=1 DEEPGRAM_API_KEY=… python -m scripts.run_audio_live --live

# the transport tier
python -m lab.voice.transport.report
LAB_LIVE_TRANSPORT=1 python -m scripts.make_transport_fixtures --suffix="-my-session"
```

| artefact | what |
|---|---|
| `fixtures/audio/cloud/audio_suite_live_pass.json` | the second live transcription of all 33 variants, with the digest-by-digest diff |
| `fixtures/audio/cloud/audio_suite_report.json` | every figure in §16–§22, derived by replay |
| `fixtures/audio/transport/{delivery-gap,degradation,lifecycle}-live-run.json` | the three live transport sessions, plus their traces |

Variable **names** only, everywhere. No key value is logged, committed, or written
into a trace or a fixture.
