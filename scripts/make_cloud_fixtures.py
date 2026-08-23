"""Record the cloud-engine evidence: real ElevenLabs synthesis, real Deepgram recognition.

WHAT THIS DEMONSTRATES
----------------------
The claims in `lab/voice/engines/elevenlabs_tts.py`, `deepgram_stt.py` and
`docs/AUDIO_SUITE.md` are not arguments from documentation. They are measurements,
and this is the script that takes them. It runs the two paid engines over a small
deliberate corpus and commits four things:

    the clips        16 kHz mono WAV in the digest cache, so a re-run is free
    the cassette     Deepgram's answer per clip, so replay needs no key
    the evidence     one JSON row per line: what was sent, what was spoken, what
                     was heard raw, what was heard smart-formatted, the word
                     confidences, and both word error rates
    the ledger       characters and credits spent, printed and committed

WHY IT IS A SEPARATE SCRIPT AND NOT A TEST
------------------------------------------
Because it spends money. The free ElevenLabs allowance is 10,000 characters that
do not renew until the monthly reset, so a suite that synthesised on every run
would work about four times and then fail halfway through a corpus. Tests consume
what this script produces; only this script produces it, only when a human runs
it, and it refuses to start without both live flags.

THE CORPUS IS SMALL ON PURPOSE, AND EVERY ROW HAS A JOB
-------------------------------------------------------
Six groups, and none of them is padding:

    read-back        names, dates of birth, postcodes, account and sort codes.
                     The capture failures that actually harm a caller, and the
                     rows where WER is the wrong instrument.
    advisory         ordinary sentences from the product's domain, for the
                     silent-correction reconciliation.
    code-switched    the four pairs that are testable end to end: es, hi, ja, fr.
    monolingual      Mandarin and Arabic — synthesisable, *not* code-switchable.
                     The rows that prove the middle verdict in the coverage matrix
                     is real rather than a hedge.
    reference-trap   one line on `eleven_v3`, to show the engine declining to
                     publish a spoken form on a live response rather than only in
                     a unit test.
    agent-voice      one line in the agent voice, for the TTS-intelligibility
                     probe — the harness measuring its own noise floor.

Around 660 characters, roughly 340 credits. A cache hit costs nothing, so running
this twice costs the same as running it once.

THE MEASUREMENT THAT PAYS FOR THE WHOLE SCRIPT
----------------------------------------------
Each clip is transcribed **twice**: once with `smart_format=false` (the scored
string) and once with `smart_format=true` (display only). Then both word error
rates are computed against both candidate references. That two-by-two is the
evidence for the central claim of this suite — that the choice of reference and
the choice of formatting between them move a word error rate by tens of points on
a transcript that is perfect — and it is the reason a reader should believe the
refusals in `lab/voice/adapter.py` are load-bearing rather than decorative.

Deepgram is billed against a signup credit and is effectively free here, so the
second request buys a lot for nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # pragma: no cover - script convenience
    sys.path.insert(0, str(REPO))

from lab.voice.engines.base import DEFAULT_SAMPLE_RATE, audio_digest  # noqa: E402
from lab.voice.engines.clipcache import ClipCache, clip_cache_key  # noqa: E402
from lab.voice.engines.deepgram_stt import DeepgramSTT  # noqa: E402
from lab.voice.engines.elevenlabs_tts import (  # noqa: E402
    DEFAULT_AGENT_VOICE,
    DEFAULT_CALLER_VOICE,
    ElevenLabsTTS,
    credits_for,
)
from lab.voice.adapter import _collapse_digit_runs  # noqa: E402
from lab.voice.wer import normalise, wer  # noqa: E402

OUT = REPO / "fixtures" / "audio" / "cloud"
CASSETTE = OUT / "deepgram_transcripts.json"
EVIDENCE = OUT / "round_trip_evidence.json"


class Row:
    """One line to synthesise, and what it is meant to prove."""

    def __init__(
        self,
        row_id: str,
        text: str,
        *,
        group: str,
        language: str = "en",
        model: str = "eleven_flash_v2_5",
        voice: str = DEFAULT_CALLER_VOICE,
        expect: dict[str, str] | None = None,
        note: str = "",
    ) -> None:
        self.id = row_id
        self.text = text
        self.group = group
        self.language = language
        self.model = model
        self.voice = voice
        self.expect = expect or {}
        self.note = note


ROWS: tuple[Row, ...] = (
    # ---- read-back: the capture failures that actually harm a caller ----------
    Row(
        "readback-name-spelled",
        "My name is Priya Gupta, that's G-U-P-T-A.",
        group="read-back",
        expect={"surname": "Gupta"},
        note="a spelled surname: the letters are the whole point",
    ),
    Row(
        "readback-dob",
        "Date of birth: the fourteenth of March, nineteen eighty-two.",
        group="read-back",
        expect={"dob_day": "14th", "dob_year": "1982"},
        note="a spoken date, which smart formatting rewrites and WER then punishes",
    ),
    Row(
        "readback-postcode",
        "The postcode is SW1A 1AA.",
        group="read-back",
        expect={"postcode": "SW1A 1AA"},
        note="the canonical case: perfect capture, ~50% WER under smart formatting",
    ),
    Row(
        "readback-account-number",
        "Account number 4071 9928.",
        group="read-back",
        expect={"account": "4071"},
        note="digits, where a single substitution is a wrong account",
    ),
    Row(
        "readback-irish-name",
        "My name is Siobhan O'Rourke.",
        group="read-back",
        expect={"surname": "Rourke"},
        note="a name whose spelling and pronunciation diverge",
    ),
    Row(
        "readback-sort-code",
        "Sort code 20-45-77, reference FCA1138.",
        group="read-back",
        expect={"sort_code": "204577"},
        note="grouped digits plus an alphanumeric reference",
    ),
    # ---- advisory prose: the silent-correction reconciliation -----------------
    Row(
        "advisory-drawdown",
        "I'd like to review my drawdown before the tax year ends.",
        group="advisory",
        note="ordinary domain prose, for the correction reconciliation",
    ),
    Row(
        "advisory-suitability",
        "Is this fund still suitable given my attitude to risk?",
        group="advisory",
        note="suitability language, the vocabulary a compliance check reads",
    ),
    Row(
        "advisory-charges",
        "Can you confirm the annual management charge?",
        group="advisory",
        note="a factual question, the kind live in-call support corrects",
    ),
    # ---- code-switchable: the four pairs that work end to end -----------------
    Row(
        "switch-es",
        "Necesito revisar mi pensi\u00f3n antes de fin de a\u00f1o.",
        group="code-switched",
        language="es",
        note="Spanish: inside Deepgram's ten, so genuinely code-switchable",
    ),
    Row(
        "switch-hi",
        "\u092e\u0941\u091d\u0947 \u0905\u092a\u0928\u093e \u092a\u094b\u0930\u094d\u091f\u092b\u094b\u0932\u093f\u092f\u094b \u0926\u0947\u0916\u0928\u093e \u0939\u0948\u0964",
        group="code-switched",
        language="hi",
        note="Hindi in Devanagari, not romanised: romanised text would test English "
        "phonetics rather than the language capability",
    ),
    Row(
        "switch-fr",
        "Je voudrais revoir mon portefeuille avant la fin de l'ann\u00e9e.",
        group="code-switched",
        language="fr",
        note="French: inside the ten",
    ),
    Row(
        "switch-ja",
        "\u8cc7\u7523\u914d\u5206\u3092\u898b\u76f4\u3057\u305f\u3044\u3067\u3059\u3002",
        group="code-switched",
        language="ja",
        note="Japanese in native script: inside the ten, so genuinely switchable",
    ),
    # ---- monolingual only: synthesisable, not switchable ----------------------
    Row(
        "mono-zh-singapore",
        "\u6211\u60f3\u68c0\u89c6\u6211\u7684\u6295\u8d44\u7ec4\u5408\u3002",
        group="monolingual",
        language="zh",
        note="Singapore Mandarin. ElevenLabs calls this id 'zh' and documents it as "
        "Mandarin; it synthesises fine and is outside Deepgram's code-switching ten",
    ),
    Row(
        "mono-ar-uae",
        "\u0623\u0631\u064a\u062f \u0645\u0631\u0627\u062c\u0639\u0629 \u0645\u062d\u0641\u0637\u062a\u064a \u0642\u0628\u0644 \u0646\u0647\u0627\u064a\u0629 \u0627\u0644\u0639\u0627\u0645.",
        group="monolingual",
        language="ar",
        note="UAE Arabic in native script: synthesises, outside the ten",
    ),
    # ---- the reference trap, live --------------------------------------------
    Row(
        "trap-v3-no-spoken-form",
        "Ring at 7:30 from SW1A 1AA.",
        group="reference-trap",
        model="eleven_v3",
        note="eleven_v3 ignores the normalisation request; the engine must decline "
        "to publish a spoken form and fall back to caller-input, labelled",
    ),
    # ---- the agent's own voice, for the intelligibility probe -----------------
    Row(
        "agent-confirmation",
        "Certainly. Your appointment is confirmed for seven thirty.",
        group="agent-voice",
        voice=DEFAULT_AGENT_VOICE,
        note="the agent voice, so the harness can measure its own noise floor",
    ),
)


def _plan() -> tuple[int, int]:
    """Characters and credits the corpus would cost if nothing were cached."""
    characters = sum(len(row.text) for row in ROWS)
    credits = sum(credits_for(row.text, row.model) for row in ROWS)
    return characters, credits


def _field_capture(expected: dict[str, str], heard: str) -> dict[str, bool]:
    """Field-level assertions, using **the same matcher the suite ships**.

    Both canonicalisations, exactly as `lab.voice.adapter.readback_report` does
    them. An earlier version of this generator had its own one-line matcher that
    only applied `normalise`, and it reported the account-number and sort-code
    rows as capture failures when the transcripts were perfect — because
    `normalise` corrupts digit-by-digit readouts ("four zero seven one nine nine
    two eight" becomes "4 8 9 11 8"). Two instruments, two answers, and the
    generator's answer was the one being committed as evidence.

    So there is one matcher now, in the library, and this calls it. A fixture
    generator that scores differently from the code under test is a fixture
    generator producing evidence about itself.
    """
    out: dict[str, bool] = {}
    for name, value in expected.items():
        wanted_plain = normalise(value).replace(" ", "")
        wanted_digits = _collapse_digit_runs(value).replace(" ", "")
        heard_plain = normalise(heard).replace(" ", "")
        heard_digits = _collapse_digit_runs(heard).replace(" ", "")
        out[name] = bool(
            (wanted_plain and wanted_plain in heard_plain)
            or (wanted_digits and wanted_digits in heard_digits)
        )
    return out


def record(*, dry_run: bool = False, credit_budget: int = 1_500) -> int:
    """Synthesise, transcribe twice, and commit the clips, cassette and evidence."""
    characters, credits = _plan()
    print(f"corpus: {len(ROWS)} row(s), {characters} characters, {credits} credits if uncached")
    if dry_run:
        for row in ROWS:
            print(
                f"  {row.group:15s} {row.id:26s} {len(row.text):3d} chars "
                f"{credits_for(row.text, row.model):3d} credits  {row.model}"
            )
        return 0

    missing = [
        name
        for name, value in (
            ("LAB_LIVE_TTS", os.environ.get("LAB_LIVE_TTS")),
            ("LAB_LIVE_STT", os.environ.get("LAB_LIVE_STT")),
            ("ELEVENLABS_API_KEY", os.environ.get("ELEVENLABS_API_KEY")),
            ("DEEPGRAM_API_KEY", os.environ.get("DEEPGRAM_API_KEY")),
        )
        if not value
    ]
    if missing:
        print(f"refusing to run: {', '.join(missing)} not set", file=sys.stderr)
        print("this script spends real credits; both flags and both keys are required", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    cache = ClipCache()
    cassette: dict[str, dict[str, Any]] = {}
    if CASSETTE.is_file():
        cassette = json.loads(CASSETTE.read_text(encoding="utf-8")).get("entries", {})
    evidence: list[dict[str, Any]] = []
    total_characters = 0
    total_credits = 0
    promoted = 0

    for row in ROWS:
        tts = ElevenLabsTTS(
            model_id=row.model,
            voice_id=row.voice,
            language_code=None if row.language == "en" else row.language,
            cache=cache,
            credit_budget=credit_budget,
        )
        before_credits = tts.credits_spent
        clip = tts.synthesise(row.text, sample_rate=DEFAULT_SAMPLE_RATE)
        total_characters += tts.characters_spent
        total_credits += tts.credits_spent - before_credits
        spent_here = tts.credits_spent - before_credits

        key = clip_cache_key(
            text=row.text,
            voice=row.voice,
            model=row.model,
            output_format=f"pcm_{DEFAULT_SAMPLE_RATE}",
            normalisation="on",
        )
        if cache.promote(key):
            promoted += 1

        stt = DeepgramSTT.for_language(row.language, want_display=True)
        heard = stt.transcribe(clip.audio, sample_rate=clip.sample_rate)

        digest = audio_digest(clip.audio, clip.sample_rate)
        cassette[digest] = {
            "engine": stt.name,
            "provenance": "recorded",
            "text": heard.text,
            "formatting": heard.formatting,
            "display_text": heard.display_text,
            "confidence": heard.confidence,
            "language": heard.language,
            "row_id": row.id,
        }

        # The two-by-two that pays for the script: each reference against each
        # formatting, so the size of the trap is a measured number rather than an
        # assertion. Only the raw/spoken-form cell is a legitimate WER.
        spoken = clip.spoken_text
        cells: dict[str, dict[str, Any] | None] = {}
        for ref_name, reference in (("spoken-form", spoken), ("caller-input", row.text)):
            for fmt_name, hypothesis in (("raw", heard.text), ("smart", heard.display_text)):
                if reference is None or hypothesis is None:
                    cells[f"{ref_name}/{fmt_name}"] = None
                    continue
                scored = wer(reference, hypothesis)
                # Errors and words, not only a rate. `lab.report` is
                # denominator-safe and so is this: a naked percentage is a defect
                # in this repo, and these are short utterances where one
                # substitution is a big-looking number.
                cells[f"{ref_name}/{fmt_name}"] = {
                    "normalised_wer": scored.normalised.wer,
                    "raw_wer": scored.raw.wer,
                    "errors": scored.normalised.errors,
                    "reference_words": scored.normalised.reference_words,
                    "substitutions": scored.normalised.substitutions,
                    "deletions": scored.normalised.deletions,
                    "insertions": scored.normalised.insertions,
                }

        evidence.append(
            {
                "row_id": row.id,
                "group": row.group,
                "note": row.note,
                "language": row.language,
                "model": row.model,
                "voice": row.voice,
                "characters": len(row.text),
                "credits": spent_here,
                "cache_hit": clip.replayed,
                "sent_text": row.text,
                "spoken_text": spoken,
                "reference_source": clip.reference_source,
                "heard_raw_scored": heard.text,
                "heard_smart_display_unscored": heard.display_text,
                "confidence": heard.confidence,
                "detected_language": heard.language,
                "audio_duration_s": round(clip.duration_s, 4),
                "audio_digest": digest,
                "word_count": len(heard.words),
                "mean_word_confidence": heard.mean_word_confidence,
                "lowest_confidence_words": [
                    {"word": w.word, "confidence": w.confidence}
                    for w in heard.lowest_confidence_words(3)
                ],
                "wer_matrix": cells,
                "field_capture": _field_capture(row.expect, heard.text) if row.expect else {},
                "stt_engine": stt.name,
                "tts_engine": tts.name,
            }
        )
        flag = "cached" if clip.replayed else f"{spent_here} credits"
        print(
            f"  {row.id:26s} {clip.duration_s:5.2f}s  {flag:12s} "
            f"ref={clip.reference_source:13s} conf={heard.confidence}"
        )

    CASSETTE.write_text(
        json.dumps({"entries": cassette}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    EVIDENCE.write_text(
        json.dumps(
            {
                "captured": "2026-08-23",
                "note": (
                    "One row per synthesised line. `heard_raw_scored` is the only string "
                    "a WER may be computed on; `heard_smart_display_unscored` is for "
                    "human display and is named so it cannot be quoted by mistake. The "
                    "normalised_wer block is a two-by-two of reference against "
                    "formatting: only spoken-form/raw is a legitimate figure, and the "
                    "other three cells are what the suite refuses to report."
                ),
                "ledger": {
                    "characters_spent_this_run": total_characters,
                    "credits_spent_this_run": total_credits,
                    "corpus_characters": characters,
                    "corpus_credits_if_uncached": credits,
                },
                "rows": evidence,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {CASSETTE.relative_to(REPO)} ({len(cassette)} entries)")
    print(f"wrote {EVIDENCE.relative_to(REPO)} ({len(evidence)} rows)")
    print(f"promoted {promoted} clip(s) into the committed cache")
    print(f"LEDGER: {total_characters} characters, {total_credits} credits spent this run")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record the cloud-engine round-trip evidence. Spends real credits."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the corpus and what it would cost, and spend nothing",
    )
    parser.add_argument(
        "--credit-budget",
        type=int,
        default=1_500,
        help="ceiling on credits per row-engine (default 1500)",
    )
    args = parser.parse_args(argv)
    return record(dry_run=args.dry_run, credit_budget=args.credit_budget)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
