"""A declarative contract language over a Trace — the deterministic half of the harness.

WHAT THIS DEMONSTRATES
----------------------
Anything that can be decided from the event stream should be decided from the
event stream: with zero variance, zero cost, and no API key. An LLM judge is the
right instrument for "was that reply warm and clear"; it is the wrong instrument
for "was `create_booking` actually called with party_size 6", which is a fact.
Sending facts to a judge buys you a probability distribution over an answer you
could have had exactly, and it makes your suite non-reproducible for no gain.

So the contracts here are declarative data, not code. A scenario author writes
what must be true; the engine reports what was. Six contract types cover the
failure modes that actually recur in multi-agent conversational systems:

    ToolContract              the right calls, in the right order, with the right arguments
    PromiseContract           the agent did what it *said* it did          <- the flagship
    NoReAskContract           context the caller already gave was not asked for again
    FieldPropagationContract  a value survived a handoff and reached a tool argument
    NoProgressContract        the conversation is not looping without advancing
    PhraseContract            required and forbidden language

THE ONE IDEA WORTH TAKING FROM THIS FILE
----------------------------------------
`PromiseContract` implements the decision-vs-action check: cross-referencing what
the agent *asserted in natural language* against what it *actually did in the
tool stream*. Almost all conversational-agent evaluation looks at one side or the
other. Transcript-only evaluation (human review, LLM-as-judge on the dialogue)
reads "Your table is confirmed for Friday at 8" and scores it as a success,
because as text it is a perfect response — fluent, on-task, complete. Tool-only
evaluation counts calls and never notices that the caller was told something
untrue. The failure lives precisely in the gap between the two channels, so it is
invisible to any check that looks at one of them, and it is the worst class of
production bug in a booking system: the user leaves happy and no table exists.

Catching it needs both channels in one representation with a shared clock, which
is exactly what the trace is for, and it needs the natural-language side handled
carefully enough not to cry wolf — see the precision notes on `PromiseContract`.

PRECISION IS THE WHOLE GAME
---------------------------
Every contract here is written to be *quiet when it should be quiet*. A check
that fires on healthy traces gets muted by its owners within a week, at which
point it is worse than absent — it is absent while appearing present. So the
tests for this package assert both directions for every contract: it fires on the
broken trace, and it stays silent on the good one. Where a contract cannot tell
(no handoff happened, the scenario never mentioned the value), it returns a
result marked `applicable=False` rather than a pass, so that silence is visible
in the report instead of being counted as evidence of health.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from lab.checks.result import CheckResult, Evidence
from lab.checks.text import (
    clauses,
    compile_patterns,
    contains_value,
    is_question,
    loose_equal,
    matches_any,
    question_key,
    sentences,
    to_number,
)
from lab.trace.schema import Actor, EventKind, Trace, TraceEvent

__all__ = [
    "Contract",
    "TrackedField",
    "ArgPredicate",
    "Ordering",
    "ToolContract",
    "Promise",
    "PromiseContract",
    "NoReAskContract",
    "FieldPropagationContract",
    "NoProgressContract",
    "PhraseContract",
    "DEFAULT_PROMISES",
    "DEFAULT_HEDGES",
    "DEFAULT_ASK_PATTERNS",
    "CONFIRMATION_FRAMES",
]

#: Sentinel for "this argument was not present at all", which is a different
#: finding from "this argument was present and empty".
_MISSING = object()


@lru_cache(maxsize=None)
def _compiled(patterns: tuple[str, ...], case_sensitive: bool = False) -> tuple[re.Pattern[str], ...]:
    """Compile-and-cache, so a contract reused across a thousand traces compiles once."""
    return tuple(compile_patterns(patterns, case_sensitive=case_sensitive))


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #


class Contract(ABC):
    """One assertion about a trace.

    Subclasses are frozen dataclasses: a contract is a *value*, cheap to build,
    safe to share across traces and threads, and comparable — which matters,
    because a contract set is configuration and configuration gets diffed.

    `context` carries scenario facts the trace alone cannot supply: the party
    size the caller was instructed to ask for, the dietary note they were told to
    mention. It is a plain mapping so a scenario can be a dict, a pydantic model
    dump, or a fixture, without this package growing a dependency on any of them.
    """

    name: str

    @abstractmethod
    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        """Evaluate this contract against one trace."""

    # ---------------------------------------------------------------- helpers

    def _result(
        self,
        *,
        passed: bool,
        detail: str,
        evidence: Sequence[Evidence] = (),
        applicable: bool = True,
    ) -> CheckResult:
        return CheckResult(
            name=self.name,
            passed=passed,
            detail=detail,
            evidence=list(evidence),
            applicable=applicable,
            contract=type(self).__name__,
        )


def _tool_calls(trace: Trace, name: str | None = None) -> list[TraceEvent]:
    """Every `tool_call` event, optionally filtered to one tool name."""
    calls = trace.events_of_kind(EventKind.TOOL_CALL)
    if name is None:
        return calls
    return [e for e in calls if e.get("name") == name]


def _agent_sentences(trace: Trace) -> list[tuple[TraceEvent, str]]:
    """Flatten agent utterances into (event, sentence) pairs, in order.

    The unit of judgement for every language-based contract in this file. See
    `NoReAskContract` for why it is the sentence and not the turn.
    """
    out: list[tuple[TraceEvent, str]] = []
    for event in trace.events_of_kind(EventKind.AGENT_UTTERANCE):
        for sentence in sentences(str(event.get("text", ""))):
            out.append((event, sentence))
    return out


def _or_group(spec: str) -> list[str]:
    """Split an OR-group spec like "create_booking|hold_table" into alternatives."""
    return [part.strip() for part in spec.split("|") if part.strip()]


# --------------------------------------------------------------------------- #
# Tracked fields — the shared notion of "a value the caller supplied"
# --------------------------------------------------------------------------- #


#: Default ask-patterns per field name, for the restaurant-booking domain. These
#: are starting points, not a taxonomy: a scenario that phrases things unusually
#: passes its own `ask_patterns` and overrides the default entirely.
DEFAULT_ASK_PATTERNS: dict[str, tuple[str, ...]] = {
    "party_size": (
        r"\bhow many (people|guests|persons|diners|covers|of you|in your party|will (that|it) be|are (you|we))\b",
        r"\bhow (large|big) (is|will be) (your|the) (party|group|table)\b",
        r"\b(party|group) size\b",
        r"\bfor how many\b",
        r"\btable for how many\b",
        r"\bhow many\b.{0,20}\b(people|guests|covers|diners)\b",
    ),
    "time": (
        r"\bwhat time\b",
        r"\bwhich time\b",
        r"\bpreferred time\b",
        r"\bwhat sort of time\b",
        r"\bwhat time (would|did|do|were)\b",
    ),
    "date": (
        r"\bwhat (date|day)\b",
        r"\bwhich (date|day)\b",
        r"\bwhen (would|did|do) you\b",
    ),
    "name": (
        r"\b(can|could|may) i (get|take|have) your name\b",
        r"\bwhat('s| is) your name\b",
        r"\bwho('s| is) (the )?(booking|reservation|table) (for|under|in)\b",
        r"\byour name\b",
    ),
    "booking_ref": (
        r"\b(booking|reservation|confirmation) (ref|reference|number|code)\b",
        r"\bdo you have (a|your) (booking )?(ref|reference)\b",
    ),
    "dietary": (
        r"\bany (dietary|allergy|allergies|special)\b",
        r"\bany allergies\b",
        r"\bdietary (requirements|restrictions|needs)\b",
    ),
}

#: Frames that mark a sentence as a read-back rather than a fresh request. Used
#: only as a *fallback* signal — see `NoReAskContract` for why value presence is
#: the primary one and these are second-best.
CONFIRMATION_FRAMES: tuple[str, ...] = (
    r"\bjust to confirm\b",
    r"\blet me confirm\b",
    r"\bconfirming\b",
    r"\bso that('s| is)\b",
    r"\bthat('s| is) (right|correct)\b",
    r"\bi have (you )?(down )?for\b",
    r"\bi('ve| have) got\b",
    r"\byou said\b",
    r"\bto (double[- ]?)?check\b",
)


@dataclass(frozen=True)
class TrackedField:
    """A piece of information the caller supplies, and how to recognise it.

    Used by three contracts, which is the point: "party size" means the same
    thing to the no-re-ask check, the propagation check and the loop detector,
    because they all read the same declaration instead of each carrying its own
    regexes that drift apart.

    Attributes:
        name: Field identifier, e.g. "party_size". Also the key into
            `DEFAULT_ASK_PATTERNS` when `ask_patterns` is empty.
        value: The literal value, when the scenario knows it up front.
        context_key: Where to read the value from `context` instead. Defaults to
            `name` when neither `value` nor `context_key` is given.
        ask_patterns: Regexes that mean "the agent is requesting this field".
        supply_patterns: Regexes over caller utterances that mean "the caller
            just gave this field". Needed only when the value is unknown, or when
            the caller's phrasing does not literally contain it.
        match: How the value is matched in text — see `text.contains_value`.
    """

    name: str
    value: Any = None
    context_key: str | None = None
    ask_patterns: tuple[str, ...] = ()
    supply_patterns: tuple[str, ...] = ()
    match: str = "icontains"

    # ------------------------------------------------------------- resolution

    def resolve(self, context: Mapping[str, Any] | None) -> Any:
        """The field's value: explicit if given, else read from `context`."""
        if self.value is not None:
            return self.value
        key = self.context_key or self.name
        if context is not None and key in context:
            return context[key]
        return None

    def asks(self) -> tuple[re.Pattern[str], ...]:
        """Compiled ask-patterns, falling back to the domain defaults by name."""
        patterns = self.ask_patterns or DEFAULT_ASK_PATTERNS.get(self.name, ())
        return _compiled(tuple(patterns))

    def supplies(self) -> tuple[re.Pattern[str], ...]:
        """Compiled supply-patterns (may be empty)."""
        return _compiled(tuple(self.supply_patterns))

    def supply_event(
        self, trace: Trace, context: Mapping[str, Any] | None = None
    ) -> TraceEvent | None:
        """The earliest caller utterance that supplied this field, if any.

        Two independent routes, whichever fires first in the trace: the utterance
        contains a surface form of the known value, or it matches an explicit
        supply pattern. Both are needed — "six of us" contains the value, while
        "just the two of us, plus my parents" supplies party_size 4 without
        containing it anywhere, and only a scenario-authored pattern can see that.
        """
        value = self.resolve(context)
        supply_patterns = self.supplies()
        if value is None and not supply_patterns:
            return None
        for event in trace.events_of_kind(EventKind.CALLER_UTTERANCE):
            text = str(event.get("text", ""))
            if value is not None and contains_value(text, value, mode=self.match):
                return event
            if supply_patterns and matches_any(text, supply_patterns):
                return event
        return None

    def is_asked_in(self, sentence: str) -> bool:
        """Does this sentence request the field?"""
        return matches_any(sentence, self.asks())

    def states_value_in(self, sentence: str, value: Any) -> bool:
        """Does this sentence state the field's value back (a read-back)?"""
        if value is None:
            return False
        return contains_value(sentence, value, mode=self.match)


