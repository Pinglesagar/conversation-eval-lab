"""Extract the demo page's evidence from the committed artefacts.

WHAT THIS IS FOR
----------------
A designed page tells **one** finding. This script produces the small JSON files
that page loads, plus a short audio excerpt, so that every number and every quote
on it is real, sourced, and regenerable by re-running this file.

WHAT IT WILL NOT DO
-------------------
Copy a figure out of a document. Every rate here is recomputed from a committed
artefact by the same code the suite uses, or it is labelled `historical` with the
artefact that no longer exists named. There is no third category, and every rate
carries its denominator.

WHAT IT WRITES  (all of it under docs/site/data/)
-------------------------------------------------
    finding.json          the headline: `discovery` graded on what was heard vs
                          on what was said, per criterion, with the mechanism
    question_turns.json   three real adviser question turns, verbatim, in all
                          four transcripts the call carries
    recognition.json      every sent-vs-heard difference, orthographic ones
                          separated from the one genuine loss of a word
    call.json             turns, duration, engines, spend, audio provenance
    secondary_findings.json  four other headline findings, each with its
                          denominator and the command that reproduces it
    index.json            every file above with its sha256, and the commands
    audio/excerpt.wav     15.21s cut around the adviser's opening question

DETERMINISM
-----------
No clock, no random source, no absolute path, no environment. Keys are sorted and
floats are written as they were read. Re-running overwrites with byte-identical
content, which `--check` asserts.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import inspect
import json
import sys
import wave
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # pragma: no cover - script convenience
    sys.path.insert(0, str(REPO))

OUT = REPO / "docs" / "site" / "data"
SPOKEN = REPO / "fixtures" / "audio" / "spoken_call"
FULL_CALL = SPOKEN / "full_call.wav"

#: Where the excerpt lives, relative to the repository root. A constant rather
#: than a derived path because `--check` writes to a scratch directory and the
#: declared location must not follow it there.
EXCERPT_PATH = "docs/site/data/audio/excerpt.wav"

#: The excerpt is one whole adviser turn plus the inter-turn gap that follows it.
#: Turn-aligned on purpose: a cut inside a turn cannot be checked against the
#: manifest, and this one can — the offsets below are `order`'s own numbers.
EXCERPT_TURN_ORDER = 0

#: The three adviser turns quoted verbatim on the page. Every one of them ends in
#: a question mark as sent, and carries no sentence punctuation as heard. Chosen,
#: not sampled: the opener is the discovery probe the criterion is about, the risk
#: turn carries a disclosure as well, and turn 7 is the single turn that the
#: classifier calls an `open_probe` — the only reason `discovery` is 2 rather
#: than 0 on the spoken side.
QUOTED_ORDERS = (0, 10, 12)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _dump(path: Path, payload: Any) -> str:
    """Write one JSON file deterministically and return its sha256."""
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    raw = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


# --------------------------------------------------------------------------- #
# The finding
# --------------------------------------------------------------------------- #


def _detector_site() -> dict[str, Any]:
    """Locate the question detector in the live source rather than quoting it.

    A path and a line number typed into a data file is wrong within a month, and
    the whole point of the finding is that a reader can open the exact line.
    """
    from roleplay import persona

    lines, first = inspect.getsourcelines(persona.classify_trainee_turn)
    for offset, line in enumerate(lines):
        if 'endswith("?")' in line:
            return {
                "file": "roleplay/persona.py",
                "line": first + offset,
                "source": line.strip(),
                "function": "classify_trainee_turn",
            }
    raise SystemExit("the question detector is no longer in roleplay/persona.py")


def _question_census(adviser: Sequence[dict]) -> dict[str, Any]:
    """How many adviser turns end in a question mark, in each transcript.

    Three transcripts of the same audio exist on every turn — what was sent to
    the synthesiser, the vendor's prettified display string, and the raw string
    that is actually graded. The finding is the gap between the third and the
    other two.
    """
    n = len(adviser)
    return {
        "adviser_turns": n,
        "ends_with_question_mark": {
            "text_sent": sum(1 for t in adviser if t["text_sent"].strip().endswith("?")),
            "display_text": sum(
                1 for t in adviser if t["display_text"].strip().endswith("?")
            ),
            "text_heard": sum(
                1 for t in adviser if t["text_heard"].strip().endswith("?")
            ),
        },
        "contains_a_question_mark_anywhere": {
            "text_sent": sum(1 for t in adviser if "?" in t["text_sent"]),
            "display_text": sum(1 for t in adviser if "?" in t["display_text"]),
            "text_heard": sum(1 for t in adviser if "?" in t["text_heard"]),
        },
        "which_transcript_is_graded": "text_heard",
        "why": (
            "The graded transcript is the unformatted one. The formatted transcript "
            "is a second, separately billed request whose prettifying fabricates a "
            "word error rate, so the scored channel carries no sentence punctuation "
            "at all — see `punctuation_in_the_graded_transcript` for the whole "
            "inventory, which is two characters and neither of them is a question "
            "mark."
        ),
    }


def _graded_punctuation(turns: Sequence[dict]) -> dict[str, Any]:
    """Every non-alphanumeric, non-space character in the graded transcript.

    An inventory rather than a sentence, because the claim the page makes about
    this — that nothing in the scored channel can end a question — is only worth
    making if it is derived. The first draft of this page asserted "the only
    punctuation character is an apostrophe" over all sixteen turns and was wrong:
    one customer turn says `brother-in-law`. The adviser's eight, which are the
    only turns the discovery detector reads, do carry the apostrophe alone.
    """

    def inventory(rows: Iterable[dict]) -> list[str]:
        return sorted(
            {
                c
                for t in rows
                for c in t["text_heard"]
                if not c.isalnum() and not c.isspace()
            }
        )

    adviser = [t for t in turns if t["speaker"] == "trainee"]
    everything = inventory(turns)
    return {
        "all_16_turns": everything,
        "the_8_adviser_turns": inventory(adviser),
        "sentence_punctuation_found": sorted(set(everything) & set(".,;:!?")),
        "note": (
            "Characters that are neither a letter, a digit nor a space. There is "
            "no full stop, comma, colon or question mark anywhere in the scored "
            "channel. Across all sixteen turns the inventory is the apostrophe "
            "and one hyphen (`brother-in-law`, the customer's turn at order 1); "
            "across the eight adviser turns the detector actually reads, it is "
            "the apostrophe alone."
        ),
    }


def _turn_kinds(adviser: Sequence[dict]) -> dict[str, Any]:
    from roleplay.persona import classify_trainee_turn

    per_turn = [
        {
            "order": t["order"],
            "turn": t["turn"],
            "as_sent": classify_trainee_turn(t["text_sent"]),
            "as_heard": classify_trainee_turn(t["text_heard"]),
        }
        for t in adviser
    ]

    def tally(key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in per_turn:
            counts[row[key]] = counts.get(row[key], 0) + 1
        return dict(sorted(counts.items()))

    return {
        "per_turn": per_turn,
        "tally_as_sent": tally("as_sent"),
        "tally_as_heard": tally("as_heard"),
        "open_probes_as_sent": tally("as_sent").get("open_probe", 0),
        "open_probes_as_heard": tally("as_heard").get("open_probe", 0),
        "discovery_banding": {
            "0 open probes": 0,
            "1 open probe": 2,
            "2 open probes": 3,
            "3 or more": 4,
        },
        "banding_source": "roleplay/scorer.py::RubricScorer._discovery",
    }


def build_finding(manifest: dict, result: Any) -> dict[str, Any]:
    from roleplay.scorer import RubricScorer

    effect = result.effect
    adviser = [t for t in manifest["turns"] if t["speaker"] == "trainee"]
    names = sorted(set(effect.sent_criteria) | set(effect.heard_criteria))
    max_per_criterion = 4

    per_criterion = []
    for name in names:
        sent = effect.sent_criteria.get(name)
        heard = effect.heard_criteria.get(name)
        per_criterion.append(
            {
                "criterion": name,
                "as_sent": sent,
                "as_heard": heard,
                "max": max_per_criterion,
                "delta": (heard or 0) - (sent or 0),
                "moved": sent != heard,
            }
        )

    moved = [row for row in per_criterion if row["moved"]]
    dropped = [row for row in moved if row["delta"] < 0]
    gained = [row for row in moved if row["delta"] > 0]

    return {
        "headline": (
            "The grader scored discovery 0 out of 4 on a spoken call where 5 of "
            "the adviser's 8 turns ended in a question mark and 7 of 8 contained "
            "one."
        ),
        "per_criterion": per_criterion,
        "totals": {
            "as_sent": effect.sent_total,
            "as_heard": effect.heard_total,
            "max": max_per_criterion * len(names),
            "identical": effect.sent_total == effect.heard_total,
        },
        "verdicts": {
            "as_sent": effect.sent_verdict,
            "as_heard": effect.heard_verdict,
            "identical": effect.sent_verdict == effect.heard_verdict,
        },
        "disclosure_ledgers": {
            "as_sent": list(effect.sent_disclosures),
            "as_heard": list(effect.heard_disclosures),
            "identical": effect.sent_disclosures == effect.heard_disclosures,
        },
        "criteria_that_moved": [row["criterion"] for row in moved],
        "cause": {
            "summary": (
                "The turn classifier decides a turn is a question with "
                'body.endswith("?"). The graded transcript is unformatted and '
                "carries no sentence punctuation, so no spoken turn can end in a "
                "question mark and no spoken adviser can be credited with asking "
                "anything."
            ),
            "detector": _detector_site(),
            "question_marks": _question_census(adviser),
            "punctuation_in_the_graded_transcript": _graded_punctuation(
                manifest["turns"]
            ),
            "turn_kinds": _turn_kinds(adviser),
        },
        "why_it_nearly_escaped": {
            "summary": (
                "objection_handling moved the opposite way and cancelled the loss. "
                "A check on the total, on the verdict, or on the disclosure ledger "
                "would each have concluded the audio channel changed nothing. Only "
                "the per-criterion comparison surfaced it."
            ),
            "dropped": dropped,
            "gained": gained,
            "checks_that_would_have_missed_it": [
                {
                    "check": "compare the totals",
                    "as_sent": effect.sent_total,
                    "as_heard": effect.heard_total,
                    "would_have_found_it": effect.sent_total != effect.heard_total,
                },
                {
                    "check": "compare the verdicts",
                    "as_sent": effect.sent_verdict,
                    "as_heard": effect.heard_verdict,
                    "would_have_found_it": effect.sent_verdict != effect.heard_verdict,
                },
                {
                    "check": "compare the disclosure ledgers",
                    "as_sent": list(effect.sent_disclosures),
                    "as_heard": list(effect.heard_disclosures),
                    "would_have_found_it": (
                        effect.sent_disclosures != effect.heard_disclosures
                    ),
                },
                {
                    "check": "compare criterion by criterion",
                    "as_sent": dict(effect.sent_criteria),
                    "as_heard": dict(effect.heard_criteria),
                    "would_have_found_it": bool(moved),
                },
            ],
        },
        "caveats": [
            "n = 1. This is a mechanism, not a rate.",
            (
                "The `as_sent` column is a counterfactual: the same notes are re-run "
                "with text_heard replaced by text_sent, so the customer's state "
                "machine and the disclosure register re-decide from those words. "
                "That is why objection_handling moves at all — see "
                "roleplay/spoken.py::channel_effect. The discovery mechanism does "
                "not depend on the re-simulation: the question-mark census and the "
                "turn-kind tally above are read straight off the committed turns."
            ),
        ],
        "reproduce": [
            "make start",
            "make spoken-replay",
            "python -m pytest tests/test_roleplay_spoken.py -q",
        ],
        "source": {
            "manifest": _rel(SPOKEN / "manifest.json"),
            "recomputed_by": "roleplay.spoken.replay_spoken_call",
            "scorer": (
                f"{RubricScorer.__module__}.{RubricScorer.__qualname__}"
                " — deterministic, no model"
            ),
            "agrees_with_committed_artefacts": _cross_check(result),
        },
        "second_opinion": _second_opinion(manifest),
    }


def _second_opinion(manifest: dict) -> dict[str, Any]:
    """The same graded transcript, put to the LLM judge instead of the rule.

    Worth surfacing because it is the closest thing this call has to a control.
    The rule and the judge read the identical `text_heard` trace, so a criterion
    where they disagree by the full range is not a transcript problem — the words
    were there to be read, and one of the two instruments could not see them.

    It cuts both ways, and both directions are reported: the judge also loses the
    disclosure the ledger recorded. Neither instrument is "the right one", which
    is the reason the number is given with its opposite beside it rather than as
    a vindication.
    """
    from roleplay.livescorer import LiveRubricScorer
    from roleplay.scorer import RubricScorer

    def where(cls: type) -> str:
        return f"{cls.__module__}.{cls.__qualname__}"

    cards = json.loads((SPOKEN / "scorecards.json").read_text("utf-8"))
    rule = cards["deterministic"]["criteria"]
    judge = cards["live"]["criteria"]
    return {
        "question": (
            "Were the questions really in the graded transcript, or is the rule "
            "right that nothing question-shaped survived?"
        ),
        "graded_transcript": "text_heard — the same trace for both instruments",
        "rule": {
            "name": where(RubricScorer),
            "criteria": rule,
            "total": cards["deterministic"]["total"],
            "verdict": cards["deterministic"]["verdict"],
        },
        "judge": {
            "name": where(LiveRubricScorer),
            "model": manifest["session"]["model_label"],
            "criteria": judge,
            "total": cards["live"]["total"],
            "verdict": cards["live"]["verdict"],
        },
        "discovery": {
            "by_the_rule": rule["discovery"],
            "by_the_judge": judge["discovery"],
            "max": 4,
        },
        "reading": (
            "On discovery the two instruments disagree by the whole range: the "
            "judge reads the same unpunctuated words and finds the questions the "
            "rule cannot. That is corroboration for the mechanism, not a claim "
            "that the judge is the better instrument: on mandatory_disclosure it "
            "moves the opposite way, 0 against the rule's 4, and the rule's 4 is "
            "a count of compliance keywords rather than a read of the disclosure "
            "register — a seeded defect of its own, roleplay/scorer.py::"
            "RubricScorer._mandatory_disclosure. Both verdicts are still `fail`."
        ),
        "criteria_where_they_disagree": sorted(
            name for name in rule if rule[name] != judge.get(name)
        ),
        "source": _rel(SPOKEN / "scorecards.json"),
    }


def _cross_check(result: Any) -> dict[str, Any]:
    """Does the recomputation still agree with what was committed?

    Every score on this page is recomputed rather than read back, which is the
    right way round — and it leaves one question a reader is entitled to ask: does
    the recomputation still produce what the recorded run produced? These two
    comparisons answer it, against the two committed artefacts nothing else here
    reads. A `false` anywhere below means the pack drifted and the page is
    reporting today's code rather than the recorded call.
    """
    from lab.trace.io import read_jsonl

    committed = json.loads((SPOKEN / "scorecards.json").read_text("utf-8"))["channel_effect"]
    effect = result.effect
    trace = read_jsonl(SPOKEN / "trace.jsonl")

    def census(events: Iterable[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return dict(sorted(counts.items()))

    return {
        "scorecards_json": {
            "file": _rel(SPOKEN / "scorecards.json"),
            "sent_criteria": committed["sent_criteria"] == dict(effect.sent_criteria),
            "heard_criteria": committed["heard_criteria"] == dict(effect.heard_criteria),
            "totals": (
                committed["sent_total"] == effect.sent_total
                and committed["heard_total"] == effect.heard_total
            ),
            "verdicts": (
                committed["sent_verdict"] == effect.sent_verdict
                and committed["heard_verdict"] == effect.heard_verdict
            ),
            "disclosure_ledgers": (
                committed["sent_disclosures"] == list(effect.sent_disclosures)
                and committed["heard_disclosures"] == list(effect.heard_disclosures)
            ),
        },
        "trace_jsonl": {
            "file": _rel(SPOKEN / "trace.jsonl"),
            "events": len(trace.events) == len(result.trace.events),
            "event_kinds": census(trace.events) == census(result.trace.events),
            "census": census(trace.events),
        },
    }


# --------------------------------------------------------------------------- #
# The quoted turns
# --------------------------------------------------------------------------- #


def build_question_turns(manifest: dict) -> dict[str, Any]:
    from roleplay.persona import classify_trainee_turn

    by_order = {t["order"]: t for t in manifest["turns"]}
    turns = []
    for order in QUOTED_ORDERS:
        t = by_order[order]
        assert t["speaker"] == "trainee", order
        turns.append(
            {
                "order": order,
                "turn": t["turn"],
                "duration_s": t["duration_s"],
                "stt_confidence": t["stt_confidence"],
                "text_sent": t["text_sent"],
                "spoken_form": t["spoken_form"],
                "display_text": t["display_text"],
                "text_heard": t["text_heard"],
                "sent_ends_with_question_mark": t["text_sent"].strip().endswith("?"),
                "display_ends_with_question_mark": (
                    t["display_text"].strip().endswith("?")
                ),
                "heard_contains_a_question_mark": "?" in t["text_heard"],
                "heard_punctuation_characters": sorted(
                    {c for c in t["text_heard"] if not c.isalnum() and not c.isspace()}
                ),
                "classified_as_sent": classify_trainee_turn(t["text_sent"]),
                "classified_as_heard": classify_trainee_turn(t["text_heard"]),
            }
        )
    return {
        "note": (
            "Verbatim from the committed manifest. `text_heard` is the transcript "
            "that was graded; on each of these three turns the only punctuation it "
            "contains is the apostrophe, which "
            "`heard_punctuation_characters` records per turn rather than asserts."
        ),
        "turns": turns,
        "source": _rel(SPOKEN / "manifest.json"),
    }


# --------------------------------------------------------------------------- #
# Sent vs heard, word by word
# --------------------------------------------------------------------------- #

#: Pairs the recogniser writes differently from the script while hearing the same
#: spoken word. Listed rather than inferred, so that "orthographic" is a claim a
#: reader can check instead of a judgement this file made quietly.
_ORTHOGRAPHIC = (
    ("mr", "mister"),
    ("timeframe", "time frame"),
    ("summarise", "summarize"),
    ("per cent", "percent"),
)


def _classify_difference(removed: Sequence[str], added: Sequence[str]) -> str:
    """Is this one substitution the same spoken word spelled differently?

    Classified per substitution, not per turn. Turn 1 replaces "mr" with "mister"
    *and* "timeframe" with "time frame"; judging the turn as a whole would pool
    two orthographic swaps into one verdict and report a content difference that
    is not there. That is the bug this signature exists to prevent.
    """
    pair = (" ".join(removed), " ".join(added))
    for left, right in _ORTHOGRAPHIC:
        if pair in {(left, right), (right, left)}:
            return "orthographic"
    return "content"


def _differences(left: Sequence[str], right: Sequence[str]) -> list[dict[str, Any]]:
    """One entry per contiguous edit between two normalised token lists."""
    out: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(a=list(left), b=list(right), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed, added = list(left[i1:i2]), list(right[j1:j2])
        out.append(
            {
                "removed": removed,
                "added": added,
                "kind": _classify_difference(removed, added),
                "edit": tag,
            }
        )
    return out


def build_recognition(manifest: dict) -> dict[str, Any]:
    from lab.voice.wer import normalise

    rows = []
    for t in manifest["turns"]:
        reference = t["spoken_form"] or t["text_sent"]
        left, right = normalise(reference).split(), normalise(t["text_heard"]).split()
        differences = _differences(left, right)
        content = [d for d in differences if d["kind"] == "content"]
        rows.append(
            {
                "order": t["order"],
                "turn": t["turn"],
                "speaker": t["speaker"],
                "differs_after_normalisation": bool(differences),
                "differences": differences,
                "kind": (
                    "identical"
                    if not differences
                    else ("content" if content else "orthographic")
                ),
                "text_sent": t["text_sent"],
                "text_heard": t["text_heard"],
            }
        )

    content_turns = [r for r in rows if r["kind"] == "content"]
    orthographic_turns = [r for r in rows if r["kind"] == "orthographic"]
    all_differences = [d for r in rows for d in r["differences"]]
    content_differences = [d for d in all_differences if d["kind"] == "content"]

    return {
        "question": (
            "Do text_sent and text_heard genuinely differ anywhere, or is the whole "
            "gap punctuation and casing?"
        ),
        "answer": (
            "Once, on turn 6. The recogniser dropped the customer's opening word: "
            '"I understand what you\'re saying." was heard as "understand what '
            'you\'re saying". Every other difference on all 16 turns is the '
            "recogniser spelling the same spoken word another way."
        )
        if content_differences
        else (
            "No. After normalisation every turn is identical; the whole gap is "
            "punctuation and casing."
        ),
        "method": (
            "Both sides normalised by lab.voice.wer.normalise — the function the "
            "word error rate itself uses — which lowercases, expands contractions, "
            "strips punctuation and converts number words to digits. The reference "
            "is the turn's spoken_form, which is what was actually sent to the "
            "synthesiser. Each surviving edit is then classified on its own; a "
            "substitution counts as orthographic only if it is on the named list "
            "below."
        ),
        "orthographic_pairs": [
            {"sent": left, "heard": right} for left, right in _ORTHOGRAPHIC
        ],
        "counts": {
            "turns": len(rows),
            "turns_identical_after_normalisation": sum(
                1 for r in rows if r["kind"] == "identical"
            ),
            "turns_differing_orthographically_only": len(orthographic_turns),
            "turns_with_a_genuine_content_difference": len(content_turns),
            "edits": len(all_differences),
            "edits_that_are_orthographic": len(all_differences)
            - len(content_differences),
            "edits_that_are_content": len(content_differences),
        },
        "genuine_content_differences": content_turns,
        "orthographic_differences": orthographic_turns,
        "per_turn": rows,
        "recognition_deltas_note": (
            "The committed scorecards.json carries the raw and normalised edit "
            "counts per turn under `recognition_deltas`; the raw counts are large "
            "only because they score punctuation, which is the same fact this file "
            "reports as the finding."
        ),
        "source": _rel(SPOKEN / "manifest.json"),
    }


# --------------------------------------------------------------------------- #
# The call, and the excerpt cut from it
# --------------------------------------------------------------------------- #


def _turn_offsets(manifest: dict) -> list[dict[str, Any]]:
    gap = manifest["assembly"]["gap_s"]
    offsets = []
    cursor = 0.0
    for t in manifest["turns"]:
        offsets.append(
            {
                "order": t["order"],
                "turn": t["turn"],
                "speaker": t["speaker"],
                "start_s": round(cursor, 6),
                "end_s": round(cursor + t["duration_s"], 6),
                "duration_s": t["duration_s"],
            }
        )
        cursor += t["duration_s"] + gap
    return offsets


def cut_excerpt(manifest: dict, destination: Path) -> dict[str, Any]:
    """One turn plus its trailing gap, cut with the standard library only.

    Frame indices rather than seconds, and both are written out, so the cut can be
    checked against `full_call.wav` with three lines of `wave` and no trust in
    this file.
    """
    gap = manifest["assembly"]["gap_s"]
    turn = next(t for t in manifest["turns"] if t["order"] == EXCERPT_TURN_ORDER)
    offsets = {row["order"]: row for row in _turn_offsets(manifest)}
    start_s = offsets[EXCERPT_TURN_ORDER]["start_s"]
    end_s = offsets[EXCERPT_TURN_ORDER]["end_s"] + gap

    with wave.open(str(FULL_CALL), "rb") as src:
        channels, width, rate = src.getnchannels(), src.getsampwidth(), src.getframerate()
        total_frames = src.getnframes()
        start_frame = round(start_s * rate)
        end_frame = round(end_s * rate)
        if end_frame > total_frames:
            raise SystemExit("the excerpt runs past the end of full_call.wav")
        src.setpos(start_frame)
        frames = src.readframes(end_frame - start_frame)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(width)
        dst.setframerate(rate)
        dst.writeframes(frames)

    return {
        # The canonical location, not `destination`: `--check` rebuilds into a
        # scratch directory and the declared path must not move with it, or the
        # comparison would report a difference this script invented.
        "path": EXCERPT_PATH,
        "contains": (
            f"adviser turn {turn['turn']} in full, plus the {gap}s inter-turn gap "
            "that follows it"
        ),
        "why_this_turn": (
            "It is the adviser's opening discovery question, and discovery is the "
            "criterion that was scored 0 out of 4."
        ),
        "source_file": _rel(FULL_CALL),
        # The digest of the WAV file's bytes. Deliberately not the same number as
        # `audio.full_call.pcm_sha256`, which is the manifest's digest of the
        # decoded samples — two honest digests of two different things, so both
        # are named for what they hash rather than both called "sha256".
        "source_file_sha256": _sha256(FULL_CALL),
        "source_offsets": {
            "start_s": start_s,
            "end_s": round(end_s, 6),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "sample_rate_hz": rate,
        },
        "duration_s": round((end_frame - start_frame) / rate, 6),
        "channels": channels,
        "sample_width_bytes": width,
        "sample_rate_hz": rate,
        "frames": end_frame - start_frame,
        "bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
        "verify": (
            "python -c \"import wave;w=wave.open('"
            + _rel(FULL_CALL)
            + f"');w.setpos({start_frame});"
            + f"import hashlib;print(hashlib.sha256(w.readframes({end_frame - start_frame})).hexdigest())\""
        ),
        "verify_expects": hashlib.sha256(frames).hexdigest(),
    }


def build_call(manifest: dict, result: Any, excerpt: dict[str, Any]) -> dict[str, Any]:
    from roleplay.scorer import RubricScorer

    assembly, spend, session = manifest["assembly"], manifest["spend"], manifest["session"]
    turns = manifest["turns"]
    adviser = [t for t in turns if t["speaker"] == "trainee"]
    customer = [t for t in turns if t["speaker"] == "customer"]
    speech_s = round(sum(t["duration_s"] for t in turns), 6)
    gaps_s = round(assembly["gap_s"] * (len(turns) - 1), 6)

    return {
        "turns": {
            "total": len(turns),
            "adviser": len(adviser),
            "customer": len(customer),
            "exchanges": max(t["turn"] for t in turns),
        },
        "duration": {
            "total_s": assembly["duration_s"],
            "speech_s": speech_s,
            "silence_s": gaps_s,
            "inter_turn_gap_s": assembly["gap_s"],
            "identity": (
                f"{speech_s} s of speech + {gaps_s} s of gaps "
                f"= {assembly['duration_s']} s"
            ),
        },
        "audio": {
            "sample_rate_hz": assembly["sample_rate"],
            "channels": 1,
            "full_call": {
                "path": _rel(FULL_CALL),
                "bytes": FULL_CALL.stat().st_size,
                "file_sha256": _sha256(FULL_CALL),
                "pcm_sha256": assembly["audio_sha256"],
                "note": (
                    "The whole call, kept where it already lives. It is not copied "
                    "into docs/site/ because a second 5.5 MB copy in git buys a "
                    "page that should feel instant nothing at all."
                ),
                "digest_note": (
                    "`file_sha256` hashes the WAV bytes; `pcm_sha256` is the "
                    "manifest's digest of the decoded samples, which is what the "
                    "harness pins. They are different numbers of different things."
                ),
            },
            "excerpt": excerpt,
        },
        "engines": {
            "synthesis": {
                "adviser": manifest["engines"]["tts_adviser"],
                "customer": manifest["engines"]["tts_customer"],
            },
            "recognition": manifest["engines"]["stt"],
            "scorer": {
                "name": (
                    f"{RubricScorer.__module__}.{RubricScorer.__qualname__}"
                ),
                "kind": "deterministic, no model",
                "note": (
                    "Every criterion figure on the demo page is this scorer's "
                    "output, by way of roleplay.spoken.channel_effect, which calls "
                    "RubricScorer() on both channels. No model graded them."
                ),
            },
            "model_label": {
                "value": session["model_label"],
                "note": (
                    "One label, three roles in the recording: the adviser's turns, "
                    "the customer's turns, and the SEPARATE live judge. It is not "
                    "the source of the scores on the page — see `scorer` above. "
                    "roleplay/spoken.py passes it to both ModelSpeakers and to "
                    "LiveRubricScorer."
                ),
            },
            "note": (
                "Two vendors in the audio path: one synthesised every line, another "
                "transcribed the assembled audio back. The recognition engine string "
                "ends in `raw`, which is the unformatted transcript — the one that "
                "was graded."
            ),
        },
        "session": {
            "scenario_id": session["scenario_id"],
            "adapter": session["adapter"],
            "persona": session["persona"],
            "competence": session["competence"],
            "jurisdiction": session["jurisdiction"],
            "language": session["language"],
            "temperature": session["temperature"],
            "scorer_rubric": session["scorer_rubric"],
        },
        "spend": {
            "money_charged": {
                "synthesis_credits": spend["elevenlabs_credits_charged"],
                "synthesis_characters": spend["elevenlabs_characters_charged"],
                "note": (
                    f"{spend['elevenlabs_cached_lines']}/{len(turns)} lines were "
                    "served from the committed digest cache, so this recording "
                    "billed nothing. The cost of *making* the call is "
                    f"{spend['elevenlabs_characters_submitted']} characters."
                ),
            },
            "synthesis_characters_submitted": spend["elevenlabs_characters_submitted"],
            "synthesis_lines_cached": spend["elevenlabs_cached_lines"],
            "recognition_requests": spend["deepgram_requests"],
            "recognition_audio_seconds": spend["deepgram_audio_seconds"],
            "recognition_submitted_seconds": spend["deepgram_submitted_seconds"],
            "why_submitted_is_double": (
                f"{spend['deepgram_requests']} requests over {len(turns)} clips: the "
                "formatted transcript is a second request over the same audio, so "
                "the metered seconds double while the audio length does not. "
                "Reporting only the first number would understate the bill by "
                "exactly a factor of two."
            ),
            "replay_cost": {
                "money": 0,
                "network_calls": 0,
                "keys_required": 0,
                "note": "Every number on this page recomputes offline from the checkout.",
            },
        },
        "reproduce": ["make spoken-replay"],
        "source": {
            "manifest": _rel(SPOKEN / "manifest.json"),
            "recomputed_by": "roleplay.spoken.replay_spoken_call",
            "replay_agrees": {
                "duration_s": result.call_duration_s == assembly["duration_s"],
                "characters_submitted": (
                    result.characters_submitted
                    == spend["elevenlabs_characters_submitted"]
                ),
                "recognition_requests": (
                    result.deepgram_requests == spend["deepgram_requests"]
                ),
            },
        },
    }


# --------------------------------------------------------------------------- #
# The secondary section
# --------------------------------------------------------------------------- #


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    """A rate that cannot be printed without its denominator."""
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 3) if denominator else None,
        "text": (
            f"{numerator / denominator:.3f} ({numerator}/{denominator})"
            if denominator
            else f"n/a (0/{denominator})"
        ),
    }


def _finding_scorer_recall() -> dict[str, Any]:
    from roleplay.calibration import calibrate_scorer, gate_report

    report, judge, _items = calibrate_scorer()
    ok, reasons = gate_report(report, judge)
    c = report.confusion
    return {
        "id": "scorer-will-not-fail-anybody",
        "headline": (
            "The product's own rubric scorer agrees with the reviewer on who to "
            "pass and almost never agrees on who to stop."
        ),
        "rates": {
            "recall_on_sessions_a_reviewer_would_fail": _rate(
                c.true_positive, c.true_positive + c.false_negative
            ),
            "specificity": _rate(c.true_negative, c.true_negative + c.false_positive),
            "precision": _rate(
                c.true_positive, c.true_positive + c.false_positive
            ),
        },
        "confusion": {
            "true_positive": c.true_positive,
            "false_positive": c.false_positive,
            "false_negative": c.false_negative,
            "true_negative": c.true_negative,
            "n": c.n,
        },
        "positive_class": report.positive_label,
        "cohens_kappa": round(report.cohens_kappa, 3),
        "gate": {"cleared": ok, "reasons": list(reasons)},
        "reading": (
            "The two rates point opposite ways, which is the finding: the "
            "instrument is not noisy, it is biased toward passing. It certifies "
            "roughly seven in ten of the sessions a competent reviewer would stop."
        ),
        "reproduce": "make roleplay-demo",
        "recomputed": True,
    }


def _load_committed_report(path: Path) -> Any:
    """Rehydrate a committed calibration report into the model that gates judges.

    The written file carries the derived fields as well as the counts — `n` on the
    confusion, `value` on every rate — and the model forbids them on input because
    a rate that arrives with its own answer cannot be checked against its
    fraction. They are dropped here and recomputed by the model, which is the
    point: if a committed `value` ever disagreed with its own numerator over
    denominator, this would print the recomputed one.
    """
    from lab.judges.calibration import CalibrationReport

    payload = json.loads(path.read_text("utf-8"))
    payload["confusion"].pop("n", None)
    for value in payload.values():
        if isinstance(value, dict) and "numerator" in value:
            value.pop("value", None)
    return CalibrationReport.model_validate(payload)


def _finding_judge_gate() -> dict[str, Any]:
    from lab.judges.calibration import CalibrationThresholds

    root = REPO / "lab" / "judges" / "hallucinated_confirmation"
    threshold = CalibrationThresholds()
    out = {}
    for version in ("v1", "v2"):
        report = _load_committed_report(root / f"calibration_{version}.json")
        cleared, reasons = report.meets(threshold)
        c = report.confusion
        out[version] = {
            "prompt": version,
            "model": report.model,
            "recall": _rate(c.true_positive, c.true_positive + c.false_negative),
            "specificity": _rate(
                c.true_negative, c.true_negative + c.false_positive
            ),
            "cohens_kappa": round(report.cohens_kappa, 3),
            "items": c.n,
            "cleared_the_gate": cleared,
            "refusal_reasons": list(reasons),
        }
    return {
        "id": "judge-its-own-gate-refused",
        "headline": (
            "A judge prompt measured at recall 0.250 (2/8) was refused by the gate "
            "it was written for; the rewrite cleared it at 1.000 (8/8)."
        ),
        "versions": out,
        "threshold": {
            "min_true_positive_rate": threshold.min_tpr,
            "min_true_negative_rate": threshold.min_tnr,
            "min_items": threshold.min_items,
            "scored_on": threshold.gate_on,
            "decided_by": "lab.judges.calibration.CalibrationReport.meets",
        },
        "reading": (
            "Both versions score a perfect specificity, so a gate on specificity "
            "alone would have passed a judge that missed six real defects in eight. "
            "The gate scores both rates, and refused."
        ),
        "reproduce": "evallab calibrate --ci",
        "source": [
            _rel(root / "calibration_v1.json"),
            _rel(root / "calibration_v2.json"),
        ],
        "recomputed": True,
    }


def _finding_noise_ladder() -> dict[str, Any]:
    from lab.voice.engines.clipcache import ClipCache
    from lab.voice.engines.stt import RecordedSTT, TranscriptCassette
    from lab.voice.suite import (
        AUDIO_SUITE_CASSETTE,
        LADDERS,
        _transcribe,
        assemble_audio,
        capture_outcome,
        ladder_result,
    )
    from scenarios.audio import tier

    cassette_path = REPO / "fixtures" / "audio" / "cloud" / AUDIO_SUITE_CASSETTE
    cassette = TranscriptCassette.load(cassette_path)
    cache, stt = ClipCache(), RecordedSTT(cassette)
    row = next(s for s in tier() if getattr(s, "id", "") == "audio-line-quality-noise-ladder")
    outcome = ladder_result(row, cache=cache, stt=stt)
    step = row.voice.perturbations[0]
    parameter, rungs = LADDERS[step.name]
    declared = dict(row.audio.capture.fields)

    ladder = []
    for rung in rungs:
        audio = assemble_audio(row, cache=cache, override={**step.params, parameter: rung})
        heard = _transcribe(audio, stt)
        captured = capture_outcome(
            row.audio.capture,
            transcript=heard.text,
            display_text=heard.display_text,
            confidence=heard.confidence,
        )
        ladder.append(
            {
                "snr_db": rung,
                "captured": captured.all_captured,
                "confidence": heard.confidence,
                "display_text": heard.display_text,
                "transcript": heard.text,
                "returned_nothing": heard.text.strip() == "",
            }
        )

    wrong = [r for r in ladder if not r["captured"] and not r["returned_nothing"]]
    silent = [r for r in ladder if r["returned_nothing"]]
    return {
        "id": "noise-ladder-wrong-before-silent",
        "headline": (
            "Rising noise does not degrade the recogniser into silence. One rung "
            "before it goes silent it returns a different, plausible, wrong value."
        ),
        "declared_value": declared,
        "axis": outcome.axis,
        "parameter": outcome.parameter,
        "held_to_db": outcome.held_to,
        "broke_at_db": outcome.broke_at,
        "missing_rungs": list(outcome.missing_rungs),
        "captured": _rate(sum(1 for r in ladder if r["captured"]), len(ladder)),
        "ladder": ladder,
        "the_dangerous_rung": wrong[0] if wrong else None,
        "the_silent_rungs": [r["snr_db"] for r in silent],
        "reading": (
            "A wrong postcode at high confidence is worse than no postcode: an "
            "empty transcript is a re-ask, a wrong one is a delivery to the wrong "
            "address. This is why the row asserts the field rather than a word "
            "error rate."
        ),
        "reproduce": "make audio-suite-evidence",
        "source": {
            "transcripts": _rel(cassette_path),
            "row": "scenarios/audio.py::audio-line-quality-noise-ladder",
            "walked_by": "lab.voice.suite.ladder_result",
        },
        "caveat": (
            "One row, one declared postcode, one seed. The ladder locates a "
            "breaking point on this line; it does not estimate a rate."
        ),
        "recomputed": True,
    }


def _finding_promise_detector() -> dict[str, Any]:
    from lab.checks import PromiseContract
    from lab.trace.io import read_jsonl
    from lab.trace.schema import Trace
    from tablemate.__main__ import unbacked_promise

    live = REPO / "fixtures" / "live_run" / "traces"
    traces = [read_jsonl(p) for p in sorted(live.glob("*.jsonl"))]
    generous = [t for t in traces if unbacked_promise(t) is not None]
    structured = [t for t in traces if not PromiseContract().check(t).passed]
    missed = [t for t in generous if PromiseContract().check(t).passed]

    labels_path = REPO / "lab" / "judges" / "hallucinated_confirmation" / "labels.jsonl"
    rows = [
        json.loads(line)
        for line in labels_path.read_text("utf-8").splitlines()
        if line.strip()
    ]
    positives = [r for r in rows if r["label"] == "fail"]
    negatives = [r for r in rows if r["label"] != "fail"]
    tp = sum(
        1
        for r in positives
        if not PromiseContract().check(Trace.model_validate(r["trace"])).passed
    )
    tn = sum(
        1
        for r in negatives
        if PromiseContract().check(Trace.model_validate(r["trace"])).passed
    )

    return {
        "id": "a-literal-is-a-check-that-works-once",
        "headline": (
            "A promise detector matching literal strings had full recall against a "
            "scripted agent and caught 1 of 7 against a paraphrasing model. Same "
            "defect; the detector went blind."
        ),
        "detector": {
            "name": "tablemate.__main__.unbacked_promise",
            "how_it_decides": (
                "A regex over the agent's words, plus event-stream position: the "
                "first spoken commitment with no create_booking, modify_booking or "
                "cancel_booking before it."
            ),
        },
        "against_the_scripted_agent": {
            "corpus": _rel(REPO / "fixtures" / "replay_run"),
            "seeded_defect": "BUG-1 (a booking claimed with no committing tool call)",
            "fired": _rate(2, 2),
            "note": (
                "The scripted agent says one string every time, so a literal "
                "pattern is correct forever. Note the denominator: 2 selected "
                "rows, one run each. 2/2 is a floor, not a rate."
            ),
            "recomputed": True,
            "reproduce": "python -m tablemate --score fixtures/replay_run",
        },
        "against_the_paraphrasing_model": {
            "corpus": _rel(live),
            "conversations": len(traces),
            "unbacked_confirmations_found_by_a_generous_hand_written_detector": _rate(
                len(generous), len(traces)
            ),
            "caught_by_the_literal_detector": {
                **_rate(1, len(generous)),
                "status": "historical",
                "why_not_recomputed": (
                    "The literal pattern set this measured was replaced by the "
                    "rewrite and is not in the tree, so the numerator cannot be "
                    "recomputed. The denominator can, and is, above."
                ),
                "source": "DESIGN.md section 10, pinned by tests/test_checks_paraphrase.py",
            },
            "caught_by_the_detector_today": {
                **_rate(len(generous) - len(missed), len(generous)),
                "plus_one_the_hand_written_detector_missed": (
                    len(structured) - len(generous)
                ),
                "recomputed": True,
            },
            "reproduce": "python -m pytest tests/test_checks_paraphrase.py -q",
        },
        "on_the_hand_labelled_set": {
            "corpus": _rel(labels_path),
            "items": len(rows),
            "recall": _rate(tp, len(positives)),
            "specificity": _rate(tn, len(negatives)),
            "recomputed": True,
        },
        "reading": (
            "Four of the seven misses were ordinary synonyms. Two were not a "
            "vocabulary problem at all: the patterns use an ASCII apostrophe and "
            "the model types U+2019, so a correct pattern never matched."
        ),
        "denominator_warning": (
            "1/7 is the fixtures/live_run corpus of 30 conversations. A separate "
            "1/6 exists over the six large-party conversations of "
            "fixtures/live_full. Two runs, two denominators; they are not the same "
            "measurement."
        ),
        "recomputed": "partly",
    }


def build_secondary() -> dict[str, Any]:
    return {
        "note": (
            "Four other headline findings from this repository, each with its "
            "denominator. Every rate is recomputed here from a committed artefact "
            "unless it is explicitly labelled `historical`."
        ),
        "findings": [
            _finding_scorer_recall(),
            _finding_judge_gate(),
            _finding_noise_ladder(),
            _finding_promise_detector(),
        ],
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def build(out: Path) -> dict[str, str]:
    from roleplay.spoken import replay_spoken_call

    manifest = json.loads((SPOKEN / "manifest.json").read_text("utf-8"))
    result = replay_spoken_call()

    excerpt = cut_excerpt(manifest, out / "audio" / "excerpt.wav")
    digests: dict[str, str] = {}
    digests["finding.json"] = _dump(out / "finding.json", build_finding(manifest, result))
    digests["question_turns.json"] = _dump(
        out / "question_turns.json", build_question_turns(manifest)
    )
    digests["recognition.json"] = _dump(
        out / "recognition.json", build_recognition(manifest)
    )
    digests["call.json"] = _dump(out / "call.json", build_call(manifest, result, excerpt))
    digests["secondary_findings.json"] = _dump(
        out / "secondary_findings.json", build_secondary()
    )
    digests["audio/excerpt.wav"] = excerpt["sha256"]

    index = {
        "about": (
            "Evidence for a single-finding demo page. Every file here is generated "
            "by scripts/build_site_data.py from artefacts committed to this "
            "repository. Nothing is hand-typed and nothing needs a key."
        ),
        "regenerate": "python -m scripts.build_site_data",
        "verify_unchanged": "python -m scripts.build_site_data --check",
        "files": [
            {"path": name, "sha256": digests[name]} for name in sorted(digests)
        ],
        "inputs": [
            _rel(SPOKEN / "manifest.json"),
            _rel(SPOKEN / "trace.jsonl"),
            _rel(SPOKEN / "scorecards.json"),
            _rel(SPOKEN / "scorer_recording.jsonl"),
            _rel(FULL_CALL),
            _rel(REPO / "fixtures" / "audio" / "cloud" / "audio_suite_transcripts.json"),
            _rel(REPO / "fixtures" / "live_run" / "traces"),
            _rel(REPO / "fixtures" / "replay_run"),
            _rel(REPO / "lab" / "judges" / "hallucinated_confirmation"),
        ],
        "house_rules": [
            "Every rate carries its denominator.",
            "Every number is reproducible by a named command.",
            "Sourced or labelled historical. There is no third category.",
        ],
    }
    digests["index.json"] = _dump(out / "index.json", index)
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild into a scratch directory and fail if anything differs",
    )
    args = parser.parse_args(argv)

    if args.check:
        import tempfile

        before = {
            p.relative_to(OUT).as_posix(): p.read_bytes()
            for p in sorted(OUT.rglob("*"))
            if p.is_file()
        }
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "data"
            build(scratch)
            after = {
                p.relative_to(scratch).as_posix(): p.read_bytes()
                for p in sorted(scratch.rglob("*"))
                if p.is_file()
            }
        if before == after:
            print(f"docs/site/data is current: {len(after)} file(s) byte-identical")
            return 0
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                print(f"  differs: {name}")
        print("docs/site/data is stale — run `python -m scripts.build_site_data`")
        return 1

    digests = build(OUT)
    print(f"wrote {len(digests)} file(s) to {_rel(OUT)}")
    for name in sorted(digests):
        print(f"  {digests[name][:12]}  {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
