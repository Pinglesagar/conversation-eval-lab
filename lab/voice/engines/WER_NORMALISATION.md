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