# --------------------------------------------------------------------------- #
# ToolContract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Ordering:
    """`first` must be called before `then`.

    Ordering is evaluated over the whole session, not within a turn, because the
    sequences worth constraining legitimately span turns: an agent searches for
    availability, the caller picks a slot two turns later, and only then is the
    booking created. A within-turn ordering rule would fail every healthy
    conversation of that shape.

    Attributes:
        strict: False (default) compares first occurrences — the earliest `first`
            must precede the earliest `then`. True requires *every* `then` to
            have some `first` before it, which is what you want for a tool that
            must never run on stale state (re-search before every re-book).
    """

    first: str
    then: str
    strict: bool = False


@dataclass(frozen=True)
class ArgPredicate:
    """A condition on one argument of one tool call.

    Attributes:
        tool: Tool name whose calls this applies to.
        arg: Argument key inside the call's `args` payload.
        op: One of eq, ne, contains, tokens, matches, in, gt, gte, lt, lte,
            present, absent, truthy.
        value: Literal right-hand side.
        ref: Read the right-hand side from `context[ref]` instead of `value`.
            When the key is absent the predicate reports itself *inapplicable*
            rather than failing — a scenario that never specified a party size
            has not been violated by any party size.
        match: Match mode for `contains`; see `text.contains_value`.
        quantifier: "any" (default) — at least one call satisfies this; or "all"
            — every call to the tool must.
        label: Human name for reports; defaults to a rendering of the predicate.

    Note on a call that never happened: if the tool was not called at all, this
    predicate is inapplicable, not failed. The absence of a required call is
    `expected`'s finding (or `PromiseContract`'s); reporting it here as well
    would double-count one bug as two, and a report that inflates its own
    findings is a report nobody can size work from.
    """

    tool: str
    arg: str
    op: str = "eq"
    value: Any = None
    ref: str | None = None
    match: str = "icontains"
    quantifier: str = "any"
    label: str | None = None

    _OPS = frozenset(
        {
            "eq",
            "ne",
            "contains",
            "tokens",
            "matches",
            "in",
            "gt",
            "gte",
            "lt",
            "lte",
            "present",
            "absent",
            "truthy",
        }
    )
    _NEEDS_VALUE = frozenset({"eq", "ne", "contains", "tokens", "matches", "in", "gt", "gte", "lt", "lte"})

    def describe(self) -> str:
        if self.label:
            return self.label
        rhs = f"context[{self.ref!r}]" if self.ref else repr(self.value)
        if self.op in ("present", "absent", "truthy"):
            return f"{self.tool}.{self.arg} {self.op}"
        return f"{self.tool}.{self.arg} {self.op} {rhs}"

    def expected(self, context: Mapping[str, Any] | None) -> Any:
        """Resolve the right-hand side, or `_MISSING` if a `ref` cannot be read."""
        if self.ref is not None:
            if context is None or self.ref not in context:
                return _MISSING
            return context[self.ref]
        return self.value

    def _holds(self, actual: Any, expected: Any) -> bool:
        if self.op == "present":
            return actual is not _MISSING and actual not in (None, "")
        if self.op == "absent":
            return actual is _MISSING or actual in (None, "")
        if self.op == "truthy":
            return actual is not _MISSING and bool(actual)
        if actual is _MISSING:
            # Every remaining operator is a claim about a value that is not there.
            return False
        if self.op == "eq":
            return loose_equal(actual, expected)
        if self.op == "ne":
            return not loose_equal(actual, expected)
        if self.op == "contains":
            return contains_value(str(actual), expected, mode=self.match)
        if self.op == "tokens":
            return contains_value(str(actual), expected, mode="tokens")
        if self.op == "matches":
            return re.search(str(expected), str(actual), re.IGNORECASE) is not None
        if self.op == "in":
            if isinstance(expected, (str, bytes)) or not isinstance(expected, Iterable):
                raise ValueError(f"op 'in' needs an iterable value, got {expected!r}")
            return any(loose_equal(actual, option) for option in expected)
        left, right = to_number(actual), to_number(expected)
        if left is None or right is None:
            raise ValueError(f"op {self.op!r} needs numeric operands, got {actual!r} / {expected!r}")
        if self.op == "gt":
            return left > right
        if self.op == "gte":
            return left >= right
        if self.op == "lt":
            return left < right
        if self.op == "lte":
            return left <= right
        raise ValueError(f"unknown op: {self.op!r}")

    def evaluate(
        self, trace: Trace, context: Mapping[str, Any] | None = None
    ) -> tuple[bool | None, str, list[Evidence]]:
        """Returns (verdict, detail, evidence); verdict None means inapplicable."""
        if self.op not in self._OPS:
            raise ValueError(f"unknown op: {self.op!r} (known: {sorted(self._OPS)})")
        if self.quantifier not in ("any", "all"):
            raise ValueError(f"quantifier must be 'any' or 'all', got {self.quantifier!r}")

        expected = self.expected(context)
        if expected is _MISSING and self.op in self._NEEDS_VALUE:
            return None, f"{self.describe()}: no value for ref {self.ref!r} in context", []

        calls = _tool_calls(trace, self.tool)
        if not calls:
            return None, f"{self.describe()}: {self.tool} was never called", []

        outcomes: list[tuple[TraceEvent, bool]] = []
        for call in calls:
            args = call.get("args") or {}
            actual = args.get(self.arg, _MISSING) if isinstance(args, Mapping) else _MISSING
            outcomes.append((call, self._holds(actual, expected)))

        satisfied = [c for c, ok in outcomes if ok]
        failed = [c for c, ok in outcomes if not ok]
        passed = bool(satisfied) if self.quantifier == "any" else not failed

        if passed:
            return True, f"{self.describe()}: satisfied by {len(satisfied)}/{len(calls)} call(s)", []
        evidence = [
            Evidence.from_event(call, note=f"violates {self.describe()}")
            for call in (failed if self.quantifier == "all" else calls)
        ]
        return (
            False,
            f"{self.describe()}: satisfied by {len(satisfied)}/{len(calls)} call(s)",
            evidence,
        )


