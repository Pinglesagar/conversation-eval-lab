"""Run the in-process audio tier against the live recognisers a second time, and
report every figure with its denominator.

WHY THIS EXISTS RATHER THAN JUST RE-RUNNING THE GENERATOR
--------------------------------------------------------
`make_audio_suite_fixtures.py` is a *recorder*: it skips any audio digest already
in the cassette, which is correct for a generator (a re-run must not spend money
or churn committed evidence) but means that once the cassette is complete a
re-run makes **no live calls at all**. "I ran it live" would then be a claim about
a replay.

So this script does the one thing the recorder deliberately will not: it
re-transcribes **every** variant of every runnable row against the live API,
ignoring the committed cassette on the way in, and then compares the two
digest by digest. That turns an unfalsifiable claim into two numbers — how many
variants a second live pass reproduced exactly, and where it did not.

THE TTS LEDGER IS ZERO BY CONSTRUCTION, NOT BY HOPE
---------------------------------------------------
This script never imports or constructs the synthesiser. Audio comes only from
`ClipCache`, and a missing key is a hard refusal naming the clip. That is a
stronger guarantee than a budget guard: a guard can be wrong about a price, but
code with no path to the vendor cannot be charged by it. The character allowance
does not renew until the monthly reset, so "this run cannot spend" needed to be
a property of the program rather than an intention of the operator.

Recognition is charged against a Deepgram signup credit and is effectively free,
which is what makes a full second pass affordable at all: 36 variants, two
requests each (raw for scoring, smart for display), and no ElevenLabs characters.

WHAT IT REPORTS
---------------
Everything the tier can say, with the denominator attached to each figure:
capture accuracy per category, the field-level results for the digit and name
rows, the silent-correction rate with its attribution fraction, the ladder
breaking points, the silence verdicts against their thresholds, the control-arm
reading, and the untestable row counted as neither a pass nor a failure. The WER
pair and the TTS-intelligibility probe are read from the engine phase's evidence
and printed under names that cannot be confused for each other.
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

import numpy as np  # noqa: E402

from lab.trace import TraceBuilder  # noqa: E402
from lab.voice.adapter import silent_correction_report  # noqa: E402
from lab.voice.engines.base import audio_digest  # noqa: E402
from lab.voice.engines.clipcache import ClipCache, clip_cache_key  # noqa: E402
from lab.voice.engines.deepgram_stt import DeepgramSTT  # noqa: E402
from lab.voice.engines.stt import RecordedSTT, TranscriptCassette  # noqa: E402
from lab.voice.suite import (  # noqa: E402
    AUDIO_SUITE_CASSETTE,
    CLIPS,
    assemble_audio,
    clip_for,
    corpus_cost,
    is_cross_script,
    run_tier,
    spoken_reference,
    tier_summary,
)

# Imported rather than restated. The variant enumeration decides *which audio*
# gets transcribed; a second copy of it here that drifted by one rung would make
# this script's "reproduced exactly" figure a comparison between two different
# sounds, which is the one way this measurement can lie without looking wrong.
from scripts.make_audio_suite_fixtures import (  # noqa: E402
    DEFAULT_LANGUAGE,
    ROW_LANGUAGE,
    _variants,
)
from scenarios.audio import tier  # noqa: E402

OUT = REPO / "fixtures" / "audio" / "cloud"
COMMITTED_CASSETTE = OUT / AUDIO_SUITE_CASSETTE
LIVE_PASS = OUT / "audio_suite_live_pass.json"
ROUND_TRIP = OUT / "round_trip_evidence.json"


# --------------------------------------------------------------------------- #
# The live pass
# --------------------------------------------------------------------------- #


def _audio_for(scenario: Any, cache: ClipCache) -> list[dict[str, Any]]:
    """Every (label, audio) this row needs transcribed, plus its control clip."""
    out: list[dict[str, Any]] = []
    for variant in _variants(scenario):
        assembled = assemble_audio(scenario, cache=cache, override=variant["override"])
        out.append(
            {
                "label": str(variant["label"]),
                "audio": assembled.audio,
                "sample_rate": assembled.sample_rate,
            }
        )
    control_id = (scenario.audio.control_clip or "") if scenario.audio else ""
    if control_id:
        clip = clip_for(control_id)
        entry = cache.get(
            clip_cache_key(
                text=clip.text,
                voice=clip.voice,
                model=clip.model,
                output_format="pcm_16000",
            )
        )
        if entry is not None:
            out.append(
                {
                    "label": f"control:{control_id}",
                    "audio": np.asarray(entry.audio),
                    "sample_rate": entry.sample_rate,
                }
            )
    return out


def _require_every_clip_cached(cache: ClipCache) -> None:
    """Refuse before the first API call if any clip would need synthesising.

    Failing here rather than at the point of use is the difference between a run
    that costs nothing and a run that costs whatever was missing.
    """
    missing = [
        clip.id
        for clip in CLIPS.values()
        if clip_cache_key(
            text=clip.text,
            voice=clip.voice,
            model=clip.model,
            output_format="pcm_16000",
        )
        not in cache
    ]
    if missing:
        raise SystemExit(
            f"refusing to run: {len(missing)} clip(s) are not in the committed cache "
            f"({', '.join(sorted(missing))}). This script never synthesises, so it "
            "cannot fill them. Run scripts/make_audio_suite_fixtures.py, which "
            "prices the work first."
        )


def live_pass() -> dict[str, Any]:
    """Transcribe every variant live and diff it against the committed cassette."""
    missing_env = [
        name for name in ("LAB_LIVE_STT", "DEEPGRAM_API_KEY") if not os.environ.get(name)
    ]
    if missing_env:
        raise SystemExit(f"refusing to run live: {', '.join(missing_env)} not set")

    cache = ClipCache()
    _require_every_clip_cached(cache)
    committed = json.loads(COMMITTED_CASSETTE.read_text(encoding="utf-8"))["entries"]

    entries: dict[str, dict[str, Any]] = {}
    comparisons: list[dict[str, Any]] = []
    audio_seconds = 0.0
    requests = 0

    for scenario in tier():
        if scenario.audio_status() != "runnable":
            continue
        language = ROW_LANGUAGE.get(scenario.id, DEFAULT_LANGUAGE)
        for item in _audio_for(scenario, cache):
            audio = np.asarray(item["audio"])
            rate = int(item["sample_rate"])
            digest = audio_digest(audio, sample_rate=rate)
            if digest in entries:  # the same sound reached twice in one tier
                continue
            raw = DeepgramSTT(language=language, smart_format=False).transcribe(
                audio, sample_rate=rate
            )
            smart = DeepgramSTT(
                language=language, smart_format=True, want_display=True
            ).transcribe(audio, sample_rate=rate)
            requests += 2
            audio_seconds += 2.0 * audio.size / rate
            entries[digest] = {
                "row_id": scenario.id,
                "variant": item["label"],
                "language": language,
                "text": raw.text,
                "display_text": smart.display_text or smart.text,
                "confidence": raw.confidence,
                "engine": raw.engine,
                "formatting": "raw",
                "provenance": "recorded",
            }
            before = committed.get(digest)
            comparisons.append(
                {
                    "row_id": scenario.id,
                    "variant": item["label"],
                    "digest": digest,
                    "in_committed_cassette": before is not None,
                    "text_first_pass": None if before is None else before.get("text"),
                    "text_second_pass": raw.text,
                    "text_identical": (
                        None if before is None else before.get("text") == raw.text
                    ),
                    "confidence_first_pass": (
                        None if before is None else before.get("confidence")
                    ),
                    "confidence_second_pass": raw.confidence,
                }
            )
            flag = (
                "?"
                if before is None
                else ("same" if before.get("text") == raw.text else "DIFFERS")
            )
            print(f"    {scenario.id:42s} {item['label']:28s} {flag}")

    paired = [c for c in comparisons if c["in_committed_cassette"]]
    identical = [c for c in paired if c["text_identical"]]
    return {
        "note": (
            "A second live transcription of every variant, compared against the "
            "committed cassette digest by digest. No synthesiser is reachable from "
            "this script, so `elevenlabs_characters_spent` is zero by construction."
        ),
        "elevenlabs_characters_spent": 0,
        "elevenlabs_credits_spent": 0,
        "deepgram_requests": requests,
        "deepgram_audio_seconds": round(audio_seconds, 2),
        "variants_transcribed": len(entries),
        "variants_paired_with_first_pass": len(paired),
        "variants_reproduced_exactly": len(identical),
        "reproduction": f"{len(identical)}/{len(paired)} paired variants",
        "comparisons": sorted(comparisons, key=lambda c: (c["row_id"], c["variant"])),
        "entries": entries,
    }


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def _silent_corrections(results: list[Any], cache: ClipCache) -> dict[str, Any]:
    """Reconcile spoken against heard for every capture row that has a reference.

    Built through `TraceBuilder` rather than by calling the aligner directly, so
    the report inherits the same provenance and formatting refusals that protect
    it everywhere else: a reference-provenance or smart-formatted transcript
    raises instead of quietly reporting zero corrections.

    Rows without a usable reference are **excluded and listed**, never counted as
    zero. A reconciliation whose denominator silently absorbs the rows it could
    not check is the same defect as a naked percentage.
    """
    builder = TraceBuilder(
        scenario_id="audio-tier", adapter="voice:audio", session_id="audio-tier-live"
    )
    builder.session_start(latency_gate="PASS", latency_gate_detail="calibration gate PASS")
    declined: list[dict[str, str]] = []
    included: list[str] = []
    for row in results:
        if row.kind != "capture" or row.capture is None:
            continue
        reference, why = spoken_reference(row.clip_ids, cache=cache)
        if reference is None:
            declined.append({"row_id": row.scenario_id, "reason": why})
            continue
        included.append(row.scenario_id)
        builder.caller_utterance(
            reference, reference_source="spoken-form", spoken_text=reference
        )
        builder.transcript_in(
            row.capture.transcript,
            provenance="engine",
            formatting="raw",
            confidence=row.capture.confidence,
        )
    report = silent_correction_report(builder.build())

    # A correction whose two tokens are written in different alphabets is the
    # vendor and the recogniser disagreeing about script for the same word, not a
    # mishearing. Counted separately so it cannot be quoted as a recognition
    # error, and kept in the total so the total stays honest.
    cross_script = [
        c
        for c in report.corrections
        if c.kind == "substitution" and is_cross_script(c.spoken, c.heard)
    ]
    recognition = [c for c in report.corrections if c not in cross_script]
    return {
        "rows_reconciled": included,
        "rows_declined": declined,
        "turns_compared": report.turns_compared,
        "corrections": len(report.corrections),
        "per_hundred_turns": report.per_hundred_turns,
        "unattributable": report.unattributable,
        "attributed_fraction": report.attributed_fraction,
        "reference_source": report.reference_source,
        "description": report.describe(),
        "cross_script_corrections": len(cross_script),
        "recognition_corrections": len(recognition),
        "recognition_per_hundred_turns": (
            100.0 * len(recognition) / report.turns_compared
            if report.turns_compared
            else None
        ),
        "detail": [c.describe() for c in report.corrections],
        "cross_script_detail": [c.describe() for c in cross_script],
    }


def _wer_pair() -> dict[str, Any]:
    """The two named WER numbers from the engine phase, and nothing else.

    Only the `spoken-form/raw` cell is a legitimate figure; the other three are
    kept by the engine phase precisely so the illegitimate ones are visible. This
    reads the legitimate cell and reports raw and normalised side by side.
    """
    if not ROUND_TRIP.is_file():
        return {}
    payload = json.loads(ROUND_TRIP.read_text(encoding="utf-8"))
    rows = []
    for row in payload.get("rows", []):
        cell = (row.get("wer_matrix") or {}).get("spoken-form/raw")
        if cell is None:
            continue
        rows.append(
            {
                "row_id": row["row_id"],
                "group": row.get("group"),
                "language": row.get("language"),
                "reference_source": row.get("reference_source"),
                "raw_wer": cell["raw_wer"],
                "normalised_wer": cell["normalised_wer"],
                "reference_words": cell["reference_words"],
                "errors": cell["errors"],
            }
        )
    scored = [r for r in rows if r["reference_source"] == "spoken-form"]
    words = sum(r["reference_words"] for r in scored)
    errors = sum(r["errors"] for r in scored)
    return {
        "metric": "tts_intelligibility_probe",
        "caveat": (
            "Both sides of every figure below were produced by this harness: the "
            "audio by our TTS, the transcript by our STT. There is no agent in this "
            "loop at all, so none of these numbers is an agent word error rate. "
            "They are the instrument's noise floor — the error a caller-side row "
            "inherits before the system under test has done anything."
        ),
        "rows": rows,
        "corpus_rows_with_spoken_form_reference": len(scored),
        "corpus_rows_total": len(rows),
        "corpus_reference_words": words,
        "corpus_errors": errors,
        "corpus_normalised_wer": (errors / words) if words else None,
        "corpus_raw_wer_mean": (
            sum(r["raw_wer"] for r in scored) / len(scored) if scored else None
        ),
    }


def build_report() -> dict[str, Any]:
    """Replay the committed cassette and assemble every figure the tier can state."""
    cassette = TranscriptCassette.load(COMMITTED_CASSETTE)
    stt = RecordedSTT(cassette)
    cache = ClipCache()
    results = run_tier(tier(), cache=cache, stt=stt)
    summary = tier_summary(results)

    by_category: dict[str, dict[str, Any]] = {}
    for row in results:
        bucket = by_category.setdefault(
            row.category, {"rows": 0, "runnable": 0, "passed": 0, "ids": []}
        )
        bucket["rows"] += 1
        bucket["ids"].append(row.scenario_id)
        if row.status == "runnable":
            bucket["runnable"] += 1
            if row.passed:
                bucket["passed"] += 1
    for bucket in by_category.values():
        bucket["result"] = f"{bucket['passed']}/{bucket['runnable']} runnable"

    fields = [
        {
            "row_id": row.scenario_id,
            "expected_capture": row.capture.expected_capture,
            "passed": row.passed,
            "confidence": row.capture.confidence,
            "fields": row.capture.fields,
            "numeric": row.capture.numeric,
            "numeric_seen": row.capture.numeric_seen,
            "verbatim": row.capture.verbatim,
            "transcript": row.capture.transcript,
            "control_transcript": None if row.control is None else row.control.transcript,
            "control_all_captured": (
                None if row.control is None else row.control.all_captured
            ),
            "control_confidence": None if row.control is None else row.control.confidence,
        }
        for row in results
        if row.kind == "capture" and row.capture is not None
    ]
    checks = [ok for row in fields for ok in
              list(row["fields"].values()) + list(row["numeric"].values())
              + list(row["verbatim"].values())]

    ladders = [
        {
            "row_id": row.scenario_id,
            "axis": row.ladder.axis,
            "parameter": row.ladder.parameter,
            "rungs": list(row.ladder.rungs),
            "captured": list(row.ladder.captured),
            "held_to": row.ladder.held_to,
            "broke_at": row.ladder.broke_at,
            "description": row.ladder.describe(),
        }
        for row in results
        if row.ladder is not None
    ]
    silences = [
        {
            "row_id": row.scenario_id,
            "declared_pause_s": row.silence.declared_pause_s,
            "measured_silence_s": row.silence.measured_silence_s,
            "threshold_s": row.silence.threshold_s,
            "declared_matches_measured": row.silence.declared_matches_measured,
            "fires": row.silence.fires,
            "verdict": row.silence.verdict,
            "expected_verdict": row.silence.expected_verdict,
            "reason_is_accurate": row.silence.reason_is_accurate,
            "expected_reason_accurate": row.silence.expected_reason_accurate,
            "passed": row.passed,
            "description": row.silence.description,
        }
        for row in results
        if row.silence is not None
    ]
    barge_ins = [
        {
            "row_id": row.scenario_id,
            "yielded": row.barge_in.yielded,
            "yield_ms": row.barge_in.yield_ms,
            "budget_ms": row.barge_in.budget_ms,
            "overlap_s": row.barge_in.overlap_s,
            "agent_duration_s": row.barge_in.agent_duration_s,
            "passed": row.passed,
        }
        for row in results
        if row.barge_in is not None
    ]
    corrections = _silent_corrections(results, cache)

    return {
        "source": "scripts/run_audio_live.py",
        "note": (
            "Assembled by replaying the committed cassette, so every figure here is "
            "reproducible with each key unset. The live second pass is a separate "
            "file; this one is the tier's result."
        ),
        "cost": corpus_cost(),
        "summary": summary,
        "by_category": by_category,
        "field_level": fields,
        "field_checks": {
            "checks": len(checks),
            "captured": sum(1 for ok in checks if ok),
            "result": f"{sum(1 for ok in checks if ok)}/{len(checks)} declared field checks",
        },
        "ladders": ladders,
        "silence": silences,
        "barge_in": barge_ins,
        "silent_corrections": corrections,
        "wer": _wer_pair(),
        "untestable": [
            {
                "row_id": row.scenario_id,
                "language": row.untestable_language,
                "remediation": list(row.remediation),
                "passed": row.passed,
            }
            for row in results
            if row.status == "untestable"
        ],
        "blocked": [
            {"row_id": row.scenario_id, "note": row.note, "passed": row.passed}
            for row in results
            if row.status == "blocked"
        ],
    }


def print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("\n== tier ==")
    print(
        f"  {summary['rows']} rows: {summary['runnable']} runnable, "
        f"{len(summary['blocked'])} blocked, {len(summary['untestable'])} untestable"
    )
    print(f"  verdict: {summary['pass_rate']} passed")

    print("\n== capture accuracy per category ==")
    for category, bucket in sorted(report["by_category"].items()):
        print(f"  {category:22s} {bucket['result']:20s} ({bucket['rows']} rows in tier)")

    print("\n== field-level results ==")
    for row in report["field_level"]:
        checks = {**row["fields"], **row["numeric"], **row["verbatim"]}
        rendered = ", ".join(f"{k}={'ok' if v else 'MISSED'}" for k, v in sorted(checks.items()))
        conf = "n/a" if row["confidence"] is None else f"{row['confidence']:.3f}"
        print(f"  {row['row_id']:44s} conf {conf}  {rendered}")
        print(f"      heard: {row['transcript']!r}")
        if row["control_transcript"] is not None:
            print(
                f"      control: {row['control_transcript']!r} "
                f"(all captured: {row['control_all_captured']})"
            )
    print(f"  total: {report['field_checks']['result']}")

    print("\n== silent corrections (ground truth is an input, not an inference) ==")
    sc = report["silent_corrections"]
    print(f"  {sc['description']}")
    print(
        f"  of those, {sc['recognition_corrections']} are recognition corrections "
        f"and {sc['cross_script_corrections']} are cross-script (the vendor and the "
        f"recogniser choosing different alphabets for the same word)"
    )
    for line in sc["detail"]:
        tag = " [cross-script]" if line in sc["cross_script_detail"] else ""
        print(f"    {line}{tag}")
    print(f"  reconciled {len(sc['rows_reconciled'])} rows; declined {len(sc['rows_declined'])}:")
    for row in sc["rows_declined"]:
        print(f"    {row['row_id']}: {row['reason']}")

    print("\n== line-quality ladders ==")
    for ladder in report["ladders"]:
        print(f"  {ladder['row_id']:44s} {ladder['description']}")
        pairs = ", ".join(
            f"{rung}{'ok' if ok else ' BREAK'}"
            for rung, ok in zip(ladder["rungs"], ladder["captured"], strict=False)
        )
        print(f"      {ladder['parameter']}: {pairs}")

    print("\n== silence attribution ==")
    for row in report["silence"]:
        print(
            f"  {row['row_id']:44s} pause {row['declared_pause_s']}s "
            f"(measured {row['measured_silence_s']:.3f}s, declared==measured "
            f"{row['declared_matches_measured']}) threshold {row['threshold_s']}s "
            f"-> fires={row['fires']} verdict={row['verdict']} "
            f"(expected {row['expected_verdict']}) reason_accurate="
            f"{row['reason_is_accurate']} (expected {row['expected_reason_accurate']}) "
            f"{'PASS' if row['passed'] else 'FAIL'}"
        )

    print("\n== barge-in ==")
    for row in report["barge_in"]:
        yielded = "no" if not row["yielded"] else f"{row['yield_ms']:.0f} ms"
        print(
            f"  {row['row_id']:44s} yielded={yielded} budget={row['budget_ms']} "
            f"overlap={row['overlap_s']:.3f}s {'PASS' if row['passed'] else 'FAIL'}"
        )

    wer = report.get("wer") or {}
    if wer:
        print(
            "\n== TTS-intelligibility probe: raw and normalised, spoken-form reference, "
            "raw formatting =="
        )
        print(f"  NOT an agent WER. {wer['caveat']}")
        for row in wer["rows"]:
            print(
                f"  {row['row_id']:26s} {row['language']:5s} raw_wer={row['raw_wer']:.3f} "
                f"normalised_wer={row['normalised_wer']:.3f} "
                f"({row['errors']}/{row['reference_words']} words, ref={row['reference_source']})"
            )
        print(
            f"  corpus RAW error rate (mean over rows): {wer['corpus_raw_wer_mean']:.4f}"
        )
        print(
            f"  corpus NORMALISED error rate: {wer['corpus_errors']}/"
            f"{wer['corpus_reference_words']} words = {wer['corpus_normalised_wer']:.4f} "
            f"over {wer['corpus_rows_with_spoken_form_reference']} of "
            f"{wer['corpus_rows_total']} rows"
        )
        print(
            "  the gap between those two figures is the whole subject of "
            "lab/voice/engines/WER_NORMALISATION.md: same audio, same transcripts, "
            "one canonicalisation step apart"
        )

    print("\n== untestable, counted as neither pass nor fail ==")
    for row in report["untestable"]:
        print(
            f"  {row['row_id']:44s} language={row['language']} passed={row['passed']} "
            f"remediation={row['remediation']}"
        )
    for row in report["blocked"]:
        print(f"  BLOCKED {row['row_id']:36s} passed={row['passed']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="re-transcribe every variant against the live API and diff the two passes",
    )
    args = parser.parse_args(argv)

    if args.live:
        print("live second pass: re-transcribing every variant")
        result = live_pass()
        LIVE_PASS.write_text(
            json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"\n  reproduced: {result['reproduction']}; "
            f"{result['deepgram_requests']} Deepgram requests over "
            f"{result['deepgram_audio_seconds']}s of audio; "
            f"{result['elevenlabs_characters_spent']} ElevenLabs characters"
        )
        print(f"  wrote {LIVE_PASS}")

    report = build_report()
    (OUT / "audio_suite_report.json").write_text(
        json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print_report(report)
    print(f"\n  wrote {OUT / 'audio_suite_report.json'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
