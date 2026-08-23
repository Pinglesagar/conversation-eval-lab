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