@dataclass(frozen=True)
class ToolContract(Contract):
    """What the agent must and must not call, how often, in what order, with what arguments.

    The workhorse. Five independent clause families, all optional, all reported
    together as one result so that a scenario's tool expectations read as one
    block of configuration rather than five loosely related checks:

        expected     tool names that must appear. Each entry may be an OR-group,
                     "create_booking|hold_table", satisfied by any alternative —
                     because a contract should constrain the outcome, not
                     over-specify which of two acceptable implementations
                     produced it. Over-specified contracts are the reason eval
                     suites have to be rewritten every time the agent is.
        forbidden    tool names that must not appear at all.
        min_calls /  per-tool call-count bounds. `max_calls` is the one that
        max_calls    catches retry storms: an agent that calls `search_tables`
                     eleven times found availability, and also burned the
                     caller's patience and your rate limit.
        ordering     see `Ordering`.
        args         see `ArgPredicate`.

    Names in `min_calls` / `max_calls` / `ordering` are plain tool names, not
    OR-groups; counting occurrences of a disjunction is ambiguous, so it is
    disallowed rather than guessed at.
    """

    name: str = "tools"
    expected: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    min_calls: Mapping[str, int] = field(default_factory=dict)
    max_calls: Mapping[str, int] = field(default_factory=dict)
    ordering: tuple[Ordering, ...] = ()
    args: tuple[ArgPredicate, ...] = ()

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        called = trace.tool_names()
        counts: dict[str, int] = {}
        for tool in called:
            counts[tool] = counts.get(tool, 0) + 1

        violations: list[str] = []
        evidence: list[Evidence] = []
        evaluated = 0
        satisfied = 0
        skipped: list[str] = []

        # -- expected -------------------------------------------------------
        for spec in self.expected:
            evaluated += 1
            alternatives = _or_group(spec)
            if any(alt in counts for alt in alternatives):
                satisfied += 1
            else:
                shown = " or ".join(alternatives)
                violations.append(f"expected {shown}, never called")
                evidence.append(
                    Evidence.absence(
                        f"no call to {shown}",
                        note=f"tools called: {', '.join(called) if called else 'none'}",
                    )
                )

        # -- forbidden ------------------------------------------------------
        for tool in self.forbidden:
            evaluated += 1
            offenders = _tool_calls(trace, tool)
            if not offenders:
                satisfied += 1
            else:
                violations.append(f"forbidden {tool} called {len(offenders)}x")
                evidence.extend(
                    Evidence.from_event(e, note=f"{tool} is forbidden by this contract")
                    for e in offenders
                )

        # -- call counts ----------------------------------------------------
        for tool, minimum in self.min_calls.items():
            evaluated += 1
            actual = counts.get(tool, 0)
            if actual >= minimum:
                satisfied += 1
            else:
                violations.append(f"{tool} called {actual}x, minimum {minimum}")
                evidence.append(
                    Evidence.absence(f"{tool} called {actual}x", note=f"minimum is {minimum}")
                )
        for tool, maximum in self.max_calls.items():
            evaluated += 1
            actual = counts.get(tool, 0)
            if actual <= maximum:
                satisfied += 1
            else:
                violations.append(f"{tool} called {actual}x, maximum {maximum}")
                evidence.extend(
                    Evidence.from_event(e, note=f"{tool} exceeds max of {maximum}")
                    for e in _tool_calls(trace, tool)
                )

        # -- ordering -------------------------------------------------------
        for rule in self.ordering:
            evaluated += 1
            ok, detail, order_evidence = self._check_ordering(trace, rule)
            if ok is None:
                evaluated -= 1
                skipped.append(detail)
            elif ok:
                satisfied += 1
            else:
                violations.append(detail)
                evidence.extend(order_evidence)

        # -- argument predicates -------------------------------------------
        for predicate in self.args:
            verdict, detail, arg_evidence = predicate.evaluate(trace, context)
            if verdict is None:
                skipped.append(detail)
                continue
            evaluated += 1
            if verdict:
                satisfied += 1
            else:
                violations.append(detail)
                evidence.extend(arg_evidence)

        if evaluated == 0:
            reason = "; ".join(skipped) if skipped else "contract declares no clauses"
            return self._result(
                passed=True,
                detail=f"0/0 tool clauses evaluated ({reason})",
                applicable=False,
            )

        head = f"{satisfied}/{evaluated} tool clauses satisfied"
        if skipped:
            head += f" ({len(skipped)} not applicable: {'; '.join(skipped)})"
        if violations:
            head += " -- " + "; ".join(violations)
        return self._result(passed=not violations, detail=head, evidence=evidence)

    def _check_ordering(
        self, trace: Trace, rule: Ordering
    ) -> tuple[bool | None, str, list[Evidence]]:
        firsts = _tool_calls(trace, rule.first)
        thens = _tool_calls(trace, rule.then)
        if not thens:
            return None, f"ordering {rule.first} before {rule.then}: {rule.then} never called", []
        if not firsts:
            return (
                False,
                f"{rule.then} called with no preceding {rule.first}",
                [
                    Evidence.from_event(thens[0], note=f"no {rule.first} call anywhere in the trace"),
                ],
            )
        if rule.strict:
            offenders = [t for t in thens if not any(f.ts <= t.ts for f in firsts)]
            if offenders:
                return (
                    False,
                    f"{len(offenders)}/{len(thens)} {rule.then} calls had no preceding {rule.first}",
                    [
                        Evidence.from_event(o, note=f"no {rule.first} call before t={o.ts:.3f}s")
                        for o in offenders
                    ],
                )
            return True, "", []
        if firsts[0].ts <= thens[0].ts:
            return True, "", []
        return (
            False,
            f"first {rule.then} at t={thens[0].ts:.3f}s precedes first {rule.first} at t={firsts[0].ts:.3f}s",
            [
                Evidence.from_event(thens[0], note=f"came before the first {rule.first}"),
                Evidence.from_event(firsts[0], note=f"the {rule.first} that should have come first"),
            ],
        )


