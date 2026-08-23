# WER normalisation: a live-measured trap

Verified against real engines, 23 Aug 2026. Any WER implementation in this package
must handle this or its numbers are meaningless.

## What happened

One sentence, synthesised by ElevenLabs (`eleven_flash_v2_5`, pcm_16000) and
transcribed by Deepgram (`nova-3`, `smart_format=true`):

    reference (ElevenLabs normalized_alignment)
      "Table for four at seven thirty, postcode S W one A one A A."

    hypothesis (Deepgram, smart_format=true)
      "Table for four at 07:30. Postcode SW1A1AA."
      confidence 0.997

**The recognition was perfect.** Every digit and every letter of the postcode is
correct. A word-level WER over those two strings reports roughly 50% error.

## Why the two sides disagree

They are formatting the same content for different purposes:

- The reference is *what was spoken*. ElevenLabs normalises text before synthesis
  (that is why `normalized_alignment` exists and why it, not the caller's input
  string, is the only valid reference — see `tts.py`).
- The hypothesis is *what a human would want to read*. `smart_format=true` turns
  spoken numerals into written form: `seven thirty` -> `07:30`, spelled letters
  into a joined token, and inserts sentence punctuation.

Neither side is wrong. Comparing them directly measures **formatting policy**, not
recognition accuracy.

## What this package must do

1. **Score on `smart_format=false`.** Request the unformatted hypothesis for any
   metric. Keep the smart-formatted string alongside it for human display only,
   and never let a reader mistake one for the other.
2. **Canonicalise both sides identically before scoring** — lowercase, strip
   punctuation, expand digits to number words (or contract number words to
   digits, consistently), and collapse spelled-letter runs (`s w one a one a a`
   and `sw1a1aa` must reduce to the same token).
3. **Report raw and normalised WER as two named numbers.** A single "WER" figure
   that silently applied normalisation is not auditable. `wer.py` already carries
   both; the audio adapter must not collapse them.
4. **Digit and postcode rows need a field-level assertion, not WER.** For the
   digits-and-names category the question is "was the captured value correct?",
   which is an exact comparison against the declared expected value. WER is the
   wrong instrument there and will understate a perfect capture.

## The failure this prevents

Without this, the digits-and-names category — the ten rows whose entire purpose is
proving the agent hears a postcode correctly — would report the highest error rate
in the suite while the engine was performing flawlessly. The suite would then be
used to argue for a vendor change that fixes nothing.

---

## Confirmed and extended by the live cloud round trip (23 Aug 2026)

The four required fixes above were implemented in `elevenlabs_tts.py`,
`deepgram_stt.py` and `adapter.py`. Running them against the real APIs confirmed
the trap and produced three refinements this document did not anticipate. Full
account in `docs/AUDIO_SUITE.md`; evidence in `fixtures/audio/cloud/`.

**The magnitude was understated.** On the committed corpus, comparing the *same*
perfect transcript against the two candidate references:

| row | vs spoken form | vs the string we sent |
|---|---|---|
| postcode | 0.000 | **1.400** |
| account number | 0.000 | **1.250** |
| sort code | 0.200 | **1.429** |

Above 1.0, because the spoken form has roughly twice the tokens of the written
one, so the errors are mostly insertions. "Roughly 50%" was this document's
estimate from one sentence; the digit rows are worse than that.

The smart-formatting axis, separately: postcode 0.700, date of birth 0.556, sort
code 0.800. The date row is worth reading — `smart_format` rendered "the
fourteenth of March, nineteen eighty-two" as `03/14/1982`, in US order for a UK
date.

**Refinement 1 — the trap has a model boundary.** `eleven_v3` accepts
`apply_text_normalization="on"`, returns HTTP 200, and hands back
`normalized_alignment` byte-identical to `alignment`. So "score on the spoken
form" is only available on the models measured to honour normalisation
(`SPOKEN_FORM_MODELS`). A predicate that checks the *request* rather than the
*model* re-creates this bug on the newest model.

**Refinement 2 — the rule inverts for CJK.** For Japanese and Mandarin input the
"normalised" form comes back **romanised into Mandarin pinyin** while the audio is
correct. Scoring against it reports 100% error on near-perfect audio: the exact
mirror of the trap this document describes. `_is_romanised` declines the reference
in that case. Arabic and Devanagari are unaffected.

**Refinement 3 — item 2 above ("canonicalise both sides identically") was not
possible as written.** `wer.normalise` was ASCII-only: it reduced Hindi, Japanese,
Mandarin and Arabic to the empty string, and silently *deleted* accented Latin
characters, so "pensión" became "pensi n" — two tokens, two phantom errors, in
every Spanish and French row. Now Unicode-aware and category-based (combining
marks are kept, which `\w` does not do), with per-character segmentation and a
`scoring_unit()` label for scripts that do not delimit words.

**And item 4 ("digit and postcode rows need a field-level assertion") needed one
more step than stated.** A digit-by-digit readout is how account numbers are
actually spoken, and `normalise`'s cardinal parser corrupts it — "four zero seven
one nine nine two eight" becomes "4 8 9 11 8". The field path therefore bypasses
`normalise` and maps digit names one at a time. Before that, two perfect
transcripts were reported as capture failures.
