"""The lexical layer: tokens, a crude stemmer, stopwords, numbers.

WHAT THIS DEMONSTRATES
----------------------
Every retrieval and support heuristic in this package bottoms out in these six
functions, and they are collected in one file so that the *scoring* modules can
be read without them and the honest limits of the whole approach are written
down in one place.

This is word matching. It has no embeddings, no model, and no semantics:

*   `stem` chops a handful of English suffixes. It maps "cancelling" and
    "cancelled" onto "cancel", which is the point, and it also maps "policies"
    onto "polic" and would happily conflate "booking" with "book". A real
    system uses a proper stemmer or an embedding; this one is here so the
    fixtures run offline with no model and no network.
*   `content_words` drops a 60-word stoplist. A stoplist is a blunt instrument:
    it removes "not", which is exactly the word that decides whether a passage
    supports a claim or contradicts it. `negations` exists to recover that
    signal separately, and `ragcheck.offline` documents where it still fails.
*   `numbers` extracts digit strings only. "forty-eight hours" and "48 hours"
    are different tokens to this module.

None of that is a defect to be fixed later: it is the reason the offline judge
stand-in is presented as a stand-in, calibrated, and refused as a CI gate. The
metrics in this package are indifferent to which support oracle they are given —
a model, a human, or this — which is the property that makes them testable at
all.
"""

from __future__ import annotations

import re

__all__ = [
    "STOPWORDS",
    "NEGATIONS",
    "tokenize",
    "stem",
    "content_words",
    "numbers",
    "negations",
    "overlap",
]

#: Dropped before scoring. Deliberately short: an aggressive stoplist removes
#: the words that carry the comparison ("more", "only", "per"), and every one of
#: those has already caused a wrong support verdict somewhere.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but if then than that this these those there here
    is are was were be been being am do does did doing done
    have has had having will would shall should can could may might must
    i we you he she it they me us them my our your his her its their
    of to in on at for with from by as into about over under between
    so such very too also just still yet
    what when where which who whom whose why how
    """.split()
)

#: Words whose presence flips the meaning of an otherwise matching sentence.
#: Kept out of `STOPWORDS` and surfaced separately, because "vouchers may be
#: used against a deposit" and "vouchers may not be used against a deposit"
#: share every content word they have.
NEGATIONS: frozenset[str] = frozenset(
    {"no", "not", "never", "cannot", "without", "except", "unless", "nothing", "neither", "nor"}
)

_WORD = re.compile(r"[A-Za-z]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# (suffix, replacement), longest first, so "cancelling" loses "ing" before
# anything shorter can strip a single letter off it. "ies"/"ied" map to "i"
# rather than to nothing so that "parties" and "party" meet at "parti".
_RULES: tuple[tuple[str, str], ...] = (
    ("ingly", ""),
    ("edly", ""),
    ("ing", ""),
    ("ies", "i"),
    ("ied", "i"),
    ("ed", ""),
    ("es", ""),
    ("ly", ""),
    ("s", ""),
)

#: A stem must keep this many characters, or the rule is skipped. Without the
#: floor, "is" becomes "i" and "yes" becomes "y".
_MIN_STEM = 3


def tokenize(text: str) -> list[str]:
    """Lowercase alphabetic words, in order, with duplicates kept."""
    return [match.group(0).lower() for match in _WORD.finditer(text or "")]


def stem(word: str) -> str:
    """Normalise one English word so inflections of it collide.

    Four steps, in order, each one earning its place by a pair that would
    otherwise fail to match:

        one suffix rule          cancelled/cancelling -> cancell
        final y -> i             parties/party        -> parti
        undouble a consonant     cancell              -> cancel
        drop a trailing e        minutes/minute       -> minut

    It is not a linguistically correct stemmer and does not try to be. It
    conflates "booking" with "book", leaves "used" and "use" apart (the suffix
    rule would leave a two-character stem, so it is skipped), and has no idea
    that "held" is "hold". Those are the limits of the offline layer, written
    down here rather than discovered later.
    """
    stemmed = word.lower()
    for suffix, replacement in _RULES:
        if stemmed.endswith(suffix) and len(stemmed) - len(suffix) >= _MIN_STEM:
            stemmed = stemmed[: -len(suffix)] + replacement
            break
    if len(stemmed) >= 4 and stemmed.endswith("y") and stemmed[-2] not in "aeiou":
        stemmed = stemmed[:-1] + "i"
    if len(stemmed) >= 4 and stemmed[-1] == stemmed[-2] and stemmed[-1] not in "aeiou":
        stemmed = stemmed[:-1]
    if len(stemmed) >= 5 and stemmed.endswith("e"):
        stemmed = stemmed[:-1]
    return stemmed


def content_words(text: str) -> list[str]:
    """Stemmed, stopword-free tokens, in order, duplicates kept.

    Order and duplicates are preserved because a caller measuring overlap may
    want term frequency; callers that want a set can build one.
    """
    return [stem(token) for token in tokenize(text) if token not in STOPWORDS]


def numbers(text: str) -> set[str]:
    """Digit strings in `text`, normalised so "1,500" and "1500" agree.

    Numbers get their own extractor because they are where an ungrounded answer
    is most often caught and least often argued about: a claim of "GBP 25 per
    person" against a passage that says 15 is wrong in a way no reviewer needs
    to interpret.
    """
    found = set()
    for match in _NUMBER.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        found.add(raw.rstrip(".0") if "." in raw else raw)
    return {value for value in found if value}


def negations(text: str) -> set[str]:
    """Negation words present in `text`, unstemmed."""
    return {token for token in tokenize(text) if token in NEGATIONS}


def overlap(claim: str, passage: str) -> float:
    """Fraction of the claim's distinct content words that appear in `passage`.

    Asymmetric on purpose. The question a support check asks is "is all of this
    claim in that passage", not "are the two texts similar", and a passage that
    is fifty times longer than the claim should not be penalised for it.
    Returns 0.0 for a claim with no content words at all, which is the honest
    answer: there was nothing to match.
    """
    claim_words = set(content_words(claim))
    if not claim_words:
        return 0.0
    passage_words = set(content_words(passage))
    return len(claim_words & passage_words) / len(claim_words)
