# The spoken call

One full advisory conversation, run turn by turn through real speech synthesis and
real speech recognition, and graded by the same scorers that grade the text tier.

Before this, the two tiers in this repository never met. The audio tier
([`docs/AUDIO_SUITE.md`](AUDIO_SUITE.md)) proves that **single utterances** survive
a real TTS → STT round trip. The roleplay tier grades **whole conversations** — in
text. Nothing joined them, so nothing here could answer the question a voice
product actually has: *what does recognition error do to the grade?*

`roleplay/spoken.py` is the join. It is a wrapper, not a fork: the existing
conversation loop runs unchanged, and each speaking side's text is routed through
the existing engines before the other side receives it.

```
trainee model ──text_sent──▶ ElevenLabs ──wav──▶ Deepgram ──text_heard──▶ customer model
                                                                │
                                                                └─▶ the trace, the
                                                                    register, the scorers
```

## The rule

> **The scorer grades what was heard, never what was sent.**

A disclosure the adviser said and the channel mangled is a disclosure the register
does not credit — exactly as in production. Both strings ride on every spoken
trace event (`text_sent` beside the heard text), so the gap is readable per turn
and is never inferred.

`roleplay.scorer.session_view` is a pure function of trace events, so a spoken
trace is graded by the existing scorers with no changes at all. That is the whole
reason this was cheap to build.

## What is committed

Everything in [`fixtures/audio/spoken_call/`](../fixtures/audio/spoken_call/):

| file | what it is |
| --- | --- |
| `full_call.wav` | the whole call, one playable 16 kHz mono WAV, turns in order with 0.35 s gaps |
| `manifest.json` | per turn: who spoke, `text_sent`, the synthesiser's spoken form, `text_heard`, the display string, STT confidence, clip digest, wall clocks |
| `trace.jsonl` | the conversation trace both scorers graded |
| `scorecards.json` | both score cards, the channel-effect diff, the recognition deltas |
| `scorer_recording.jsonl` | the live scorer's raw answer, pinned to its prompt digest |

`make spoken-replay` needs **no keys**. It does not read a summary back: the
committed per-turn notes drive the same production loop again, so the trace, the
disclosure register, the persona ledgers and the deterministic card are all
*recomputed*. Only the live scorer's answer is replayed, and a rubric edit makes
that raise rather than silently reuse a grade belonging to a different question.

`make spoken-record` is the only way to produce new recordings. It spends real
ElevenLabs characters and refuses, naming every missing piece at once, without
`LAB_LIVE_SPOKEN=1` plus both audio keys, a provider key and three model routes.

## The recorded call

An exemplary adviser against `aggressive_challenger`, eu-retail, `gpt-4.1` in all
three seats (both speakers and the live scorer).

| | |
| --- | --- |
| turns | 8 adviser + 8 customer = 16 spoken turns |
| recording | 181.3 s |
| stopped by | the character budget, not a close (`stop_reason="character_budget"`) |
| ElevenLabs | 3,014 characters submitted (cap 3,400), 1,510 credits |
| Deepgram | 176.1 s of distinct audio, 352.1 s submitted over 32 requests |
| deterministic scorer | FAIL 12/20 |
| live LLM scorer (rubric v2) | FAIL 16/20 |

Synthesis is digest-cached, so re-recording this unchanged call bills **zero**.

## The finding

Seven of the sixteen turns picked up a recognition delta. None of them touched
registered disclosure wording — the register came through the channel intact, and
the disclosure ledger is identical graded on heard text and on sent text. The
damage was somewhere else entirely.

`roleplay.persona.classify_trainee_turn` decides a turn is a question with
`body.endswith("?")`. A scored transcript is `smart_format=false`, which
[`WER_NORMALISATION.md`](../lab/voice/engines/WER_NORMALISATION.md) *requires* —
the prettified string turns "seven thirty" into "07:30" and fabricates a word
error rate. The verbatim string carries no punctuation at all.

