"""`make start` — the on-ramp. One finding, recomputed, offline, in a second.

This is deliberately **not** a tour. The repository has thirty-one make targets
and seventeen documents, and a newcomer who is handed all of them reads none of
them. So this prints one finding — the strongest one here — and then stops and
says where to go next.

The finding is recomputed, never read back from a summary. `replay_spoken_call`
drives the committed per-turn manifest through the same conversation loop that
produced it, so the trace, the disclosure ledger and both score cards are
*computed again* on this machine; the numbers below are then read off that
result and off the manifest. Nothing here is a literal typed into a print
statement, which is the same rule the rest of the repository follows: if a
figure moves, this screen moves with it rather than going quietly stale.

Zero API keys, zero network, zero spend — like every default path in this repo.
"""

from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
RULE = "-" * 78


def _detector_site() -> tuple[str, str]:
    """Locate the question detector in the source, rather than quoting it here.

    A path and a line number typed into a docstring is wrong within a month. The
    point of the finding is that a one-character heuristic decides a grade, so
    the reader has to be able to open the exact line — which means reading it out
    of the live source object.
    """
    from roleplay import persona

    lines, first = inspect.getsourcelines(persona.classify_trainee_turn)
    for offset, line in enumerate(lines):
        if 'endswith("?")' in line:
            return f"roleplay/persona.py:{first + offset}", line.strip()
    return "roleplay/persona.py", "(question detector not located in source)"


def _q_counts(turns: Sequence[dict]) -> tuple[int, int, int, int]:
    """Adviser turns, and how many carry a question mark in each transcript.

    Three transcripts of the same audio exist on every turn: what was sent to
    the synthesiser, the vendor's prettified display string, and the raw string
    that is actually graded. The whole finding is the gap between the third and
    the other two.
    """
    total = len(turns)
    sent = sum(1 for t in turns if t["text_sent"].strip().endswith("?"))
    display = sum(1 for t in turns if t["display_text"].strip().endswith("?"))
    heard = sum(1 for t in turns if "?" in t["text_heard"])
    return total, sent, display, heard


def main(argv: Sequence[str] | None = None) -> int:
    started = time.perf_counter()
    try:
        from roleplay.spoken import SPOKEN_DIR, replay_spoken_call

        result = replay_spoken_call()
        manifest = json.loads(
            (SPOKEN_DIR / "manifest.json").read_text(encoding="utf-8")
        )
    except Exception as exc:  # pragma: no cover - the on-ramp must never dead-end
        print(f"make start could not replay the committed call: {exc}", file=sys.stderr)
        print(
            "\nThis path needs no key, so a failure here is a broken tree rather "
            "than a missing\ncredential. Try `make install`, then `make gate` for "
            "the ordered diagnosis.",
            file=sys.stderr,
        )
        return 1

    effect = result.effect
    adviser = [t for t in manifest["turns"] if t["speaker"] == "trainee"]
    n_turns, q_sent, q_display, q_heard = _q_counts(adviser)
    site, source_line = _detector_site()

    moved = [
        (name, effect.sent_criteria.get(name), effect.heard_criteria.get(name))
        for name in sorted(set(effect.sent_criteria) | set(effect.heard_criteria))
        if effect.sent_criteria.get(name) != effect.heard_criteria.get(name)
    ]
    dropped = [m for m in moved if (m[2] or 0) < (m[1] or 0)]
    gained = [m for m in moved if (m[2] or 0) > (m[1] or 0)]
    ledgers_match = effect.heard_disclosures == effect.sent_disclosures

    out: list[str] = []
    add = out.append

    add("")
    add("conversation-eval-lab — one finding, recomputed just now. No key, no network.")
    add(RULE)
    add("")
    add("THE RUN")
    add(
        f"  A {len(result.notes)}-turn spoken sales call, {result.call_duration_s:.0f}s "
        "of audio: every line synthesised,"
    )
    add(
        "  heard back through a recogniser, and graded on what the recogniser HEARD."
    )
    add("")
    add("THE FINDING")
    if dropped:
        name, sent, heard = dropped[0]
        add(
            f"  The grader marked this adviser down for not asking questions. "
            f"He asked {q_sent}."
        )
        add("")
        add(
            f"    {name:<22} {sent}/4 on what was said  ->  {heard}/4 on what was heard"
        )
    else:  # pragma: no cover - only reachable if the finding is fixed upstream
        add("  No criterion moved between the spoken and the heard grading.")
    add(
        f"    {'turns with a ?':<22} {q_sent}/{n_turns} as sent, "
        f"{q_display}/{n_turns} in the vendor's display transcript,"
    )
    add(
        f"    {'':<22} {q_heard}/{n_turns} in the transcript that was graded"
    )
    add(f"    {'the detector':<22} {site}   {source_line}")
    add("")
    add(
        "  The graded transcript is the one with the sentence punctuation stripped,"
    )
    add(
        "  because the word-error-rate rules require it. So no spoken turn can ever"
    )
    add("  end in a question mark, and no spoken adviser can ever be credited with")
    add("  asking anything.")
    add("")
    add("WHY IT ALMOST ESCAPED")
    if gained:
        name, sent, heard = gained[0]
        add(f"  {name} moved {sent}/4 -> {heard}/4 the other way, so both channels")
    add(
        f"  total {effect.heard_total}/20, both verdicts {effect.heard_verdict.upper()}"
        + (", both disclosure ledgers identical." if ledgers_match else ".")
    )
    add(
        "  A check on the total, on the verdict, or on the ledger would each have"
    )
    add(
        "  reported that the audio channel changed nothing. Only the per-criterion"
    )
    add("  comparison saw it. n = 1: this demonstrates a mechanism, not a rate.")
    add("")
    add("READ NEXT")
    add(RULE)
    add("  make gate            every offline check, cheapest first, stops at first red")
    add("  make help            every target, grouped; the ones that spend money marked")
    add("  docs/README.md       the documentation, indexed by the question you have")
    add("  make spoken-replay   this same call in full: spend, per-turn word errors,")
    add("                       both score cards, every recognition delta")
    add("")
    add(
        f"  ({time.perf_counter() - started:.1f}s, entirely offline. "
        "The suite is `pytest`.)"
    )
    add("")

    print("\n".join(out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
