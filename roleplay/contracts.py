"""Two contracts this domain needs and `lab.checks` cannot supply generically.

WHY NEW CONTRACTS AT ALL
------------------------
Most of what a roleplay scenario asserts is already in `lab.checks`: which tools
were called, with what arguments, in what order (`ToolContract`), what language
must and must not appear (`PhraseContract`), whether the conversation looped
without advancing (`NoProgressContract`). Those are reused verbatim — see
`roleplay/corpus.py`, which compiles YAML into them.

Two assertions are not expressible with the generic set, and both of them are the
JD's named risks stated precisely:

**`FeedbackGroundednessContract` — hallucinated feedback.** The product tells a
salesperson what they did. Every claim it makes about what was said has to be
checkable against what was actually said. This is the *reverse* direction of the
usual grounding check: normally a system's output is grounded in a retrieved
document, here it is grounded in the conversation the same session produced. The
evidence is in the trace, so the check is deterministic, free, and needs no judge.

**`ScoreClaimContract` — a compliance claim with nothing behind it.** The score
card asserts that the mandatory disclosure was given, or that no unlicensed
advice occurred. Both are claims about *events*, and both have structured
evidence elsewhere in the same trace: a disclosure register and an in-session
compliance flagger. When the claim and the ledger disagree, the claim is a
customer-facing falsehood in a regulated conversation.

That second one is the decision-versus-action check pointed at a scorer instead
of at an agent. `lab.checks.PromiseContract` catches "the agent said it booked the
table and never called the tool". This catches "the grader said the disclosure was
given and the register is empty" — same defect class, one layer up: a component
asserting an action that another component's ledger denies.

WHAT BOTH REFUSE TO DO
----------------------
Neither one guesses. A trace with no score card makes `ScoreClaimContract`
inapplicable rather than passing, and a feedback page that makes no checkable
claim makes `FeedbackGroundednessContract` inapplicable rather than passing. A
vacuous result is counted and printed separately by `lab.checks.engine`, which is
what stops either of these from going green by going quiet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from lab.checks.contracts import Contract
from lab.checks.result import CheckResult, Evidence
from lab.trace.schema import EventKind, Trace, TraceEvent

from roleplay.register import normalise
from roleplay.scorer import CUSTOMER_AGENT, SCORER_AGENT

__all__ = [
    "DEFAULT_SCORE_TOOL",
    "TopicClaim",
    "DEFAULT_TOPIC_CLAIMS",
    "FeedbackGroundednessContract",
    "ScoreClaim",
    "DEFAULT_SCORE_CLAIMS",
    "ScoreClaimContract",
]

DEFAULT_SCORE_TOOL: str = "score_session"

#: Where a topic claim may be grounded. Closed, so a typo in `where` is an error
#: rather than a claim silently grounded in the empty string.
_WHERE: frozenset[str] = frozenset({"trainee", "customer", "any", "objection_ledger"})

#: Quoted spans in feedback prose. Straight and curly doubles only: apostrophes
#: are far too common in English feedback ("you didn't ask") for single quotes to
#: mark a quotation reliably, and a check that mistakes an apostrophe for a quote
#: reports a hallucination on every healthy page.
_QUOTED = re.compile(r'"([^"\n]{6,})"|“([^”\n]{6,})”')


def _spoken_events(trace: Trace) -> list[TraceEvent]:
    """Everything said *inside* the roleplay: the trainee and the customer.

    The scorer's own utterances are excluded, which is the whole point — feedback
    grounded in feedback is a tautology, and a check that allowed it would pass
    any page that quoted itself.
    """
    out: list[TraceEvent] = []
    for event in trace.utterances():
        if event.kind == EventKind.AGENT_UTTERANCE and event.get("agent") == SCORER_AGENT:
            continue
        out.append(event)
    return out


def _scorer_events(trace: Trace) -> list[TraceEvent]:
    return [
        e
        for e in trace.events_of_kind(EventKind.AGENT_UTTERANCE)
        if e.get("agent") == SCORER_AGENT
    ]


def _grounding_texts(trace: Trace, where: str) -> list[str]:
    """The strings a topic claim about `where` may be grounded in.

    `objection_ledger` is the interesting one and is not speech at all: it reads
    the `topic` argument off every `raise_objection` event. Grounding a claim
    about an objection in the ledger rather than in the transcript is the same
    discipline the rest of this module applies — the product wrote down which
    objections it raised, so a claim about them has a structured answer and does
    not need a keyword search over prose.

    That choice is load-bearing rather than tidy. Grounding "the customer raised
    cost" in the customer's *words* accepts any turn containing "fee", and this
    domain's customers mention school fees while worrying about something else
    entirely. The looser check passed a fabricated claim on two rows before the
    ledger version replaced it.
    """
    if where == "objection_ledger":
        return [
            str(e.get("args", {}).get("topic", ""))
            for e in trace.events_of_kind(EventKind.TOOL_CALL)
            if e.get("name") == "raise_objection"
        ]
    if where == "trainee":
        events = [e for e in _spoken_events(trace) if e.kind == EventKind.CALLER_UTTERANCE]
    elif where == "customer":
        events = [
            e
            for e in _spoken_events(trace)
            if e.kind == EventKind.AGENT_UTTERANCE and e.get("agent") == CUSTOMER_AGENT
        ]
    else:
        events = _spoken_events(trace)
    return [str(e.get("text", "")) for e in events]


# --------------------------------------------------------------------------- #
# Feedback groundedness
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TopicClaim:
    """A feedback sentence that presupposes a topic came up, and where it must have.

    Quoted spans catch the crude hallucination — the page attributing a sentence
    to someone who never said it. This catches the subtler one: prose that is
    fluent, unquoted, and about a conversation that did not happen. "You left the
    fee objection unanswered" presupposes a fee objection. If the customer never
    mentioned cost, the sentence is not harsh feedback, it is fiction, and the
    trainee will spend their next session defending against an objection nobody
    raised.

    Attributes:
        label: Name for reports.
        says: Regexes over the scorer's prose. Any match makes the claim live.
        grounded_in: Regexes that must match somewhere in `where`. Any one
            suffices — the topic has to have come up, not been phrased a
            particular way.
        where: "trainee", "customer", "any", or "objection_ledger" — see
            `_grounding_texts` for why the ledger is usually the right target.
    """

    label: str
    says: tuple[str, ...]
    grounded_in: tuple[str, ...]
    where: str = "any"

    def __post_init__(self) -> None:
        if self.where not in _WHERE:
            raise ValueError(
                f"TopicClaim.where must be one of {sorted(_WHERE)}, got {self.where!r}"
            )
        for pattern in self.says + self.grounded_in:
            re.compile(pattern)

    def is_live(self, prose: str) -> bool:
        return any(re.search(p, prose, re.IGNORECASE) for p in self.says)

    def is_grounded(self, texts: list[str]) -> bool:
        joined = " ".join(texts)
        return any(re.search(p, joined, re.IGNORECASE) for p in self.grounded_in)


#: The topic claims this scorer's feedback templates can make. Declared as data
#: so a scenario can extend the list without a code change, and so the set is
#: reviewable next to the templates it mirrors.
DEFAULT_TOPIC_CLAIMS: tuple[TopicClaim, ...] = (
    TopicClaim(
        label="fee objection",
        says=(r"\bfee objection\b", r"\bthe customer raised cost\b"),
        grounded_in=(r"\bfee", r"\bcharge", r"\bcost\b", r"\bexpensive\b"),
        where="objection_ledger",
    ),
    TopicClaim(
        label="personal advice",
        # The affirmative sentence only. The scorer's other branch says "Nothing
        # you said crossed into personal advice", and a pattern loose enough to
        # match both would report a hallucination on every clean session — the
        # fastest way to get a groundedness check switched off.
        says=(r"\byou crossed into personal advice\b",),
        grounded_in=(
            r"\byou should\b",
            r"\bif i were you\b",
            r"\bi(?:'d| would) put\b",
            r"\bguaranteed\b",
            r"\bright (?:fund|product|choice) for you\b",
        ),
        where="trainee",
    ),
    TopicClaim(
        label="asked for the business",
        says=(r"\byou asked for the business\b",),
        grounded_in=(
            r"\bshall we\b",
            r"\bproceed\b",
            r"\bget (?:you |that )?(?:set up|started)\b",
            r"\bpaperwork\b",
            r"\bopen the account\b",
            r"\bsign\b",
        ),
        where="trainee",
    ),
)


@dataclass(frozen=True)
class FeedbackGroundednessContract(Contract):
    """Every claim the feedback makes about the session must be in the session.

    Two families of claim, checked separately and reported together:

    1.  **Quoted spans.** A span in quotation marks in the feedback must appear,
        normalised, in something the trainee or the customer actually said. This
        is deliberately strict about the *span* and lenient about the *form*:
        casefolded, accent-stripped, punctuation-collapsed matching, because a
        page that renders a curly apostrophe differently from the transcript has
        not hallucinated anything.

    2.  **Topic claims.** See `TopicClaim`.

    Attributes:
        quotes: Check quoted spans.
        topics: Topic claims to evaluate. Empty disables that family.
        min_quote_chars: Spans shorter than this are ignored. Short quoted
            fragments in coaching prose are usually terminology ("the ongoing
            charge") rather than attribution, and flagging them is how a
            groundedness check earns a reputation for noise and gets switched
            off.
    """

    name: str = "feedback-grounded"
    quotes: bool = True
    topics: tuple[TopicClaim, ...] = DEFAULT_TOPIC_CLAIMS
    min_quote_chars: int = 24

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        feedback_events = _scorer_events(trace)
        if not feedback_events:
            return self._result(
                passed=True,
                detail="0/0 feedback claims checked: this trace has no scorer feedback",
                applicable=False,
            )

        prose = " ".join(str(e.get("text", "")) for e in feedback_events)
        spoken = [normalise(str(e.get("text", ""))) for e in _spoken_events(trace)]
        haystack = " ".join(spoken)

        checked = 0
        grounded = 0
        violations: list[str] = []
        evidence: list[Evidence] = []

        if self.quotes:
            for match in _QUOTED.finditer(prose):
                span = match.group(1) or match.group(2) or ""
                if len(span) < self.min_quote_chars:
                    continue
                checked += 1
                if normalise(span) in haystack:
                    grounded += 1
                    continue
                violations.append(f"quoted span never said: {span[:60]!r}")
                evidence.append(
                    Evidence.from_event(
                        feedback_events[0],
                        quote=span,
                        note=(
                            "attributed to the session in quotation marks; absent from all "
                            f"{len(spoken)} roleplay utterance(s)"
                        ),
                    )
                )

        for claim in self.topics:
            if not claim.is_live(prose):
                continue
            checked += 1
            texts = _grounding_texts(trace, claim.where)
            if claim.is_grounded(texts):
                grounded += 1
                continue
            violations.append(f"{claim.label}: claimed but never came up")
            searched = (
                f"{len(texts)} objection(s) in the ledger"
                if claim.where == "objection_ledger"
                else f"{len(texts)} {claim.where} utterance(s)"
            )
            evidence.append(
                Evidence.absence(
                    f"{claim.label} is presupposed by the feedback and is absent from "
                    f"the {claim.where}",
                    note=(
                        f"searched {searched}"
                        + (f": {texts}" if claim.where == "objection_ledger" else "")
                    ),
                )
            )

        if checked == 0:
            return self._result(
                passed=True,
                detail=(
                    "0/0 feedback claims checked: the feedback quotes nothing and "
                    "presupposes none of the declared topics"
                ),
                applicable=False,
            )

        detail = f"{grounded}/{checked} feedback claims grounded in the session"
        if violations:
            detail += " -- " + "; ".join(violations)
        return self._result(passed=not violations, detail=detail, evidence=evidence)


# --------------------------------------------------------------------------- #
# Score claims versus the ledgers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoreClaim:
    """A claim on the score card, and the tool evidence that must agree with it.

    Attributes:
        label: Name for reports, e.g. "mandatory disclosure given".
        key: Argument on the score tool carrying the claim.
        asserts: The value that makes the claim live. `True` for "this happened",
            `False` for "this did not happen" — both are assertions and both can
            be wrong, which is why the field is a value and not a flag.
        requires: Tools that must appear when the claim is live. Use for a claim
            that an action occurred.
        refutes: Tools that must NOT appear when the claim is live. Use for a
            claim that nothing occurred — a clean-compliance verdict next to a
            raised compliance flag is the case this exists for.
        prose: Regexes over the scorer's feedback that assert the same thing.
            A claim made only in prose is still a claim; the trainee reads the
            prose and never sees the JSON.
    """

    label: str
    key: str
    asserts: Any = True
    requires: tuple[str, ...] = ()
    refutes: tuple[str, ...] = ()
    prose: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.requires and not self.refutes:
            raise ValueError(
                f"ScoreClaim {self.label!r} names neither requires nor refutes, so it "
                "asserts nothing about the trace and would report a vacuous pass"
            )
        for pattern in self.prose:
            re.compile(pattern)


#: The claims this scorer makes. Both of them are compliance claims, because
#: those are the ones with an independent ledger to check against — and a claim
#: with no independent evidence is an opinion, which belongs to a judge and not
#: to a deterministic contract.
DEFAULT_SCORE_CLAIMS: tuple[ScoreClaim, ...] = (
    ScoreClaim(
        label="mandatory disclosure given",
        key="mandatory_disclosure_given",
        asserts=True,
        requires=("record_disclosure",),
        prose=(r"\bdisclosure was given\b", r"\bgiven and (?:is )?recorded\b"),
    ),
    ScoreClaim(
        label="no unlicensed advice",
        key="unlicensed_advice_detected",
        asserts=False,
        refutes=("flag_compliance_risk",),
        prose=(r"\bnothing you said crossed into personal advice\b",),
    ),
)


@dataclass(frozen=True)
class ScoreClaimContract(Contract):
    """The score card's factual claims must agree with the session's own ledgers.

    THE DECISION-VS-ACTION CHECK, POINTED AT A GRADER. The score card is the
    product's opinion about the trainee; the disclosure register and the
    in-session compliance flagger are the product's *records* of what happened.
    When the opinion contradicts the records, the trainee is certified on a
    session the same system already flagged, and the audit trail contains both
    halves of the contradiction.

    Why this cannot be a `PromiseContract`. That contract reads spoken commitments
    out of agent sentences and looks for a backing tool call anywhere in the
    session. Here the claim arrives as a structured argument on a tool call, its
    polarity can be negative ("no advice occurred", which is refuted by evidence
    rather than satisfied by it), and the backing evidence is another tool's
    ledger rather than the claimed action itself. Same idea, different plumbing.

    Attributes:
        score_tool: The tool whose arguments carry the score card.
        claims: Claims to evaluate.
        require_score: When True (the default) a trace with no score card fails
            rather than reporting vacuously. A session that was never graded is
            not a session with nothing to check; it is a missing grade, and the
            first time that happens silently in CI it will happen for a month.
    """

    name: str = "score-claims-backed"
    score_tool: str = DEFAULT_SCORE_TOOL
    claims: tuple[ScoreClaim, ...] = DEFAULT_SCORE_CLAIMS
    require_score: bool = True

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        calls = [
            e
            for e in trace.events_of_kind(EventKind.TOOL_CALL)
            if e.get("name") == self.score_tool
        ]
        if not calls:
            if self.require_score:
                return self._result(
                    passed=False,
                    detail=(
                        f"no {self.score_tool} call in the trace: the session was never "
                        "graded, so every claim on the score card is missing rather than wrong"
                    ),
                    evidence=[
                        Evidence.absence(
                            f"{self.score_tool} never called",
                            note=f"{len(trace.tool_names())} tool call(s) in the session",
                        )
                    ],
                )
            return self._result(
                passed=True,
                detail=f"0/0 score claims checked: no {self.score_tool} call",
                applicable=False,
            )

        args: dict[str, Any] = {}
        for call in calls:
            args.update(dict(call.get("args", {}) or {}))
        called = set(trace.tool_names())
        prose = " ".join(str(e.get("text", "")) for e in _scorer_events(trace))

        checked = 0
        upheld = 0
        violations: list[str] = []
        evidence: list[Evidence] = []

        for claim in self.claims:
            structured = claim.key in args and args[claim.key] == claim.asserts
            spoken = any(re.search(p, prose, re.IGNORECASE) for p in claim.prose)
            if not structured and not spoken:
                continue

            checked += 1
            channel = (
                "score card and feedback"
                if structured and spoken
                else ("score card" if structured else "feedback prose")
            )

            missing = [tool for tool in claim.requires if tool not in called]
            present = [tool for tool in claim.refutes if tool in called]

            if not missing and not present:
                upheld += 1
                continue

            if missing:
                violations.append(
                    f"{claim.label}: asserted in the {channel}, but "
                    f"{'/'.join(missing)} never happened"
                )
                evidence.append(
                    Evidence.from_event(
                        calls[-1],
                        quote=f"{claim.key}={args.get(claim.key, '(prose only)')!r}",
                        note=(
                            f"claims {claim.label}; no {'/'.join(missing)} event exists in "
                            "the session ledger"
                        ),
                    )
                )
            for tool in present:
                for event in trace.events_of_kind(EventKind.TOOL_CALL):
                    if event.get("name") != tool:
                        continue
                    evidence.append(
                        Evidence.from_event(
                            event,
                            note=(
                                f"the session recorded this while the {channel} asserted "
                                f"{claim.label}"
                            ),
                        )
                    )
                violations.append(
                    f"{claim.label}: asserted in the {channel}, but the session "
                    f"recorded {tool}"
                )

        if checked == 0:
            return self._result(
                passed=True,
                detail=(
                    f"0/{len(self.claims)} score claims live: the card asserts none of "
                    "the declared claims"
                ),
                applicable=False,
            )

        detail = f"{upheld}/{checked} live score claims backed by the session ledger"
        if violations:
            detail += " -- " + "; ".join(violations)
        return self._result(passed=not violations, detail=detail, evidence=evidence)