So no spoken turn can ever end in a question mark. Five of the eight adviser turns
were reclassified from `closed_question` / `open_probe` to `pitch`, and the
`discovery` criterion went **2/4 as spoken → 0/4 as heard** on a call where the
adviser demonstrably did ask the questions.

Two individually correct decisions, composing into a silent scoring failure that
neither one is wrong about on its own. It is invisible in text, because in text
the punctuation is always there.

**And it nearly hid.** `objection_handling` moved 2 → 4 in the opposite direction
— the persona state machine treats a `pitch` as drawing the next objection, so
reclassifying the adviser's questions as pitches pulled objections forward — and
the two changes cancelled. Both gradings total 12/20. The disclosure ledgers match.
The verdicts match. **A check on the total, the verdict or the register would each
have reported that the audio channel changed nothing.** `ChannelEffect` compares
the criteria individually, which is the only reason this was seen at all.

The finding is pinned in `tests/test_roleplay_spoken.py` as a mechanism, not a
number, so that fixing either half — a punctuation-independent classifier, or a
scored transcript that carries sentence boundaries — fails the test and demands a
re-read.

## Two scorers, one call, opposite reasons

Both scorers returned FAIL, and they agree on almost nothing:

| criterion | deterministic | live LLM |
| --- | --- | --- |
| discovery | 0 | 4 |
| objection_handling | 4 | 4 |
| mandatory_disclosure | 4 | 0 |
| no_unlicensed_advice | 4 | 4 |
| closing | 0 | 4 |

Three of five criteria are maximally apart, in both directions. The LLM scorer
reads the unpunctuated transcript and still recognises the questions, so it is
robust to exactly the failure that erases `discovery` for the regex scorer. In the
other direction the deterministic scorer awards full marks for mandatory
disclosure on keyword presence (that is `DEFECT-3`, seeded on purpose) where the
live scorer gives zero — and only two of the three eu-retail requirements were
actually met.

"The two scorers agreed" is true only at the resolution of the verdict, and on
this call that agreement is a coincidence of opposite errors.

## What the numbers here are not

- **No agent voice-response latency is quoted, because none exists here.** This
  loop is half-duplex and file-based. `synthesis_s`, `transcribe_s` and
  `model_turn_s` are direct wall-clock measurements of harness-side vendor calls:
  ElevenLabs synthesis (cache hits excluded), the Deepgram *scored* request (the
  display request excluded), and the LLM turn (retry backoff included). The
  trace's `ts` values come from the modelled latency clock and are labelled as
  modelled. The timing gate that governs trace-recovered latencies is
  `fixtures/calibration_report.json`, verdict PASS.
- **Every WER figure carries raw and normalised counts with their denominators**,
  scored against the synthesiser's published spoken form. On this call the raw
  counts exceed the normalised ones roughly fivefold, and that gap is punctuation
  and casing — reporting the raw figure as a recognition error rate is precisely
  the trap `WER_NORMALISATION.md` exists to prevent.
- **n=1.** One call, one persona, one model, one voice pair, one day. Nothing here
  supports a rate, and no percentage in this document appears without its
  denominator.

## A hazard worth recording

The first attempt at this call used the `cautious_saver` persona and died after
two adviser turns: the customer model's paraphrase of that persona's liquidity
objection — *"and how long would my money be tied up if I went ahead"* — was
refused by the provider's content filter, which ends the session with
`stop_reason="content_filter"`. The refusal reproduces on its own with a one-line
system prompt, and the persona's **own committed wording** of the same objection
passes, so the trigger was the model's phrasing on the day, not the corpus.

Recorded here because it is a real hazard for anyone running conversational evals
against a filtered provider — a benign retail-finance sentence can end a session
mid-call — and because a scenario swapped without saying why is a result nobody
can audit. Nothing about any grade was involved in the choice: the discarded run
never reached a gradeable call.
