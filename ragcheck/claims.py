"""Splitting an answer into the claims a support check can be run on.

WHAT THIS DEMONSTRATES
----------------------
Groundedness is a fraction, and this module owns its denominator. That is a more
consequential job than it looks, and it is the reason the decomposition here is
deterministic code rather than a model call.

**Why not an LLM decomposer.** Ragas and DeepEval both ask a model to break an
answer into atomic statements. The metric that results has a denominator chosen
by a model at run time, so the same answer can score 3/4 on Monday and 4/5 on
Tuesday with nothing about the system under test having changed. You then cannot
tell a regression from a re-roll of the decomposer, which makes the metric
unusable as a gate — the exact property a release gate needs. A sentence
splitter is worse at English and better at measurement: same answer, same
denominator, every run, so a moved number means the answer moved.

The cost is paid honestly here: this splitter is a heuristic, and its failure
modes are known and listed.

*   It splits on sentence terminators, then on `; `, `, and ` and `, but `,
    because "we hold the table for 20 minutes, and after that we will phone you"
    is two claims of which exactly one is true, and a checker that sees one
    sentence scores it 1/1 or 0/1 and is wrong either way.
*   It will wrongly split a list — "a table for six, and a high chair" becomes
    two fragments. On this corpus that does not arise; on a corpus of order
    confirmations it would, and the fix is a domain-specific rule, not a model.
*   It does not resolve pronouns. "It is GBP 25 per person" is checked as
    written, which is what a reviewer reading the trace also sees.
*   It drops fragments under three words and anything ending in a question mark:
    neither carries a checkable assertion.

A model-based decomposer plugs in at the same seam — `split_claims` returns a
list of strings, and every metric in this package takes that list — so the
choice is a constructor argument, not an architecture.
"""

from __future__ import annotations

import re

__all__ = ["MIN_CLAIM_WORDS", "split_sentences", "split_claims"]

#: Fragments shorter than this are dropped. "Yes." is not a claim about the
#: world; whatever it agrees with is the claim, and that is another sentence.
MIN_CLAIM_WORDS = 3

# A terminator followed by whitespace and a capital letter or a digit. Requiring
# what comes next is what keeps "GBP 15." from ending a sentence mid-number and
# "e.g." from ending one at all.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(]?[A-Z0-9])")

# Clause separators that reliably introduce a second assertion rather than a
# second noun. The comma is required: bare " and " joins list items far more
# often than it joins claims.
_CLAUSE = re.compile(r";\s+|,\s+and\s+|,\s+but\s+|,\s+which\s+")

_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "no.", "mr.", "mrs.", "ms.", "dr.", "vs.")


def split_sentences(text: str) -> list[str]:
    """Sentences, in order, whitespace collapsed.

    Abbreviations are protected by substitution rather than by lookbehind: the
    regex alternative grows a new special case for every new abbreviation, and
    the substitution is one line that a reader can check.
    """
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return []
    guarded = collapsed
    for index, abbreviation in enumerate(_ABBREVIATIONS):
        guarded = guarded.replace(abbreviation, f"\x00{index}\x00")
    parts = [part.strip() for part in _SENTENCE_END.split(guarded)]
    restored: list[str] = []
    for part in parts:
        for index, abbreviation in enumerate(_ABBREVIATIONS):
            part = part.replace(f"\x00{index}\x00", abbreviation)
        if part:
            restored.append(part)
    return restored


def split_claims(text: str, *, min_words: int = MIN_CLAIM_WORDS) -> list[str]:
    """The checkable claims in `text`, in order.

    Deterministic: the same string always yields the same list, which is what
    makes a groundedness fraction comparable between two runs.
    """
    claims: list[str] = []
    for sentence in split_sentences(text):
        for fragment in _CLAUSE.split(sentence):
            claim = fragment.strip().strip(" ,;")
            if not claim:
                continue
            if claim.endswith("?"):
                continue
            if len(claim.split()) < min_words:
                continue
            # A clause split leaves the terminator on the last fragment only;
            # put one back so each claim reads as a sentence in a prompt.
            if claim[-1] not in ".!?":
                claim += "."
            claims.append(claim[0].upper() + claim[1:])
    return claims
