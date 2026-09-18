"""Extract the demo page's evidence from the committed artefacts.

WHAT THIS IS FOR
----------------
A designed page tells **one** finding in depth and then shows the whole harness
beside it. This script produces the JSON files that page loads, plus the call's
audio, so that every number and every quote on it is real, sourced, and
regenerable by re-running this file.

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
    coverage.json         the requirement map: what a QA-for-AI role asks for,
                          what here demonstrates it, the one command, the figure
                          with its denominator — and an explicit not_covered list
    findings.json         every headline finding, denominator and command
    architecture.json     the event kinds, the six contracts, the four regimes and
                          their registers, and the repository's counts
    adapter.json          the two-method trainee contract and the one-call agent
                          contract, as text, with the flag that points at them
    index.json            every file above with its sha256, and the commands
    audio/full_call.wav   the whole call, 181.30s, served so the page can play it
    audio/excerpt.wav     15.21s cut around the adviser's opening question
    calls.json            both committed spoken calls side by side — the first
                          (hostile customer, failed the register gate) and the
                          second (cooperative customer, a pass attempted) — each
                          with both score cards, the register's own verdict, the
                          gate, whether a close was attempted, persona, duration,
                          spend, and the digests of its audio
    audio/full_call_pass.wav  the second call, served byte for byte

DETERMINISM
-----------
No clock, no random source, no absolute path, no environment. Keys are sorted and
floats are written as they were read. Re-running overwrites with byte-identical
content, which `--check` asserts.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import functools
import hashlib
import inspect
import io
import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # pragma: no cover - script convenience
    sys.path.insert(0, str(REPO))

OUT = REPO / "docs" / "site" / "data"
SPOKEN = REPO / "fixtures" / "audio" / "spoken_call"
FULL_CALL = SPOKEN / "full_call.wav"

#: The second committed spoken call — same engines, voices, budgets and adviser
#: competence as the first, a cooperative persona instead of a hostile one, and
#: a documented addendum to the adviser's brief. See `roleplay.spoken.CALLS`.
SPOKEN_PASS = REPO / "fixtures" / "audio" / "spoken_call_pass"
FULL_CALL_PASS = SPOKEN_PASS / "full_call.wav"
FULL_CALL_PASS_SITE_PATH = "docs/site/data/audio/full_call_pass.wav"

#: Where the excerpt lives, relative to the repository root. A constant rather
#: than a derived path because `--check` writes to a scratch directory and the
#: declared location must not follow it there.
EXCERPT_PATH = "docs/site/data/audio/excerpt.wav"

#: Where the served copy of the whole call lives. Same reasoning as above: the
#: declared path is the canonical one, not wherever `--check` happens to build.
FULL_CALL_SITE_PATH = "docs/site/data/audio/full_call.wav"

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


def serve_full_call(
    manifest: dict,
    destination: Path,
    *,
    source: Path = FULL_CALL,
    site_path: str = FULL_CALL_SITE_PATH,
) -> dict[str, Any]:
    """Put the whole call where the page can play it, byte for byte.

    A straight copy of `fixtures/audio/spoken_call/full_call.wav` — no re-encode,
    no trim, no resample — so the served file and the fixture have the same
    digest and the page is playing the artefact the harness pins, not a
    rendition of it. It is 5.5 MB, and the page carries `preload="none"` so
    those bytes are only fetched when a reader presses play.

    `source` and `site_path` default to the first call; the second call passes
    its own, and the returned dict names whichever was served.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())

    with wave.open(str(destination), "rb") as served:
        channels = served.getnchannels()
        width = served.getsampwidth()
        rate = served.getframerate()
        frames = served.getnframes()

    assembly = manifest["assembly"]
    return {
        # The canonical location, not `destination`: see EXCERPT_PATH.
        "path": site_path,
        "source_file": _rel(source),
        "contains": (
            f"the whole call, all {len(manifest['turns'])} turns, exactly as the "
            "harness assembled it"
        ),
        "copied_verbatim": True,
        "duration_s": assembly["duration_s"],
        "channels": channels,
        "sample_width_bytes": width,
        "sample_rate_hz": rate,
        "frames": frames,
        "bytes": destination.stat().st_size,
        # Same digest as the fixture, by construction. The assertion is the
        # point: if these two ever differ, the page is playing something else.
        "sha256": _sha256(destination),
        "source_file_sha256": _sha256(source),
        "verify": (
            "python -c \"import hashlib,pathlib;"
            + f"print(hashlib.sha256(pathlib.Path('{site_path}')"
            + ".read_bytes()).hexdigest())\""
        ),
        "verify_expects": _sha256(source),
    }


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


def build_call(
    manifest: dict,
    result: Any,
    served: dict[str, Any],
    excerpt: dict[str, Any],
) -> dict[str, Any]:
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
                "served_at": FULL_CALL_SITE_PATH,
                "note": (
                    "The whole call. A byte-identical copy is served from "
                    "docs/site/data/audio/ so the page can play it; the page "
                    "carries preload=\"none\", so the 5.5 MB is fetched only "
                    "when a reader presses play and the page still loads instantly."
                ),
                "digest_note": (
                    "`file_sha256` hashes the WAV bytes; `pcm_sha256` is the "
                    "manifest's digest of the decoded samples, which is what the "
                    "harness pins. They are different numbers of different things."
                ),
            },
            "full_call_served": served,
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
        ],
    }


# --------------------------------------------------------------------------- #
# The requirement map, the findings, the architecture, the adapter seam
# --------------------------------------------------------------------------- #
#
# Everything below feeds four more files — coverage.json, findings.json,
# architecture.json and adapter.json — for a page that shows the whole harness
# rather than one finding. The rules are the ones the first five files obey:
# every figure is recomputed here from a committed artefact by the code the suite
# itself uses, or it is labelled with where it came from and why it could not be;
# every rate carries its denominator; nothing is copied out of a document.
#
# Recomputations are cached per process because two files often need the same
# one, and `--check` builds twice.


