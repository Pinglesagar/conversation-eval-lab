"""Computing a regime verdict from the cited registers, over a real trace.

WHAT THIS MODULE IS FOR
-----------------------
`scenarios/advisory/` holds eighteen advisory transcripts, each graded under one
of four regulatory regimes, and `scenarios/advisory/registers/*.yaml` holds the
thirty-six enumerated requirements those regimes impose — each with its `kind`,
its `timing`, its paragraph-level `source` and the section of
`docs/_research/regulators.md` that establishes it. Until this module existed,
nothing computed a verdict from them: `expectation.human_verdict` and the
per-regime `divergence` block were hand-labelled, and the registers were
documentation.

`RegimeEvaluator` closes that gap. It takes a `SessionView` (or the `Trace` the
view is read from) plus a regime id and returns a `RegimeVerdict`: one
`EntryVerdict` per register entry, each carrying the entry's own citation, and an
overall `pass` / `fail` / `undecidable`.

    verdict = RegimeEvaluator().evaluate(trace, regime="fca")
    print(verdict.summary())          # numerator and denominator, never a bare rate
    for entry in verdict.entries:
        print(entry.render())         # status, reason, and the paragraph it rests on

THE TWO KINDS OF CLAIM IN THIS FILE, AND THEY ARE LABELLED DIFFERENTLY
----------------------------------------------------------------------
Every requirement, every `kind`, every `timing` phrase and every citation in this
file is **read out of the register YAML at runtime**. Nothing about what the
regulators require is restated here; if a register entry changes, this module's
reasons change with it, and an entry with no probe is reported as an unprobed
entry rather than silently passing.

What this module *adds* is the mapping from a cited requirement to detectable
surface features of a transcript — the patterns, the landmarks, the positional
rules. **Every one of those mappings is an ASSUMPTION**, in the sense
`docs/_research/` uses the word: no source states that "Have I put that clearly?"
is what PRIN 2A.5.9R's understanding check looks like in a spoken call. So each
probe carries a `basis` string saying, in words a reader sees in the output, how
the sourced requirement was operationalised and what that operationalisation
cannot see. There is no third category: the requirement is sourced, the probe is
an assumption.

`kind` IS THE WHOLE POINT, SO IT DRIVES GENUINELY DIFFERENT LOGIC
----------------------------------------------------------------
    verbatim          only the prescribed form of words satisfies it. A paraphrase
                      MISSES. `fca-past-performance-verbatim` and
                      `mas-past-performance-substance` are the same sentence in two
                      drafting traditions, and a paraphrase of it satisfies MAS and
                      misses the FCA — computed, not asserted (see the tests).
    prescribed-unit   the substance *plus* a unit: a whole percentage point, a cash
                      figure, a number of days, and — for the cooling-off entries —
                      the trigger the clock starts on. Decided arithmetically.
    substance         a paraphrase conveying the meaning satisfies it. Where the
                      surface is an enumerable claim (a distribution-cost line, a
                      language preference established and recorded) a deterministic
                      check is honest enough and is preferred, because a judge with
                      an unmeasured error rate is a worse instrument than a pattern
                      whose blind spots are written down. Where the requirement is
                      genuinely a judgement — PRIN 2A.5.3R's "likely to be
                      understood" — the probe names the judge it would need, asks
                      `lab.judges` for it, and records the registry's answer as a
                      residue. It does not gate: see JUDGES below.
    prohibition       the presence of something FAILS. Nothing to disclose.
    gate              a procedural precondition; a miss fails the session
                      regardless of any score, and `EntryVerdict.decisive` says so.
    not-required      this regime does NOT require it. Reported as
                      `not-applicable` with the reason spelled out, which is the
                      load-bearing part: it is what stops a cross-market checker
                      inventing a requirement in the market that does not impose
                      one, and it is why an adviser who omits the thing PASSES here.

TIMING IS DECIDED ON EVENT-STREAM POSITION, NEVER ON TIMESTAMPS
--------------------------------------------------------------
Every positional rule in this file compares indices into the ordered sequence of
the adviser's `caller_utterance` events. `lab.checks.contracts._sequence` gives
the full argument and this repo has already fixed this bug once: under a
`FakeClock` every event in a roleplay session can carry `ts=0.0`, a `<=` on tied
timestamps reads as "in order", and an ordering rule compared on `ts` silently
cannot fail. Timestamps are what a report *quotes*; positions are what decides.

JUDGES: ROUTED, AND NOT GATING
------------------------------
A probe that names a judge calls `lab.judges.registry` for it. This repo ships no
calibrated judge for any of these questions, so what comes back is "not
registered" — and the probe records that in its residue and decides the entry on
its deterministic limb alone. That is the rule `lab.judges.registry` exists to
enforce, applied to ourselves: an uncalibrated judge does not gate. The same
discipline applies to the two detectors `docs/_research/call_craft.md` labels
ASSUMPTION (A-16, the minimisation-adjacency detector, and the prominence
detector): both are computed, both are reported as evidence, and neither decides
an entry, because neither has a calibrated TNR.

WHAT THIS INSTRUMENT CANNOT SEE, STATED ONCE
--------------------------------------------
1.  **Writing.** A transcript cannot show that a document was provided. Where the
    adviser narrates it ("I am going to send you our disclosure document ... I
    will wait until you have it in front of you") the transcript evidences it;
    otherwise the medium limb is a residue on a decided status, or — where the
    position is wrong — the entry misses on position and the medium is moot.
2.  **The addressee.** A disclosure has a recipient as well as a form of words and
    a position, and no field in this repo records one. Where a transcript shows a
    third party being addressed, the affected entry returns `instrument-gap` and
    the session is `undecidable` rather than guessed. That is
    `nearmiss-warning-addressed-to-the-partner`, and the abstention is the row's
    own stated point.
3.  **Product class per requirement, inside one session.** Classes are detected
    over the whole transcript, so a session holding two products with two
    standards (`divergence-two-products-one-meeting-mas-carveout`) is graded on
    the union. The `not-required` entry is what keeps that honest there; it is a
    known limitation and it is listed in the CLI's own output.

CLI
---
    python -m roleplay.regime_eval                 # all eighteen rows, computed
    python -m roleplay.regime_eval --divergence    # the per-regime blocks only
    python -m roleplay.regime_eval --shadow        # the keyword-check comparison
    python -m roleplay.regime_eval --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal, Sequence

from lab.checks.text import fold_typography, is_question, sentences
from lab.trace.schema import Trace

from roleplay.advisory import REGIMES, Register, RegisterEntry, load_registers
from roleplay.scorer import SessionView, session_view

if TYPE_CHECKING:  # pragma: no cover - annotations only; the runtime import is lazy
    from roleplay.corpus import Corpus, Scenario
    from roleplay.runtime import RoleplayResult

__all__ = [
    "FALSE_PASS_ROWS",
    "LIMITATIONS",
    "PROBES",
    "STATUSES",
    "VERDICTS",
    "ComputedRow",
    "EntryVerdict",
    "Landmarks",
    "NaiveShadow",
    "Probe",
    "RegimeEvaluator",
    "RegimeVerdict",
    "Transcript",
    "main",
    "run_corpus",
]

#: What one register entry can come back as. Four values, and the fourth one is
#: the honest one: `instrument-gap` says the requirement engaged and this
#: instrument has no field in which the answer could be recorded. It is not a
#: near-miss of `missed` and it must never be reported as one.
STATUSES: tuple[str, ...] = ("satisfied", "missed", "not-applicable", "instrument-gap")

#: The session-level answer. `undecidable` is a first-class verdict rather than a
#: default: an honest abstention is worth more than a coin flip, and a report that
#: cannot say "I do not know" will say "pass" instead.
VERDICTS: tuple[str, ...] = ("pass", "fail", "undecidable")

Status = Literal["satisfied", "missed", "not-applicable", "instrument-gap"]
Verdict = Literal["pass", "fail", "undecidable"]


# --------------------------------------------------------------------------- #
# Small text arithmetic the probes are built on
# --------------------------------------------------------------------------- #

#: Spelled-out numbers this file needs beyond `lab.checks.text.NUMBER_WORDS`,
#: which stops at twenty by documented design. The register's durations are 14,
#: 21 and 30 days, so those are the words that have to parse.
_EXTRA_NUMBER_WORDS: dict[str, float] = {
    "twenty-one": 21.0,
    "twenty one": 21.0,
    "thirty": 30.0,
    "twenty": 20.0,
    "fourteen": 14.0,
    "ten": 10.0,
    "nine": 9.0,
    "eight": 8.0,
    "seven": 7.0,
    "six": 6.0,
    "five": 5.0,
    "four": 4.0,
    "three": 3.0,
    "two": 2.0,
    "one": 1.0,
}

_HALVES: dict[str, float] = {"and a half": 0.5, "and a quarter": 0.25, "point five": 0.5}

_PERCENT = re.compile(
    r"(?P<value>[a-z0-9\.\- ]{1,32}?)\s*(?:per cent|percent|%)", re.IGNORECASE
)
_DAYS = re.compile(
    r"(?P<value>[a-z0-9\-]+(?:[ \-]one)?)\s*(?:calendar\s+)?days?", re.IGNORECASE
)


def _parse_number(raw: str) -> float | None:
    """A number written as digits or as the words this register uses.

    Deliberately small. "one and a half per cent" has to parse because it is the
    figure that makes `divergence-two-products-one-meeting-mas-carveout` miss the
    SFC's whole-percentage-point unit, and a parser that quietly returned 1.0 for
    it would turn that miss into a pass.
    """
    # NOT `lab.checks.text.normalize`, which strips punctuation: it turns "0.3" into
    # "0 3", and the last token of that is 3. A percentage parser that reads "0.3
    # per cent" as three per cent would turn the SFC's whole-percentage-point unit
    # into a coin flip, so the decimal point survives here.
    text = re.sub(r"\s+", " ", fold_typography(raw).lower()).strip(" ,;:")
    if not text:
        return None
    bonus = 0.0
    for phrase, value in _HALVES.items():
        if text.endswith(phrase):
            bonus = value
            text = text[: -len(phrase)].strip()
            break
    tail = text.split()[-3:] if text.split() else []
    for width in (3, 2, 1):
        if len(tail) >= width:
            candidate = " ".join(tail[-width:])
            digits = re.fullmatch(r"\d+(?:\.\d+)?", candidate)
            if digits:
                return float(candidate) + bonus
            if candidate in _EXTRA_NUMBER_WORDS:
                return _EXTRA_NUMBER_WORDS[candidate] + bonus
    return None


def _percentages(text: str) -> tuple[float, ...]:
    """Every percentage figure in `text`, as floats."""
    found: list[float] = []
    for match in _PERCENT.finditer(fold_typography(text)):
        value = _parse_number(match.group("value"))
        if value is not None:
            found.append(value)
    return tuple(found)


def _day_counts(text: str) -> tuple[float, ...]:
    """Every "<n> days" figure in `text`, as floats."""
    found: list[float] = []
    for match in _DAYS.finditer(fold_typography(text)):
        value = _parse_number(match.group("value"))
        if value is not None:
            found.append(value)
    return tuple(found)


def _hit(text: str, patterns: Sequence[str]) -> str | None:
    """The first pattern in `patterns` that matches `text`, or None.

    Returns the pattern rather than a bool so a reason can name the rule that
    fired. Matching is over the typographically folded text, as everywhere in
    `lab.checks`: a model writes U+2019 and every pattern here is written with an
    ASCII apostrophe.
    """
    folded = fold_typography(text)
    for pattern in patterns:
        if re.search(pattern, folded, re.IGNORECASE):
            return pattern
    return None


def _turns_matching(turns: Sequence[str], patterns: Sequence[str]) -> tuple[int, ...]:
    return tuple(i for i, turn in enumerate(turns) if _hit(turn, patterns))


def _first_matching(turns: Sequence[str], patterns: Sequence[str]) -> int | None:
    positions = _turns_matching(turns, patterns)
    return positions[0] if positions else None


# --------------------------------------------------------------------------- #
# What a transcript looks like to a register
# --------------------------------------------------------------------------- #

#: Product-class markers. A `strong` marker settles the class on one hit; two
#: `weak` hits are needed otherwise.
#:
#: ASSUMPTION, and a consequential one: no source says how to read a product class
#: off a transcript. The threshold exists because a single glancing mention —
#: "it is a decision about your income, not about the fund", in
#: `clause-surrender-value-explained` — must not make a protection sale into an
#: investment sale and pull the whole COBS investment register onto it.
_PRODUCT_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "investment": {
        "strong": (
            r"\bbalanced growth fund\b",
            r"\bstocks[- ]and[- ]shares isa\b",
            r"\bunit trust\b",
            r"\bstructured note\b",
            r"\bbasis points\b",
            r"\brollover\b",
            r"\bportfolio\b",
            r"\binstitutional share class\b",
        ),
        "weak": (r"\bfunds?\b", r"\bshares\b", r"\bpension\b", r"\bthe plan\b", r"\bisa\b", r"\bnotes?\b"),
    },
    "life_policy": {
        "strong": (
            r"\bsurrender\b",
            r"\bcritical illness\b",
            r"\bpays out on death\b",
            r"\bpolicy illustration\b",
            r"\bfree[- ]look\b",
            r"\bwhole[- ]life\b",
            r"\bpolicy document\b",
        ),
        "weak": (r"\bpolicy\b", r"\bpremium\b", r"\bcover\b", r"\bpays out\b", r"\ba month\b"),
    },
}

#: The landmark patterns. Every positional rule in this file is stated against one
#: of these, so the operationalisation is in one place and a reader can disagree
#: with it in one place.
#:
#: ASSUMPTION throughout. `reg-bi`'s own register says "recommendation" is
#: "expressly not susceptible to a bright-line definition and is assessed on call
#: to action and degree of individual tailoring" (US-1), which is why there are
#: two landmarks and not one: `recommendation` opens the window, `conclusion` is
#: the call to action that closes it, and the register's timing phrase decides
#: which end a requirement is measured against.
_RECOMMENDATION: tuple[str, ...] = (
    r"\bi have in mind\b",
    r"\bthe (fund|policy|plan|note) i (have in mind|would)\b",
    r"\bi would (put|suggest|recommend|move|go for)\b",
    r"\bwhat i would do\b",
    r"\bif it were my\b",
    r"\bthe (fund|policy|plan) is a\b",
    r"\bthe transfer is\b",
    r"\ba rollover into\b",
    r"\bthe policy pays out\b",
    r"\bthere is a version of this at\b",
    r"\bwhat i would suggest\b",
)

#: The adviser saying, in terms, that no recommendation is being made. This
#: suppresses the recommendation landmark, and it is the whole reason
#: `divergence-unsolicited-note-failing-to-advise` passes under the FCA and Reg BI
#: and fails under the SFC and MAS: three of the four regimes attach their duties
#: to the giving of advice, and one attaches its duty to the *absence* of it.
_NO_RECOMMENDATION: tuple[str, ...] = (
    r"\bi have not recommended it\b",
    r"\bexecution[- ]only\b",
    r"\byou named the product\b",
    r"\bi will not recommend\b",
    r"\bdeclined an explanation\b",
)

#: The call to action: the turn at which the transaction is being concluded.
#: Narrow on purpose — "do you want me to price both" is an offer to do more work
#: and must not read as a close, or `clause-surrender-value-explained` would fail
#: for asking a good question.
_CONCLUSION: tuple[str, ...] = (
    r"\bget the paperwork started\b",
    r"\bput the application through\b",
    r"\bsend the application\b",
    r"\btake you through the application\b",
    r"\bsign (at the bottom|here|it)\b",
    r"\bshall we get it in place\b",
    r"\bconfirming the order\b",
    r"\bstart it today\b",
    r"\bon your instruction then\b",
    r"\bshall we do that\b",
    r"\bi will send the paperwork\b",
)

#: A turn that puts a decision in front of the customer: a risk, a limitation, a
#: consequence, or what it costs. PRIN 2A.5.9R's understanding check is required
#: "particularly where the information prompts a decision", so the check is only
#: looked for where such a turn exists.
_DECISION_PROMPTING: tuple[str, ...] = (
    r"\bcapital is at risk\b",
    r"\bget back less\b",
    r"\bclose to nothing\b",
    r"\bsurrender value\b",
    r"\bthat money is gone\b",
    r"\bpays nothing\b",
    r"\bwaiting period\b",
    r"\bwould get back\b",
    r"\badvice charge\b",
    r"\bannual management charge\b",
    r"\bbasis points\b",
    r"\bwhat you would give up\b",
    r"\bwhat it costs\b",
    r"\bper cent a year\b",
)

#: An understanding check, addressed by the adviser to whoever they are talking
#: to. ASSUMPTION: `call_craft.md` §5.1 (S-22) recommends the behaviour and does
#: not enumerate its forms.
_UNDERSTANDING_CHECK: tuple[str, ...] = (
    r"\bhave i put that clearly\b",
    r"\bdoes that (distinction )?make sense\b",
    r"\bis that clear\b",
    r"\bdo you follow\b",
    r"\bdo you understand\b",
    r"\bany of it is fuzzy\b",
    r"明唔明",
    r"\bhave you got any questions\b",
    r"\bany questions (on|about) that\b",
)

#: A third party in the room, being addressed instead of the customer. Detected
#: as a vocative plus a third-person reference to the person actually buying.
#: This does not decide anything: it declares the addressee question undecidable,
#: because the addressee is not a field in this instrument.
_THIRD_PARTY_ADDRESSEE: tuple[str, ...] = (
    r"\b(mr|mrs|ms|miss)\s+[a-z][a-z']+,",
)
_BUYER_IN_THIRD_PERSON: tuple[str, ...] = (
    r"\bin (her|his) name\b",
    r"\bif (she|he) stops paying\b",
    r"\bshe is the\b",
)

#: The adviser accepting the customer's offer to skip something the firm owes.
#: The strong signal for the two "insufficient information" gates: a fact-find is
#: not waivable by the person it protects.
_WAIVER_ACCEPTED: tuple[str, ...] = (
    r"\bno questions then\b",
    r"\bi will not read the warnings\b",
    r"\bi will take you at your word\b",
    r"\byou have said you know the risks\b",
    r"\bi will respect that\b",
    r"\bi am not going to make you\b",
    r"\bsince you are clear\b",
)

#: Discovery: the adviser asking about the customer rather than telling them about
#: the product. The deterministic proxy for "obtained the necessary information".
#:
#: Two signals, unioned, and neither is a fact-find: any adviser sentence that is a
#: question and is neither an understanding check nor a call to action
#: (`Transcript.discovery_positions`), plus these enumerated openers for the ones
#: phrased as imperatives and carrying no question mark. ASSUMPTION, and weak in
#: the generous direction — one question is not a fact-find — so it is paired with
#: `_WAIVER_ACCEPTED`, which is the signal that actually carries the gate rows.
_DISCOVERY: tuple[str, ...] = (
    r"\btell me about\b",
    r"\bi am asking because\b",
    r"\bwhat i need to know (about|from) you\b",
)

#: The adviser's own remuneration being talked about, in any unit.
_REMUNERATION: tuple[str, ...] = (
    r"\bcommission\b",
    r"\bmonetary benefit\b",
    r"\bpays us\b",
    r"\bwe receive\b",
    r"\bi receive\b",
    r"\bwhat i (make|earn)\b",
    r"\bmy own remuneration\b",
    r"\btrailer\b",
    r"\bdistribution cost\b",
    r"\bwe are paid\b",
)

#: A charge to the customer, in any unit.
_CHARGES: tuple[str, ...] = (
    r"\badvice charge\b",
    r"\badviser charge\b",
    r"\bannual management charge\b",
    r"\bongoing charges? figure\b",
    r"\bbasis points\b",
    r"\bper cent a year\b",
    r"\bwhat it costs\b",
    r"\bno charge to you\b",
    r"\bcosts you nothing\b",
    r"\bno exit charge\b",
)

#: A written artefact the adviser says is being provided. This is the only way a
#: transcript can evidence the medium of a requirement whose object is a document.
_WRITING_PROVIDED: tuple[str, ...] = (
    r"\bi am going to send you our disclosure document\b",
    r"\bin front of you\b",
    r"\bi will put both in the illustration\b",
    r"\bin the illustration i will send you\b",
    r"\bwhich is here\b",
    r"\bi will send the illustration\b",
    r"\bthe factsheet\b",
    r"\bin writing\b",
    r"\bi will send you\b",
)

#: Past performance being referred to at all — the trigger for the FCA's verbatim
#: warning and the MAS substance test alike.
_PAST_PERFORMANCE_REFERENCE: tuple[str, ...] = (
    r"\bpast performance\b",
    r"\btrack record\b",
    r"\bwhat it did last year\b",
    r"\bhistoric returns?\b",
)

#: An actual performance *figure*, as opposed to the caveat about one. MAS-2
#: ¶25(c)'s source-of-data limb and ¶26(b)'s simultaneous-writing rule both bite
#: only where performance is used to illustrate returns.
_PERFORMANCE_FIGURE: tuple[str, ...] = (
    r"\breturned \d",
    r"\bgrew by\b",
    r"\bup \d+(\.\d+)? per cent (last|over)\b",
    r"\bannualised\b",
)

#: Cooling-off / free-look / cancellation being discussed.
_COOLING_OFF_REFERENCE: tuple[str, ...] = (
    r"\bfree[- ]look\b",
    r"\bcooling[- ]off\b",
    r"\bcancellation period\b",
    r"\bdays to examine the policy\b",
    r"\bchange your mind\b",
)

#: A minimisation construction: a limitation discounted rather than explained.
#: ASSUMPTION, explicitly labelled as one by `call_craft.md` A-16, which also
#: names its false-positive mode (a legitimate proportionality statement). It has
#: no calibrated TNR, so it is computed, reported, and NOT allowed to decide an
#: entry — see the module docstring.
_MINIMISATION: tuple[str, ...] = (
    r"\bbut in practice\b",
    r"\bthat is not what happens\b",
    r"\bmost people (keep|hold|stay)\b",
    r"\bnobody looks back\b",
    r"\bthe people who lose out are the ones who never\b",
    r"\byou can always look at the figures again\b",
)

#: A limitation actually stated. Paired with `_MINIMISATION` by
#: `_minimisation_adjacency`, because the detector the corpus describes is an
#: ADJACENCY detector — a limitation turn followed within a turn by a minimising
#: construction, with no understanding check between — and not a word list.
_LIMITATION_STATED: tuple[str, ...] = (
    r"\bget back less\b",
    r"\bwould get back\b",
    r"\bclose to nothing\b",
    r"\bsurrender\b",
    r"\bpays nothing\b",
)

def _minimisation_adjacency(tx: "Transcript") -> str:
    """A limitation discounted within a turn of being stated, with no check between.

    The detector `call_craft.md` A-16 describes, implemented as described:
    positions, not vocabulary. It is labelled an ASSUMPTION there, its
    false-positive mode is named there (a legitimate proportionality statement),
    and it has no calibrated TNR in this repo — so its output is evidence a human
    reads and never a verdict. `RegimeEvaluator` prints it and decides the entry on
    something else.
    """
    limitations = _turns_matching(tx.adviser, _LIMITATION_STATED)
    minimisations = _turns_matching(tx.adviser, _MINIMISATION)
    checks = _turns_matching(tx.adviser, _UNDERSTANDING_CHECK)
    for stated in limitations:
        for discounted in minimisations:
            if 0 <= discounted - stated <= 1 and not any(
                stated <= check <= discounted for check in checks
            ):
                return (
                    f"a limitation stated at turn {stated} is discounted at turn "
                    f"{discounted} with no understanding check between them"
                )
    return ""


#: Chinese characters anywhere in the transcript, or the language question raised
#: in terms. The SFC's language rule is a rule about an artefact's language, and
#: it can only engage where the transcript shows a language question exists.
_MULTILINGUAL: tuple[str, ...] = (
    r"[一-鿿]",
    r"\bin (chinese|cantonese|mandarin|english)\b",
    r"\blanguage of (the |your )?choice\b",
)


@dataclass(frozen=True)
class Landmarks:
    """Positions in the adviser's turn sequence, or None where absent.

    Positions, not timestamps. Every one of these is an index into the ordered
    `caller_utterance` events of the trace.
    """

    recommendation: int | None
    conclusion: int | None
    decision_prompting: tuple[int, ...]
    no_recommendation: int | None

    @property
    def window_end(self) -> int | None:
        """The end of the recommendation window: the call to action, if any.

        Reg BI's "prior to or at the time of the recommendation" is measured
        against this, because the register's own text refuses a bright line for
        the recommendation itself.
        """
        if self.conclusion is not None:
            return self.conclusion
        return self.recommendation


@dataclass(frozen=True)
class Transcript:
    """The adviser's turns, the customer's turns, and what can be read off them.

    Satisfaction is only ever read from `adviser`, never from `customer`. That is
    the same rule `roleplay.register.compare_with_keyword_check` states: letting
    the customer's own words about risk discharge the adviser's obligation is the
    mistake that makes a compliance report worthless. The customer's turns are
    kept because *engagement* — the facts of the case — legitimately depends on
    what was said in the room by anybody.
    """

    adviser: tuple[str, ...]
    customer: tuple[str, ...] = ()
    scenario_id: str = ""

    @classmethod
    def from_view(cls, view: SessionView, *, scenario_id: str = "") -> "Transcript":
        return cls(
            adviser=view.trainee_turns, customer=view.customer_turns, scenario_id=scenario_id
        )

    # --------------------------------------------------------------- reading

    @property
    def adviser_text(self) -> str:
        return "\n".join(self.adviser)

    @property
    def all_text(self) -> str:
        return "\n".join(self.adviser + self.customer)

    def product_classes(self) -> frozenset[str]:
        """Which product classes this transcript is about. See `_PRODUCT_MARKERS`."""
        classes: set[str] = set()
        text = self.adviser_text
        for name, markers in _PRODUCT_MARKERS.items():
            if _hit(text, markers["strong"]):
                classes.add(name)
                continue
            weak = sum(
                len(re.findall(pattern, fold_typography(text), re.IGNORECASE))
                for pattern in markers["weak"]
            )
            if weak >= 2:
                classes.add(name)
        return frozenset(classes)

    def landmarks(self) -> Landmarks:
        no_rec = _first_matching(self.adviser, _NO_RECOMMENDATION)
        rec = _first_matching(self.adviser, _RECOMMENDATION)
        return Landmarks(
            recommendation=None if no_rec is not None else rec,
            conclusion=_first_matching(self.adviser, _CONCLUSION),
            decision_prompting=_turns_matching(self.adviser, _DECISION_PROMPTING),
            no_recommendation=no_rec,
        )

    def discovery_positions(self) -> tuple[int, ...]:
        """Turns where the adviser asked about the customer rather than told them.

        A question that is an understanding check ("do you follow that part?") or a
        call to action ("shall we get the paperwork started?") is not discovery,
        and both are excluded by name. Sentence-level, because a single turn
        routinely welds a statement to a question.
        """
        found: list[int] = []
        for index, turn in enumerate(self.adviser):
            if _hit(turn, _DISCOVERY):
                found.append(index)
                continue
            for sentence in sentences(turn):
                if not is_question(sentence):
                    continue
                if _hit(sentence, _UNDERSTANDING_CHECK) or _hit(sentence, _CONCLUSION):
                    continue
                found.append(index)
                break
        return tuple(found)

    def first(self, patterns: Sequence[str]) -> int | None:
        return _first_matching(self.adviser, patterns)

    def says(self, patterns: Sequence[str]) -> str | None:
        return _hit(self.adviser_text, patterns)

    def anyone_says(self, patterns: Sequence[str]) -> str | None:
        return _hit(self.all_text, patterns)

    def quote(self, index: int | None, limit: int = 96) -> str:
        if index is None or index >= len(self.adviser):
            return ""
        turn = " ".join(self.adviser[index].split())
        clipped = turn if len(turn) <= limit else turn[: limit - 1] + "…"
        return f"turn {index}: {clipped!r}"

    def sentences_with(self, patterns: Sequence[str]) -> tuple[str, ...]:
        """Adviser sentences containing any of `patterns`.

        Sentence-level rather than turn-level because the prescribed-unit probes
        need the figure and the thing it is a figure *of* in the same clause: "the
        commission I receive is one and a half per cent of the amount you invest"
        and "the annual management charge is 0.68 per cent a year" are two
        percentages in one turn answering two different requirements.
        """
        found: list[str] = []
        for turn in self.adviser:
            for sentence in re.split(r"(?<=[.!?])\s+|\n", turn):
                if sentence.strip() and _hit(sentence, patterns):
                    found.append(sentence.strip())
        return tuple(found)


# --------------------------------------------------------------------------- #
# The output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EntryVerdict:
    """One register entry, decided, with the paragraph it rests on.

    `citation` and `research` are copied off the `RegisterEntry` rather than
    restated, so any verdict this object carries can be traced to a
    paragraph-level source and to the section of the research file that
    established it. That traceability is the reason the register is data.
    """

    entry_id: str
    regime: str
    kind: str
    status: Status
    reason: str
    citation: str
    research: str
    evidence: tuple[str, ...] = ()
    #: What the operationalisation of a sourced requirement assumes. Always an
    #: ASSUMPTION; never blank for a probed entry.
    basis: str = ""
    #: What this instrument could not see about an entry it nonetheless decided.
    residue: str = ""
    #: True for a gate or a prohibition: a miss here fails the session whatever
    #: any score says.
    decisive: bool = False
    #: For a miss: what the register's own logic caught it on. The vocabulary for
    #: the shadow comparison — "absence" is the one a keyword check also finds, and
    #: "position", "unit", "polarity" and "form-of-words" are the four a keyword
    #: check cannot.
    miss_class: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"{self.entry_id}: status {self.status!r} not in {STATUSES}")

    @property
    def failed(self) -> bool:
        return self.status == "missed"

    def render(self) -> str:
        head = f"    [{self.status:<15}] {self.entry_id} ({self.kind}) — {self.citation}"
        lines = [head, f"        {self.reason}"]
        for item in self.evidence:
            lines.append(f"        evidence: {item}")
        if self.residue:
            lines.append(f"        residue: {self.residue}")
        if self.basis:
            lines.append(f"        ASSUMPTION: {self.basis}")
        return "\n".join(lines)


@dataclass(frozen=True)
class RegimeVerdict:
    """What one regime's register says about one transcript."""

    regime: str
    scenario_id: str
    verdict: Verdict
    entries: tuple[EntryVerdict, ...]
    #: Entry ids in this register that no probe covers. Reported rather than
    #: skipped: an unprobed requirement is a requirement nothing grades, and it
    #: must not read as a satisfied one.
    unprobed: tuple[str, ...] = ()

    def of_status(self, status: Status) -> tuple[EntryVerdict, ...]:
        return tuple(e for e in self.entries if e.status == status)

    @property
    def missed(self) -> tuple[EntryVerdict, ...]:
        return self.of_status("missed")

    @property
    def gaps(self) -> tuple[EntryVerdict, ...]:
        return self.of_status("instrument-gap")

    @property
    def engaged(self) -> tuple[EntryVerdict, ...]:
        """Entries this transcript actually engaged — the real denominator."""
        return tuple(e for e in self.entries if e.status != "not-applicable")

    @property
    def decisive_misses(self) -> tuple[EntryVerdict, ...]:
        return tuple(e for e in self.missed if e.decisive)

    def reason(self) -> str:
        """The verdict in words, naming entries rather than summarising them."""
        if self.verdict == "fail":
            gate = self.decisive_misses
            lead = (
                f"gate or prohibition missed ({', '.join(e.entry_id for e in gate)}), "
                "which fails the session regardless of any score; "
                if gate
                else ""
            )
            return lead + "missed: " + ", ".join(
                f"{e.entry_id} [{e.citation}]" for e in self.missed
            )
        if self.verdict == "undecidable":
            return (
                "no requirement was missed, and "
                + ", ".join(f"{e.entry_id} [{e.citation}]" for e in self.gaps)
                + " engaged and cannot be decided by this instrument"
            )
        satisfied = self.of_status("satisfied")
        return (
            "every requirement this transcript engaged was satisfied: "
            + (", ".join(e.entry_id for e in satisfied) or "none engaged")
        )

    def summary(self) -> str:
        """One line, with both denominators. A bare rate here would be a defect."""
        return (
            f"{self.regime}: {self.verdict.upper()} — "
            f"{len(self.of_status('satisfied'))} satisfied, {len(self.missed)} missed, "
            f"{len(self.gaps)} undecidable of {len(self.engaged)} engaged "
            f"({len(self.entries)} entries in the register"
            + (f", {len(self.unprobed)} unprobed" if self.unprobed else "")
            + ")"
        )

    def render(self, *, all_entries: bool = False) -> str:
        lines = [self.summary(), f"    {self.reason()}"]
        for entry in self.entries:
            # `not-required` entries print even though they are not-applicable: a
            # recorded absence is the load-bearing half of a cross-market check,
            # and a report that hides it is the report that invents the
            # requirement next release.
            if all_entries or entry.status != "not-applicable" or entry.kind == "not-required":
                lines.append(entry.render())
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "regime": self.regime,
            "verdict": self.verdict,
            "reason": self.reason(),
            "entries": [
                {
                    "entry_id": e.entry_id,
                    "kind": e.kind,
                    "status": e.status,
                    "reason": e.reason,
                    "citation": e.citation,
                    "research": e.research,
                    "residue": e.residue,
                    "decisive": e.decisive,
                }
                for e in self.entries
            ],
            "unprobed": list(self.unprobed),
        }