# --------------------------------------------------------------------------- #
# PromiseContract — the flagship
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Promise:
    """A spoken commitment and the tool call that would make it true.

    Attributes:
        label: Name for reports, e.g. "booking confirmed".
        says: Regexes over a single sentence, matched case-insensitively. Any one
            matching makes the sentence a candidate commitment.
        requires: Tool names, treated as an OR-group — any one of them satisfies
            the promise.
    """

    label: str
    says: tuple[str, ...]
    requires: tuple[str, ...]


#: Commitment patterns for the booking domain. Every pattern is in the perfect or
#: present-stative — "is confirmed", "I've booked", "you're all set" — because
#: those are assertions that the deed is *done*. Nothing here matches future or
#: conditional speech: "I'll book that now" and "shall I confirm?" are honest
#: things to say before acting, and a contract that flagged them would fire on
#: every well-behaved conversation.
DEFAULT_PROMISES: tuple[Promise, ...] = (
    Promise(
        label="booking confirmed",
        says=(
            r"\b(is|are)\s+(all\s+)?(now\s+)?(confirmed|booked|reserved)\b",
            r"\byou('re|r| are)\s+(all\s+)?(set|booked|confirmed)\b",
            r"\bi('ve| have)\s+(gone ahead and\s+)?(booked|reserved|confirmed|secured)\b",
            r"\bthat('s| is)\s+(all\s+)?(booked|confirmed|done|sorted)\b",
            r"\b(booking|reservation|table)\s+(is|has been)\s+(now\s+)?(confirmed|booked|made|secured)\b",
            r"\bi('ve| have)\s+(put|got)\s+you\s+down\b",
            r"\b(we|i)\s+(have|'ve)\s+you\s+(booked|down)\b",
            r"\b(all|it's all)\s+booked\s+in\b",
        ),
        requires=("create_booking",),
    ),
    Promise(
        label="booking cancelled",
        says=(
            r"\b(is|has been|been)\s+cancelled\b",
            r"\bi('ve| have)\s+cancelled\b",
            r"\bcancellation\s+(is\s+)?(confirmed|done|complete|processed)\b",
            r"\bthat('s| is)\s+cancelled\b",
        ),
        requires=("cancel_booking",),
    ),
    Promise(
        label="booking modified",
        says=(
            r"\bi('ve| have)\s+(changed|updated|moved|amended|switched)\b",
            r"\b(is|has been)\s+(changed|updated|moved|amended)\b",
            r"\bchanges?\s+(have|has)\s+been\s+(made|saved|applied)\b",
            r"\bi('ve| have)\s+(gone ahead and\s+)?(changed|updated|moved)\b",
        ),
        requires=("modify_booking",),
    ),
)