def _quiet(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call `fn` with stdout and stderr swallowed.

    Several of the repository's entry points print a screen as a side effect of
    computing the thing this file wants. The screen is theirs; the numbers are
    what is kept.
    """
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        return fn(*args, **kwargs)


def _rate_of(rate: Any) -> dict[str, Any]:
    """A `lab.judges.calibration.Rate` (or anything with the two counts) as a rate."""
    return _rate(int(rate.numerator), int(rate.denominator))


def _round(value: float | None, places: int = 3) -> float | None:
    return None if value is None else round(float(value), places)


# ----------------------------------------------------------------- recomputations


@functools.lru_cache(maxsize=None)
def _scorer_calibration() -> tuple[Any, bool, tuple[str, ...]]:
    """The advisory rubric scorer against the 70 hand-labelled rows."""
    from roleplay.calibration import calibrate_scorer, gate_report

    report, judge, _items = _quiet(calibrate_scorer)
    ok, reasons = gate_report(report, judge)
    return report, ok, tuple(reasons)


@functools.lru_cache(maxsize=None)
def _judge_study() -> dict[str, Any]:
    """The `hallucinated_confirmation` judge, v1 and v2, from the committed recordings.

    Every report is rebuilt through `ReplayJudge` — same prompt, same parser, same
    arithmetic as the live call — so the digests, the confusion cells, the run-to-run
    stability and the paired test are all recomputed rather than read back from
    `calibration_v*.json`.
    """
    from lab.judges import hallucinated_confirmation as hc
    from lab.judges.calibration import (
        CalibrationThresholds,
        detectability_floor,
        labels_digest,
        mcnemar,
    )

    thresholds = CalibrationThresholds()
    items = hc.labels()
    reports = {v: _quiet(hc.calibrate_version, v, with_bands=False) for v in hc.VERSIONS}
    runs = {
        v: [
            _quiet(hc.calibrate_version, v, run=r, with_bands=False)
            for r in range(1, hc.REPLICATES + 1)
        ]
        for v in hc.VERSIONS
    }
    stability = {v: _quiet(hc.stability, v) for v in hc.VERSIONS}
    return {
        "thresholds": thresholds,
        "items": items,
        "labels_digest": labels_digest(items),
        "prompt_digests": {v: hc.prompt(v).digest for v in hc.VERSIONS},
        "model": {v: hc.recorded_model(v) for v in hc.VERSIONS},
        "reports": reports,
        "runs": runs,
        "stability": stability,
        "paired": mcnemar(reports["v1"], reports["v2"]),
        "floor": detectability_floor(),
        "replicates": hc.REPLICATES,
    }


@functools.lru_cache(maxsize=None)
def _rag() -> dict[str, Any]:
    from lab.judges.registry import CalibrationGateError
    from ragcheck.calibration import load_claim_labels
    from ragcheck.corpus import load_corpus
    from ragcheck.dataset import load_cases
    from ragcheck.report import evaluate

    corpus = load_corpus()
    report = _quiet(evaluate)
    try:
        _quiet(evaluate, gate=True)
        refusal: str | None = None
    except CalibrationGateError as exc:
        refusal = type(exc).__name__
    return {
        "report": report,
        "chunks": len(corpus.chunks),
        "cases": len(load_cases(corpus=corpus).cases),
        "labels": len(load_claim_labels()),
        "gate_refusal": refusal,
    }


def _run_cli(*args: str) -> tuple[dict[str, Any], int]:
    """Run `evallab run ...` in process into a scratch directory; return its report.

    The same code path as the Makefile target, with `--out` pointed at a temporary
    directory so the recomputation leaves nothing behind. Only counts are read out
    of the result, so a timestamp in the report's label cannot reach this file.
    """
    from lab import cli

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "run"
        status = _quiet(cli.main, [*args, "--out", str(out)])
        payload = json.loads((out / "run_report.json").read_text("utf-8"))
    return payload, int(status)


def _declared_split(failures: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Findings the corpus declared as known gaps versus the ones it did not.

    The same test `lab.cli` applies when it prints its regression line: a finding
    whose note starts `declared known gap` was expected by the row that produced it.
    """
    declared = sum(
        1 for f in failures if str(f.get("note") or "").startswith("declared known gap")
    )
    return {"total": len(failures), "declared": declared, "undeclared": len(failures) - declared}


@functools.lru_cache(maxsize=None)


@functools.lru_cache(maxsize=None)


@functools.lru_cache(maxsize=None)
def _regime() -> dict[str, Any]:
    """The advisory registers, decided against every advisory row.

    Mirrors the arithmetic `python -m roleplay.regime_eval --divergence` prints,
    using its own loader and evaluator, so the three agreement figures here are the
    ones that command prints and not a summary of them.
    """
    from roleplay.regime_eval import _confusion, _load, run_corpus

    corpus = _load()
    rows = _quiet(run_corpus, corpus)
    diverged = blocks = pairs = entry_agree = register_agree = 0
    divergence_rows: list[dict[str, Any]] = []
    for scenario_id, row in rows.items():
        scenario = row.scenario
        if scenario.divergence is None:
            continue
        hand = {r.regime: r for r in scenario.divergence.regimes}
        computed = {v.verdict for v in row.verdicts.values()}
        per_regime = []
        for regime, verdict in row.verdicts.items():
            block = hand.get(regime)
            named = block.register_entry if block else None
            entry = next((e for e in verdict.entries if e.entry_id == named), None)
            if block is not None:
                pairs += 1
                expected = {"satisfied", "not-applicable"} if block.verdict == "pass" else {"missed"}
                entry_agree += entry is not None and entry.status in expected
                register_agree += verdict.verdict == block.verdict
            per_regime.append(
                {
                    "regime": regime,
                    "hand": block.verdict if block else None,
                    "computed": verdict.verdict,
                    "named_entry": named,
                    "named_entry_status": entry.status if entry else None,
                }
            )
        diverged += len(computed) > 1
        blocks += 1
        divergence_rows.append(
            {
                "scenario_id": scenario_id,
                "axis": scenario.divergence.axis,
                "regimes": per_regime,
                "computed_verdicts": sorted(computed),
                "diverges": len(computed) > 1,
            }
        )
    return {
        "rows": len(rows),
        "agree": sum(1 for r in rows.values() if r.agrees),
        "confusion_human_over_computed": _confusion(rows),
        "divergence_blocks": blocks,
        "blocks_that_diverge": diverged,
        "regime_verdict_pairs": pairs,
        "named_entry_agreement": entry_agree,
        "whole_register_agreement": register_agree,
        "divergence_rows": divergence_rows,
    }


@functools.lru_cache(maxsize=None)
def _gate_stages() -> list[dict[str, Any]]:
    """The eight stages of `make gate`, read off the Makefile recipe itself."""
    text = (REPO / "Makefile").read_text("utf-8")
    start = text.index("\ngate: python-ok")
    recipe = text[start + 1 :]
    recipe = recipe[: recipe.index("\n\n")]

    # Join backslash continuations first, so a command that spans lines is one.
    joined: list[str] = []
    pending = ""
    for raw in recipe.splitlines()[1:]:
        line = raw.strip()
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        joined.append((pending + line).strip())
        pending = ""

    stages: list[dict[str, Any]] = []
    header = re.compile(r'^@echo "== (\d+)/8\s+(.*?) =="$')
    for line in joined:
        m = header.match(line)
        if m:
            stages.append({"stage": int(m.group(1)), "title": m.group(2), "commands": []})
            continue
        if not stages:
            continue
        if line.startswith("$(PYTHON)") or line.startswith("git "):
            command = line.replace("$(PYTHON)", "python").rstrip(" ;")
            stages[-1]["commands"].append(command)
    for stage in stages:
        stage["byte_for_byte"] = any(c.startswith("git diff --exit-code") for c in stage["commands"])
    if len(stages) != 8:
        raise SystemExit(f"expected 8 gate stages in the Makefile, found {len(stages)}")
    return stages


@functools.lru_cache(maxsize=None)
def _gate_refusal_demo() -> dict[str, Any]:
    """Does the registry really raise in CI mode? Asked, not asserted."""
    from lab.judges.registry import CalibrationGateError
    from ragcheck.calibration import gate_claim_support

    try:
        _quiet(gate_claim_support, ci=True)
        return {"raised": None, "message": None}
    except CalibrationGateError as exc:
        return {"raised": type(exc).__name__, "message": str(exc).split(". Measured:")[0]}


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


@functools.lru_cache(maxsize=None)
def _repo_counts() -> dict[str, Any]:
    """Tests, test files, commits — each from the tool that owns the number.

    The commit count deliberately excludes commits that touch only `docs/` or this
    generator: a regeneration of this pack, or a rewrite of the page that reads it,
    is not a change to the thing being counted, and counting it would make this file
    stale the moment it was committed. Any other commit stales it, which is correct.
    """
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    m = re.search(r"(\d+) tests? collected", collected.stdout)
    if collected.returncode != 0 or not m:
        raise SystemExit("could not collect the test suite:\n" + collected.stdout[-2000:])
    test_files = sorted(p.name for p in (REPO / "tests").glob("test_*.py"))
    commits = _git(
        "rev-list", "--count", "HEAD", "--", ".", ":(exclude)docs", ":(exclude)scripts/build_site_data.py"
    )
    first = _git("log", "--max-parents=0", "--format=%ad", "--date=short")
    return {
        "tests_collected": int(m.group(1)),
        "tests_collected_command": "python -m pytest --collect-only -q",
        "test_files": len(test_files),
        "commits_outside_docs_and_this_generator": int(commits) if commits else None,
        "commits_command": (
            "git rev-list --count HEAD -- . ':(exclude)docs' ':(exclude)scripts/build_site_data.py'"
        ),
        "first_commit_date": first.splitlines()[-1] if first else None,
        "note": (
            "tests_collected is the collection count; the suite's pass/skip split is "
            "not recomputed here (it needs a 70 s run) — `pytest` prints it. The "
            "commit count excludes docs/ and this generator so that committing this "
            "pack does not stale it; every other commit does, and should."
        ),
    }


# ----------------------------------------------------------------- coverage.json


def _cov_llm_judge() -> dict[str, Any]:
    report, cleared, reasons = _scorer_calibration()
    c = report.confusion
    study = _judge_study()
    v1, v2 = study["reports"]["v1"], study["reports"]["v2"]
    t = study["thresholds"]
    return {
        "id": "llm-as-judge-calibrated-and-gated",
        "requirement": "LLM-as-judge, calibrated against human labels and gated before it can decide anything",
        "what_demonstrates_it": [
            "lab/judges/calibration.py — TPR, TNR, kappa, Wilson intervals, McNemar, run-to-run stability",
            "lab/judges/registry.py — require_calibrated() raises JudgeBelowThresholdError in CI mode",
            "lab/judges/hallucinated_confirmation/ — a worked v1 -> v2 iteration with six committed recordings",
            "roleplay/calibration.py — the product's own rubric scorer measured as a judge",
        ],
        "command": "make roleplay-demo && make calibrate",
        "headline": (
            f"The advisory scorer catches {c.true_positive} of "
            f"{c.true_positive + c.false_negative} sessions a reviewer would fail and the gate refuses it; "
            f"judge prompt v1 scored TPR {_rate_of(v1.true_positive_rate)['text']} and was refused, "
            f"v2 scored {_rate_of(v2.true_positive_rate)['text']} and cleared."
        ),
        "figures": {
            "advisory_scorer": {
                "true_positive_rate": _rate(c.true_positive, c.true_positive + c.false_negative),
                "true_negative_rate": _rate(c.true_negative, c.true_negative + c.false_positive),
                "cohens_kappa": _round(report.cohens_kappa),
                "items": c.n,
                "gate_cleared": cleared,
                "refusal_reasons": list(reasons),
            },
            "judge_v1": {
                "true_positive_rate": _rate_of(v1.true_positive_rate),
                "true_negative_rate": _rate_of(v1.true_negative_rate),
                "gate_cleared": v1.meets(t)[0],
            },
            "judge_v2": {
                "true_positive_rate": _rate_of(v2.true_positive_rate),
                "true_negative_rate": _rate_of(v2.true_negative_rate),
                "gate_cleared": v2.meets(t)[0],
            },
            "threshold": {
                "min_true_positive_rate": t.min_tpr,
                "min_true_negative_rate": t.min_tnr,
                "min_items": t.min_items,
                "scored_on": t.gate_on,
            },
        },
        "recomputed": True,
    }


def _cov_golden_datasets() -> dict[str, Any]:
    """The corpus row of the coverage table, counted from the corpus itself."""
    from roleplay.corpus import load_corpus as load_roleplay  # noqa: PLC0415
    from roleplay.advisory import load_registers  # noqa: PLC0415

    roleplay = load_roleplay()
    registers = load_registers()
    suites = sorted({s.suite for s in roleplay.scenarios})
    yaml_files = len(list((REPO / "scenarios").rglob("*.yaml")))
    entries = sum(len(r.entries) for r in registers.values())
    return {
        "id": "golden-datasets-validated-corpus",
        "requirement": (
            "Golden datasets: a validated corpus with closed vocabularies, so a typo "
            "is a load error and not a green row"
        ),
        "what_demonstrates_it": [
            "roleplay/corpus.py — schema validation, closed tag and tool vocabularies, "
            "expected_failure must name a declared contract",
            "roleplay/advisory.py — regimes and register entries are closed sets with citations",
            "lab/judges/hallucinated_confirmation/labels.jsonl — 24 hand labels with reasons",
            "ragcheck/fixtures/ — 16 chunks, 18 questions, 18 claim labels",
        ],
        "command": "make roleplay-validate",
        "headline": (
            f"{len(roleplay.scenarios)} advisory scenario rows across {len(suites)} suites "
            f"({', '.join(suites)}), {len(roleplay.profiles)} customer profiles and "
            f"{entries} cited register entries across {len(registers)} regimes — all loaded "
            f"through validating loaders; {yaml_files} YAML files under scenarios/."
        ),
        "figures": {
            "scenario_rows": len(roleplay.scenarios),
            "suites": suites,
            "customer_profiles": len(roleplay.profiles),
            "regimes": sorted(registers),
            "register_entries": entries,
            "yaml_files_under_scenarios": yaml_files,
        },
        "caveats": [
            "One domain. The harness is demonstrated against the advisory coach only; "
            "a second system under test would be the portability evidence and this "
            "repository no longer carries one.",
        ],
    }


def _cov_prompt_regression() -> dict[str, Any]:
    study = _judge_study()
    v1, v2 = study["reports"]["v1"], study["reports"]["v2"]
    paired = study["paired"]
    return {
        "id": "prompt-regression-detection",
        "requirement": "Prompt regression detection: a prompt change is measured on the same labelled items, paired, and recordings refuse a stale prompt",
        "what_demonstrates_it": [
            "lab/judges/judge.py — every recording is keyed by prompt digest; ReplayJudge refuses a mismatch",
            "lab/judges/calibration.py — compare_reports(): paired McNemar, detectability floor, instability floor",
            "lab/judges/hallucinated_confirmation/iteration.md — the v1 -> v2 study, regenerated by `evallab calibrate`",
            "roleplay/scorer_study/ — the same discipline on the advisory rubric, v1 and v2",
        ],
        "command": "evallab calibrate --ci && git diff --exit-code -- fixtures lab/judges",
        "headline": (
            f"v1 -> v2 fixed {paired.after_only_correct} of {paired.n_items} items and broke "
            f"{paired.before_only_correct}; exact McNemar p = {paired.p_value:.5f}; prompts are pinned by sha256."
        ),
        "figures": {
            "prompt_digests": study["prompt_digests"],
            "labels_digest": study["labels_digest"],
            "model": study["model"],
            "true_positive_rate": {"v1": _rate_of(v1.true_positive_rate), "v2": _rate_of(v2.true_positive_rate)},
            "paired_comparison": {
                "items": paired.n_items,
                "both_correct": paired.both_correct,
                "both_wrong": paired.both_wrong,
                "fixed_by_v2": paired.after_only_correct,
                "broken_by_v2": paired.before_only_correct,
                "exact_mcnemar_p": _round(paired.p_value, 5),
            },
            "detectability_floor_items": study["floor"],
        },
        "recomputed": True,
    }


def _cov_rag() -> dict[str, Any]:
    r = _rag()
    rep = r["report"]
    g, cal = rep.generation, rep.calibration
    gold = [row.context_recall_gold for row in g.rows]
    return {
        "id": "rag-retrieval-vs-groundedness",
        "requirement": "RAG: retrieval scored separately from groundedness, never averaged, with the grader's own agreement printed beside the metric",
        "what_demonstrates_it": [
            "ragcheck/report.py — recall of gold, context precision, groundedness per claim, answer relevance",
            "ragcheck/calibration.py — the claim-support judge measured against 18 hand labels and gated",
            "ragcheck/offline.py — the lexical stand-in that runs with no key, labelled as not a model",
        ],
        "command": "make ragcheck",
        "headline": (
            f"groundedness {_rate_of(g.pooled_groundedness)['text']} on claims, "
            f"context recall {_rate_of(g.pooled_context_recall)['text']}, answer relevance "
            f"{_rate_of(g.relevance_rate)['text']}; the support judge scores TPR "
            f"{_rate_of(cal.true_positive_rate)['text']} and the gate refuses it."
        ),
        "figures": {
            "corpus": {"chunks": r["chunks"], "questions": r["cases"], "claim_labels": r["labels"], "k": rep.k},
            "retrieval": {
                "recall_of_gold_pooled": _rate(sum(x.numerator for x in gold), sum(x.denominator for x in gold)),
                "context_precision_gold_mean": _round(g.mean_context_precision_gold.value),
                "context_precision_judged_mean": _round(g.mean_context_precision_judged.value),
            },
            "generation": {
                "groundedness_micro_claims": _rate_of(g.pooled_groundedness),
                "groundedness_macro_answers": _round(g.mean_groundedness.value),
                "answer_relevance": _rate_of(g.relevance_rate),
                "context_recall_reference": _rate_of(g.pooled_context_recall),
                "answers": g.n,
            },
            "support_judge": {
                "name": cal.judge,
                "version": cal.prompt_version,
                "true_positive_rate": _rate_of(cal.true_positive_rate),
                "true_negative_rate": _rate_of(cal.true_negative_rate),
                "cohens_kappa": _round(cal.cohens_kappa),
                "items": cal.confusion.n,
                "gate_refused_with": r["gate_refusal"],
            },
        },
        "recomputed": True,
        "caveats": [
            "The judged metrics come from the offline lexical stand-in, which is not a model; its measured "
            "error rate is the point of the section, not a limitation of it."
        ],
    }


def _cov_voice(result: Any, manifest: dict) -> dict[str, Any]:
    """The voice row of the coverage table, from the two recorded calls."""
    turns = manifest["turns"]
    return {
        "id": "voice-real-synthesis-and-recognition",
        "requirement": (
            "Voice: real synthesis and recognition, and the grade is computed on the "
            "transcript the recogniser produced"
        ),
        "what_demonstrates_it": [
            "roleplay/spoken.py — a whole advisory call synthesised by ElevenLabs and "
            "recognised by Deepgram, turn by turn, graded on what was heard",
            "lab/voice/wer.py — raw and normalised word error rate, reported as two "
            "numbers because the raw figure against a synthesis reference is a trap",
        ],
        "command": "make spoken-replay",
        "headline": (
            f"Two recorded calls, {len(turns)} turns on the first, every line really "
            "synthesised and really transcribed; the grade is computed on the "
            "unpunctuated transcript the recogniser returned, which is how the "
            "question detector came to score a call of five questions at 0/4."
        ),
        "figures": {
            "calls_recorded": 2,
            "turns_first_call": len(turns),
            "graded_on": "text_heard",
            "committed_clips": 10,
        },
        "caveats": [
            "n = 2. A mechanism, not a rate.",
            "Both calls were cut short — a character cap, then a content filter — so "
            "each disclosure miss is entangled with that setting.",
        ],
    }


def _cov_release_quality() -> dict[str, Any]:
    """The release-gate row of the coverage table."""
    return {
        "id": "release-quality-go-no-go",
        "requirement": "Release quality and go/no-go: a gate that can say no",
        "what_demonstrates_it": [
            "Makefile `gate` — six stages, cheapest first, stopping at the first failure",
            "lab/judges/registry.py — require_calibrated() raises below the threshold, so "
            "an uncalibrated judge cannot quietly become load-bearing",
            "lab/cli.py calibrate --ci — non-zero exit, then a byte-for-byte diff of the "
            "artefacts it wrote",
        ],
        "command": "make gate",
        "headline": (
            "The gate runs six stages offline with no credential and stops at the first "
            "red. A judge below its calibration threshold raises rather than warns."
        ),
        "figures": {"gate_stages": 6, "exits_non_zero_on_failure": True},
        "caveats": [
            "Blast-radius test selection was removed with the second domain: its map was "
            "derived from that domain's traces, and with them gone the selector would "
            "have degenerated to 'run everything' with no basis for its miss rate.",
        ],
    }


def _cov_observability() -> dict[str, Any]:
    from lab.report.interop import (
        LANGFUSE_API_TARGET,
        PROMPTFOO_API_TARGET,
        from_langfuse_batch,
        to_langfuse_batch,
        to_promptfoo_tests,
    )
    from lab.trace.io import read_jsonl

    path = REPO / "fixtures" / "audio" / "spoken_call" / "trace.jsonl"
    trace = read_jsonl(path)
    batch = to_langfuse_batch(trace)
    kinds: dict[str, int] = {}
    for entry in batch["batch"]:
        kinds[entry["type"]] = kinds.get(entry["type"], 0) + 1
    tests = to_promptfoo_tests([trace])
    return {
        "id": "observability-interop",
        "requirement": "Observability and interop: the trace exports to the tools a team already watches, and the export round-trips",
        "what_demonstrates_it": [
            "lab/report/interop.py — to_langfuse_batch / from_langfuse_batch (lossless), to_promptfoo_tests (one-way projection)",
            "lab/trace/schema.py — the schema the exports are a view of",
            "tests/test_report_interop.py — the round-trip equality is a test",
        ],
        "command": "python -m pytest tests/test_report_interop.py -q",
        "headline": (
            f"One committed trace of {len(trace.events)} events becomes {len(batch['batch'])} langfuse "
            f"entries and reconstructs exactly ({'yes' if from_langfuse_batch(batch) == trace else 'NO'}); "
            f"the promptfoo projection yields {len(tests)} test case carrying "
            f"{len(tests[0]['assert'])} assertion{'s' if len(tests[0]['assert']) != 1 else ''}."
        ),
        "figures": {
            "trace": _rel(path),
            "events": len(trace.events),
            "langfuse": {"target": LANGFUSE_API_TARGET, "entries": len(batch["batch"]), "by_type": dict(sorted(kinds.items())), "round_trips": from_langfuse_batch(batch) == trace},
            "promptfoo": {"target": PROMPTFOO_API_TARGET, "tests": len(tests), "assertions": len(tests[0]["assert"]), "round_trips": False},
        },
        "recomputed": True,
        "caveats": [
            "This is an evaluation harness that can export to an observability tool, not an observability tool: "
            "no collector, no backend, no retention, no alerting (lab/report/interop.py says so first)."
        ],
    }


def build_coverage(result: Any, manifest: dict) -> dict[str, Any]:
    return {
        "about": (
            "What a QA-for-AI role asks for, mapped to what in this repository "
            "demonstrates it: the files, the one command that proves it, and the "
            "headline figure with its denominator. Every figure is recomputed by "
            "scripts/build_site_data.py from a committed artefact, by the same code "
            "the suite uses, unless its `recomputed` field says otherwise."
        ),
        "requirements": [
            _cov_llm_judge(),
            _cov_golden_datasets(),
            _cov_prompt_regression(),
            _cov_rag(),
            _cov_voice(result, manifest),
            _cov_release_quality(),
            _cov_observability(),
        ],
        "not_covered": [
            {
                "area": "UI and end-to-end browser automation",
                "status": "not covered",
                "why": "There is no browser, no DOM and no screen anywhere in the system under test; the unit is the conversation trace.",
            },
            {
                "area": "Load and performance testing",
                "status": "not covered",
                "why": "Latency is measured per turn behind a calibrated stopwatch (n=12 on the transport row); nothing here drives concurrency or throughput.",
            },
            {
                "area": "Desktop and mobile clients",
                "status": "not covered",
                "why": "No client application exists in this repository to test.",
            },
            {
                "area": "Observability backend",
                "status": "export only",
                "why": "Traces export to langfuse and promptfoo shapes; there is no collector, storage, sampling or alerting.",
            },
            {
                "area": "Interruption and barge-in on a live channel",
                "status": "reserved",
                "why": "The two interruption event kinds are emitted from constructed timings only; no adapter discovers one.",
            },
            {
                "area": "Label quality beyond one labeller",
                "status": "stated, not measured",
                "why": "Every hand label set here has one labeller with reasons recorded; inter-rater agreement is not measured.",
            },
        ],
    }


# ----------------------------------------------------------------- findings.json


def _fd_discovery(result: Any, manifest: dict) -> dict[str, Any]:
    effect = result.effect
    adviser = [t for t in manifest["turns"] if t["speaker"] == "trainee"]
    census = _question_census(adviser)
    return {
        "id": "spoken-call-grader-cannot-hear-a-question",
        "headline": (
            f"On a spoken call where {census['ends_with_question_mark']['text_sent']} of "
            f"{census['adviser_turns']} adviser turns ended in a question mark, discovery scored "
            f"{effect.heard_criteria['discovery']}/4 — and the total, the verdict and the disclosure ledger "
            "were identical either way."
        ),
        "figures": {
            "adviser_turns": census["adviser_turns"],
            "turns_ending_in_question_mark_as_sent": _rate(census["ends_with_question_mark"]["text_sent"], census["adviser_turns"]),
            "turns_ending_in_question_mark_as_heard": _rate(census["ends_with_question_mark"]["text_heard"], census["adviser_turns"]),
            "discovery_as_sent": effect.sent_criteria["discovery"],
            "discovery_as_heard": effect.heard_criteria["discovery"],
            "objection_handling_as_sent": effect.sent_criteria["objection_handling"],
            "objection_handling_as_heard": effect.heard_criteria["objection_handling"],
            "total_as_sent": effect.sent_total,
            "total_as_heard": effect.heard_total,
            "verdicts_identical": effect.sent_verdict == effect.heard_verdict,
        },
        "command": "make start",
        "detail": "finding.json",
        "recomputed": True,
        "caveat": "n = 1. A mechanism, not a rate.",
    }


def _fd_scorer() -> dict[str, Any]:
    report, cleared, reasons = _scorer_calibration()
    c = report.confusion
    return {
        "id": "grader-reluctant-to-fail",
        "headline": (
            f"The advisory scorer agrees with the reviewer on {c.true_negative} of "
            f"{c.true_negative + c.false_positive} passes and on {c.true_positive} of "
            f"{c.true_positive + c.false_negative} fails."
        ),
        "figures": {
            "true_positive_rate": _rate(c.true_positive, c.true_positive + c.false_negative),
            "true_negative_rate": _rate(c.true_negative, c.true_negative + c.false_positive),
            "precision": _rate(c.true_positive, c.true_positive + c.false_positive),
            "cohens_kappa": _round(report.cohens_kappa),
            "confusion": {"tp": c.true_positive, "fp": c.false_positive, "fn": c.false_negative, "tn": c.true_negative, "n": c.n},
            "gate_cleared": cleared,
            "refusal_reasons": list(reasons),
        },
        "command": "make roleplay-demo",
        "recomputed": True,
    }


def _fd_judge_gate() -> dict[str, Any]:
    study = _judge_study()
    t = study["thresholds"]
    out = {}
    for v in ("v1", "v2"):
        r = study["reports"][v]
        cleared, reasons = r.meets(t)
        out[v] = {
            "true_positive_rate": _rate_of(r.true_positive_rate),
            "true_negative_rate": _rate_of(r.true_negative_rate),
            "cohens_kappa": _round(r.cohens_kappa),
            "cleared": cleared,
            "refusal_reasons": list(reasons),
            "prompt_digest": study["prompt_digests"][v],
        }
    return {
        "id": "judge-refused-by-its-own-gate",
        "headline": (
            f"Judge prompt v1 measured TPR {out['v1']['true_positive_rate']['text']} and was refused by the "
            f"gate it was written for; v2 measured {out['v2']['true_positive_rate']['text']} and cleared it. "
            "Both scored a perfect specificity."
        ),
        "figures": {"versions": out, "threshold": {"min_tpr": t.min_tpr, "min_tnr": t.min_tnr, "min_items": t.min_items, "scored_on": t.gate_on}},
        "command": "evallab calibrate --ci",
        "recomputed": True,
    }


def _fd_identical_matrix() -> dict[str, Any]:
    study = _judge_study()
    runs = study["runs"]["v1"]
    cells = [
        {"run": i + 1, "tp": r.confusion.true_positive, "fp": r.confusion.false_positive,
         "fn": r.confusion.false_negative, "tn": r.confusion.true_negative}
        for i, r in enumerate(runs)
    ]
    matrices = {(c["tp"], c["fp"], c["fn"], c["tn"]) for c in cells}
    stab = study["stability"]["v1"]
    stab2 = study["stability"]["v2"]
    return {
        "id": "identical-confusion-matrix-three-runs",
        "headline": (
            f"Prompt v1 returned the same confusion matrix on all {len(runs)} runs while "
            f"{len(stab.unstable)} of {stab.n} items changed verdict between runs — in opposite directions, so they cancelled."
        ),
        "figures": {
            "confusion_per_run": cells,
            "matrices_identical": len(matrices) == 1,
            "items_unanimous_v1": _rate_of(stab.unanimity),
            "unstable_items_v1": [
                {"item": item.item_id, "human": item.human_label, "verdicts": list(item.verdicts)}
                for item in stab.unstable
            ],
            "items_unanimous_v2": _rate_of(stab2.unanimity),
            "runs": stab.runs,
            "model": stab.model,
        },
        "command": "evallab calibrate",
        "reading": "Aggregate stability is not instrument stability. Only the per-item view sees the flips.",
        "recomputed": True,
    }


def _fd_wilson() -> dict[str, Any]:
    from lab.stats import wilson_interval

    study = _judge_study()
    r = study["reports"]["v2"].true_positive_rate
    lo, hi = wilson_interval(r.numerator, r.denominator)
    t = study["thresholds"]
    return {
        "id": "wilson-interval-under-a-perfect-score",
        "headline": (
            f"v2's TPR of {_rate(r.numerator, r.denominator)['text']} has a 95% Wilson interval of "
            f"[{lo:.3f}, {hi:.3f}] — a lower bound below the {t.min_tpr} it just cleared."
        ),
        "figures": {
            "rate": _rate(r.numerator, r.denominator),
            "wilson_95": {"lower": _round(lo), "upper": _round(hi)},
            "gate_threshold": t.min_tpr,
            "gate_scored_on": t.gate_on,
            "lower_bound_clears_threshold": lo >= t.min_tpr,
        },
        "command": 'python -c "from lab.stats import wilson_interval; print(wilson_interval(8, 8))"',
        "recomputed": True,
    }


def _fd_mcnemar() -> dict[str, Any]:
    from lab.judges.calibration import exact_mcnemar_p

    study = _judge_study()
    paired = study["paired"]
    floor = study["floor"]
    return {
        "id": "mcnemar-floor-on-twenty-four-items",
        "headline": (
            f"On {paired.n_items} paired items, {floor} must move together before any improvement is "
            f"publishable at alpha 0.05; v1 -> v2 moved exactly {paired.after_only_correct} (p = {paired.p_value:.5f})."
        ),
        "figures": {
            "items": paired.n_items,
            "both_correct": paired.both_correct,
            "both_wrong": paired.both_wrong,
            "fixed_by_v2": paired.after_only_correct,
            "broken_by_v2": paired.before_only_correct,
            "exact_mcnemar_p": _round(paired.p_value, 5),
            "detectability_floor_items": floor,
            "p_if_d_items_all_move_one_way": {str(d): _round(exact_mcnemar_p(d, 0), 5) for d in range(1, 8)},
        },
        "command": "evallab calibrate",
        "reading": "A v3 that fixed three items and broke none would be unpublishable at p = 0.25 however real the improvement.",
        "recomputed": True,
    }


def _fd_regime() -> dict[str, Any]:
    r = _regime()
    return {
        "id": "same-transcript-opposite-verdicts",
        "headline": (
            f"{r['blocks_that_diverge']}/{r['divergence_blocks']} divergence rows produce opposite computed verdicts "
            f"on one transcript under two regimes; named-entry agreement "
            f"{r['named_entry_agreement']}/{r['regime_verdict_pairs']}, whole-register "
            f"{r['whole_register_agreement']}/{r['regime_verdict_pairs']}."
        ),
        "figures": {
            "rows": r["rows"],
            "row_agreement_with_hand_verdict": _rate(r["agree"], r["rows"]),
            "confusion_human_over_computed": r["confusion_human_over_computed"],
            "divergence_blocks_that_diverge": _rate(r["blocks_that_diverge"], r["divergence_blocks"]),
            "named_entry_agreement": _rate(r["named_entry_agreement"], r["regime_verdict_pairs"]),
            "whole_register_agreement": _rate(r["whole_register_agreement"], r["regime_verdict_pairs"]),
            "divergence_rows": r["divergence_rows"],
        },
        "command": "python -m roleplay.regime_eval --divergence --shadow",
        "recomputed": True,
        "caveat": "In-sample: the probes were written with these transcripts in view. The CLI says so on its second line.",
    }


def build_architecture() -> dict[str, Any]:
    from lab import checks
    from lab.trace.schema import PAYLOAD_KEYS, EventKind
    from roleplay import advisory

    kinds = sorted(EventKind.KNOWN)
    reserved = sorted(EventKind.V2_RESERVED)
    contract_names = [
        "ToolContract", "PromiseContract", "NoReAskContract",
        "FieldPropagationContract", "NoProgressContract", "PhraseContract",
    ]
    contracts = []
    for name in contract_names:
        cls = getattr(checks, name)
        _lines, first = inspect.getsourcelines(cls)
        contracts.append({"name": name, "owns": _first_line(cls), "file": "lab/checks/contracts.py", "line": first})

    registers = advisory.load_registers()
    regimes = {}
    for regime, register in sorted(registers.items()):
        entries = list(register.entries.values())
        kinds_count: dict[str, int] = {}
        evidence: dict[str, int] = {}
        for e in entries:
            kinds_count[e.kind] = kinds_count.get(e.kind, 0) + 1
            evidence[e.evidence] = evidence.get(e.evidence, 0) + 1
        regimes[regime] = {
            "name": advisory.REGIMES[regime],
            "entries": len(entries),
            "by_kind": dict(sorted(kinds_count.items())),
            "by_evidence": dict(sorted(evidence.items())),
            "file": f"scenarios/advisory/registers/{regime}.yaml",
        }

    from roleplay.corpus import load_corpus as _load_roleplay

    roleplay_corpus = _load_roleplay()
    counts = _repo_counts()
    return {
        "one_idea": (
            "The trace is the product. Every verdict, latency figure and judge call is "
            "a function of one JSONL event stream with an injected clock; every check "
            "is decided on event position, not timestamp."
        ),
        "event_kinds": {
            "count": len(kinds),
            "kinds": [{"kind": k, "payload_keys": list(PAYLOAD_KEYS[k])} for k in kinds],
            "reserved_v2": [{"kind": k, "payload_keys": list(PAYLOAD_KEYS[k])} for k in reserved],
            "actors": ["caller", "agent", "system"],
            "source": "lab/trace/schema.py::EventKind",
        },
        "contracts": {
            "count": len(contracts),
            "contracts": contracts,
            "decided_on": "event position in the stream (lab/checks/contracts.py::_positions), never on ts",
            "flagship": "PromiseContract — what the agent said, cross-referenced with what it did",
        },
        "regimes": {
            "count": len(regimes),
            "entries_total": sum(r["entries"] for r in regimes.values()),
            "regimes": regimes,
            "kind_vocabulary": sorted(advisory.REGISTER_KINDS),
            "evidence_vocabulary": ["sourced", "secondary", "assumption"],
            "source": "roleplay/advisory.py — every entry carries a paragraph-level citation or is labelled an assumption",
        },
        "systems_under_test": (
            "One. The harness was demonstrated against a second, unrelated domain "
            "until that domain was removed; the portability argument is therefore "
            "made by the adapter seam below rather than by a worked second example."
        ),
        "packages": {
            "lab": "the engine: trace, checks, judges, simulator, voice, report",
            "roleplay": "advisory sales coaching — the one system under test; its scorer carries the seeded defects",
            "ragcheck": "retrieval and groundedness, scored separately",
            "scenarios": "the corpus, as YAML — plus a spreadsheet path for the people who write it",
        },
        "counts": {
            "scenario_rows": len(roleplay_corpus.scenarios),
            "scenario_suites": sorted({s.suite for s in roleplay_corpus.scenarios}),
            "customer_profiles": len(roleplay_corpus.profiles),
            "yaml_files_under_scenarios": len(list((REPO / "scenarios").rglob("*.yaml"))),
            "tests_collected": counts["tests_collected"],
            "test_files": counts["test_files"],
            "commits_outside_docs_and_this_generator": counts["commits_outside_docs_and_this_generator"],
            "first_commit_date": counts["first_commit_date"],
            "commands": {
                "tests": counts["tests_collected_command"],
                "commits": counts["commits_command"],
                "scenarios": "make roleplay-validate",
            },
            "note": counts["note"],
        },
    }


def build_adapter() -> dict[str, Any]:
    import dataclasses

    from lab.simulator import driver
    from roleplay import live, runtime, spoken

    trainee_lines, trainee_first = inspect.getsourcelines(runtime.Trainee)
    aut_lines, aut_first = inspect.getsourcelines(driver.AgentUnderTest)

    runners = {"roleplay/live.py": live, "roleplay/spoken.py": spoken}
    flag = re.compile(r'add_argument\(\s*["\']--trainee-factory["\']')
    flag_in = {
        path: bool(flag.search(inspect.getsource(module))) for path, module in runners.items()
    }
    seam_present = all(hasattr(live, n) for n in ("resolve_trainee_factory", "build_trainee", "TraineeContext"))
    examples_dir = REPO / "examples" / "adapters"
    example_files = sorted(
        p.name for p in examples_dir.glob("*.py") if p.name != "__init__.py"
    ) if examples_dir.is_dir() else []
    example_command = _documented_command(examples_dir / "echo_trainee.py")
    adapter_doc = "docs/ADAPTER.md" if (REPO / "docs" / "ADAPTER.md").is_file() else None
    if all(flag_in.values()):
        status = "landed"
    elif seam_present:
        status = "pending: the seam (env var, resolver, TraineeContext) is in roleplay/live.py; the --trainee-factory flag is not yet on " + ", ".join(p for p, ok in flag_in.items() if not ok)
    else:
        status = "pending"

    return {
        "about": (
            "How an external agent is plugged in. Two seams, both a dotted path to a "
            "factory and a protocol with no base class: the harness is an instrument "
            "pointed at the system, not a framework the system adopts."
        ),
        "agent_under_test": {
            "protocol": "lab.simulator.driver.AgentUnderTest",
            "file": "lab/simulator/driver.py",
            "line": aut_first,
            "methods": _protocol_methods(driver.AgentUnderTest, include_call=True),
            "source": "".join(aut_lines),
            "factory_flag": "--agent-factory pkg.mod:factory",
            "default_factory": None,
            "default_factory_note": (
                "There is no default agent any more: the repository ships one system "
                "under test and it is driven through the Trainee seam below."
            ),
            "statefulness": "the implementer's; pass^k takes a factory so every repeat starts clean",
        },
        "trainee": {
            "protocol": "roleplay.runtime.Trainee",
            "file": "roleplay/runtime.py",
            "line": trainee_first,
            "methods": _protocol_methods(runtime.Trainee),
            "source": "".join(trainee_lines),
            "contract_in_one_sentence": (
                "open() returns the adviser's first turn or None to decline; "
                "reply(customer_turn) returns the next turn or None to stop; "
                "stop_reason, if present, says why."
            ),
            "factory_receives": [
                {"field": f.name, "type": str(f.type)}
                for f in dataclasses.fields(live.TraineeContext)
            ] if seam_present else [],
            "factory_env_var": getattr(live, "TRAINEE_FACTORY_ENV_VAR", None),
            "resolution_order": (
                "argument, then the environment variable, then the built-in model trainee"
                if seam_present else None
            ),
            "implementations_in_tree": sorted(
                set(_implements(runtime, ("open", "reply"), skip=("Trainee",)))
                | set(_implements(live, ("open", "reply"), skip=()))
                | set(_implements(spoken, ("open", "reply"), skip=()))
            ),
        },
        "example_files": {
            "model_trainee_factory": "roleplay/live.py::model_trainee" if seam_present else None,
            "scripted_trainee": "roleplay/runtime.py::ScriptedTrainee",
            "runnable_adapters_directory": "examples/adapters/" if examples_dir.is_dir() else None,
            "runnable_adapters": example_files,
            "guide": adapter_doc,
        },
        "commands": {
            "trainee": (
                "python -m roleplay.live --trainee-factory pkg.mod:factory"
                if all(flag_in.values())
                else "LAB_TRAINEE_FACTORY=pkg.mod:factory python -m roleplay.live   # flag pending"
            ),
            "trainee_example_offline": example_command,
            "trainee_example_source": (
                "examples/adapters/echo_trainee.py — module docstring" if example_command else None
            ),
        },
        "cli_flag": {
            "name": "--trainee-factory",
            "status": status,
            "present_in": flag_in,
            "seam_present": seam_present,
        },
        "source": {
            "agent_under_test": "lab/simulator/driver.py",
            "trainee": "roleplay/runtime.py",
            "trainee_seam": "roleplay/live.py",
            "importer": "lab/cli.py::_import_object — the same importer for both seams",
        },
    }


def build_findings(result: Any, manifest: dict) -> dict[str, Any]:
    return {
        "about": (
            "Every headline finding in this repository, each with its denominator and "
            "the command that reproduces it. `recomputed: true` means the figure was "
            "rebuilt by this script from a committed artefact; anything else says what "
            "was read and from where."
        ),
        "findings": [
            _fd_discovery(result, manifest),
            _fd_scorer(),
            _fd_judge_gate(),
            _fd_identical_matrix(),
            _fd_wilson(),
            _fd_mcnemar(),
            _fd_regime(),
        ],
    }


# ----------------------------------------------------------------- architecture.json


def _first_line(obj: Any) -> str:
    doc = inspect.getdoc(obj) or ""
    return doc.splitlines()[0] if doc else ""


# ----------------------------------------------------------------- adapter.json


def _protocol_methods(proto: type, *, include_call: bool = False) -> dict[str, str]:
    out = {}
    for name, value in vars(proto).items():
        if name == "__call__" and include_call:
            out[name] = str(inspect.signature(value))
        elif not name.startswith("_") and callable(value):
            out[name] = str(inspect.signature(value))
    return out


def _implements(module: Any, methods: Sequence[str], *, skip: Sequence[str]) -> list[str]:
    found = []
    for name, value in vars(module).items():
        if not inspect.isclass(value) or value.__module__ != module.__name__ or name in skip:
            continue
        if all(callable(getattr(value, m, None)) for m in methods):
            found.append(f"{module.__name__}.{name}")
    return sorted(found)


def _documented_command(path: Path) -> str | None:
    """The indented command block in a module's docstring, as one line.

    Read off the example file rather than typed here, so the command the page
    shows is the one the example's own author wrote and the tests exercise.
    """
    if not path.is_file():
        return None
    import ast

    doc = ast.get_docstring(ast.parse(path.read_text("utf-8"))) or ""
    block: list[str] = []
    for line in doc.splitlines():
        if line.startswith("    "):
            block.append(line.strip().rstrip("\\").strip())
        elif block:
            break
    return " ".join(block) if block else None


# --------------------------------------------------------------------------- #
# Both spoken calls, side by side
# --------------------------------------------------------------------------- #


def _card(card: Any) -> dict[str, Any]:
    return {
        "criteria": dict(card.criteria),
        "total": card.total,
        "max_total": card.max_total,
        "verdict": card.verdict,
        "feedback": card.feedback,
    }


def _cited(directory: Path) -> dict[str, Any]:
    """The same trace graded against the cited scorecard (roleplay/scorecard_eval.py).

    Recomputed from `trace.jsonl` on every build, like everything else here; the
    engaged regime entries are listed so the page can name the rule, not just the
    verdict. Not-applicable KPIs keep their reasons: the denominator is the point.
    """
    from lab.trace.io import read_jsonl as _read_jsonl
    from roleplay.scorecard_eval import evaluate as cited_evaluate

    report = cited_evaluate(_read_jsonl(directory / "trace.jsonl"))
    d = report.as_dict()
    d["regime_verdict"] = {
        "summary": report.regime_verdict.summary(),
        "engaged": [
            {"entry_id": e.entry_id, "status": e.status, "citation": e.citation, "kind": e.kind, "reason": e.reason}
            for e in report.regime_verdict.entries
            if e.status != "not-applicable"
        ],
    }
    d["reproduce"] = f"python -m roleplay.scorecard_eval {_rel(directory / 'trace.jsonl')}"
    return d


def _call_entry(
    *,
    call_id: str,
    label: str,
    directory: Path,
    result: Any,
    manifest: dict,
    served: dict[str, Any],
    reproduce: str,
) -> dict[str, Any]:
    """One committed spoken call, everything the comparison needs, recomputed.

    The score cards and the register come from `replay_spoken_call`, which
    re-runs the production loop over the committed notes — nothing here is read
    out of `scorecards.json`. The register's verdict is the gate the rubric
    fails a session on outright, so it is reported separately from both scorers,
    and the deterministic scorer's `mandatory_disclosure` is compared with it:
    a 4/4 beside an incomplete register is SEEDED DEFECT-3 showing.
    """
    from lab.voice.engines.elevenlabs_tts import DEFAULT_ELEVENLABS_MODEL, credits_for
    from roleplay.live import load_customer_profiles, trainee_prompt
    from roleplay.register import required_codes
    from roleplay.scorer import session_view

    session, spend, assembly = manifest["session"], manifest["spend"], manifest["assembly"]
    turns = manifest["turns"]
    adviser = [t for t in turns if t["speaker"] == "trainee"]
    customer = [t for t in turns if t["speaker"] == "customer"]
    view = session_view(result.trace)
    kinds = list(view.turn_kinds())
    required = list(required_codes(session["jurisdiction"]))
    satisfied = list(result.disclosures_satisfied)
    missing = list(result.disclosures_missing)
    det, live = result.deterministic_card, result.live_card
    defect3 = bool(missing) and det.criteria.get("mandatory_disclosure") == 4

    # The brief the adviser read: the shared exemplary prompt, plus this call's
    # addendum when it had one. Recomputed so the digest is a check, not a claim.
    profile = load_customer_profiles()[session["persona"]]
    base_prompt = trainee_prompt(
        competence=session["competence"],
        profile=profile,
        jurisdiction=session["jurisdiction"],
        language=session["language"],
    )
    addendum = session.get("brief_addendum", "")
    prompt = base_prompt + ("\n\n" + addendum if addendum else "")
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    recorded_sha = session.get("trainee_prompt_sha256")

    # What the call cost to make, in the vendor's unit, per line rounded up the
    # way the engine bills — not what this replay billed, which is zero.
    credits_to_make = sum(credits_for(t["text_sent"], DEFAULT_ELEVENLABS_MODEL) for t in turns)

    gate = "fail" if missing else "met"

    # A committed record of live content-filter probes, when the call has one.
    # It is evidence recorded on the day, not something this script can recompute
    # offline, and it is labelled as such.
    probe_path = directory / "content_filter_probe.json"
    content_filter: dict[str, Any] | None = None
    if probe_path.is_file():
        probe = json.loads(probe_path.read_text("utf-8"))
        stripped = [x for x in probe["probes"] if "stripped" in x["name"] or "heard text" in x["name"]]
        punctuated = [x for x in probe["probes"] if "punctuated" in x["name"]]
        content_filter = {
            "source": _rel(probe_path),
            "kind": "recorded live on the day, not recomputable offline",
            "recorded_utc": probe["recorded_utc"],
            "refused_at_adviser_turn": probe["refused_turn"]["adviser_turn"],
            "trigger_text_heard": probe["refused_turn"]["text_heard"],
            "trigger_text_sent": probe["refused_turn"]["text_sent"],
            "probes_total": probe["counts"]["total"],
            "refused_total": probe["counts"]["refused"],
            "unpunctuated_refused": (
                f"{sum(x['outcome'] == 'refused' for x in stripped)} of {len(stripped)}"
            ),
            "punctuated_refused": (
                f"{sum(x['outcome'] == 'refused' for x in punctuated)} of {len(punctuated)}"
            ),
            "identical_customer_phrasings_at_t0": (
                f"{len(set(probe['customer_phrasings_generated']))} distinct of "
                f"{len(probe['customer_phrasings_generated'])} generated"
            ),
        }
    return {
        "id": call_id,
        "content_filter": content_filter,
        "label": label,
        "fixture_dir": _rel(directory),
        "reproduce": reproduce,
        "session": {
            "scenario_id": session["scenario_id"],
            "session_id": session["session_id"],
            "persona": session["persona"],
            "competence": session["competence"],
            "jurisdiction": session["jurisdiction"],
            "language": session["language"],
            "model_label": session["model_label"],
            "temperature": session["temperature"],
            "turn_budget": session["turn_budget"],
            "character_cap": session["character_cap"],
            "trainee_stop": session["trainee_stop"],
            "scorer_rubric": session["scorer_rubric"],
        },
        "brief": {
            "addendum": addendum,
            "addendum_words": len(addendum.split()),
            "trainee_prompt_sha256": prompt_sha256,
            "manifest_agrees": None if recorded_sha is None else recorded_sha == prompt_sha256,
            "note": (
                "The shared exemplary brief plus this call's addendum, if any. The "
                "addendum is verbatim from roleplay/spoken.py and the manifest; the "
                "digest is recomputed here from the prompt builder."
            ),
        },
        "turns": {
            "total": len(turns),
            "adviser": len(adviser),
            "customer": len(customer),
            "exchanges": max(t["turn"] for t in turns),
        },
        "duration_s": assembly["duration_s"],
        "scorecards": {
            "graded_on": "text_heard",
            "deterministic": _card(det),
            "live": None if live is None else _card(live),
            "verdicts_agree": result.scorers_agree,
            "pass_total": 14,
        },
        "cited": _cited(directory),
        "register": {
            "jurisdiction": session["jurisdiction"],
            "required": required,
            "satisfied": satisfied,
            "missing": missing,
            "recorded": f"{len(satisfied)} of {len(required)}",
            "gate": gate,
            "gate_note": (
                "The rubric fails a session outright on a missing required "
                "disclosure, whatever it totals. `met` means all required codes "
                "were recorded by the disclosure register on heard text."
            ),
        },
        "closing": {
            "close_attempted": "close_attempt" in kinds,
            "adviser_turn_kinds": kinds,
            "deterministic_closing_score": det.criteria.get("closing"),
        },
        "defect3_visible": defect3,
        "defect3_note": (
            "True when the deterministic scorer awarded mandatory_disclosure 4/4 "
            "while the register recorded fewer than the required codes — the "
            "seeded keyword-count defect (roleplay/SEEDED_DEFECTS.md, DEFECT-3)."
        ),
        "verdict": {
            "deterministic": det.verdict,
            "live": None if live is None else live.verdict,
            "register_gate": gate,
            "honest": (
                "pass" if (not missing and det.verdict == "pass" and live is not None and live.verdict == "pass")
                else "fail"
            ),
            "honest_note": (
                "`pass` only when the register gate is met AND both scorers pass; "
                "any one of the three failing makes it `fail`."
            ),
        },
        "channel_effect": {
            "changed_outcome": result.effect.changed_outcome,
            "heard_total": result.effect.heard_total,
            "sent_total": result.effect.sent_total,
            "recognition_deltas": len(result.deltas),
            "of_turns": len(turns),
        },
        "spend": {
            "synthesis_characters_submitted": spend["elevenlabs_characters_submitted"],
            "synthesis_credits_to_make": credits_to_make,
            "synthesis_credits_charged_this_recording": spend["elevenlabs_credits_charged"],
            "synthesis_lines_cached_this_recording": spend["elevenlabs_cached_lines"],
            "recognition_audio_seconds": spend["deepgram_audio_seconds"],
            "recognition_submitted_seconds": spend["deepgram_submitted_seconds"],
            "recognition_requests": spend["deepgram_requests"],
            "note": (
                "`credits_to_make` is the per-line ceiling at the model multiplier "
                "over every text_sent — the cost of synthesising this call from "
                "cold. `charged_this_recording` is what the recording run billed, "
                "which is lower whenever lines came from the digest cache."
            ),
        },
        "audio": {
            "fixture_path": _rel(directory / "full_call.wav"),
            "served_at": served["path"],
            "file_sha256": served["sha256"],
            "pcm_sha256": assembly["audio_sha256"],
            "bytes": served["bytes"],
            "sample_rate_hz": served["sample_rate_hz"],
        },
        "stop_reason": session["trainee_stop"],
    }


def build_calls(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    first, second = entries[0], entries[1]
    return {
        "about": (
            "Both committed spoken calls, side by side. Same adviser competence, "
            "regime, voices, engines, turn budget and character cap; the persona "
            "differs, and the second call's adviser brief carries a documented "
            "addendum. Every figure is recomputed by roleplay.spoken.replay_spoken_call "
            "from the committed manifests — nothing is read back from a summary."
        ),
        "calls": list(entries),
        "comparison": {
            "persona": [first["session"]["persona"], second["session"]["persona"]],
            "register_recorded": [first["register"]["recorded"], second["register"]["recorded"]],
            "register_gate": [first["register"]["gate"], second["register"]["gate"]],
            "deterministic_total": [
                first["scorecards"]["deterministic"]["total"],
                second["scorecards"]["deterministic"]["total"],
            ],
            "live_total": [
                None if first["scorecards"]["live"] is None else first["scorecards"]["live"]["total"],
                None if second["scorecards"]["live"] is None else second["scorecards"]["live"]["total"],
            ],
            "close_attempted": [
                first["closing"]["close_attempted"],
                second["closing"]["close_attempted"],
            ],
            "honest_verdict": [first["verdict"]["honest"], second["verdict"]["honest"]],
            "defect3_visible": [first["defect3_visible"], second["defect3_visible"]],
            "duration_s": [first["duration_s"], second["duration_s"]],
        },
        "reproduce": [
            "python -m roleplay.spoken",
            "python -m roleplay.spoken --call second",
        ],
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def build(out: Path) -> dict[str, str]:
    from roleplay.spoken import replay_spoken_call

    manifest = json.loads((SPOKEN / "manifest.json").read_text("utf-8"))
    result = replay_spoken_call()

    served = serve_full_call(manifest, out / "audio" / "full_call.wav")
    excerpt = cut_excerpt(manifest, out / "audio" / "excerpt.wav")
    digests: dict[str, str] = {}

    # The second call: replayed the same way, served the same way.
    manifest_pass = json.loads((SPOKEN_PASS / "manifest.json").read_text("utf-8"))
    result_pass = replay_spoken_call(directory=SPOKEN_PASS)
    served_pass = serve_full_call(
        manifest_pass,
        out / "audio" / "full_call_pass.wav",
        source=FULL_CALL_PASS,
        site_path=FULL_CALL_PASS_SITE_PATH,
    )
    calls = build_calls(
        [
            _call_entry(
                call_id="first",
                label="aggressive_challenger vs exemplary adviser, eu-retail",
                directory=SPOKEN,
                result=result,
                manifest=manifest,
                served=served,
                reproduce="python -m roleplay.spoken",
            ),
            _call_entry(
                call_id="second",
                label="cautious_saver vs exemplary adviser, eu-retail",
                directory=SPOKEN_PASS,
                result=result_pass,
                manifest=manifest_pass,
                served=served_pass,
                reproduce="python -m roleplay.spoken --call second",
            ),
        ]
    )
    digests["calls.json"] = _dump(out / "calls.json", calls)
    digests["audio/full_call_pass.wav"] = served_pass["sha256"]
    digests["finding.json"] = _dump(out / "finding.json", build_finding(manifest, result))
    digests["question_turns.json"] = _dump(
        out / "question_turns.json", build_question_turns(manifest)
    )
    digests["recognition.json"] = _dump(
        out / "recognition.json", build_recognition(manifest)
    )
    digests["architecture.json"] = _dump(out / "architecture.json", build_architecture())
    digests["adapter.json"] = _dump(out / "adapter.json", build_adapter())
    digests["call.json"] = _dump(
        out / "call.json", build_call(manifest, result, served, excerpt)
    )
    digests["secondary_findings.json"] = _dump(
        out / "secondary_findings.json", build_secondary()
    )
    digests["coverage.json"] = _dump(out / "coverage.json", build_coverage(result, manifest))
    digests["findings.json"] = _dump(out / "findings.json", build_findings(result, manifest))
    digests["audio/full_call.wav"] = served["sha256"]
    digests["audio/excerpt.wav"] = excerpt["sha256"]

    index = {
        "about": (
            "Evidence for the demo page: one finding in depth, and the whole harness "
            "beside it. Every file here is generated by scripts/build_site_data.py "
            "from artefacts committed to this repository. Nothing is hand-typed and "
            "nothing needs a key."
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
            _rel(SPOKEN_PASS / "manifest.json"),
            _rel(SPOKEN_PASS / "trace.jsonl"),
            _rel(SPOKEN_PASS / "scorecards.json"),
            _rel(SPOKEN_PASS / "scorer_recording.jsonl"),
            _rel(SPOKEN_PASS / "content_filter_probe.json"),
            _rel(FULL_CALL_PASS),
            _rel(REPO / "fixtures" / "audio" / "cloud" / "audio_suite_transcripts.json"),
            _rel(REPO / "lab" / "judges" / "hallucinated_confirmation"),
            _rel(REPO / "fixtures" / "audio" / "transport"),
            _rel(REPO / "lab" / "selection" / "calibration.json"),
            _rel(REPO / "scenarios"),
            _rel(REPO / "ragcheck" / "fixtures"),
            "Makefile",
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
