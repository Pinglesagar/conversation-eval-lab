"""The scorer — the system under test that actually matters.

WHAT THIS MODULE IS
-------------------
The second half of a sales-roleplay product: after the trainee has finished
talking to the AI customer, something grades the performance against a rubric and
writes feedback. Both outputs are customer-facing. A wrong number goes on a
manager's dashboard and into a certification decision; a wrong sentence tells a
salesperson they did something they did not do.

`RubricScorer` is a pure function of a `SessionView`, which is a pure function of
a `Trace`. That chain is not decoration — it is the repo's central invariant
applied to a second domain: every score, claim and sentence this module produces
is recomputable from the trace on disk, so a disputed grade can be reopened
months later without re-running the session.

THE THREE SEEDED DEFECTS LIVE HERE
----------------------------------
All of them, on purpose. The customer persona in `roleplay.persona` is clean, so
any finding is attributable to this file. They are documented in exactly one
place, `roleplay/SEEDED_DEFECTS.md`, and nothing in `lab/` knows they exist.

They are real code paths, not switches: no flag, no injected fault, no random
seed. Each one is a plausible decision made for a plausible reason, and each
leaves output that reads as competent.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not call a model. A real implementation would send the transcript to an
LLM with a rubric prompt, and the interesting failures — a score that moves on
identical input, prose that describes a conversation that did not happen, a
compliance criterion satisfied by vocabulary rather than by fact — are all
reproducible without one. Substituting deterministic code for the model keeps the
pack runnable with zero API keys while leaving every *check* pointed at exactly
the output shape a model would produce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from lab.trace.schema import EventKind, Trace

from roleplay.persona import classify_trainee_turn

__all__ = [
    "CRITERIA",
    "MAX_PER_CRITERION",
    "PASS_TOTAL",
    "SCORER_AGENT",
    "CUSTOMER_AGENT",
    "SessionView",
    "ScoreCard",
    "RubricScorer",
    "session_view",
]

#: The agent names written into `agent_utterance` payloads. Checks filter on
#: these, so they are constants rather than string literals scattered about.
CUSTOMER_AGENT: str = "CustomerPersona"
SCORER_AGENT: str = "Scorer"

#: The rubric. Five criteria, each scored 0-4, and the order is the order they
#: are reported in — a rubric whose criteria move around between releases cannot
#: be trended.
CRITERIA: tuple[str, ...] = (
    "discovery",
    "objection_handling",
    "mandatory_disclosure",
    "no_unlicensed_advice",
    "closing",
)

MAX_PER_CRITERION: int = 4

#: Total needed to pass, out of 20. 14/20 is 70%, the conventional certification
#: bar. It is a module constant rather than a magic number inside the verdict so
#: that a report can print the threshold next to the score.
PASS_TOTAL: int = 14

#: ------------------------------------------------------------------ DEFECT-3
#: The compliance keyword list. See SEEDED_DEFECTS.md: the mandatory-disclosure
#: criterion is scored on the presence of these words in the trainee's speech
#: instead of on the disclosure register, so "there is no real risk to your
#: capital here" scores full marks on a session where the register is empty.
_COMPLIANCE_KEYWORDS: tuple[str, ...] = (
    "risk",
    "capital",
    "past performance",
    "value can go",
    "charge",
    "fee",
)

#: ------------------------------------------------------------------ DEFECT-3
#: The unlicensed-advice blocklist, as short as it looks. It catches the two
#: phrasings someone thought of while writing the criterion and misses every
#: ordinary way a salesperson makes a personal recommendation.
_ADVICE_BLOCKLIST: tuple[str, ...] = (
    r"\bguaranteed returns?\b",
    r"\byou will definitely make\b",
)

_SUMMARY_MARKERS: tuple[str, ...] = (
    r"\bto summarise\b",
    r"\bto summarize\b",
    r"\bto recap\b",
    r"\bjust to confirm\b",
    r"\bwhere we(?:'ve| have) got to\b",
)


# --------------------------------------------------------------------------- #
# The view the scorer grades
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SessionView:
    """Everything the scorer is allowed to see, read off one trace.

    A named projection rather than the raw trace, because "what did the scorer
    have access to" is a question that comes up the first time a grade is
    disputed, and the answer should be a type rather than an argument.

    Note what is present and unused. `disclosures` and `compliance_flags` are
    both in the view; the scorer reads neither. That is the seeded defect made
    visible at the type level: the information was available and the criterion
    was computed some other way.
    """

    trainee_turns: tuple[str, ...] = ()
    customer_turns: tuple[str, ...] = ()
    disclosures: tuple[dict[str, Any], ...] = ()
    compliance_flags: tuple[dict[str, Any], ...] = ()
    objections_raised: tuple[dict[str, Any], ...] = ()
    objections_resolved: tuple[dict[str, Any], ...] = ()
    jurisdiction: str = "eu-retail"
    language: str = "en"

    @property
    def disclosed_codes(self) -> tuple[str, ...]:
        return tuple(str(d.get("code")) for d in self.disclosures)

    def turn_kinds(self) -> tuple[str, ...]:
        return tuple(classify_trainee_turn(t) for t in self.trainee_turns)


def session_view(trace: Trace) -> SessionView:
    """Project a trace onto what the scorer grades.

    Reads only trace events. Nothing is carried in from the session object that
    produced it, which is what makes a stored trace a complete and sufficient
    input — the property that lets `roleplay.calibration` score the scorer
    against hand-labelled transcripts it never ran.
    """
    trainee: list[str] = []
    customer: list[str] = []
    disclosures: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    raised: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    jurisdiction = "eu-retail"
    language = "en"

    for event in trace.events:
        if event.kind == EventKind.CALLER_UTTERANCE:
            trainee.append(str(event.get("text", "")))
        elif event.kind == EventKind.AGENT_UTTERANCE and event.get("agent") == CUSTOMER_AGENT:
            customer.append(str(event.get("text", "")))
        elif event.kind == EventKind.TOOL_CALL:
            name = event.get("name")
            args = dict(event.get("args", {}) or {})
            if name == "record_disclosure":
                disclosures.append(args)
            elif name == "flag_compliance_risk":
                flags.append(args)
            elif name == "raise_objection":
                raised.append(args)
            elif name == "resolve_objection":
                resolved.append(args)
            elif name == "load_customer_profile":
                jurisdiction = str(args.get("jurisdiction", jurisdiction))
                language = str(args.get("language", language))

    return SessionView(
        trainee_turns=tuple(trainee),
        customer_turns=tuple(customer),
        disclosures=tuple(disclosures),
        compliance_flags=tuple(flags),
        objections_raised=tuple(raised),
        objections_resolved=tuple(resolved),
        jurisdiction=jurisdiction,
        language=language,
    )


# --------------------------------------------------------------------------- #
# The output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoreCard:
    """One graded session: the numbers, the claims, and the prose.

    `claims` is separated from `criteria` deliberately. A criterion is an opinion
    with a number attached; a claim is an assertion about something that either
    happened or did not — "the mandatory disclosure was given", "no unlicensed
    advice occurred". Only the second kind can be checked against the rest of the
    trace, and keeping them in different fields is what lets
    `roleplay.contracts.ScoreClaimContract` be written at all.
    """

    criteria: dict[str, int]
    raw_total: int
    adjustment: int
    total: int
    max_total: int
    verdict: Literal["pass", "fail"]
    claims: dict[str, Any]
    feedback: str
    cohort_size: int

    @property
    def percent(self) -> float:
        return round(100.0 * self.total / self.max_total, 1) if self.max_total else 0.0

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def tool_args(self) -> dict[str, Any]:
        """The `score_session` tool arguments, flat enough to assert on.

        Flat because `lab.checks.ArgPredicate` addresses one argument by name. A
        nested blob would push every compliance assertion into bespoke Python and
        out of the declarative scenario layer, which is where a domain expert can
        read it.
        """
        return {
            "criteria": dict(self.criteria),
            "raw_total": self.raw_total,
            "adjustment": self.adjustment,
            "total": self.total,
            "max_total": self.max_total,
            "percent": self.percent,
            "verdict": self.verdict,
            "cohort_size": self.cohort_size,
            **self.claims,
        }

    def summary_line(self) -> str:
        breakdown = ", ".join(f"{k}={self.criteria[k]}" for k in CRITERIA)
        return (
            f"{self.verdict.upper()} {self.total}/{self.max_total} ({self.percent}%) "
            f"[threshold {PASS_TOTAL}] raw={self.raw_total} adj={self.adjustment:+d} "
            f"-- {breakdown}"
        )


# --------------------------------------------------------------------------- #
# The scorer
# --------------------------------------------------------------------------- #


@dataclass
class RubricScorer:
    """Grades a session against the rubric and writes the feedback.

    One instance per *deployed service*, not one per session — which is how a
    real scoring service is shaped, and which is what makes DEFECT-1 reachable.
    See `roleplay.consistency` for why a harness that constructs a fresh scorer
    for every repeat cannot see it.

    Attributes:
        curve_target_pass_rate: The pass rate the cohort curve steers towards.
        curve_window: How many recent sessions the curve looks at.
        curve_limit: How far the curve may move a total, in either direction.
        history: Recent verdicts, newest last. Cross-session state.
        adjustment: The live curve offset. Cross-session state.
    """

    curve_target_pass_rate: float = 0.6
    curve_window: int = 5
    curve_limit: int = 4
    history: list[bool] = field(default_factory=list)
    adjustment: int = 0

    # ------------------------------------------------------- public interface

    def score_trace(self, trace: Trace) -> ScoreCard:
        """Grade a session from its trace."""
        return self.score(session_view(trace))

    def score(self, view: SessionView) -> ScoreCard:
        """Grade a session view.

        The order here is the order the defects compose in: the criteria are
        computed (two of them wrongly), the cohort curve moves the total, the
        verdict is read off the curved total, and the feedback is written from
        the criteria rather than from the conversation.
        """
        criteria = {
            "discovery": self._discovery(view),
            "objection_handling": self._objection_handling(view),
            "mandatory_disclosure": self._mandatory_disclosure(view),
            "no_unlicensed_advice": self._no_unlicensed_advice(view),
            "closing": self._closing(view),
        }
        raw_total = sum(criteria.values())
        max_total = MAX_PER_CRITERION * len(CRITERIA)

        # ------------------------------------------------------------ DEFECT-1
        # The cohort curve. `self.adjustment` was computed from *previous*
        # sessions and is applied to this one, so an identical transcript scores
        # differently depending on what the service graded before it.
        adjustment = self.adjustment
        total = max(0, min(max_total, raw_total + adjustment))
        verdict: Literal["pass", "fail"] = "pass" if total >= PASS_TOTAL else "fail"

        cohort_size = len(self.history)
        self._update_curve(verdict == "pass")

        claims = {
            "mandatory_disclosure_given": criteria["mandatory_disclosure"] >= 3,
            "unlicensed_advice_detected": criteria["no_unlicensed_advice"] < 4,
            "jurisdiction": view.jurisdiction,
            "language": view.language,
        }
        card = ScoreCard(
            criteria=criteria,
            raw_total=raw_total,
            adjustment=adjustment,
            total=total,
            max_total=max_total,
            verdict=verdict,
            claims=claims,
            feedback="",
            cohort_size=cohort_size,
        )
        return replace(card, feedback=self._feedback(card, view))

    def reset(self) -> None:
        """Clear the cross-session state. The one-line fix for DEFECT-1, kept as
        a method so a test can demonstrate the fix without editing the scorer."""
        self.history.clear()
        self.adjustment = 0

    # ------------------------------------------------------------- the curve

    def _update_curve(self, passed: bool) -> None:
        """Steer the recent pass rate towards the target.

        Written to stop grade inflation drifting a cohort's certification rate,
        which is a real thing coaching products do and a defensible product
        requirement. The defect is not the idea; it is that the correction is
        applied to *individual* scores and the state is per service rather than
        per cohort, so the same performance is graded differently depending on
        its position in the queue.
        """
        self.history.append(passed)
        window = self.history[-self.curve_window :]
        rate = sum(1 for p in window if p) / len(window)
        if rate > self.curve_target_pass_rate:
            self.adjustment = max(-self.curve_limit, self.adjustment - 1)
        elif rate < self.curve_target_pass_rate:
            self.adjustment = min(self.curve_limit, self.adjustment + 1)

    # ---------------------------------------------------------- the criteria

    def _discovery(self, view: SessionView) -> int:
        """Open probes, banded. Correct: this criterion has no seeded defect."""
        probes = sum(1 for kind in view.turn_kinds() if kind == "open_probe")
        return {0: 0, 1: 2, 2: 3}.get(probes, 4)

    def _objection_handling(self, view: SessionView) -> int:
        """Share of raised objections that were resolved.

        Correct, and it reads the objection ledger rather than the transcript —
        which makes the contrast with `_mandatory_disclosure` below the point of
        this file: the same product does the same job properly one method up.

        Counted over *distinct* objection keys, not over ledger rows. A combative
        customer re-raises an objection the trainee ignored, and the ledger
        records that second raise because it happened — so counting rows would
        make an unhandled objection look like two, and the score would fall the
        more insistent the customer became rather than the less the trainee said.
        The denominator has to be "how many things were objected to".
        """
        raised = {str(o.get("key")) for o in view.objections_raised}
        if not raised:
            return 3
        resolved = {str(o.get("key")) for o in view.objections_resolved} & raised
        return max(
            0, min(MAX_PER_CRITERION, round(MAX_PER_CRITERION * len(resolved) / len(raised)))
        )

    def _mandatory_disclosure(self, view: SessionView) -> int:
        """--- DEFECT-3 --- Keyword presence, not the disclosure register.

        `view.disclosures` is right there and is never read. The keywords are
        English-only, which is the same defect wearing a second hat: a session
        conducted in Spanish scores zero on a criterion the trainee satisfied.
        """
        haystack = " ".join(view.trainee_turns).lower()
        hits = sum(1 for word in _COMPLIANCE_KEYWORDS if word in haystack)
        return {0: 0, 1: 2, 2: 3}.get(hits, 4)

    def _no_unlicensed_advice(self, view: SessionView) -> int:
        """--- DEFECT-3 --- A two-entry blocklist, not the live compliance flags.

        `view.compliance_flags` holds what the product's own in-session flagger
        already detected, and this method ignores it in favour of a regex list
        that covers neither "you should move your pension into this" nor "I'd put
        my own mother in it".
        """
        haystack = " ".join(view.trainee_turns)
        if any(re.search(p, haystack, re.IGNORECASE) for p in _ADVICE_BLOCKLIST):
            return 0
        return MAX_PER_CRITERION

    def _closing(self, view: SessionView) -> int:
        """Asked for the business, and whether a summary preceded it."""
        kinds = view.turn_kinds()
        if not any(k == "close_attempt" for k in kinds):
            return 0
        haystack = " ".join(view.trainee_turns)
        summarised = any(re.search(p, haystack, re.IGNORECASE) for p in _SUMMARY_MARKERS)
        return 4 if summarised else 3

    # ---------------------------------------------------------- the feedback

    def _feedback(self, card: ScoreCard, view: SessionView) -> str:
        """--- DEFECT-2 --- Prose assembled from rubric templates.

        The templates are keyed on the *score*, not on the transcript, so a
        criterion that scored well produces a sentence describing the thing that
        criterion usually rewards — including, in the discovery branch, a
        verbatim exemplar question in quotation marks that the trainee never
        asked. The objection branch names the fee objection because fees are the
        objection the template was written against, whatever the customer
        actually raised.

        Why this is plausible: templated feedback is cheaper, faster and far more
        consistent than generated prose, and the exemplar was put in quotes so
        the trainee could see what a good question looks like. Somewhere between
        the design and the copy, the exemplar stopped being an illustration and
        started being a quotation.
        """
        lines: list[str] = []
        criteria = card.criteria

        if criteria["discovery"] >= 3:
            lines.append(
                'You opened well - asking "what would you want this money to be doing '
                'for you in ten years?" gave you the horizon to work with.'
            )
        elif criteria["discovery"] <= 1:
            lines.append(
                "You went into the product before you understood the customer. "
                "Open the next one with a question, not a feature."
            )

        if criteria["objection_handling"] <= 1:
            lines.append(
                "You left the fee objection unanswered - the customer raised cost "
                "and you moved past it."
            )
        elif criteria["objection_handling"] >= 3:
            lines.append("You took the objection head on rather than deflecting it.")

        if criteria["mandatory_disclosure"] >= 3:
            lines.append("The mandatory risk disclosure was given and is recorded.")
        else:
            lines.append(
                "The mandatory risk disclosure is missing. On its own that fails the session."
            )

        if criteria["no_unlicensed_advice"] == MAX_PER_CRITERION:
            lines.append("Nothing you said crossed into personal advice.")
        else:
            lines.append("You crossed into personal advice. Stay on suitability.")

        lines.append(
            "You asked for the business."
            if criteria["closing"] >= 3
            else "You never asked for the business."
        )
        return " ".join(lines)

    def __repr__(self) -> str:
        return (
            f"RubricScorer(sessions_graded={len(self.history)}, "
            f"adjustment={self.adjustment:+d}, "
            f"target_pass_rate={self.curve_target_pass_rate})"
        )