# --------------------------------------------------------------------------- #
# The probes: one per register entry
# --------------------------------------------------------------------------- #

#: The signature a bespoke decider has: transcript in, (status, reason, evidence,
#: miss-class) out. Used for the entries whose logic is arithmetic rather than
#: lexical. The fourth element is what the register caught a miss on — a decider
#: that finds a *late* disclosure of a prescribed unit has caught it on position,
#: not on the unit, and the shadow comparison reports the difference.
Decider = Callable[["Probe", Transcript], tuple[Status, str, tuple[str, ...], str]]


@dataclass(frozen=True)
class Probe:
    """How one cited requirement is looked for in a transcript.

    The requirement, its `kind`, its `timing` and its citation all come from the
    register at evaluation time. Everything in *this* object is the
    operationalisation, and `basis` is the sentence a reader sees saying so.
    """

    entry_id: str
    basis: str
    #: Product classes this requirement reaches. Empty means any.
    applies_to: frozenset[str] = frozenset()
    #: Extra engagement conditions, all of which must hold.
    engages_when: tuple[str, ...] = ()
    #: Engagement requires this landmark to exist: "recommendation",
    #: "conclusion", "recommendation-or-conclusion",
    #: "recommendation-and-conclusion", or "" for none. Which one a requirement
    #: needs is read off its own `timing` phrase — "before the transaction is
    #: concluded" for a duty that attaches to advising needs both.
    needs_landmark: str = ""
    #: Engagement requires a decision-prompting turn.
    needs_decision_turn: bool = False
    #: What satisfies it. For `verbatim`, the prescribed form of words.
    satisfied_by: tuple[str, ...] = ()
    #: Satisfied by discovery having happened, rather than by a form of words.
    #: The three "did you obtain the information you were required to obtain"
    #: requirements — one per regime that has one — are all of this shape.
    satisfied_by_discovery: bool = False
    #: For a prohibition: presence fails.
    forbidden: tuple[str, ...] = ()
    #: Positional rule: "before-recommendation" (strictly), "by-window-end" (at or
    #: before the call to action), or "".
    position: str = ""
    #: The judge this requirement's residual limb would need. Routed through
    #: `lab.judges.registry`; never gates.
    judge: str = ""
    #: Reported alongside a decided status.
    residue: str = ""
    #: Where a keyword check would look for this requirement, for the control arm
    #: in `NaiveShadow`. Defaults to this probe's own patterns; set explicitly
    #: where the lax version of the check is looser than the strict one — a
    #: keyword check credits a *paraphrase* of a verbatim rule and credits *any*
    #: duration for a prescribed one.
    naive_vocabulary: tuple[str, ...] = ()
    #: An ASSUMPTION-labelled detector that is computed and reported but never
    #: allowed to decide, because it has no calibrated TNR. Patterns for the ones
    #: that are lexical; `advisory_positional` for the ones that are not, which
    #: returns its own evidence sentence or an empty string.
    advisory_detector: tuple[str, ...] = ()
    advisory_positional: Callable[["Transcript"], str] | None = None
    advisory_note: str = ""
    decider: Decider | None = None


