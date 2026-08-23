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

### Running it

```
pytest tests/test_audio_suite.py          # 37 tests, no keys, no network
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
