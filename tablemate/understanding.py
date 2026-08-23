"""Deterministic routing and slot extraction — the agent's ears.

WHAT THIS DEMONSTRATES
----------------------
Why the *decisions* live here and the *wording* does not.

TableMate ships with two backends (see `tablemate.runtime`): a scripted one that
phrases everything from templates, and an LLM one that phrases the same turn with
a model. Both drive the same orchestration, and the reason that is possible is
that everything decision-shaped — which agent should hold the turn, which facts
the caller just supplied, which tool to reach for — is computed by this module
from the utterance alone, with regexes and a small vocabulary, and never by the
language model.

That split is the load-bearing design choice in this package, and it is here for
a testing reason rather than an architectural one: **a bug that only sometimes
reproduces cannot anchor a case study.** If a model chose the tool calls, the
three seeded defects would fire on some samples and not others, every recorded
fixture would be one draw from a distribution, and "the eval suite caught the
bug" would be a claim about luck. With the decisions deterministic and only the
surface wording model-generated, the defects reproduce byte-identically offline,
and swapping backends isolates exactly one variable: phrasing. That turns the
LLM backend into a *measurement* — it answers "how much of my detector's recall
depends on the agent's phrasing?" — instead of a source of noise.

WHAT THIS DOES NOT DO
---------------------
This is demo-grade natural-language understanding and says so. It recognises the
seven weekdays plus "today"/"tomorrow"/"tonight", clock times in a handful of
written forms, integers and number words up to twenty, and a fixed allergen
vocabulary. It does no date arithmetic (so "next Friday" and "Friday" are the
same token and neither is a calendar date), no coreference, and no spelling
correction. A production booking agent needs a real date parser and a real
entity model; substituting one here would add code the harness cannot
demonstrate anything about.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = [
    "SLOT_NAMES",
    "WEEKDAYS",
    "Intent",
    "INTENT_PRECEDENCE",
    "route_intent",
    "intents_in",
    "extract_slots",
    "note_clause",
    "policy_topic",
    "is_policy_question",
    "looks_like_question",
    "wants_to_end",
    "is_affirmative",
    "number_word",
    "merged",
]

#: Every fact the agents track about a call. Named here so the orchestrator, the
#: renderer and the tests all mean the same thing by "party_size".
SLOT_NAMES: tuple[str, ...] = (
    "party_size",
    "date",
    "time",
    "name",
    "dietary",
    "booking_ref",
)

WEEKDAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_NUMBER_WORDS: dict[str, int] = {
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
_WORD_FOR: dict[int, str] = {v: k for k, v in _NUMBER_WORDS.items()}
_NUM = r"(?:\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"


def number_word(value: Any) -> str:
    """`4` -> "four", `24` -> "24". How an agent says a small number out loud.

    Small numbers as words is how people speak, and `lab.checks.text` bridges
    words and digits, so a read-back written this way is still recognised as
    stating the value.
    """
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return str(value)
    return _WORD_FOR.get(number, str(number))


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


class Intent:
    """Where a turn belongs. String constants, so a route is greppable."""

    BOOK = "book"
    MODIFY = "modify"
    POLICY = "policy"
    NONE = "none"


#: Amendment and cancellation markers. Each requires a booking-shaped object
#: nearby, so "what is your cancellation policy" is not mistaken for a request to
#: cancel something — the bare verb would swallow the policy question, and a
#: misrouted turn looks exactly like a routing bug in the report.
_MODIFY_PATTERNS = (
    r"\b(change|move|reschedule|amend|alter|update|push back|bring forward)\b.{0,24}\b(booking|reservation|table|it|that)\b",
    r"\bcancel\b.{0,24}\b(booking|reservation|table)\b",
    r"\b(my|our|the) (existing|current) (booking|reservation)\b",
    r"\b(change|amend|cancel) my (booking|reservation)\b",
    r"\bbooking (ref|reference|number)\b",
    r"\btm-?\d{4}\b",
)

#: The unambiguous policy markers: the words that make a turn a question about
#: house rules however it is phrased. Everything else is decided by
#: `is_policy_question`, which requires the turn to actually *be* a question.
_POLICY_PATTERNS = (
    r"\bpolic(y|ies)\b",
    r"\bdress code\b",
    r"\bcorkage\b",
    r"\bam i allowed\b",
)

#: A request to make a reservation. Narrower than the word "booking" on purpose:
#: "I need to cancel my booking" contains it, and reading that as a request for a
#: new table would have the agent book a table for someone ringing to cancel one.
#: The verb "book" counts; the noun "booking" does not.
_BOOK_PATTERNS = (
    # The verb, which "booking" cannot match: \b requires a boundary after "book".
    r"\b(book|rebook|reserve)\b",
    r"\b(make|making) a (reservation|booking)\b",
    r"\btable for\b",
    r"\b(reservation|table)\b.{0,24}\b(for|on)\b.{0,16}\b(?:"
    + "|".join(WEEKDAYS)
    + r"|tonight|tomorrow|today)\b",
    r"\bavailability\b|\bany (space|room|tables?)\b",
    r"\bget us in\b|\bfit us in\b",
    r"\bsqueeze us in\b",
)

_END_PATTERNS = (
    r"\b(bye|goodbye|cheers then)\b",
    r"\bthat('s| is) (it|all|everything|lovely|great)\b",
    r"\bnothing else\b",
    r"\bno,? (that('s| is) )?(all|it|everything)\b",
    r"\b(all|that's) (good|fine),? thanks\b",
    r"\bsee you (then|there)\b",
)

_YES_PATTERNS = (
    r"\b(yes|yeah|yep|sure|please do|go ahead|that('s| is) right|correct|perfect|lovely)\b",
)


def _any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


#: Interrogative shape, for utterances that arrive without punctuation — which is
#: every utterance, once speech-to-text is in the path.
_QUESTION_RES = (
    re.compile(r"\?\s*$"),
    re.compile(
        r"^\s*(do|does|did|are|is|was|can|could|may|might|will|would|should|have|"
        r"has|what|whats|what's|how|when|where|which|who|why|any)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(do you|does the|is there|are there|is it|can i|can we|could i|could we|"
        r"may i|am i allowed|what('s| is| are)|how (do|does|late|much|many of)|"
        r"do i need|would it be)\b",
        re.IGNORECASE,
    ),
)


def looks_like_question(utterance: str) -> bool:
    """Is this utterance a question, by punctuation or by shape?

    Both, because the punctuation is the first thing transcription loses and an
    agent that only recognises questions ending in "?" behaves differently on the
    voice adapter than on the text one — which would make every voice finding
    ambiguous between a real regression and a missing question mark.
    """
    text = utterance or ""
    return any(pattern.search(text) for pattern in _QUESTION_RES)


def is_policy_question(utterance: str) -> bool:
    """Is the caller asking about house rules rather than stating a requirement?

    Two ways to qualify: an unambiguous marker ("what's your policy on…",
    "corkage"), or a recognised policy topic named *inside an interrogative
    clause*.

    Per clause, not per utterance, and that is the whole subtlety. "Can I book a
    table for two on Friday? One of us has a severe peanut allergy." is a booking
    request followed by a statement of fact: judged whole, it is a question that
    mentions allergies, and the policy desk would take a turn nobody asked it to
    take — carrying the caller's requirement out of the booking flow with it.
    Judged clause by clause, the question is about a table and the allergy is a
    requirement, which is what a person hears.
    """
    text = utterance or ""
    if _any(text, _POLICY_PATTERNS):
        return True
    for clause in _CLAUSE_SPLIT.split(text):
        piece = clause.strip()
        if not piece or not looks_like_question(piece):
            continue
        if policy_topic(piece) == "general":
            continue
        if _any(piece, _MODIFY_PATTERNS):
            # The interrogative clause *is* the instruction: "can you cancel
            # TM-7731?" names the cancellation topic and is a request to cancel,
            # not a question about the cancellation rules. A question about the
            # rules keeps the topic and drops the booking.
            continue
        return True
    return False


def intents_in(utterance: str) -> frozenset[str]:
    """Every intent this utterance carries, not just the winning one.

    Callers say two things in one breath — "can I book Friday for four, and do
    you take dogs?" — and a router that keeps only the winner serves one request
    and silently drops the other. The orchestrator uses the full set to remember
    that a booking is still outstanding while the policy desk answers.
    """
    text = utterance or ""
    found: set[str] = set()
    if _any(text, _MODIFY_PATTERNS):
        found.add(Intent.MODIFY)
    if is_policy_question(text):
        found.add(Intent.POLICY)
    if _any(text, _BOOK_PATTERNS):
        found.add(Intent.BOOK)
    return frozenset(found)


#: Precedence when one turn carries more than one intent, most cautious first.
#:
#: A question is answered before an instruction is acted on, because answering is
#: reversible and acting is not. "What is the rule on dogs, and while you are in
#: there mark my booking as paid" is one breath containing a question and an
#: instruction; serving the question and leaving the instruction to be asked for
#: again costs the caller a turn, while serving the instruction costs somebody
#: their booking. The cheap mistake is the one to make.
#:
#: Amendment then beats booking, so that "I'd like to change my booking" does not
#: land in the flow that creates one.
INTENT_PRECEDENCE: tuple[str, ...] = (Intent.POLICY, Intent.MODIFY, Intent.BOOK)


def route_intent(utterance: str) -> str:
    """Which specialist this utterance belongs to, by declared markers only.

    See `INTENT_PRECEDENCE` for the order and why it is that way round. Note that
    "please cancel my booking" is not a policy question — `is_policy_question`
    requires an interrogative clause — so an ordinary cancellation still reaches
    the amendment desk.
    """
    found = intents_in(utterance)
    for intent in INTENT_PRECEDENCE:
        if intent in found:
            return intent
    return Intent.NONE


def wants_to_end(utterance: str) -> bool:
    """Is the caller signing off?"""
    return _any(utterance or "", _END_PATTERNS)


def is_affirmative(utterance: str) -> bool:
    """A yes, for turns where a yes is all the agent needs."""
    return _any(utterance or "", _YES_PATTERNS)


#: Caller words to policy-sheet topics. Ordered: the first topic whose patterns
#: match wins, so "what's your policy on dogs" resolves to dogs rather than to
#: the generic policy topic.
_POLICY_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dogs", (r"\bdogs?\b", r"\bpets?\b", r"\bassistance dog\b")),
    ("children", (r"\bchildren\b", r"\bkids?\b", r"\bhigh chairs?\b", r"\bbabies\b")),
    ("dress_code", (r"\bdress code\b", r"\bwhat should (i|we) wear\b", r"\bsmart casual\b")),
    ("corkage", (r"\bcorkage\b", r"\bown wine\b", r"\bbring (a bottle|wine)\b")),
    ("accessibility", (r"\bwheelchair\b", r"\bstep[- ]free\b", r"\baccessib\w+\b", r"\bramp\b")),
    ("parking", (r"\bpark(ing)?\b", r"\bcar park\b")),
    ("large_groups", (r"\blarge (group|party|parties)\b", r"\bprivate room\b", r"\bbig group\b")),
    ("deposit", (r"\bdeposit\b", r"\bpre[- ]?order\b")),
    ("cancellation", (r"\bcancel\w*\b", r"\brefund\b", r"\bno[- ]show\b")),
    (
        "allergies",
        (
            r"\ballerg\w+\b",
            r"\bgluten\b",
            r"\bcoeliac\b",
            r"\bceliac\b",
            r"\bdietary\b",
            r"\bcross[- ]contamin\w+\b",
            r"\bnut[- ]free\b",
        ),
    ),
    ("menu", (r"\bset menu\b", r"\btasting menu\b", r"\bmenu\b", r"\bwine list\b")),
)


def policy_topic(utterance: str) -> str:
    """The policy-sheet topic this question is about, or "general" if unclear.

    "general" is a real topic slug that the sheet does not carry, so the tool
    reports `found: False` and the agent says it will check — which is the honest
    behaviour, and is what makes the not-found path reachable in a scenario.
    """
    text = utterance or ""
    for topic, patterns in _POLICY_TOPICS:
        if _any(text, patterns):
            return topic
    return "general"


# --------------------------------------------------------------------------- #
# Slot extraction
# --------------------------------------------------------------------------- #

_REF_RE = re.compile(r"\btm-?(\d{4})\b", re.IGNORECASE)
_TIME_RES = (
    re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})[:.](\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE),
    re.compile(r"\b(noon|midday|midnight)\b", re.IGNORECASE),
)
_DATE_RE = re.compile(
    r"\b(" + "|".join(WEEKDAYS) + r"|today|tomorrow|tonight)\b", re.IGNORECASE
)
_PARTY_RES = (
    re.compile(rf"\btable for ({_NUM})\b", re.IGNORECASE),
    re.compile(rf"\bparty of ({_NUM})\b", re.IGNORECASE),
    re.compile(rf"\bthere(?:'ll| will) be ({_NUM})\b", re.IGNORECASE),
    re.compile(rf"\b({_NUM})\s+(?:people|guests|adults|covers|diners)\b", re.IGNORECASE),
    # "five of us" is a party size; "one of us has an allergy" is a partitive and
    # the single most common false positive in this pattern family. Excluding the
    # one case that is never a count is cheaper, and more honest, than excluding
    # the sentence shapes it shows up in.
    re.compile(rf"\b(?!one\b)({_NUM})\s+of us\b", re.IGNORECASE),
    re.compile(rf"\bmake (?:it|that) ({_NUM})\b", re.IGNORECASE),
    re.compile(rf"\bfor ({_NUM})\b", re.IGNORECASE),
)
_BARE_NUM_RE = re.compile(rf"\b({_NUM})\b", re.IGNORECASE)

_DIET_RE = re.compile(
    r"\b(allerg\w+|gluten[- ]free|gluten|dairy|lactose|nuts?|peanuts?|shellfish|"
    r"vegan|vegetarian|coeliac|celiac|halal|kosher|dietary|intoleran\w+)\b",
    re.IGNORECASE,
)
#: A negated allergen clause is not a dietary requirement. "No allergies, thanks"
#: matches the allergen vocabulary and means the opposite of what it matches;
#: storing it as a note would put the word "allergies" in the kitchen's copy of a
#: booking that has none.
_DIET_NEGATION_RE = re.compile(r"\b(no|none|nothing|not|n't|nope|without)\b", re.IGNORECASE)

#: Whose name goes on the booking, tried before the caller's own name. A personal
#: assistant says both in one breath — "it is for my director, Helena Marchetti,
#: I'm Tom" — and an agent that takes the last name it heard puts the wrong one on
#: the reservation.
#: One or two capitalised words: "Okonkwo", "Helena Marchetti". Two, because a
#: caller who gives a full name and gets half of it on the booking has been
#: half heard.
_PERSON = (
    r"((?:[A-Z]\.?\s+)?[A-Z][A-Za-z'\-]{1,20}(?:\s+[A-Z][A-Za-z'\-]{1,20})?)"
)

_NAME_FOR_BOOKING_RE = re.compile(
    r"(?i:under|in the name of|the name of|name(?:'s| is)?|surname(?:'s| is)?|"
    r"booking (?:is )?for|table (?:is )?for|reservation (?:is )?for)"
    r"\s+(?:(?:the name of|of|for|mr|mrs|ms|miss|dr)\s+)*" + _PERSON + r"\b"
)
#: Booking for somebody else: "it is for my director, Helena Marchetti".
_NAME_THIRD_PARTY_RE = re.compile(
    r"(?i:for (?:my|our|the) [a-z]{2,15},?)\s+" + _PERSON + r"\b"
)
#: The caller introducing themselves. Correct when nobody else has been named.
_NAME_SELF_RE = re.compile(
    r"(?i:it's|its|this is|i'm|i am|call it)\s+" + _PERSON + r"\b"
)
_CAPITALISED_RE = re.compile(r"\b([A-Z][A-Za-z'\-]{1,20}(?:\s+[A-Z][A-Za-z'\-]{1,20})?)\b")
#: Capitalised words that are never a surname, so the "answering the name
#: question" fallback cannot mistake a sentence opener for a caller's name.
_NOT_A_NAME = frozenset(
    {
        "i",
        "it",
        "its",
        "we",
        "the",
        "yes",
        "no",
        "hi",
        "hello",
        "thanks",
        "thank",
        "ok",
        "okay",
        "sure",
        "sorry",
        "and",
        "but",
        "please",
        "lumen",
        "table",
        "booking",
        "reservation",
        *(w.capitalize().lower() for w in WEEKDAYS),
        "today",
        "tomorrow",
        "tonight",
        "actually",
        "perfect",
        "lovely",
        "great",
        "right",
        # Interrogatives and auxiliaries. A capitalised word at the start of a
        # question is the question, not a surname, and "How does the kitchen…"
        # answered to a pending name prompt would otherwise put "How" on the
        # booking — a wrong value is worse than a missing one, because the agent
        # stops asking.
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whose",
        "why",
        "do",
        "does",
        "did",
        "can",
        "could",
        "may",
        "might",
        "shall",
        "should",
        "will",
        "would",
        "is",
        "are",
        "was",
        "have",
        "has",
        "my",
        "your",
        "our",
        "there",
        "just",
        *(_NUMBER_WORDS),
    }
)

#: Clause boundaries, including the question mark: a caller who states a
#: requirement and asks a question in one breath produces one utterance with two
#: clauses of different kinds, and only the statement belongs on the booking.
_CLAUSE_SPLIT = re.compile(r"\s*[,;.?!]\s*|\s+[—–-]\s+")


def extract_slots(utterance: str, *, expecting: str | None = None) -> dict[str, Any]:
    """Every fact this utterance supplies, keyed by slot name.

    Args:
        utterance: What the caller said.
        expecting: The slot the agent last asked about. Used only to read a bare
            answer — "Five." after "how many people?" — which is how people
            actually answer questions and which no amount of context-free pattern
            matching can resolve.

    Returns:
        A mapping over `SLOT_NAMES`; absent keys mean "this utterance said
        nothing about it", which is different from "the caller said no".

    Extraction order matters and is fixed: reference, then time, then date, then
    party size, because each match is *removed* from the working copy before the
    next pattern runs. Without that, "a table for two at 7pm" yields a party of
    seven, and "TM-1042" yields a party of ten.
    """
    text = utterance or ""
    if not text.strip():
        return {}

    found: dict[str, Any] = {}
    working = text

    match = _REF_RE.search(working)
    if match:
        found["booking_ref"] = f"TM-{match.group(1)}"
        working = _cut(working, match)

    for pattern in _TIME_RES:
        match = pattern.search(working)
        if match:
            found["time"] = _normalise_time(match)
            working = _cut(working, match)
            break

    match = _DATE_RE.search(working)
    if match:
        found["date"] = match.group(1).lower()
        working = _cut(working, match)

    for pattern in _PARTY_RES:
        match = pattern.search(working)
        if match:
            size = _to_int(match.group(1))
            if size is not None:
                found["party_size"] = size
                working = _cut(working, match)
            break
    else:
        if expecting == "party_size":
            match = _BARE_NUM_RE.search(working)
            if match:
                size = _to_int(match.group(1))
                if size is not None:
                    found["party_size"] = size
                    working = _cut(working, match)

    dietary = _dietary_clause(text)
    if dietary:
        found["dietary"] = dietary

    name = _name(text, expecting=expecting)
    if name:
        found["name"] = name

    return found


#: Leading filler on a clause the caller tacked onto a sentence. Trimmed so that
#: a note reads as a note — "and one of us has a peanut allergy" is the right
#: clause with the wrong first word, and the kitchen sees the note verbatim.
_CLAUSE_LEAD_RE = re.compile(r"^(?:and|but|also|oh|so|plus|although|though)\s+", re.IGNORECASE)


def _trim_clause(clause: str) -> str:
    """One clause, tidied into something worth writing on a booking."""
    return _CLAUSE_LEAD_RE.sub("", clause.strip()).rstrip(" .!?,;").strip()


def _cut(text: str, match: re.Match[str]) -> str:
    """Remove a matched span so a later pattern cannot re-read its digits."""
    return text[: match.start()] + " " + text[match.end() :]


def _to_int(token: str) -> int | None:
    lowered = token.strip().lower()
    if lowered in _NUMBER_WORDS:
        return _NUMBER_WORDS[lowered]
    try:
        return int(lowered)
    except ValueError:
        return None


def _normalise_time(match: re.Match[str]) -> str:
    """Collapse "7 pm" and "7pm" to one surface form; leave 24-hour times alone."""
    groups = [g for g in match.groups() if g]
    if len(groups) == 1:
        return groups[0].lower()
    if len(groups) == 2 and groups[1].lower() in ("am", "pm"):
        return f"{int(groups[0])}{groups[1].lower()}"
    if len(groups) == 2:
        return f"{int(groups[0]):02d}:{groups[1]}"
    hour, minute, meridiem = groups[0], groups[1], groups[2]
    return f"{int(hour)}:{minute}{meridiem.lower()}"


def _dietary_clause(text: str) -> str | None:
    """The clause that carries a dietary requirement, or None.

    The *clause* rather than the keyword, because "nut allergy" is the useful
    note and "nut" is not, and because the kitchen wants "severe nut allergy"
    with the severity attached.
    """
    for clause in _CLAUSE_SPLIT.split(text):
        piece = clause.strip()
        if not piece or not _DIET_RE.search(piece):
            continue
        if _DIET_NEGATION_RE.search(piece):
            continue
        return _trim_clause(piece)
    return None


#: Requests that belong on the booking rather than in the conversation: the
#: occasion, the seating, an awkward arrival time, what the party needs. These are
#: the things a caller says once and expects to find waiting for them.
_NOTE_RES = (
    r"\b(birthday|anniversary|celebrat\w+|engagement|graduation|candle|cake)\b",
    r"\bhigh[- ]?chairs?\b",
    r"\b(late|delayed|held up)\b",
    r"\b(away from|next to|by|near) the (door|kitchen|window|bar|terrace|toilets)\b",
    r"\b(quiet|quieter|corner|booth|terrace|outside|upstairs|window) (table|seat|spot|corner)\b",
    r"\b(wheelchair|pram|pushchair|buggy|step[- ]free|walking frame)\b",
    r"\b(pre[- ]?order|set menu for us)\b",
)
#: A note is a request, not a question. "Do you have high chairs?" belongs to the
#: policy desk; "we will need a high chair" belongs on the booking.
_NOTE_NEGATION_RE = re.compile(r"\b(no|none|nothing|not|n't|nope|without)\b", re.IGNORECASE)


def note_clause(utterance: str) -> str | None:
    """The clause carrying a request that belongs on the booking, or None.

    Deliberately separate from `extract_slots`: a note is free text the kitchen or
    the floor needs to see, not a slot with a value, and the two are lost in
    different ways. Questions are excluded — an agent that wrote "do you have high
    chairs?" into the booking notes would be filing the question instead of
    answering it.
    """
    text = utterance or ""
    if looks_like_question(text) and len(_CLAUSE_SPLIT.split(text)) == 1:
        return None
    for clause in _CLAUSE_SPLIT.split(text):
        piece = clause.strip()
        if not piece or not _any(piece, _NOTE_RES):
            continue
        if _NOTE_NEGATION_RE.search(piece) or looks_like_question(piece):
            continue
        return _trim_clause(piece)
    return None


def _name(text: str, *, expecting: str | None) -> str | None:
    for pattern in (_NAME_FOR_BOOKING_RE, _NAME_THIRD_PARTY_RE, _NAME_SELF_RE):
        match = pattern.search(text)
        if match and match.group(1).lower() not in _NOT_A_NAME:
            return match.group(1)
    if expecting != "name" or looks_like_question(text):
        # A question is not an answer. Reading a surname out of "How does the
        # kitchen handle that?" is how an agent ends up confidently wrong.
        return None
    # Answering "can I take your name?" with a bare surname.
    for candidate in _CAPITALISED_RE.findall(text):
        if candidate.lower() not in _NOT_A_NAME:
            return candidate
    return None


def merged(slots: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """`slots` with `update` laid over it, ignoring None values.

    A convenience with one rule worth stating: a later utterance that says
    nothing about a slot must not erase it. Only an explicit new value overwrites.
    """
    out = dict(slots)
    for key, value in update.items():
        if value is not None:
            out[key] = value
    return out
