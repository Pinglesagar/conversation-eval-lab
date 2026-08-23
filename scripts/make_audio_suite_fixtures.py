"""Record the audio tier's fixtures: seven new clips, and a transcript per variant.

WHAT THIS PRODUCES
------------------
    the clips        the seven lines the tier could not get from the existing
                     corpus, 16 kHz mono WAV, promoted into the committed
                     digest cache so a fresh clone with no keys can run the tier
    the cassette     `fixtures/audio/cloud/audio_suite_transcripts.json`, one
                     entry per *assembled and perturbed* clip, keyed by the
                     digest of the audio that produced it
    the evidence     `fixtures/audio/cloud/audio_suite_evidence.json`, one row
                     per scenario: what was assembled, what was heard, which
                     declared values survived, and the ladder breaking points
    the ledger       characters and credits, printed and committed

WHY A SECOND GENERATOR RATHER THAN ROWS IN THE FIRST ONE
-------------------------------------------------------
`make_cloud_fixtures.py` records the *engine* evidence: what the two vendors do,
measured line by line. This records the *suite* evidence: what eighteen declared
rows observe. They write different files on purpose — a generator that rewrote a
cassette it did not produce would be one bad flag away from destroying the
measurements another document cites, and those measurements cost characters that
do not renew until the monthly reset.

WHAT IT COSTS, AND WHY SO LITTLE
--------------------------------
371 characters, 188 credits — for eighteen rows. Eleven of them cost **nothing**,
because they are assembled from clips the engine phase already paid for and the
cache key is `sha256(text, voice, model, format, normalisation)`, so an identical
line is free. Every silence row, every barge-in row, all three line-quality axes
and three of the five capture rows are reuse. That is the whole reason a tier
this wide fits inside a nearly-exhausted free allowance, and it is also why
`lab/voice/suite.py` holds the clip registry as data with the exact synthesis
parameters: one changed character is a cache miss and a new charge.

Recognition is charged against a Deepgram signup credit and is effectively free,
which is what makes the ladders affordable. Each rung of each axis is a distinct
audio digest and therefore a distinct transcription — 6 SNRs plus 6 loss rates
plus the band, per row that declares one — and none of it costs ElevenLabs
characters, because the perturbation happens to a clip already in hand.

THE ORDER OF OPERATIONS IS PART OF THE MEASUREMENT
--------------------------------------------------
Assemble, then pause, then perturb. Perturbing before the pause would add noise
into the silence and the pause would no longer be silent — which would quietly
break the three rows whose entire subject is a silent interval of known length.
`lab.voice.suite.assemble_audio` owns that order so the generator and the test
cannot drift apart on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # pragma: no cover - script convenience
    sys.path.insert(0, str(REPO))

from lab.voice.engines.clipcache import ClipCache, clip_cache_key  # noqa: E402
from lab.voice.engines.deepgram_stt import DeepgramSTT  # noqa: E402
from lab.voice.engines.elevenlabs_tts import ElevenLabsTTS  # noqa: E402
from lab.voice.engines.stt import RecordedSTT, TranscriptCassette  # noqa: E402
from lab.voice.suite import (  # noqa: E402
    AUDIO_SUITE_CASSETTE,
    CLIPS,
    LADDERS,
    assemble_audio,
    clip_for,
    corpus_cost,
    new_clips,
    run_row,
)
from scenarios.audio import tier  # noqa: E402

OUT = REPO / "fixtures" / "audio" / "cloud"
CASSETTE = OUT / AUDIO_SUITE_CASSETTE
EVIDENCE = OUT / "audio_suite_evidence.json"

#: Which recogniser language each row is transcribed under.
#:
#: Not a detail. Phase one measured that sending `detect_language` alongside
#: `language` returns an **empty transcript at confidence 0.0 with no error** —
#: which reads as "the recogniser cannot handle this language" and gets a market
#: written off. So the language is pinned per row, explicitly, and never
#: auto-detected.
#:
#: `multi` is nova-3's ten-language code-switching model. The Singapore row is
#: given `multi` deliberately even though `zh` is outside those ten: `multi` is
#: what a Singapore deployment would actually configure, so the row measures the
#: real consequence of that configuration rather than a kinder one.
ROW_LANGUAGE: dict[str, str] = {
    "audio-bilingual-es-us-disclosure": "multi",
    "audio-bilingual-es-us-regulator-verbatim": "multi",
    "audio-hinglish-lakh-magnitude": "multi",
    "audio-sg-constructed-code-switch": "multi",
}
DEFAULT_LANGUAGE = "en"


def _variants(scenario: Any) -> list[dict[str, Any]]:
    """Every audio variant one row needs a transcript for.

    A row's own rung, plus every rung of its ladder, plus its control clip. Each
    is a different digest, so each needs its own recorded transcript; a cassette
    keyed on the row id instead would replay a transcript recorded from different
    sound and nobody would see it happen.
    """
    spec = scenario.audio
    if spec is None or spec.untestable is not None or spec.capture is None:
        return [{"label": "row", "override": None}]
    out: list[dict[str, Any]] = [{"label": "row", "override": None}]
    voice = getattr(scenario, "voice", None)
    if voice is not None and len(voice.perturbations) == 1:
        step = voice.perturbations[0]
        if step.name in LADDERS:
            parameter, rungs = LADDERS[step.name]
            for rung in rungs:
                out.append(
                    {
                        "label": f"{step.name}:{parameter}={rung}",
                        "override": {**step.params, parameter: rung},
                    }
                )
    return out


def _plan() -> dict[str, Any]:
    """What would be synthesised and what would be transcribed, without doing it."""
    rows = tier()
    transcriptions = 0
    controls = 0
    for scenario in rows:
        if scenario.audio_status() != "runnable":
            continue
        transcriptions += len(_variants(scenario))
        if scenario.audio.control_clip:
            controls += 1
    cost = corpus_cost()
    return {
        "rows": len(rows),
        "runnable": sum(1 for s in rows if s.audio_status() == "runnable"),
        "blocked": [s.id for s in rows if s.audio_status() == "blocked"],
        "untestable": [s.id for s in rows if s.audio_status() == "untestable"],
        "clips_to_synthesise": [c.id for c in new_clips()],
        "transcriptions": transcriptions + controls,
        **cost,
    }


def _synthesise_new_clips(*, credit_budget: int) -> dict[str, Any]:
    """Synthesise and promote the clips this tier adds. Cache hits cost nothing."""
    cache = ClipCache()
    spent_characters = 0
    spent_credits = 0
    records: dict[str, Any] = {}
    for clip in new_clips():
        key = clip_cache_key(
            text=clip.text,
            voice=clip.voice,
            model=clip.model,
            output_format="pcm_16000",
        )
        already = cache.get(key) is not None
        engine = ElevenLabsTTS(
            model_id=clip.model,
            voice_id=clip.voice,
            # `language_code` is dropped by the engine for the models that reject
            # it, so passing it is safe; pinning it matters because an unpinned
            # multilingual request is a guess the vendor makes silently.
            language_code=clip.language,
            credit_budget=credit_budget,
            cache=cache,
        )
        result = engine.synthesise(clip.text)
        if not already:
            spent_characters += len(clip.text)
            spent_credits += clip.credits
        promoted = cache.promote(key)
        records[clip.id] = {
            "key": key,
            "model": clip.model,
            "voice": clip.voice,
            "language": clip.language,
            "characters": len(clip.text),
            "credits": clip.credits,
            "cache_hit": already,
            "promoted": promoted,
            "duration_s": round(len(result.audio) / result.sample_rate, 3),
            "spoken_form_available": result.spoken_text is not None,
            # `eleven_flash_v2` is not a spoken-form model, so the phonetic row
            # has no WER reference at all. Recorded rather than inferred.
            "reference_source": "spoken" if result.spoken_text else "caller-input",
        }
        state = "cached" if already else f"synthesised {clip.credits} credits"
        print(f"  clip {clip.id:20s} {state}")
    return {
        "clips": records,
        "characters_spent": spent_characters,
        "credits_spent": spent_credits,
    }


def _record_transcripts() -> dict[str, Any]:
    """Transcribe every variant of every runnable row, twice, into one cassette.

    Twice: `smart_format=false` is the string every metric is computed on, and
    `smart_format=true` is kept for display only. Both go into one entry, so the
    offline replay can reproduce the formatting finding without a second lookup —
    and so nothing downstream has to guess which of the two it is holding.
    """
    cache = ClipCache()
    entries: dict[str, dict[str, Any]] = {}
    if CASSETTE.is_file():
        entries = dict(json.loads(CASSETTE.read_text(encoding="utf-8")).get("entries", {}))

    def record(audio: Any, rate: int, *, language: str, row_id: str, label: str) -> None:
        from lab.voice.engines.base import audio_digest  # noqa: PLC0415

        digest = audio_digest(audio, sample_rate=rate)
        if digest in entries:
            return
        raw = DeepgramSTT(language=language, smart_format=False).transcribe(
            audio, sample_rate=rate
        )
        smart = DeepgramSTT(
            language=language, smart_format=True, want_display=True
        ).transcribe(audio, sample_rate=rate)
        entries[digest] = {
            "row_id": row_id,
            "variant": label,
            "language": language,
            "text": raw.text,
            "display_text": smart.display_text or smart.text,
            "confidence": raw.confidence,
            "engine": raw.engine,
            "formatting": "raw",
            "provenance": "recorded",
        }
        print(f"    {row_id:42s} {label:28s} -> {raw.text[:52]!r}")

    for scenario in tier():
        if scenario.audio_status() != "runnable":
            continue
        language = ROW_LANGUAGE.get(scenario.id, DEFAULT_LANGUAGE)
        for variant in _variants(scenario):
            assembled = assemble_audio(
                scenario, cache=cache, override=variant["override"]
            )
            record(
                assembled.audio,
                assembled.sample_rate,
                language=language,
                row_id=scenario.id,
                label=str(variant["label"]),
            )
        control_id = scenario.audio.control_clip
        if control_id:
            import numpy as np  # noqa: PLC0415

            from lab.voice.engines.clipcache import clip_cache_key as _key  # noqa: PLC0415

            clip = clip_for(control_id)
            entry = cache.get(
                _key(
                    text=clip.text,
                    voice=clip.voice,
                    model=clip.model,
                    output_format="pcm_16000",
                )
            )
            if entry is not None:
                record(
                    np.asarray(entry.audio),
                    entry.sample_rate,
                    language=language,
                    row_id=scenario.id,
                    label=f"control:{control_id}",
                )

    CASSETTE.parent.mkdir(parents=True, exist_ok=True)
    CASSETTE.write_text(
        json.dumps({"entries": entries}, indent=1, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"entries": len(entries)}


def _record_evidence() -> dict[str, Any]:
    """Run the tier offline against what was just recorded, and commit the result.

    Offline on purpose, through the same `RecordedSTT` path the tests use. If the
    evidence were produced by the live engines it would be the only artefact in
    the repository that nobody could reproduce, and a difference between the live
    result and the replayed one — a changed digest, a dropped field — would be
    invisible in exactly the file that exists to be believed.
    """
    cassette = TranscriptCassette.load(CASSETTE)
    stt = RecordedSTT(cassette)
    cache = ClipCache()
    rows = [run_row(s, cache=cache, stt=stt) for s in tier()]
    payload = {
        "source": "scripts/make_audio_suite_fixtures.py",
        "note": (
            "Produced by replaying the committed cassette, not by calling the live "
            "engines, so this file is reproducible with every key unset."
        ),
        "cost": corpus_cost(),
        "rows": [row.model_dump(mode="json") for row in rows],
    }
    EVIDENCE.write_text(
        json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"rows": len(rows)}


def record(
    *, dry_run: bool = False, credit_budget: int = 600, evidence_only: bool = False
) -> int:
    # `--evidence-only` needs **no keys and no flags**, because it only replays
    # the committed cassette. Requiring credentials to regenerate a file that is
    # produced entirely from committed fixtures would be a false dependency, and
    # the kind that quietly makes an artefact unreproducible for anyone who
    # cloned the repository.
    if evidence_only:
        result = _record_evidence()
        print(f"evidence: {result['rows']} rows -> {EVIDENCE} (replay only, 0 credits)")
        return 0

    plan = _plan()
    print(
        f"audio tier: {plan['rows']} rows "
        f"({plan['runnable']} runnable, {len(plan['blocked'])} blocked, "
        f"{len(plan['untestable'])} untestable)"
    )
    print(
        f"clips: {plan['clips_total']} total, {plan['clips_new']} new, "
        f"{plan['clips_reused']} reused"
    )
    print(
        f"cost if uncached: {plan['characters_new']} characters, "
        f"{plan['credits_new']} credits "
        f"({plan['credits_avoided_by_reuse']} credits avoided by reuse, "
        f"{plan['credits_if_nothing_reused']} if nothing were reused)"
    )
    print(f"transcriptions to record: {plan['transcriptions']} (Deepgram, no character cost)")
    if dry_run:
        for clip in new_clips():
            print(
                f"  {clip.id:20s} {len(clip.text):3d} chars {clip.credits:3d} credits "
                f"{clip.model} lang={clip.language}"
            )
        return 0

    missing = [
        name
        for name in ("LAB_LIVE_TTS", "LAB_LIVE_STT", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        print(f"refusing to run: {', '.join(missing)} not set", file=sys.stderr)
        return 2

    print("synthesising new clips")
    tts = _synthesise_new_clips(credit_budget=credit_budget)
    print("recording transcripts")
    cassette = _record_transcripts()
    print("replaying to produce evidence")
    evidence = _record_evidence()

    print(
        f"\nledger: {tts['characters_spent']} characters, "
        f"{tts['credits_spent']} credits spent this run"
    )
    print(f"cassette: {cassette['entries']} entries -> {CASSETTE}")
    print(f"evidence: {evidence['rows']} rows -> {EVIDENCE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and its cost without spending anything",
    )
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help="re-derive the evidence file from the committed cassette; needs no keys",
    )
    parser.add_argument(
        "--credit-budget",
        type=int,
        default=600,
        help="hard ceiling for this run's ElevenLabs credits (default: 600)",
    )
    args = parser.parse_args(argv)
    return record(
        dry_run=args.dry_run,
        credit_budget=args.credit_budget,
        evidence_only=args.evidence_only,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