#: Clause-level vetoes. If one of these appears in the same *clause* as a
#: commitment pattern, that clause is not counted as a commitment. They cover the
#: two ways a fluent agent discusses an action without claiming it is done:
#: intent or condition ("I'll", "let me", "once I", "before I") and negation
#: ("isn't confirmed", "couldn't book", "unable to").
#:
#: The scope is the clause, not the sentence, and that choice is load-bearing in
#: both directions. Sentence scope would throw away the genuine claim in "Not a
#: problem, your table is confirmed." — a real phrasing, vetoed by a stray "not"
#: three words earlier. Clause scope reads the negation where it actually applies,
#: so "your table is not confirmed" is still correctly ignored while the sentence
#: above is still caught.
#:
#: Offer forms — "shall I", "would you like", "can I book that" — are absent from
#: this list on purpose. They are interrogative, so `text.is_question` already
#: excludes them by mood, including when STT delivers them without a question
#: mark. Listing them here as well would veto the assertion clause in "You're all
#: booked in, can I help with anything else?", which is precisely the sentence
#: this contract must not miss.
DEFAULT_HEDGES: tuple[str, ...] = (
    r"\bi(')?ll\b",
    r"\bi will\b",
    r"\bwe(')?ll\b",
    r"\blet me\b",
    r"\bgoing to\b",
    r"\bgonna\b",
    r"\bonce (i|we|that|you)\b",
    r"\bas soon as\b",
    r"\bif you('d| would) like\b",
    r"\bbefore i\b",
    r"\bin (a|one) (moment|sec|second)\b",
    r"\bnot\b",
    r"n't\b",
    r"\bunable\b",
    r"\bunfortunately\b",
    r"\bfail(ed|ure|s)?\b",
    r"\btrying to\b",
)


@dataclass(frozen=True)
class PromiseContract(Contract):
    """Every spoken commitment must be backed by the tool call that realises it.

    THE DECISION-VS-ACTION CHECK. If the agent says the booking is confirmed,
    `create_booking` must be in the trace. If it says the reservation is
    cancelled, `cancel_booking` must be. The check is cheap, deterministic, and
    catches the highest-severity failure class in any agent that both talks and
    acts: a confident false statement to the user's face.

    Why it is not just a phrase search
    ----------------------------------
    The natural-language side has to be handled with more care than a keyword
    match, or the check becomes noise. Three precision measures, in order of how
    much they matter:

    1. **Per clause, not per turn.** "I can't confirm that yet. Shall I book it?"
       contains both "confirm" and "book" and promises nothing. Turn-level
       matching loses the boundaries that carry the meaning — and sentence-level
       matching is still too coarse, because agents weld assertions to questions
       with a comma: "You're all booked in, can I help with anything else?" is a
       question by punctuation and a firm claim by content. So each sentence is
       split into clauses and each clause is judged on its own.

    2. **Tense and mood.** Only the perfect and present-stative count — "is
       confirmed", "I've booked", "you're all set". Future and conditional forms
       are what a *correct* agent says immediately before it acts, so matching
       them would fire on healthy traces. Interrogative clauses are skipped by
       mood, which is what makes offer forms ("shall I confirm that?") free.

    3. **Hedges veto.** `DEFAULT_HEDGES` holds negation, intent and condition
       markers; one in a clause disqualifies that clause. This trades a little
       recall for precision — "your table isn't confirmed yet" is correctly
       ignored, and so, as a side effect, would be a contrived clause that hedges
       and promises at once. That trade is the right way round: a check that
       occasionally misses is a gap, while a check that occasionally lies gets the
       whole suite switched off.

    Attributes:
        require_before_utterance: Off by default. When off, a commitment is
            satisfied by a qualifying call anywhere in the session. That is
            deliberate: within a turn, the order of "text streamed to TTS" and
            "tool invoked" is an implementation detail of the runtime, and many
            correct agents speak first and act on the same turn. The failure this
            contract exists to catch is *total absence*, not ordering. Turn it on
            when the product genuinely requires the deed before the claim — then
            "confirmed at t=9s, booked at t=12s" is a finding.
        ignore_agents: Sub-agents whose speech is exempt (rare; a summariser
            reading back a completed session, for instance).
    """

    name: str = "promise-kept"
    promises: tuple[Promise, ...] = DEFAULT_PROMISES
    hedges: tuple[str, ...] = DEFAULT_HEDGES
    require_before_utterance: bool = False
    ignore_agents: tuple[str, ...] = ()

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        hedges = _compiled(tuple(self.hedges))
        detected: list[tuple[TraceEvent, str, Promise]] = []

        for event, sentence in _agent_sentences(trace):
            if self.ignore_agents and event.get("agent") in self.ignore_agents:
                continue
            match: Promise | None = None
            for clause in clauses(sentence):
                # Mood first: a question commits to nothing, however it is worded.
                if is_question(clause):
                    continue
                if matches_any(clause, hedges):
                    continue
                for promise in self.promises:
                    if matches_any(clause, _compiled(promise.says)):
                        match = promise
                        break
                if match is not None:
                    break
            if match is not None:
                detected.append((event, sentence, match))

        if not detected:
            return self._result(
                passed=True,
                detail=(
                    "0/0 spoken commitments checked: the agent never claimed an action "
                    "was complete, so there is nothing to hold it to"
                ),
                applicable=False,
            )

        evidence: list[Evidence] = []
        broken = 0
        for event, sentence, promise in detected:
            calls = [c for name in promise.requires for c in _tool_calls(trace, name)]
            if self.require_before_utterance:
                calls = [c for c in calls if c.ts <= event.ts]
            if calls:
                continue
            broken += 1
            required = " or ".join(promise.requires)
            when = " before the claim" if self.require_before_utterance else ""
            evidence.append(
                Evidence.from_event(
                    event,
                    quote=sentence,
                    note=f"claims {promise.label}, but no {required} call{when}",
                )
            )
            called = trace.tool_names()
            evidence.append(
                Evidence.absence(
                    f"no {required} call{when}",
                    note=f"tools called in this session: {', '.join(called) if called else 'none'}",
                )
            )

        kept = len(detected) - broken
        detail = f"{kept}/{len(detected)} spoken commitments backed by the required tool call"
        if broken:
            detail += f" -- {broken} unbacked claim(s) made to the caller"
        return self._result(passed=broken == 0, detail=detail, evidence=evidence)