def _cooling_off_decider(days: float, trigger_label: str, triggers: tuple[str, ...]) -> Decider:
    """Decide a cooling-off entry on its duration AND its start trigger.

    Two limbs, and the register says why both matter: the trigger is the half that
    decides whether the customer still has the right when they try to use it
    (`divergence-cooling-off-duration-and-trigger`, regulators.md §6 D7). A check
    keyed only on the number is wrong about the trigger, so this one names which
    limb failed.
    """

    def decide(probe: "Probe", tx: Transcript) -> tuple[Status, str, tuple[str, ...], str]:
        sentences = tx.sentences_with(_COOLING_OFF_REFERENCE)
        stated = sorted({d for s in sentences for d in _day_counts(s)})
        trigger = tx.says(triggers)
        if not stated:
            return (
                "missed",
                "the period is discussed and no number of days is stated, so the "
                "prescribed unit is not disclosed",
                tuple(sentences[:2]),
                "unit",
            )
        if days not in stated:
            return (
                "missed",
                f"the prescribed unit is {days:.0f} days and the transcript states "
                f"{', '.join(f'{d:.0f}' for d in stated)}"
                + ("" if trigger else f"; the {trigger_label} is also not stated"),
                tuple(sentences[:2]),
                "unit",
            )
        if trigger is None:
            return (
                "missed",
                f"{days:.0f} days is right and the {trigger_label} is not stated, "
                "which is the limb that decides whether the customer still has the "
                "right when they try to use it",
                tuple(sentences[:2]),
                "unit",
            )
        return (
            "satisfied",
            f"{days:.0f} days stated, and the {trigger_label} stated as required "
            f"(matched {trigger!r})",
            tuple(sentences[:1]),
            "",
        )

    return decide


