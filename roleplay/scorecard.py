"""The behavioural scorecard: what the adviser is judged on, as data.

WHAT THIS MODULE IS
-------------------
`roleplay/rubric_v1.md` grades five criteria out of four and totals out of twenty.
That is enough to demonstrate a scoring product and not enough to certify a
person. This module is the scorecard the domain research actually supports:
twenty-eight KPIs in seven groups, each one an *observable behaviour* with a named
detector, a stated denominator, and the business metric it is a leading indicator
for.

It extends `rubric_v1` rather than replacing it. `RUBRIC_V1_SUCCESSORS` maps every
one of the five original criteria onto the KPIs that inherited it, and a test
asserts the map is total — so nothing the original rubric graded has quietly
fallen off the end.

THE LADDER, WHICH IS THE WHOLE ARGUMENT
---------------------------------------
The product category this repo models sells on business outcomes: call conversion,
product penetration, positioning and readiness, time-to-first-sale, active ratio.
A coaching platform cannot move any of those directly. It can only change what an
adviser does on a call. So every KPI here is written as

    observable behaviour -> the business metric it is a leading indicator for

and a KPI that cannot complete that sentence does not belong in the registry.
`business_metric` is therefore a required field over a closed vocabulary, and
`_validate` refuses a KPI without one.

Every one of those links is a design hypothesis rather than a measured causal
fact, and the research says so in both files: `docs/_research/call_craft.md` A-01
and `docs/_research/regulators.md` §10 item 9. Nobody in the sources ran an
experiment showing that stating the reason for the call raises product
penetration. What is claimed is plausibility of mechanism plus availability of
measurement — which is why `BUSINESS_METRICS` carries that caveat in its own
docstring rather than in a footnote nobody reads.

GATES ARE COUNTED, NEVER AVERAGED
---------------------------------
A compliance requirement that can be traded against a discovery score is not a
requirement. The structural enforcement is that `max_points` is zero for every
GATE, so `points_available` cannot include one by construction — there is no
filter to forget. Gates are reported as a second figure beside the score, never
folded into it.

There is a third value, `DIAGNOSTIC`, and it is not a hedge. It marks a
measurement that bears on the *instrument* rather than on the adviser: this
session's word error rate, its code-mixing band, the text-only control run. Those
must be published beside every score and must never move a certification, because
an adviser whose accent the speech engine handles worse is not a worse adviser.
See `docs/SCORECARD.md` §3.

SOURCING
--------
Every KPI carries `basis` (citations into `docs/_research/`, which carry the
primary references and a verification level V1-V4) or `assumptions` (labelled
inferences), and `_validate` requires at least one of the two. There is no third
category. A KPI asserting a disclosure requirement with neither is an import-time
error, which is the strongest place to put that rule.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not detect anything. It is the registry plus the arithmetic that turns
per-KPI outcomes into a session verdict. The detectors it names live in
`lab/checks` (deterministic contracts) and `lab/judges` (calibrated judges), and
naming a judge here does not make it usable: `lab.judges.require_calibrated`
raises below threshold, and a judge without a committed calibration report cannot
gate anything. `Detector.requires_calibration` records that, and `_validate`
requires every judge-detected GATE to name a deterministic fallback — because a
gate that cannot run until someone calibrates a judge is a gate that silently does
not run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

__all__ = [
    "BUSINESS_METRICS",
    "DETECTOR_KINDS",
    "GROUPS",
    "KPIS",
    "PASS_FRACTION",
    "RUBRIC_V1_SUCCESSORS",
    "Detector",
    "Group",
    "KPI",
    "KPIOutcome",
    "SessionScore",
    "by_group",
    "by_id",
    "gates",
    "points_available",
    "score_session",
    "scored",
]


Disposition = Literal["GATE", "SCORE", "DIAGNOSTIC"]
DetectorKind = Literal["contract", "judge", "ledger", "measurement"]

#: What a detector can be, and what each kind costs to trust.
#:
#: The distinction is not taxonomy for its own sake. A `contract` is a
#: deterministic function of the trace and is as trustworthy as its patterns — see
#: `tests/test_checks_paraphrase.py` for how untrustworthy a literal pattern list
#: turns out to be against a paraphrasing model. A `ledger` reads events the
#: product itself recorded, which is the strongest evidence available here because
#: it is not an interpretation of speech at all. A `judge` needs a committed
#: calibration report before it may be believed. A `measurement` is an instrument
#: reading and grades nobody.
DETECTOR_KINDS: dict[str, str] = {
    "contract": "a deterministic contract from lab/checks; a pure function of the Trace",
    "judge": "an LLM judge from lab/judges; unusable until its calibration report clears the gate",
    "ledger": "a read of events the product itself recorded (a register, a flag, a tool call)",
    "measurement": "an instrument reading (word error rate, code-mixing band); grades the harness, not the adviser",
}

#: The business metrics this product category is sold on, plus the one that is not
#: a growth metric at all.
#:
#: Closed vocabulary on purpose. A KPI whose leading indicator is "quality" is a
#: KPI nobody will defend in a QBR, and a *gate* whose stated business metric is
#: conversion is a gate somebody will eventually trade away — so the gates point
#: at `licence_to_operate`, which is deliberately not a growth number.
#:
#: Every mapping from a behaviour in `KPIS` to a metric here is a hypothesis, not
#: a finding: no regulator publishes conversion data and none of the sales-research
#: sources ran the experiment. See call_craft.md A-01 and regulators.md §10(9).
BUSINESS_METRICS: dict[str, str] = {
    "call_conversion": "call conversion — the vendor's headline growth number",
    "product_penetration": "product penetration; products per relationship",
    "positioning_readiness": "sales positioning and readiness, as scored by the platform itself",
    "time_to_first_sale": "time from onboarding to an adviser's first sale",
    "active_ratio": "the proportion of advisers actually producing",
    "licence_to_operate": (
        "not a growth metric: mis-selling exposure, audit findings, and whether the "
        "certification decision itself is defensible"
    ),
}

#: The fraction of available points a session must reach. 0.70 is the same
#: convention as `rubric_v1`'s 14/20, kept so the two are comparable — and it is a
#: convention, not a discovery, which is why it is printed next to every verdict.
PASS_FRACTION: float = 0.70


# --------------------------------------------------------------------------- #
# The seven groups
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Group:
    """One KPI group: what it asks, and what the group as a whole ladders to."""

    id: str
    name: str
    question: str
    ladders_to: str


GROUPS: dict[str, Group] = {
    "CS": Group(
        id="CS",
        name="call survival",
        question="Did the adviser earn the right to continue?",
        ladders_to="call_conversion, time_to_first_sale, active_ratio",
    ),
    "DI": Group(
        id="DI",
        name="discovery",
        question="Was this a needs analysis or a questionnaire?",
        ladders_to="product_penetration",
    ),
    "OH": Group(
        id="OH",
        name="objection handling",
        question="Was the objection engaged with, or acknowledged and abandoned?",
        ladders_to="call_conversion",
    ),
    "CE": Group(
        id="CE",
        name="clause explanation",
        question="Was the limitation explained, recited, or understated?",
        ladders_to="product_penetration, licence_to_operate",
    ),
    "CG": Group(
        id="CG",
        name="compliance gates",
        question="Was this session lawful in this market? Not a score — a gate.",
        ladders_to="licence_to_operate",
    ),
    "CL": Group(
        id="CL",
        name="closing",
        question="Did the adviser ask for the business, and was the ask honest?",
        ladders_to="call_conversion",
    ),
    "LL": Group(
        id="LL",
        name="language and locale",
        question="Does any of the above survive a change of language and market?",
        ladders_to="call_conversion, positioning_readiness, licence_to_operate",
    ),
}


# --------------------------------------------------------------------------- #
# The KPI record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Detector:
    """How a KPI is decided, and what it costs to believe the answer."""

    kind: DetectorKind
    name: str
    note: str
    #: True exactly when `kind == "judge"`. Redundant with `kind` by design: this
    #: is the field pipeline code reads, and a reader of the registry should not
    #: have to know that "judge" implies "not yet usable".
    requires_calibration: bool = False
    #: A deterministic detector that runs when the judge cannot. Required on any
    #: GATE whose primary detector is a judge.
    fallback: str | None = None

    def describe(self) -> str:
        suffix = " (uncalibrated: cannot gate)" if self.requires_calibration else ""
        return f"{self.kind}:{self.name}{suffix}"


@dataclass(frozen=True)
class KPI:
    """One thing an adviser is judged on.

    `max_points` is the structural half of "gates are never averaged": it is zero
    for anything that is not a SCORE, so a total computed by summing `max_points`
    cannot include a gate even if the caller forgets to filter.

    `denominator` and `excludes` are both required and both non-empty. This repo
    treats a naked percentage as a defect, and a rate whose exclusions are
    undeclared is the same defect wearing a denominator.
    """

    id: str
    group: str
    question: str
    evidence: str
    detector: Detector
    scale: str
    max_points: int
    gate_or_score: Disposition
    business_metric: str
    denominator: str
    excludes: str
    basis: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    note: str = ""

    @property
    def is_gate(self) -> bool:
        return self.gate_or_score == "GATE"

    @property
    def is_scored(self) -> bool:
        return self.gate_or_score == "SCORE"

    def summary_line(self) -> str:
        return (
            f"{self.id} [{self.gate_or_score}] {self.question} "
            f"-- {self.detector.describe()} -- leads {self.business_metric}"
        )


# --------------------------------------------------------------------------- #
# CS — call survival
# --------------------------------------------------------------------------- #

_CS = (
    KPI(
        id="CS-1",
        group="CS",
        question="Did the adviser say what the call was for before asking the customer for anything?",
        evidence=(
            "A purpose clause in an adviser turn positioned before the first elicitation "
            "or product turn. Position in the event stream, not elapsed time."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract",
            note="positional ordering; the contract already decides on position rather than timestamp",
        ),
        scale="binary, 2 points",
        max_points=2,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="the session (n=1); not a rate",
        excludes=(
            "sessions with no adviser turn before the customer's first substantive turn — "
            "a wrong number or an immediate transfer is not a failed opener"
        ),
        basis=("call_craft.md S-05 (R1, V2): stating the reason for the call, 2.1x success rate",),
        assumptions=("call_craft.md A-02: a B2B technology-sales corpus transferred to regulated retail advisory",),
    ),
    KPI(
        id="CS-2",
        group="CS",
        question="Did the adviser ask permission to continue, bound the time, and name one specific next step?",
        evidence=(
            "An explicit yes/no request to continue within the first few adviser turns; a "
            "stated duration or turn ceiling in the same turn as the ask; and an ask naming "
            "one small next step rather than an open commitment."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract",
            note="positional window plus the content of the ask itself, not sentiment",
        ),
        scale="0-3 points: asked / asked and bounded / asked, bounded and single named step",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="the session (n=1); not a rate",
        excludes="sessions that end before the adviser's second turn",
        basis=(
            "call_craft.md S-08 (R5, V3): across 153 cold calls, persuasive conduct was "
            "pre-expansion plus minimising the imposition of the request",
        ),
        assumptions=(
            "call_craft.md A-03: that minimising the imposition (craft) is separable from "
            "securing alignment before disclosing the purpose (pressure). The same paper "
            "supplies both signals and evaluates neither.",
        ),
        note=(
            "Scored on whether the ask was actually smaller — one named step — not on whether "
            "the words 'just two minutes' appeared. See SCORECARD.md §2 for why."
        ),
    ),
    KPI(
        id="CS-3",
        group="CS",
        question="When the customer resisted, did the adviser answer the kind of resistance it actually was?",
        evidence=(
            "A customer turn carrying block features (turn-initial delay, an account for not "
            "answering, a second unit moving to end the call) or stall features (a hedge plus "
            "a deferring counter-proposal: 'email it', 'call me next month', 'I'll discuss "
            "internally'). The next adviser turn either narrows and re-specifies the immediate "
            "request so it is separable from the sale (block) or declines the deferring "
            "trajectory and names a nearer concrete alternative (stall)."
        ),
        detector=Detector(
            kind="judge",
            name="resistance_response",
            note=(
                "three labels: answered-in-kind / answered-as-the-other-kind / not answered. "
                "The two failure modes have deterministic floors, which is what runs today."
            ),
            requires_calibration=True,
            fallback=(
                "lab.checks.NoReAskContract for the block case (an identical re-ask after an "
                "account for not answering) and lab.checks.NoProgressContract for the stall "
                "case (a deferral accepted with no counter-proposal is literally a turn that "
                "produced no progress toward the goal state)"
            ),
        ),
        scale="0-3 points per resistance event, averaged over events",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="active_ratio",
        denominator="resistance events in the session (blocks plus stalls)",
        excludes=(
            "customer turns that are questions rather than resistance; and sessions with zero "
            "resistance events, reported as 0/0 and never as a full score"
        ),
        basis=(
            "call_craft.md S-09 to S-16 (R4, V1, read in full text): 159 transcribed cold calls, "
            "the block/stall taxonomy, and the salesperson counter-moves the paper's extracts show",
        ),
        assumptions=(
            "call_craft.md A-05: that offering a genuine exit is conversion-positive and not "
            "merely honest — the single most important assumption in that file to test",
            "call_craft.md A-07: that the block/stall taxonomy transfers from B2B organisational "
            "calls to retail calls to individuals",
        ),
    ),
    KPI(
        id="CS-4",
        group="CS",
        question="Did the adviser avoid inviting the customer to name the call as an imposition?",
        evidence="Absence of 'is this a bad time?'-class phrasing in the adviser's opening turns.",
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract",
            note="an absence check over a positional window",
        ),
        scale="binary, 1 point",
        max_points=1,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="the session (n=1); not a rate",
        excludes="sessions with no adviser opening turn",
        basis=(
            "call_craft.md S-03 (R1, V2) and S-06 (R2, V3): the same vendor publishes 0.9% and "
            "2.15% for the same opener on two different corpora, neither stating its denominator",
        ),
        assumptions=("call_craft.md A-04: no survival curve by elapsed seconds was located for advisory calls",),
        note=(
            "Worth one point, deliberately. The direction is consistent across both publications "
            "and the effect size is unpublishable, so this KPI carries the direction and refuses "
            "to carry a threshold."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# DI — discovery
# --------------------------------------------------------------------------- #

_DI = (
    KPI(
        id="DI-1",
        group="DI",
        question="How much of the fact-find this market requires was actually elicited?",
        evidence=(
            "Fields present in the elicitation ledger against the register's required field set "
            "for this jurisdiction. Elicited, not mentioned: a field the adviser named and moved "
            "past is not a field the adviser obtained."
        ),
        detector=Detector(
            kind="ledger",
            name="roleplay.register required-field set + lab.checks.ToolContract",
            note="a ledger read, so the numerator is events the product recorded rather than a reading of speech",
        ),
        scale="rate, banded to 4 points",
        max_points=4,
        gate_or_score="SCORE",
        business_metric="product_penetration",
        denominator=(
            "fields the jurisdiction's fact-find register requires — the FCA COBS 9A.2.6R to "
            "9A.2.8R field set, or the MAS know-your-client checklist for an APAC session"
        ),
        excludes=(
            "fields the customer refused to answer, which are recorded as refused and removed "
            "from the denominator: a refusal is the customer's behaviour, not the adviser's"
        ),
        basis=(
            "call_craft.md S-38, S-39, S-40 (R10, V2): COBS 9A.2.6R knowledge and experience, "
            "9A.2.7R financial situation, 9A.2.8R objectives",
            "call_craft.md S-43 (R17, V4): FAA s.36 reasonable basis and FAA-N16's KYC set — "
            "MAS primary text was unreachable, so this is commentary standing in for a notice",
            "regulators.md §9: MAS-3 ¶11's nine-item checklist, which is already a scoring rubric",
        ),
    ),
    KPI(
        id="DI-2",
        group="DI",
        question="Did the adviser use the answers, or just collect them?",
        evidence=(
            "A later adviser turn carrying the content of an earlier answer — the stated goal, "
            "the stated horizon, the stated constraint. Asking and then ignoring is the defining "
            "move of performed discovery."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.FieldPropagationContract",
            note=(
                "the primitive already exists for exactly this defect class: a value obtained and "
                "never propagated into the outcome"
            ),
        ),
        scale="rate, banded to 4 points",
        max_points=4,
        gate_or_score="SCORE",
        business_metric="product_penetration",
        denominator="answers the customer actually gave (elicited fields with a non-refusal answer)",
        excludes=(
            "answers given in the final two turns, which had no later adviser turn to propagate "
            "into; counting them would score the adviser for the session's length"
        ),
        basis=("call_craft.md §6.3 signal 2: answer uptake, named there as a propagation property",),
        note="The hardest KPI in this registry to game, and that is why it carries four points.",
    ),
    KPI(
        id="DI-3",
        group="DI",
        question="Did any question build on an answer, rather than advance a fixed list?",
        evidence="An adviser question containing a term the customer introduced.",
        detector=Detector(
            kind="contract",
            name="lab.checks.text.surface_forms + question_key",
            note="deterministic term overlap between an adviser question and the customer's prior turns",
        ),
        scale="0-2 points, banded on the count of such questions",
        max_points=2,
        gate_or_score="SCORE",
        business_metric="product_penetration",
        denominator="adviser questions in the session",
        excludes=(
            "questions repeating a term the adviser introduced first; and confirmations, which "
            "echo by construction"
        ),
        basis=(
            "call_craft.md S-47 (R7, V3): the discriminator between high and average performers "
            "was the type of question — implication and need-payoff — not the quantity",
            "call_craft.md S-48 (R7, V3): the same programme found the open-versus-closed "
            "distinction had no measurable effect on outcomes, which is why this KPI replaced "
            "rubric_v1's open-question count rather than sitting beside it",
        ),
        assumptions=(
            "call_craft.md A-17: 'contains a term the customer introduced' is my operationalisation "
            "of an implication question and will accept a shallow echo",
            "call_craft.md A-19: Rackham's dataset appears never to have been peer-reviewed or "
            "replicated — and call_craft.md §12 states the book itself was not read",
        ),
    ),
    KPI(
        id="DI-4",
        group="DI",
        question="Was the fact-find neither steered away from nor bypassed?",
        evidence=(
            "Two outcomes on one gate. Steering: a required field reframed as optional in an "
            "adviser turn ('we can skip that one'). Bypass: a recommendation turn occurring while "
            "register fields are still unelicited."
        ),
        detector=Detector(
            kind="judge",
            name="fact_find_steering",
            note="the steering outcome is a judgement about intent in a turn; the bypass outcome is not",
            requires_calibration=True,
            fallback=(
                "deterministic ordering check: a turn classified as advice by "
                "roleplay.persona.classify_trainee_turn positioned before register completeness. "
                "Needs no oracle, and it is the outcome with the rule number."
            ),
        ),
        scale="gate: pass or fail, per outcome",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator=(
            "one gate, applicable when the session reaches a recommendation. Gates are counted, "
            "not averaged: this session's figure is gates passed over gates applicable"
        ),
        excludes=(
            "sessions that never reach a recommendation — the gate is not applicable and is "
            "removed from both numerator and denominator rather than passed by default"
        ),
        basis=(
            "call_craft.md S-41 (R10, V2): COBS 9A.2.13R — without the required information the "
            "firm must not recommend",
            "call_craft.md S-42 (R10, V2): COBS 9A.2.11R — a firm must not encourage a client not "
            "to provide the information",
        ),
        note=(
            "The commonest commercial shortcut in existence has a rule number, which is what makes "
            "'impatient customer, adviser shortens the fact-find' a gate rather than a matter of taste."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# OH — objection handling
# --------------------------------------------------------------------------- #

_OH = (
    KPI(
        id="OH-1",
        group="OH",
        question="How often did the customer have to raise the same objection twice?",
        evidence=(
            "A distinct objection key raised a second time in the customer's own turns. The "
            "customer's behaviour scores the adviser's answer, which is why this needs no oracle: "
            "a repetition is evidence the first answer did not land, independent of any judgement "
            "about its quality."
        ),
        detector=Detector(
            kind="ledger",
            name="objection ledger repetition test",
            note="a repetition test on the event stream; fully deterministic",
        ),
        scale="rate, inverted, banded to 4 points",
        max_points=4,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator=(
            "distinct objection keys raised, not ledger rows — roleplay/scorer.py already counts "
            "distinct keys for this reason, because counting rows makes one unhandled objection "
            "look like two and the score then falls as the customer becomes more insistent"
        ),
        excludes=(
            "sessions with no objection raised, reported as 0/0 and never as a perfect score. And "
            "the rate is never pooled across personas: an aggressive persona re-raises more by "
            "construction, so pooling measures the persona mix"
        ),
        basis=(
            "call_craft.md S-16 and S-19 (R4, V1): a partial repetition of the recipient's own "
            "prior talk produces escalated disaffiliation and marks the intervening move as inapposite",
        ),
        note=(
            "The best single metric in this group, and the cohort artefact the manager-analytics "
            "surface actually sells: second-raise rate by objection category, with its denominator."
        ),
    ),
    KPI(
        id="OH-2",
        group="OH",
        question="Was each objection engaged with, or acknowledged and abandoned?",
        evidence=(
            "Engaged: within a few turns of the objection, an adviser turn carrying a quantity or "
            "named term drawn from the objection's own subject matter — the actual charge for a fee "
            "objection, the actual surrender schedule for a lock-in objection, the actual commission "
            "for a trust objection — followed by a check that it landed. Abandoned: an empathy "
            "token, no objection-specific content, and a return to the pre-objection agenda position."
        ),
        detector=Detector(
            kind="judge",
            name="objection_engagement",
            note="a judge is needed for the residue the proxy cannot separate: an adviser reciting a number without engaging",
            requires_calibration=True,
            fallback=(
                "deterministic proxy: quantity-or-named-term presence in the following turns, plus "
                "lab.checks.NoProgressContract on agenda resumption"
            ),
        ),
        scale="rate over objections, banded to 4 points",
        max_points=4,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="objections raised, counted by distinct key",
        excludes="objections raised in the final turn, which had no following turn in which to be engaged",
        basis=(
            "call_craft.md §4.2: the engaged-versus-abandoned distinction, with the two detectable "
            "signals it names",
            "call_craft.md S-17 (R22, V3): the six-category objection taxonomy the coverage is "
            "measured against",
        ),
        assumptions=(
            "call_craft.md A-10: 'contains a quantity or named term from the objection's subject' "
            "proxies engagement, and its false-positive mode is the recital",
            "call_craft.md A-09: the BFSI-specific category list is that file's own assembly; there "
            "is no insurance-specific objection frequency study behind it, so this measures coverage "
            "of a taxonomy and not representativeness",
        ),
    ),
    KPI(
        id="OH-3",
        group="OH",
        question="On a deferral, did the adviser find out what it was, or just accept it?",
        evidence=(
            "A diagnostic question positioned after the deferral and before any acceptance of it: "
            "did the adviser ask which of price, trust or another decision-maker was in the way?"
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract",
            note="a positional test: diagnostic question before acceptance, both in the event stream",
        ),
        scale="0-2 points per deferral event, averaged",
        max_points=2,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="deferral events in the session",
        excludes="sessions with no deferral, reported as 0/0",
        basis=(
            "call_craft.md S-18 (R25, V3): a hidden objection is one not openly stated but still an "
            "obstacle, recognisable from trivial questions or an asserted absence of need",
            "call_craft.md S-56 (R4, V1): a stall is a deferral rather than a refusal, and the "
            "counter-move in that data is a concrete nearer alternative",
        ),
        assumptions=(
            "call_craft.md A-11: the practitioner claim that 'I need to think about it' is usually "
            "price, trust or another decision-maker is unsourced and must not appear in a scenario "
            "rationale as fact. This KPI is written so it does not depend on that claim being true: "
            "it grades whether the adviser asked, not whether the answer matched the folklore",
            "call_craft.md A-12: that diagnosing a deferral outperforms accepting it — mechanism only",
        ),
    ),
)


# --------------------------------------------------------------------------- #
# CE — clause explanation
# --------------------------------------------------------------------------- #

_CE = (
    KPI(
        id="CE-1",
        group="CE",
        question="Was understanding checked, or assumed?",
        evidence=(
            "Within a few turns of a key-information turn — one that states a limitation or a cost, "
            "or prompts the customer to make a decision — an adviser turn asking whether the "
            "customer understands and whether they have further questions."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract",
            note="a positional contract: an understanding-check turn within k turns of a key-information turn",
        ),
        scale="rate over key-information turns, banded to 3 points",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="product_penetration",
        denominator="key-information turns in the session",
        excludes=(
            "key-information turns inside the final k turns, which had no window in which a check "
            "could appear"
        ),
        basis=(
            "call_craft.md S-22 (R9, V3 for the wording, V2 for the section): FCA PRIN 2A.5.9R "
            "applies to telephone and other interactive dialogue and requires the firm to ask the "
            "retail customer whether they understand and whether they have further questions, "
            "particularly where the information prompts a decision. call_craft.md §12 flags this "
            "as load-bearing and not V1: re-fetch from the Handbook before it goes in front of anyone",
            "call_craft.md S-24 (R16, V3): where a client has little prior knowledge of a product "
            "type, the SFC requires the licensed person to provide more assistance to ensure the "
            "client understands",
        ),
        note=(
            "This is the one KPI in the registry that a regulator states as a *turn* rather than as "
            "an outcome, in exactly the interaction type the roleplay surface simulates."
        ),
    ),
    KPI(
        id="CE-2",
        group="CE",
        question="Was the clause explained, recited, or understated?",
        evidence=(
            "Three observably different behaviours. Recital: read out in product language, no "
            "translation, no worked case, no check. Genuine explanation: restated in consequence "
            "terms for this customer ('if you were diagnosed in month two this would pay nothing, "
            "and you would be relying on your savings'), then a check. Minimisation: stated and then "
            "discounted in the same or the next turn ('technically there is a waiting period, but in "
            "practice...'), with no check in between."
        ),
        detector=Detector(
            kind="judge",
            name="clause_explanation",
            note="three labels; minimisation is the highest-severity outcome and the one that passes a keyword scorer",
            requires_calibration=True,
            fallback=(
                "deterministic prefilter: a limitation turn followed within one or two turns by a "
                "minimising construction with no intervening understanding-check. Adjacency, which "
                "is what lab.checks.PhraseContract decides on."
            ),
        ),
        scale="3-level label mapped to points: minimisation 0, recital 1, genuine explanation 3",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="licence_to_operate",
        denominator="limitation turns in the session (exclusions, waiting periods, lock-ins, surrender terms)",
        excludes="turns naming a limitation only inside a quoted policy title, which is a reference and not a statement",
        basis=(
            "call_craft.md §5.3: the recital / explanation / minimisation table",
            "call_craft.md S-20 and S-21 (R9, V2): FCA PRIN 2A.5.3R, communications likely to be "
            "understood, and 2A.5.8R, tailored to characteristics including vulnerability",
        ),
        assumptions=(
            "call_craft.md A-15: the clause-explanation quality bar is that file's construction, not "
            "a quoted standard",
            "call_craft.md A-16: minimiser-adjacency will false-positive on legitimate proportionality "
            "('there is a waiting period, and for your age band the premium difference is small')",
        ),
        note=(
            "A gate in waiting. It is a SCORE today only because the detector's true negative rate is "
            "unmeasured (A-16), and this repo's rule is that an uncalibrated detector does not gate. "
            "Calibrate the TNR and this becomes the highest-severity gate in the registry."
        ),
    ),
    KPI(
        id="CE-3",
        group="CE",
        question="Was the non-guaranteed figure given more emphasis than the guaranteed one?",
        evidence=(
            "Emphasis in speech is positional and countable: which figure is said first, which is "
            "repeated, which is attached to the customer's own stated goal, whether the guaranteed "
            "figure is said at all, and whether variability was expressed as a range or as a point "
            "estimate."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract + numeric mention counting",
            note="order, repetition count and attachment; a presence check passes this failure by construction",
        ),
        scale="0-3 points",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="licence_to_operate",
        denominator="sessions in which a projected or non-guaranteed figure was quoted at all",
        excludes=(
            "sessions quoting no figures; and sessions on products with no guaranteed element, where "
            "the comparison does not exist"
        ),
        basis=(
            "call_craft.md S-30 (R15, V3): HK IA GL28 — assumed rates are neither guaranteed nor "
            "based on past performance, and insurers should not highlight non-guaranteed figures. "
            "call_craft.md §12 flags this as load-bearing and not V1",
            "call_craft.md S-31 (R15, V3): participating policies require pessimistic and optimistic "
            "projections to show the variability — the range-versus-point-estimate test",
            "call_craft.md C-2: the breach is relative emphasis, which only a position-aware check sees",
        ),
    ),
    KPI(
        id="CE-4",
        group="CE",
        question="When the customer asked what happens if they stop, did they get a recoverable value?",
        evidence=(
            "A trigger — affordability raised, income volatility mentioned, or 'what if I stop paying' "
            "asked — followed within a few turns by an adviser turn stating what is recoverable in the "
            "early years: a number, or an explicit 'substantially less than you paid in'. The failure "
            "signature is a horizon statement in its place ('it is a long-term product, you would want "
            "to keep it going')."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract",
            note="a conditional contract: a trigger event obliges specific content within a window",
        ),
        scale="0-2 points per trigger event, averaged",
        max_points=2,
        gate_or_score="SCORE",
        business_metric="licence_to_operate",
        denominator="trigger events in the session",
        excludes="sessions with no trigger, reported as 0/0",
        basis=(
            "call_craft.md S-32 (R20, V1, read in full text): insurers sell front-loaded policies, "
            "make money on lapsers and lose money on non-lapsers",
            "call_craft.md S-34 (R20, V1): almost 25% of permanent policyholders lapse within three "
            "years and 40% within ten — which is what makes 'most people keep these going' false "
            "rather than merely optimistic",
            "call_craft.md S-36 (R20, V1): households are roughly twice as likely to surrender after "
            "a spouse becomes unemployed, which is why volatility is a trigger and not a digression",
        ),
        note=(
            "The product's economics depend on a customer outcome the adviser has a duty to explain "
            "and an incentive to minimise. An adviser explaining early-surrender loss honestly is "
            "explaining how the policy makes money."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# CG — compliance gates. Every row here has max_points == 0.
# --------------------------------------------------------------------------- #

_CG = (
    KPI(
        id="CG-1",
        group="CG",
        question="Did every disclosure this market requires actually get made, in this session's language?",
        evidence=(
            "One record_disclosure event per required code, keyed by jurisdiction, code and "
            "language. The register also records, per code, which *kind* of requirement it is — "
            "verbatim, prescribed-unit, or substance — because that determines whether a miss is "
            "evidence about the adviser or evidence about the instrument."
        ),
        detector=Detector(
            kind="ledger",
            name="roleplay.register.DisclosureRegister + lab.checks.ToolContract",
            note=(
                "a ledger of recorded events, not a phrase scan of the transcript. That choice is "
                "the whole finding of tests/test_checks_paraphrase.py: a literal pattern set went "
                "blind against a paraphrasing model, so no phrase list may gate."
            ),
        ),
        scale="gate: every required code recorded, or fail",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator=(
            "codes this jurisdiction requires — three for eu-retail, four for apac-retail, three "
            "for amer-retail in the demo register. The requirement set is exactly the list, not "
            "more and not fewer"
        ),
        excludes=(
            "codes no jurisdiction requires; and a code satisfied in a language the register does "
            "not carry, which is recorded as an instrument gap and not as an adviser failure"
        ),
        basis=(
            "regulators.md §8: the four regimes split into two drafting traditions — some "
            "requirements are satisfiable only by a specific form of words (FCA COBS 4.12A verbatim "
            "warnings, the COBS 4.5A.10R past-performance sentence, the literal terms 'independent "
            "advice' / 'restricted advice') and some only by substance (SFC Schedule 9 'containing "
            "the substance', MAS ¶25(c) 'not necessarily indicative', FCA consumer understanding)",
            "regulators.md D4: 'not a reliable indicator of future results' and 'not necessarily "
            "indicative of future performance' carry the same meaning and share almost no tokens, so "
            "a substring register keyed on the UK phrasing records zero disclosures in a correctly "
            "conducted Singapore session",
        ),
        note=(
            "roleplay/register.py already carries KEYWORD_SHADOW_TERMS as a committed control, so the "
            "gap between the register and a keyword check is a measured number in this repo rather "
            "than an assertion."
        ),
    ),
    KPI(
        id="CG-2",
        group="CG",
        question="Were the disclosures delivered in the required order, and where required, simultaneously?",
        evidence=(
            "A-before-B tests on the utterance and artefact sequence: written disclosure of "
            "relationship, fees and conflicts prior to or at the time of the recommendation; the "
            "charging structure in writing in good time before the personal recommendation; the "
            "suitability report before the transaction is concluded; the recommendation document "
            "before the client signs. Two MAS requirements are simultaneity rather than sequence: "
            "an oral past-performance statement is permitted only if the written disclosure is "
            "provided at the same time."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract + lab.checks.Ordering",
            note="position in the event stream, not timestamps — none of these requirements needs a clock",
        ),
        scale="gate: every applicable ordering requirement satisfied, or fail",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator="ordering requirements applicable to this jurisdiction and product",
        excludes=(
            "requirements whose trigger never occurred in the session — a suitability-report "
            "ordering rule is not applicable to a session that reached no recommendation"
        ),
        basis=(
            "regulators.md §7 rows 1-24, each with a paragraph-level citation",
            "regulators.md §7 closing note: MAS ¶26(a)-(b) require simultaneity, not sequence",
        ),
        note=(
            "regulators.md §7 also warns that the rubric's own 'a summary must precede the ask' is a "
            "*rubric* requirement and not a regulatory one. It lives in CL-2, and it must not be "
            "cited to a regulator."
        ),
    ),
    KPI(
        id="CG-3",
        group="CG",
        question="Did the adviser stay on the right side of the licensing boundary — in whichever direction it runs here?",
        evidence=(
            "Two outcomes, because the boundary is a different kind of object in each regime. "
            "Outcome one: a modal shift to second-person prescription ('you should move your pension "
            "into this') absent the licensing and suitability precondition, in a regime where duties "
            "attach at the recommendation. Outcome two: the *absence* of advice where the regime "
            "inverts — for an unsolicited non-exchange-traded derivative sold to a client without "
            "derivatives knowledge, the SFC's ¶5.1A(b)(ii) duty is to warn and advise on suitability, "
            "so failing to advise is the breach."
        ),
        detector=Detector(
            kind="judge",
            name="licensing_boundary",
            note="two outcomes on one gate; the recommendation trigger is expressly a gradient in one regime and not susceptible to a bright line",
            requires_calibration=True,
            fallback=(
                "the in-session compliance-flag ledger, which roleplay/rubric_v1.md already treats "
                "as dispositive: any flag raised by the product's own flagger fails the gate without "
                "a judge"
            ),
        ),
        scale="gate: pass or fail, per outcome",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator="one gate with two outcomes; both are applicable in a Hong Kong derivative session, one in the others",
        excludes="sessions whose product and solicitation status do not engage either outcome",
        basis=(
            "regulators.md D10: MAS a scope carve-out, FCA a trigger for a body of rules, Reg BI a "
            "gradient assessed on call-to-action and tailoring, SFC inverted",
            "regulators.md §9 final row: every off-the-shelf compliance detector fires on *giving* "
            "advice, and a corpus containing the Hong Kong row demonstrates a defect class a keyword "
            "checker has no place to put",
            "call_craft.md S-44 (R13, V3): Reg BI's four obligations attach to a recommendation",
        ),
        assumptions=(
            "regulators.md §10 items 1 and 3: the UK advice perimeter (Article 53 RAO) and the SEC "
            "post-adoption staff bulletins were not read. Only the weaker claim is used — that the "
            "cited obligations attach to advice rather than to product information",
        ),
    ),
    KPI(
        id="CG-4",
        group="CG",
        question="Was remuneration disclosed in the form this regime permits — including 'not at all'?",
        evidence=(
            "The unit, not the sentence. MAS: the amount of commission on an investment product, "
            "and for a life policy only the distribution-cost item in the illustration. SFC: a "
            "percentage ceiling of the investment amount, per transaction, rounded up to the whole "
            "percentage point. Reg BI: standardised ranges are sufficient. FCA: provider commission "
            "on a retail investment recommendation is prohibited outright, so a disclosure of it is "
            "a confession rather than a compliance."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.ArgPredicate",
            note="a unit check on a recorded number; units are checkable far more reliably than sentences",
        ),
        scale="gate: the regime's permitted form, or fail",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator="one gate, applicable when the session reaches a recommendation on a remunerated product",
        excludes=(
            "sessions with no recommendation; and, inside a MAS session, the life-policy leg, which "
            "carries a different standard from the investment leg in the same conversation"
        ),
        basis=(
            "regulators.md D1: 'there is no charge to you, the provider pays us 3%' satisfies MAS, "
            "satisfies the SFC, over-satisfies Reg BI, and confesses a prohibited remuneration "
            "arrangement under FCA COBS 6.1A.4R. A keyword checker looking for a commission "
            "disclosure scores that line identically in all four",
            "regulators.md D8: MAS's intra-conversation carve-out — a unit trust and a whole-of-life "
            "policy in one meeting owe two different disclosure standards",
            "regulators.md §8 consequence 3: some requirements are about units rather than sentences, "
            "and those are the cheapest high-confidence compliance signals available",
        ),
    ),
    KPI(
        id="CG-5",
        group="CG",
        question="On a vulnerability signal, did the adviser do what *this* regime requires?",
        evidence=(
            "The required action differs in kind, not in wording. MAS: the selected-client "
            "determination made, documented and declared before the sales and advisory process "
            "proceeds, then a qualifying trusted individual present or a written declination — a "
            "procedure. FCA: communications tailored to the characteristics of vulnerability and an "
            "understanding-check, which is CE-1's contract evaluated on the vulnerability signal — an "
            "outcome."
        ),
        detector=Detector(
            kind="ledger",
            name="lab.checks.ToolContract on the selected-client determination + CE-1's contract",
            note="the MAS route is a ledger read; the FCA route is a positional contract. Same signal, two detectors",
        ),
        scale="gate: the regime's required action taken, or fail",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator="one gate, applicable when a vulnerability signal appears in the customer's turns",
        excludes=(
            "sessions with no vulnerability signal. Note what is *not* excluded: an adviser who "
            "correctly stops the call still passes this gate, and CS is suppressed rather than scored "
            "zero — see SCORECARD.md §1"
        ),
        basis=(
            "call_craft.md S-45 (R19, V2): from 29 December 2025 MAS's revised notices require the "
            "selected-client determination, a trusted individual present subject to criteria, "
            "audio-recorded pre-transaction call-backs, and independent sales audit checks",
            "call_craft.md S-21 (R9, V2): FCA PRIN 2A.5.8R, tailoring to characteristics of vulnerability",
            "regulators.md D6: MAS's three objective questions with two negatives making a selected "
            "client, against the FCA's no-thresholds outcomes test",
        ),
        note=(
            "MAS asks whether the procedure was followed; the FCA asks whether the customer "
            "understood. Those are different observable behaviours, which is why a locale axis that "
            "only swaps disclosure strings has proven less than it looks (call_craft.md §9 item 7)."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# CL — closing
# --------------------------------------------------------------------------- #

_CL = (
    KPI(
        id="CL-1",
        group="CL",
        question="Did the adviser ask for the business at all?",
        evidence="A turn classified as a close attempt.",
        detector=Detector(
            kind="contract",
            name="roleplay.persona.classify_trainee_turn",
            note=(
                "already ordered so that a personal recommendation dressed as a close classifies as "
                "advice, not as a close — the compliance consequence outranks the conversational one"
            ),
        ),
        scale="binary, 2 points",
        max_points=2,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="the session (n=1); not a rate",
        excludes=(
            "sessions the customer ended before any close was possible, and sessions where a "
            "vulnerability gate made stopping the correct action"
        ),
        basis=("roleplay/rubric_v1.md criterion 5, retained unchanged: 'did the trainee ask for the business'",),
    ),
    KPI(
        id="CL-2",
        group="CL",
        question="Did a real summary precede the ask — including what is wrong with this product for this customer?",
        evidence=(
            "A summary turn positioned before the close attempt, whose content carries fields "
            "elicited earlier in the session (propagation, not a template), and which states at "
            "least one disadvantage of this product for this customer."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract + lab.checks.FieldPropagationContract",
            note="ordering plus propagation; the propagation half is what a template summary fails",
        ),
        scale="0-3 points: summary present / positioned before the ask / carrying a disadvantage",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="the session (n=1); applicable only where a close attempt exists",
        excludes="sessions with no close attempt — there is nothing for a summary to precede",
        basis=(
            "call_craft.md S-52 (R3, V3): successful calls tend to rapport, then problems explored "
            "in depth, then logistics and next steps",
            "call_craft.md S-44 (R13, V3): Reg BI's Disclosure Obligation covers material limitations "
            "on what may be recommended",
            "regulators.md §9: MAS-3 ¶35(c) requires the product's *disadvantages for this client* to "
            "be documented, and ¶30 requires 'no suitable product' to be said when it is true",
        ),
        note=(
            "The ordering half is the rubric's own requirement and not a regulator's (regulators.md "
            "§7); the disadvantage half is a regulator's. Keeping the two apart in one KPI is "
            "deliberate — a reader should be able to see which half survives a challenge."
        ),
    ),
    KPI(
        id="CL-3",
        group="CL",
        question="Was the close soft or pressured?",
        evidence=(
            "Soft: names a specific next step, states what the customer is not committing to, and "
            "accepts a no without a further attempt. Pressured, three signals: a re-ask after a "
            "clear decline; a deadline or scarcity claim; and the cooling-off period offered as a "
            "reason to sign ('you can always cancel'), which converts a consumer protection into a "
            "closing lever."
        ),
        detector=Detector(
            kind="judge",
            name="close_pressure",
            note="the residue after the deterministic signals: tone-free, evidence-based, and the boundary is constructed rather than quoted",
            requires_calibration=True,
            fallback="lab.checks.NoReAskContract for the re-ask after a clear decline, which is the signal that needs no oracle",
        ),
        scale="0-3 points",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="the session (n=1); applicable only where a close attempt exists",
        excludes="sessions with no close attempt",
        basis=(
            "call_craft.md S-51 (R7, V3): in large, complex sales aggressive closing techniques "
            "reduce success, and the effect worsens as decision consequence rises",
            "call_craft.md S-54 (R8, V2): FCA COBS 4.2.1R — fair, clear and not misleading, taking "
            "into account the nature of the client",
        ),
        assumptions=(
            "call_craft.md A-20: the soft/pressured boundary is constructed from S-51, S-53 and S-54 "
            "and is not quoted from any of them",
            "call_craft.md A-21: cooling-off-as-closing-lever is a distinct gradeable pressure move — "
            "no source states it; it follows from the purpose of the cooling-off period",
        ),
        note=(
            "A-21 is why this is a SCORE and not part of the CL-4 gate. Gating on an unsourced "
            "construct would be exactly the failure this repo is a portfolio piece against."
        ),
    ),
    KPI(
        id="CL-4",
        group="CL",
        question="Was there any unevidenced urgency, or any incentive leaking into the call?",
        evidence=(
            "A deadline or scarcity claim with no basis in the product ledger; or first-person quota "
            "or campaign language in an adviser turn ('I have one more of these to place this month', "
            "'the campaign closes Friday')."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.PhraseContract + lab.checks.ArgPredicate against the product ledger",
            note="the claim is checked against the ledger, so 'the rate does end Friday' passes and an invented deadline does not",
        ),
        scale="gate: no unevidenced urgency and no incentive leakage, or fail",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator="one gate, applicable to every session with an adviser turn",
        excludes="nothing; this gate is applicable in every session",
        basis=(
            "call_craft.md S-53 (R13, V3): Reg BI's Conflict of Interest Obligation requires firms to "
            "*eliminate* sales contests, quotas and bonuses based on specific securities within a "
            "limited time period, because those create high-pressure situations",
            "call_craft.md S-54 (R8, V2): FCA COBS 4.2.1R",
            "call_craft.md C-6 and C-11: on a conversion-only scorer, invented urgency is the single "
            "most-rewarded non-compliant move",
        ),
        assumptions=(
            "call_craft.md A-06: that false urgency also fails commercially is unsourced. The gate "
            "does not rest on it — the gate rests on the rule",
        ),
    ),
)


# --------------------------------------------------------------------------- #
# LL — language and locale
# --------------------------------------------------------------------------- #

_LL = (
    KPI(
        id="LL-1",
        group="LL",
        question="When the customer refused, did the adviser hear it?",
        evidence=(
            "A closing-sequence customer turn labelled by the market's own refusal taxonomy: "
            "direct-no, conventional-indirect-no, genuine-defer, open. The adviser failure is "
            "treating a conventional-indirect refusal as still open — a follow-up ask, or a session "
            "outcome recorded as OPEN."
        ),
        detector=Detector(
            kind="judge",
            name="refusal_taxonomy",
            note=(
                "four labels, calibrated PER MARKET against human labels and published per market. "
                "Pooling markets would hide precisely the disparity that matters, so per-market "
                "calibration is not extra rigour, it is the point."
            ),
            requires_calibration=True,
            fallback=None,
        ),
        scale="0-3 points",
        max_points=3,
        gate_or_score="SCORE",
        business_metric="call_conversion",
        denominator="closing-sequence customer turns, per market",
        excludes=(
            "markets with no committed per-market calibration report: the KPI does not run there and "
            "is removed from the denominator, rather than silently scoring zero"
        ),
        basis=(
            "markets_languages.md §3.4 [S26-sec]: Chinese and American respondents both prefer "
            "indirect refusal strategies but Americans use materially more direct ones; Japanese "
            "speakers overwhelmingly prefer indirect strategies, with unfinished sentences a "
            "conventionalised polite refusal; the Beebe et al. direct/indirect taxonomy is the "
            "standard frame",
        ),
        assumptions=(
            "markets_languages.md §3.4, labelled loudly there as the most consequential inference in "
            "that file: the specific mapping 'I will consider it' = a settled no is widely reported "
            "for East Asian business communication but is not quantified for a financial advisory "
            "setting. It is a hypothesis this corpus should test, not a fact the scorer assumes",
        ),
        note=(
            "This is the label every conversion-linked KPI is validated against. Misread it and dead "
            "calls enter the 'still in play' denominator, the coaching recommendation becomes 'follow "
            "up', and the correct recommendation — 'you lost this at the objection and did not "
            "notice' — is never made. You cannot calibrate a conversion-linked judge in a high-context "
            "market until the refusal taxonomy is market-specific."
        ),
    ),
    KPI(
        id="LL-2",
        group="LL",
        question="Was the adviser's formality register stable and appropriate to the relationship?",
        evidence=(
            "An unrequested switch between formality levels inside one session: the T-V distinction "
            "in German, French, Spanish and Italian; Japanese keigo; Korean speech levels; Vietnamese "
            "kinship address; Indonesian Bapak/Ibu."
        ),
        detector=Detector(
            kind="contract",
            name="per-language register-switch detection",
            note="deterministic and per language; it detects a switch, which is measurable, rather than politeness, which is not",
        ),
        scale="0-2 points",
        max_points=2,
        gate_or_score="SCORE",
        business_metric="positioning_readiness",
        denominator="adviser turns in a language that grammaticalises formality",
        excludes=(
            "sessions in a language with no grammaticalised formality distinction; and switches the "
            "customer requested, which are correct behaviour"
        ),
        basis=(
            "markets_languages.md §3.4: keigo, Korean speech levels, Vietnamese kinship address, "
            "Bapak/Ibu and the grammaticalised T-V distinction all encode something a scorer "
            "routinely grades under 'professionalism' or 'rapport'",
        ),
        assumptions=(
            "markets_languages.md §3.4: that the useful eval is register *stability and "
            "appropriateness* rather than politeness, because stability is measurable without a "
            "cultural oracle. That is that file's assumption and it is this KPI's whole basis",
        ),
        note="The politeness version of this KPI must never be built. SCORECARD.md §3 says why.",
    ),
    KPI(
        id="LL-3",
        group="LL",
        question="Was every prescribed number correct for this jurisdiction — the number and its trigger?",
        evidence=(
            "The cooling-off or free-look period and the event that starts its clock. FCA: 30 "
            "calendar days, from conclusion of the contract or from when the consumer is informed it "
            "was concluded, and where several periods apply the firm applies the longest. SFC/IA: 21 "
            "calendar days from delivery of the policy or of the cooling-off notice, whichever is "
            "earlier. MAS: at least 14 days from the date of receipt of the policy. US: state-"
            "dependent, typically 10-30 days, and unanswerable without naming the state."
        ),
        detector=Detector(
            kind="contract",
            name="lab.checks.ArgPredicate",
            note=(
                "a numeric register check. A prescribed number is a prescribed unit, which makes this "
                "one of the few compliance requirements that is genuinely string-checkable"
            ),
        ),
        scale="gate: correct number and correct trigger for this jurisdiction, or fail",
        max_points=0,
        gate_or_score="GATE",
        business_metric="licence_to_operate",
        denominator="prescribed numbers quoted in the session",
        excludes=(
            "sessions quoting no prescribed number. A US session quoting a free-look period without "
            "naming the state fails rather than being excluded — the omission is the defect"
        ),
        basis=(
            "regulators.md D7: every number differs and every start-trigger differs. FCA-14 COBS "
            "15.2.1R and 15.2.3R, and 15.2.2G for the longest-period tie-break; HK-3 (secondary); "
            "SG-1 reg 8(1)(a); US-4 (secondary)",
            "regulators.md §7 row 24: the cooling-off clock start is an ordering rule inside a "
            "duration rule",
        ),
        assumptions=(
            "regulators.md §10 item 5: no specific US state's free-look number is asserted anywhere in "
            "the research, so a US scenario turning on one must name the state and cite that state's "
            "provision",
        ),
        note=(
            "'You have a couple of weeks to change your mind, starting from when you sign' is roughly "
            "right in Singapore and wrong about the trigger, materially wrong in the UK and Hong Kong, "
            "and unanswerable in the US."
        ),
    ),
    KPI(
        id="LL-4",
        group="LL",
        question="Is this session's behavioural score readable at all, and readable against what?",
        evidence=(
            "Three instrument readings published beside every voice score: this session's word error "
            "rate; its code-mixing band (CMI and I-index, as measured metadata rather than a "
            "verdict); and the text-only control run of the same scenario. Text score minus voice "
            "score, per market, *is* the speech-engine contribution, separated from the adviser."
        ),
        detector=Detector(
            kind="measurement",
            name="lab.voice WER + timing calibration; per-utterance code-mixing metrics",
            note="an instrument reading. It grades the harness and the vendor stack, and it grades nobody's adviser",
        ),
        scale="not scored: reported",
        max_points=0,
        gate_or_score="DIAGNOSTIC",
        business_metric="licence_to_operate",
        denominator=(
            "reference tokens in this session's transcript, harness-relative — it compares a "
            "transcript against the reference text the harness itself supplied to synthesis, so it is "
            "not a benchmark number for any speech-to-text engine"
        ),
        excludes=(
            "sessions with no audio path. And a cross-market comparison whose per-market WERs fall "
            "outside a stated band is not reported at all, rather than reported with a caveat"
        ),
        basis=(
            "markets_languages.md §3.6 [S18]: five commercial ASR systems transcribing matched "
            "structured interviews averaged WER 0.35 for Black speakers against 0.19 for white "
            "speakers, matched for age and gender",
            "markets_languages.md §3.6 [S19-sec]: Whisper recognised American English better than "
            "British or Australian, and native accents better than non-native",
            "markets_languages.md §3.1 [S15], [S16], [S21], [S22]: 82% of transcribed segments in the "
            "Singapore/Malaysia corpus of record are neither monolingual Mandarin nor monolingual "
            "English; CMI, M-index and I-index are the published code-mixing metrics; and the two "
            "code-switching pairs that matter most in this product's two Asian hubs are not in our "
            "own speech vendor's supported code-switching set",
            "lab/voice/engines/WER_NORMALISATION.md: a verified round trip where recognition was "
            "perfect still scored roughly 50% word error because the two sides format numerals "
            "differently — so even the instrument that protects against the bias needs its own "
            "normalisation stated",
        ),
        note=(
            "A DIAGNOSTIC rather than a SCORE because it measures the instrument and not the adviser, "
            "and a measurement of the instrument must never move a person's certification. It is "
            "mandatory reporting all the same: no behavioural score from a voice session is reportable "
            "without this session's WER beside it. Same family as this repo's denominator-safety rule."
        ),
    ),
)


#: The registry. Order is group order, then id order within a group, and it is the
#: order every report prints — a scorecard whose rows move between releases cannot
#: be trended.
KPIS: tuple[KPI, ...] = _CS + _DI + _OH + _CE + _CG + _CL + _LL


#: What became of each `rubric_v1` criterion. This module extends the rubric; it
#: does not fork it, and this map is how a reader checks that claim. A test
#: asserts every criterion has at least one successor and every successor exists.
RUBRIC_V1_SUCCESSORS: dict[str, tuple[str, ...]] = {
    "discovery": ("DI-1", "DI-2", "DI-3", "DI-4"),
    "objection_handling": ("OH-1", "OH-2", "OH-3"),
    "mandatory_disclosure": ("CG-1", "CG-2", "LL-3"),
    "no_unlicensed_advice": ("CG-3",),
    "closing": ("CL-1", "CL-2", "CL-3", "CL-4"),
}


# --------------------------------------------------------------------------- #
# Reading the registry
# --------------------------------------------------------------------------- #


def by_id(kpi_id: str) -> KPI:
    """The KPI with this id.

    Raises on an unknown id rather than returning None, for the same reason
    `roleplay.register.required_codes` does: a typo must not read as "nothing
    required here".
    """
    for kpi in KPIS:
        if kpi.id == kpi_id:
            return kpi
    raise KeyError(f"unknown KPI {kpi_id!r}; known: {[k.id for k in KPIS]}")


def by_group(group: str) -> tuple[KPI, ...]:
    """Every KPI in `group`, in registry order."""
    if group not in GROUPS:
        raise KeyError(f"unknown group {group!r}; known: {sorted(GROUPS)}")
    return tuple(k for k in KPIS if k.group == group)


def scored(kpis: Sequence[KPI] = KPIS) -> tuple[KPI, ...]:
    """The SCORE rows — the only ones that contribute points."""
    return tuple(k for k in kpis if k.is_scored)


def gates(kpis: Sequence[KPI] = KPIS) -> tuple[KPI, ...]:
    """The GATE rows. Counted, never averaged."""
    return tuple(k for k in kpis if k.is_gate)


def points_available(kpis: Sequence[KPI] = KPIS) -> int:
    """The score denominator.

    Sums `max_points` over everything, which is safe *because* gates and
    diagnostics are structurally zero. There is no filter here to forget.
    """
    return sum(k.max_points for k in kpis)


# --------------------------------------------------------------------------- #
# One session's outcome
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KPIOutcome:
    """What one KPI decided about one session.

    `applicable` is the field that makes the denominator honest. A KPI that did
    not apply — no objection was raised, no vulnerability signal appeared, the
    market has no committed calibration report — is removed from *both* numerator
    and denominator, and `SessionScore` prints the reduced denominator. Scoring it
    zero instead would punish an adviser for a scenario they did not choose; and
    scoring it full would hide the gap.
    """

    kpi_id: str
    applicable: bool = True
    points: int | None = None
    gate_passed: bool | None = None
    evidence: str = ""


@dataclass(frozen=True)
class SessionScore:
    """The two figures, always together.

    There is no single number here, and that is the design. `points` over
    `points_available` is the behavioural score; `gates_passed` over
    `gates_applicable` is the compliance result; and a session with one failed gate
    is a failed session at any score.
    """

    points: int
    points_available: int
    pass_points: int
    gates_passed: int
    gates_applicable: int
    gates_failed: tuple[str, ...]
    not_applicable: tuple[str, ...]
    verdict: Literal["pass", "fail"]

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def score_percent(self) -> float:
        """Never printed alone. `summary_line` always carries the fraction too."""
        return round(100.0 * self.points / self.points_available, 1) if self.points_available else 0.0

    def summary_line(self) -> str:
        gate_note = (
            f" gates FAILED: {', '.join(self.gates_failed)}"
            if self.gates_failed
            else " gates: all applicable gates passed"
        )
        na = f" n/a: {len(self.not_applicable)}" if self.not_applicable else ""
        return (
            f"{self.verdict.upper()} {self.points}/{self.points_available} points "
            f"({self.score_percent}%, threshold {self.pass_points}/{self.points_available}) "
            f"| {self.gates_passed}/{self.gates_applicable} gates."
            f"{gate_note}{na}"
        )


def score_session(
    outcomes: Sequence[KPIOutcome],
    *,
    kpis: Sequence[KPI] = KPIS,
    pass_fraction: float = PASS_FRACTION,
) -> SessionScore:
    """Turn per-KPI outcomes into one session verdict.

    Every KPI in `kpis` must have an outcome. A missing outcome is an error rather
    than a zero or a skip, because a KPI that silently vanishes changes the
    denominator without appearing in the report — which is the same defect class as
    a naked percentage, arriving by a quieter route.

    A failed gate fails the session whatever the points total, and the failure is
    named. A gate cannot be outscored, which is the only reading of "gate" that
    means anything.
    """
    by_kpi = {k.id: k for k in kpis}
    seen: dict[str, KPIOutcome] = {}
    for outcome in outcomes:
        if outcome.kpi_id not in by_kpi:
            raise KeyError(f"outcome for unknown KPI {outcome.kpi_id!r}")
        if outcome.kpi_id in seen:
            raise ValueError(f"two outcomes for {outcome.kpi_id!r}")
        seen[outcome.kpi_id] = outcome

    missing = [k.id for k in kpis if k.id not in seen]
    if missing:
        raise ValueError(
            f"no outcome reported for {len(missing)} KPI(s): {missing}. Report every KPI, "
            "marking the ones that did not apply as applicable=False, so the denominator is visible."
        )

    points = 0
    available = 0
    gates_applicable = 0
    gates_passed = 0
    gates_failed: list[str] = []
    not_applicable: list[str] = []

    for kpi in kpis:
        outcome = seen[kpi.id]

        if kpi.is_scored:
            if outcome.gate_passed is not None:
                raise ValueError(f"{kpi.id} is a SCORE; gate_passed must be None")
            if not outcome.applicable:
                not_applicable.append(kpi.id)
                continue
            if outcome.points is None:
                raise ValueError(f"{kpi.id} is an applicable SCORE and reported no points")
            if not 0 <= outcome.points <= kpi.max_points:
                raise ValueError(
                    f"{kpi.id} reported {outcome.points} points, outside 0..{kpi.max_points}"
                )
            points += outcome.points
            available += kpi.max_points
            continue

        if outcome.points is not None:
            raise ValueError(f"{kpi.id} is a {kpi.gate_or_score}; points must be None")

        if kpi.is_gate:
            if not outcome.applicable:
                not_applicable.append(kpi.id)
                continue
            if outcome.gate_passed is None:
                raise ValueError(f"{kpi.id} is an applicable GATE and reported no verdict")
            gates_applicable += 1
            if outcome.gate_passed:
                gates_passed += 1
            else:
                gates_failed.append(kpi.id)
            continue

        # DIAGNOSTIC: reported, never scored, never gating.
        if outcome.gate_passed is not None:
            raise ValueError(f"{kpi.id} is a DIAGNOSTIC; it decides nothing and gate_passed must be None")
        if not outcome.applicable:
            not_applicable.append(kpi.id)

    pass_points = math.ceil(pass_fraction * available)
    verdict: Literal["pass", "fail"]
    if gates_failed:
        verdict = "fail"
    else:
        verdict = "pass" if points >= pass_points else "fail"

    return SessionScore(
        points=points,
        points_available=available,
        pass_points=pass_points,
        gates_passed=gates_passed,
        gates_applicable=gates_applicable,
        gates_failed=tuple(gates_failed),
        not_applicable=tuple(not_applicable),
        verdict=verdict,
    )


# --------------------------------------------------------------------------- #
# Validation, at import
# --------------------------------------------------------------------------- #


def _validate() -> None:
    """Refuse to import a registry that cannot be defended.

    Import time, not test time, for the same reason `roleplay.register` validates
    a jurisdiction in `__post_init__`: a scorecard row that certifies a person on
    an undeclared basis should not be *reachable*, and a test only catches it if
    somebody runs the test.
    """
    seen: set[str] = set()
    for kpi in KPIS:
        where = f"KPI {kpi.id}"

        if kpi.id in seen:
            raise ValueError(f"{where}: duplicate id")
        seen.add(kpi.id)

        if kpi.group not in GROUPS:
            raise ValueError(f"{where}: unknown group {kpi.group!r}")
        if not kpi.id.startswith(f"{kpi.group}-"):
            raise ValueError(f"{where}: id must be prefixed with its group {kpi.group!r}")

        if not kpi.question.strip() or not kpi.evidence.strip():
            raise ValueError(f"{where}: question and evidence are both required")

        # Every KPI declares a detector.
        if not kpi.detector.name.strip() or not kpi.detector.note.strip():
            raise ValueError(f"{where}: declares no detector")
        if kpi.detector.kind not in DETECTOR_KINDS:
            raise ValueError(f"{where}: unknown detector kind {kpi.detector.kind!r}")
        if kpi.detector.requires_calibration != (kpi.detector.kind == "judge"):
            raise ValueError(
                f"{where}: requires_calibration must be True exactly for judges — a judge that "
                "does not declare it reads as usable, and it is not"
            )

        # A gate that cannot run is not a gate.
        if kpi.is_gate and kpi.detector.requires_calibration and not kpi.detector.fallback:
            raise ValueError(
                f"{where}: a GATE whose detector is an uncalibrated judge must name a "
                "deterministic fallback, or it is a gate that silently does not run"
            )

        # Gates and diagnostics are excluded from the score total, structurally.
        if kpi.is_scored:
            if kpi.max_points <= 0:
                raise ValueError(f"{where}: a SCORE must carry points")
        elif kpi.max_points != 0:
            raise ValueError(
                f"{where}: {kpi.gate_or_score} rows must have max_points == 0 so that "
                "points_available cannot include them"
            )

        if kpi.business_metric not in BUSINESS_METRICS:
            raise ValueError(
                f"{where}: business_metric {kpi.business_metric!r} is not in the closed vocabulary. "
                "A behaviour that cannot name the metric it leads does not belong on the scorecard"
            )
        if kpi.is_gate and kpi.business_metric != "licence_to_operate":
            raise ValueError(
                f"{where}: a GATE must ladder to licence_to_operate. A gate pointed at a growth "
                "metric is a gate somebody will eventually trade away"
            )

        if not kpi.scale.strip():
            raise ValueError(f"{where}: declares no scale")
        if not kpi.denominator.strip():
            raise ValueError(f"{where}: declares no denominator")
        if not kpi.excludes.strip():
            raise ValueError(f"{where}: declares no exclusions; write 'nothing' if that is true")

        if not kpi.basis and not kpi.assumptions:
            raise ValueError(
                f"{where}: every KPI is sourced or labelled an assumption, and there is no third "
                "category. Cite docs/_research/ or declare the assumption"
            )

    for group in GROUPS:
        n = len(by_group(group))
        if not 3 <= n <= 5:
            raise ValueError(
                f"group {group} holds {n} KPIs; the design budget is 3-5. A scorecard nobody "
                "reads all of certifies nobody"
            )

    known = {k.id for k in KPIS}
    for criterion, successors in RUBRIC_V1_SUCCESSORS.items():
        if not successors:
            raise ValueError(f"rubric_v1 criterion {criterion!r} has no successor KPI")
        for kpi_id in successors:
            if kpi_id not in known:
                raise ValueError(f"rubric_v1 criterion {criterion!r} names unknown KPI {kpi_id!r}")


_validate()