# --------------------------------------------------------------------------- #
# NoReAskContract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NoReAskContract(Contract):
    """Information the caller already gave must not be requested again.

    The characteristic failure of a multi-agent system: the caller tells the
    greeter it is a table for six, the greeter hands off, and the next agent
    opens with "and how many people will that be?". Nothing errored, no tool
    failed, the transcript is fluent — and the caller has to repeat themselves,
    which in a voice product is the single most reliable predictor of an
    abandoned call.

    TWO PITFALLS THIS IMPLEMENTATION EXISTS TO AVOID
    ------------------------------------------------
    **1. A read-back confirmation is not a re-ask.** "So that's a table for six
    on Friday, is that right?" contains a question about party size, and it is
    *good behaviour* — confirming a value is how a careful agent guards against
    mis-transcription. A naive detector that flags any interrogative mentioning
    the field will fire on every well-designed confirmation step, and will
    therefore be switched off. The distinction drawn here is that **an ask
    requests information it does not state, and a confirmation states the
    information it is checking**. So the primary test is value presence: if the
    sentence contains a surface form of the value the caller supplied, it is a
    read-back, not a re-ask. `CONFIRMATION_FRAMES` is only a fallback for when
    the harness does not know the value ("just to confirm, what was that?" — a
    frame with nothing stated is genuinely ambiguous, and is treated as a
    confirmation, erring towards silence).

    **2. Score sentences, not turns.** Real agent turns mix both moves in one
    breath: *"Six people, got it. And how many will be in the second party?"* or
    *"Perfect, six of you. What time works?"*. Scoring at turn granularity forces
    a false choice — require the whole turn to be free of the field's ask
    patterns and every turn that also confirms the value gets flagged; require
    only that the turn mention the value somewhere and a genuine re-ask sitting
    next to an unrelated read-back is excused. Neither is acceptable, so the unit
    of judgement is the sentence, and each is classified on its own.

    Attributes:
        fields: What to track. A field with no resolvable value and no supply
            patterns is skipped and reported, not silently dropped.
        grace_seconds: A re-ask within this window of the caller's supplying
            utterance is forgiven. Zero by default. In a voice pipeline the
            agent's question may already have been in flight when the caller
            started speaking, and punishing that is punishing physics rather than
            the agent; set it to roughly one utterance's worth of latency when
            evaluating a barge-in-capable system.
    """

    name: str = "no-re-ask"
    fields: tuple[TrackedField, ...] = ()
    grace_seconds: float = 0.0

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        if not self.fields:
            return self._result(
                passed=True, detail="0/0 fields tracked: contract declares none", applicable=False
            )

        frames = _compiled(CONFIRMATION_FRAMES)
        agent_sentences = _agent_sentences(trace)

        tracked = 0
        clean = 0
        skipped: list[str] = []
        evidence: list[Evidence] = []
        violations: list[str] = []

        for tracked_field in self.fields:
            value = tracked_field.resolve(context)
            supply = tracked_field.supply_event(trace, context)
            if supply is None:
                skipped.append(f"{tracked_field.name} was never supplied by the caller")
                continue
            if not tracked_field.asks():
                skipped.append(f"{tracked_field.name} has no ask-patterns to match")
                continue

            tracked += 1
            cutoff = supply.ts + self.grace_seconds
            offenders: list[tuple[TraceEvent, str]] = []

            for event, sentence in agent_sentences:
                if event.ts <= cutoff:
                    continue
                if not tracked_field.is_asked_in(sentence):
                    continue
                # Pitfall 1: a sentence that states the value is a read-back.
                if tracked_field.states_value_in(sentence, value):
                    continue
                if value is None and matches_any(sentence, frames):
                    continue
                offenders.append((event, sentence))

            if not offenders:
                clean += 1
                continue

            violations.append(f"{tracked_field.name} re-asked {len(offenders)}x")
            evidence.append(
                Evidence.from_event(
                    supply,
                    note=f"caller supplied {tracked_field.name}"
                    + (f" = {value!r}" if value is not None else ""),
                )
            )
            for event, sentence in offenders:
                who = event.get("agent") or "agent"
                evidence.append(
                    Evidence.from_event(
                        event,
                        quote=sentence,
                        note=f"{who} re-asks {tracked_field.name} already given at t={supply.ts:.3f}s",
                    )
                )

        if tracked == 0:
            reason = "; ".join(skipped) if skipped else "no fields resolvable"
            return self._result(
                passed=True, detail=f"0/0 supplied fields checked ({reason})", applicable=False
            )

        detail = f"{clean}/{tracked} supplied fields were not re-asked"
        if skipped:
            detail += f" ({len(skipped)} not applicable: {'; '.join(skipped)})"
        if violations:
            detail += " -- " + "; ".join(violations)
        return self._result(passed=not violations, detail=detail, evidence=evidence)


