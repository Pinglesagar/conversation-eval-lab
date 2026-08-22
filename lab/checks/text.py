"""Deliberately small string tools that the contracts are built on.

WHAT THIS DEMONSTRATES
----------------------
The hard part of a deterministic check on a conversation is never the trace
plumbing — it is deciding, without a model in the loop, whether one English
sentence counts as an instance of the thing you are looking for. That decision is
where false positives are born, so it lives here, in one small pure-string module
with no dependency on the trace schema, and it is tested directly.

Three decisions are made in this file, and each one is a deliberate trade:

**1. Sentences, not turns.** `sentences()` splits on terminal punctuation. Every
contract that looks for a question scores per sentence, because a single agent
turn routinely mixes a read-back and a fresh question — *"Six people, got it. And
what time would you like?"* — and scoring the turn as a unit makes both possible
answers wrong (see `NoReAskContract`'s docstring for the full argument). The
splitter is naive about abbreviations; conversational agent output rarely contains
them, and a mis-split costs at most one extra candidate sentence, which the
downstream value test then rejects.

**2. Values are matched by surface form, not by equality.** A caller says "six of
us", the tool receives `party_size=6`, and the agent's read-back says "a table
for six". These are the same fact in three notations. `surface_forms()` and
`contains_value()` bridge them for small integers and plain strings. What they
deliberately do *not* bridge is dates and times — "7pm" versus "19:00" versus
"seven in the evening" is a normalisation problem with real ambiguity, and
guessing at it inside a check would produce confident wrong verdicts. Scenarios
in this repo use one surface form per value; anything richer belongs in a parser
that ships with its own tests, not in an assertion helper.

**3. Filler is stripped before questions are compared.** A stuck agent rarely
repeats itself verbatim; it says *"How many people?"* and then *"Sorry, how many
people will that be?"*. `question_key()` reduces a question to its content tokens
so that near-repeats collapse to one key, which is what makes loop detection work
on real transcripts instead of only on synthetic ones.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = [
    "NUMBER_WORDS",
    "FILLER_WORDS",
    "normalize",
    "sentences",
    "clauses",
    "is_question",
    "surface_forms",
    "contains_value",
    "to_number",
    "loose_equal",
    "question_key",
    "compile_patterns",
    "matches_any",
    "first_match",
]


#: Number words this module can bridge to integers, in both directions. Stops at
#: twenty on purpose: past that, spelled-out numbers stop appearing in speech for
#: the quantities this domain cares about (party sizes, booking references are
#: digits), and a longer table would imply a completeness it does not have.
NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

_NUMBER_TO_WORD: dict[int, str] = {v: k for k, v in NUMBER_WORDS.items()}

#: Tokens dropped before two questions are compared for sameness. These are the
#: words a stuck agent adds when it re-asks, plus politeness scaffolding that
#: carries no content.
FILLER_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "again",
        "ah",
        "also",
        "and",
        "anyway",
        "apologies",
        "besides",
        "but",
        "certainly",
        "cool",
        "er",
        "erm",
        "excuse",
        "great",
        "hi",
        "hello",
        "just",
        "kindly",
        "lovely",
        "me",
        "my",
        "no",
        "now",
        "oh",
        "ok",
        "okay",
        "perfect",
        "please",
        "quickly",
        "really",
        "right",
        "same",
        "so",
        "sorry",
        "sure",
        "th",
        "thanks",
        "the",
        "then",
        "um",
        "uh",
        "well",
        "yeah",
        "yes",
    }
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\s—\-]+")
_CLAUSE_SPLIT = re.compile(r"\s*[,;:]\s*|\s+[—–]\s+")
_PUNCT = re.compile(r"[^\w\s']+")
_WS = re.compile(r"\s+")

# Interrogative openers that make a sentence a question even without a '?'. Voice
# transcripts frequently arrive unpunctuated, so relying on '?' alone would make
# every question-based contract silently blind on STT output.
_QUESTION_OPENERS = (
    "how",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "can",
    "could",
    "would",
    "will",
    "do",
    "does",
    "did",
    "are",
    "is",
    "was",
    "may",
    "might",
    "shall",
    "should",
    "have",
    "has",
)


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    The canonical form for every comparison in this package. Apostrophes survive
    so that contractions stay one token and `\\b` boundaries behave.
    """
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def sentences(text: str) -> list[str]:
    """Split an utterance into sentences, preserving original casing.

    Empty fragments are dropped. A turn with no terminal punctuation comes back
    as a single sentence, which is the right default: an unpunctuated STT turn is
    one unit of speech as far as this module can tell.
    """
    if not text or not text.strip():
        return []
    parts = _SENTENCE_SPLIT.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def clauses(sentence: str) -> list[str]:
    """Split a sentence into comma/semicolon/dash-separated clauses.

    Needed because agents routinely weld an assertion to a question with a comma:
    *"You're all booked in, can I help with anything else?"*. That sentence ends
    in a question mark and contains an offer form, so any sentence-level
    interrogative filter throws the whole thing away — including the assertion at
    the front, which is exactly the claim a promise check exists to test. Clauses
    are the finest unit at which "is this an assertion or a question" still has a
    reliable answer.

    A sentence with no internal punctuation comes back as a single clause, so this
    is safe to apply unconditionally.
    """
    if not sentence or not sentence.strip():
        return []
    parts = _CLAUSE_SPLIT.split(sentence.strip())
    return [p.strip() for p in parts if p and p.strip()]