def _whole_percentage_ceiling(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """The SFC unit: a percentage of the investment amount, at a whole point.

    HK-1 ¶8.3 Part A(a)(i) and its Notes prescribe a *unit*, not an amount, and
    that is the difference this probe exists to compute rather than assert: three
    per cent of the sum invested satisfies it by accident, and one and a half per
    cent of the same sum does not satisfy it at all.
    """
    sentences = [s for s in tx.sentences_with(_REMUNERATION)]
    basis_pattern = (
        r"\bof (what|the amount) you invest\b",
        r"\bof the (sum|amount) invested\b",
        r"\bof the investment amount\b",
        r"\bper transaction\b",
    )
    for sentence in sentences:
        values = _percentages(sentence)
        if not values:
            continue
        if _hit(sentence, basis_pattern) is None:
            continue
        whole = [v for v in values if float(v).is_integer()]
        if whole:
            return (
                "satisfied",
                f"disclosed as {whole[0]:.0f} per cent of the invested amount — a whole "
                "percentage point, which is the prescribed unit",
                (sentence,),
                "unit",
            )
        return (
            "missed",
            f"disclosed as {values[0]:g} per cent of the invested amount, which is not a "
            "whole percentage point; the requirement is a unit requirement, not an "
            "amount requirement, so a correct figure in the wrong unit misses it",
            (sentence,),
            "unit",
        )
    if not sentences:
        return (
            "missed",
            "the transaction proceeds and no monetary benefit is disclosed in any unit",
            (),
            "unit",
        )
    return (
        "missed",
        "remuneration is discussed but not as a percentage of the invested amount, so the "
        "prescribed unit is absent",
        (sentences[0],),
        "unit",
    )


def _mas_commission_amount(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """MAS-2 ¶18: the *amount* of commission, in a figure of any unit."""
    sentences = tx.sentences_with(_REMUNERATION)
    for sentence in sentences:
        values = _percentages(sentence)
        cash = re.search(r"\b(\d[\d,]*|[a-z\- ]{3,30})\s*(pounds|dollars)\b", sentence, re.I)
        if values or cash:
            return (
                "satisfied",
                "the amount received on the recommended product is stated as a figure "
                f"({'a percentage of the sum invested' if values else 'a cash amount'})",
                (sentence,),
                "",
            )
    if not sentences:
        return (
            "instrument-gap",
            "a recommendation was made and the transcript says nothing about the firm's "
            "remuneration in either direction; whether commission is received on this "
            "product is not a fact a transcript contains, so the requirement can be "
            "neither discharged nor breached on this evidence",
            (),
            "absence",
        )
    return (
        "missed",
        "remuneration is discussed without an amount or an estimated rate",
        (sentences[0],),
        "absence",
    )


def _mas_past_performance(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """MAS-2 ¶25(c): the substance, plus the source where figures are used.

    The counterpart of `fca-past-performance-verbatim` and the reason `kind` is
    load-bearing: the same sentence discharges both, and a *paraphrase* discharges
    this one and misses the FCA's.
    """
    caveat = (
        r"past performance is not\b",
        r"past performance is no\b",
        r"\bnot necessarily indicative of future\b",
        r"\bnot a (reliable indicator|guide) (of|to) future\b",
        r"\btells you nothing about next year\b",
        r"\bwhat it did last year tells you nothing\b",
    )
    hit = tx.says(caveat)
    if hit is None:
        return (
            "missed",
            "past performance is referred to and the client is not advised that it is not "
            "necessarily indicative of future performance",
            (),
            "absence",
        )
    if tx.says(_PERFORMANCE_FIGURE) and tx.says((r"\bsource\b", r"\bfactsheet\b")) is None:
        return (
            "missed",
            "the substance limb is discharged but a performance figure is used and its "
            "source is not made known, which ¶25(c) requires in the same breath",
            (),
            "",
        )
    return (
        "satisfied",
        f"the substance is conveyed (matched {hit!r}); wording is free under this regime, "
        "and the source limb bites only where a figure is used to illustrate returns",
        (),
        "",
    )


def _mas_life_policy_carve_out(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """MAS-2 ¶22: the carve-out, recorded so nothing can invent the requirement.

    `kind: not-required`, so there is nothing here to miss. What the probe adds is
    the observation that makes the absence legible in a report: whether the
    transcript shows the distribution-cost line that the regime asks for *instead*
    of the adviser's own remuneration.
    """
    shown = tx.says((r"\bdistribution cost\b",))
    declined = tx.says((r"\bnot required to disclose my own remuneration\b",))
    if shown and declined:
        return (
            "not-applicable",
            "this regime does not require the adviser's own remuneration on a life policy: "
            "the distribution-cost item in the policy illustration is what is owed, and it "
            "is shown. An omission here is the rule being followed, not a gap — and a "
            "cross-market checker with one commission rule per jurisdiction fails this "
            "session for it",
            tuple(tx.sentences_with((r"\bdistribution cost\b",))[:1]),
            "",
        )
    return (
        "not-applicable",
        "this regime does not require the adviser's own remuneration to be disclosed on a "
        "life policy, so there is nothing here that an omission could miss",
        (),
        "",
    )


def _fca_cash_terms(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """COBS 6.1A.24R: the total adviser charge in cash terms, early.

    A prescribed unit, and the register says why it matters: it is the cheapest
    high-confidence compliance signal available (regulators.md §8). Nil counts —
    "there is no charge to you" states the charge in cash terms.
    """
    cash = (
        r"\b(one thousand|two thousand|£\s?\d|\d[\d,]{2,})\s*(six hundred\s*)?(pounds)?\b",
        r"\bpounds\b",
        r"\bno charge to you\b",
        r"\bcosts you nothing\b",
    )
    positions = _turns_matching(tx.adviser, cash)
    window = tx.landmarks().window_end
    if not positions:
        return (
            "missed",
            "the total adviser charge is never expressed in cash terms",
            (),
            "absence",
        )
    if window is not None and min(positions) > window:
        return (
            "missed",
            f"the cash figure appears at turn {min(positions)}, after the call to action at "
            f"turn {window}; 'as early as practicable' is not satisfied by a figure the "
            "customer hears once she has already agreed",
            (tx.quote(min(positions)),),
            "position",
        )
    return (
        "satisfied",
        f"the charge is stated in cash terms at turn {min(positions)}"
        + (f", at or before the call to action at turn {window}" if window is not None else ""),
        (tx.quote(min(positions)),),
        "",
    )


def _mas_selected_client_gate(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """MAS-3 ¶10A/¶10C/¶10D: two negatives, and one of two permitted routes.

    ¶10D allows either a qualifying trusted individual present or a written
    statement declining one, so a probe that credits only the first would be wrong
    about the rule in the same way a scorer would be.
    """
    # The two routes ¶10D permits, and the refusal to proceed. Deliberately not
    # including "come back another day": the adviser in
    # `conflict-vulnerable-customer-keen-to-sign` says that phrase while *reciting
    # the customer's objection to it*, and a probe that credits a recitation as a
    # route taken would pass the row this gate exists to fail.
    routes = (
        r"\btrusted (individual|person)\b",
        r"\bsomeone with you\b",
        r"\banother person (present|with you)\b",
        r"\bwritten statement declining\b",
        r"\bdeclin(e|ing) (one|a trusted)\b",
        r"\b(we|i) cannot proceed\b",
    )
    hit = tx.says(routes)
    if hit:
        return (
            "satisfied",
            f"one of the two routes ¶10D permits is taken (matched {hit!r})",
            (),
            "waiver",
        )
    waiver = tx.says(_WAIVER_ACCEPTED)
    return (
        "missed",
        "two of the three objective facts are negative on the customer's own account and "
        "the process proceeds with neither a qualifying trusted individual present nor a "
        "written statement declining one"
        + (
            f"; the customer's keenness is accepted instead (matched {waiver!r})"
            if waiver
            else ""
        ),
        tuple(tx.quote(i) for i in _turns_matching(tx.adviser, _WAIVER_ACCEPTED)[:1]),
        "waiver",
    )


def _understanding_check(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """PRIN 2A.5.9R's check, and the addressee this instrument has no field for.

    Two limbs. 2A.5.9R — did the firm ask the customer whether they understand —
    is deterministic on a transcript. 2A.5.3R — was the communication *likely to
    be understood* — is a judgement, and the judge it would need is named on the
    probe and routed through `lab.judges`; it does not gate.

    The third thing, and the reason this probe can abstain: 2A.5.9R names the
    RETAIL CUSTOMER. Where the transcript shows a third party being addressed
    instead, the answer turns on an addressee, and an addressee is not a field
    anywhere in this repo. That returns `instrument-gap`.
    """
    check_at = tx.first(_UNDERSTANDING_CHECK)
    vocative = tx.says(_THIRD_PARTY_ADDRESSEE)
    third_person = tx.says(_BUYER_IN_THIRD_PERSON)
    if vocative and third_person:
        return (
            "instrument-gap",
            "an understanding check is present and the transcript shows it addressed to a "
            f"third party (matched {vocative!r}) while the customer is referred to in the "
            f"third person (matched {third_person!r}). 2A.5.9R names the retail customer, "
            "so the answer turns on the addressee — and a disclosure's recipient is not a "
            "field in this register, this evaluator or the scorer. Abstaining rather than "
            "crediting the check",
            (tx.quote(check_at),) if check_at is not None else (),
            "",
        )
    if check_at is None:
        return (
            "missed",
            "information prompting a decision was given and the customer was never asked "
            "whether they understand or have further questions",
            (),
            "",
        )
    return (
        "satisfied",
        f"an understanding check is asked at turn {check_at}, at a turn that prompts a "
        "decision",
        (tx.quote(check_at),),
        "",
    )


def _sfc_language_of_choice(
    probe: "Probe", tx: Transcript
) -> tuple[Status, str, tuple[str, ...], str]:
    """HK-1 ¶8.3A(d) and Schedule 1: the client's language, as a rule not an outcome.

    The prescribed form is a *declaration* about an artefact, and a transcript
    cannot contain an artefact. What it can contain is the adviser establishing
    the client's preference and recording it, and that is what this probe decides
    on — with the artefact itself as an explicit residue.
    """
    establishes = tx.says(
        (
            r"\b(chinese|english)\b.{0,40}\b(or|定)\b.{0,20}\b(english|chinese|中文)\b",
            r"\byour choice\b",
            r"\blanguage of (the |your )?choice\b",
            r"\b用中文 定\b",
        )
    )
    records = tx.says((r"\bnote\b.{0,20}\bfile\b", r"\bnote 落\b", r"\brecord(ed)? (it|that)\b"))
    if establishes and records:
        return (
            "satisfied",
            f"the client's language preference is established (matched {establishes!r}) and "
            f"recorded (matched {records!r}), which are the two limbs of the Schedule 1 "
            "declaration a transcript can carry",
            (),
            "absence",
        )
    if establishes:
        return (
            "missed",
            "the preference is established and nothing records it, and Schedule 1 requires "
            "a licensed staff member to confirm the statement was provided in the language "
            "of the client's choice",
            (),
            "absence",
        )
    return (
        "missed",
        "a language question is live in this session and the client's preference for the "
        "written disclosure is never established",
        (),
        "absence",
    )


def _negative_only(breach_label: str) -> Decider:
    """Satisfied unless the transcript refutes it.

    For the requirements an instrument over a transcript can only ever *refute*:
    "fair, clear and not misleading", "suitability reasonable in all the
    circumstances". A pattern set cannot certify that a communication was fair; it
    can find a construction that makes it not. So a clean result here means "no
    breach detected", never "confirmed compliant", and the residue says so on
    every single one.
    """

    def decide(probe: "Probe", tx: Transcript) -> tuple[Status, str, tuple[str, ...], str]:
        breach = tx.says(probe.forbidden) if probe.forbidden else None
        if breach:
            return (
                "missed",
                f"{breach_label} (matched {breach!r})",
                tuple(tx.quote(i) for i in _turns_matching(tx.adviser, probe.forbidden)[:1]),
                "",
            )
        return (
            "satisfied",
            "no breach of this requirement is detectable in the transcript",
            (),
            "",
        )

    # The polarity, marked so `RegimeEvaluator.naive_shadow` can model the lax
    # instrument honestly. A refutation-only requirement has the same polarity as a
    # `prohibition`: an engineer writing a keyword check has nothing to *look for*,
    # so the requirement is credited by silence. Without this marker the naive
    # control would treat the refutation patterns as things it must find in order to
    # credit, which is backwards, and would make the lax instrument stricter than
    # the register on exactly the requirement the register is least able to decide.
    decide.refutation_only = True  # type: ignore[attr-defined]
    return decide


#: One probe per register entry, keyed by entry id. Thirty-six entries, and an
#: entry with no probe here is reported as unprobed by `RegimeVerdict` rather than
#: counted as satisfied — a requirement nothing grades must not read green.
PROBES: dict[str, Probe] = {
    # ------------------------------------------------------------------ FCA
    "fca-adviser-charging-only": Probe(
        entry_id="fca-adviser-charging-only",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        forbidden=(
            r"\bthe provider pays us\b",
            r"\bwe receive a commission\b",
            r"\bthe (bank|fund house|insurer) (is )?pa(ys|id)\b",
            r"\bcommission,? and on this fund\b",
            r"\bcommission (from|paid by) the (provider|fund)\b",
        ),
        basis=(
            "the ban is read as breached where the adviser describes a provider paying the "
            "firm on a product it has recommended; a transcript cannot show what the firm "
            "actually accepted, only what it says it accepts"
        ),
    ),
    "fca-charging-structure-in-writing-before": Probe(
        entry_id="fca-charging-structure-in-writing-before",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        satisfied_by=_CHARGES,
        position="before-recommendation",
        residue=(
            "the medium limb — 'in writing' — is not observable in a transcript unless the "
            "adviser narrates the document; a position that satisfies this probe does not "
            "establish that anything was in writing"
        ),
        basis=(
            "'in good time before the personal recommendation' is read strictly: the "
            "charging structure must appear at a turn before the one that proposes the "
            "product. A same-turn disclosure misses. This is the strictest defensible "
            "reading of 'in good time before' and it is where this probe is most arguable"
        ),
    ),
    "fca-adviser-charge-cash-terms": Probe(
        entry_id="fca-adviser-charge-cash-terms",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        decider=_fca_cash_terms,
        naive_vocabulary=(r"\bcharge\b", r"\bpounds\b", r"\bper cent\b"),
        basis=(
            "'as early as practicable' is read as at or before the call to action; a nil "
            "charge stated as 'no charge to you' counts as cash terms"
        ),
    ),
    "fca-past-performance-verbatim": Probe(
        entry_id="fca-past-performance-verbatim",
        engages_when=_PAST_PERFORMANCE_REFERENCE,
        satisfied_by=(r"past performance is not a reliable indicator of future results",),
        naive_vocabulary=(r"\bpast performance\b", r"\btrack record\b"),
        basis=(
            "the prescribed form of words is taken verbatim from the register entry's own "
            "requirement text; a paraphrase misses, which is the entire difference between "
            "this entry and mas-past-performance-substance. Prominence is not tested — see "
            "the advisory detector on fca-restricted-advice-oral-disclosure"
        ),
    ),
    "fca-suitability-report-before-conclusion": Probe(
        entry_id="fca-suitability-report-before-conclusion",
        applies_to=frozenset({"investment"}),
        # Both landmarks: COBS 9A attaches to the giving of a personal
        # recommendation, and the report is owed before the transaction is
        # concluded. An execution-only order taken with no recommendation engages
        # neither limb, which is why `divergence-unsolicited-note-failing-to-advise`
        # passes here and fails in Hong Kong.
        needs_landmark="recommendation-and-conclusion",
        satisfied_by=(
            r"\bsuitability report\b",
            r"\bsuitability assessment\b",
            r"\bmy recommendation in writing\b",
            r"\bwritten recommendation\b",
            r"\breport setting out why\b",
        ),
        position="by-window-end",
        basis=(
            "engagement requires a call to action, because the requirement bites 'before "
            "the transaction is concluded'; the distance-communication exception in "
            "9A.3.2R(3) needs the client's consent and a genuine option to delay, and this "
            "probe looks for neither, so it will read a validly deferred report as a miss"
        ),
    ),
    "fca-must-not-recommend-on-insufficient-information": Probe(
        entry_id="fca-must-not-recommend-on-insufficient-information",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        satisfied_by_discovery=True,
        forbidden=_WAIVER_ACCEPTED,
        position="before-recommendation",
        basis=(
            "'obtained the necessary information' is proxied by a question about the "
            "customer's objectives or circumstances before the recommendation, and the "
            "acceptance of a waiver overrides it. One question is not a fact-find, so this "
            "probe is generous in the passing direction; the waiver signal is what carries "
            "the gate rows"
        ),
    ),
    "fca-support-retail-customer-understanding": Probe(
        entry_id="fca-support-retail-customer-understanding",
        needs_landmark="recommendation-or-conclusion",
        needs_decision_turn=True,
        decider=_understanding_check,
        naive_vocabulary=_UNDERSTANDING_CHECK,
        judge="disclosure_understood",
        residue=(
            "2A.5.3R's 'likely to be understood' limb is a judgement and is not decided "
            "here; the judge that would decide it is named on this probe and routed through "
            "lab.judges, which has no calibrated judge for it"
        ),
        basis=(
            "the 2A.5.9R limb is read as: where a turn prompts a decision, the adviser must "
            "ask whether the customer understands. The forms of that question are enumerated "
            "and no source enumerates them"
        ),
    ),
    "fca-cancellation-30-days-life": Probe(
        entry_id="fca-cancellation-30-days-life",
        applies_to=frozenset({"life_policy"}),
        engages_when=_COOLING_OFF_REFERENCE,
        naive_vocabulary=(r"\bdays\b", r"\bfree[- ]look\b", r"\bcooling[- ]off\b"),
        decider=_cooling_off_decider(
            30.0,
            "start trigger (conclusion of the contract, or the consumer being informed of it)",
            (
                r"\bconclusion of the contract\b",
                r"\bwhen (you are|the consumer is) (told|informed)\b",
                r"\bfrom when you are informed\b",
            ),
        ),
        basis=(
            "30 days and its trigger are both read off the register entry; the entry also "
            "says the firm applies the longest applicable period, which this probe does not "
            "compute because it grades one regime at a time"
        ),
    ),
    "fca-fair-clear-not-misleading": Probe(
        entry_id="fca-fair-clear-not-misleading",
        decider=_negative_only("the communication misstates the risk or manufactures urgency"),
        # A refutation-only decider with no refutation patterns is a check that
        # cannot fail, and this repo's own rule is that such a check is worse than
        # none — `_negative_only` had an empty `forbidden` here until the
        # reachability test below was written, so the entry was satisfied on every
        # input including "this is risk-free and you cannot lose". Two limbs:
        #
        #   - the urgency limb is CITED. call_craft.md §8 C-6 puts "a deadline or
        #     scarcity claim with no evidenced basis in the product ledger" against
        #     this entry's own rule, COBS 4.2.1R (S-54), and calls it the most
        #     conversion-rewarded non-compliant move in the set.
        #   - the misstated-risk limb is an ASSUMPTION. No source enumerates the
        #     sentences that make a communication misleading; that an unqualified
        #     "risk-free" or "guaranteed returns" claim about a capital-at-risk
        #     product refutes 4.2.1R(1) follows from the rule rather than from a
        #     quotation, and the pattern list is this module's.
        #
        # Both limbs refute and neither certifies: a clean result still means "no
        # detected breach", which is why `_negative_only` says so in every reason
        # string. The patterns are deliberately unnegatable forms — the roleplay
        # pack's `compliance-cautious-tone-crosses-anyway` row exists because "I
        # would never say this is guaranteed" must NOT match a guarantee check, and
        # a test below holds that trap shut.
        forbidden=(
            r"\bguaranteed returns?\b",
            r"\bguaranteed to (beat|outperform|grow|return)\b",
            r"\bno real risk\b",
            r"\bno risk of loss\b",
            r"\brisk[- ]free\b",
            r"\byou cannot lose\b",
            r"\bnothing can go wrong\b",
            r"\bthis (month|week) only\b",
            r"\boffer (closes|ends) (today|tomorrow|this week)\b",
        ),
        advisory_positional=_minimisation_adjacency,
        advisory_note=(
            "the minimisation-adjacency detector fired. call_craft.md A-16 labels that "
            "detector an ASSUMPTION and names its false-positive mode, and it has no "
            "calibrated TNR in this repo, so it is reported and is NOT allowed to decide "
            "this entry"
        ),
        basis=(
            "an instrument over a transcript can refute 'fair, clear and not misleading' and "
            "cannot certify it, so a clean result means no detected breach and never "
            "confirmed compliance. The urgency limb is call_craft.md §8 C-6 against this "
            "entry's own COBS 4.2.1R; the misstated-risk limb is an ASSUMPTION, and adding "
            "neither moved any of the eighteen rows' verdicts — it removed a check that "
            "could not fail"
        ),
    ),
    "fca-restricted-advice-oral-disclosure": Probe(
        entry_id="fca-restricted-advice-oral-disclosure",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        satisfied_by=(r"\brestricted advice\b", r"\bindependent advice\b"),
        # The register entry's `timing` is "in good time before providing advice",
        # and a verbatim check with no positional rule graded this on presence
        # alone: the prescribed term said *after* the close satisfied it. The
        # landmark is the recommendation, which is the same landmark COBS 6.2B.33R
        # keys on, and adding it moved none of the eighteen rows' verdicts.
        position="before-recommendation",
        naive_vocabulary=(r"\brestricted\b", r"\bindependent\b", r"\bpanel\b"),
        advisory_detector=(r"\bmost of it is marketing\b", r"\byou did not ring me to hear about us\b"),
        advisory_note=(
            "the prescribed term is present and the turn carrying it is long and framed to "
            "discount itself. A prominence detector — turn length, position within the turn, "
            "what the neighbouring clauses do to it — is what would see that, and "
            "call_craft.md leaves its threshold an explicit ASSUMPTION with no calibrated "
            "TNR, so it is reported and does not decide this entry"
        ),
        basis=(
            "6.2B.33R(2) prescribes literal terms, so a substring check on the prescribed "
            "form of words is the correct instrument for this requirement — and a correct "
            "verbatim check still cannot see prominence"
        ),
    ),
    # ------------------------------------------------------------------ MAS
    "mas-fees-at-the-outset": Probe(
        entry_id="mas-fees-at-the-outset",
        engages_when=_REMUNERATION + _CHARGES,
        satisfied_by=_REMUNERATION + _CHARGES,
        position="by-window-end",
        residue="the writing limb of ¶16 is not observable in a transcript",
        basis=(
            "'at the outset' is read as at or before the call to action, which is weaker "
            "than the words; a stricter reading would need a notion of where the advisory "
            "process began that a five-turn excerpt does not carry"
        ),
    ),
    "mas-commission-amount": Probe(
        entry_id="mas-commission-amount",
        needs_landmark="recommendation",
        naive_vocabulary=_REMUNERATION,
        decider=_mas_commission_amount,
        basis=(
            "an 'amount' is read as any figure tied to the recommended product — a "
            "percentage of the sum invested counts, which is what makes this entry pass on "
            "a sentence that the SFC's unit rule fails"
        ),
    ),
    "mas-life-policy-distribution-cost": Probe(
        entry_id="mas-life-policy-distribution-cost",
        applies_to=frozenset({"life_policy"}),
        decider=_mas_life_policy_carve_out,
        basis=(
            "kind: not-required, so there is nothing to satisfy and nothing to miss; the "
            "probe only records what the regime asks for instead"
        ),
    ),
    "mas-past-performance-substance": Probe(
        entry_id="mas-past-performance-substance",
        engages_when=_PAST_PERFORMANCE_REFERENCE,
        decider=_mas_past_performance,
        basis=(
            "the substance limb is read as any construction conveying that past performance "
            "does not predict future performance; the source-of-data limb is only required "
            "where a performance figure is actually used"
        ),
    ),
    "mas-oral-performance-needs-simultaneous-writing": Probe(
        entry_id="mas-oral-performance-needs-simultaneous-writing",
        engages_when=_PERFORMANCE_FIGURE,
        satisfied_by=_WRITING_PROVIDED,
        basis=(
            "engagement requires an actual performance figure, because ¶26(b) governs "
            "disclosing performance rather than warning about it. No row in the advisory "
            "corpus quotes a performance figure, so this entry never engages there — which "
            "is a coverage statement about the corpus, not a pass"
        ),
    ),
    "mas-recommendation-document-before-signing": Probe(
        entry_id="mas-recommendation-document-before-signing",
        # ¶36's document contains "the recommendation with its basis", so a session
        # with no recommendation in it does not engage the requirement.
        needs_landmark="recommendation-and-conclusion",
        satisfied_by=(
            r"\brecommendation (document|in writing)\b",
            r"\binformation summary\b",
            r"\bthe basis (for|of) (my|the) recommendation\b",
            r"\bdocument (setting out|containing)\b",
            r"\bbefore you sign\b.{0,60}\b(document|illustration|summary)\b",
        ),
        position="by-window-end",
        basis=(
            "¶36 asks for a document containing the information summary and the "
            "recommendation with its basis; the probe looks for the adviser saying that such "
            "a document is being furnished. An adviser who furnishes one silently misses"
        ),
    ),
    "mas-selected-client-gate": Probe(
        entry_id="mas-selected-client-gate",
        engages_when=(
            r"\bforms are hard\b",
            r"\bsign and ask (my|your) son\b",
            r"\bwritten english is not strong\b",
            r"\bleft school\b",
            r"\bsixty[- ]eight\b",
            # No bare-number age pattern: `\b68\b` matches inside "0.68 per cent",
            # and an engagement test that fires on a fund charge is worse than one
            # that misses an age nobody restated.
        ),
        decider=_mas_selected_client_gate,
        basis=(
            "the two negatives are read from the facts as they appear in the room — the "
            "adviser's restatement or the customer's own words — because that is where a "
            "transcript carries them. The determination and declaration ¶10C requires are "
            "records, and records are not observable here"
        ),
    ),
    "mas-insufficient-information-forces-negative-cka": Probe(
        entry_id="mas-insufficient-information-forces-negative-cka",
        engages_when=(
            r"\bno notes\b",
            r"\bnot done one of these\b",
            r"\bno derivatives\b",
            r"\bstructured note\b",
            r"\bthe note your colleague\b",
        ),
        satisfied_by=(
            r"\bwritten notice\b",
            r"\bsenior management\b",
            r"\bwritten confirmation\b",
            r"\bknowledge assessment\b",
            r"\bcustomer knowledge\b",
        ),
        basis=(
            "engagement is read as an unlisted SIP plus the client's own account of no "
            "experience of the product type; satisfaction requires one of the three "
            "procedural artefacts ¶24-¶25 name to be mentioned at all"
        ),
    ),
    "mas-free-look-14-days": Probe(
        entry_id="mas-free-look-14-days",
        applies_to=frozenset({"life_policy"}),
        engages_when=_COOLING_OFF_REFERENCE,
        naive_vocabulary=(r"\bdays\b", r"\bfree[- ]look\b", r"\bcooling[- ]off\b"),
        decider=_cooling_off_decider(
            14.0,
            "start trigger (receipt of the policy document)",
            (
                r"\bpolicy document reaches you\b",
                r"\breceipt of the policy\b",
                r"\bdocument is in your hands\b",
                r"\bwhen you receive the policy\b",
            ),
        ),
        basis=(
            "'at least 14 days' is read as satisfied by a stated 14 with the receipt "
            "trigger; a stated 14 with the wrong trigger misses, which is the half of this "
            "requirement that decides whether the right survives"
        ),
    ),
    # --------------------------------------------------------------- Reg BI
    "reg-bi-written-disclosure-before-or-at-recommendation": Probe(
        entry_id="reg-bi-written-disclosure-before-or-at-recommendation",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        satisfied_by=_CHARGES + _REMUNERATION + tuple(_WRITING_PROVIDED),
        position="by-window-end",
        residue=(
            "the writing limb is evidenced only where the adviser narrates the document; "
            "where they do not, this probe has decided the substance and the position and "
            "not the medium"
        ),
        basis=(
            "'prior to or at the time of the recommendation' is measured against the call to "
            "action, on the register's own ground that a recommendation is 'expressly not "
            "susceptible to a bright-line definition' (US-1); scope, fees or conflicts "
            "spoken before the close satisfy it"
        ),
    ),
    "reg-bi-fees-standardised-ranges-acceptable": Probe(
        entry_id="reg-bi-fees-standardised-ranges-acceptable",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        satisfied_by=_CHARGES,
        basis=(
            "deliberately the weakest fee probe of the four regimes, because the register "
            "entry is deliberately the weakest requirement: a range or a hypothetical "
            "satisfies it, so any fee statement does"
        ),
    ),
    "reg-bi-no-suitability-report": Probe(
        entry_id="reg-bi-no-suitability-report",
        basis="kind: not-required — the recorded absence is the requirement",
    ),
    "reg-bi-no-duty-to-monitor": Probe(
        entry_id="reg-bi-no-duty-to-monitor",
        basis="kind: not-required — the recorded absence is the requirement",
    ),
    "reg-bi-no-vulnerability-construct": Probe(
        entry_id="reg-bi-no-vulnerability-construct",
        basis="kind: not-required — the recorded absence is the requirement",
    ),
    "reg-bi-care-obligation-binds-the-recommendation": Probe(
        entry_id="reg-bi-care-obligation-binds-the-recommendation",
        applies_to=frozenset({"investment"}),
        needs_landmark="recommendation",
        satisfied_by_discovery=True,
        forbidden=_WAIVER_ACCEPTED,
        position="before-recommendation",
        residue=(
            "whether the recommendation was in fact in the customer's best interest is not "
            "decided here; what is decided is whether the obligation was engaged and "
            "whether the profile it binds to was obtained"
        ),
        basis=(
            "the Care Obligation binds the recommendation, so engagement follows the "
            "recommendation landmark and the absence of one — as in the execution-only row "
            "— makes the entry inapplicable rather than satisfied"
        ),
    ),
    "reg-bi-conflict-elimination-time-limited-incentives": Probe(
        entry_id="reg-bi-conflict-elimination-time-limited-incentives",
        forbidden=(
            r"\bsales contest\b",
            r"\bthis month only\b",
            r"\bmy quota\b",
            r"\bbonus (on|for) this (product|sale)\b",
            r"\bincentive (on|for) this (product|sale)\b",
        ),
        basis=(
            "a firm-level obligation is being looked for in a transcript, which can only "
            "ever catch an adviser who says the contest out loud; a firm that runs one "
            "quietly is invisible here"
        ),
    ),
    "reg-bi-no-cooling-off": Probe(
        entry_id="reg-bi-no-cooling-off",
        basis="kind: not-required — the recorded absence is the requirement",
    ),
    # --------------------------------------------------------------- SFC/IA
    "sfc-ia-monetary-benefit-percentage-ceiling": Probe(
        entry_id="sfc-ia-monetary-benefit-percentage-ceiling",
        engages_when=_REMUNERATION,
        naive_vocabulary=(r"\bper cent\b", r"\bcommission\b", r"\bmonetary benefit\b"),
        decider=_whole_percentage_ceiling,
        basis=(
            "the prescribed unit is read as a percentage of the invested amount at a whole "
            "percentage point; a figure that is correct as an amount and wrong as a unit "
            "misses, which is the second-order trap the commission row's notes name"
        ),
    ),
    "sfc-ia-transaction-information-before-or-at-entry": Probe(
        entry_id="sfc-ia-transaction-information-before-or-at-entry",
        needs_landmark="conclusion",
        satisfied_by=_CHARGES + _REMUNERATION + (r"\bcapital is at risk\b", r"\bget back less\b"),
        position="by-window-end",
        residue="whether anything was confirmed in writing after conclusion is not visible here",
        basis=(
            "¶8.3A's transaction information is proxied by the monetary-benefit and "
            "product-cost disclosures appearing at or before the point of entering the "
            "transaction"
        ),
    ),
    "sfc-ia-language-of-the-client-s-choice": Probe(
        entry_id="sfc-ia-language-of-the-client-s-choice",
        engages_when=_MULTILINGUAL,
        decider=_sfc_language_of_choice,
        residue=(
            "the artefact itself — a written risk disclosure statement in the chosen "
            "language, and the Schedule 1 declaration about it — is not in a transcript. "
            "Note also that this probe finds the ASCII fragments of a code-switched turn; "
            "the same behaviour conducted wholly in Chinese would be missed, which is the "
            "schema gap the lang suite records"
        ),
        basis=(
            "engagement requires a language question to be live in the session (Chinese "
            "characters, or the choice raised in terms). A monolingual English session with "
            "an English-speaking client is treated as not raising the artefact-language "
            "question, which is an assumption and is the reason this entry does not make "
            "every Hong Kong row undecidable"
        ),
    ),
    "sfc-ia-complex-product-information-before-decision": Probe(
        entry_id="sfc-ia-complex-product-information-before-decision",
        engages_when=(r"\bstructured note\b", r"\bthe note your colleague\b", r"\bderivative\b"),
        satisfied_by=(
            r"\bkey (nature|features)\b",
            r"\bhow (the|this) (note|product) works\b",
            r"\bwhat (the|this) (note|product) does\b",
            r"\bwarning\b",
            r"\bthe risks of\b",
            r"\bexplain the risks\b",
        ),
        position="by-window-end",
        basis=(
            "engagement is read from a complex-product marker; satisfaction requires the "
            "adviser to have said something about the product's nature, features or risks "
            "before the decision"
        ),
    ),
    "sfc-ia-suitability-reasonable-in-all-circumstances": Probe(
        entry_id="sfc-ia-suitability-reasonable-in-all-circumstances",
        needs_landmark="recommendation",
        satisfied_by_discovery=True,
        forbidden=_WAIVER_ACCEPTED,
        position="before-recommendation",
        judge="suitability_reasonable",
        residue=(
            "whether the recommendation was reasonable in all the circumstances is a "
            "judgement; the judge that would decide it is named here and routed through "
            "lab.judges, which has no calibrated judge for it, so what is decided is only "
            "whether the circumstances were obtained at all"
        ),
        basis=(
            "a substance test proxied by discovery before the recommendation. Generous in "
            "the passing direction by construction, and the residue says which part is "
            "undecided"
        ),
    ),
    "sfc-ia-unsolicited-derivative-duty-to-advise": Probe(
        entry_id="sfc-ia-unsolicited-derivative-duty-to-advise",
        engages_when=(r"\bstructured note\b", r"\bthe note your colleague\b", r"\bderivative\b"),
        satisfied_by=(
            r"\bi (do )?have to advise\b",
            r"\bi am required to (advise|assess)\b",
            r"\bi must (advise|explain|assess)\b",
            r"\bsuitab\w+ (assessment|for you)\b",
            r"\bbefore i can place\b",
            r"\bexplain the risks\b",
        ),
        basis=(
            "the duty is triggered by the ABSENCE of a recommendation plus no derivatives "
            "knowledge, so this probe engages on the product marker and is satisfied only by "
            "the adviser advising anyway. Every unlicensed-advice detector in this repo has "
            "the opposite polarity, and that is the point of the row this entry decides"
        ),
    ),
    "sfc-ia-more-assistance-for-the-inexperienced": Probe(
        entry_id="sfc-ia-more-assistance-for-the-inexperienced",
        engages_when=(
            r"\bnot done one of these\b",
            r"\bno notes\b",
            r"\bfirst time\b",
            r"\bi do not follow\b",
            r"\bdo not understand\b",
        ),
        satisfied_by=_UNDERSTANDING_CHECK
        + (r"\blet me (explain|put it)\b", r"\bin plain terms\b", r"\bput it against your own\b"),
        judge="disclosure_understood",
        residue=(
            "whether the assistance given was enough for this client's sophistication is a "
            "judgement, and the judge is not calibrated, so only its presence is decided"
        ),
        basis=(
            "engagement is read from an expressed lack of experience or understanding by "
            "either speaker; satisfaction from the adviser doing something about it"
        ),
    ),
    "sfc-ia-cooling-off-21-days": Probe(
        entry_id="sfc-ia-cooling-off-21-days",
        applies_to=frozenset({"life_policy"}),
        engages_when=_COOLING_OFF_REFERENCE,
        naive_vocabulary=(r"\bdays\b", r"\bfree[- ]look\b", r"\bcooling[- ]off\b"),
        decider=_cooling_off_decider(
            21.0,
            "start trigger (delivery of the policy or the cooling-off notice, whichever is earlier)",
            (r"\bwhichever is earlier\b", r"\bcooling[- ]off notice\b"),
        ),
        basis=(
            "21 days and the whichever-is-earlier ordering rule are both read off the "
            "register entry, whose own evidence field says the citation is secondary"
        ),
    ),
    "sfc-ia-benefit-illustration-no-emphasis-on-non-guaranteed": Probe(
        entry_id="sfc-ia-benefit-illustration-no-emphasis-on-non-guaranteed",
        engages_when=(
            r"\bprojected\b",
            r"\bassumed rate\b",
            r"\billustrat(ed|ive) (return|value)\b",
            r"\bnon[- ]guaranteed\b",
        ),
        satisfied_by=(
            r"\bnot guaranteed\b",
            r"\bneither guaranteed\b",
            r"\bpessimistic\b",
            r"\bmay not be achieved\b",
        ),
        basis=(
            "engagement requires a projected figure to be in play; no row in the advisory "
            "corpus projects one, so this entry never engages there"
        ),
    ),
}


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NaiveShadow:
    """The same register, checked the lax way, so the difference is a number.

    The control arm, in the same spirit as `roleplay.register.keyword_shadow_codes`
    and `lab.voice.calibration`'s naive baseline: an instrument kept beside the
    real one *because* it is wrong, so that "the register buys you something" is a
    measurement rather than an assertion.

    The naive check is built from the register's own vocabulary and then has
    everything else removed: no position (a disclosure counts wherever it lands),
    no unit arithmetic (a percentage is a percentage), no polarity (a commission
    *described* counts as a commission *disclosed*), and no distinction between a
    prescribed form of words and a paraphrase. That is the check a reasonable
    engineer writes in an afternoon, and it is what the near-miss rows were built
    to fool.
    """

    regime: str
    scenario_id: str
    engaged: tuple[str, ...]
    credited: tuple[str, ...]
    register_missed: tuple[str, ...]
    #: entry id -> what the register caught the miss on: position, unit, polarity,
    #: form-of-words, waiver, or absence.
    miss_classes: tuple[tuple[str, str], ...] = ()

    @property
    def over_credited(self) -> tuple[str, ...]:
        """Entries the naive check passes and the register does not. The finding."""
        return tuple(e for e in self.credited if e in self.register_missed)

    def over_credited_by_class(self) -> dict[str, tuple[str, ...]]:
        """The over-credits grouped by what the register caught them on.

        This is the argument the corpus exists to make, made as a number: an
        "absence" over-credit would be a bug in the control arm, and every other
        class is a requirement a vocabulary check cannot express.
        """
        classes = dict(self.miss_classes)
        grouped: dict[str, list[str]] = {}
        for entry_id in self.over_credited:
            grouped.setdefault(classes.get(entry_id, "unknown"), []).append(entry_id)
        return {k: tuple(v) for k, v in sorted(grouped.items())}

    @property
    def naive_verdict(self) -> str:
        return "pass" if len(self.credited) == len(self.engaged) else "fail"

    def summary(self) -> str:
        return (
            f"{self.regime}: naive check credits {len(self.credited)}/{len(self.engaged)} "
            f"engaged entries and would report {self.naive_verdict.upper()}; the register "
            f"missed {len(self.register_missed)}"
            + (
                "; over-credited: "
                + "; ".join(
                    f"{cls} ({', '.join(ids)})"
                    for cls, ids in self.over_credited_by_class().items()
                )
                if self.over_credited
                else "; no over-credit"
            )
        )


@dataclass
class RegimeEvaluator:
    """Compute one regime's verdict on one transcript, from that regime's register.

    Stateless by construction: no history, no cohort, no cross-session anything.
    That is deliberate and it is the contrast with `roleplay.scorer.RubricScorer`,
    whose cohort curve makes one session's grade depend on the sessions graded
    before it. Two calls with the same trace and the same regime return the same
    verdict, and a disputed verdict can be recomputed from the trace on disk
    months later.
    """

    registers: dict[str, Register] = field(default_factory=load_registers)
    probes: dict[str, Probe] = field(default_factory=lambda: dict(PROBES))
    #: Judge names already reported, so one run does not print the same
    #: "no such judge" line thirty times.
    _judges_reported: set[str] = field(default_factory=set, repr=False)

    # ---------------------------------------------------------------- public

    def evaluate(
        self, source: Trace | SessionView, *, regime: str, scenario_id: str = ""
    ) -> RegimeVerdict:
        """Grade `source` against `regime`'s register.

        Args:
            source: A `Trace`, or the `SessionView` read off one. Both are
                accepted because the scorer already builds the view and there is
                no reason to make a caller rebuild it — and because taking a trace
                keeps the invariant that every verdict is recomputable from the
                trace alone.
            regime: One of `roleplay.advisory.REGIMES`.
            scenario_id: Carried into the verdict for reporting. Read off the
                trace when one is given.
        """
        if regime not in REGIMES:
            raise KeyError(f"unknown regime {regime!r}; known: {sorted(REGIMES)}")
        register = self.registers.get(regime)
        if register is None:
            raise KeyError(f"no register loaded for regime {regime!r}")

        if isinstance(source, Trace):
            view = session_view(source)
            scenario_id = scenario_id or source.scenario_id
        else:
            view = source
        tx = Transcript.from_view(view, scenario_id=scenario_id)

        entries: list[EntryVerdict] = []
        unprobed: list[str] = []
        for entry in register.entries.values():
            probe = self.probes.get(entry.id)
            if probe is None:
                unprobed.append(entry.id)
                continue
            entries.append(self._decide(entry, probe, tx))

        missed = [e for e in entries if e.status == "missed"]
        gaps = [e for e in entries if e.status == "instrument-gap"]
        verdict: Verdict = "fail" if missed else ("undecidable" if gaps else "pass")
        return RegimeVerdict(
            regime=regime,
            scenario_id=scenario_id,
            verdict=verdict,
            entries=tuple(entries),
            unprobed=tuple(unprobed),
        )

    def naive_shadow(
        self, source: Trace | SessionView, *, regime: str, scenario_id: str = ""
    ) -> NaiveShadow:
        """Score the same transcript the lax way and return the disagreement.

        Deliberately not a fallback and never wired into `evaluate`: this is the
        control arm and its only job is to be wrong in a way the register is not.
        """
        verdict = self.evaluate(source, regime=regime, scenario_id=scenario_id)
        view = session_view(source) if isinstance(source, Trace) else source
        tx = Transcript.from_view(view)
        credited: list[str] = []
        engaged = tuple(e.entry_id for e in verdict.engaged)
        for entry_id in engaged:
            probe = self.probes[entry_id]
            kind = next(e.kind for e in verdict.entries if e.entry_id == entry_id)
            if kind == "prohibition" or getattr(probe.decider, "refutation_only", False):
                # The polarity inversion, and the whole reason the commission row
                # exists: a keyword check looking for a commission disclosure finds
                # one, credits it, and is right in three regimes out of four. A
                # refutation-only requirement inverts the same way — see
                # `_negative_only`, which marks its deciders for this branch.
                credited.append(entry_id)
                continue
            if probe.satisfied_by_discovery:
                if tx.discovery_positions():
                    credited.append(entry_id)
                continue
            vocabulary = probe.naive_vocabulary or (
                probe.satisfied_by + probe.forbidden + probe.engages_when
            )
            if not vocabulary:
                # A lax check has no words for this requirement, so it never fires
                # on it — which credits it. Silence is how a keyword instrument
                # passes the requirements it cannot express.
                credited.append(entry_id)
                continue
            if _hit(tx.adviser_text, vocabulary):
                credited.append(entry_id)
        return NaiveShadow(
            regime=regime,
            scenario_id=verdict.scenario_id,
            engaged=engaged,
            credited=tuple(credited),
            register_missed=tuple(e.entry_id for e in verdict.missed),
            miss_classes=tuple((e.entry_id, e.miss_class) for e in verdict.missed),
        )

    def evaluate_all(
        self, source: Trace | SessionView, *, scenario_id: str = ""
    ) -> dict[str, RegimeVerdict]:
        """The same transcript under every regime — the divergence question."""
        return {
            regime: self.evaluate(source, regime=regime, scenario_id=scenario_id)
            for regime in sorted(REGIMES)
        }

    # --------------------------------------------------------------- private

    def _decide(self, entry: RegisterEntry, probe: Probe, tx: Transcript) -> EntryVerdict:
        """One entry, decided. `kind` chooses the logic; nothing else may."""
        residue = probe.residue
        if probe.judge:
            note = self._judge_note(probe.judge)
            residue = f"{residue}. {note}" if residue else note

        default_class = {
            "prohibition": "polarity",
            "gate": "waiver",
            "prescribed-unit": "unit",
            "verbatim": "form-of-words",
        }.get(entry.kind, "absence")

        def built(
            status: Status,
            reason: str,
            evidence: tuple[str, ...] = (),
            miss_class: str = "",
        ) -> EntryVerdict:
            extra = ""
            fired = ""
            if probe.advisory_positional is not None:
                fired = probe.advisory_positional(tx)
            elif probe.advisory_detector and _hit(tx.adviser_text, probe.advisory_detector):
                fired = "matched " + repr(_hit(tx.adviser_text, probe.advisory_detector))
            if fired:
                extra = (
                    f" [ADVISORY DETECTOR fired ({fired}), and no verdict is taken from it: "
                    f"{probe.advisory_note}]"
                )
            return EntryVerdict(
                entry_id=entry.id,
                regime=entry.regime,
                kind=entry.kind,
                status=status,
                reason=reason + extra,
                citation=entry.source,
                research=entry.research,
                evidence=evidence,
                basis=probe.basis,
                residue=residue,
                decisive=entry.kind in {"gate", "prohibition"},
                miss_class=(miss_class or default_class) if status == "missed" else "",
            )

        # `not-required` is decided before anything else is even looked at. The
        # whole point of the kind is that no transcript feature can turn it into a
        # requirement.
        if entry.kind == "not-required":
            if probe.decider is not None:
                status, reason, evidence, miss_class = probe.decider(probe, tx)
                return built(status, reason, evidence, miss_class)
            return built(
                "not-applicable",
                f"{entry.regime} does not impose this requirement, so an adviser who omits "
                "it is compliant here. Recorded rather than skipped, because a checker that "
                "only stores requirements invents them in the market that has none",
            )

        engaged, why_not = self._engagement(probe, tx)
        if not engaged:
            return built("not-applicable", why_not)

        if probe.decider is not None:
            status, reason, evidence, miss_class = probe.decider(probe, tx)
            return built(status, reason, evidence, miss_class)

        if entry.kind == "prohibition":
            hit = tx.says(probe.forbidden)
            if hit:
                positions = _turns_matching(tx.adviser, probe.forbidden)
                return built(
                    "missed",
                    f"the prohibited conduct is present in terms (matched {hit!r}); this is "
                    "not a disclosure item and disclosing it does not cure it",
                    tuple(tx.quote(i) for i in positions[:1]),
                )
            return built(
                "satisfied", "nothing in the transcript describes the prohibited conduct"
            )

        # A declared waiver pattern is checked whatever the kind. It used to be read
        # only under `gate`, which meant the identical sentence — "you have said you
        # know the risks, so I will take you at your word" — failed the FCA gate at
        # COBS 9A.2.13R and was silently ignored by Reg BI's care obligation and the
        # SFC's "reasonable in all the circumstances", because those two entries are
        # `kind: substance`. Same words, same shape of failure, and fourteen declared
        # patterns doing nothing. The waiver still cannot *satisfy* anything; it can
        # only refute, and the reason string names the kind it refuted.
        if probe.forbidden:
            hit = tx.says(probe.forbidden)
            if hit:
                return built(
                    "missed",
                    f"the precondition is waived rather than met (matched {hit!r}); the duty "
                    "runs to the firm and the customer cannot discharge it",
                    tuple(tx.quote(i) for i in _turns_matching(tx.adviser, probe.forbidden)[:1]),
                    "waiver",
                )
        return self._positional(entry, probe, tx, built)

    def _positional(
        self,
        entry: RegisterEntry,
        probe: Probe,
        tx: Transcript,
        built: Callable[..., EntryVerdict],
    ) -> EntryVerdict:
        """Presence, and then position — decided on event-stream position only.

        Never on timestamps. Under a `FakeClock` every event in a roleplay session
        can share `ts=0.0`, and a `<=` on tied timestamps reads as "in order",
        which makes an ordering rule that silently cannot fail. See
        `lab.checks.contracts._sequence`.
        """
        positions = (
            tx.discovery_positions()
            if probe.satisfied_by_discovery
            else _turns_matching(tx.adviser, probe.satisfied_by)
        )
        if not positions and probe.satisfied_by_discovery:
            return built(
                "missed",
                "the adviser asked the customer nothing about their circumstances or "
                "objectives before proceeding",
            )
        if not positions:
            return built(
                "missed",
                f"nothing in the adviser's turns satisfies it (looked for: "
                f"{probe.satisfied_by[0] if probe.satisfied_by else 'no pattern'}"
                f"{' and ' + str(len(probe.satisfied_by) - 1) + ' more' if len(probe.satisfied_by) > 1 else ''})",
            )
        at = min(positions)
        marks = tx.landmarks()
        if probe.position == "before-recommendation" and marks.recommendation is not None:
            if at >= marks.recommendation:
                return built(
                    "missed",
                    f"present at turn {at}, which is not before the recommendation at turn "
                    f"{marks.recommendation}; the timing the register states is "
                    f"{entry.timing!r}, and position in the event stream is what decides it",
                    (tx.quote(at), tx.quote(marks.recommendation)),
                    miss_class="position",
                )
        if probe.position == "by-window-end" and marks.window_end is not None:
            if at > marks.window_end:
                return built(
                    "missed",
                    f"present at turn {at}, after the call to action at turn "
                    f"{marks.window_end}; the timing the register states is "
                    f"{entry.timing!r}, and a customer who has already agreed is not "
                    "choosing whether to accept it",
                    (tx.quote(at), tx.quote(marks.window_end)),
                    miss_class="position",
                )
        return built(
            "satisfied",
            f"satisfied at turn {at}"
            + (
                f", within the timing the register states ({entry.timing})"
                if entry.timing and probe.position
                else ""
            ),
            (tx.quote(at),),
        )

    def _engagement(self, probe: Probe, tx: Transcript) -> tuple[bool, str]:
        """Does this requirement reach this transcript at all?

        An honest engagement test is what keeps the instrument from inventing
        requirements — and it is also its main weakness in the other direction: an
        entry gated on a topic being raised cannot catch an adviser who never
        raises it. Both directions are stated in the CLI's limitations block.
        """
        classes = tx.product_classes()
        if probe.applies_to and not (probe.applies_to & classes):
            return (
                False,
                f"this requirement reaches {sorted(probe.applies_to)} and the transcript is "
                f"about {sorted(classes) or 'no product class this instrument recognises'}",
            )
        if probe.engages_when and tx.anyone_says(probe.engages_when) is None:
            return (
                False,
                "nothing in this session engages the requirement",
            )
        marks = tx.landmarks()
        if probe.needs_landmark == "recommendation" and marks.recommendation is None:
            return (
                False,
                "no personal recommendation is made in this session"
                + (
                    " — the adviser says in terms that none is being given, which is what "
                    "makes the duties that attach to advising inapplicable"
                    if marks.no_recommendation is not None
                    else ""
                ),
            )
        if probe.needs_landmark == "conclusion" and marks.conclusion is None:
            return (False, "the transaction is not concluded in this session")
        if probe.needs_landmark == "recommendation-or-conclusion" and (
            marks.recommendation is None and marks.conclusion is None
        ):
            return (False, "no recommendation is made and no transaction is concluded")
        if probe.needs_landmark == "recommendation-and-conclusion":
            if marks.recommendation is None:
                return (
                    False,
                    "the requirement attaches to a personal recommendation and none is made "
                    "in this session"
                    + (
                        " — the adviser says in terms that none is being given"
                        if marks.no_recommendation is not None
                        else ""
                    ),
                )
            if marks.conclusion is None:
                return (False, "the transaction is not concluded in this session")
        if probe.needs_decision_turn and not marks.decision_prompting:
            return (False, "no turn in this session puts a decision in front of the customer")
        return (True, "")

    def _judge_note(self, name: str) -> str:
        """Ask `lab.judges` about a judge, and report the answer. Never gate on it.

        This is the whole judge story in five lines. The registry is the authority
        on whether a model-graded check may decide anything, and the answer here is
        no: nothing registers a judge for these questions, so the substance limb is
        reported as undecided rather than guessed. If somebody later registers and
        calibrates one, this is the call site that will find it.
        """
        from lab.judges.registry import DEFAULT_REGISTRY

        try:
            judge = DEFAULT_REGISTRY.get(name)
        except KeyError:
            return (
                f"judge {name!r} is not registered in lab.judges, so its limb of this "
                "requirement is not decided and does not gate"
            )
        report = DEFAULT_REGISTRY.require_calibrated(judge, ci=False)
        if report is None:
            return (
                f"judge {name!r} is registered and uncalibrated; lab.judges refuses it as a "
                "gate, so its limb is reported and not decided"
            )
        return (
            f"judge {name!r} is calibrated ({report.summary_line()}); this evaluator still "
            "decides the deterministic limb only, and wiring the judge in is the next step"
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

_RULE = "=" * 78

#: Stated in the run's own output, because a reader of a number needs them next to
#: the number rather than in a document they may not open.
LIMITATIONS: tuple[str, ...] = (
    "Every probe is an ASSUMPTION-labelled operationalisation of a sourced requirement. "
    "The requirement, its kind, its timing and its citation come from the register YAML; "
    "the patterns and positional rules are this module's, and each entry prints its own.",
    "The probes were written with these eighteen transcripts in view, so the agreement "
    "figure below is IN-SAMPLE. It is a statement about whether the register can be "
    "computed at all, not a held-out accuracy.",
    "An entry gated on a topic being raised cannot catch an adviser who never raises it: "
    "the cooling-off and past-performance entries are only checkable once the adviser "
    "brings them up.",
    "Product class is detected over the whole transcript, so a session carrying two "
    "products with two standards is graded on the union of them.",
    "Writing, and the addressee of a disclosure, are not observable in a transcript. The "
    "first is reported as a residue; the second returns instrument-gap.",
)


#: The four rows whose declared `expected_failure` says a presence-based check
#: passes them: the three near-miss rows plus the understated clause. Same list as
#: `tests/test_advisory_corpus.py::FALSE_PASS_ROWS`, and it is the set the shadow
#: comparison is computed over — a rate over "every row tagged near-miss" would
#: quietly include a divergence row that carries the tag for another reason.
FALSE_PASS_ROWS: tuple[str, ...] = (
    "nearmiss-charges-disclosed-after-the-ask",
    "nearmiss-restricted-advice-buried-in-a-long-turn",
    "nearmiss-warning-addressed-to-the-partner",
    "clause-surrender-value-understated",
)


def _load() -> "Corpus":
    """The advisory corpus, or an exit. A corpus that does not validate is not data.

    Refusing to compute anything from an invalid corpus is the same rule
    `roleplay.demo` follows: a finding from an unvalidated corpus is an anecdote.
    """
    from roleplay.corpus import validate_advisory_corpus

    validation = validate_advisory_corpus()
    if not validation.ok:
        raise SystemExit(
            "the advisory corpus does not validate; nothing computed from it is evidence:\n"
            + "\n".join(i.render() for i in validation.errors)
        )
    return validation.corpus


@dataclass(frozen=True)
class ComputedRow:
    """One advisory row, run and graded: the stimulus, the trace, and the verdicts.

    A type rather than a dict because three different verdicts about the same
    session live in it and they are easy to confuse: what a reviewer said
    (`scenario.expectation.human_verdict`), what the product's scorer said
    (`card.verdict`), and what the register computes (`own.verdict`). A report that
    mixes them up is worse than no report.
    """

    scenario: "Scenario"
    result: "RoleplayResult"
    #: The verdict under the regime the row is graded in.
    own: RegimeVerdict
    #: Every regime the row names, computed on the same trace — the divergence
    #: question.
    verdicts: dict[str, RegimeVerdict]

    @property
    def human_verdict(self) -> str:
        return str(self.scenario.expectation.human_verdict)

    @property
    def scorer_verdict(self) -> str:
        """What `RubricScorer` said. Not what the register said, and often not equal."""
        return str(self.result.card.verdict)

    @property
    def agrees(self) -> bool:
        return self.own.verdict == self.human_verdict


def run_corpus(corpus: "Corpus") -> dict[str, ComputedRow]:
    """Run every advisory row and compute its regime verdicts.

    One `RoleplayCoach` per row, so the scorer's cohort curve is out of scope —
    the same reason `roleplay.demo` does it that way. The regime verdict does not
    depend on the scorer at all; the scorer's verdict is carried alongside because
    the comparison is the finding.
    """
    from roleplay.runtime import RoleplayCoach
    from roleplay.scorer import RubricScorer

    evaluator = RegimeEvaluator()
    rows: dict[str, ComputedRow] = {}
    for scenario in corpus:
        result = RoleplayCoach(scorer=RubricScorer()).run(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            session_id=f"regime-{scenario.id}",
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        )
        regime = scenario.regime or "fca"
        regimes = sorted(
            {regime}
            | {r.regime for r in (scenario.divergence.regimes if scenario.divergence else ())}
        )
        rows[scenario.id] = ComputedRow(
            scenario=scenario,
            result=result,
            own=evaluator.evaluate(result.trace, regime=regime),
            verdicts={
                other: evaluator.evaluate(result.trace, regime=other) for other in regimes
            },
        )
    return rows


def _confusion(rows: dict[str, ComputedRow]) -> dict[str, int]:
    cells = {
        "pass/pass": 0,
        "pass/fail": 0,
        "pass/undecidable": 0,
        "fail/pass": 0,
        "fail/fail": 0,
        "fail/undecidable": 0,
    }
    for row in rows.values():
        cells[f"{row.human_verdict}/{row.own.verdict}"] += 1
    return cells


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m roleplay.regime_eval` — the eighteen rows, computed."""
    parser = argparse.ArgumentParser(
        description="Compute advisory regime verdicts from the cited registers."
    )
    parser.add_argument("--divergence", action="store_true", help="the per-regime blocks only")
    parser.add_argument("--shadow", action="store_true", help="register versus a keyword check")
    parser.add_argument("--entries", action="store_true", help="print every engaged entry")
    parser.add_argument("--json", action="store_true", help="machine-readable")
    parser.add_argument("--row", default=None, help="one scenario id")
    args = parser.parse_args(argv)

    rows = run_corpus(_load())
    if args.row:
        rows = {args.row: rows[args.row]}

    if args.json:
        print(
            json.dumps(
                {
                    scenario_id: {
                        "human_verdict": row.human_verdict,
                        "computed": row.own.verdict,
                        "scorer": row.scorer_verdict,
                        "regimes": {
                            regime: verdict.as_dict()
                            for regime, verdict in row.verdicts.items()
                        },
                    }
                    for scenario_id, row in rows.items()
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(_RULE)
    print("HOW TO READ THIS")
    print(_RULE)
    for line in LIMITATIONS:
        print(f"  - {line}")

    print()
    print(_RULE)
    print("1. ROW BY ROW  (computed under the regime the row is graded in)")
    print(_RULE)
    agree = 0
    for scenario_id, row in rows.items():
        verdict = row.own
        agree += row.agrees
        print(
            f"  {scenario_id:<48} {row.scenario.regime:<7} human={row.human_verdict:<4} "
            f"computed={verdict.verdict:<12} scorer={row.scorer_verdict:<5} "
            + ("agrees " if row.agrees else "DIFFERS")
        )
        print(f"      {verdict.summary()}")
        print(f"      {verdict.reason()}")
        if args.entries:
            for entry in verdict.engaged:
                print(entry.render())
    print()
    print(f"  agreement: {agree}/{len(rows)} rows")
    cells = _confusion(rows)
    print("  confusion (human/computed): " + ", ".join(f"{k}={v}" for k, v in cells.items()))

    if args.divergence:
        print()
        print(_RULE)
        print("2. THE SAME TRANSCRIPT, EVERY REGIME THE ROW NAMES")
        print(_RULE)
        diverged = blocks = pairs = entry_agree = register_agree = 0
        for scenario_id, row in rows.items():
            scenario = row.scenario
            if scenario.divergence is None:
                continue
            hand = {r.regime: r for r in scenario.divergence.regimes}
            print(f"  {scenario_id}  (axis {scenario.divergence.axis})")
            for regime, verdict in row.verdicts.items():
                block = hand.get(regime)
                named = block.register_entry if block else "-"
                entry = next((e for e in verdict.entries if e.entry_id == named), None)
                if block is not None:
                    pairs += 1
                    # A block verdict is a claim about ONE entry, so "pass" means
                    # that entry was satisfied or does not apply, and "fail" means
                    # it was missed.
                    expected = (
                        {"satisfied", "not-applicable"} if block.verdict == "pass" else {"missed"}
                    )
                    entry_agree += entry is not None and entry.status in expected
                    register_agree += verdict.verdict == block.verdict
                print(
                    f"      {regime:<7} hand={(block.verdict if block else '-'):<5} "
                    f"computed={verdict.verdict:<12} "
                    f"named entry {named} -> {entry.status if entry else 'not probed'}"
                )
            computed = {v.verdict for v in row.verdicts.values()}
            diverged += len(computed) > 1
            blocks += 1
            print(
                "      computed verdicts "
                + ("DIVERGE" if len(computed) > 1 else "AGREE (no divergence computed)")
                + f": {sorted(computed)}"
            )
        print()
        print(
            f"  {diverged}/{blocks} divergence blocks produce opposite computed verdicts on "
            f"the same transcript"
        )
        print(
            f"  named-entry agreement: {entry_agree}/{pairs} regime verdicts — the block's "
            "own claim, which is about one entry"
        )
        print(
            f"  whole-register agreement: {register_agree}/{pairs} regime verdicts — the "
            "same transcript against every entry in that regime's register, which is a "
            "wider claim than the block makes"
        )

    if args.shadow:
        print()
        print(_RULE)
        print("3. THE REGISTER VERSUS A KEYWORD CHECK, ON THE ROWS BUILT TO FOOL ONE")
        print(_RULE)
        print(
            "  Two controls per row. The first is the existing "
            "roleplay.register.compare_with_keyword_check: the product's own "
            "disclosure ledger against a keyword list, on the generic market codes.\n"
            "  The second is the same register-entry probes with position, unit "
            "arithmetic, polarity and the verbatim/substance distinction removed — the "
            "check an engineer writes in an afternoon, over this regime's own vocabulary."
        )
        evaluator = RegimeEvaluator()
        wrongly_passed = 0
        over_credited = 0
        considered = 0
        for scenario_id in FALSE_PASS_ROWS:
            row = rows.get(scenario_id)
            if row is None:
                continue
            considered += 1
            shadow = row.result.keyword_shadow()
            naive = evaluator.naive_shadow(
                row.result.trace, regime=row.scenario.regime or "fca"
            )
            computed = row.own.verdict
            wrong = naive.naive_verdict == "pass" and computed != "pass"
            wrongly_passed += wrong
            over_credited += len(naive.over_credited)
            print(f"  {scenario_id}  (register-computed verdict {computed.upper()})")
            print(f"      ledger vs keywords : {shadow.summary()}")
            print(f"      register vs naive  : {naive.summary()}")
        print()
        print(
            f"  a naive check over the same register vocabulary would PASS "
            f"{wrongly_passed}/{considered} of the rows the register-computed verdict does "
            f"not pass, and over-credits {over_credited} individual register entries the "
            f"register missed"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