# --------------------------------------------------------------------------- #
# FieldPropagationContract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldPropagationContract(Contract):
    """A value given in one turn must survive a handoff and reach a tool argument.

    Handoffs are where multi-agent systems lose information, and the loss is
    silent: the receiving agent builds a perfectly well-formed tool call out of
    the context it *was* given, so nothing errors and no transcript reads wrong.
    The caller mentioned a severe nut allergy, the booking exists, the `notes`
    field is empty, and the failure only surfaces at the table.

    This contract joins the three channels needed to see it — the caller's words,
    the handoff boundary, and the tool arguments — and is the reason the trace
    keeps all three in one ordered stream.

    Deliberate inapplicability
    --------------------------
    Two situations return a vacuous result rather than a failure:

    * **The tool was never called.** Then nothing propagated because nothing
      happened, and that absence is `ToolContract`'s or `PromiseContract`'s
      finding. Failing here as well would report one bug twice.
    * **No handoff between the supply and the call** (when `require_handoff` is
      on). The hypothesis under test is specifically "the boundary lost it"; on a
      single-agent path there is no boundary, so there is no verdict to give.

    Both are reported as `applicable=False` and counted separately, so a scenario
    that quietly stops exercising the handoff shows up as a gap in coverage
    instead of as a green check.
    """

    name: str = "field-propagation"
    tracked: TrackedField = field(default_factory=lambda: TrackedField("value"))
    tool: str = "create_booking"
    arg: str = "notes"
    match: str = "icontains"
    require_handoff: bool = True

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        value = self.tracked.resolve(context)
        supply = self.tracked.supply_event(trace, context)
        target = f"{self.tool}.{self.arg}"

        if value is None:
            return self._result(
                passed=True,
                detail=f"0/0 propagations checked: no value known for {self.tracked.name!r}",
                applicable=False,
            )
        if supply is None:
            return self._result(
                passed=True,
                detail=(
                    f"0/0 propagations checked: the caller never supplied "
                    f"{self.tracked.name!r} ({value!r})"
                ),
                applicable=False,
            )

        calls = [c for c in _tool_calls(trace, self.tool) if c.ts >= supply.ts]
        if not calls:
            return self._result(
                passed=True,
                detail=(
                    f"0/0 propagations checked: {self.tool} was never called after "
                    f"{self.tracked.name!r} was supplied, so nothing could carry it"
                ),
                applicable=False,
                evidence=[
                    Evidence.from_event(supply, note=f"supplied {self.tracked.name} = {value!r}")
                ],
            )

        crossings = [h for h in trace.handoffs() if supply.ts <= h.ts <= calls[-1].ts]
        if self.require_handoff and not crossings:
            return self._result(
                passed=True,
                detail=(
                    f"0/0 propagations checked: no handoff between the caller supplying "
                    f"{self.tracked.name!r} and the {self.tool} call, so no boundary was tested"
                ),
                applicable=False,
            )

        carriers = [
            c
            for c in calls
            if self._carries(c.get("args") or {}, value)
        ]
        evidence: list[Evidence] = [
            Evidence.from_event(supply, note=f"caller supplied {self.tracked.name} = {value!r}")
        ]
        for handoff in crossings:
            evidence.append(
                Evidence.from_event(handoff, note="the boundary the value had to survive")
            )

        if carriers:
            evidence.append(
                Evidence.from_event(carriers[0], note=f"{target} carries the value")
            )
            return self._result(
                passed=True,
                detail=f"1/1 values reached {target} across {len(crossings)} handoff(s)",
                evidence=evidence,
            )

        for call in calls:
            evidence.append(
                Evidence.from_event(
                    call, note=f"{target} does not carry {self.tracked.name} = {value!r}"
                )
            )
        return self._result(
            passed=False,
            detail=(
                f"0/1 values reached {target}: {self.tracked.name!r} = {value!r} was supplied at "
                f"t={supply.ts:.3f}s and lost across {len(crossings)} handoff(s)"
            ),
            evidence=evidence,
        )

    def _carries(self, args: Any, value: Any) -> bool:
        if not isinstance(args, Mapping) or self.arg not in args:
            return False
        actual = args[self.arg]
        if actual is None:
            return False
        if self.match == "eq":
            return loose_equal(actual, value)
        return contains_value(str(actual), value, mode=self.match)