def is_question(sentence: str) -> bool:
    """True if this sentence asks something.

    Two signals, either sufficient: a terminal question mark, or an interrogative
    opening token. The second exists because STT output is often unpunctuated —
    a detector that required '?' would report zero questions on exactly the voice
    traces this harness is built for.
    """
    stripped = sentence.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    tokens = normalize(stripped).split()
    if not tokens:
        return False
    # Skip a leading filler ("So, how many people") before testing the opener.
    head = next((t for t in tokens if t not in FILLER_WORDS), tokens[0])
    return head in _QUESTION_OPENERS


def to_number(value: Any) -> float | None:
    """Coerce ints, floats, digit strings and number words to a float, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        token = value.strip().lower()
        try:
            return float(token)
        except ValueError:
            if token in NUMBER_WORDS:
                return float(NUMBER_WORDS[token])
    return None


def surface_forms(value: Any) -> set[str]:
    """Every written form of `value` this module will recognise.

    For 6 that is {"6", "six"}; for "nut allergy" it is just the normalised
    string. Used both to find where a caller supplied a value and to decide
    whether an agent sentence states it back.
    """
    forms: set[str] = set()
    if value is None:
        return forms
    if isinstance(value, bool):
        return {str(value).lower()}
    number = to_number(value)
    if number is not None and number.is_integer():
        as_int = int(number)
        forms.add(str(as_int))
        if as_int in _NUMBER_TO_WORD:
            forms.add(_NUMBER_TO_WORD[as_int])
    text = normalize(str(value))
    if text:
        forms.add(text)
    return {f for f in forms if f}


def contains_value(text: str, value: Any, *, mode: str = "icontains") -> bool:
    """Does `text` state `value`?

    Modes:
      icontains  normalised substring, with word boundaries for numeric forms so
                 that party_size 6 is not matched by the "6" inside "16".
      tokens     every content token of the value appears somewhere in the text,
                 in any order — for multi-word values a speaker may reorder.
      eq         the whole normalised text equals a surface form of the value.
    """
    haystack = normalize(text)
    if not haystack:
        return False
    forms = surface_forms(value)
    if not forms:
        return False
    if mode == "eq":
        return haystack in forms
    if mode == "tokens":
        for form in forms:
            wanted = [t for t in form.split() if t]
            if wanted and all(
                re.search(rf"\b{re.escape(tok)}\b", haystack) for tok in wanted
            ):
                return True
        return False
    if mode != "icontains":
        raise ValueError(f"unknown match mode: {mode!r}")
    for form in forms:
        if re.search(rf"\b{re.escape(form)}\b", haystack):
            return True
    return False


def loose_equal(left: Any, right: Any) -> bool:
    """Equality across notations: 6 == "6" == "six", "Friday" == " friday ".

    Numeric comparison wins when both sides are numeric; otherwise both sides are
    normalised as strings. Deliberately does not understand dates or times — see
    the module docstring.
    """
    if left is None or right is None:
        return left is None and right is None
    left_num, right_num = to_number(left), to_number(right)
    if left_num is not None and right_num is not None:
        return abs(left_num - right_num) < 1e-9
    return normalize(str(left)) == normalize(str(right))


def question_key(sentence: str) -> tuple[str, ...]:
    """Reduce a question to its content tokens, for near-repeat detection.

    "How many people?" and "Sorry, so how many people again?" both reduce to
    ("how", "many", "people"), so a re-worded repeat still collapses to one key.
    Order is preserved — a set would make "table for two" and "two for table"
    equal, which is a collision a loop detector cannot afford.
    """
    return tuple(t for t in normalize(sentence).split() if t not in FILLER_WORDS)


def compile_patterns(patterns: Iterable[str], *, case_sensitive: bool = False) -> list[re.Pattern[str]]:
    """Compile regex patterns once, case-insensitively by default."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return [re.compile(p, flags) for p in patterns]


def matches_any(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    """True if any compiled pattern is found in `text`."""
    return any(p.search(text) for p in patterns)


def first_match(text: str, patterns: Iterable[re.Pattern[str]]) -> re.Pattern[str] | None:
    """The first compiled pattern that matches, so evidence can name the rule that fired."""
    for pattern in patterns:
        if pattern.search(text):
            return pattern
    return None