# --------------------------------------------------------------------------- #
# NoProgressContract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NoProgressContract(Contract):
    """A question repeated with nothing accomplished in between is a stuck agent.

    WHY A GLOBAL REPEAT COUNTER IS THE WRONG TOOL
    ---------------------------------------------
    The obvious implementation counts how many times each agent line occurs and
    flags anything above one. It produces false positives on every healthy
    conversation, because repetition is normal and often correct:

      * "Anything else I can help with?" is *supposed* to recur — once after each
        completed request. Three occurrences means three things were done.
      * "What time would you like?" legitimately recurs when the caller changes
        their mind, or when the first choice was unavailable and a fresh search
        happened in between.
      * A confirmation loop ("and the name for the booking?") reappears when the
        caller books a second table in the same call.

    In every one of those cases something *moved* between the repeats. What makes
    a repeat pathological is not the repetition — it is the absence of progress
    around it. So this contract detects repeats and then examines the **window
    between consecutive occurrences**, flagging the pair only when that window
    contains no advance at all: no tool call, no handoff, and no newly captured
    field. That reframing is the whole design: the same surface behaviour is a
    finding or not depending on what happened around it, and only a windowed
    check can tell the difference.

    Repeats are matched on `text.question_key`, which strips filler, so the
    common real-world form — "How many people?" then "Sorry, how many people
    will that be?" — collapses to one key instead of escaping as two distinct
    strings.

    Attributes:
        fields: Fields whose capture counts as progress. Without these, a window
            in which the caller finally answered a question but no tool ran would
            be reported as a loop.
        progress_kinds: Event kinds that count as progress on their own.
        questions_only: Only consider interrogative sentences. Non-questions
            repeat innocuously ("No problem.") far more often than they signal a
            loop.
        min_repeats: How many occurrences of one key before any window is
            examined. Two means the first repeat is enough.
    """

    name: str = "no-progress-loop"
    fields: tuple[TrackedField, ...] = ()
    progress_kinds: tuple[str, ...] = (EventKind.TOOL_CALL, EventKind.AGENT_HANDOFF)
    questions_only: bool = True
    min_repeats: int = 2

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        if self.min_repeats < 2:
            raise ValueError(f"min_repeats must be >= 2, got {self.min_repeats}")

        groups: dict[tuple[str, ...], list[tuple[TraceEvent, str]]] = {}
        for event, sentence in _agent_sentences(trace):
            if self.questions_only and not is_question(sentence):
                continue
            key = question_key(sentence)
            if not key:
                continue
            groups.setdefault(key, []).append((event, sentence))

        repeated = {k: v for k, v in groups.items() if len(v) >= self.min_repeats}
        if not repeated:
            return self._result(
                passed=True,
                detail=(
                    f"0/0 repeat windows examined: no agent question recurred "
                    f"{self.min_repeats}+ times across {len(groups)} distinct question(s)"
                ),
                applicable=False,
            )

        progress_events = trace.events_of_kind(*self.progress_kinds)
        captures = self._capture_times(trace, context)

        windows = 0
        stalled = 0
        evidence: list[Evidence] = []

        for occurrences in repeated.values():
            for (prev_event, prev_text), (next_event, next_text) in zip(
                occurrences, occurrences[1:], strict=False
            ):
                windows += 1
                lo, hi = prev_event.ts, next_event.ts
                advanced = [e for e in progress_events if lo < e.ts <= hi]
                captured = [(name, ts) for name, ts in captures if lo < ts <= hi]
                if advanced or captured:
                    continue
                stalled += 1
                evidence.append(
                    Evidence.from_event(prev_event, quote=prev_text, note="asked here")
                )
                evidence.append(
                    Evidence.from_event(
                        next_event,
                        quote=next_text,
                        note=(
                            f"asked again {hi - lo:.3f}s later with no tool call, no handoff "
                            "and no new field captured in between"
                        ),
                    )
                )

        detail = f"{windows - stalled}/{windows} repeat windows showed progress between the repeats"
        if stalled:
            detail += f" -- {stalled} stalled repeat(s)"
        return self._result(passed=stalled == 0, detail=detail, evidence=evidence)

    def _capture_times(
        self, trace: Trace, context: Mapping[str, Any] | None
    ) -> list[tuple[str, float]]:
        """When each tracked field was first supplied — the third progress signal."""
        out: list[tuple[str, float]] = []
        for tracked_field in self.fields:
            supply = tracked_field.supply_event(trace, context)
            if supply is not None:
                out.append((tracked_field.name, supply.ts))
        return out


# --------------------------------------------------------------------------- #
# PhraseContract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PhraseContract(Contract):
    """Language that must appear, and language that must not.

    The blunt instrument, and worth having precisely because it is blunt: policy
    disclosures that must be read out, and phrasing that must never be uttered
    (inventing a discount, quoting a price, promising a specific table). Both are
    compliance questions with exact answers, so they get an exact check rather
    than a judge with a rubric.

    Matching is over whole utterances rather than sentences: a required
    disclosure may legitimately span a sentence boundary, and a forbidden phrase
    is forbidden wherever it falls.

    Attributes:
        required: Each entry must be found in at least one matching utterance.
        forbidden: No matching utterance may contain any entry.
        regex: Treat entries as regular expressions rather than literals.
        actor: Restrict to one actor's speech; "agent" by default, since the
            caller is simulated and constraining its script here would be
            checking the harness rather than the system under test.
        case_sensitive: Off by default.
    """

    name: str = "phrases"
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    regex: bool = False
    actor: Actor | None = "agent"
    case_sensitive: bool = False

    def check(self, trace: Trace, context: Mapping[str, Any] | None = None) -> CheckResult:
        if not self.required and not self.forbidden:
            return self._result(
                passed=True,
                detail="0/0 phrase clauses evaluated: contract declares none",
                applicable=False,
            )

        utterances = [
            e for e in trace.utterances() if self.actor is None or e.actor == self.actor
        ]
        evaluated = 0
        satisfied = 0
        violations: list[str] = []
        evidence: list[Evidence] = []

        for phrase in self.required:
            evaluated += 1
            hits = [e for e in utterances if self._matches(str(e.get("text", "")), phrase)]
            if hits:
                satisfied += 1
            else:
                violations.append(f"required phrase never said: {phrase!r}")
                evidence.append(
                    Evidence.absence(
                        f"required phrase {phrase!r} absent",
                        note=f"searched {len(utterances)} {self.actor or 'any-actor'} utterance(s)",
                    )
                )

        for phrase in self.forbidden:
            evaluated += 1
            hits = [e for e in utterances if self._matches(str(e.get("text", "")), phrase)]
            if not hits:
                satisfied += 1
            else:
                violations.append(f"forbidden phrase said {len(hits)}x: {phrase!r}")
                evidence.extend(
                    Evidence.from_event(e, note=f"contains forbidden phrase {phrase!r}")
                    for e in hits
                )

        detail = f"{satisfied}/{evaluated} phrase clauses satisfied"
        if violations:
            detail += " -- " + "; ".join(violations)
        return self._result(passed=not violations, detail=detail, evidence=evidence)

    def _matches(self, text: str, phrase: str) -> bool:
        if self.regex:
            return bool(re.search(phrase, text, 0 if self.case_sensitive else re.IGNORECASE))
        if self.case_sensitive:
            return phrase in text
        return phrase.lower() in text.lower()
